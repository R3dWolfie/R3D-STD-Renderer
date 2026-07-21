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
  * loudnorm (single-pass, I=-14:TP=-1.5, the 2026-07-03 audio directive)
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

LOUDNORM = "loudnorm=I=-10:TP=-1.5:LRA=11"


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


def build_ffmpeg_cmd(*, encoder: str, resolution: tuple[int, int], fps: int,
                     output_path: Path, audio_path: Path | None = None,
                     audio_offset_ms: int = 0, video_bitrate: str | None = None,
                     crf: int = 16, audio_bitrate: str = "192k",
                     loudnorm: bool = True, extra_vf: str = "") -> list[str]:
    """rawvideo rgb24 on stdin → encoder → faststart mp4 (§5.6 shape)."""
    w, h = resolution
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "pipe:0"]
    if audio_path is not None:
        if audio_offset_ms:
            cmd += ["-itsoffset", f"{audio_offset_ms / 1000.0:.3f}"]
        cmd += ["-i", str(audio_path)]
    if extra_vf:
        cmd += ["-vf", extra_vf]
    cmd += ["-c:v", encoder]
    if encoder == "libx264":
        cmd += ["-crf", str(crf), "-preset", "faster", "-profile:v", "high"]
    elif encoder in ("h264_nvenc", "hevc_nvenc"):
        # Constant-quality (VBR+CQ, -b:v 0 = pure CQ) so bitrate tracks
        # resolution/motion. Previously NO rate control was set for NVENC, so
        # it fell back to its ~2 Mbps default -- starving 1440p/120fps (looked
        # like 720p). cq = crf+4 for comparable quality; resolution-scaled
        # maxrate caps worst-case file size.
        _mbps = max(6, round((w * h) / 150_000))
        if int(fps) >= 120:              # 120fps needs ~1.5x the bits for
            _mbps = round(_mbps * 1.5)    # equal quality (Red 2026-07-21)
        cmd += ["-rc", "vbr", "-cq", str(crf + 4), "-b:v", "0",
                "-maxrate", f"{_mbps}M", "-bufsize", f"{2 * _mbps}M",
                "-profile:v", "high"]
    elif video_bitrate:
        cmd += ["-b:v", video_bitrate]
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

    def __init__(self, cmd: list[str]):
        self.cmd = cmd
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
                continue          # drain (never write after an error)
            try:
                data = frame.tobytes()
                if self._hash is not None:
                    self._hash.update(data)
                    self._hash_frames += 1
                stdin.write(data)
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
