"""Transform-family "fun" mods — per-object ENTRANCE animations (GR/DF/SI/WG/TR).

These five lazer mods all hook the SAME injection point: a per-object visual
transform driven by the object's spawn/preempt window. They are purely VISUAL
(``ModWithVisibilityAdjustment`` — the drawable's appear animation only); the
beatmap geometry, the replay cursor and the judgement/reconcile are UNTOUCHED,
so the object stays hittable at its real (un-transformed) position. This module
is the pure-math core (start state + easing + duration relative to
``TimePreempt``/``TimeFadeIn``); the scene wires each config to its acronym.

Ported from ppy/osu (MIT), cited per mod:

  osu.Game.Rulesets.Osu/Mods/OsuModObjectScaleTween.cs  (base of GR + DF)
      ``ApplyNormalVisibilityState`` → for a DrawableSlider / DrawableHitCircle,
      ``BeginAbsoluteSequence(StartTime - TimePreempt)`` then
      ``ScaleTo(StartScale).Then().ScaleTo(EndScale, TimePreempt, Easing.OutSine)``
      (EndScale == 1). DrawableSliderHead / DrawableSliderTail are NOT scaled
      on their own (they scale as children of the parent DrawableSlider). The
      base implements ``IHidesApproachCircles`` and additionally hides each hit
      circle's approach circle.
  osu.Game.Rulesets.Osu/Mods/OsuModGrow.cs
      StartScale ``new BindableFloat(0.5f){MinValue=0, MaxValue=0.99, Precision
      =0.01}`` — objects START SMALL (0.5×) and grow to full size by the hit.
  osu.Game.Rulesets.Osu/Mods/OsuModDeflate.cs
      StartScale ``new BindableFloat(2){MinValue=1, MaxValue=25, Precision=0.1}``
      — objects START LARGE (2×) and shrink to normal by the hit.

  osu.Game.Rulesets.Osu/Mods/OsuModSpinIn.cs  (IHidesApproachCircles)
      const rotate_offset = 360; const rotate_starting_width = 2. A
      DrawableHitCircle: ``RotateTo(360).Then().RotateTo(0, TimePreempt,
      Easing.InOutSine)`` + ``ScaleTo(new Vector2(2, 0)).Then().ScaleTo(1,
      TimePreempt, Easing.InOutSine)`` + ``FadeIn()`` (bypasses the fade-in →
      the circle is OPAQUE from spawn; that is why SI is incompatible with HD).
      A DrawableSlider instead ``ScaleTo(0).Then().ScaleTo(1, TimePreempt,
      Easing.InOutSine)`` + ``FadeIn()`` (uniform grow-from-zero, NO rotation).

  osu.Game.Rulesets.Osu/Mods/OsuModWiggle.cs
      const wiggle_duration = 100 (ms). Strength ``new BindableDouble(1)
      {MinValue=0.1, MaxValue=2, Precision=0.1}``. Per object a
      ``new Random((int)StartTime)`` (.NET System.Random — our DotNetRandom)
      drives a chain of ``MoveTo(origin + dist·(cos θ, sin θ), 100)`` keyframes,
      θ = NextDouble()·2π, dist = NextDouble()·Strength·7, one every 100 ms
      across TimePreempt (and, for sliders/spinners, across their Duration too).
      SliderRepeat / SliderTailCircle are skipped (they wiggle with the slider).
      NOT an IHidesApproachCircles mod (the approach circle wiggles along).

  osu.Game.Rulesets.Osu/Mods/OsuModTransform.cs
      A per-mod ``theta`` accumulator (starts 0, ``theta += TimeFadeIn/1000``
      after each processed object). Per object ``appearDistance = (TimePreempt
      - TimeFadeIn)/2``; ``appearOffset = (cos θ, sin θ)·appearDistance``;
      ``BeginAbsoluteSequence(StartTime - TimePreempt - 1)`` then
      ``MoveToOffset(appearOffset).MoveTo(originalPosition, TimePreempt + 1,
      Easing.InOutSine)`` — the object flies in from a rotating offset toward
      its real spot. Slider head/tail/tick/repeat are skipped (they fly with
      the parent). NOT an IHidesApproachCircles mod.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

# acronyms
GROW = "GR"
DEFLATE = "DF"
SPIN_IN = "SI"
WIGGLE = "WG"
TRANSFORM = "TR"

TRANSFORM_MODS = frozenset({GROW, DEFLATE, SPIN_IN, WIGGLE, TRANSFORM})
# GR/DF/SI implement IHidesApproachCircles — no approach ring is drawn.
HIDES_APPROACH = frozenset({GROW, DEFLATE, SPIN_IN})
# GR/DF/SI drive scale (+ SI rotation) about the object's origin; WG/TR drive a
# position OFFSET only (scale/rotation identity). No mod does both, which lets
# the scene apply the two families through one affine hook.
SCALE_MODS = frozenset({GROW, DEFLATE, SPIN_IN})
OFFSET_MODS = frozenset({WIGGLE, TRANSFORM})

SI_ROTATE_OFFSET_DEG = 360.0        # OsuModSpinIn.rotate_offset
SI_START_WIDTH = 2.0                # OsuModSpinIn.rotate_starting_width
WIGGLE_DURATION_MS = 100.0          # OsuModWiggle.wiggle_duration
WIGGLE_DIST_MULT = 7.0              # OsuModWiggle: dist = NextDouble()*Strength*7


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# --- osu!framework easings ----------------------------------------------------
def ease_out_sine(p: float) -> float:
    """Easing.OutSine."""
    return math.sin(_clamp01(p) * (math.pi / 2.0))


def ease_in_out_sine(p: float) -> float:
    """Easing.InOutSine."""
    return 0.5 * (1.0 - math.cos(math.pi * _clamp01(p)))


@dataclass(frozen=True)
class ObjTransform:
    """The per-object visual modifier at one time.

    ``scale_x``/``scale_y`` multiply the drawn size (about the object origin);
    ``rotation`` (radians) is ADDED to each sprite's rotation; ``off_x``/
    ``off_y`` are an osu!px position offset added to the object's real
    position; ``force_opaque`` (SI) overrides the fade-in so the object is fully
    opaque from spawn. ``IDENTITY`` is the no-op (a non-transform frame)."""
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    off_x: float = 0.0
    off_y: float = 0.0
    force_opaque: bool = False


IDENTITY = ObjTransform()


# --- GR / DF: OsuModObjectScaleTween ------------------------------------------
def grow_deflate_scale(t: float, spawn: float, preempt: float,
                       start_scale: float) -> float:
    """Uniform scale multiplier at time ``t`` for Grow/Deflate. ScaleTo(
    start_scale) at ``spawn`` (== StartTime - TimePreempt), then ScaleTo(1,
    TimePreempt, OutSine): reaches 1.0 exactly at the hit time (spawn +
    preempt) and holds 1.0 after."""
    if preempt <= 0.0 or t <= spawn:
        return start_scale
    p = (t - spawn) / preempt
    if p >= 1.0:
        return 1.0
    return start_scale + (1.0 - start_scale) * ease_out_sine(p)


# --- SI: OsuModSpinIn ---------------------------------------------------------
def spin_in_circle(t: float, spawn: float, preempt: float) -> tuple[float, float, float]:
    """(scale_x, scale_y, rotation_rad) for a hit circle under Spin In.
    Scale (2, 0) → (1, 1) and rotation 360° → 0°, both InOutSine over
    TimePreempt. At spawn the circle is a thin 2×-wide line rotated a full
    turn; at the hit it is upright and full size."""
    full_rot = math.radians(SI_ROTATE_OFFSET_DEG)
    if preempt <= 0.0 or t <= spawn:
        return SI_START_WIDTH, 0.0, full_rot
    p = (t - spawn) / preempt
    if p >= 1.0:
        return 1.0, 1.0, 0.0
    e = ease_in_out_sine(p)
    sx = SI_START_WIDTH + (1.0 - SI_START_WIDTH) * e
    sy = e                                    # 0 → 1
    rot = full_rot * (1.0 - e)                # 360° → 0°
    return sx, sy, rot


def spin_in_slider_scale(t: float, spawn: float, preempt: float) -> float:
    """Uniform scale for a SLIDER under Spin In: ScaleTo(0) → ScaleTo(1,
    TimePreempt, InOutSine). No rotation (sliders don't spin, only grow)."""
    if preempt <= 0.0 or t <= spawn:
        return 0.0
    p = (t - spawn) / preempt
    if p >= 1.0:
        return 1.0
    return ease_in_out_sine(p)               # 0 → 1


# --- TR: OsuModTransform ------------------------------------------------------
def transform_appear_offset(theta: float, preempt: float,
                            time_fade_in: float) -> tuple[float, float]:
    """The initial appear offset (osu!px) for an object whose accumulated
    ``theta`` is given: (cos θ, sin θ) · appearDistance,
    appearDistance = (TimePreempt - TimeFadeIn) / 2."""
    appear_distance = (preempt - time_fade_in) / 2.0
    return (math.cos(theta) * appear_distance,
            math.sin(theta) * appear_distance)


def transform_offset_at(t: float, appear_time: float, move_duration: float,
                        off_x0: float, off_y0: float) -> tuple[float, float]:
    """The Transform fly-in offset (osu!px) at time ``t``: appearOffset ·
    (1 - InOutSine(progress)) over [appear_time, appear_time + move_duration].
    appear_time = StartTime - TimePreempt - 1; move_duration = TimePreempt + 1
    (so the offset reaches 0 exactly at StartTime)."""
    if move_duration <= 0.0 or t <= appear_time:
        return off_x0, off_y0
    p = (t - appear_time) / move_duration
    if p >= 1.0:
        return 0.0, 0.0
    f = 1.0 - ease_in_out_sine(p)
    return off_x0 * f, off_y0 * f


# --- WG: OsuModWiggle ---------------------------------------------------------
def wiggle_events(start_time: float, preempt: float, duration: float,
                  strength: float) -> list[tuple[float, float, float]]:
    """The chain of ``MoveTo`` keyframes (move_start_ms, target_off_x,
    target_off_y) for one object, drawing the .NET System.Random stream in the
    game's order. ``duration`` is the object's IHasDuration length (0 for a
    hit circle → preempt wiggles only). Offsets are osu!px relative to the real
    position (the MoveTo origin)."""
    from ..beatmap.dotnet_random import DotNetRandom

    rng = DotNetRandom(int(start_time))         # new Random((int)StartTime)
    spawn = start_time - preempt
    events: list[tuple[float, float, float]] = []

    def draw() -> tuple[float, float]:
        angle = rng.next_double() * 2.0 * math.pi
        dist = rng.next_double() * strength * WIGGLE_DIST_MULT
        return dist * math.cos(angle), dist * math.sin(angle)

    # preempt wiggles: (int)TimePreempt / wiggle_duration  (integer division)
    n_pre = int(preempt) // int(WIGGLE_DURATION_MS)
    for i in range(n_pre):
        ox, oy = draw()
        events.append((spawn + i * WIGGLE_DURATION_MS, ox, oy))

    # duration wiggles (sliders/spinners): (int)(Duration / wiggle_duration)
    n_dur = int(duration / WIGGLE_DURATION_MS)
    for i in range(n_dur):
        ox, oy = draw()
        events.append((start_time + i * WIGGLE_DURATION_MS, ox, oy))
    return events


def wiggle_offset_at(events: list[tuple[float, float, float]],
                     t: float) -> tuple[float, float]:
    """Piecewise-linear offset (osu!px) at time ``t`` from the keyframe chain.
    Each MoveTo animates (Easing.None) from the previous target to its own over
    100 ms; the first animates from the origin (0, 0). Holds the last target
    after the chain ends (a circle rests at a small non-zero wiggle offset —
    the animation does NOT return to origin)."""
    if not events:
        return 0.0, 0.0
    starts = [e[0] for e in events]
    idx = bisect.bisect_right(starts, t) - 1
    if idx < 0:
        return 0.0, 0.0
    tgt_t, gx, gy = events[idx]
    if idx == 0:
        sx = sy = 0.0                            # first MoveTo starts at origin
    else:
        sx, sy = events[idx - 1][1], events[idx - 1][2]
    p = _clamp01((t - tgt_t) / WIGGLE_DURATION_MS)
    return sx + (gx - sx) * p, sy + (gy - sy) * p
