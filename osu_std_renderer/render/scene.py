"""Object lifecycle + per-frame scene draw — the Phase-1 "first moving
render" pass.

RENDER_PLAN.md semantics implemented here:
  §2.5 circle fades   — fade-in over TimeFadeIn from startTime-Preempt,
                        full until the hit, then the hit-explosion: alpha
                        1→0 and scale 1→1.4 over HitFadeOut=240 ms (skin
                        v2+ shape). Combo number does the v2+ quick 60 ms
                        fade instead of scaling with the explosion.
  §2.5 approach       — ring scales 4→1 over [startTime-Preempt,
                        startTime], combo-coloured, alpha capped at 0.9,
                        gone at the hit.
  §5.3 draw order     — objects iterate REVERSED (newest under oldest);
                        a slider's body under its own end circle / ball /
                        head; approach circles on a top layer; cursor on
                        top of everything.
  §4.9 Snaking.In     — body grows over the first third of the preempt
                        (lazer SnakingSliderBody timing); the tail circle
                        rides the snake tip.

JUDGMENT PHASE (ruleset/ruleset.py drives this; pass judgments=SimResult):
  * explosions fire at the REAL hit time (click delta), not startTime;
    a missed circle/slider-head never explodes — it quick-fades out at
    its window close (stable's brief red-tint variant is SKIPPED — plain
    fade; noted per the plan).
  * judgment popups: procedural 300/100/50 numbers + red miss X at the
    object's popup position, ResultFadeIn=120 / ResultFadeOut=600 (§2.4);
    300 popups draw fainter so a clean run doesn't strobe.
  * sliderbreaks read as the ball detaching: the follow ball dims while
    the ruleset says tracking was lost (and there is no tail explosion —
    the phase-1 body fade already has none).
  * without a replay/judgments (--dump-frames debug, future --no-replay)
    every object falls back to the Phase-1 "perfect hit at startTime".

BACKGROUND + DIM PHASE (§4.10, render/background.py): the map background
draws FIRST (under everything), aspect-filled to the frame, tinted grey
by 1 - DimEnvelope.level(t) so only the background dims. No bg → the
dark-void clear, unchanged.

SKIN PHASE (render/skin_elements.py): when a SkinElements is attached,
each core element the skin provides replaces its procedural stand-in —
hitcircle (combo-tinted) + hitcircleoverlay (untinted, above the circle,
under the number unless HitCircleOverlayAboveNumber), approachcircle
(tinted), HitCirclePrefix combo digits (HitCircleOverlap layout), sliderb
(tint per AllowSliderBallTint/SliderBall), sliderfollowcircle (2.4× while
tracking), skin.ini SliderBorder/SliderTrackOverride body colours, skin
cursor/trail/middle (behind --skin-cursor, CursorCentre honored), hit0/
50/100/300 judgment sprites (a fully-transparent skin texture = draw
NOTHING — the classic empty hit300). Elements the skin lacks fall back
per-element to the procedural set (the §3.1 LOCAL source).

ARROWS + TICKS + FOLLOW POINTS PHASE (render/markers.py holds the pure
schedule/lifecycle math — see its docstring for the semantics):
  * reverse arrows on the active repeat end, rotated along the path
    tangent INTO the slider, white (never combo-tinted), beat-pulsed
    (§3.2 version gate: v<2 ±6° wobble, v2+ 1.3→1.0 scale), exploding
    on a hit repeat / vanishing on a sliderbreak.
  * slider ticks for the ACTIVE span only, snake-gated, popping when
    hit / vanishing when missed (per the ruleset's part outcomes).
  * follow points between same-combo neighbours (never spinners) on the
    lazer schedule; DrawFollowPoints honored (draw_follow_points).
  * slider heads/tails use sliderstartcircle/sliderendcircle(+overlays)
    when the skin ships them (§3.3 GetMostSpecific vs hitcircle).

CURSOR TRAIL (§3.3 two-mode rule, skin cursor only): skin HAS
cursormiddle (or ForceLongTrail) → the LONG CONNECTED trail — cursortrail
sprites laid along the cursor path every LONG_TRAIL_SPACING_OSU osu!px
(distance-resampled, LongTrailDensity semantics, capped at
LONG_TRAIL_MAX_POINTS≈stable's 2048), aged out over LONG_TRAIL_WINDOW_MS.
No cursormiddle → the classic sparse trail: one sprite dropped every
16.67 ms, each fading over TRAIL_SPRITE_LIFE_MS. The procedural (non-skin)
cursor keeps its shader-style glow trail.

REMAINING SIMPLIFICATIONS (each is a later-phase item):
  * spinners: no visuals (judgment popup only); counted and logged.
  * no hit lighting yet.
  * slider bodies are rebuilt every frame (0.2 ms/body — fine at this
    phase; an FBO cache per static body is the known perf step).
  * skin gaps: see skin_elements.py's honest list (spinner/HUD/scorebar
    stay procedural or absent; animations take frame 0 except
    sliderb/followpoint which cycle per AnimationFramerate; no cursor
    rotate/expand).

The lifecycle math lives in module-level pure functions so tests need no
GL context.
"""
from __future__ import annotations

import math
import sys

from ..beatmap.difficulty import (HIT_FADE_OUT, RESULT_FADE_IN,
                                  RESULT_FADE_OUT)
from ..beatmap.objects import Slider, Spinner
from ..replay.replay import cursor_at
from ..ruleset import JudgmentKind
from .gl import Sprite
from .markers import (arrow_alpha_scale, arrow_pulse, arrow_rotation,
                      beat_phase, followpoint_dots, followpoint_eligible,
                      followpoint_state, reverse_arrow_schedule,
                      tick_alpha_scale, tick_schedule)
from .skin_elements import (FOLLOW_CIRCLE_SCALE, CURSOR_UI_HEIGHT,
                            circle_pixel_scale, layout_skin_digits)
from .slider_body import DEFAULT_COMBO_COLORS, BodyStyle, sub_path

EXPLODE_SCALE = 1.4            # §2.5 hit-explosion end scale (skin v2+)
NUMBER_FADE_OUT = 60.0         # §3.2 v2+ combo-number quick fade (ms)
MISS_FADE_OUT = 60.0           # missed circle: quick fade at window close
APPROACH_START_SCALE = 4.0     # §2.5 approach circle 4→1
APPROACH_MAX_ALPHA = 0.9       # stable caps the approach ring alpha
SNAKE_IN_PORTION = 1.0 / 3.0   # lazer: snake-in completes after preempt/3
TRAIL_WINDOW_MS = 150.0
TRAIL_STEPS = 12
# skin-cursor trail (§3.3 two-mode rule; see module docstring)
TRAIL_SPRITE_INTERVAL_MS = 1000.0 / 60.0   # sparse mode: 16.67 ms drops
TRAIL_SPRITE_LIFE_MS = 150.0               # sparse sprite fade-out
LONG_TRAIL_SPACING_OSU = 2.0               # long mode: point every 2 osu!px
LONG_TRAIL_MAX_POINTS = 2048               # stable LongTrailLength scale
LONG_TRAIL_WINDOW_MS = 800.0               # long-mode age-out horizon
LONG_TRAIL_STEP_MS = 2.0                   # cursor path sampling step
TICK_LOGICAL_PX = 16.0         # procedural slider-tick dot, logical skin px
FP_DOT_LOGICAL_PX = 16.0       # procedural followpoint dot, logical skin px
CURSOR_RADIUS_OSU = 14.0       # cursor core sizing base, osu!px
CURSOR_GLOW_COLOR = (1.0, 0.30, 0.35)   # R3D red
DIGIT_SPACING = 0.06           # of digit height, between combo digits
BALL_DETACHED_ALPHA = 0.35     # slider ball while tracking is lost
POPUP_HEIGHT_FRAC = 0.62       # judgment number height / circle radius
POPUP_COLORS = {
    JudgmentKind.HIT300: (0.38, 0.72, 1.00),   # Argon-ish blue
    JudgmentKind.HIT100: (0.42, 0.88, 0.47),   # green
    JudgmentKind.HIT50: (0.95, 0.76, 0.36),    # orange
    JudgmentKind.MISS: (0.95, 0.25, 0.30),     # red X
}
POPUP_300_ALPHA = 0.45         # 300s draw fainter (anti-strobe; procedural only)
POPUP_SKIN_ELEMENT = {         # judgment kind → skin sprite element
    JudgmentKind.MISS: "hit0",
    JudgmentKind.HIT50: "hit50",
    JudgmentKind.HIT100: "hit100",
    JudgmentKind.HIT300: "hit300",
}


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# --- lifecycle math (pure; unit-tested) ---------------------------------------

def fade_in_alpha(t: float, start_time: float, preempt: float,
                  time_fade_in: float) -> float:
    """§2.5: 0→1 over TimeFadeIn starting at startTime - Preempt."""
    t0 = start_time - preempt
    if t < t0:
        return 0.0
    if time_fade_in <= 0:
        return 1.0
    return _clamp01((t - t0) / time_fade_in)


def circle_alpha_scale(t: float, start_time: float, preempt: float,
                       time_fade_in: float,
                       hit_time: float | None = None) -> tuple[float, float]:
    """(alpha, scale) for a hit-circle disc+ring. Phase 1 assumes the hit
    lands exactly at startTime (pass hit_time when the ruleset exists)."""
    hit = start_time if hit_time is None else hit_time
    if t < start_time - preempt:
        return 0.0, 1.0
    if t <= hit:
        return fade_in_alpha(t, start_time, preempt, time_fade_in), 1.0
    p = (t - hit) / HIT_FADE_OUT
    if p >= 1.0:
        return 0.0, EXPLODE_SCALE
    return 1.0 - p, 1.0 + (EXPLODE_SCALE - 1.0) * p


def approach_scale_alpha(t: float, start_time: float, preempt: float,
                         time_fade_in: float) -> tuple[float, float] | None:
    """(scale, alpha) for the approach ring, or None outside its life
    ([startTime - Preempt, startTime); it vanishes at the hit)."""
    if t >= start_time or t < start_time - preempt:
        return None
    w = _clamp01((t - (start_time - preempt)) / preempt)
    scale = APPROACH_START_SCALE - (APPROACH_START_SCALE - 1.0) * w
    alpha = min(fade_in_alpha(t, start_time, preempt, time_fade_in),
                APPROACH_MAX_ALPHA)
    return scale, alpha


def number_alpha(t: float, start_time: float, preempt: float,
                 time_fade_in: float, hit_time: float | None = None) -> float:
    """Combo number: rides the fade-in, then the v2+ quick 60 ms fade."""
    hit = start_time if hit_time is None else hit_time
    if t <= hit:
        return fade_in_alpha(t, start_time, preempt, time_fade_in)
    return _clamp01(1.0 - (t - hit) / NUMBER_FADE_OUT)


def miss_fade_alpha(t: float, start_time: float, preempt: float,
                    time_fade_in: float, deadline: float) -> float:
    """Missed circle/slider-head: normal fade-in, full until the miss
    window closes, then a quick fade out (NO explosion, no scale). Stable's
    brief red tint is skipped (plain fade) — noted in the module docstring."""
    if t <= deadline:
        return fade_in_alpha(t, start_time, preempt, time_fade_in)
    return _clamp01(1.0 - (t - deadline) / MISS_FADE_OUT)


def popup_alpha_scale(t: float, popup_time: float) -> tuple[float, float] | None:
    """(alpha, scale) of a judgment popup, or None outside its life.
    §2.4 ResultFadeIn=120 / ResultFadeOut=600; a small 0.85→1 pop on the
    way in."""
    age = t - popup_time
    if age < 0:
        return None
    if age <= RESULT_FADE_IN:
        w = age / RESULT_FADE_IN
        return w, 0.85 + 0.15 * w
    p = (age - RESULT_FADE_IN) / RESULT_FADE_OUT
    if p >= 1.0:
        return None
    return 1.0 - p, 1.0


def snake_end_fraction(t: float, start_time: float, preempt: float,
                       snaking_in: bool = True) -> float:
    """§4.9 Snaking.In (lazer timing): 0→1 over the first preempt/3."""
    if not snaking_in:
        return 1.0
    return _clamp01((t - (start_time - preempt))
                    / (preempt * SNAKE_IN_PORTION))


def body_alpha(t: float, start_time: float, end_time: float, preempt: float,
               time_fade_in: float) -> float:
    """Slider body/end-circle fade: in with the head, solid through the
    slide, out over HitFadeOut after endTime."""
    if t <= start_time:
        return fade_in_alpha(t, start_time, preempt, time_fade_in)
    if t <= end_time:
        return 1.0
    return _clamp01(1.0 - (t - end_time) / HIT_FADE_OUT)


def visible_window(obj, preempt: float) -> tuple[float, float]:
    """[spawn, retire] for drawing: startTime-Preempt .. endTime+HitFadeOut."""
    return (obj.get_start_time() - preempt,
            obj.get_end_time() + HIT_FADE_OUT)


def layout_digits(number: int, aspects: dict[str, float], height: float,
                  spacing: float = DIGIT_SPACING) -> list[tuple[str, float, float]]:
    """[(char, dx_of_center, width)] for the digits of `number`, centred
    on dx=0 at the given digit height."""
    s = str(max(0, int(number)))
    widths = [aspects.get(ch, 0.6) * height for ch in s]
    gap = spacing * height
    total = sum(widths) + gap * (len(s) - 1)
    out: list[tuple[str, float, float]] = []
    x = -total / 2.0
    for ch, w in zip(s, widths):
        out.append((ch, x + w / 2.0, w))
        x += w + gap
    return out


def trail_times(t: float, window_ms: float = TRAIL_WINDOW_MS,
                steps: int = TRAIL_STEPS) -> list[tuple[float, float]]:
    """[(sample_time, strength 0..1)] oldest→newest for the PROCEDURAL
    cursor's glow trail (the non-skin cursor keeps this look)."""
    step = window_ms / steps
    return [(t - i * step, 1.0 - i / (steps + 1.0))
            for i in range(steps, 0, -1)]


def skin_trail_is_long(has_cursormiddle: bool, force_long: bool) -> bool:
    """§3.3: long connected trail when `cursormiddle` exists or
    ForceLongTrail; else the sparse 16.67 ms sprite trail."""
    return has_cursormiddle or force_long


def sparse_trail_times(t: float,
                       interval_ms: float = TRAIL_SPRITE_INTERVAL_MS,
                       life_ms: float = TRAIL_SPRITE_LIFE_MS,
                       ) -> list[tuple[float, float]]:
    """[(drop_time, strength 0..1)] oldest→newest for the sparse skin
    trail: sprites drop on a fixed 16.67 ms grid (quantized to absolute
    time so drops don't slide between frames) and fade linearly over
    life_ms."""
    newest = math.floor(t / interval_ms) * interval_ms
    out: list[tuple[float, float]] = []
    k = 0
    while True:
        ti = newest - k * interval_ms
        strength = 1.0 - (t - ti) / life_ms
        if strength <= 0.0:
            break
        out.append((ti, strength))
        k += 1
    out.reverse()
    return out


def long_trail_points(sample_fn, t: float, *,
                      spacing_osu: float = LONG_TRAIL_SPACING_OSU,
                      window_ms: float = LONG_TRAIL_WINDOW_MS,
                      step_ms: float = LONG_TRAIL_STEP_MS,
                      max_points: int = LONG_TRAIL_MAX_POINTS,
                      ) -> list[tuple[float, float, float]]:
    """[(x_osu, y_osu, strength 0..1)] oldest→newest for the LONG
    connected trail: the cursor path over the last window_ms resampled by
    DISTANCE — one point every spacing_osu osu!px (LongTrailDensity
    semantics), at most max_points (stable LongTrailLength scale) —
    each aged linearly to 0 at the window edge. sample_fn(time)→(x, y)."""
    out: list[tuple[float, float, float]] = []
    px, py = sample_fn(t)
    out.append((px, py, 1.0))
    acc = 0.0
    ti = t
    while len(out) < max_points:
        ti -= step_ms
        age = t - ti
        if age >= window_ms:
            break
        x, y = sample_fn(ti)
        acc += math.hypot(x - px, y - py)
        px, py = x, y
        if acc >= spacing_osu:
            out.append((x, y, 1.0 - age / window_ms))
            acc = 0.0
    out.reverse()
    return out


# --- the scene -----------------------------------------------------------------

class StdScene:
    """Draws one frame of gameplay at time t into a SpriteRenderer's FBO.

    Frame times must be non-decreasing (the record loop guarantees it);
    spawn/retire advance a pointer over the time-sorted object list.
    """

    def __init__(self, beatmap, frames, camera, sprites, bodies, bank, *,
                 combo_colors=DEFAULT_COMBO_COLORS,
                 snaking_in: bool = True,
                 draw_approach_circles: bool = True,
                 draw_combo_numbers: bool = True,
                 draw_follow_points: bool = True,
                 draw_cursor: bool = True,
                 cursor_scale: float = 1.0,
                 force_long_trail: bool = False,
                 background: tuple[float, float, float] = (0.043, 0.043, 0.055),
                 judgments=None,
                 draw_judgment_popups: bool = True,
                 hud=None,
                 skin_elems=None,
                 use_skin_cursor: bool = False,
                 border_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 track_override: tuple[float, float, float] | None = None,
                 bg_key: str | None = None,
                 bg_draw_size: tuple[float, float] | None = None,
                 dim_envelope=None):
        self.beatmap = beatmap
        self.diff = beatmap.diff
        self.frames = frames
        self.cam = camera
        self.spr = sprites
        self.bodies = bodies
        self.bank = bank
        self.combo_colors = [tuple(c) for c in combo_colors]
        self.snaking_in = snaking_in
        self.draw_approach_circles = draw_approach_circles
        self.draw_combo_numbers = draw_combo_numbers
        self.draw_follow_points = draw_follow_points
        self.draw_cursor = draw_cursor
        self.cursor_scale = cursor_scale
        self.background = background

        self.judgments = judgments        # ruleset SimResult | None
        self.draw_judgment_popups = draw_judgment_popups
        self.hud = hud                    # hud.StdHud | None (§5.3: topmost)

        self.skin = skin_elems            # skin_elements.SkinElements | None
        self.use_skin_cursor = bool(use_skin_cursor and skin_elems is not None
                                    and skin_elems.has("cursor"))
        # §3.3 trail mode: cursormiddle present (or forced) → long trail
        self.long_trail = skin_trail_is_long(
            skin_elems is not None and skin_elems.has("cursormiddle"),
            force_long_trail)
        self.border_color = tuple(border_color)
        self.track_override = (tuple(track_override)
                               if track_override is not None else None)
        self.bg_key = bg_key              # §4.10 background layer
        self.bg_draw_size = bg_draw_size  # cover-fit (w, h), frame-centered
        self.dim = dim_envelope           # background.DimEnvelope | None

        self.objects = sorted(beatmap.hit_objects,
                              key=lambda o: o.get_start_time())
        self.radius_px = camera.len_to_screen(self.diff.circle_radius)
        self.circle_k = circle_pixel_scale(self.radius_px)
        self._spawn_idx = 0
        self._active: list = []
        self._last_t = -math.inf
        self._slider_paths: dict[int, list[tuple[float, float]]] = {}
        self._slider_marks: dict[int, tuple[list, list]] = {}
        self.skipped_spinners = 0
        self._spinner_ids: set[int] = set()
        # judgment popups: time-sorted events, pointer + active window
        self._popups = (sorted(judgments.events, key=lambda e: e.time_ms)
                        if judgments is not None else [])
        self._popup_idx = 0
        self._popup_active: list = []
        # follow points: precomputed dot schedule, pointer + active window
        self._fp_dots: list = []
        if self.draw_follow_points:
            for prev, nxt in zip(self.objects, self.objects[1:]):
                if not followpoint_eligible(prev, nxt):
                    continue
                self._fp_dots.extend(followpoint_dots(
                    prev.get_stacked_end_position(self.diff),
                    prev.get_end_time(),
                    nxt.get_stacked_start_position(self.diff),
                    nxt.get_start_time()))
            self._fp_dots.sort(key=lambda d: d.fade_in)
        self._fp_idx = 0
        self._fp_active: list = []

    # --- lifecycle management ---------------------------------------------------

    def _advance(self, t: float) -> None:
        if t < self._last_t:
            raise ValueError(f"scene time went backwards: {t} < {self._last_t}")
        self._last_t = t
        preempt = self.diff.preempt
        while (self._spawn_idx < len(self.objects)
               and self.objects[self._spawn_idx].get_start_time() - preempt <= t):
            obj = self.objects[self._spawn_idx]
            self._spawn_idx += 1
            self._active.append(obj)
            if isinstance(obj, Slider):
                # presented path: HR flip + stack offset per vertex, → screen px
                self._slider_paths[id(obj)] = [
                    self.cam.to_screen(*obj.modify_position(p, self.diff))
                    for p in obj.multi_curve.path]
                self._slider_marks[id(obj)] = self._build_marks(obj)
        keep = []
        for obj in self._active:
            if t <= obj.get_end_time() + HIT_FADE_OUT:
                keep.append(obj)
            else:
                self._slider_paths.pop(id(obj), None)
                self._slider_marks.pop(id(obj), None)
        self._active = keep

    def _build_marks(self, obj) -> tuple[list, list]:
        """(ticks, arrows) draw records for one spawning slider — screen
        positions + per-part HIT flags from the ruleset (no judgments →
        the Phase-1 perfect-play fallback: everything hit).

        ticks:  (TickMark, screen_x, screen_y, span_start, hit)
        arrows: (ReverseArrow, screen_x, screen_y, rotation, hit)
        """
        v = self._verdict(obj)
        outcomes: dict[tuple[str, float], bool] = {}
        if v is not None:
            for p in v.parts:
                if p.kind in ("tick", "repeat"):
                    outcomes[(p.kind, round(p.time, 2))] = p.hit
        pts = self._slider_paths[id(obj)]
        ticks = []
        for tm in tick_schedule(obj):
            sx, sy = self.cam.to_screen(*obj.modify_position(tm.pos, self.diff))
            ticks.append((tm, sx, sy,
                          obj.start_time + tm.span * obj.part_len,
                          outcomes.get(("tick", round(tm.time, 2)), True)))
        arrows = []
        spawn = obj.get_start_time() - self.diff.preempt
        schedule = reverse_arrow_schedule(obj.get_start_time(), obj.part_len,
                                          obj.repeat_count, spawn)
        markers = obj.tick_reverse
        for arrow in schedule:
            marker = markers[arrow.r - 1] if arrow.r - 1 < len(markers) else None
            if marker is not None:
                sx, sy = self.cam.to_screen(
                    *obj.modify_position(marker.pos, self.diff))
                hit = outcomes.get(("repeat", round(marker.time, 2)), True)
            else:   # tick walk suppressed (degenerate) — fall back to path end
                sx, sy = pts[-1] if arrow.at_tail else pts[0]
                hit = True
            arrows.append((arrow, sx, sy,
                           arrow_rotation(pts, arrow.at_tail), hit))
        return ticks, arrows

    # --- frame draw ---------------------------------------------------------------

    def render_frame(self, t: float) -> None:
        self._advance(t)
        self.spr.begin(clear=self.background)
        if self.bg_key is not None and self.bg_draw_size is not None:
            # §4.10/§5.3: background first, dimmed by tinting the sprite
            b = 1.0 - (self.dim.level(t) if self.dim is not None else 0.0)
            bw, bh = self.bg_draw_size
            self.spr.draw([Sprite(self.cam.screen_w / 2.0,
                                  self.cam.screen_h / 2.0,
                                  bw, bh, self.bg_key, (b, b, b, 1.0))])
        if self.draw_follow_points and self._fp_dots:
            fps = self._followpoint_sprites(t)
            if fps:
                self.spr.draw(fps)    # §5.3: follow points under all objects
        approach: list[Sprite] = []
        # §5.3: newest objects draw FIRST → end up under older ones
        for obj in reversed(self._active):
            if isinstance(obj, Spinner):
                if id(obj) not in self._spinner_ids:
                    self._spinner_ids.add(id(obj))
                    self.skipped_spinners += 1
                continue
            if isinstance(obj, Slider):
                self._draw_slider(obj, t, approach)
            else:
                self._draw_circle(obj, t, approach)
        if approach:
            self.spr.draw(approach)
        if self.draw_judgment_popups and self._popups:
            popups = self._popup_sprites(t)
            if popups:
                self.spr.draw(popups)
        if self.draw_cursor and self.frames:
            self.spr.draw(self._cursor_sprites(t))
        if self.hud is not None:
            self.hud.draw(t)          # §5.3 draw order: … → cursors → HUD

    def frame_rgb(self, t: float):
        self.render_frame(t)
        return self.spr.read_rgb()

    # --- per-object draws -----------------------------------------------------------

    def _color(self, obj) -> tuple[float, float, float]:
        return self.combo_colors[obj.combo_set % len(self.combo_colors)]

    def _plain_circle_sprites(self, x: float, y: float, color, alpha: float,
                              scale: float = 1.0,
                              role: str = "hit") -> list[Sprite]:
        """Hit-circle visual without a number: skin circle (tinted) +
        overlay (untinted, osu semantics) when the skin provides them,
        else the procedural disc + ring. `role` picks the §3.3
        specialisation — slider heads/tails prefer sliderstartcircle/
        sliderendcircle(+overlays) via GetMostSpecific. A skin-blanked
        element (fully transparent) draws nothing."""
        sk = self.skin
        if sk is not None:
            circle_el, overlay_el = sk.circle_elements(role)
            if circle_el is not None:
                k = self.circle_k
                out: list[Sprite] = []
                if circle_el not in sk.empty:
                    w, h = sk.size[circle_el]
                    out.append(Sprite(x, y, w * k * scale, h * k * scale,
                                      f"sk_{circle_el}", (*color, alpha)))
                if overlay_el is not None and overlay_el not in sk.empty:
                    ow, oh = sk.size[overlay_el]
                    out.append(Sprite(x, y, ow * k * scale, oh * k * scale,
                                      f"sk_{overlay_el}",
                                      (1.0, 1.0, 1.0, alpha)))
                return out
        d = 2.0 * self.radius_px * scale
        return [
            Sprite(x, y, d, d, "disc", (*color, alpha)),
            Sprite(x, y, d, d, "ring", (1.0, 1.0, 1.0, alpha)),
        ]

    def _number_sprites(self, x: float, y: float, number: int,
                        num_alpha: float) -> list[Sprite]:
        """Combo number: skin HitCirclePrefix digits (HitCircleOverlap
        layout, native size at circle scale) or the procedural digits."""
        sk = self.skin
        out: list[Sprite] = []
        if sk is not None and sk.has("digits"):
            k = self.circle_k
            for ch, dx, w, h in layout_skin_digits(number, sk.digit_sizes,
                                                   sk.info.hit_circle_overlap):
                out.append(Sprite(x + dx * k, y, w * k, h * k,
                                  f"sk_digit_{ch}",
                                  (1.0, 1.0, 1.0, num_alpha)))
            return out
        h = self.radius_px  # digit height = half the circle diameter
        for ch, dx, w in layout_digits(number, self.bank.digit_aspect, h):
            out.append(Sprite(x + dx, y, w, h, f"digit_{ch}",
                              (1.0, 1.0, 1.0, num_alpha)))
        return out

    def _circle_sprites(self, x: float, y: float, color, alpha: float,
                        scale: float, number: int | None,
                        num_alpha: float, role: str = "hit") -> list[Sprite]:
        plain = self._plain_circle_sprites(x, y, color, alpha, scale, role)
        nums: list[Sprite] = []
        if (number is not None and self.draw_combo_numbers
                and num_alpha > 0.0):
            nums = self._number_sprites(x, y, number, num_alpha)
        sk = self.skin
        if (nums and sk is not None
                and sk.info.hit_circle_overlay_above_number):
            circle_el, overlay_el = sk.circle_elements(role)
            if (circle_el is not None and overlay_el is not None
                    and overlay_el not in sk.empty and plain):
                # skin.ini flag: circle → number → overlay (the overlay is
                # always the last plain sprite when it drew)
                return plain[:-1] + nums + plain[-1:]
        return plain + nums  # default: circle → overlay → number

    def _verdict(self, obj):
        return self.judgments.verdict_for(obj) if self.judgments else None

    def _head_sprites(self, obj, t: float, pos_osu, approach_out,
                      role: str = "hit") -> list[Sprite]:
        """Hit-circle lifecycle sprites (shared by circles and slider heads).
        With judgments: the explosion anchors on the REAL click time; a
        missed head quick-fades at its window close instead of exploding.
        Without judgments: the Phase-1 perfect-hit-at-startTime fallback."""
        color = self._color(obj)
        preempt, fade_in = self.diff.preempt, self.diff.time_fade_in
        start = obj.get_start_time()
        v = self._verdict(obj)
        x, y = self.cam.to_screen(*pos_osu)
        if v is not None and v.hit_time is None:      # head missed
            alpha = miss_fade_alpha(t, start, preempt, fade_in, v.deadline)
            scale = 1.0
            na = alpha
        else:
            hit_time = v.hit_time if v is not None else None
            alpha, scale = circle_alpha_scale(t, start, preempt, fade_in,
                                              hit_time=hit_time)
            na = number_alpha(t, start, preempt, fade_in, hit_time=hit_time)
        sprites: list[Sprite] = []
        if alpha > 0.0:
            sprites = self._circle_sprites(x, y, color, alpha, scale,
                                           obj.combo_number, na, role)
        if self.draw_approach_circles:
            asa = approach_scale_alpha(t, start, preempt, fade_in)
            if asa is not None:
                a_scale, a_alpha = asa
                sk = self.skin
                if sk is not None and sk.has("approachcircle"):
                    aw, ah = sk.size["approachcircle"]
                    k = self.circle_k
                    approach_out.append(Sprite(
                        x, y, aw * k * a_scale, ah * k * a_scale,
                        "sk_approachcircle", (*color, a_alpha)))
                else:
                    ad = 2.0 * self.radius_px * a_scale
                    approach_out.append(Sprite(x, y, ad, ad, "approach",
                                               (*color, a_alpha)))
        return sprites

    def _draw_circle(self, obj, t: float, approach_out) -> None:
        sprites = self._head_sprites(
            obj, t, obj.get_stacked_start_position(self.diff), approach_out)
        if sprites:
            self.spr.draw(sprites)

    def _draw_slider(self, obj, t: float, approach_out) -> None:
        color = self._color(obj)
        preempt, fade_in = self.diff.preempt, self.diff.time_fade_in
        start, end = obj.get_start_time(), obj.get_end_time()
        spawn = start - preempt
        b_alpha = body_alpha(t, start, end, preempt, fade_in)
        pts = self._slider_paths.get(id(obj))
        ticks, arrows = self._slider_marks.get(id(obj), ([], []))
        snake = snake_end_fraction(t, start, preempt, self.snaking_in)
        sprites: list[Sprite] = []
        tip = None
        if b_alpha > 0.0 and pts:
            body_base = (self.track_override if self.track_override is not None
                         else color)   # skin.ini SliderTrackOverride
            body = self.bodies.build_body(
                pts, self.radius_px,
                BodyStyle(body_color=body_base,
                          border_color=self.border_color),
                snake=(0.0, snake))
            self.bodies.draw_body(body, self.spr.fbo, alpha=b_alpha)
            # ticks of the ACTIVE span: above the body, under the circles
            sprites.extend(self._tick_sprites(t, ticks, obj, spawn, fade_in,
                                              snake))
            # tail end circle rides the snake tip (lazer snaking semantics)
            tip = sub_path(pts, 0.0, snake)[-1]
            sprites.extend(self._plain_circle_sprites(tip[0], tip[1],
                                                      color, b_alpha,
                                                      role="slider_end"))
            # tail reverse arrow ON the end circle (rides the tip too)
            sprites.extend(self._arrow_sprites(t, arrows, True, spawn,
                                               fade_in, pts, snake, tip))
        # slider ball following PositionAt(t) (repeats included — scorePath
        # handles the back-and-forth). While the ruleset says tracking was
        # lost the ball dims — the visible sliderbreak cue (there is no
        # tail explosion to lose in this phase).
        if start <= t <= end:
            v = self._verdict(obj)
            tracked = v is None or v.tracked_at(t)
            ball_alpha = 1.0 if tracked else BALL_DETACHED_ALPHA
            bx, by = self.cam.to_screen(
                *obj.get_stacked_position_at(t, self.diff))
            sk = self.skin
            if tracked and sk is not None and sk.has("sliderfollowcircle"):
                # follow circle at 2.4× the circle diameter while tracking
                fw, fh = sk.size["sliderfollowcircle"]
                m = FOLLOW_CIRCLE_SCALE * 2.0 * self.radius_px / max(fw, fh)
                sprites.append(Sprite(bx, by, fw * m, fh * m,
                                      "sk_sliderfollowcircle",
                                      (1.0, 1.0, 1.0, 1.0)))
            if sk is not None and sk.has("sliderb"):
                bw, bh = sk.size["sliderb"]
                k = self.circle_k
                sprites.append(Sprite(bx, by, bw * k, bh * k,
                                      sk.frame_key("sliderb", t),
                                      (*self._ball_tint(color), ball_alpha)))
            else:
                d = 2.0 * self.radius_px
                sprites.append(Sprite(bx, by, d, d, "disc",
                                      (*color, ball_alpha)))
        # head circle (+ its approach ring) on top of body/ball
        sprites.extend(self._head_sprites(
            obj, t, obj.get_stacked_start_position(self.diff), approach_out,
            role="slider_head"))
        if pts:
            # head reverse arrow ON TOP of the head circle
            sprites.extend(self._arrow_sprites(t, arrows, False, spawn,
                                               fade_in, pts, snake, None))
        if sprites:
            self.spr.draw(sprites)

    def _tick_sprites(self, t: float, ticks, obj, spawn: float,
                      fade_in: float, snake: float) -> list[Sprite]:
        """sliderscorepoint sprites for the ACTIVE span only — untinted,
        snake-gated during snake-in, pop-on-hit / vanish-on-miss."""
        if not ticks or obj.part_len <= 0:
            return []
        start = obj.get_start_time()
        active_span = (0 if t < start else
                       min(int((t - start) // obj.part_len),
                           obj.repeat_count - 1))
        sk = self.skin
        use_skin = sk is not None and sk.has("sliderscorepoint")
        if use_skin and "sliderscorepoint" in sk.empty:
            return []          # skin explicitly blanks ticks
        out: list[Sprite] = []
        k = self.circle_k
        for tm, sx, sy, span_start, hit in ticks:
            if tm.span != active_span or snake < tm.progress:
                continue
            asa = tick_alpha_scale(t, tm.time, tm.span, span_start, spawn,
                                   fade_in, hit)
            if asa is None:
                continue
            alpha, scale = asa
            if use_skin:
                w, h = sk.size["sliderscorepoint"]
                out.append(Sprite(sx, sy, w * k * scale, h * k * scale,
                                  "sk_sliderscorepoint",
                                  (1.0, 1.0, 1.0, alpha)))
            else:
                d = TICK_LOGICAL_PX * k * scale
                out.append(Sprite(sx, sy, d, d, "dot",
                                  (1.0, 1.0, 1.0, alpha)))
        return out

    def _arrow_sprites(self, t: float, arrows, at_tail: bool, spawn: float,
                       fade_in: float, pts, snake: float,
                       tip) -> list[Sprite]:
        """reversearrow sprites for one end — WHITE (never combo-tinted),
        beat-pulsed per the §3.2 version gate, pointing along the path
        tangent INTO the slider. The tail arrow rides the snake tip while
        the body grows."""
        sel = [a for a in arrows if a[0].at_tail == at_tail]
        if not sel:
            return []
        sk = self.skin
        use_skin = sk is not None and sk.has("reversearrow")
        if use_skin and "reversearrow" in sk.empty:
            return []
        legacy = sk.info.legacy_v1_behavior if sk is not None else False
        out: list[Sprite] = []
        k = self.circle_k
        for arrow, sx, sy, rot, hit in sel:
            asa = arrow_alpha_scale(t, arrow, spawn, fade_in, hit)
            if asa is None:
                continue
            alpha, pop_scale = asa
            if at_tail and snake < 1.0 and tip is not None:
                sx, sy = tip
                rot = arrow_rotation(sub_path(pts, 0.0, snake), True)
            p_scale, p_rot = arrow_pulse(beat_phase(t, self.beatmap.timings),
                                         legacy)
            scale = pop_scale * p_scale
            if use_skin:
                w, h = sk.size["reversearrow"]
                out.append(Sprite(sx, sy, w * k * scale, h * k * scale,
                                  "sk_reversearrow", (1.0, 1.0, 1.0, alpha),
                                  rotation=rot + p_rot))
            else:
                d = 2.0 * self.radius_px * scale
                out.append(Sprite(sx, sy, d, d, "arrow",
                                  (1.0, 1.0, 1.0, alpha),
                                  rotation=rot + p_rot))
        return out

    def _ball_tint(self, combo_color) -> tuple[float, float, float]:
        """osu semantics: sliderb is combo-tinted only when the skin allows
        it (AllowSliderBallTint), else the skin.ini SliderBall colour, else
        white (untinted)."""
        info = self.skin.info
        if info.slider_ball_tint:
            return combo_color
        if info.slider_ball is not None:
            return tuple(c / 255.0 for c in info.slider_ball)
        return (1.0, 1.0, 1.0)

    # --- judgment popups ---------------------------------------------------------------

    def _popup_sprites(self, t: float) -> list[Sprite]:
        """Procedural judgment indicators (the ruleset phase's sprites):
        300/100/50 as tinted digit runs, miss as the red X — at the
        object's popup position, ResultFadeIn/Out lifecycle."""
        while (self._popup_idx < len(self._popups)
               and self._popups[self._popup_idx].time_ms <= t):
            self._popup_active.append(self._popups[self._popup_idx])
            self._popup_idx += 1
        out: list[Sprite] = []
        keep: list = []
        for ev in self._popup_active:
            asa = popup_alpha_scale(t, ev.time_ms)
            if asa is None:
                if t >= ev.time_ms:      # expired
                    continue
                keep.append(ev)
                continue
            keep.append(ev)
            alpha, scale = asa
            sk = self.skin
            el = POPUP_SKIN_ELEMENT[ev.kind]
            if sk is not None and sk.has(el):
                if el in sk.empty:
                    continue   # skin blanks this judgment (empty hit300)
                x, y = self.cam.to_screen(ev.x, ev.y)
                w, h = sk.size[el]
                k = self.circle_k
                out.append(Sprite(x, y, w * k * scale, h * k * scale,
                                  f"sk_{el}", (1.0, 1.0, 1.0, alpha)))
                continue       # skin sprites draw as-authored, untinted
            color = POPUP_COLORS[ev.kind]
            x, y = self.cam.to_screen(ev.x, ev.y)
            if ev.kind is JudgmentKind.MISS:
                d = 2.2 * self.radius_px * scale
                out.append(Sprite(x, y, d, d, "miss_x", (*color, alpha)))
                continue
            if ev.kind is JudgmentKind.HIT300:
                alpha *= POPUP_300_ALPHA
            h = 2.0 * self.radius_px * POPUP_HEIGHT_FRAC * scale
            for ch, dx, w in layout_digits(int(ev.kind.value),
                                           self.bank.digit_aspect, h):
                out.append(Sprite(x + dx, y, w, h, f"digit_{ch}",
                                  (*color, alpha)))
        self._popup_active = keep
        return out

    # --- follow points -----------------------------------------------------------------

    def _followpoint_sprites(self, t: float) -> list[Sprite]:
        """The dotted same-combo connections (markers.py schedule): skin
        `followpoint` (frame 0 / AnimationFramerate cycling) or the
        procedural dot, rotated along the line, drawn UNDER all objects."""
        while (self._fp_idx < len(self._fp_dots)
               and self._fp_dots[self._fp_idx].fade_in <= t):
            self._fp_active.append(self._fp_dots[self._fp_idx])
            self._fp_idx += 1
        fade = self.diff.time_fade_in
        sk = self.skin
        use_skin = sk is not None and sk.has("followpoint")
        blanked = use_skin and "followpoint" in sk.empty
        out: list[Sprite] = []
        keep: list = []
        k = self.circle_k
        for dot in self._fp_active:
            if t > dot.fade_out + fade:
                continue                  # expired
            keep.append(dot)
            if blanked:
                continue                  # skin explicitly blanks followpoints
            st = followpoint_state(t, dot, fade)
            if st is None:
                continue
            alpha, x, y, scale = st
            sx, sy = self.cam.to_screen(x, y)
            if use_skin:
                w, h = sk.size["followpoint"]
                out.append(Sprite(sx, sy, w * k * scale, h * k * scale,
                                  sk.frame_key("followpoint", t),
                                  (1.0, 1.0, 1.0, alpha),
                                  rotation=dot.rotation))
            else:
                d = FP_DOT_LOGICAL_PX * k * scale
                out.append(Sprite(sx, sy, d, d, "dot",
                                  (1.0, 1.0, 1.0, 0.85 * alpha),
                                  rotation=dot.rotation))
        self._fp_active = keep
        return out

    # --- cursor -----------------------------------------------------------------------

    def _cursor_sprites(self, t: float) -> list[Sprite]:
        if self.use_skin_cursor:
            return self._skin_cursor_sprites(t)
        out: list[Sprite] = []
        d_glow = 2.0 * self.cam.len_to_screen(CURSOR_RADIUS_OSU) * self.cursor_scale
        for ti, k in trail_times(t):
            x, y, _ = cursor_at(self.frames, ti)
            sx, sy = self.cam.to_screen(x, y)
            s = d_glow * (0.55 + 0.35 * k)
            out.append(Sprite(sx, sy, s, s, "glow",
                              (*CURSOR_GLOW_COLOR, 0.28 * k), additive=True))
        x, y, _ = cursor_at(self.frames, t)
        sx, sy = self.cam.to_screen(x, y)
        core = d_glow * 0.62
        out.append(Sprite(sx, sy, core, core, "disc", (1.0, 1.0, 1.0, 1.0)))
        out.append(Sprite(sx, sy, core, core, "ring",
                          (*CURSOR_GLOW_COLOR, 0.9)))
        out.append(Sprite(sx, sy, d_glow * 1.6, d_glow * 1.6, "glow",
                          (*CURSOR_GLOW_COLOR, 0.5), additive=True))
        return out

    def _skin_cursor_sprites(self, t: float) -> list[Sprite]:
        """Skin cursor: trail snapshots under the cursor, cursormiddle on
        top. Sized in stable's 768-line UI space (native logical px ×
        screen_h/768 × cursor_scale — NOT circle-tied). CursorCentre=0
        hangs the texture from the pointer (stable's top-left anchor).

        Trail per the §3.3 two-mode rule (self.long_trail): cursormiddle
        present (or ForceLongTrail) → LONG CONNECTED trail — cursortrail
        laid along the cursor path by DISTANCE; else the classic sparse
        16.67 ms drops fading over TRAIL_SPRITE_LIFE_MS."""
        sk = self.skin
        k = (self.cam.screen_h / CURSOR_UI_HEIGHT) * self.cursor_scale
        centre = sk.info.cursor_centre
        out: list[Sprite] = []
        if sk.has("cursortrail"):
            tw, th = sk.size["cursortrail"]
            ox, oy = (0.0, 0.0) if centre else (tw * k / 2.0, th * k / 2.0)
            if self.long_trail:
                pts = long_trail_points(
                    lambda ti: cursor_at(self.frames, ti)[:2], t)
                for x, y, strength in pts:
                    sx, sy = self.cam.to_screen(x, y)
                    out.append(Sprite(sx + ox, sy + oy, tw * k, th * k,
                                      "sk_cursortrail",
                                      (1.0, 1.0, 1.0, 0.85 * strength)))
            else:
                for ti, strength in sparse_trail_times(t):
                    x, y, _ = cursor_at(self.frames, ti)
                    sx, sy = self.cam.to_screen(x, y)
                    out.append(Sprite(sx + ox, sy + oy, tw * k, th * k,
                                      "sk_cursortrail",
                                      (1.0, 1.0, 1.0, 0.85 * strength)))
        x, y, _ = cursor_at(self.frames, t)
        sx, sy = self.cam.to_screen(x, y)
        cw, ch = sk.size["cursor"]
        ox, oy = (0.0, 0.0) if centre else (cw * k / 2.0, ch * k / 2.0)
        out.append(Sprite(sx + ox, sy + oy, cw * k, ch * k, "sk_cursor",
                          (1.0, 1.0, 1.0, 1.0)))
        if sk.has("cursormiddle"):
            mw, mh = sk.size["cursormiddle"]
            out.append(Sprite(sx, sy, mw * k, mh * k, "sk_cursormiddle",
                              (1.0, 1.0, 1.0, 1.0)))
        return out


class ScenePlayer:
    """record.pipeline.Player over a StdScene: fixed-timestep map clock.

    §5.3 Update semantics: map time advances delta*speed per tick (DT/HT
    rate mods change speed; frame TIMES stay wall-clock so the video runs
    at the right rate with atempo'd audio).
    """

    def __init__(self, scene: StdScene, end_ms: float,
                 speed: float = 1.0, start_ms: float = 0.0):
        self.scene = scene
        self.t = start_ms
        self.end_ms = end_ms
        self.speed = speed

    def update(self, delta_ms: float) -> bool:
        self.t += delta_ms * self.speed
        return self.t >= self.end_ms

    def draw(self):
        return self.scene.frame_rgb(self.t)


def log_skips(scene: StdScene) -> None:
    if scene.skipped_spinners:
        print(f"note: skipped {scene.skipped_spinners} spinner(s) "
              "(no spinner visuals in this phase)", file=sys.stderr)
