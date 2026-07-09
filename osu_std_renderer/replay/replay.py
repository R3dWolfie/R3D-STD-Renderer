"""std .osr decode + 2D cursor interpolation — adapted from the production
catch renderer's replay.py (osu-catch/osu_catch_renderer/replay.py), extended
from 1D catcher-x to the std (x, y, keys) frame.

RENDER_PLAN.md §5.5 — the replay→cursor contract the ruleset phase must obey
(documented here; the full ruleset ships in a later phase):

  * the .osr is parsed by osrparse (reference used rplpa); the RNG seed
    frame (time_delta == -12345) is stripped;
  * playback replays EVERY INTERMEDIATE INTEGER MILLISECOND so low draw
    rates never skip frames: per tick, while
    `replayTime + frames[idx].Time <= floor(nTime)` advance the index and
    apply cursor.SetPos(MouseX, MouseY) + the key bitfield (feeding
    UpdateClickFor/UpdateNormalFor/UpdatePostFor for judgments);
  * when no frame is consumed on a tick, the cursor position is
    INTERPOLATED between the neighbouring frames (cursor_at below);
  * Relax/Autopilot get synthesized clicks/aim.

Frame times are in MAP time (rate mods don't compress them), so they line
up 1:1 with beatmap object times — same finding as catch.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from osrparse import Replay

from .lazer_mods import (LAZER_GAME_VERSION,
                         difficulty_adjust_from_mods,
                         mirror_from_mods,
                         random_from_mods,
                         rate_adjust_from_mods,
                         rate_ramp_from_mods,
                         read_lazer_mods,
                         transform_from_mods)

# osrparse seeds the last frame with this sentinel time_delta (RNG seed).
_SEED_DELTA = -12345

# std key bitmask per frame (osu! wiki / osr format)
KEY_M1 = 1
KEY_M2 = 2
KEY_K1 = 4
KEY_K2 = 8
KEY_SMOKE = 16

# mod bits (osu! wiki / .osr Mods) relevant to fail immunity. A play under a
# mod that overrides failing NEVER reaches the fail screen — these implement
# osu.Game IApplicableFailOverride with PerformFail() => false:
#   * OsuModNoFail    (ModNoFail)        — bit 1
#   * OsuModAutopilot (Relax2)           — bit 8192
# (Relax does NOT override failing — you can still fail while relaxing, so it
# is deliberately absent. Cinema/Autoplay don't submit replays.)
MOD_NOFAIL = 0x1
MOD_AUTOPILOT = 0x2000
_FAIL_IMMUNE_MODS = MOD_NOFAIL | MOD_AUTOPILOT

# life-bar health at/under this counts as "dead" (osrparse LifeBarState.life
# is 0..1; use a tiny epsilon so float noise near 0 still trips).
_DEAD_HEALTH_EPS = 0.001


class ReplayParseError(RuntimeError):
    pass


def detect_fail_time(replay) -> float | None:
    """The death point of a replay, or None for a PASS.

    Ported from the osu! fail model: a play fails the instant its health
    (HP bar) reaches zero (osu.Game/Rulesets/Scoring/HealthProcessor.cs —
    ``HasFailed => Health.Value == HealthProcessor.MinimumHealth`` where the
    minimum is 0). We read the AUTHORITATIVE record of that curve — the
    .osr's life-bar graph (osrparse ``Replay.life_bar_graph`` = a list of
    ``LifeBarState(time, life)`` with life in 0..1) — and return the FIRST
    state whose life hits 0.

    Safeguards:
      * FAIL-IMMUNE MODS: NoFail / Autopilot override failing in osu!
        (IApplicableFailOverride.PerformFail() => false), so the play can
        never fail regardless of the HP curve — return None.
      * EMPTY / MISSING life-bar graph (some lazer .osr omit it): be
        CONSERVATIVE and return None (PASS). We NEVER fail a replay off our
        own HP simulation — a submitted replay demonstrably *finished the
        map* unless the lifebar explicitly records a zero, so only that
        explicit evidence trips a fail. This keeps false-fails impossible on
        the pass path.
    """
    try:
        mods = int(replay.mods)
    except (TypeError, ValueError, AttributeError):
        mods = 0
    if mods & _FAIL_IMMUNE_MODS:
        return None
    graph = getattr(replay, "life_bar_graph", None) or []
    for state in graph:
        try:
            if float(state.life) <= _DEAD_HEALTH_EPS:
                return float(int(state.time))
        except (TypeError, ValueError, AttributeError):
            continue
    return None


@dataclass(frozen=True)
class StdFrame:
    """One replay frame: cursor position in osu!px (512×384) + key bitmask."""
    time_ms: int
    x: float
    y: float
    keys: int

    @property
    def pressed(self) -> bool:
        return bool(self.keys & (KEY_M1 | KEY_M2 | KEY_K1 | KEY_K2))


@dataclass(frozen=True)
class ReplayMeta:
    mode: int
    beatmap_md5: str
    player_name: str
    mods: int
    score: int
    max_combo: int
    count_300: int
    count_100: int
    count_50: int
    count_geki: int
    count_katu: int
    count_miss: int
    accuracy: float
    grade: str
    game_version: int = 0    # <30000000 = osu!stable, else lazer
    death_ms: int | None = None
    played_at: str = ""      # .osr timestamp → "Played on <date>" (results)
    # lazer mod acronyms read from the .osr's appended ScoreInfo blob
    # (empty for stable replays / when the blob is absent);
    # has_classic_mod = the Classic mod ("CL") is in that list.
    lazer_mods: tuple[str, ...] = ()
    has_classic_mod: bool = False
    # Difficulty Adjust (DA) overrides read from the ScoreInfo blob (lazer
    # only). Each is the custom AR/CS/OD/HP or None (= keep the beatmap's
    # value). da_extended_limits mirrors the mod's ExtendedLimits toggle
    # (allows AR/CS/OD/HP beyond 0..10). See lazer_mods.read_difficulty_adjust.
    da_ar: float | None = None
    da_cs: float | None = None
    da_od: float | None = None
    da_hp: float | None = None
    da_extended_limits: bool = False
    # Custom-rate mods (lazer DT/NC/HT/DC with a per-play ``speed_change`` that
    # differs from the fixed legacy bitmask rate). rate_override is the
    # effective clock rate (None = plain bitmask rate — DT 1.5 / HT 0.75 /
    # nomod 1.0 — so legacy replays are byte-identical). rate_pitch mirrors
    # ModNightcore/ModDaycore: NC/DC shift the audio pitch with the rate,
    # DT/HT change tempo only. See lazer_mods.rate_adjust_from_mods.
    rate_override: float | None = None
    rate_pitch: bool = False
    # Rate-RAMP mods (lazer Wind Up / Wind Down): the clock rate RAMPS linearly
    # from ramp_initial to ramp_final over the map (see replay/lazer_mods.py
    # RateRamp + timewarp.TimeWarp). ramp_initial is None for every non-ramp
    # replay (constant rate / bitmask / nomod) so those stay byte-identical.
    # ramp_pitch mirrors ModTimeRamp.AdjustPitch (True = pitch follows rate).
    ramp_acronym: str = ""
    ramp_initial: float | None = None
    ramp_final: float | None = None
    ramp_pitch: bool = False
    # Position mods (lazer-only). Mirror (MR): the reflection axis
    # ("horizontal"/"vertical"/"both", "" = no MR) — objects are flipped about
    # the playfield centre. Random (RD): the .NET System.Random seed that
    # scatters the objects (None = no RD) plus its AngleSharpness. Both are
    # empty/None for every non-MR/RD replay, so those renders are byte-identical.
    # See beatmap/mods_position.py.
    mirror_reflection: str = ""
    random_seed: int | None = None
    random_angle_sharpness: float = 7.0
    # Transform-family "fun" mods (GR/DF/SI/WG/TR): per-object visual entrance
    # animation only — the beatmap geometry, the cursor and the judgement are
    # untouched (see render/transform_mods.py). transform_acronym is "" for
    # every non-transform replay (the gate that keeps those renders identical);
    # transform_start_scale is the GR/DF starting size (else 1.0) and
    # transform_strength the WG wiggle strength (else 1.0).
    transform_acronym: str = ""
    transform_start_scale: float = 1.0
    transform_strength: float = 1.0

    @property
    def has_transform(self) -> bool:
        """True when the replay carries a GR/DF/SI/WG/TR entrance-animation
        mod (purely visual — judgement/reconcile stay on the real positions)."""
        return bool(self.transform_acronym)

    @property
    def has_mirror(self) -> bool:
        """True when the replay carries a Mirror mod (objects reflected)."""
        return bool(self.mirror_reflection)

    @property
    def has_random(self) -> bool:
        """True when the replay carries a reproducible Random mod (a seed is
        present). The gate that keeps every non-RD render byte-identical."""
        return self.random_seed is not None

    @property
    def fail_time(self) -> float | None:
        """Death point in MAP ms, or None for a pass (alias of death_ms)."""
        return None if self.death_ms is None else float(self.death_ms)

    @property
    def has_difficulty_adjust(self) -> bool:
        """True when the replay carries DA overrides that actually change a
        stat (an all-absent DA is a no-op and leaves the beatmap untouched)."""
        return (self.da_ar is not None or self.da_cs is not None
                or self.da_od is not None or self.da_hp is not None)

    @property
    def difficulty_adjust(self) -> dict | None:
        """The DA override mapping for load_full, or None when absent (the
        gate that keeps non-DA renders on the untouched beatmap path)."""
        if not self.has_difficulty_adjust:
            return None
        return {"ar": self.da_ar, "cs": self.da_cs, "od": self.da_od,
                "hp": self.da_hp, "extended": self.da_extended_limits}

    @property
    def has_rate_override(self) -> bool:
        """True when the replay carries a custom clock rate (a lazer
        DT/NC/HT/DC whose speed_change differs from the fixed bitmask rate).
        The gate that keeps plain bitmask/nomod renders on the legacy path."""
        return self.rate_override is not None

    @property
    def has_rate_ramp(self) -> bool:
        """True when the replay carries a Wind Up / Wind Down ramp (WU/WD).
        The gate that keeps every constant-rate/nomod render byte-identical —
        the time-varying warp only activates when this is True."""
        return self.ramp_initial is not None

    @property
    def rate_ramp(self) -> tuple[float, float, bool] | None:
        """The ramp descriptor ``(initial, final, adjust_pitch)`` for building
        a timewarp.TimeWarp, or None when the replay carries no WU/WD mod."""
        if not self.has_rate_ramp:
            return None
        return (self.ramp_initial, self.ramp_final, self.ramp_pitch)


def parse_replay(path: Path) -> tuple[list[StdFrame], ReplayMeta]:
    path = Path(path)
    if not path.exists():
        raise ReplayParseError(f"replay not found: {path}")
    try:
        r = Replay.from_path(path)
    except Exception as e:  # noqa: BLE001 - osrparse raises bare exceptions
        raise ReplayParseError(f"osrparse failed: {e}") from e

    frames: list[StdFrame] = []
    t = 0
    first = True
    for ev in r.replay_data or []:
        delta = int(getattr(ev, "time_delta", 0))
        if delta == _SEED_DELTA:
            continue  # RNG seed frame, not a real position
        if first:
            first = False
            # osu! sometimes prefixes a garbage placeholder frame with a huge
            # negative delta that would shift the whole timeline; a normal
            # AudioLeadIn is only ~1-2 s, so anything beyond that is the
            # placeholder — ignore its jump. (Catch-proven heuristic.)
            if delta < -5000:
                delta = 0
        t += delta
        x = getattr(ev, "x", None)
        y = getattr(ev, "y", None)
        if x is None or y is None:
            continue  # non-positional frame
        keys = getattr(ev, "keys", 0)
        try:
            keys = int(keys)
        except (TypeError, ValueError):
            keys = 0
        frames.append(StdFrame(time_ms=max(t, 0), x=float(x), y=float(y), keys=keys))
    frames.sort(key=lambda f: f.time_ms)

    # std accuracy: 300/100/50 weighted (300 = full weight)
    total = r.count_300 + r.count_100 + r.count_50 + r.count_miss
    if total > 0:
        acc = (300 * r.count_300 + 100 * r.count_100 + 50 * r.count_50) / (300 * total)
    else:
        acc = 1.0

    # fail detection from the life-bar graph — NoFail/Autopilot exempt,
    # empty-lifebar conservative (see detect_fail_time).
    _ft = detect_fail_time(r)
    death_ms: int | None = None if _ft is None else int(_ft)

    # play date from the .osr timestamp (osrparse → datetime); "" fail-soft
    played_at = ""
    ts = getattr(r, "timestamp", None)
    if ts is not None:
        try:
            played_at = ts.strftime("%d %b %Y")
        except Exception:  # noqa: BLE001 — odd/naive datetime → skip
            played_at = str(ts)[:10]

    # lazer Classic-mod detection: read the mod-acronym list from the
    # .osr's appended ScoreInfo blob (lazer replays only — see
    # lazer_mods.py; the legacy `mods` bitmask can't encode CL).
    gv = int(getattr(r, "game_version", 0) or 0)
    lazer_mods: tuple[str, ...] = ()
    has_classic = False
    da_ar = da_cs = da_od = da_hp = None
    da_ext = False
    rate_override: float | None = None
    rate_pitch = False
    ramp_acronym = ""
    ramp_initial: float | None = None
    ramp_final: float | None = None
    ramp_pitch = False
    mirror_reflection = ""
    random_seed: int | None = None
    random_angle_sharpness = 7.0
    transform_acronym = ""
    transform_start_scale = 1.0
    transform_strength = 1.0
    if gv >= LAZER_GAME_VERSION:
        mlist = read_lazer_mods(path)
        if mlist is not None:
            lazer_mods = tuple(m["acronym"] for m in mlist)
            has_classic = any(a.upper() == "CL" for a in lazer_mods)
            # Difficulty Adjust: the legacy `mods` bitmask can't encode DA, so
            # its custom AR/CS/OD/HP live only in the ScoreInfo blob settings.
            da = difficulty_adjust_from_mods(mlist)
            if da is not None:
                da_ar, da_cs, da_od, da_hp = da.ar, da.cs, da.od, da.hp
                da_ext = da.extended_limits
            # Custom-rate DT/NC/HT/DC: surface an override ONLY when the
            # speed_change differs from the fixed bitmask rate. A default-rate
            # DT/NC/HT/DC (is_default) leaves rate_override None so it renders
            # exactly like the legacy bitmask path (byte-identical).
            ra = rate_adjust_from_mods(mlist)
            if ra is not None and not ra.is_default:
                rate_override = ra.speed
                rate_pitch = ra.pitch
            # Rate-RAMP (Wind Up / Wind Down): a continuously-varying clock
            # rate the constant `speed` can't represent. Incompatible with the
            # rate-adjust family above, so it never co-occurs with a rate
            # override. Surfaced as ramp_initial/final/pitch for the timewarp.
            rr = rate_ramp_from_mods(mlist)
            if rr is not None:
                ramp_acronym = rr.acronym
                ramp_initial = rr.initial
                ramp_final = rr.final
                ramp_pitch = rr.adjust_pitch
            # Mirror (MR): the reflection axis the objects are flipped about.
            mr = mirror_from_mods(mlist)
            if mr is not None:
                mirror_reflection = mr
            # Random (RD): the .NET Random seed that repositions the objects.
            # A replay persists the actual seed used; without one the positions
            # can't be reproduced, so leave random_seed None (renders untouched).
            rd = random_from_mods(mlist)
            if rd is not None and rd.seed is not None:
                random_seed = rd.seed
                random_angle_sharpness = rd.angle_sharpness
            # Transform-family (GR/DF/SI/WG/TR): a per-object entrance
            # animation. Visual only, so no geometry/judgement change — the
            # scene reads transform_acronym to drive the appear transform.
            tr = transform_from_mods(mlist)
            if tr is not None:
                transform_acronym = tr.acronym
                transform_start_scale = tr.start_scale
                transform_strength = tr.strength

    meta = ReplayMeta(
        mode=int(r.mode.value if hasattr(r.mode, "value") else r.mode),
        beatmap_md5=str(getattr(r, "beatmap_hash", "") or ""),
        player_name=r.username,
        mods=int(r.mods),
        score=int(r.score),
        max_combo=int(r.max_combo),
        count_300=int(r.count_300),
        count_100=int(r.count_100),
        count_50=int(r.count_50),
        count_geki=int(getattr(r, "count_geki", 0) or 0),
        count_katu=int(getattr(r, "count_katu", 0) or 0),
        count_miss=int(r.count_miss),
        accuracy=round(acc * 100, 2),
        grade=_grade(r),
        game_version=gv,
        death_ms=death_ms,
        played_at=played_at,
        lazer_mods=lazer_mods,
        has_classic_mod=has_classic,
        da_ar=da_ar,
        da_cs=da_cs,
        da_od=da_od,
        da_hp=da_hp,
        da_extended_limits=da_ext,
        rate_override=rate_override,
        rate_pitch=rate_pitch,
        ramp_acronym=ramp_acronym,
        ramp_initial=ramp_initial,
        ramp_final=ramp_final,
        ramp_pitch=ramp_pitch,
        mirror_reflection=mirror_reflection,
        random_seed=random_seed,
        random_angle_sharpness=random_angle_sharpness,
        transform_acronym=transform_acronym,
        transform_start_scale=transform_start_scale,
        transform_strength=transform_strength,
    )
    return frames, meta


def cursor_at(frames: list[StdFrame], t_ms: float) -> tuple[float, float, int]:
    """Linearly interpolate the cursor at t_ms → (x, y, keys).

    2D extension of catch's catcher_x_at: binary search + lerp. Keys are NOT
    interpolated — the frame at-or-after t carries the held state (key
    edges are only meaningful at exact frame times; the §5.5 per-ms walk in
    the ruleset consumes frames directly, this is for DRAWING)."""
    if not frames:
        return 256.0, 192.0, 0
    if t_ms <= frames[0].time_ms:
        f = frames[0]
        return f.x, f.y, f.keys
    if t_ms >= frames[-1].time_ms:
        f = frames[-1]
        return f.x, f.y, f.keys
    lo, hi = 0, len(frames) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if frames[mid].time_ms < t_ms:
            lo = mid + 1
        else:
            hi = mid
    a, b = frames[lo - 1], frames[lo]
    span = b.time_ms - a.time_ms
    if span <= 0:
        return b.x, b.y, b.keys
    f = (t_ms - a.time_ms) / span
    return a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, b.keys


def _grade(r) -> str:
    """osu!(lazer)-EXACT rank from the replay counts. Ported from
    ScoreProcessor.RankFromScore (accuracy cutoffs X=1/S=.95/A=.9/B=.8/
    C=.7/D=0) + OsuScoreProcessor's std override (a Miss caps S/X at A).
    HD/FL silver variants are a display concern, applied elsewhere."""
    c300, c100, c50, miss = (int(r.count_300), int(r.count_100),
                             int(r.count_50), int(r.count_miss))
    total = c300 + c100 + c50 + miss
    if total == 0:
        return "D"
    acc = (300 * c300 + 100 * c100 + 50 * c50) / (300.0 * total)
    if acc >= 1.0:
        rank = "SS"
    elif acc >= 0.95:
        rank = "S"
    elif acc >= 0.9:
        rank = "A"
    elif acc >= 0.8:
        rank = "B"
    elif acc >= 0.7:
        rank = "C"
    else:
        rank = "D"
    if miss > 0 and rank in ("S", "SS"):
        rank = "A"
    return rank
