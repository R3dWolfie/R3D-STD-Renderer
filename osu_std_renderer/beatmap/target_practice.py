"""Target Practice (TP) — OsuModTargetPractice.cs (ppy/osu, MIT).

TP is a MAJOR *conversion* mod (ModType.Conversion): it DISCARDS the map's
sliders/spinners/circles entirely and rebuilds the beatmap as a sequence of
plain hit circles ("targets") placed on the beat by a seeded algorithm. A
replay is played on the CONVERTED map, so to render it we must reproduce
lazer's exact conversion (which beats become targets, and each target's
position) — otherwise we would draw the ORIGINAL map while the recorded cursor
chased the targets, and every hit would read as a miss.

Reproducibility (why this is exact, not an approximation):

  * The seed is persisted in the .osr. ``OsuModTargetPractice.ApplyToBeatmap``
    does ``Seed.Value ??= RNG.Next()`` before using it, and ``IHasSeed`` mods
    round-trip ``Seed`` through the ScoreInfo blob's ``settings`` — exactly like
    OsuModRandom (see replay/lazer_mods.py ``read_target_practice``).
  * The RNG is .NET ``System.Random`` (``new Random(Seed.Value.Value)``), i.e.
    this renderer's bit-exact ``DotNetRandom`` (verified against dotnet/runtime
    test vectors — see beatmap/dotnet_random.py).
  * The placement algorithm reads NO original geometry: target positions are a
    pure function of (timing points, breaks, last-object time, combo structure,
    CS-derived radius, seed). So MR/HR flips — which only move the discarded
    originals — cannot change a target's position. HR still matters via the
    difficulty (bigger radius); that is already baked into ``circle_radius_l``.

lazer application ORDER: TP is IApplicableToBeatmap, so like OsuModRandom it
runs AFTER PostProcess (stacking). The generated targets are FRESH HitCircles
that never went through stacking (StackHeight 0) and are NOT re-flipped by HR
(HR's IApplicableToHitObject already ran on the now-discarded originals). This
module therefore produces unstacked circles; the parser applies it as the final
beatmap step and does not stack the result.

Everything below is a faithful port of ``OsuModTargetPractice.cs``; each helper
cites its C# counterpart. Numeric constants are the file's ``#region Constants``.
"""
from __future__ import annotations

import math

from .difficulty import Difficulty, Mods
from .dotnet_random import DotNetRandom
from .mods_position import (BASE_X, BASE_Y, PLAYFIELD_CENTRE,
                            _f32, _rotate_away_from_edge)
from .objects.base import HitObject
from .objects.circle import Circle

# --- #region Constants (OsuModTargetPractice.cs) -----------------------------
_MAX_BASE_DISTANCE = 333.0        # jump distance for the last combo
_DISTANCE_CAP = 380.0             # max jump distance after multipliers
_EDGE_ROTATION_MULTIPLIER = 0.75  # RotateAwayFromEdge ratio near the border
_OVERLAP_CHECK_COUNT = 5          # recent circles checked for overlap
_TIMING_PRECISION = 1.0           # Precision.* acceptableDifference (ms)


# --- Precision helpers (osu.Framework Precision, acceptableDifference = 1) ----
def _almost_bigger(a: float, b: float) -> bool:
    """Precision.AlmostBigger(a, b, 1)  ==  a - b > -1."""
    return a - b > -_TIMING_PRECISION


def _definitely_bigger(a: float, b: float) -> bool:
    """Precision.DefinitelyBigger(a, b, 1)  ==  a - b > 1."""
    return a - b > _TIMING_PRECISION


# =========================================================================
# generateBeats  (deterministic — no RNG)
# =========================================================================

def _beats_for_timing_point(tp, end_time: float, timings) -> list[float]:
    """getBeatsForTimingPoint: floored beat times from ``tp.time`` forward,
    while still inside the map AND while ``tp`` is the active red point."""
    beats: list[float] = []
    i = 0
    current = tp.time
    while (not _definitely_bigger(current, end_time)
           and timings.get_original_point_at(current) is tp):
        beats.append(math.floor(current))
        i += 1
        current = tp.time + i * tp.beat_length_base
    return beats


def _first_object_after(original: list[HitObject], t: float):
    """FirstOrDefault(obj => almostBigger(obj.StartTime, t))."""
    for o in original:
        if _almost_bigger(o.start_time, t):
            return o
    return None


def _is_inside_break(original: list[HitObject], breaks, time: float) -> bool:
    """isInsideBreakPeriod: inside a break, or earlier than the first original
    object AFTER that break."""
    for bp in breaks:
        first_after = _first_object_after(original, bp.end_time)
        if (_almost_bigger(time, bp.start_time)
                and (first_after is None
                     or _definitely_bigger(first_after.start_time, time))):
            return True
    return False


def _generate_beats(original: list[HitObject], timings, breaks) -> list[float]:
    """OsuModTargetPractice.generateBeats — the LINQ chain, in order."""
    start_time = original[0].start_time
    end_time = max(o.end_time for o in original)   # GetLastObjectTime = Max(EndTime)

    beats: list[float] = []
    for tp in timings.original_points:             # ControlPointInfo.TimingPoints (red)
        if _definitely_bigger(tp.time, end_time):
            continue
        beats.extend(_beats_for_timing_point(tp, end_time, timings))

    beats = [b for b in beats if _almost_bigger(b, start_time)]
    beats = [b for b in beats if not _is_inside_break(original, breaks, b)]

    # Remove beats too close to the next one (e.g. after a timing-point change).
    for i in range(len(beats) - 2, -1, -1):
        beat = beats[i]
        beat_length = timings.get_original_point_at(beat).beat_length_base
        if not _definitely_bigger(beats[i + 1] - beat, beat_length / 2.0):
            beats.pop(i)

    return beats


# =========================================================================
# fixComboInfo  (deterministic — feeds randomizeCirclePos AND the render)
# =========================================================================

def _fix_combo_info(targets: list[Circle], original: list[HitObject]) -> None:
    """OsuModTargetPractice.fixComboInfo.

    Copy each target's combo index from the closest PRECEDING original object
    (FindLast StartTime <= target time), then regroup so indices are continuous
    and set NewCombo / LastInCombo / IndexInCurrentCombo. lazer's ComboIndex is
    this renderer's 0-based ``combo_set``; combo COLOUR = colours[ComboIndex]
    (GetComboColour) and the drawn NUMBER = IndexInCurrentCombo + 1.
    """
    for t in targets:
        closest_index = 0
        for o in original:                 # FindLast(almostBigger(t, o))
            if _almost_bigger(t.start_time, o.start_time):
                closest_index = o.combo_set
        t._tp_combo_index = closest_index  # type: ignore[attr-defined]

    # GroupBy(ComboIndex): indices are monotonic non-decreasing in time, so a
    # group is a consecutive run of equal index.
    groups: list[list[Circle]] = []
    last_key = None
    for t in targets:
        key = t._tp_combo_index           # type: ignore[attr-defined]
        if last_key is not None and key == last_key:
            groups[-1].append(t)
        else:
            groups.append([t])
            last_key = key

    for gi, group in enumerate(groups):
        group[0].new_combo = True
        group[0].file_new_combo = True
        group[-1].last_in_combo = True
        for j, x in enumerate(group):
            x._final_combo_index = gi     # type: ignore[attr-defined]
            x._index_in_combo = j         # type: ignore[attr-defined]


# =========================================================================
# addHitSamples  (audio only — closest-object fallback, see note)
# =========================================================================

def _closest_hit_object(original: list[HitObject], time: float) -> HitObject:
    """getClosestHitObject: nearest original object in TIME (preceding first)."""
    preceding_index = -1
    for i, o in enumerate(original):
        if o.start_time < time:
            preceding_index = i
    if preceding_index < 0:
        return original[0]
    if preceding_index == len(original) - 1:
        return original[preceding_index]
    nxt = original[preceding_index + 1]
    cur = original[preceding_index]
    return nxt if (nxt.start_time - time) < (time - cur.start_time) else cur


def _add_hit_samples(targets: list[Circle], original: list[HitObject]) -> None:
    """addHitSamples — SIMPLIFIED to lazer's fallback branch (closest object's
    samples). The exact ``getSamplesAtTime`` (repeat-node lookup on sliders) is
    not ported; hitsounds are audio-only and never affect target position/time
    or the cursor reconcile. Each target inherits the closest original object's
    basic hit sound, addition bits and legacy sample bank."""
    for t in targets:
        src = _closest_hit_object(original, t.start_time)
        t.basic_hit_sound = src.basic_hit_sound
        t.hit_sound_bits = src.hit_sound_bits
        t.sample = getattr(src, "sample", 0)


# =========================================================================
# randomizeCirclePos  (THE seeded placement)
# =========================================================================

def _map_range(v: float, from_low: float, from_high: float,
               to_low: float, to_high: float) -> float:
    """mapRange (float arithmetic)."""
    return _f32((v - from_low) * (to_high - to_low) / (from_high - from_low) + to_low)


def _clamp_to_playfield(pos, radius: float):
    """clampToPlayfield — move the circle fully inside, per axis."""
    x, y = pos
    if y < radius:
        y = radius
    elif y > BASE_Y - radius:
        y = BASE_Y - radius
    if x < radius:
        x = radius
    elif x > BASE_X - radius:
        x = BASE_X - radius
    return (_f32(x), _f32(y))


def _check_overlap(preceding: list[Circle], pos, radius: float) -> bool:
    """checkForOverlap: any preceding target within 2r of ``pos``."""
    two_r = radius * 2.0
    for h in preceding:
        hx, hy = h.start_pos_raw
        if math.hypot(hx - pos[0], hy - pos[1]) < two_r:
            return True
    return False


def _randomize_circle_pos(targets: list[Circle], rng: DotNetRandom,
                          radius: float) -> None:
    """OsuModTargetPractice.randomizeCirclePos — the number stream MUST be
    consumed in the game's exact order (the overlap-avoidance loop draws extra
    values, so any divergence there cascades to every later target)."""
    if not targets:
        return

    two_pi = _f32(_f32(math.pi) * 2.0)          # MathF.PI * 2

    def next_single(mx: float = 1.0) -> float:
        return _f32(rng.next_double() * mx)     # (float)(NextDouble() * max)

    direction = _f32(two_pi * next_single())
    max_combo_index = targets[-1]._final_combo_index  # type: ignore[attr-defined]

    for i, obj in enumerate(targets):
        last_pos = (PLAYFIELD_CENTRE[0], PLAYFIELD_CENTRE[1]) if i == 0 \
            else targets[i - 1].start_pos_raw

        combo_index = obj._final_combo_index      # type: ignore[attr-defined]
        if max_combo_index == 0:
            distance = _f32(radius)
        else:
            distance = _map_range(float(combo_index), 0.0, float(max_combo_index),
                                  _f32(radius), _MAX_BASE_DISTANCE)
        if obj.new_combo:
            distance = _f32(distance * 1.5)
        if obj._tp_kiai:                          # type: ignore[attr-defined]
            distance = _f32(distance * 1.2)
        distance = min(_DISTANCE_CAP, distance)

        try_count = 0
        # precedingObjects = last OVERLAP_CHECK_COUNT already-placed targets.
        preceding = targets[max(0, i - _OVERLAP_CHECK_COUNT):i]

        while True:
            if try_count > 0:
                direction = _f32(two_pi * next_single())

            rel = (_f32(distance * _f32(math.cos(direction))),
                   _f32(distance * _f32(math.sin(direction))))
            rel = _rotate_away_from_edge(last_pos, rel, _EDGE_ROTATION_MULTIPLIER)
            direction = _f32(math.atan2(rel[1], rel[0]))

            new_pos = (_f32(last_pos[0] + rel[0]), _f32(last_pos[1] + rel[1]))
            new_pos = _clamp_to_playfield(new_pos, radius)
            obj.start_pos_raw = new_pos
            obj.end_pos_raw = new_pos

            try_count += 1
            if try_count % 10 == 0:
                distance = _f32(distance * 0.9)

            if not (distance >= radius * 2.0
                    and _check_overlap(preceding, new_pos, radius)):
                break

        if obj.last_in_combo:
            direction = _f32(two_pi * next_single())
        else:
            direction = _f32(direction
                             + _f32(distance / _DISTANCE_CAP)
                             * _f32(next_single() * two_pi - _f32(math.pi)))


# =========================================================================
# Entry point  (OsuModTargetPractice.ApplyToBeatmap)
# =========================================================================

def apply_target_practice(hit_objects: list[HitObject], seed: int,
                          diff: Difficulty, timings, breaks,
                          version: int) -> list[HitObject]:
    """TP: replace the beatmap's objects with the generated target circles.

    Returns a NEW list of :class:`Circle` (the caller assigns it to
    ``beatmap.hit_objects``). An empty input map is returned unchanged (lazer
    ``if (osuBeatmap.HitObjects.Count == 0) return``).
    """
    if not hit_objects:
        return hit_objects

    rng = DotNetRandom(seed)                      # new Random(Seed.Value.Value)
    original = sorted(hit_objects, key=lambda o: o.start_time)  # OrderBy StartTime

    beats = _generate_beats(original, timings, breaks)

    radius = diff.circle_radius_l                 # == lazer OsuHitObject.Radius
    targets: list[Circle] = []
    for beat in beats:
        c = Circle()
        c.start_time = beat
        c.end_time = beat
        c.start_pos_raw = (PLAYFIELD_CENTRE[0], PLAYFIELD_CENTRE[1])
        c.end_pos_raw = c.start_pos_raw
        c.stack_leniency = original[0].stack_leniency
        # kiai from the effect/timing point active at the beat (obj.Kiai).
        c._tp_kiai = timings.get_point_at(beat).kiai   # type: ignore[attr-defined]
        targets.append(c)

    _add_hit_samples(targets, original)
    _fix_combo_info(targets, original)
    _randomize_circle_pos(targets, rng, radius)

    # HR (rare with TP): the targets are placed in the standard playfield frame
    # and lazer draws them there (they were NOT re-flipped by HR). This
    # renderer flips at DRAW time in HitObject.modify_position when HARD_ROCK is
    # set, so pre-flip Y here to cancel it and land back on the generated
    # position. Plain TP (the validated path) skips this entirely.
    if diff.mods & Mods.HARD_ROCK:
        for c in targets:
            x, y = c.start_pos_raw
            c.start_pos_raw = (x, BASE_Y - y)
            c.end_pos_raw = c.start_pos_raw

    # Render/DB surface: combo number/colour + timing/difficulty attributes.
    for idx, c in enumerate(targets):
        c.hit_object_id = idx
        c.combo_number = c._index_in_combo + 1      # type: ignore[attr-defined]
        c.combo_set = c._final_combo_index          # type: ignore[attr-defined]
        c.combo_set_hax = c._final_combo_index       # type: ignore[attr-defined]
        c.color_offset = 0
        c.set_timing(timings, version)
        c.set_difficulty(diff)

    return targets
