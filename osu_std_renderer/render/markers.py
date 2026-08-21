"""Reverse arrows, slider ticks and follow points — schedule + lifecycle
math (pure functions/dataclasses, no GL; render/scene.py draws them).

REVERSE ARROWS (§3.2/§3.3 reversearrow):
  * one arrow per remaining repeat: reverse #r (r = 1..RepeatCount-1) is
    consumed at startTime + r*partLen and sits at the end of span r-1 —
    the TAIL when r is odd (forward span arriving), the HEAD when r is
    even (backward span arriving). This matches slider.py's TickReverse
    markers 1:1 (end_progress = 1.0 on even spans, 0.0 on odd).
  * orientation: the arrow points along the path tangent at that end,
    INTO the slider (a right-pointing texture rotated by atan2 of the
    inward direction — screen space, so HR flips come for free).
  * visibility: repeats #1 (tail) and #2 (head) appear with the slider's
    fade-in from spawn; every later arrow r>2 appears when arrow r-2 (same
    end) is consumed, with the quick ARROW_FADE_MS ramp — stable's "one
    arrow per end, replaced as consumed" (lazer DrawableSliderRepeat fades
    span-0 repeats in with the snaking body during the preempt and later
    ones one SpanDuration ahead). Hidden does NOT alter reverse-arrow
    lifetime: a pending arrow is visible BEFORE its repeat regardless of
    whether the head has been hit. Consumption: repeat HIT → the standard
    hit-explosion pop (alpha 1→0, scale 1→1.4 over HitFadeOut); repeat
    MISSED (sliderbreak) → vanishes instantly.
  * pulse (§3.2 version-gated): skin Version < 2 → rotation wobble ±6°
    over each beat; v2+ → scale pulse 1.3→1.0 eased out on each beat.
    Beat phase comes from the map's red timing lines (beat_phase()).
  * NOT combo-tinted — white sprite over the circle (osu semantics).

SLIDER TICKS (sliderscorepoint):
  * drawn at each TickPoint of the ACTIVE span only. Span-0 ticks fade
    in with the slider (from spawn over TimeFadeIn); span s>0 ticks
    appear at their span start with a quick TICK_FADE_MS ramp (they sit
    at the same path positions as the previous span's, so no early
    overlap). Consumed tick: HIT → tiny pop (scale 1→1.5, gone over
    TICK_POP_MS); MISSED (per ruleset) → vanishes, no pop. Untinted.
  * during snake-in a tick only shows once the body has grown past its
    path progress (tick_schedule computes progress per tick).

FOLLOW POINTS (followpoint, lazer FollowPointConnection.cs semantics):
  * between consecutive objects in the SAME combo (next not new-combo)
    and never from/to spinners.
  * dots every SPACING=32 osu!px from d=int(SPACING*1.5) while
    d < distance - SPACING, along prev END position → next START
    position (stacked).
  * per-dot schedule (lazer): fadeOutTime = prevEnd + fraction*duration,
    fadeInTime = fadeOutTime - PREEMPT(800). From fadeInTime the dot
    fades 0→1 over the next object's TimeFadeIn while easing (quad-out)
    from (fraction-0.1) to fraction along the line, scale 1.5→1; at
    fadeOutTime it fades back out over TimeFadeIn. All dots are gone by
    (or fading right at) next.startTime.
  * the sprite is rotated along the line (directional skin followpoints).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..beatmap.objects import Slider, Spinner

# reverse arrows
ARROW_FADE_MS = 150.0          # quick fade for arrows appearing mid-slide
ARROW_EXPLODE_MS = 240.0       # consumed-arrow pop = HitFadeOut
ARROW_EXPLODE_SCALE = 1.4
ARROW_PULSE_AMOUNT = 0.3       # v2+ beat pulse: scale 1.3 → 1.0
ARROW_PULSE_ROT_RAD = math.radians(6.0)   # v1 pulse: ±6°

# slider ticks
TICK_FADE_MS = 60.0            # span > 0: quick fade at span start
TICK_POP_MS = 120.0
TICK_POP_SCALE = 1.5

# follow points (lazer FollowPointConnection)
FP_SPACING = 32.0
FP_PREEMPT = 800.0
FP_PREEMPT_MIN = 450.0        # lazer OsuHitObject.PREEMPT_MIN


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# --- reverse arrows ---------------------------------------------------------------

@dataclass(frozen=True)
class ReverseArrow:
    r: int             # 1-based repeat index
    time: float        # consumed at startTime + r*partLen
    appear: float      # becomes visible (spawn for r<=2, else time of r-2)
    at_tail: bool      # True → path end (progress 1), False → path head


def reverse_arrow_schedule(start: float, part_len: float, repeat_count: int,
                           spawn: float,
                           ) -> list[ReverseArrow]:
    """One ReverseArrow per remaining repeat (r = 1..repeat_count-1).

    Hidden does NOT alter reverse-arrow lifetime: a pending reverse arrow
    is visible BEFORE its repeat (lazer DrawableSliderRepeat), whether or
    not the slider head has been hit. Repeats #1 (tail) and #2 (head) ride
    the slider body fade-in from spawn; every later arrow r>2 appears when
    the same-end arrow r-2 is consumed (start + (r-2)*part_len), so exactly
    one arrow per end is lit as the pending indicator, replaced as it is
    consumed."""
    if repeat_count < 2 or part_len <= 0:
        return []
    out: list[ReverseArrow] = []
    for r in range(1, repeat_count):
        # reverses #1 (tail) and #2 (head) are visible from spawn, riding
        # the slider body fade-in; every LATER arrow (r>2) appears exactly
        # when the previous same-end arrow (r-2) is consumed, so one arrow
        # per end acts as the pending indicator, replaced as it is consumed
        # (lazer DrawableSliderRepeat). Hidden does NOT gate this lifetime.
        natural = spawn if r <= 2 else start + (r - 2) * part_len
        appear = max(spawn, natural)
        out.append(ReverseArrow(r=r, time=start + r * part_len,
                                appear=appear, at_tail=(r % 2 == 1)))
    return out


def arrow_direction(path: list[tuple[float, float]],
                    at_tail: bool) -> tuple[float, float] | None:
    """Unit-less inward direction at a path end: from the end point toward
    its (first distinct) neighbour. None when the path is degenerate."""
    if len(path) < 2:
        return None
    if at_tail:
        ex, ey = path[-1]
        rest = range(len(path) - 2, -1, -1)
    else:
        ex, ey = path[0]
        rest = range(1, len(path))
    for i in rest:
        dx, dy = path[i][0] - ex, path[i][1] - ey
        if dx * dx + dy * dy > 1e-12:
            return (dx, dy)
    return None


def arrow_rotation(path: list[tuple[float, float]], at_tail: bool) -> float:
    """Sprite rotation (rad) turning a RIGHT-pointing arrow texture along
    the inward tangent. Works in screen space (y-down) — the gl.py sprite
    rotation convention matches atan2(dy, dx) directly."""
    d = arrow_direction(path, at_tail)
    if d is None:
        return 0.0
    return math.atan2(d[1], d[0])


def beat_phase(t: float, timings) -> float:
    """0..1 phase inside the current beat, from the map's red lines."""
    p = timings.get_original_point_at(t)
    bl = p.beat_length_base
    if not (bl > 0) or math.isnan(bl):
        return 0.0
    return ((t - p.time) % bl) / bl


def arrow_pulse(phase: float, legacy: bool) -> tuple[float, float]:
    """(scale, rotation_offset_rad) of the beat pulse at `phase`.
    Skin v<2 (legacy): rotation wobble ±6°, no scale pulse.
    v2+: scale 1.3 at the beat easing out to 1.0, no rotation."""
    if legacy:
        return 1.0, ARROW_PULSE_ROT_RAD * math.sin(2.0 * math.pi * phase)
    return 1.0 + ARROW_PULSE_AMOUNT * (1.0 - phase) ** 2, 0.0


def arrow_alpha_scale(t: float, arrow: ReverseArrow, spawn: float,
                      time_fade_in: float,
                      hit: bool = True) -> tuple[float, float] | None:
    """(alpha, scale) of one reverse arrow, or None outside its life.
    Arrows appearing at spawn ride the slider fade-in; later ones use the
    quick ARROW_FADE_MS ramp. Consumed: hit → explosion pop, miss → gone."""
    if t < arrow.appear:
        return None
    if t <= arrow.time:
        fade = time_fade_in if arrow.appear <= spawn else ARROW_FADE_MS
        a = _clamp01((t - arrow.appear) / fade) if fade > 0 else 1.0
        return a, 1.0
    if not hit:
        return None                      # sliderbreak at this repeat: vanish
    p = (t - arrow.time) / ARROW_EXPLODE_MS
    if p >= 1.0:
        return None
    return 1.0 - p, 1.0 + (ARROW_EXPLODE_SCALE - 1.0) * p


# --- slider ticks -----------------------------------------------------------------

@dataclass(frozen=True)
class TickMark:
    time: float                 # scoring/pass time
    span: int
    pos: tuple[float, float]    # RAW osu!px (caller applies modify_position)
    progress: float             # 0..1 along the PATH (snake-in gating)


def tick_schedule(slider: Slider) -> list[TickMark]:
    """TickMarks for every parsed TickPoint (path progress recovered from
    the span parity — slider.py stores only times/positions)."""
    out: list[TickMark] = []
    if slider.part_len <= 0:
        return out
    for tp in slider.tick_points:
        span_start = slider.start_time + tp.span * slider.part_len
        time_prog = (tp.time - span_start) / slider.part_len
        prog = 1.0 - time_prog if tp.span % 2 else time_prog
        out.append(TickMark(time=tp.time, span=tp.span, pos=tp.pos,
                            progress=prog))
    return out


def tick_alpha_scale(t: float, tick_time: float, span: int, span_start: float,
                     spawn: float, time_fade_in: float,
                     hit: bool = True) -> tuple[float, float] | None:
    """(alpha, scale) of one tick, or None outside its life. Span 0 fades
    in with the slider from spawn; later spans ramp at their span start.
    Consumed: hit → TICK_POP pop, miss → vanish."""
    appear = spawn if span == 0 else span_start
    fade = time_fade_in if span == 0 else TICK_FADE_MS
    if t < appear:
        return None
    if t <= tick_time:
        a = _clamp01((t - appear) / fade) if fade > 0 else 1.0
        return a, 1.0
    if not hit:
        return None
    p = (t - tick_time) / TICK_POP_MS
    if p >= 1.0:
        return None
    return 1.0 - p, 1.0 + (TICK_POP_SCALE - 1.0) * p


# --- follow points ----------------------------------------------------------------

@dataclass(frozen=True)
class FollowPointDot:
    fade_in: float
    fade_out: float
    x_start: float
    y_start: float
    x_end: float
    y_end: float
    rotation: float


def followpoint_eligible(prev, nxt) -> bool:
    """Follow points connect consecutive objects in the SAME combo; never
    from or to a spinner."""
    if isinstance(prev, Spinner) or isinstance(nxt, Spinner):
        return False
    return not nxt.new_combo


def followpoint_dots(p1: tuple[float, float], t_end: float,
                     p2: tuple[float, float],
                     t_start: float,
                     prev_preempt: float) -> list[FollowPointDot]:
    """The lazer FollowPointConnection dot schedule between prev END
    (p1 @ t_end) and next START (p2 @ t_start), osu!px.

    prev_preempt is the ORIGIN object's TimePreempt — our un-speed-scaled
    ``Difficulty.preempt_u`` (the AR→preempt in map ms). Lazer scales the
    connection preempt exactly like FollowPointConnection.GetFadeTimes:

        preempt = PREEMPT * min(1, start.TimePreempt / PREEMPT_MIN)

    so on extended-AR objects (TimePreempt < 450 → AR > 10) the follow
    points appear later and linger less. For AR <= 10 (preempt_u >= 450) the
    factor is 1 → flat 800, byte-identical to the old behaviour."""
    duration = t_start - t_end
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    distance = math.hypot(dx, dy)
    if duration <= 0 or distance <= 0:
        return []
    preempt = FP_PREEMPT * min(1.0, prev_preempt / FP_PREEMPT_MIN)
    rotation = math.atan2(dy, dx)
    out: list[FollowPointDot] = []
    d = float(int(FP_SPACING * 1.5))
    while d < distance - FP_SPACING:
        fraction = d / distance
        fade_out = t_end + fraction * duration
        out.append(FollowPointDot(
            fade_in=fade_out - preempt, fade_out=fade_out,
            x_start=p1[0] + (fraction - 0.1) * dx,
            y_start=p1[1] + (fraction - 0.1) * dy,
            x_end=p1[0] + fraction * dx,
            y_end=p1[1] + fraction * dy,
            rotation=rotation))
        d += FP_SPACING
    return out


def followpoint_state(t: float, dot: FollowPointDot, time_fade_in: float) \
        -> tuple[float, float, float, float] | None:
    """(alpha, x, y, scale) of one dot at time t, or None outside its
    life. Lazer transforms: fade 0→1 over TimeFadeIn while quad-out
    easing from the (fraction-0.1) position to the dot position and
    scale 1.5→1; fade back out over TimeFadeIn from fade_out."""
    if time_fade_in <= 0:
        time_fade_in = 1.0
    if t < dot.fade_in:
        return None
    w = _clamp01((t - dot.fade_in) / time_fade_in)
    ease = 1.0 - (1.0 - w) ** 2
    alpha = w
    if t > dot.fade_out:
        alpha = min(alpha, 1.0 - (t - dot.fade_out) / time_fade_in)
        if alpha <= 0.0:
            return None
    x = dot.x_start + (dot.x_end - dot.x_start) * ease
    y = dot.y_start + (dot.y_end - dot.y_start) * ease
    return alpha, x, y, 1.5 - 0.5 * ease
