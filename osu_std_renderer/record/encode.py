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

import shutil
import subprocess
from pathlib import Path

LOUDNORM = "loudnorm=I=-14:TP=-1.5"


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
    elif video_bitrate:
        cmd += ["-b:v", video_bitrate]
    cmd += ["-pix_fmt", "yuv420p"]
    if audio_path is not None:
        af = [LOUDNORM] if loudnorm else []
        if af:
            cmd += ["-af", ",".join(af)]
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-shortest"]
    cmd += ["-movflags", "+faststart", str(output_path)]
    return cmd


class FfmpegPipe:
    """Spawn ffmpeg, push raw frames, close. Mirrors mania v2's FfmpegPipe
    contract minus asyncio (the worker wraps the CLI in a subprocess
    already; in-process async buys nothing here)."""

    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "FfmpegPipe":
        self.proc = subprocess.Popen(
            self.cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return self

    def push(self, frame_rgb) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.write(frame_rgb.tobytes())

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc is None:
            return
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
        if exc_type is None and code != 0:
            raise EncoderError(
                f"ffmpeg exited {code}: {err.decode(errors='replace')[-2000:]}")
