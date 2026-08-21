"""Scene lifecycle math (§2.5 fades / §4.9 snaking / §5.3 order) — pure
CPU tests, plus a GPU smoke test that skips gracefully without EGL (the
run_all contract: tests must pass on any dev box)."""
from __future__ import annotations

import math

from osu_std_renderer.beatmap.difficulty import (HIT_FADE_OUT,
                                                 RESULT_FADE_IN,
                                                 RESULT_FADE_OUT)
from osu_std_renderer.render.scene import (
    APPROACH_MAX_ALPHA, APPROACH_START_SCALE, EXPLODE_SCALE,
    MISS_FALL_DISTANCE_OSU, MISS_FALL_ROT_RAD, NUMBER_FADE_OUT, RESULT_HOLD,
    approach_scale_alpha, body_alpha, circle_alpha_scale, fade_in_alpha,
    layout_digits, miss_fall_transform, number_alpha, popup_alpha_scale,
    playfield_border_rects, snake_end_fraction, ssaa_internal_size,
    trail_times, visible_window,
)

# a typical AR9.4-ish object: preempt 540, fade-in 400
START, PRE, FIN = 2212.0, 540.0, 400.0
SPAWN = START - PRE


def test_fade_in_ramp():
    assert fade_in_alpha(SPAWN - 1, START, PRE, FIN) == 0.0
    assert fade_in_alpha(SPAWN, START, PRE, FIN) == 0.0
    assert abs(fade_in_alpha(SPAWN + 200, START, PRE, FIN) - 0.5) < 1e-9
    assert fade_in_alpha(SPAWN + FIN, START, PRE, FIN) == 1.0
    assert fade_in_alpha(START, START, PRE, FIN) == 1.0     # preempt > fade-in
    # degenerate fade-in never divides by zero
    assert fade_in_alpha(SPAWN + 1, START, PRE, 0.0) == 1.0


def test_circle_pre_hit():
    a, s = circle_alpha_scale(SPAWN - 10, START, PRE, FIN)
    assert a == 0.0 and s == 1.0
    a, s = circle_alpha_scale(SPAWN + 200, START, PRE, FIN)
    assert abs(a - 0.5) < 1e-9 and s == 1.0
    a, s = circle_alpha_scale(START, START, PRE, FIN)
    assert a == 1.0 and s == 1.0


def test_circle_hit_explosion():
    # §2.5: alpha 1→0, scale 1→1.4 over HitFadeOut=240 ms after the hit
    a, s = circle_alpha_scale(START + HIT_FADE_OUT / 2, START, PRE, FIN)
    assert abs(a - 0.5) < 1e-9
    assert abs(s - (1.0 + (EXPLODE_SCALE - 1.0) / 2)) < 1e-9
    a, s = circle_alpha_scale(START + HIT_FADE_OUT, START, PRE, FIN)
    assert a == 0.0 and s == EXPLODE_SCALE
    # late hit (future ruleset): explosion anchors on hit_time
    a, s = circle_alpha_scale(START + 130, START, PRE, FIN, hit_time=START + 10)
    assert abs(a - 0.5) < 1e-9


def test_approach_scale_and_alpha():
    s, a = approach_scale_alpha(SPAWN, START, PRE, FIN)
    assert s == APPROACH_START_SCALE and a == 0.0
    s, a = approach_scale_alpha(SPAWN + PRE / 2, START, PRE, FIN)
    assert abs(s - (APPROACH_START_SCALE + 1.0) / 2) < 1e-9
    assert abs(a - (PRE / 2) / FIN) < 1e-9   # still riding the fade-in ramp
    _, a = approach_scale_alpha(SPAWN + FIN, START, PRE, FIN)
    assert a == APPROACH_MAX_ALPHA           # fade-in done → capped at 0.9
    s, a = approach_scale_alpha(START - 1e-6, START, PRE, FIN)
    assert abs(s - 1.0) < 1e-5
    # gone at/after the hit and before spawn
    assert approach_scale_alpha(START, START, PRE, FIN) is None
    assert approach_scale_alpha(SPAWN - 1, START, PRE, FIN) is None


def test_number_quick_fade():
    assert number_alpha(START, START, PRE, FIN) == 1.0
    assert abs(number_alpha(START + NUMBER_FADE_OUT / 2, START, PRE, FIN) - 0.5) < 1e-9
    assert number_alpha(START + NUMBER_FADE_OUT, START, PRE, FIN) == 0.0
    # pre-hit it rides the fade-in ramp
    assert abs(number_alpha(SPAWN + 200, START, PRE, FIN) - 0.5) < 1e-9


def test_snake_in():
    third = PRE / 3.0
    assert snake_end_fraction(SPAWN, START, PRE) == 0.0
    assert abs(snake_end_fraction(SPAWN + third / 2, START, PRE) - 0.5) < 1e-9
    assert snake_end_fraction(SPAWN + third, START, PRE) == 1.0
    assert snake_end_fraction(START, START, PRE) == 1.0
    # snaking off → always fully grown
    assert snake_end_fraction(SPAWN, START, PRE, snaking_in=False) == 1.0


def test_body_alpha_lifecycle():
    end = START + 600.0
    assert body_alpha(SPAWN, START, end, PRE, FIN) == 0.0
    assert abs(body_alpha(SPAWN + 200, START, end, PRE, FIN) - 0.5) < 1e-9
    assert body_alpha(START + 300, START, end, PRE, FIN) == 1.0   # mid-slide
    assert abs(body_alpha(end + HIT_FADE_OUT / 2, START, end, PRE, FIN) - 0.5) < 1e-9
    assert body_alpha(end + HIT_FADE_OUT, START, end, PRE, FIN) == 0.0


def test_visible_window():
    class _O:
        def get_start_time(self):
            return START

        def get_end_time(self):
            return START + 600.0

    lo, hi = visible_window(_O(), PRE)
    assert lo == SPAWN and hi == START + 600.0 + HIT_FADE_OUT


def test_layout_digits_centred():
    aspects = {"1": 0.5, "2": 0.6}
    placed = layout_digits(12, aspects, 100.0, spacing=0.06)
    assert [p[0] for p in placed] == ["1", "2"]
    (c1, x1, w1), (c2, x2, w2) = placed
    assert w1 == 50.0 and w2 == 60.0
    total = 50.0 + 60.0 + 6.0
    assert abs((x1 - w1 / 2) - (-total / 2)) < 1e-9     # left edge
    assert abs((x2 + w2 / 2) - (total / 2)) < 1e-9      # right edge
    assert abs((x2 - w2 / 2) - (x1 + w1 / 2) - 6.0) < 1e-9   # gap
    # single digit sits dead centre
    only = layout_digits(7, {"7": 0.55}, 80.0)
    assert len(only) == 1 and abs(only[0][1]) < 1e-9


def test_trail_times_shape():
    ts = trail_times(1000.0, window_ms=150.0, steps=12)
    assert len(ts) == 12
    assert ts[0][0] == 1000.0 - 150.0                    # oldest first
    assert ts[-1][0] == 1000.0 - 150.0 / 12
    assert all(ts[i][0] < ts[i + 1][0] for i in range(11))
    assert all(0.0 < k < 1.0 for _, k in ts)
    assert ts[-1][1] > ts[0][1]                          # newer = stronger


def test_miss_fall_transform():
    """Classic miss: starts at the popup position, drifts DOWN with a
    slight rotation as the popup fades (quad-in over the popup life)."""
    dur = RESULT_FADE_IN + RESULT_HOLD + RESULT_FADE_OUT
    dy0, rot0 = miss_fall_transform(0.0, seed=7)
    assert dy0 == 0.0 and rot0 == 0.0
    dy_end, rot_end = miss_fall_transform(dur, seed=7)
    assert abs(dy_end - MISS_FALL_DISTANCE_OSU) < 1e-9
    assert abs(rot_end) <= MISS_FALL_ROT_RAD + 1e-9
    # monotone fall, ease-in (slow start)
    dys = [miss_fall_transform(a, seed=7)[0] for a in range(0, 721, 60)]
    assert all(b >= a for a, b in zip(dys, dys[1:]))
    assert dys[1] < MISS_FALL_DISTANCE_OSU * (60.0 / dur)   # slower than linear
    # deterministic per seed; different objects can rotate differently
    assert miss_fall_transform(500.0, seed=7) == miss_fall_transform(500.0, seed=7)
    rots = {round(miss_fall_transform(dur, seed=s)[1], 6) for s in range(6)}
    assert len(rots) > 1


def test_popup_holds_full_then_fades():
    """M-2: a judgment popup fades in, HOLDS full opacity for RESULT_HOLD ms,
    then fades out — so two closely-spaced popups both read crisp."""
    from osu_std_renderer.beatmap.difficulty import (RESULT_FADE_IN,
                                                     RESULT_FADE_OUT)
    t0 = 1000.0
    # mid fade-in: below full
    a_in, _ = popup_alpha_scale(t0 + RESULT_FADE_IN * 0.5, t0)
    assert 0.4 < a_in < 0.6
    # just after fade-in and all through the hold: FULL
    a_hold0, _ = popup_alpha_scale(t0 + RESULT_FADE_IN + 1.0, t0)
    a_hold1, _ = popup_alpha_scale(t0 + RESULT_FADE_IN + RESULT_HOLD - 1.0, t0)
    assert a_hold0 == 1.0 and a_hold1 == 1.0
    # into the fade-out: strictly decreasing, still visible partway
    a_out = popup_alpha_scale(
        t0 + RESULT_FADE_IN + RESULT_HOLD + RESULT_FADE_OUT * 0.5, t0)[0]
    assert 0.3 < a_out < 0.7
    # gone after the full life
    assert popup_alpha_scale(
        t0 + RESULT_FADE_IN + RESULT_HOLD + RESULT_FADE_OUT + 1.0, t0) is None


def test_reverse_arrow_pinned_at_marker_position():
    """M-4: the reverse arrow is drawn at its marker position (slider end),
    NOT the snake-in tip — so it no longer floats above the end circle or
    leaves a crescent near the head. Exercise _arrow_sprites with a bogus
    `tip` far away and a mid-snake `snake`; the emitted sprite must sit at
    the input (sx, sy)."""
    from types import SimpleNamespace
    from osu_std_renderer.render.scene import StdScene
    from osu_std_renderer.render.markers import ReverseArrow

    class _Pt:
        beat_length_base = 500.0
        time = 0.0

    class _Timings:
        def get_original_point_at(self, t):
            return _Pt()

    fake = SimpleNamespace(
        skin=None, circle_k=1.0, radius_px=30.0,
        beatmap=SimpleNamespace(timings=_Timings()))
    arrow = ReverseArrow(r=1, time=2000.0, appear=1000.0, at_tail=True)
    marker_x, marker_y = 400.0, 250.0
    records = [(arrow, marker_x, marker_y, 0.0, True)]
    tip = (999.0, 5.0)             # deliberately nowhere near the marker
    sprites = StdScene._arrow_sprites(
        fake, 1500.0, records, True, spawn=1000.0, fade_in=400.0,
        pts=[(0.0, 0.0), (marker_x, marker_y)], snake=0.4, tip=tip)
    assert len(sprites) == 1
    assert abs(sprites[0].x - marker_x) < 1e-6
    assert abs(sprites[0].y - marker_y) < 1e-6


def test_argon_slider_tail_draws_nothing():
    """Fix: real Argon has NO slider-tail circle. lazer's DrawableSliderTail
    wraps a SkinnableDrawable(SliderTailHitCircle, _ => Empty()) ("no default
    for this; only visible in legacy skins") and OsuArgonSkinTransformer has no
    SliderTailHitCircle case, so it resolves to that Empty() fallback — the
    ArgonSliderBody's rounded snake cap IS the visible end ("the body just
    ends"). Therefore the skinless Argon league (skin is None) must emit ZERO
    sprites for role="slider_end", while head/hit roles keep the ArgonMain-
    CirclePiece (accent disc + white border) and custom/legacy skins keep
    their sliderendcircle byte-for-byte."""
    import types
    from types import SimpleNamespace
    from osu_std_renderer.render.scene import StdScene

    argon = SimpleNamespace(skin=None, radius_px=30.0, circle_k=1.0)
    # head/hit roles delegate to _argon_circle_sprites (uses self.radius_px)
    argon._argon_circle_sprites = types.MethodType(
        StdScene._argon_circle_sprites, argon)
    col = (1.0, 0.5, 0.0)
    # Argon slider tail: nothing drawn (was argon_circle + argon_border before)
    tail = StdScene._plain_circle_sprites(argon, 100.0, 100.0, col, 1.0,
                                          role="slider_end")
    assert tail == [], f"Argon slider tail must draw nothing, got {tail}"
    # Argon slider HEAD is unchanged: accent disc under the white ring
    head = StdScene._plain_circle_sprites(argon, 100.0, 100.0, col, 1.0,
                                          role="slider_head")
    assert [s.texture_key for s in head] == ["argon_circle", "argon_border"]
    # Argon plain hit circle likewise unchanged
    hit = StdScene._plain_circle_sprites(argon, 100.0, 100.0, col, 1.0,
                                         role="hit")
    assert [s.texture_key for s in hit] == ["argon_circle", "argon_border"]

    # custom/legacy skin: the slider end STILL draws its sliderendcircle
    fake_skin = SimpleNamespace(
        empty=set(),
        size={"sliderendcircle": (128.0, 128.0)},
        circle_elements=lambda role: ("sliderendcircle", None))
    skinned = SimpleNamespace(skin=fake_skin, radius_px=30.0, circle_k=1.0)
    ct = StdScene._plain_circle_sprites(skinned, 100.0, 100.0,
                                        (1.0, 1.0, 1.0), 1.0,
                                        role="slider_end")
    assert len(ct) == 1 and ct[0].texture_key == "sk_sliderendcircle", ct


def test_warning_arrows_at_four_corners():
    """CRITICAL-3: break-end warning arrows sit at the FOUR playfield corners
    (skin arrow-warning sprite when shipped, procedural fallback otherwise),
    not two mid-edge arrows."""
    from types import SimpleNamespace
    from osu_std_renderer.render.scene import StdScene

    class _Cam:
        screen_w = 1280
        screen_h = 720

        def to_screen(self, x, y):
            return (256.0 + x * 1.5, 72.0 + y * 1.5)

        def len_to_screen(self, v):
            return v * 1.5

    t = 850.0                                 # inside a blink "on" window
    # procedural fallback (no skin)
    proc = SimpleNamespace(_warn_anchors=[1000.0], cam=_Cam(), skin=None,
                           _skinned=lambda name: False)
    ps = StdScene._warning_arrow_sprites(proc, t)
    assert len(ps) == 4
    want = {(256.0 + x * 1.5, 72.0 + y * 1.5)
            for x, y in ((-59, 52), (571, 52), (-59, 332), (571, 332))}
    assert {(round(s.x, 3), round(s.y, 3)) for s in ps} == \
        {(round(x, 3), round(y, 3)) for x, y in want}
    # skin arrow-warning path: four sk_arrow-warning sprites, same corners
    sk = SimpleNamespace(size={"arrow-warning": (260.0, 250.0)})
    skn = SimpleNamespace(_warn_anchors=[1000.0], cam=_Cam(), skin=sk,
                          _skinned=lambda name: True)
    ss = StdScene._warning_arrow_sprites(skn, t)
    assert len(ss) == 4
    assert all(s.texture_key == "sk_arrow-warning" for s in ss)
    xs = sorted({round(s.x) for s in ss})
    ys = sorted({round(s.y) for s in ss})
    assert len(xs) == 2 and len(ys) == 2         # symmetric 2×2 corner grid
    assert abs((xs[0] + xs[1]) / 2 - 640) < 1.0   # centred horizontally


def test_playfield_border_rects_modes():
    x0, y0, x1, y1 = 100.0, 50.0, 500.0, 350.0
    assert playfield_border_rects(x0, y0, x1, y1, "none", 2.0, 30.0) == []
    full = playfield_border_rects(x0, y0, x1, y1, "full", 2.0, 30.0)
    assert len(full) == 4
    # every rect stays INSIDE the bounds box
    for cx, cy, w, h in full:
        assert x0 - 1e-9 <= cx - w / 2.0 and cx + w / 2.0 <= x1 + 1e-9
        assert y0 - 1e-9 <= cy - h / 2.0 and cy + h / 2.0 <= y1 + 1e-9
    # top edge spans the full width at thickness 2
    top = min(full, key=lambda r: r[1])
    assert abs(top[2] - (x1 - x0)) < 1e-9 and abs(top[3] - 2.0) < 1e-9
    edges = playfield_border_rects(x0, y0, x1, y1, "edges", 2.0, 30.0)
    assert len(edges) == 8            # an L pair per corner
    for cx, cy, w, h in edges:
        assert max(w, h) <= 30.0 + 1e-9          # short corner strokes
        assert x0 - 1e-9 <= cx - w / 2.0 and cx + w / 2.0 <= x1 + 1e-9
        assert y0 - 1e-9 <= cy - h / 2.0 and cy + h / 2.0 <= y1 + 1e-9
    # degenerate box → nothing
    assert playfield_border_rects(10.0, 10.0, 10.0, 40.0, "full", 2.0, 30.0) == []


# --- GPU smoke test (skips without EGL) ------------------------------------------

_SYNTH_OSU = """osu file format v14

[General]
AudioFilename: none.mp3
Mode: 0

[Metadata]
Title:Scene Smoke
Artist:Test
Creator:tests
Version:smoke

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,100,1,0

[HitObjects]
256,192,1000,5,0,0:0:0:0:
100,100,2000,2,0,L|400:100,2,280
"""
# the slider REPEATS (2 spans) and shares the circle's combo — the smoke
# frame exercises reverse arrows, slider ticks and follow points too


def test_scene_gpu_smoke():
    try:
        from osu_std_renderer.render.gl import SpriteRenderer
        spr = SpriteRenderer(320, 240)
    except Exception:  # noqa: BLE001 — no EGL device on this box
        print("SKIP (no GL context)")
        return
    import tempfile
    from pathlib import Path

    import numpy as np

    from osu_std_renderer.beatmap import load_full
    from osu_std_renderer.render.playfield import PlayfieldCamera
    from osu_std_renderer.render.scene import StdScene
    from osu_std_renderer.render.slider_body import SliderBodyRenderer
    from osu_std_renderer.render.textures import TextureBank
    from osu_std_renderer.replay.replay import StdFrame

    with tempfile.NamedTemporaryFile("w", suffix=".osu", delete=False) as fh:
        fh.write(_SYNTH_OSU)
        osu_path = Path(fh.name)
    try:
        bm = load_full(osu_path)
        bank = TextureBank(spr)
        bodies = SliderBodyRenderer(spr.ctx, 320, 240)
        cam = PlayfieldCamera(320, 240)
        frames = [StdFrame(0, 256.0, 192.0, 0), StdFrame(3000, 100.0, 100.0, 1)]
        scene = StdScene(bm, frames, cam, spr, bodies, bank)
        # mid-approach on the circle: disc + approach ring + cursor visible
        rgb = scene.frame_rgb(800.0)
        assert rgb.shape == (240, 320, 3)
        assert int(rgb.max()) > 40      # something bright rendered
        # circle centre (256,192 osu) must carry the first combo colour
        cx, cy = cam.to_screen(256.0, 192.0)
        px = rgb[int(cy), int(cx)].astype(int)
        assert px.sum() > 150, f"hit circle not drawn at centre: {px}"
        # monotonic-time contract holds across frames; slider mid-slide draws
        rgb2 = scene.frame_rgb(2100.0)
        assert int(rgb2.max()) > 40
        went_back = False
        try:
            scene.frame_rgb(100.0)
        except ValueError:
            went_back = True
        assert went_back, "scene must reject time going backwards"
    finally:
        osu_path.unlink(missing_ok=True)
        spr.release()


def test_ssaa_internal_size():
    # sub-1080p: lift height to 1080, scale width to keep the output aspect
    assert ssaa_internal_size(1280, 720) == (1920, 1080)
    assert ssaa_internal_size(854, 480) == (1922, 1080)     # 16:9-ish
    iw, ih = ssaa_internal_size(640, 360)
    assert ih == 1080 and iw == 1920
    # aspect ratio is preserved (within rounding)
    assert abs(iw / ih - 640 / 360) < 1e-3
    # no-op at exactly 1080p and above (results already native-or-better)
    assert ssaa_internal_size(1920, 1080) == (1920, 1080)
    assert ssaa_internal_size(2560, 1440) == (2560, 1440)
    assert ssaa_internal_size(3840, 2160) == (3840, 2160)
    # a >1080p but non-16:9 output is still a pass-through
    assert ssaa_internal_size(1080, 1080) == (1080, 1080)



def test_slider_ball_fades_out_after_end_not_hard_cut():
    # BUG #3 fix: the slider ball no longer hard-cuts at EndTime; osu's
    # DrawableSlider does FadeOut(240).Expire(), so the ball (a child)
    # fades linearly over 240 ms after EndTime. OsuModHidden never touches
    # the ball, and its fade-IN at slider start is unchanged.
    from osu_std_renderer.render.scene import (BALL_DETACHED_ALPHA,
                                               BALL_FADE_OUT_MS,
                                               slider_ball_alpha)
    start, end = 1000.0, 1600.0

    def close(a, b):
        return abs(a - b) < 1e-9

    # tracking: full alpha across the slider, None before it appears
    assert slider_ball_alpha(start - 1e-6, start, end, 1.0) is None
    assert slider_ball_alpha(start, start, end, 1.0) == 1.0
    assert slider_ball_alpha((start + end) / 2, start, end, 1.0) == 1.0
    assert slider_ball_alpha(end, start, end, 1.0) == 1.0
    # post-end fade instead of vanishing; seeks around end
    assert close(slider_ball_alpha(end + 1.0, start, end, 1.0),
                 1.0 - 1.0 / BALL_FADE_OUT_MS)
    assert close(slider_ball_alpha(end + 120.0, start, end, 1.0), 0.5)
    assert close(slider_ball_alpha(end + BALL_FADE_OUT_MS, start, end, 1.0), 0.0)
    assert slider_ball_alpha(end + BALL_FADE_OUT_MS + 1.0, start, end, 1.0) is None
    # detached (tracking lost): fade starts from the ball's CURRENT dimmed
    # alpha, not full
    d = BALL_DETACHED_ALPHA
    assert slider_ball_alpha((start + end) / 2, start, end, d) == d
    assert slider_ball_alpha(end, start, end, d) == d
    assert close(slider_ball_alpha(end + 120.0, start, end, d), d * 0.5)
    assert close(slider_ball_alpha(end + BALL_FADE_OUT_MS, start, end, d), 0.0)
    # HD / HDHR never touch the ball: the alpha is independent of the Hidden
    # body fade (no mod argument here), so the lifetime is identical.
    assert slider_ball_alpha(end + 120.0, start, end, 1.0) == 0.5
