"""Screen / cursor-effect visual mods — BR/BM/SY/BL/NS/DP/BU.

The pure-math core for a batch of osu!std mods that alter the SCREEN, the
CURSOR or a per-object DRAW transform. Unlike the transform family these do NOT
share one injection point, so each has its own helpers here; the scene
(render/scene.py) wires each config to its acronym and draws it.

All are purely VISUAL: none mutates the beatmap geometry, the replay cursor or
the judgement — the renderer simulates the untouched beatmap + untouched replay
frames, so the SimResult (and every reconciled count) is byte-identical with or
without the mod. BR rotates the DRAW only (both objects AND the drawn cursor
about the same screen centre); judgement runs on the real, un-rotated osu!px
positions, exactly as lazer judges in un-transformed playfield space.

Ported from ppy/osu (MIT), cited per mod:

  osu.Game.Rulesets.Osu/Mods/OsuModBarrelRoll.cs + osu.Game/Rulesets/Mods/
  ModBarrelRoll.cs (BR): Update sets playfield rotation (deg) = CurrentRotation
      = (Direction==CCW ? -1 : 1) * 360 * (time/60000 * SpinSpeed);
      ApplyToDrawableRuleset scales the playfield by minSide/maxSide.
  osu.Game.Rulesets.Osu/Mods/OsuModBloom.cs (BM): currentSize = clamp(
      MaxCursorSize * combo/MaxSizeComboCount, MIN_SIZE=1, MaxCursorSize),
      cursor ModScaleAdjust lerps toward it over 100ms (=1 during breaks).
  osu.Game/.../ModSynesthesia.cs + OsuModSynesthesia.cs (SY): AccentColour =
      BindableBeatDivisor.GetColourFor(ControlPointInfo.GetClosestBeatDivisor(t)).
  osu.Game.Rulesets.Osu/Mods/OsuModBlinds.cs (BL): DrawableOsuBlinds — two black
      boxes whose covered width = (end-start)*0.5*curve(clamp(closedness)*
      breakMult), curve(v)=0.6v²+0.4v, closedness = health, leniency=0.1.
  osu.Game/.../ModNoScope.cs + OsuModNoScope.cs (NS): ComboBasedAlpha = max(
      MIN_ALPHA=0.0002, 1 - combo/HiddenComboCount) fades the cursor (=1 during
      breaks + spinners), lerp over 100ms.
  osu.Game.Rulesets.Osu/Mods/OsuModDepth.cs (DP): objects approach in 3D —
      z(t) from MaxDepth to 0, scale = -cameraZ/max(1, z-cameraZ), position =
      (pos-camera.xy)*scale + camera.xy, camera = (256,192,-200).
  osu.Game.Rulesets.Osu/Mods/OsuModBubbles.cs (BU): a bubble spawns at each
      hit, scale 1→MaxSize over 0.8·duration then →MaxSize·1.5 over 0.2·duration
      (fading out), MaxSize = min(1.75, 1.25 + 0.005*combo).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# =========================================================================
# BR — Barrel Roll (ModBarrelRoll)
# =========================================================================
def barrel_rotation_deg(t: float, spin_speed: float, direction: int) -> float:
    """ModBarrelRoll.Update: CurrentRotation (degrees) =
    dir * 360 * (time/60000 * SpinSpeed). ``direction`` is +1 clockwise /
    -1 counterclockwise; ``spin_speed`` is rev/min. Grows linearly with time
    (a positive value is a clockwise screen rotation, matching osu!framework's
    y-down rotation convention)."""
    return direction * 360.0 * (t / 60000.0 * spin_speed)


def barrel_playfield_scale(width: float, height: float) -> float:
    """ModBarrelRoll.ApplyToDrawableRuleset: the playfield is scaled by
    minSide/maxSide of its draw area so the rotated playfield's corners stay
    on-screen. Applied about the screen centre together with the rotation."""
    lo, hi = min(width, height), max(width, height)
    return lo / hi if hi > 0.0 else 1.0


# =========================================================================
# Combo-driven value timeline (shared by BM cursor scale + NS cursor alpha)
# =========================================================================
def build_combo_timeline(events, value_fn) -> list[tuple[float, float, float]]:
    """From the judgment stream build a value-change schedule
    [(change_time_ms, from_value, to_value)] where ``value_fn(combo)`` maps the
    running combo to the mod value. A change point is emitted only when the
    mapped value actually changes (like build_flashlight_timeline). Combo starts
    at 0, so the initial value is ``value_fn(0)``."""
    changes: list[tuple[float, float, float]] = []
    cur = value_fn(0)
    for ev in sorted(events, key=lambda e: e.time_ms):
        combo = getattr(ev, "combo_after", None)
        if combo is None:
            continue
        v = value_fn(combo)
        if v != cur:
            changes.append((ev.time_ms, cur, v))
            cur = v
    return changes


def combo_value_at(timeline: list[tuple[float, float, float]], t: float,
                   transition_ms: float, initial: float) -> float:
    """The combo-driven value at time ``t`` — each change point lerps linearly
    between the two values over ``transition_ms`` (lazer's per-frame
    Interpolation.Lerp toward the target over TRANSITION_DURATION, modelled as a
    100 ms linear settle). ``initial`` is the value before the first change."""
    val = initial
    for ct, frm, to in timeline:
        if t < ct:
            break
        if transition_ms > 0.0 and t < ct + transition_ms:
            return frm + (to - frm) * (t - ct) / transition_ms
        val = to
    return val


# =========================================================================
# BM — Bloom (OsuModBloom)
# =========================================================================
BLOOM_MIN_SIZE = 1.0                # OsuModBloom.MIN_SIZE
BLOOM_TRANSITION_MS = 100.0         # OsuModBloom.TRANSITION_DURATION


def bloom_cursor_size(combo: int, max_size_combo_count: int,
                      max_cursor_size: float) -> float:
    """OsuModBloom currentSize = clamp(MaxCursorSize * combo/MaxSizeComboCount,
    MIN_SIZE=1, MaxCursorSize). The cursor-scale multiplier at ``combo``: 1 at
    combo 0, rising linearly to MaxCursorSize once combo reaches
    MaxSizeComboCount, then capped there."""
    if max_size_combo_count <= 0:
        return max_cursor_size
    return _clamp(max_cursor_size * (float(combo) / max_size_combo_count),
                  BLOOM_MIN_SIZE, max_cursor_size)


# =========================================================================
# SY — Synesthesia (ModSynesthesia / OsuModSynesthesia)
# =========================================================================
# BindableBeatDivisor.GetColourFor: the beat-snap divisor -> OsuColour palette
# (hex from osu.Game/Graphics/OsuColour.cs). 5/7/9 all share GreenLight; the
# default (an unsnapped/odd divisor) is pure Color4.Red.
def _hex(h: str) -> tuple[float, float, float]:
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
            int(h[4:6], 16) / 255.0)


SNAP_COLOURS: dict[int, tuple[float, float, float]] = {
    1: (1.0, 1.0, 1.0),      # White
    2: _hex("ed1121"),       # colours.Red
    4: _hex("66ccff"),       # colours.Blue
    8: _hex("ffcc22"),       # colours.Yellow
    16: _hex("6644cc"),      # colours.PurpleDark
    3: _hex("8866ee"),       # colours.Purple
    6: _hex("eeaa00"),       # colours.YellowDark
    12: _hex("cc6600"),      # colours.YellowDarker
    5: _hex("b3d944"),       # colours.GreenLight
    7: _hex("b3d944"),
    9: _hex("b3d944"),
}
SNAP_COLOUR_DEFAULT = (1.0, 0.0, 0.0)   # Color4.Red

# BindableBeatDivisor.PREDEFINED_DIVISORS
PREDEFINED_DIVISORS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 16)


def snap_colour(divisor: int) -> tuple[float, float, float]:
    """BindableBeatDivisor.GetColourFor(divisor) as an RGB triple in [0,1]."""
    return SNAP_COLOURS.get(divisor, SNAP_COLOUR_DEFAULT)


def _round_away_from_zero(x: float) -> int:
    """.NET Math.Round(x, MidpointRounding.AwayFromZero)."""
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def closest_beat_divisor(time: float, tp_time: float, beat_length: float,
                         eps: float = 1e-3) -> int:
    """ControlPointInfo.GetClosestBeatDivisor(time): the PREDEFINED divisor
    whose snap grid (from the active timing point ``tp_time`` with red-line
    ``beat_length`` ms/beat) lands closest to ``time``. Ties keep the SMALLEST
    divisor (Precision.DefinitelyBigger's epsilon)."""
    if beat_length <= 0.0:
        return 1
    tt = time
    if tt < 0.0:                                  # move into the positives
        offset_beats = math.ceil(-tt / beat_length)
        tt += offset_beats * beat_length
    best_div = 0
    best_dist = math.inf
    for divisor in PREDEFINED_DIVISORS:
        snap_len = beat_length / divisor
        beats = _round_away_from_zero((max(tt, 0.0) - tp_time) / snap_len)
        snapped = tp_time + beats * snap_len
        dist = abs(tt - snapped)
        if best_dist > dist + eps:                # DefinitelyBigger
            best_div = divisor
            best_dist = dist
    return best_div


# =========================================================================
# BL — Blinds (OsuModBlinds.DrawableOsuBlinds)
# =========================================================================
BLINDS_LENIENCY = 0.1               # DrawableOsuBlinds.leniency
BLINDS_TARGET_CLAMP = 1.0           # DrawableOsuBlinds.target_clamp
BLINDS_BREAK_OPEN_EARLY = 500.0     # ms before a break the blinds open
BLINDS_BREAK_CLOSE_LATE = 250.0     # ms after a break (and after start) to close


def blinds_adjustment_curve(v: float) -> float:
    """DrawableOsuBlinds.applyAdjustmentCurve: 0.6v² + 0.4v (a Lagrange curve
    through (0,0), (0.6,0.4), (1,1))."""
    return 0.6 * v * v + 0.4 * v


def blinds_gap(closedness: float, break_multiplier: float) -> float:
    """calculateGap: clamp(closedness, 0, target_clamp) * targetBreakMultiplier."""
    return _clamp(closedness, 0.0, BLINDS_TARGET_CLAMP) * break_multiplier


def blinds_inner_edges(start: float, end: float, closedness: float,
                       break_multiplier: float) -> tuple[float, float]:
    """The inner (screen-x) edges of the two black boxes, given the playfield
    span [start, end] in screen px, the ``closedness`` (health, 0 open / 1
    closed) and the break multiplier. Returns (left_edge, right_edge): the left
    box covers [0, left_edge], the right box covers [right_edge, screen_w].
    Matches DrawableOsuBlinds.Update (leniency expansion + width)."""
    raw_width = end - start
    start -= raw_width * BLINDS_LENIENCY * 0.5
    end += raw_width * BLINDS_LENIENCY * 0.5
    width = (end - start) * 0.5 * blinds_adjustment_curve(
        blinds_gap(closedness, break_multiplier))
    return start + width, end - width


# --- osu!framework easings used by the break-multiplier timeline --------------
def ease_out_sine(p: float) -> float:
    return math.sin(_clamp01(p) * (math.pi / 2.0))


def ease_out_quint(p: float) -> float:
    return 1.0 - (1.0 - _clamp01(p)) ** 5


def ease_out_circ(p: float) -> float:
    p = _clamp01(p) - 1.0
    return math.sqrt(1.0 - p * p)


def ease_out_bounce(p: float) -> float:
    """osu!framework Easing.OutBounce."""
    p = _clamp01(p)
    if p < 1.0 / 2.75:
        return 7.5625 * p * p
    if p < 2.0 / 2.75:
        p -= 1.5 / 2.75
        return 7.5625 * p * p + 0.75
    if p < 2.5 / 2.75:
        p -= 2.25 / 2.75
        return 7.5625 * p * p + 0.9375
    p -= 2.625 / 2.75
    return 7.5625 * p * p + 0.984375


@dataclass(frozen=True)
class _BreakSeg:
    start: float
    duration: float
    frm: float
    to: float
    ease: str


def build_blinds_break_timeline(first_start: float, first_preempt: float,
                                breaks) -> list["_BreakSeg"]:
    """DrawableOsuBlinds.LoadComplete: the targetBreakMultiplier schedule.
    Starts at 0, ramps 0->1 (leaveBreak, 2500 ms OutBounce) at
    firstObj.StartTime - firstObj.TimePreempt + break_close_late; each effective
    break ramps 1->0 (enterBreak, 1000 ms OutSine) at breakStart - 500 then
    0->1 (leaveBreak) after break.Duration + 500 + 250. ``breaks`` is a list of
    (start_ms, end_ms) with an effect."""
    segs: list[_BreakSeg] = []
    start_delay = first_start - first_preempt
    segs.append(_BreakSeg(start_delay + BLINDS_BREAK_CLOSE_LATE, 2500.0,
                          0.0, 1.0, "outbounce"))
    for bstart, bend in breaks:
        segs.append(_BreakSeg(bstart - BLINDS_BREAK_OPEN_EARLY, 1000.0,
                              1.0, 0.0, "outsine"))
        dur = bend - bstart
        leave = (bstart - BLINDS_BREAK_OPEN_EARLY
                 + dur + BLINDS_BREAK_OPEN_EARLY + BLINDS_BREAK_CLOSE_LATE)
        segs.append(_BreakSeg(leave, 2500.0, 0.0, 1.0, "outbounce"))
    segs.sort(key=lambda s: s.start)
    return segs


_BLINDS_EASE = {"outbounce": ease_out_bounce, "outsine": ease_out_sine}


def blinds_break_multiplier_at(timeline: list["_BreakSeg"], t: float) -> float:
    """targetBreakMultiplier at ``t`` from the schedule. A later TransformTo
    (enterBreak) supersedes a still-running earlier one, so the GOVERNING
    segment is the last one whose start has passed; within its window it eases
    from->to, past it the target holds."""
    gov = None
    for seg in timeline:
        if seg.start <= t:
            gov = seg
        else:
            break
    if gov is None:
        return 0.0
    if t < gov.start + gov.duration and gov.duration > 0.0:
        e = _BLINDS_EASE[gov.ease]((t - gov.start) / gov.duration)
        return gov.frm + (gov.to - gov.frm) * e
    return gov.to


# =========================================================================
# NS — No Scope (ModNoScope / OsuModNoScope)
# =========================================================================
NO_SCOPE_MIN_ALPHA = 0.0002         # ModNoScope.MIN_ALPHA
NO_SCOPE_TRANSITION_MS = 100.0      # ModNoScope.TRANSITION_DURATION


def no_scope_alpha(combo: int, hidden_combo_count: int) -> float:
    """ModNoScope.ComboBasedAlpha = max(MIN_ALPHA, 1 - combo/HiddenComboCount).
    ``hidden_combo_count`` == 0 means the cursor is ALWAYS hidden (lazer's early
    return leaves ComboBasedAlpha at 0)."""
    if hidden_combo_count <= 0:
        return NO_SCOPE_MIN_ALPHA
    return max(NO_SCOPE_MIN_ALPHA, 1.0 - float(combo) / hidden_combo_count)


# =========================================================================
# DP — Depth (OsuModDepth)
# =========================================================================
DEPTH_CAMERA_Z = -200.0             # camera_position.Z
DEPTH_CENTER = (256.0, 192.0)       # OsuPlayfield.BASE_SIZE * 0.5


def depth_scale_for(z: float) -> float:
    """OsuModDepth.scaleForDepth: -cameraZ / max(1, z - cameraZ)."""
    return -DEPTH_CAMERA_Z / max(1.0, z - DEPTH_CAMERA_Z)


def depth_for_scale(scale: float) -> float:
    """OsuModDepth.depthForScale: -cameraZ/scale + cameraZ."""
    return -DEPTH_CAMERA_Z / scale + DEPTH_CAMERA_Z


def depth_position(px: float, py: float, scale: float) -> tuple[float, float]:
    """OsuModDepth.toPlayfieldPosition: (pos - camera.xy)*scale + camera.xy."""
    cx, cy = DEPTH_CENTER
    return (px - cx) * scale + cx, (py - cy) * scale + cy


def depth_z_circle(t: float, start_time: float, preempt: float,
                   max_depth: float) -> float:
    """OsuModDepth.processHitObject z at ``t``: MaxDepth at appear
    (start-preempt), decreasing at constant speed MaxDepth/preempt to 0 at the
    hit (and continuing negative after)."""
    speed = max_depth / preempt
    appear = start_time - preempt
    return max_depth - (max(t, appear) - appear) * speed


def depth_z_slider(t: float, start_time: float, duration: float,
                   preempt: float, max_depth: float) -> float:
    """OsuModDepth.processSlider z at ``t``. Long sliders (whose end scale would
    exceed 1.5) decelerate near the hit so the whole body clears the camera;
    short ones fall through to the constant-speed circle model."""
    slider_min_depth = depth_for_scale(1.5)
    base_speed = max_depth / preempt
    appear = start_time - preempt
    z_end = max_depth - (max(start_time + duration, appear) - appear) * base_speed
    if z_end > slider_min_depth:
        return depth_z_circle(t, start_time, preempt, max_depth)

    offset_after_start = duration + 500.0
    slow_speed = min(-slider_min_depth / offset_after_start, base_speed)
    decel_time = preempt * 0.2
    decel_dist = decel_time * (base_speed + slow_speed) * 0.5

    if t < start_time - decel_time:
        full_distance = decel_dist + base_speed * (preempt - decel_time)
        return full_distance - (max(t, appear) - appear) * base_speed
    if t < start_time:
        time_offset = t - (start_time - decel_time)
        deceleration = (slow_speed - base_speed) / decel_time
        return decel_dist - (base_speed * time_offset
                             + deceleration * time_offset * time_offset * 0.5)
    end_time = start_time + offset_after_start
    return -(min(t, end_time) - start_time) * slow_speed


# =========================================================================
# BU — Bubbles (OsuModBubbles)
# =========================================================================
BUBBLE_SIZE_MULT = 1.90             # firstObject.Radius * 1.90 (InitialSize)
BUBBLE_MAX_CAP = 1.75               # min(1.75, ...)
BUBBLE_POP_SCALE = 1.5              # MaxSize * 1.5 pop at end of life
BUBBLE_GROW_FRACTION = 0.8          # ScaleTo(MaxSize, duration*0.8)


def bubble_max_size(combo: int) -> float:
    """OsuModBubbles maxSize = min(1.75, 1.25 + 0.005*combo)."""
    return min(BUBBLE_MAX_CAP, 1.25 + 0.005 * combo)


def bubble_initial_size_osu(circle_radius: float) -> float:
    """The bubble's InitialSize (osu!px): firstObject.Radius * 1.90."""
    return circle_radius * BUBBLE_SIZE_MULT


def bubble_fade_time(preempt: float) -> float:
    """bubbleFade = firstObject.TimePreempt * 2."""
    return preempt * 2.0


def bubble_duration(fade_time: float) -> float:
    """A bubble's total animation length: 1700 + FadeTime^1.07."""
    return 1700.0 + math.pow(fade_time, 1.07)


def bubble_scale_at(age: float, duration: float, max_size: float) -> float:
    """The bubble's Scale at ``age`` ms since spawn. ScaleTo(MaxSize) linearly
    over duration*0.8 (from Scale 1), then ScaleTo(MaxSize*1.5, duration*0.2,
    OutQuint) as it pops. Beyond ``duration`` it has Expired."""
    grow = duration * BUBBLE_GROW_FRACTION
    if age <= 0.0:
        return 1.0
    if age < grow:
        return 1.0 + (max_size - 1.0) * (age / grow)
    pop = duration - grow
    if pop <= 0.0 or age >= duration:
        return max_size * BUBBLE_POP_SCALE
    e = ease_out_quint((age - grow) / pop)
    return max_size + (max_size * BUBBLE_POP_SCALE - max_size) * e


def bubble_alpha_at(age: float, duration: float) -> float:
    """The bubble's alpha at ``age``: 1 while growing, then FadeOut over
    duration*0.2 with Easing.OutCirc during the pop."""
    grow = duration * BUBBLE_GROW_FRACTION
    if age < grow:
        return 1.0
    pop = duration - grow
    if pop <= 0.0 or age >= duration:
        return 0.0
    return 1.0 - ease_out_circ((age - grow) / pop)
