"""ffmpeg raw-RGB pipe — RENDER_PLAN.md §5.6 semantics on our stack; adapted
from the mania v2 encoder (OsuManiaRenderer_v2/osu_mania_renderer_v2/
encode.py: encoder probing order, rawvideo stdin input shape) simplified to
the synchronous single-process model the catch/taiko engines use.

Differences from the reference (§5.6) — deliberate:
  * ONE ffmpeg process, video on stdin + audio as a FILE input, instead of
    danser's two processes + mux step. Our audio is premixed offline
    (record/audio.py — NO BASS), so there is nothing to stream in
    lockstep; ffmpeg muxes in the same invocation.
  * Pixel path is rgb24 (renderer reads back RGB); the GPU RGB→YUV shader
    + PBO pool (§5.6 readback) is a later perf phase — mania v2's
    gpu/readback.py already proves it on this stack.
  * loudnorm (single-pass, I=-18:TP=-1.5, the 2026-07-31 audio directive)
    is applied to the audio filter chain like every in-house engine.
"""
from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from ..render import perf

LOUDNORM = "loudnorm=I=-18:TP=-1.5:LRA=11"


class EncoderError(RuntimeError):
    pass


def probe_encoder(encoder: str = "auto") -> str:
    """Resolve 'auto' → preferred encoder available on this system.
    Preference: h264_nvenc → h264_vaapi → libx264 (pool A/B are NVENC;
    pool C is AMD/VAAPI with system ffmpeg)."""
    if encoder != "auto":
        return encoder
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise EncoderError("ffmpeg not found on PATH")
    out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                         capture_output=True, text=True, check=False).stdout
    for cand in ("h264_nvenc", "h264_vaapi", "libx264"):
        if cand in out:
            return cand
    return "libx264"


def nvenc_target_bps(w: int, h: int, fps: float) -> int:
    """Resolution-scaled NVENC bitrate ladder (R3D cross-engine policy, 2026-07).

    Replaces the flat per-engine bitrate: scale a 4 Mbps 720p30 reference
    by pixel rate with a perceptual exponent (0.70 -- deliberately NOT
    linear), clamped to [2.5, 16] Mbps.  Anchors: 720p30=4.0M,
    720p60=6.5M, 1080p30=7.1M, 1080p60=11.5M, 1440p60/1080p120+=16M cap.
    Callers pair the target with maxrate=1.5x / bufsize=2x for NVENC VBR.
    Same formula in all four engines (catch/taiko/std/mania v2).
    """
    ref = 1280.0 * 720.0 * 30.0
    target = 4_000_000.0 * ((float(w) * float(h) * float(fps)) / ref) ** 0.70
    return int(min(16_000_000.0, max(2_500_000.0, target)))


def build_ffmpeg_cmd(*, encoder: str, resolution: tuple[int, int], fps: int,
                     output_path: Path, audio_path: Path | None = None,
                     audio_offset_ms: int = 0, video_bitrate: int | None = None,
                     crf: int = 16, audio_bitrate: str = "192k",
                     loudnorm: bool = True, extra_vf: str = "",
                     encoder_device: str | None = None) -> list[str]:
    """rawvideo rgb24 on stdin → encoder → faststart mp4 (§5.6 shape)."""
    w, h = resolution
    is_vaapi = encoder == "h264_vaapi"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if is_vaapi:
        # VAAPI needs the DRM render node initialised before the encoder and the
        # frames uploaded to a GPU surface (format=nv12,hwupload below). Without
        # this ffmpeg cannot open h264_vaapi -> dies at startup -> BrokenPipe on
        # the first frame write. std was NVENC-only until AMD contributors ran it.
        cmd += ["-vaapi_device", encoder_device or "/dev/dri/renderD128"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "pipe:0"]
    if audio_path is not None:
        if audio_offset_ms:
            cmd += ["-itsoffset", f"{audio_offset_ms / 1000.0:.3f}"]
        cmd += ["-i", str(audio_path)]
    # Frames arrive BOTTOM-UP on stdin and ffmpeg's vflip filter restores
    # them (an exact row reorder on rawvideo — zero pixel math; the mania
    # v2 prod encoder ships the same shape). This lets the writer hand
    # the GL readback buffer to the pipe zero-copy instead of paying a
    # ~6 MB negative-stride flip copy per frame — see FfmpegPipe._writer.
    _vf = "vflip" + ("," + extra_vf if extra_vf else "")
    if is_vaapi:
        _vf += ",format=nv12,hwupload"
    cmd += ["-vf", _vf]
    cmd += ["-c:v", encoder]
    if encoder == "libx264":
        if video_bitrate:
            _vb = int(video_bitrate)
            cmd += ["-b:v", str(_vb), "-maxrate", str(int(_vb * 1.5)),
                    "-bufsize", str(_vb * 2), "-preset", "faster", "-profile:v", "high"]
        else:
            cmd += ["-crf", str(crf), "-preset", "faster", "-profile:v", "high"]
    elif encoder in ("h264_nvenc", "hevc_nvenc"):
        # Resolution-scaled NVENC bitrate ladder (R3D cross-engine policy,
        # 2026-07): replaces the 2026-07-21 CQ scheme (cq=crf+4, -b:v 0,
        # maxrate (w*h)/150k) with the shared target-VBR ladder so all four
        # engines land on the same size/quality curve -- see nvenc_target_bps.
        _tgt = video_bitrate or nvenc_target_bps(w, h, fps)
        # CQ23 quality-targeted VBR (quality-approved 2026-09-02): visually
        # identical to the fixed-target ladder, ~17% smaller; the ladder is
        # kept only as the -maxrate/-bufsize cap below.
        cmd += ["-rc", "vbr", "-cq", "23", "-b:v", "0",
                "-maxrate", str(int(_tgt * 1.5)), "-bufsize", str(_tgt * 2),
                "-profile:v", "high"]
    elif is_vaapi:
        _vb = video_bitrate or nvenc_target_bps(w, h, fps)
        cmd += ["-b:v", str(_vb), "-maxrate", str(int(_vb * 1.5)),
                "-bufsize", str(_vb * 2)]
    elif video_bitrate:
        cmd += ["-b:v", str(video_bitrate)]
    if not is_vaapi:
        # VAAPI output pixel format is set by the hwupload filtergraph (nv12 on a
        # GPU surface); forcing -pix_fmt yuv420p here conflicts with the encoder.
        cmd += ["-pix_fmt", "yuv420p"]
    if audio_path is not None:
        # LOUDNORM DUCK FIX (#17): when the music was pre-normalised upstream
        # (loudnorm=False), do NOT loudnorm the mixed song+hits (that ducked the
        # song under hits) -- apply only a clamp-only true-peak limiter to catch
        # summed peaks without ducking.
        af = ([LOUDNORM] if loudnorm
              else ["alimiter=limit=0.95:level=disabled:attack=1:release=20"])
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-shortest"]
    cmd += ["-movflags", "+faststart", str(output_path)]
    return cmd


class FfmpegPipe:
    """Spawn ffmpeg, push raw frames, close. Mirrors mania v2's FfmpegPipe
    contract minus asyncio (the worker wraps the CLI in a subprocess
    already; in-process async buys nothing here).

    Frames are handed to a writer thread over a small bounded queue: the
    serialisation (`tobytes` — a negative-stride flip copy) and the
    blocking pipe write happen OFF the render thread, overlapping the next
    frame's draw. Order is FIFO so the byte stream ffmpeg sees (and the
    R3D_FRAME_MD5 hash, computed writer-side) is unchanged. The queue
    bounds memory (4 × ~2.7 MB frames) and provides natural backpressure
    when ffmpeg is the bottleneck; writer errors surface loudly on the
    next push() instead of deadlocking the producer."""

    _QUEUE_FRAMES = 4

    def __init__(self, cmd: list[str], recycle=None):
        # recycle: optional callable(frame) invoked writer-side once a
        # frame's bytes are in the pipe — the GL renderer's readback
        # buffer pool (SpriteRenderer.recycle_frame). Pure bookkeeping:
        # the byte stream ffmpeg sees is untouched.
        self.cmd = cmd
        self._recycle = recycle
        self.proc: subprocess.Popen | None = None
        self._q: "queue.Queue" = queue.Queue(maxsize=self._QUEUE_FRAMES)
        self._thread: threading.Thread | None = None
        self._werr: BaseException | None = None
        self._hash = None
        self._hash_frames = 0
        if perf.FRAME_MD5:
            import hashlib
            self._hash = hashlib.blake2b(digest_size=16)

    def __enter__(self) -> "FfmpegPipe":
        self.proc = subprocess.Popen(
            self.cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self._thread = threading.Thread(target=self._writer,
                                        name="ffmpeg-writer", daemon=True)
        self._thread.start()
        return self

    def _writer(self) -> None:
        stdin = self.proc.stdin
        while True:
            frame = self._q.get()
            if frame is None:
                return
            if self._werr is not None:
                if self._recycle is not None:
                    self._recycle(frame)
                continue          # drain (never write after an error)
            try:
                if self._hash is not None:
                    # R3D_FRAME_MD5 hashes the TOP-DOWN semantic stream —
                    # the same bytes the pre-vflip pipe pushed, so digests
                    # stay comparable across the pipeline change.
                    self._hash.update(frame.tobytes())
                    self._hash_frames += 1
                # ffmpeg runs `vflip` (build_ffmpeg_cmd), so the pipe wants
                # the frame BOTTOM-UP. PBO frames are flipud views over a
                # contiguous readback buffer, so frame[::-1] recovers that
                # buffer C-contiguous → a zero-copy pipe write. Fresh
                # top-down frames (the SSAA results tail) fall back to the
                # same negative-stride flip copy the old path did.
                flipped = frame[::-1]
                if flipped.flags["C_CONTIGUOUS"]:
                    stdin.write(flipped)
                else:
                    stdin.write(flipped.tobytes())
                if self._recycle is not None:
                    self._recycle(frame)
            except BaseException as e:  # noqa: BLE001 - surfaced on push()
                self._werr = e

    def push(self, frame_rgb) -> None:
        assert self.proc is not None and self._thread is not None
        if self._werr is not None:
            raise EncoderError(f"ffmpeg writer failed: {self._werr!r}")
        with perf.T("encode_push"):
            self._q.put(frame_rgb)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is None:
            return
        if self._thread is not None:
            self._q.put(None)
            self._thread.join()
        if self._hash is not None:
            print(f"frame-stream-hash: {self._hash.hexdigest()} "
                  f"({self._hash_frames} frames)", file=sys.stderr, flush=True)
        if self.proc.stdin is not None:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
        _, err = None, b""
        try:
            err = self.proc.stderr.read() if self.proc.stderr else b""
        finally:
            code = self.proc.wait()
        if exc_type is None and self._werr is not None \
                and not isinstance(self._werr, BrokenPipeError):
            raise EncoderError(f"ffmpeg writer failed: {self._werr!r}")
        if exc_type is None and code != 0:
            raise EncoderError(
                f"ffmpeg exited {code}: {err.decode(errors='replace')[-2000:]}")
