"""The automatic encoder pick tries a hardware encoder before trusting it
(record/encode.py probe_encoder + encoder_starts; the check is the mania
engine's, osu-mania-renderer #31).

What must hold:
  * an explicit encoder passes through untouched, and nothing is run for it;
  * with no hardware encoder listed (every Mac) nothing extra is run;
  * a listed hardware encoder that starts is chosen, as before;
  * a listed one that does NOT start is skipped: the next one is tried, and
    libx264 in the end;
  * a hardware encoder that hangs is given up on at the timeout;
  * R3D_ENCODER_PROBE=0 brings back "the listing decides".

A stand-in `ffmpeg` on PATH plays the machine: it lists what it is told to
list and fails for the encoders it is told are broken.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time

from osu_std_renderer.record import encode

FAKE = r'''#!/bin/sh
# stand-in ffmpeg: FAKE_LIST = encoders to list, FAKE_BROKEN = ones that fail,
# FAKE_HANG = ones that never start. Every call is appended to $FAKE_LOG.
echo "$*" >> "$FAKE_LOG"
case " $* " in *" -encoders "*)
  for e in $FAKE_LIST; do echo " V....D $e   fake"; done; exit 0;;
esac
enc=""; prev=""
for a in "$@"; do [ "$prev" = "-c:v" ] && enc="$a"; prev="$a"; done
cat > /dev/null
for b in $FAKE_HANG; do [ "$b" = "$enc" ] && exec sleep 30; done
for b in $FAKE_BROKEN; do [ "$b" = "$enc" ] && { echo "[$enc] Cannot load the encoder here" >&2; exit 1; }; done
exit 0
'''


class _Machine:
    """PATH with only the stand-in ffmpeg, and the env that drives it."""
    def __init__(self, listed, broken="", hang="", **env):
        self.env = dict(FAKE_LIST=listed, FAKE_BROKEN=broken, FAKE_HANG=hang, **env)

    def __enter__(self):
        self.d = tempfile.mkdtemp(prefix="r3d-enc-")
        f = os.path.join(self.d, "ffmpeg")
        with open(f, "w") as fh:
            fh.write(FAKE)
        os.chmod(f, os.stat(f).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.d, "calls.log")
        self.old = {k: os.environ.get(k) for k in
                    ("PATH", "FAKE_LIST", "FAKE_BROKEN", "FAKE_HANG", "FAKE_LOG",
                     "R3D_ENCODER_PROBE")}
        os.environ.pop("R3D_ENCODER_PROBE", None)
        os.environ.update(self.env, FAKE_LOG=self.log,
                          PATH=self.d + os.pathsep + "/bin" + os.pathsep + "/usr/bin")
        return self

    def tried(self):
        """The encoders a one-frame check was run for, in order."""
        out = []
        for line in open(self.log).read().splitlines() if os.path.exists(self.log) else []:
            a = line.split()
            if "-c:v" in a:
                out.append(a[a.index("-c:v") + 1])
        return out

    def calls(self):
        return len(open(self.log).read().splitlines()) if os.path.exists(self.log) else 0

    def __exit__(self, *a):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.d, ignore_errors=True)


def test_an_explicit_encoder_passes_through_and_runs_nothing():
    with _Machine("h264_nvenc h264_vaapi libx264", broken="h264_nvenc") as m:
        for e in ("libx264", "h264_nvenc", "h264_vaapi", "h264_videotoolbox"):
            assert encode.probe_encoder(e) == e
            assert encode.probe_encoder(e, "/dev/dri/renderD129") == e
        assert m.calls() == 0


def test_no_hardware_encoder_listed_means_no_extra_work():
    with _Machine("libx264 h264_videotoolbox") as m:
        assert encode.probe_encoder("auto") == "libx264"
        assert m.tried() == [] and m.calls() == 1          # the listing, nothing else
    with _Machine("") as m:                                  # nothing listed at all
        assert encode.probe_encoder("auto") == "libx264"
        assert m.tried() == []


def test_a_listed_encoder_that_starts_is_chosen():
    with _Machine("h264_nvenc h264_vaapi libx264") as m:
        assert encode.probe_encoder("auto") == "h264_nvenc"
        assert m.tried() == ["h264_nvenc"]
    with _Machine("h264_vaapi libx264") as m:
        assert encode.probe_encoder("auto", "/dev/dri/renderD129") == "h264_vaapi"
        assert m.tried() == ["h264_vaapi"]
        # the check is made on the device the render will use
        assert "-vaapi_device /dev/dri/renderD129" in open(m.log).read()
        assert "format=nv12,hwupload" in open(m.log).read()


def test_a_listed_encoder_that_does_not_start_is_skipped():
    with _Machine("h264_nvenc h264_vaapi libx264", broken="h264_nvenc") as m:
        assert encode.probe_encoder("auto") == "h264_vaapi"
        assert m.tried() == ["h264_nvenc", "h264_vaapi"]
    with _Machine("h264_nvenc h264_vaapi libx264", broken="h264_nvenc h264_vaapi") as m:
        assert encode.probe_encoder("auto") == "libx264"
        assert m.tried() == ["h264_nvenc", "h264_vaapi"]    # libx264 is not checked
    with _Machine("h264_nvenc", broken="h264_nvenc") as m:   # not even libx264 listed
        assert encode.probe_encoder("auto") == "libx264"


def test_the_reason_is_ffmpegs_first_line():
    with _Machine("h264_nvenc libx264", broken="h264_nvenc"):
        ok, why = encode.encoder_starts(shutil.which("ffmpeg"), "h264_nvenc")
        assert ok is False and why == "[h264_nvenc] Cannot load the encoder here"
        assert encode.encoder_starts(shutil.which("ffmpeg"), "libx264") == (True, "")
    assert encode.encoder_starts("/nonexistent/ffmpeg", "h264_nvenc")[0] is False


def test_an_encoder_that_hangs_is_given_up_on():
    old = encode.ENCODER_PROBE_TIMEOUT_S
    encode.ENCODER_PROBE_TIMEOUT_S = 0.5
    try:
        with _Machine("h264_nvenc libx264", hang="h264_nvenc"):
            t = time.monotonic()
            assert encode.probe_encoder("auto") == "libx264"
            assert time.monotonic() - t < 5.0
    finally:
        encode.ENCODER_PROBE_TIMEOUT_S = old


def test_the_switch_brings_back_the_listing_alone():
    with _Machine("h264_nvenc libx264", broken="h264_nvenc", R3D_ENCODER_PROBE="0") as m:
        assert encode.probe_encoder("auto") == "h264_nvenc"
        assert m.tried() == []


def test_the_real_ffmpeg_starts_libx264():
    ff = shutil.which("ffmpeg")
    if not ff:
        return
    assert encode.encoder_starts(ff, "libx264") == (True, "")
    ok, why = encode.encoder_starts(ff, "no_such_encoder")
    assert ok is False and why


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("ok  ", _n)
