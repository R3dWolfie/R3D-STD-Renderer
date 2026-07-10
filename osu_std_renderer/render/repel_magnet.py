"""Cursor-driven object-movement mods — Magnetised (MG) + Repel (RP).

These two osu!std "fun" mods share ONE mechanism: every game frame each ALIVE
hit object's drawn position is EASED toward (Magnetised) or away from (Repel)
the current cursor position, with an exponential per-frame damp. Unlike the
transform-family / screen mods (which are stateless functions of time), this is
STATEFUL — the position at frame N depends on the position at frame N-1 and the
cursor path so far — an ODE integrated frame-by-frame, exactly matching lazer's
``IUpdatableByPlayfield.Update``. The scene owns the per-object state + the
frame loop; this module is the pure-math PORT of the per-frame step.

Both mods HIDE follow points ("they won't make any sense" once objects move)
and leave the judgement/reconcile on the REAL beatmap geometry — the object's
own drawable (head / number / approach ring / slider body+ball / hit flash)
rides the eased offset, while the 300/100/50 judgement popup stays at the
object's original position, matching lazer (its Update comment notes judgements
are NOT yet repositioned to the moved drawable).

Ported from ppy/osu (MIT) + ppy/osu-framework (MIT), cited per function:

  osu.Game.Rulesets.Osu/Mods/OsuModMagnetised.cs
      AttractionStrength BindableFloat(0.5){MinValue 0.05, MaxValue 1.0,
      Precision 0.05}. Update(): for a DrawableHitCircle easeTo(clock, circle,
      cursorPos); for a DrawableSlider easeTo(clock, slider, cursorPos) while
      !HeadCircle.Result.HasResult, else easeTo(clock, slider, cursorPos -
      slider.Ball.DrawPosition). easeTo(): dampLength = Interpolation.Lerp(3000,
      40, AttractionStrength); x = Interpolation.DampContinuously(hitObject.X,
      destination.X, dampLength, clock.ElapsedFrameTime); y likewise.
      ApplyToDrawableRuleset hides the playfield FollowPoints.

  osu.Game.Rulesets.Osu/Mods/OsuModRepel.cs
      RepulsionStrength BindableFloat(0.5){MinValue 0.05, MaxValue 1.0,
      Precision 0.05}. Update(): destination = Vector2.Clamp(2 * drawable.Position
      - cursorPos, Vector2.Zero, OsuPlayfield.BASE_SIZE); for a Slider destination
      is further Vector2.Clamp'd into CalculatePossibleMovementBounds(slider).
      Then easeTo(clock, circle/slider, destination, cursorPos) — or, once the
      head has a result, easeTo(..., destination - slider.Ball.DrawPosition, ...).
      easeTo(): dampLength = Vector2.Distance(hitObject.Position, cursorPos) /
      (0.04 * RepulsionStrength + 0.04); x/y via DampContinuously as above.
      ApplyToDrawableRuleset hides the playfield FollowPoints (same as MG).

  osu.Framework/Utils/Interpolation.cs
      Lerp(start, final, amount) => start + (final - start) * amount.
      Damp(start, final, base, exponent) => Lerp(start, final, 1 - base^exponent).
      DampContinuously(current, target, halfTime, elapsedTime) =>
      Damp(current, target, 0.5, elapsedTime / halfTime).

  osu.Game.Rulesets.Osu/Utils/OsuHitObjectGenerationUtils.Reposition.cs
      CalculatePossibleMovementBounds(slider): bounding box of the slider's
      CalculatedPath ± Radius, turned into the [left, right] × [top, bottom]
      range of head positions that keep the whole slider inside BASE_SIZE.
"""
from __future__ import annotations

import math

# acronyms (osu!(lazer) mod list)
MAGNETISED = "MG"
REPEL = "RP"
REPEL_MAGNET_MODS = frozenset({MAGNETISED, REPEL})

# BindableFloat(0.5){MinValue 0.05, MaxValue 1.0} — identical for both mods.
STRENGTH_DEFAULT = 0.5
STRENGTH_RANGE = (0.05, 1.0)

# OsuPlayfield.BASE_SIZE (osu!px playfield extent the destinations clamp to).
BASE_SIZE = (512.0, 384.0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# --- osu!framework Interpolation ---------------------------------------------
def lerp(start: float, final: float, amount: float) -> float:
    """Interpolation.Lerp — the plain affine blend."""
    return start + (final - start) * amount


def damp_continuously(current: float, target: float, half_time: float,
                      elapsed: float) -> float:
    """Interpolation.DampContinuously — a frame-rate-independent exponential
    approach: current + (target-current)·(1 - 0.5^(elapsed/half_time)).

    Being normalised by ``elapsed`` this converges to the SAME trajectory at
    any frame rate (the whole point over plain Damp), so our fixed render
    timestep tracks lazer's variable per-frame update closely. Edge cases match
    the C# limits: elapsed <= 0 → no move (0.5^0 = 1); half_time <= 0 →
    snap to target (0.5^inf = 0)."""
    if elapsed <= 0.0:
        return current
    if half_time <= 0.0:
        return target
    return lerp(current, target, 1.0 - math.pow(0.5, elapsed / half_time))


# --- damp lengths (the easeTo half-time per mod) -----------------------------
def magnet_damp_length(strength: float) -> float:
    """OsuModMagnetised.easeTo: dampLength = Lerp(3000, 40, AttractionStrength).
    Strong pull → short half-time (40 ms) → the object snaps to the cursor;
    weak pull → long half-time (up to 3000 ms) → it barely drifts."""
    return lerp(3000.0, 40.0, strength)


def repel_damp_length(px: float, py: float, cx: float, cy: float,
                      strength: float) -> float:
    """OsuModRepel.easeTo: dampLength = Distance(objectPos, cursorPos) /
    (0.04·RepulsionStrength + 0.04). The half-time GROWS with distance, so a
    fleeing object decelerates the further it gets from the cursor."""
    dist = math.hypot(px - cx, py - cy)
    return dist / (0.04 * strength + 0.04)


# --- destinations ------------------------------------------------------------
def magnet_destination(cx: float, cy: float,
                       ball_off_x: float = 0.0,
                       ball_off_y: float = 0.0) -> tuple[float, float]:
    """OsuModMagnetised destination: the cursor position (so the object's head
    is pulled onto the cursor). Once a slider head is judged the destination is
    ``cursorPos - Ball.DrawPosition`` so the BALL — not the head — sits on the
    cursor; ``ball_off_*`` is that ball-relative-to-head offset (0 for circles
    and un-judged heads)."""
    return (cx - ball_off_x, cy - ball_off_y)


def repel_destination(px: float, py: float, cx: float, cy: float,
                      ) -> tuple[float, float]:
    """OsuModRepel destination: Clamp(2·objectPos - cursorPos, 0, BASE_SIZE) —
    the object's position reflected THROUGH itself away from the cursor (the
    cursor's mirror image about the object), clamped to the playfield. Sliders
    clamp this further via :func:`clamp_to_slider_bounds`."""
    dx = _clamp(2.0 * px - cx, 0.0, BASE_SIZE[0])
    dy = _clamp(2.0 * py - cy, 0.0, BASE_SIZE[1])
    return (dx, dy)


def slider_movement_bounds(rel_path: list[tuple[float, float]],
                           radius: float) -> tuple[float, float, float, float]:
    """CalculatePossibleMovementBounds(slider) as (min_x, max_x, min_y, max_y):
    the range of HEAD positions that keep the whole slider inside BASE_SIZE.
    ``rel_path`` is the slider's path points RELATIVE to its head (head at the
    origin); ``radius`` is the circle radius (osu!px). Mirrors lazer: bbox of
    the path ± radius → left = -bboxMin, right = BASE - bboxMax."""
    if not rel_path:
        return (0.0, BASE_SIZE[0], 0.0, BASE_SIZE[1])
    xs = [p[0] for p in rel_path]
    ys = [p[1] for p in rel_path]
    min_x = min(xs) - radius
    max_x = max(xs) + radius
    min_y = min(ys) - radius
    max_y = max(ys) + radius
    return (-min_x, BASE_SIZE[0] - max_x, -min_y, BASE_SIZE[1] - max_y)


def clamp_to_slider_bounds(dx: float, dy: float,
                           bounds: tuple[float, float, float, float],
                           ) -> tuple[float, float]:
    """Clamp a destination head position into a slider's movement bounds. When
    a slider is larger than the playfield on an axis the bound inverts
    (lo > hi); lazer's Vector2.Clamp would then pin to ``lo``, so we clamp to
    ``lo`` in that degenerate case rather than raise."""
    lo_x, hi_x, lo_y, hi_y = bounds
    dx = _clamp(dx, lo_x, hi_x) if lo_x <= hi_x else lo_x
    dy = _clamp(dy, lo_y, hi_y) if lo_y <= hi_y else lo_y
    return (dx, dy)


def ease_step(px: float, py: float, dest_x: float, dest_y: float,
              damp_length: float, elapsed: float) -> tuple[float, float]:
    """One easeTo step: DampContinuously each axis of (px, py) toward
    (dest_x, dest_y) with the given half-time over ``elapsed`` ms. This is the
    per-frame integration primitive the scene calls for every alive object."""
    return (damp_continuously(px, dest_x, damp_length, elapsed),
            damp_continuously(py, dest_y, damp_length, elapsed))
