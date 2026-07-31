"""Hitsound resolution + offline mixing — RENDER_PLAN.md §3.4
(app/audio/osuaudio.go) on the NO-BASS path (record/audio.py).

Semantics ported:
  * The 3×7 sample grid — sets `normal/soft/drum` × sounds `hitnormal/
    hitwhistle/hitfinish/hitclap/slidertick/sliderslide/sliderwhistle` —
    resolved through the skin chain (SKIN→FALLBACK→LOCAL, .wav/.ogg/.mp3
    via skin.Skin.find_sample); `spinnerspin`/`spinnerbonus` ride the
    same chain un-prefixed.
  * BEATMAP-folder samples override by custom index
    (`<set>-<sound><index>.*` in the map dir; index 1 = bare name,
    index 0 = never the beatmap — stable's "default samples" index)
    unless IgnoreBeatmapSamples (settings.use_skin_hitsounds). A ZERO-
    BYTE sample file silences the sound (the classic skin/map blanking
    convention) instead of falling through.
  * PlaySample: `hitnormal` plays if LayeredHitSounds OR bit 1 OR
    hitsound==0, from the BASE set; whistle/finish/clap play per bits
    2/4/8 from the ADDITION set. Volume = object-extras volume when set,
    else the timing point's, floored at 0.08 (§3.4). Positional balance
    (HitsoundPositionMultiplier) is NOT implemented (mono-centered mix).
  * One-shots fire at judged HIT times only (real click times for
    circles/heads, pass times for ticks/repeats/tails) — NO sound on
    misses.
  * `sliderslide` (+`sliderwhistle` when the slider body carries the
    whistle bit) LOOPS while the ball is tracked: the sample is TILED
    across each ruleset tracking window, split at timing-point
    boundaries so set/index/volume changes mid-slide are honored. Tile
    joins are hard cuts (the synth defaults carry edge fades to hide
    them); gapless loop stitching is a later polish.
  * `spinnerspin` is tiled across the spinner's duration (flat rate —
    SpinnerFrequencyModulate's pitch ramp is NOT implemented);
    `spinnerbonus` is NOT played (the simplified sim exposes no bonus
    ticks). The spinner's own hitsound fires at its end when cleared.
  * When no source provides a sample, a DETERMINISTIC synthesized
    placeholder is generated (short sine/noise bursts @48 kHz stereo) —
    a skinless render never goes silent. TWO synth banks exist (owner
    decision 2026-07-08), selected by the SAME league rule as the HUD
    visuals (synth_style_for): skinless renders synthesize in the ARGON
    sound family (the lazer-ish bank below); a CUSTOM SKIN's missing
    samples — and everything under --legacy-defaults — synthesize in
    the LEGACY family, approximating the CLASSIC osu default sample
    character (hitnormal = the short soft tock, whistle = the two-tone
    whistle, finish = a cymbal-ish crash decay, clap = a multi-burst
    clap; soft/drum sets are tonal/filter variations). Zero ppy sample
    files — everything stays procedural and deterministic.

All event times are gameplay (map) ms; mixing converts to wall time via
(t - start_ms) / speed, matching the rate-modded music bed. Everything is
plain numpy adds into the AudioMixer — offline and deterministic.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..beatmap.objects import Slider, Spinner
from .audio import SAMPLE_RATE, AudioError, decode_to_pcm

# §3.4 sets 1/2/3 and hitsound bits
SET_NAMES = {1: "normal", 2: "soft", 3: "drum"}
BIT_NORMAL, BIT_WHISTLE, BIT_FINISH, BIT_CLAP = 1, 2, 4, 8
ADDITION_SOUNDS = ((BIT_WHISTLE, "hitwhistle"),
                   (BIT_FINISH, "hitfinish"),
                   (BIT_CLAP, "hitclap"))
VOLUME_FLOOR = 0.08          # §3.4 sample-volume floor
SAMPLE_EXTS = (".wav", ".ogg", ".mp3")   # §3.1 GetSample extension order
# Bundled osu! DEFAULT nightcore drums (ppy/osu-resources Legacy skin) — final
# fallback for the NC-mod overlay when the skin OMITS a nightcore sample.
_DEFAULT_NC_DIR = Path(__file__).resolve().parent.parent / "assets" / "default_nightcore"


def sounds_for_bits(bits: int, layered: bool = True) -> list[tuple[str, bool]]:
    """§3.4 PlaySample bit routing → [(sound, uses_addition_set)]:
    hitnormal if LayeredHitSounds OR bit 1 OR hitsound==0 (BASE set);
    whistle/finish/clap per bits 2/4/8 (ADDITION set)."""
    out: list[tuple[str, bool]] = []
    if layered or (bits & BIT_NORMAL) or bits == 0:
        out.append(("hitnormal", False))
    for bit, name in ADDITION_SOUNDS:
        if bits & bit:
            out.append((name, True))
    return out


def resolve_volume(point, extras_volume: float = 0.0) -> float:
    """Object-extras volume when set, else the timing point's;
    §3.4 floor 0.08, capped at 1."""
    v = extras_volume if extras_volume > 0 else point.sample_volume
    return max(VOLUME_FLOOR, min(1.0, v))


def _norm_set(set_id: int, fallback: int) -> int:
    if set_id in SET_NAMES:
        return set_id
    return fallback if fallback in SET_NAMES else 1


# --- events ---------------------------------------------------------------------

@dataclass(frozen=True)
class OneShot:
    """One sample played once at a judged hit time (gameplay ms)."""
    time_ms: float
    sound: str
    set_id: int          # 0 = un-prefixed sample (spinnerspin/spinnerbonus)
    index: int           # timing-point / extras custom sample index
    volume: float


@dataclass(frozen=True)
class Loop:
    """One sample tiled across [t0, t1) gameplay ms (slider slide/whistle,
    spinner spin) at constant set/index/volume — collect() splits windows
    at timing-point boundaries so these stay constant."""
    t0: float
    t1: float
    sound: str
    set_id: int
    index: int
    volume: float


def _split_by_points(timings, t0: float, t1: float):
    """Yield (w0, w1, point_at_w0) subwindows of [t0, t1) split at every
    timing-point boundary inside it."""
    bounds = [t0]
    bounds += [p.time for p in timings.points if t0 < p.time < t1]
    bounds.append(t1)
    for a, b in zip(bounds, bounds[1:]):
        if b > a:
            yield a, b, timings.get_point_at(a)


def collect_hitsound_events(beatmap, sim, *, layered: bool = True,
                            ) -> tuple[list[OneShot], list[Loop]]:
    """Walk the judged objects (SimResult verdicts) → one-shots + loops.
    Objects without a verdict (no sim) are skipped entirely."""
    timings = beatmap.timings
    oneshots: list[OneShot] = []
    loops: list[Loop] = []
    # OsuModClassic.AlwaysPlayTailSample (Classic mod): a slider's tail
    # sample plays even when the tail wasn't tracked/hit
    # (DrawableSliderTail.SamplePlaysOnlyOnHit = false).
    always_play_tail = getattr(sim, "classic", False)

    def emit_hit(t: float, bits: int, extras, edge_set) -> None:
        point = timings.get_point_at(t)
        base = add = 0
        if edge_set is not None:
            base, add = edge_set
        if base == 0:
            base = extras.sample_set
        if base == 0:
            base = point.sample_set
        base = _norm_set(base, timings.base_set)
        if add == 0:
            add = extras.addition_set
        add = _norm_set(add, base) if add != 0 else base
        index = extras.custom_index or point.sample_index
        volume = resolve_volume(point, extras.volume)
        for sound, use_add in sounds_for_bits(bits, layered):
            oneshots.append(OneShot(t, sound, add if use_add else base,
                                    index, volume))

    for obj in beatmap.hit_objects:
        v = sim.verdict_for(obj) if sim is not None else None
        if v is None:
            continue
        extras = obj.basic_hit_sound

        if isinstance(obj, Spinner):
            # spinnerspin loop across the spin; the spinner's own hitsound
            # fires at the end when cleared (hit_time set by the sim)
            point = timings.get_point_at(obj.start_time)
            loops.append(Loop(
                obj.start_time, obj.end_time, "spinnerspin", 0,
                extras.custom_index or point.sample_index,
                resolve_volume(point, extras.volume)))
            if v.hit_time is not None:
                emit_hit(v.hit_time, obj.hit_sound_bits, extras, None)
            continue

        if isinstance(obj, Slider):
            n_edges = obj.repeat_count + 1

            def edge(i: int) -> tuple[int, tuple[int, int] | None]:
                bits = (obj.edge_sounds[i] if i < len(obj.edge_sounds)
                        else obj.hit_sound_bits)
                eset = obj.edge_sets[i] if i < len(obj.edge_sets) else None
                return bits, eset

            if v.hit_time is not None:              # judged head hit time
                bits, eset = edge(0)
                emit_hit(v.hit_time, bits, extras, eset)
            rep_i = 0
            for p in v.parts[1:]:
                if p.kind == "repeat":
                    rep_i += 1
                if p.kind == "tail":
                    # AlwaysPlayTailSample: emit even on a missed tail
                    # under the Classic mod; otherwise only on a hit.
                    if p.hit or always_play_tail:
                        bits, eset = edge(n_edges - 1)
                        emit_hit(p.time, bits, extras, eset)
                    continue
                if not p.hit:
                    continue                         # NO sound on a missed tick/repeat
                if p.kind == "repeat":
                    bits, eset = edge(rep_i)
                    emit_hit(p.time, bits, extras, eset)
                elif p.kind == "tick":
                    point = timings.get_point_at(p.time)
                    base = _norm_set(extras.sample_set or point.sample_set,
                                     timings.base_set)
                    oneshots.append(OneShot(
                        p.time, "slidertick", base,
                        extras.custom_index or point.sample_index,
                        resolve_volume(point, extras.volume)))
            # §3.4 slide loops while tracking (stable tracks headless too)
            body = ["sliderslide"]
            if obj.hit_sound_bits & BIT_WHISTLE:
                body.append("sliderwhistle")
            for t0, t1 in v.tracking:
                for w0, w1, point in _split_by_points(timings, t0, t1):
                    base = _norm_set(extras.sample_set or point.sample_set,
                                     timings.base_set)
                    idx = extras.custom_index or point.sample_index
                    vol = resolve_volume(point, extras.volume)
                    for sound in body:
                        loops.append(Loop(w0, w1, sound, base, idx, vol))
            continue

        # circle
        if v.hit_time is not None:
            emit_hit(v.hit_time, obj.hit_sound_bits, extras, None)

    return oneshots, loops


# --- nightcore beat overlay ---------------------------------------------------------

NIGHTCORE_GAIN = 0.35        # mania's _NIGHTCORE_GAIN — under per-note hits


def nightcore_beats(timings, t0: float, t1: float,
                    ) -> list[tuple[float, bool]]:
    """§4.4 PlayNightcoreSamples schedule — the mania _layer_nightcore
    mirror: [(time_ms, is_downbeat)] for every beat of every red-line
    segment inside [t0, t1). Downbeat = beat 1 of each measure; deviation
    from mania (which hardcodes 4/4): the timing point's SIGNATURE drives
    the measure length (lazer's actual NC behaviour — mania's own note
    says 'assumed 4/4'). Beat length sanity-capped at 60 ms (>1000 BPM
    lines exist in aspire maps). The caller mixes clap on every beat and
    finish on downbeats at NIGHTCORE_GAIN through the SampleBank chain."""
    reds = timings.original_points
    out: list[tuple[float, bool]] = []
    for i, tp in enumerate(reds):
        beat = max(60.0, tp.beat_length_base)
        seg_end = reds[i + 1].time if i + 1 < len(reds) else t1
        seg_end = min(seg_end, t1)
        sig = max(1, tp.signature)
        k = 0
        t = tp.time
        while t < seg_end:
            if t >= t0:
                out.append((t, k % sig == 0))
            k += 1
            t = tp.time + k * beat
    return out


def mix_nightcore(mixer, bank: "SampleBank", beats, *, speed: float = 1.0,
                  start_ms: float = 0.0, gain: float = 1.0,
                  to_wall=None) -> int:
    """Lay the nightcore overlay into the mixer: clap each beat, finish
    each downbeat, resolved through the normal-set skin chain (skin →
    fallback → synth, so the overlay never goes silent). Returns beats
    laid. ``to_wall`` (map-ms → wall-ms) overrides the constant
    ``(t - start_ms) / speed`` mapping for the WU/WD rate ramp."""
    clap, _ = bank.get(1, "hitclap", 0)
    finish, _ = bank.get(1, "hitfinish", 0)
    laid = 0
    for t, downbeat in beats:
        pcm = finish if downbeat else clap
        w = to_wall(t) if to_wall is not None else (t - start_ms) / speed
        mixer.mix_at(w, pcm, volume=NIGHTCORE_GAIN * gain)
        laid += 1
    return laid


# --- ModNightcore beat overlay (NC-mod-gated, distinct from the metronome) -----

NIGHTCORE_MOD_GAIN = 0.5      # nightcore-kick/clap/hat/finish drums


def nightcore_mod_events(timings, t0: float, t1: float,
                         play_hats: bool = True) -> list[tuple[float, str]]:
    """osu! ModNightcore beat-overlay schedule (osu.Game/Rulesets/Mods/
    ModNightcore.NightcoreBeatContainer) — the drums osu! plays on each beat
    while the Nightcore mod is active. DISTINCT from nightcore_beats (the
    general 'metronome' clap/finish). Half-beat grid (BeatSyncedContainer
    Divisor=2): within a 4-bar segment, kick on beats 1 & 3, clap on 2 & 4,
    hat on the off-beats (the '&'s), plus a finish cymbal at the start of every
    4th bar. The timing point's SIGNATURE drives the measure (3/4 ⇒ %6 with
    clap on beat position 3). Returns [(time_ms, sound)] with sound in
    {'kick','clap','hat','finish'} for every step inside [t0, t1). ``play_hats``
    gates the off-beat hats (osu: SliderTickRate%2==0). Beat length sanity-
    capped at 60 ms (>1000 BPM aspire lines)."""
    reds = timings.original_points
    out: list[tuple[float, str]] = []
    for i, tp in enumerate(reds):
        beat = max(60.0, tp.beat_length_base)
        half = beat / 2.0
        seg_end = reds[i + 1].time if i + 1 < len(reds) else t1
        seg_end = min(seg_end, t1)
        sig = max(1, tp.signature)
        seg_len = sig * 8                      # beatsPerBar * Divisor(2) * 4 bars
        triplet = (sig % 3 == 0)
        mod = 6 if triplet else 4
        clap_pos = 3 if triplet else 2
        k = 0
        t = tp.time
        while t < seg_end:
            if t >= t0:
                bseg = k % seg_len
                r = bseg % mod
                if r == 0:
                    out.append((t, "kick"))
                elif r == clap_pos:
                    out.append((t, "clap"))
                elif play_hats:
                    out.append((t, "hat"))
                if bseg == 0:
                    out.append((t, "finish"))
            k += 1
            t = tp.time + k * half
    return out


def mix_nightcore_mod(mixer, bank: "SampleBank", events, *, speed: float = 1.0,
                      start_ms: float = 0.0, gain: float = 1.0,
                      to_wall=None) -> int:
    """Lay the ModNightcore drum overlay into the mixer from the SKIN's
    nightcore-kick/-clap/-hat/-finish samples (skin chain ONLY — no synth
    fallback: a skin that ships SILENT nightcore samples plays near-nothing,
    a skin that omits one plays nothing for that voice). ``events`` is
    nightcore_mod_events' [(time_ms, sound)] list. Returns samples laid.
    ``to_wall`` (map-ms → wall-ms) overrides the constant (t-start)/speed
    mapping for the WU/WD rate ramp."""
    samples = {name: bank.nc_sample(f"nightcore-{name}")
               for name in ("kick", "clap", "hat", "finish")}
    laid = 0
    for t, name in events:
        pcm = samples.get(name)
        if pcm is None:
            continue
        w = to_wall(t) if to_wall is not None else (t - start_ms) / speed
        mixer.mix_at(w, pcm, volume=NIGHTCORE_MOD_GAIN * gain)
        laid += 1
    return laid


# --- sample bank -----------------------------------------------------------------

def synth_style_for(has_custom_skin: bool,
                    legacy_defaults: bool = False) -> str:
    """Which synthesized-default bank a render's missing samples use —
    the SAME league rule as the HUD visuals (owner decision 2026-07-08):
    skinless → "argon" (the lazer sound family); a custom skin's gaps →
    "legacy" (the classic osu character); --legacy-defaults → "legacy"
    always."""
    return "legacy" if (has_custom_skin or legacy_defaults) else "argon"


class SampleBank:
    """Resolve (set, sound, index) → 48 kHz stereo float32 PCM through
    BEATMAP(custom index) → SKIN chain → SYNTH default, with caching and
    per-source bookkeeping for the render report. `synth_style` picks
    the synthesized-default bank ("argon" | "legacy" —
    synth_style_for's league rule)."""

    def __init__(self, skin=None, beatmap_dir: Path | None = None,
                 use_beatmap_samples: bool = True,
                 synth_style: str = "argon"):
        self.skin = skin
        self.use_beatmap_samples = use_beatmap_samples
        self.synth_style = synth_style
        self._beatmap_files: dict[str, Path] = {}
        if beatmap_dir is not None:
            d = Path(beatmap_dir)
            if d.is_dir():
                for p in sorted(d.rglob("*")):
                    if p.is_file() and p.suffix.lower() in SAMPLE_EXTS:
                        self._beatmap_files.setdefault(p.name.lower(), p)
        self._pcm_cache: dict[Path, np.ndarray | None] = {}
        self._cache: dict[tuple[int, str, int], tuple[np.ndarray, str]] = {}
        self.sources: dict[str, str] = {}   # "<name>[idx]" → source label

    def get(self, set_id: int, sound: str, index: int = 0,
            ) -> tuple[np.ndarray, str]:
        key = (set_id, sound, index)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        name = f"{SET_NAMES[set_id]}-{sound}" if set_id in SET_NAMES else sound
        pcm, src = self._resolve(name, index)
        self._cache[key] = (pcm, src)
        self.sources[f"{name}:{index}"] = src
        return pcm, src

    def _resolve(self, name: str, index: int) -> tuple[np.ndarray, str]:
        # 1. beatmap folder by custom index (index 0 = skin's defaults)
        if self.use_beatmap_samples and index > 0:
            base = name if index == 1 else f"{name}{index}"
            for ext in SAMPLE_EXTS:
                p = self._beatmap_files.get(f"{base}{ext}".lower())
                if p is None:
                    continue
                pcm = self._decode(p)
                if pcm is not None:
                    return pcm, "beatmap"
        # 2. skin chain (SKIN→FALLBACK→LOCAL, §3.1 GetSample)
        if self.skin is not None:
            p = self.skin.find_sample(name)
            if p is not None:
                pcm = self._decode(p)
                if pcm is not None:
                    return pcm, "skin"
        # 3. deterministic synthesized default (league-selected bank)
        return synth_sample(name, style=self.synth_style), "synth"

    def nc_sample(self, base: str) -> np.ndarray | None:
        """ModNightcore sample (nightcore-kick/-clap/-hat/-finish): the SKIN
        chain (skin → fallback → local) first, then the bundled osu! DEFAULT as
        the FINAL fallback. A skin that ships a SILENT nightcore file plays
        (near-)silence (skin wins); a skin that OMITS it falls back to the
        default (osu!'s default-skin parity). No synth."""
        if self.skin is not None:
            p = self.skin.find_sample(base)
            if p is not None:
                pcm = self._decode(p)
                if pcm is not None:
                    return pcm
        # bundled osu! default — reached only when ABSENT from the skin chain
        if _DEFAULT_NC_DIR.is_dir():
            for ext in SAMPLE_EXTS:
                p = _DEFAULT_NC_DIR / f"{base}{ext}"
                if p.is_file():
                    pcm = self._decode(p)
                    if pcm is not None:
                        return pcm
        return None

    def _decode(self, path: Path) -> np.ndarray | None:
        """Decode a sample file; zero-byte files mean SILENCE (the classic
        blanking convention); undecodable files fall through (None)."""
        if path in self._pcm_cache:
            return self._pcm_cache[path]
        pcm: np.ndarray | None
        try:
            if path.stat().st_size == 0:
                pcm = np.zeros((1, 2), dtype=np.float32)
            else:
                pcm = decode_to_pcm(path)
                if len(pcm) == 0:
                    pcm = np.zeros((1, 2), dtype=np.float32)
        except (AudioError, OSError):
            pcm = None
        self._pcm_cache[path] = pcm
        return pcm

    def source_counts(self) -> dict[str, int]:
        out = {"beatmap": 0, "skin": 0, "synth": 0}
        for src in self.sources.values():
            out[src] = out.get(src, 0) + 1
        return out


# --- synthesized defaults ----------------------------------------------------------

def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE


def _noise(dur: float, seed_name: str) -> np.ndarray:
    rng = np.random.default_rng(zlib.crc32(seed_name.encode()))
    n = rng.standard_normal(int(dur * SAMPLE_RATE)).astype(np.float32)
    # cheap low-pass (moving average) so it reads as a soft hiss, not static
    k = np.ones(8, dtype=np.float32) / 8.0
    return np.convolve(n, k, mode="same")


def _edge_fade(x: np.ndarray, ms: float = 3.0) -> np.ndarray:
    n = min(len(x), int(ms / 1000.0 * SAMPLE_RATE))
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def _stereo(x: np.ndarray) -> np.ndarray:
    return np.repeat(x.astype(np.float32)[:, None], 2, axis=1)


def synth_failsound() -> np.ndarray:
    """The osu! fail sample ("Gameplay/failsound") — synthesized, no BASS/
    ppy assets. osu's failsound is a descending pitch sweep with a drum
    thud (the "wheee-oomph" you hear the instant you die). We build an
    exponential glide 420→55 Hz over ~1.7 s under an exp decay, plus a
    detuned sub octave for body and a short noise thud at the impact.
    Played ONCE at the death point (FailAnimationContainer.failSample.Play).
    """
    dur = 1.7
    t = _t(dur)
    p = t / dur                                   # 0..1
    f0, f1 = 420.0, 55.0
    freq = (f0 * (f1 / f0) ** p).astype(np.float32)      # exponential sweep
    phase = 2.0 * math.pi * np.cumsum(freq) / SAMPLE_RATE
    tone = np.sin(phase) + 0.5 * np.sin(0.5 * phase)     # + sub octave
    env = np.exp(-2.2 * p).astype(np.float32)            # long decay
    thud = 0.4 * _noise(dur, "failsound") * np.exp(-9.0 * p)
    x = 0.55 * tone * env + thud
    x = _edge_fade(x, 6.0)
    peak = float(np.max(np.abs(x))) or 1.0
    x = (x / peak) * 0.85
    return _stereo(x.astype(np.float32))


def synth_sample(name: str, style: str = "argon") -> np.ndarray:
    """Deterministic placeholder samples for the full §3.4 surface —
    used when neither the beatmap nor any skin in the chain provides the
    file. Two banks (synth_style_for's league rule): "argon" = the
    lazer-ish family this engine always synthesized; "legacy" = the
    classic-osu character. Loopable sounds carry edge fades so tiling
    doesn't click."""
    if style == "legacy":
        return _synth_legacy(name)
    return _synth_argon(name)


def _synth_argon(name: str) -> np.ndarray:
    """The ARGON (lazer-family) synth bank — the original bank."""
    base = name.split("-", 1)[-1]          # strip the set prefix
    if base == "hitnormal":
        t = _t(0.05)
        x = (0.75 * np.sin(2 * math.pi * 500.0 * t) * np.exp(-70.0 * t)
             + 0.3 * _noise(0.05, name) * np.exp(-220.0 * t))
    elif base == "hitwhistle":
        t = _t(0.12)
        x = 0.55 * np.sin(2 * math.pi * 1250.0 * t) * np.exp(-25.0 * t)
    elif base == "hitfinish":
        t = _t(0.4)
        x = (0.4 * _noise(0.4, name)
             + 0.3 * np.sin(2 * math.pi * 880.0 * t)
             + 0.2 * np.sin(2 * math.pi * 1320.0 * t)) * np.exp(-8.0 * t)
    elif base == "hitclap":
        t = _t(0.06)
        x = 0.8 * _noise(0.06, name) * np.exp(-80.0 * t)
    elif base == "slidertick":
        t = _t(0.02)
        x = 0.5 * np.sin(2 * math.pi * 3000.0 * t) * np.exp(-300.0 * t)
    elif base == "sliderslide":
        x = 0.20 * _noise(0.25, name)
        x = _edge_fade(x)
    elif base == "sliderwhistle":
        t = _t(0.25)
        x = 0.16 * np.sin(2 * math.pi * 1100.0 * t)
        x = _edge_fade(x)
    elif base == "spinnerspin":
        t = _t(0.3)
        x = 0.16 * _noise(0.3, name) * (0.7 + 0.3 * np.sin(2 * math.pi * 12.0 * t))
        x = _edge_fade(x)
    elif base == "spinnerbonus":
        t = _t(0.15)
        x = 0.5 * np.sin(2 * math.pi * 1760.0 * t) * np.exp(-20.0 * t)
    else:   # unknown name — quiet click, never silence
        t = _t(0.03)
        x = 0.4 * np.sin(2 * math.pi * 1000.0 * t) * np.exp(-150.0 * t)
    return _stereo(x)


# --- the LEGACY synth bank (classic-osu character, owner 2026-07-08) -----------------

def _lowpass(x: np.ndarray, k: int) -> np.ndarray:
    """Cheap moving-average low-pass (k-sample window)."""
    if k <= 1:
        return x
    kern = np.ones(k, dtype=np.float32) / float(k)
    return np.convolve(x, kern, mode="same").astype(np.float32)


def _highpass(x: np.ndarray, k: int) -> np.ndarray:
    return (x - _lowpass(x, k)).astype(np.float32)


def _lg_noise(dur: float, name: str) -> np.ndarray:
    """Raw (unfiltered) deterministic noise, decorrelated from the argon
    bank by the lg: seed prefix."""
    rng = np.random.default_rng(zlib.crc32(f"lg:{name}".encode()))
    return rng.standard_normal(int(dur * SAMPLE_RATE)).astype(np.float32)


def _synth_legacy(name: str) -> np.ndarray:
    """The LEGACY synth bank: procedural approximations of the CLASSIC
    osu default sample character (no ppy files) — hitnormal = the short
    soft tock, whistle = the two-tone whistle, finish = a cymbal-ish
    crash decay, clap = a multi-burst clap. The soft set is duller/
    gentler and the drum set punchier/lower (tonal + filter variations
    of the normal set). Deterministic like the argon bank; loopables
    carry edge fades."""
    set_name, dash, base = name.partition("-")
    if not dash:                       # un-prefixed (spinnerspin/bonus)
        set_name, base = "", set_name
    soft = set_name == "soft"
    drum = set_name == "drum"

    if base == "hitnormal":
        # the familiar short tock: mid thump + a filtered noise snap
        dur = 0.07
        t = _t(dur)
        n = _lg_noise(dur, name)
        if soft:
            x = (0.38 * np.sin(2 * math.pi * 330.0 * t) * np.exp(-65.0 * t)
                 + 0.30 * _lowpass(n, 24) * np.exp(-120.0 * t))
        elif drum:
            x = (0.70 * np.sin(2 * math.pi * 175.0 * t) * np.exp(-50.0 * t)
                 + 0.35 * _lowpass(_highpass(n, 96), 6)
                 * np.exp(-160.0 * t))
        else:
            x = (0.55 * np.sin(2 * math.pi * 440.0 * t) * np.exp(-85.0 * t)
                 + 0.45 * _lowpass(_highpass(n, 48), 4)
                 * np.exp(-170.0 * t))
    elif base == "hitwhistle":
        # the two-tone whistle: pitch steps high→low mid-sample
        dur = 0.13 if drum else 0.22
        t = _t(dur)
        f_hi, f_lo = (2093.0, 1568.0)          # C7 → G6
        if soft:
            f_hi, f_lo = f_hi * 0.75, f_lo * 0.75
        freq = np.where(t < dur * 0.45, f_hi, f_lo).astype(np.float32)
        phase = 2.0 * math.pi * np.cumsum(freq) / SAMPLE_RATE
        amp = 0.30 if soft else 0.42
        x = amp * np.sin(phase) * np.exp(-9.0 * t)
        x += 0.3 * amp * np.sin(2.0 * phase) * np.exp(-14.0 * t)
    elif base == "hitfinish":
        # cymbal-ish crash: bright high-passed noise + inharmonic shimmer
        dur = 0.45 if soft else (0.5 if drum else 0.7)
        t = _t(dur)
        n = _highpass(_lg_noise(dur, name), 10 if soft else 5)
        x = (0.35 if soft else 0.5) * n * np.exp(-4.5 * t)
        for i, f in enumerate((3135.0, 4699.0, 6271.0)):
            x += 0.07 * np.sin(2 * math.pi * f * t) * np.exp(-(6.0 + i) * t)
        if drum:
            x += 0.40 * np.sin(2 * math.pi * 100.0 * t) * np.exp(-18.0 * t)
    elif base == "hitclap":
        # the clap burst: 3 quick taps into a band-passed body decay
        dur = 0.12
        t = _t(dur)
        n = _lowpass(_highpass(_lg_noise(dur, name), 64),
                     12 if soft else 6)
        env = np.exp(-60.0 * t)
        for tap_ms, amp in ((0.0, 0.5), (11.0, 0.7), (22.0, 1.0)):
            i0 = int(tap_ms / 1000.0 * SAMPLE_RATE)
            tap = np.zeros_like(env)
            tt = t[: len(t) - i0]
            tap[i0:] = np.exp(-220.0 * tt)
            env = np.maximum(env, amp * tap)
        x = (0.55 if soft else 0.8) * n * env
        if drum:
            x += 0.30 * np.sin(2 * math.pi * 160.0 * t) * np.exp(-70.0 * t)
    elif base == "slidertick":
        dur = 0.02
        t = _t(dur)
        f = 1200.0 if drum else (1500.0 if soft else 1900.0)
        x = (0.35 if soft else 0.45) * np.sin(2 * math.pi * f * t) \
            * np.exp(-320.0 * t)
    elif base == "sliderslide":
        # rolling filtered noise (duller than the argon hiss)
        x = 0.18 * _lowpass(_lg_noise(0.25, name), 32 if soft else 16)
        if drum:
            t = _t(0.25)
            x += 0.05 * np.sin(2 * math.pi * 90.0 * t)
        x = _edge_fade(x)
    elif base == "sliderwhistle":
        t = _t(0.25)
        f = 1975.0 * (0.75 if soft else 1.0)
        trem = 1.0 + 0.25 * np.sin(2 * math.pi * 5.0 * t)
        x = 0.13 * np.sin(2 * math.pi * f * t) * trem
        x = _edge_fade(x)
    elif base == "spinnerspin":
        t = _t(0.3)
        x = 0.15 * _lowpass(_lg_noise(0.3, name), 24) \
            * (0.7 + 0.3 * np.sin(2 * math.pi * 8.0 * t))
        x = _edge_fade(x)
    elif base == "spinnerbonus":
        t = _t(0.2)
        x = 0.30 * (np.sin(2 * math.pi * 1568.0 * t)
                    + 0.6 * np.sin(2 * math.pi * 2093.0 * t)) \
            * np.exp(-15.0 * t)
    else:   # unknown name — quiet classic click, never silence
        t = _t(0.03)
        x = 0.35 * np.sin(2 * math.pi * 800.0 * t) * np.exp(-140.0 * t)
    return _stereo(x.astype(np.float32))


# --- mixing --------------------------------------------------------------------------

@dataclass
class HitsoundMixStats:
    oneshots: int = 0
    loop_ms: float = 0.0
    peak_before: float = 0.0   # track |peak| before hitsounds (the music bed)
    peak_after: float = 0.0    # track |peak| after mixing hitsounds


def mix_hitsounds(mixer, bank: SampleBank, oneshots: list[OneShot],
                  loops: list[Loop], *, speed: float = 1.0,
                  start_ms: float = 0.0, gain: float = 1.0,
                  to_wall=None,
                  ) -> HitsoundMixStats:
    """Mix collected events into the AudioMixer track. Gameplay-ms →
    wall-ms via (t - start_ms) / speed (samples keep their natural pitch
    under rate mods — stable behaviour). mix_at clips events outside the
    render window. ``to_wall`` (map-ms → wall-ms) overrides the constant
    mapping for the WU/WD rate ramp (loop durations then follow the ramp so a
    slide under Wind Up compresses toward the end); samples still play at
    natural pitch, matching lazer (hitsounds are not ramped)."""
    def _w(t: float) -> float:
        return to_wall(t) if to_wall is not None else (t - start_ms) / speed

    before = np.abs(mixer.buf).max() if len(mixer.buf) else 0.0
    for e in oneshots:
        pcm, _src = bank.get(e.set_id, e.sound, e.index)
        mixer.mix_at(_w(e.time_ms), pcm, volume=e.volume * gain)
    loop_ms = 0.0
    for lp in loops:
        pcm, _src = bank.get(lp.set_id, lp.sound, lp.index)
        dur_ms = (_w(lp.t1) - _w(lp.t0)) if to_wall is not None \
            else (lp.t1 - lp.t0) / speed
        n = int(dur_ms / 1000.0 * SAMPLE_RATE)
        if n <= 0 or len(pcm) == 0:
            continue
        reps = int(math.ceil(n / len(pcm)))
        tiled = np.tile(pcm, (reps, 1))[:n]
        mixer.mix_at(_w(lp.t0), tiled, volume=lp.volume * gain)
        loop_ms += dur_ms
    after = np.abs(mixer.buf).max() if len(mixer.buf) else 0.0
    return HitsoundMixStats(oneshots=len(oneshots), loop_ms=loop_ms,
                            peak_before=float(before),
                            peak_after=float(after))
