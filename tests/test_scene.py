"""Scene lifecycle math (§2.5 fades / §4.9 snaking / §5.3 order) — pure
CPU tests, plus a GPU smoke test that skips gracefully without EGL (the
run_all contract: tests must pass on any dev box)."""
from __future__ import annotations

import math

from osu_std_renderer.beatmap.difficulty import HIT_FADE_OUT
from osu_std_renderer.render.scene import (
    APPROACH_MAX_ALPHA, APPROACH_START_SCALE, EXPLODE_SCALE, NUMBER_FADE_OUT,
    approach_scale_alpha, body_alpha, circle_alpha_scale, fade_in_alpha,
    layout_digits, number_alpha, snake_end_fraction, trail_times,
    visible_window,
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
100,100,2000,2,0,L|400:100,1,280
"""


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
