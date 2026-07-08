"""Offline audio mixing — the NO-BASS design (locked decision).

The reference (§5.6) runs BASS as a non-playing 48 kHz float decode mixer
and streams 1 ms of PCM per sim tick into a second ffmpeg process. BASS is
proprietary (free for non-commercial use only — the one real license item
in the danser stack, APPROACH.md §1.1). This engine replaces it the same
way mania/catch/taiko already do in production:

  1. decode the map's music (any codec) → float32 stereo 48 kHz numpy via
     an ffmpeg subprocess (decode_to_pcm);
  2. mix hitsound samples into a track buffer at event times with numpy
     adds (mix_at) — sample volumes from timing points / object extras
     (§3.4 semantics), rate-mods handled by resampling the music once;
  3. write the mixed track as .wav (write_wav) and hand it to the single
     ffmpeg encode as a file input (record/encode.py), where loudnorm
     (I=-14:TP=-1.5) runs like every in-house engine.

Determinism is preserved: mixing happens in map-time before encoding, so
encoder speed can't shift audio (the property §5.2 guards).
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
CHANNELS = 2


class AudioError(RuntimeError):
    pass


def decode_to_pcm(path: Path, *, rate: float = 1.0) -> np.ndarray:
    """Decode any audio file → float32 stereo 48 kHz, shape (N, 2).

    `rate` != 1 applies the DT/HT tempo change via atempo (pitch-preserving;
    NC pitch shift is a later concern — mania's mods.py has the recipe)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioError("ffmpeg not found on PATH")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if rate != 1.0:
        # atempo is valid 0.5–100; chain if ever outside (HT 0.75/DT 1.5 fit)
        cmd += ["-af", f"atempo={rate}"]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le",
            "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg decode failed: "
                         f"{proc.stderr.decode(errors='replace')[-500:]}")
    pcm = np.frombuffer(proc.stdout, dtype=np.float32)
    return pcm.reshape(-1, CHANNELS).copy()


class AudioMixer:
    """A track buffer in map-time; numpy-add samples at millisecond offsets."""

    def __init__(self, duration_ms: float):
        n = int(duration_ms / 1000.0 * SAMPLE_RATE) + SAMPLE_RATE  # +1 s pad
        self.buf = np.zeros((n, CHANNELS), dtype=np.float32)

    def lay_music(self, pcm: np.ndarray, at_ms: float = 0.0,
                  volume: float = 1.0) -> None:
        self.mix_at(at_ms, pcm, volume=volume)

    def mix_at(self, time_ms: float, sample: np.ndarray,
               volume: float = 1.0) -> None:
        """Add `sample` (N,2 float32) into the track at time_ms.

        §3.4 volume floor: osu! floors sample volume at 0.08 — the caller
        passes the floored value; this mixer is policy-free."""
        start = int(time_ms / 1000.0 * SAMPLE_RATE)
        if start >= len(self.buf):
            return
        end = min(len(self.buf), start + len(sample))
        if start < 0:
            sample = sample[-start:]
            start = 0
        self.buf[start:end] += sample[:end - start] * volume

    def write_wav(self, path: Path) -> Path:
        """Write float32 wav (ffmpeg reads it natively; no clipping — the
        encode step normalizes via loudnorm)."""
        path = Path(path)
        data = self.buf.astype("<f4").tobytes()
        with open(path, "wb") as fh:
            byte_rate = SAMPLE_RATE * CHANNELS * 4
            fh.write(b"RIFF")
            fh.write(struct.pack("<I", 36 + len(data)))
            fh.write(b"WAVEfmt ")
            fh.write(struct.pack("<IHHIIHH", 16, 3, CHANNELS, SAMPLE_RATE,
                                 byte_rate, CHANNELS * 4, 32))
            fh.write(b"data")
            fh.write(struct.pack("<I", len(data)))
            fh.write(data)
        return path
