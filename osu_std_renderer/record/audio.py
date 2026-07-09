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


def rate_audio_filter(rate: float, pitch: bool = False) -> str:
    """The ffmpeg ``-af`` filter string for a clock-rate change → "" at rate 1.

    Two modes, matching lazer's rate-mod audio (ModRateAdjust vs
    ModNightcore/ModDaycore):

      * ``pitch=False`` — DT/HT (ModRateAdjust, AdjustableProperty.Tempo):
        ``atempo={rate}`` — tempo change, pitch PRESERVED. Identical to the
        pre-custom-rate path, so a fixed 1.5/0.75 render is byte-identical.
      * ``pitch=True`` — NC/DC (ModNightcore/ModDaycore): the pitch is pinned
        to the mod's DEFAULT rate (the classic 1.5× nightcore / 0.75× daycore
        Frequency = ``freqAdjust = SpeedChange.Default``) via ``asetrate`` +
        resample, and ``atempo = rate / default`` (= lazer's ``tempoAdjust``)
        carries the remainder. Net speed = rate; the audio pitches. (NB: this
        matches CURRENT lazer, where NC pitch is FIXED at 1.5× regardless of
        the custom rate — not "pitch == rate".)

    atempo's stable range is 0.5–2.0; every custom-rate ``atempo`` here
    (DT/HT 0.5–2.0, NC tempo 0.673–1.333, DC tempo 0.667–1.32) stays inside
    it, so a single stage suffices."""
    if rate == 1.0:
        return ""
    if not pitch:
        return f"atempo={rate}"
    default = 1.5 if rate > 1.0 else 0.75          # NC vs DC classic pitch
    new_sr = int(round(SAMPLE_RATE * default))
    tempo = rate / default
    af = f"asetrate={new_sr},aresample={SAMPLE_RATE}"
    if abs(tempo - 1.0) > 1e-9:
        af += f",atempo={tempo}"
    return af


def decode_to_pcm(path: Path, *, rate: float = 1.0,
                  pitch: bool = False) -> np.ndarray:
    """Decode any audio file → float32 stereo 48 kHz, shape (N, 2).

    `rate` != 1 applies the clock-rate change. `pitch=False` (DT/HT) is a
    pitch-preserving tempo change; `pitch=True` (NC/DC) shifts the pitch with
    the rate (nightcore/daycore). See :func:`rate_audio_filter`."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioError("ffmpeg not found on PATH")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path)]
    af = rate_audio_filter(rate, pitch)
    if af:
        cmd += ["-af", af]
    cmd += ["-f", "f32le", "-acodec", "pcm_f32le",
            "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg decode failed: "
                         f"{proc.stderr.decode(errors='replace')[-500:]}")
    pcm = np.frombuffer(proc.stdout, dtype=np.float32)
    return pcm.reshape(-1, CHANNELS).copy()


# --- WU/WD continuous rate ramp (ModTimeRamp) --------------------------------
# The constant-rate path (decode_to_pcm above) applies ONE atempo/asetrate to
# the whole track — impossible for Wind Up / Wind Down, whose rate ramps over
# the map. We warp the music PIECEWISE: partition the song into bands over which
# the rate is constant to within 0.01 (timewarp.rate_bands — the SAME 0.01
# granularity lazer itself quantizes SpeedChange to via Math.Round(rate, 2)),
# stretch each band at its average rate, and concatenate. Each band's output is
# pinned to the EXACT wall length the closed-form transform prescribes, so there
# is ZERO cumulative audio<->video drift at band boundaries.
#
#   * adjust_pitch=True  (WU/WD DEFAULT — pitch follows the rate): pure linear
#     RESAMPLE per band. Resampling a map-time slice to its wall-length both
#     changes tempo AND shifts pitch (the wind-up "chipmunk" / wind-down
#     "slowdown"). Exact, numpy-only, no ffmpeg, no drift.
#   * adjust_pitch=False (pitch preserved, tempo-only): a pitch-preserving
#     time stretch per band via ffmpeg `rubberband` (available on the box;
#     atempo fallback), then pinned to the exact band length.

def _resample_linear(src: np.ndarray, n: int) -> np.ndarray:
    """Linearly resample stereo PCM ``src`` (M,2) to exactly ``n`` frames."""
    if n <= 0:
        return np.zeros((0, CHANNELS), dtype=np.float32)
    if len(src) == 0:
        return np.zeros((n, CHANNELS), dtype=np.float32)
    if len(src) == 1:
        return np.repeat(src, n, axis=0).astype(np.float32)
    idx = np.linspace(0.0, len(src) - 1, n)
    lo = np.floor(idx).astype(np.int64)
    hi = np.minimum(lo + 1, len(src) - 1)
    frac = (idx - lo).astype(np.float32)[:, None]
    return (src[lo] * (1.0 - frac) + src[hi] * frac).astype(np.float32)


def _stretch_preserve_pitch(src: np.ndarray, tempo: float,
                            target_n: int) -> np.ndarray:
    """Pitch-preserving time stretch of ``src`` by ``tempo`` (>1 = faster),
    pinned to ``target_n`` frames. ffmpeg `rubberband` (or `atempo` fallback);
    a final micro-resample corrects any WSOLA length rounding so bands join
    sample-accurately."""
    if target_n <= 0 or len(src) == 0:
        return _resample_linear(src, target_n)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is not None and abs(tempo - 1.0) > 1e-6:
        filt = f"rubberband=tempo={tempo:.6f}"
        for af in (filt, f"atempo={tempo:.6f}"):
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
                   "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                   "-i", "pipe:0", "-af", af,
                   "-f", "f32le", "-acodec", "pcm_f32le",
                   "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "pipe:1"]
            proc = subprocess.run(cmd, input=src.astype("<f4").tobytes(),
                                  capture_output=True, check=False)
            if proc.returncode == 0 and proc.stdout:
                out = np.frombuffer(proc.stdout,
                                    dtype=np.float32).reshape(-1, CHANNELS)
                return _resample_linear(out.copy(), target_n)
    # no ffmpeg / rate 1.0 / both filters failed -> plain resample (this DOES
    # shift pitch, but only reached as a last-resort fallback)
    return _resample_linear(src, target_n)


def warp_music_pcm(pcm: np.ndarray, warp, *, adjust_pitch: bool,
                   m_lo: float = 0.0, m_hi: float | None = None,
                   quant: float = 0.01) -> np.ndarray:
    """Warp decoded music ``pcm`` (native rate, map-time domain) into the
    WALL-time domain under a :class:`timewarp.TimeWarp`. Returns float32 (N,2)
    whose frame 0 corresponds to map time ``m_lo`` (== song time 0 for a map)
    and whose length is the wall duration ``to_wall(m_hi) - to_wall(m_lo)``.
    Lay it at the wall position of ``m_lo`` in the mixer."""
    n_src = len(pcm)
    if m_hi is None:
        m_hi = n_src / SAMPLE_RATE * 1000.0
    bands = warp.rate_bands(m_lo, m_hi, quant=quant)
    if not bands:
        return pcm.astype(np.float32, copy=False)
    out: list[np.ndarray] = []
    for (b0, b1) in bands:
        i0 = max(0, min(n_src, int(round(b0 / 1000.0 * SAMPLE_RATE))))
        i1 = max(0, min(n_src, int(round(b1 / 1000.0 * SAMPLE_RATE))))
        seg = pcm[i0:i1]
        wall_ms = warp.to_wall(b1) - warp.to_wall(b0)
        target_n = max(0, int(round(wall_ms / 1000.0 * SAMPLE_RATE)))
        if adjust_pitch:
            out.append(_resample_linear(seg, target_n))
        else:
            # tempo = source frames / target frames = the band's AVERAGE rate
            tempo = (len(seg) / target_n) if target_n > 0 else 1.0
            out.append(_stretch_preserve_pitch(seg, tempo, target_n))
    return (np.concatenate(out, axis=0) if out
            else np.zeros((0, CHANNELS), dtype=np.float32))


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
        if end <= 0:
            return  # entirely before the window (a negative end would
            #         wrap the buf slice — the --start clip guard)
        if start < 0:
            sample = sample[-start:]
            start = 0
        self.buf[start:end] += sample[:end - start] * volume

    def silence_before(self, t_ms: float, fade_ms: float = 150.0) -> None:
        """§4.10 LeadInTime/SeizureWarning, audio side: the pre-roll is
        SILENT — zero everything before wall t_ms, with a short fade-in
        ending AT t_ms so a mid-track entry doesn't click. (For a
        map-start render the region is silent anyway — no-op.)"""
        if t_ms <= 0:
            return
        i1 = min(int(t_ms / 1000.0 * SAMPLE_RATE), len(self.buf))
        i0 = max(i1 - int(fade_ms / 1000.0 * SAMPLE_RATE), 0)
        self.buf[:i0] = 0.0
        if i1 > i0:
            ramp = np.linspace(0.0, 1.0, i1 - i0, endpoint=False,
                               dtype=np.float32)
            self.buf[i0:i1] *= ramp[:, None]

    def fade_out(self, t0_ms: float, t1_ms: float) -> None:
        """§4.10 FadeOutTime, audio side: linear gain 1→0 across
        [t0_ms, t1_ms) WALL time, silence after — the track fades with the
        video's fade-to-black (the results screen then sits on silence,
        matching 'fade out … before results'). No-op on a degenerate
        window."""
        if t1_ms <= t0_ms:
            return
        i0 = max(int(t0_ms / 1000.0 * SAMPLE_RATE), 0)
        i1 = min(int(t1_ms / 1000.0 * SAMPLE_RATE), len(self.buf))
        if i0 >= len(self.buf):
            return
        if i1 > i0:
            ramp = np.linspace(1.0, 0.0, i1 - i0, endpoint=False,
                               dtype=np.float32)
            self.buf[i0:i1] *= ramp[:, None]
        self.buf[i1:] = 0.0

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
