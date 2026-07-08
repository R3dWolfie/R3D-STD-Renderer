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

PHASE-1 SIMPLIFICATIONS (each is a later-phase item):
  * judgments: every object is assumed HIT exactly at startTime — the
    ruleset sim (hit windows, notelock, sliderbreaks) is the next phase,
    so explosions always fire on time and nothing ever "misses".
  * spinners: no visuals; counted and logged once at the end of the run.
  * no reverse arrows / slider ticks / follow points / hit lighting yet.
  * slider bodies are rebuilt every frame (0.2 ms/body — fine at this
    phase; an FBO cache per static body is the known perf step).

The lifecycle math lives in module-level pure functions so tests need no
GL context.
"""
from __future__ import annotations

import math
import sys

from ..beatmap.difficulty import HIT_FADE_OUT
from ..beatmap.objects import Slider, Spinner
from ..replay.replay import cursor_at
from .gl import Sprite
from .slider_body import DEFAULT_COMBO_COLORS, BodyStyle, sub_path

EXPLODE_SCALE = 1.4            # §2.5 hit-explosion end scale (skin v2+)
NUMBER_FADE_OUT = 60.0         # §3.2 v2+ combo-number quick fade (ms)
APPROACH_START_SCALE = 4.0     # §2.5 approach circle 4→1
APPROACH_MAX_ALPHA = 0.9       # stable caps the approach ring alpha
SNAKE_IN_PORTION = 1.0 / 3.0   # lazer: snake-in completes after preempt/3
TRAIL_WINDOW_MS = 150.0
TRAIL_STEPS = 12
CURSOR_RADIUS_OSU = 14.0       # cursor core sizing base, osu!px
CURSOR_GLOW_COLOR = (1.0, 0.30, 0.35)   # R3D red
DIGIT_SPACING = 0.06           # of digit height, between combo digits


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
    """[(sample_time, strength 0..1)] oldest→newest for the cursor trail."""
    step = window_ms / steps
    return [(t - i * step, 1.0 - i / (steps + 1.0))
            for i in range(steps, 0, -1)]


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
                 draw_cursor: bool = True,
                 cursor_scale: float = 1.0,
                 background: tuple[float, float, float] = (0.043, 0.043, 0.055)):
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
        self.draw_cursor = draw_cursor
        self.cursor_scale = cursor_scale
        self.background = background

        self.objects = sorted(beatmap.hit_objects,
                              key=lambda o: o.get_start_time())
        self.radius_px = camera.len_to_screen(self.diff.circle_radius)
        self._spawn_idx = 0
        self._active: list = []
        self._last_t = -math.inf
        self._slider_paths: dict[int, list[tuple[float, float]]] = {}
        self.skipped_spinners = 0
        self._spinner_ids: set[int] = set()

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
        keep = []
        for obj in self._active:
            if t <= obj.get_end_time() + HIT_FADE_OUT:
                keep.append(obj)
            else:
                self._slider_paths.pop(id(obj), None)
        self._active = keep

    # --- frame draw ---------------------------------------------------------------

    def render_frame(self, t: float) -> None:
        self._advance(t)
        self.spr.begin(clear=self.background)
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
        if self.draw_cursor and self.frames:
            self.spr.draw(self._cursor_sprites(t))

    def frame_rgb(self, t: float):
        self.render_frame(t)
        return self.spr.read_rgb()

    # --- per-object draws -----------------------------------------------------------

    def _color(self, obj) -> tuple[float, float, float]:
        return self.combo_colors[obj.combo_set % len(self.combo_colors)]

    def _circle_sprites(self, x: float, y: float, color, alpha: float,
                        scale: float, number: int | None,
                        num_alpha: float) -> list[Sprite]:
        d = 2.0 * self.radius_px * scale
        sprites = [
            Sprite(x, y, d, d, "disc", (*color, alpha)),
            Sprite(x, y, d, d, "ring", (1.0, 1.0, 1.0, alpha)),
        ]
        if (number is not None and self.draw_combo_numbers
                and num_alpha > 0.0):
            h = self.radius_px  # digit height = half the circle diameter
            for ch, dx, w in layout_digits(number, self.bank.digit_aspect, h):
                sprites.append(Sprite(x + dx, y, w, h, f"digit_{ch}",
                                      (1.0, 1.0, 1.0, num_alpha)))
        return sprites

    def _head_sprites(self, obj, t: float, pos_osu, approach_out) -> list[Sprite]:
        """Hit-circle lifecycle sprites (shared by circles and slider heads)."""
        color = self._color(obj)
        preempt, fade_in = self.diff.preempt, self.diff.time_fade_in
        start = obj.get_start_time()
        alpha, scale = circle_alpha_scale(t, start, preempt, fade_in)
        x, y = self.cam.to_screen(*pos_osu)
        sprites: list[Sprite] = []
        if alpha > 0.0:
            na = number_alpha(t, start, preempt, fade_in)
            sprites = self._circle_sprites(x, y, color, alpha, scale,
                                           obj.combo_number, na)
        if self.draw_approach_circles:
            asa = approach_scale_alpha(t, start, preempt, fade_in)
            if asa is not None:
                a_scale, a_alpha = asa
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
        b_alpha = body_alpha(t, start, end, preempt, fade_in)
        pts = self._slider_paths.get(id(obj))
        sprites: list[Sprite] = []
        if b_alpha > 0.0 and pts:
            snake = snake_end_fraction(t, start, preempt, self.snaking_in)
            body = self.bodies.build_body(
                pts, self.radius_px,
                BodyStyle(body_color=color), snake=(0.0, snake))
            self.bodies.draw_body(body, self.spr.fbo, alpha=b_alpha)
            # tail end circle rides the snake tip (lazer snaking semantics)
            tip = sub_path(pts, 0.0, snake)[-1]
            d = 2.0 * self.radius_px
            sprites.append(Sprite(tip[0], tip[1], d, d, "disc",
                                  (*color, b_alpha)))
            sprites.append(Sprite(tip[0], tip[1], d, d, "ring",
                                  (1.0, 1.0, 1.0, b_alpha)))
        # slider ball: plain disc following PositionAt(t) (repeats included
        # — scorePath handles the back-and-forth)
        if start <= t <= end:
            bx, by = self.cam.to_screen(
                *obj.get_stacked_position_at(t, self.diff))
            d = 2.0 * self.radius_px
            sprites.append(Sprite(bx, by, d, d, "disc", (*color, 1.0)))
        # head circle (+ its approach ring) on top of body/ball
        sprites.extend(self._head_sprites(
            obj, t, obj.get_stacked_start_position(self.diff), approach_out))
        if sprites:
            self.spr.draw(sprites)

    # --- cursor -----------------------------------------------------------------------

    def _cursor_sprites(self, t: float) -> list[Sprite]:
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
