"""Actual GL pixels, Metal command ordering, scene authority and geometry."""
from __future__ import annotations

import ast
import dataclasses
import math
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from osu_std_renderer.render import perf
from osu_std_renderer.render.gl import Sprite, SpriteRenderer, plan_texture_batches
from osu_std_renderer.render.playfield import PlayfieldCamera
from osu_std_renderer.render.scene import StdScene, snake_range
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.slider_body import SliderBodyRenderer, sub_path
from osu_std_renderer.render.slider_visuals import ball_variant, draw_slider_layers
from osu_std_renderer.render.textures import TextureBank, bake_argon_follow
from osu_std_renderer.ruleset import JudgmentKind, StdRuleset
from osu_std_renderer.skin.skin import Skin
from tests.test_ruleset import _map, _frames, _ball_frames, _meta
from tests.test_slider_chronological import _follow


def _scene(bm=None, frames=None, *, skin_dir=None, merge=False, sim=None,
           bodies=None, no_replay=False, **kwargs):
    bm = bm or _map("256,192,1000,2,0,L|376:192,1,120\n")
    frames = _follow(bm.hit_objects[0]) if frames is None else frames
    if sim is None and not no_replay:
        sim = StdRuleset(bm, frames, lazer=True).run()
    renderer = SpriteRenderer(640, 480)
    bank = TextureBank(renderer)
    skin = SkinElements(Skin(skin_dir=skin_dir), renderer) if skin_dir else None
    bodies = bodies or SliderBodyRenderer(renderer.ctx, 640, 480)
    scene = StdScene(bm, frames, PlayfieldCamera(640, 480), renderer, bodies,
                     bank, judgments=sim, skin_elems=skin, slider_merge=merge,
                     draw_cursor=False, draw_approach_circles=False,
                     draw_combo_numbers=False, draw_judgment_popups=False,
                     draw_hit_lighting=False, draw_follow_points=False,
                     combo_colors=((.1, .75, 1),), **kwargs)
    return scene, renderer


def _layers():
    beneath = [Sprite(32, 32, 50, 50, color=(1, 0, 0, 1))]
    follow = [Sprite(32, 32, 44, 44, color=(0, 1, 0, 1), additive=True)]
    ball = [Sprite(32, 32, 20, 20, color=(0, 0, 1, 1))]
    return beneath, follow, ball


def test_legacy_ball_disappears_while_follow_and_parent_continue():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(root / "sliderb.png")
        Image.new("RGBA", (256, 256), (0, 255, 0, 255)).save(root / "sliderfollowcircle.png")
        scene, renderer = _scene(skin_dir=root)
        try:
            obj = scene.objects[0]
            end = obj.get_end_time()
            captured = []
            original = renderer.draw
            def spy(sprites):
                captured.extend(sprites)
                original(sprites)
            renderer.draw = spy
            scene.frame_rgb(end + 60)
            assert not any(s.texture_key.startswith("sk_sliderb") and s.color[3] > 0
                           for s in captured)
            assert any(s.texture_key == "sk_sliderfollowcircle" and s.color[3] > 0
                       for s in captured)
            assert scene._slider_visual(obj).parent_alpha(end + 60) == .75
        finally:
            renderer.release()


def test_actual_gl_ball_child_and_parent_opacity_compose_once():
    for variant, offset, intrinsic in (("argon", 25, 1/32), ("default", 56.25, .25)):
        with tempfile.TemporaryDirectory() as folder:
            scene, renderer = _scene(skin_dir=Path(folder) if variant == "default" else None)
            try:
                obj = scene.objects[0]
                end = obj.get_end_time()
                time = end + offset
                scene._advance(time)
                ball = scene._slider_ball_sprites(obj, time, (1, 0, 0))
                assert ball and all(abs(s.color[3] - intrinsic) < 1e-9 for s in ball)
                actual = []
                original = renderer.draw
                def spy(sprites):
                    actual.extend(sprites)
                    original(sprites)
                renderer.draw = spy
                # The procedural disc has existing texture shading. Compare
                # the same raster/position at full opacity, not an assumed
                # solid-red texture; the alpha multiplication is independent.
                if variant == "default":
                    renderer.begin((0, 0, 0))
                    original([dataclasses.replace(s, color=(*s.color[:3], 1)) for s in ball])
                    x, y = scene.cam.to_screen(*obj.get_stacked_position_at(end, scene.diff))
                    full_red = int(renderer.read_rgb()[round(y), round(x), 0])
                renderer.begin((0, 0, 0))
                parent_alpha = scene._slider_visual(obj).parent_alpha(time)
                draw_slider_layers(renderer, [], [], ball, parent_alpha)
                assert all(abs(s.color[3] - intrinsic * (1-offset/240)) < 1e-9 for s in actual)
                pixels = renderer.read_rgb()
                assert pixels.max() > 0
                if variant == "default":
                    x, y = scene.cam.to_screen(*obj.get_stacked_position_at(end, scene.diff))
                    assert abs(int(pixels[round(y), round(x), 0]) - full_red*intrinsic*parent_alpha) <= 1
                renderer.begin((0, 0, 0))
                draw_slider_layers(renderer, [], [], scene._slider_ball_sprites(obj, end+120, (1, 0, 0)), .5)
                assert renderer.read_rgb().max() == 0
            finally:
                renderer.release()


def test_actual_gl_legacy_pixels_vanish_without_cutting_follow():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(root / "sliderb.png")
        Image.new("RGBA", (256, 256), (0, 255, 0, 255)).save(root / "sliderfollowcircle.png")
        scene, renderer = _scene(skin_dir=root)
        try:
            obj = scene.objects[0]
            end = obj.get_end_time()
            for time in (end-1, end, end+60):
                scene._advance(time)
                x, y = scene.cam.to_screen(*obj.get_stacked_position_at(min(time, end), scene.diff))
                follow = scene._follow_sprites(obj, time, x, y, (1, 1, 1), 400)
                ball = scene._slider_ball_sprites(obj, time, (1, 1, 1))
                renderer.begin((0, 0, 0))
                draw_slider_layers(renderer, [], follow, ball, scene._slider_visual(obj).parent_alpha(time))
                pixels = renderer.read_rgb()
                assert (pixels[..., 0].max() > 0) == (time < end)
                assert pixels[..., 1].max() > 0
        finally:
            renderer.release()


def test_ball_tracking_detachment_reacquisition_and_endpoint_stay_unchanged():
    bm = _map("256,192,1000,2,0,L|376:192,1,120\n")
    obj = bm.hit_objects[0]
    frames = _frames([(1000, 256, 192, 4)] + _ball_frames(obj, 1010, 1250) +
                     [(1260, 0, 0, 0)] + _ball_frames(obj, 1400, 1900))
    scene, renderer = _scene(bm, frames)
    try:
        for time, alpha in ((1200, 1), (1300, .35), (1450, 1)):
            scene.frame_rgb(time)
            ball = scene._slider_ball_sprites(obj, time, (1, 0, 0))
            assert all(s.color[3] == alpha for s in ball)
            position = scene.cam.to_screen(*obj.get_stacked_position_at(time, scene.diff))
            assert all((s.x, s.y) == position for s in ball)
        end = obj.get_end_time()
        scene.frame_rgb(end+25)
        ball = scene._slider_ball_sprites(obj, end+25, (1, 0, 0))
        assert all((s.x, s.y) == scene.cam.to_screen(*obj.get_stacked_position_at(end, scene.diff)) for s in ball)
        assert scene._slider_ball_sprites(obj, end+50, (1, 0, 0)) == []
        assert scene._slider_ball_sprites(obj, end+120, (1, 0, 0)) == []
    finally:
        renderer.release()


def test_scene_delayed_parent_keeps_ball_until_notification_not_nominal_end():
    bm = _map("256,192,1000,2,0,L|266:192,1,10\n")
    obj = bm.hit_objects[0]
    frames = _frames([(1120, 256, 192, 4), (1300, 266, 192, 4)])
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(root / "sliderb.png")
        scene, renderer = _scene(bm, frames, skin_dir=root)
        try:
            state = scene._slider_visual(obj)
            assert state.parent.time_ms == 1120 and state.parent.transform_time_ms == 1050
            for time, visible in ((1050, True), (1119, True), (1120, False), (1200, False)):
                scene.frame_rgb(time)
                ball = scene._slider_ball_sprites(obj, time, (1, 1, 1))
                assert bool(ball) == visible
            # The pure child query may be repeated/rewound without changing history.
            assert scene._slider_ball_sprites(obj, 1119, (1, 1, 1))
            assert not scene._slider_ball_sprites(obj, 1120, (1, 1, 1))
        finally:
            renderer.release()


def test_ball_missing_and_no_replay_fallback_provenance_in_real_scene():
    for no_replay, frames in ((True, []), (False, _frames([(1000, 256, 192, 4)])), (False, [])):
        scene, renderer = _scene(frames=frames, no_replay=no_replay)
        try:
            obj = scene.objects[0]
            state = scene._slider_visual(obj)
            timeline = scene._verdict(obj).slider_visuals if not no_replay else None
            snapshot = dataclasses.asdict(timeline) if timeline is not None else None
            assert state.parent is None
            expected = "approximate_debug_no_replay" if no_replay else "approximate_nominal_retirement"
            assert state.ball.provenance == expected
            scene.frame_rgb(obj.get_end_time()+25)
            assert scene._slider_ball_sprites(obj, obj.get_end_time()+25, (1, 0, 0))
            scene.frame_rgb(obj.get_end_time()+50)
            assert not scene._slider_ball_sprites(obj, obj.get_end_time()+50, (1, 0, 0))
            if timeline is not None:
                assert dataclasses.asdict(timeline) == snapshot
        finally:
            renderer.release()


def test_blank_first_legacy_ball_animation_never_falls_back_to_disc():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "skin.ini").write_text("[General]\nAnimationFramerate: 10\n")
        Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(root / "sliderb0@2x.png")
        Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(root / "sliderb1.png")
        scene, renderer = _scene(skin_dir=root)
        try:
            obj = scene.objects[0]
            assert ball_variant(scene.skin) == "legacy"
            for time, key, visible in ((1200, "sk_sliderb", False), (1300, "sk_sliderb_f1", True)):
                scene._advance(time)
                ball = scene._slider_ball_sprites(obj, time, (1, 1, 1))
                assert len(ball) == 1 and ball[0].texture_key == key
                assert ball[0].w == 128 * scene.circle_k
                renderer.begin((0, 0, 0))
                renderer.draw(ball)
                assert (renderer.read_rgb().max() > 0) == visible
            assert not scene._slider_ball_sprites(obj, obj.get_end_time(), (1, 1, 1))
        finally:
            renderer.release()


def test_ball_end_fade_ignores_spans_head_result_snaking_merge_hidden_traceable():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (128, 128), (255, 0, 0, 255)).save(root / "sliderb.png")
        # Real ruleset notifications, including partial loss, short catch-up,
        # missed heads and 1–4 spans; independently vary all rendering flags.
        for spans, length, pattern in ((1, 10, "late"), (2, 120, "tracked"),
                                       (3, 120, "missed_head"), (4, 250, "detached")):
            bm = _map(f"256,192,1000,2,0,L|506:192,{spans},{length}\n")
            obj = bm.hit_objects[0]
            end = obj.get_end_time()
            if pattern == "late":
                frames = _frames([(1120, 256, 192, 4), (1400, 266, 192, 4)])
            elif pattern == "missed_head":
                frames = _frames([(900, 0, 0, 4)] + _ball_frames(obj, 1010, end+300))
            elif pattern == "detached":
                frames = _frames([(1000, 256, 192, 4)] + _ball_frames(obj, 1010, end-100) +
                                 [(end-90, 0, 0, 4), (end+300, 0, 0, 0)])
            else:
                frames = _follow(obj)
            sim = StdRuleset(bm, frames, lazer=True).run()
            for merge, snake, mods, traceable in ((False, False, 0, False), (True, True, 0, False),
                                                 (False, True, 8, False), (True, False, 0, True)):
                scene, renderer = _scene(bm, frames, sim=sim, skin_dir=root, merge=merge,
                                         snaking_out=snake, mods=mods, traceable=traceable)
                try:
                    state = scene._slider_visual(obj)
                    assert state.parent is not None
                    notification = state.parent.time_ms
                    scene.frame_rgb(notification - 1e-5)
                    assert scene._slider_ball_sprites(obj, notification-1e-5, (1, 1, 1))
                    paths = tuple(scene._slider_paths[id(obj)])
                    marks = repr(scene._slider_marks[id(obj)])
                    scene.frame_rgb(notification)
                    assert not scene._slider_ball_sprites(obj, notification, (1, 1, 1))
                    assert tuple(scene._slider_paths[id(obj)]) == paths
                    assert repr(scene._slider_marks[id(obj)]) == marks
                    scene.frame_rgb(notification+120)
                    assert not scene._slider_ball_sprites(obj, notification+120, (1, 1, 1))
                finally:
                    renderer.release()


def test_gl_additive_follow_below_normal_ball_actual_pixels():
    renderer = SpriteRenderer(64, 64)
    try:
        renderer.begin((0, 0, 0))
        draw_slider_layers(renderer, *_layers())
        actual = renderer.read_rgb()
        assert tuple(actual[32, 32]) == (0, 0, 255)
        assert tuple(actual[32, 48]) == (255, 255, 0)
        # Prove the fixture detects the old batch reordering in real pixels.
        renderer.begin((0, 0, 0))
        renderer.draw(sum(_layers(), []))
        assert tuple(renderer.read_rgb()[32, 32]) == (0, 255, 255)
    finally:
        renderer.release()


def test_actual_metal_draw_method_records_additive_then_ball_in_order():
    # Linux cannot import the native dylib. Execute the actual checked-in
    # draw method with its normal globals and record-only object instead.
    path = Path(__file__).resolve().parents[1] / "osu_std_renderer/render/metal/renderer.py"
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "MetalSpriteRenderer")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "draw")
    namespace = dict(np=np, perf=perf, plan_texture_batches=plan_texture_batches, _UNITS=16)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    recorder = SimpleNamespace(post_xform=None, _tex={}, _white=123, _scene=[])
    recorder.draw = lambda sprites: namespace["draw"](recorder, sprites)
    draw_slider_layers(recorder, *_layers())
    assert [op[3] for op in recorder._scene] == [False, True, False]
    assert [tuple(op[1][0, 5:8]) for op in recorder._scene] == [(1, 0, 0), (0, 1, 0), (0, 0, 1)]


def test_actual_metal_draw_method_ball_fades_and_parent_composition():
    from osu_std_renderer.render.slider_visuals import BallAnimation
    from osu_std_renderer.ruleset.slider_visual import SliderVisualEvent
    path = Path(__file__).resolve().parents[1] / "osu_std_renderer/render/metal/renderer.py"
    cls = next(n for n in ast.parse(path.read_text()).body
               if isinstance(n, ast.ClassDef) and n.name == "MetalSpriteRenderer")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
    namespace = dict(np=np, perf=perf, plan_texture_batches=plan_texture_batches, _UNITS=16)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    recorder = SimpleNamespace(post_xform=None, _tex={"ball": 7, "follow": 8}, _white=123, _scene=[])
    recorder.draw = lambda sprites: namespace["draw"](recorder, sprites)
    parent = SliderVisualEvent(2000, 0, "slider_judged", "source_model", transform_time_ms=2000)
    animation = BallAnimation(1000, 2000, parent)
    for variant, offset, expected in (("argon", 25, 1/32), ("default", 56.25, .25), ("legacy", 0, 0)):
        recorder._scene.clear()
        alpha = animation.at(2000+offset, variant)
        ball = [Sprite(32, 32, 20, 20, "ball", color=(0, 0, 1, alpha))] if alpha > 0 else []
        follow = [Sprite(32, 32, 40, 40, "follow", additive=True)]
        draw_slider_layers(recorder, [], follow, ball, 1-offset/240)
        ops = recorder._scene
        assert ops[0][3] is True  # Independent follow continues first.
        assert len(ops) == (2 if expected else 1)
        if expected:
            assert ops[1][2] == [7] and ops[1][3] is False
            assert abs(float(ops[1][1][0, 8]) - expected*(1-offset/240)) < 1e-7


def test_native_metal_follow_order_when_available():
    if sys.platform != "darwin":
        return  # explicitly untested native path on this Linux host
    from osu_std_renderer.render.metal.renderer import MetalSpriteRenderer
    renderer = MetalSpriteRenderer(64, 64)
    try:
        renderer.begin((0, 0, 0))
        draw_slider_layers(renderer, *_layers())
        assert tuple(renderer.read_rgb()[32, 32]) == (0, 0, 255)
    finally:
        renderer.release()


def test_scene_argon_follow_does_not_brighten_opaque_ball_pixels():
    bm = _map("256,192,1000,2,0,L|376:192,1,120\n")
    frames = _follow(bm.hit_objects[0])
    images = []
    for enabled in (True, False):
        sim = StdRuleset(bm, frames, lazer=True).run()
        verdict = sim.verdict_for(bm.hit_objects[0])
        if not enabled:
            verdict.slider_visuals = dataclasses.replace(
                verdict.slider_visuals, events=tuple(e for e in verdict.slider_visuals.events
                if not e.kind.startswith("tracking_published_")))
        scene, renderer = _scene(bm, frames, sim=sim)
        try:
            images.append(scene.frame_rgb(1350).copy())
            x, y = scene.cam.to_screen(*bm.hit_objects[0].get_stacked_position_at(1350, bm.diff))
        finally:
            renderer.release()
    x, y = round(x), round(y)
    assert np.array_equal(images[0][y-4:y+5, x-4:x+5], images[1][y-4:y+5, x-4:x+5])
    assert np.abs(images[0].astype(int) - images[1]).sum() > 10000


def test_missing_custom_follow_uses_default_orange_not_argon_or_nothing():
    with tempfile.TemporaryDirectory() as folder:
        scene, renderer = _scene(skin_dir=Path(folder))
        try:
            scene._advance(1350)
            obj = scene.objects[0]
            x, y = scene.cam.to_screen(*obj.get_stacked_position_at(1350, scene.diff))
            sprites = scene._follow_sprites(obj, 1350, x, y, (.1, .75, 1), 400)
            assert sprites[0].texture_key == "default_follow" and sprites[0].additive
            assert sprites[0].color[:3] == (1, 165 / 255, 0)
            assert sprites[0].w == 2 * scene.radius_px * 2.4
            renderer.begin((0, 0, 0))
            renderer.draw(sprites)
            rgb = renderer.read_rgb()
            assert rgb[..., 0].max() > rgb[..., 1].max() > rgb[..., 2].max()
        finally:
            renderer.release()


def test_blank_legacy_asset_suppresses_fallback_and_native_frame_size_is_used():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "skin.ini").write_text("[General]\nAnimationFramerate: 10\n")
        Image.new("RGBA", (256, 128), (0, 0, 0, 0)).save(root / "sliderfollowcircle-0.png")
        Image.new("RGBA", (128, 256), (255, 255, 255, 255)).save(root / "sliderfollowcircle-1.png")
        scene, renderer = _scene(skin_dir=root)
        try:
            scene._advance(1400)
            obj = scene.objects[0]
            # Spawn is 400: age 800 -> frame 0, age 900 -> frame 1.
            # Both precede the tick pulse at 1500, isolating native sizing.
            first = scene._follow_sprites(obj, 1200, 200, 200, (1, 0, 0), 400)
            second = scene._follow_sprites(obj, 1300, 200, 200, (1, 0, 0), 400)
            assert first[0].texture_key == "sk_sliderfollowcircle"
            assert second[0].texture_key == "sk_sliderfollowcircle_f1"
            close = lambda a, b: abs(a - b) < 1e-8
            assert close(first[0].w, 256 * scene.circle_k)
            assert close(first[0].h, 128 * scene.circle_k)
            assert close(second[0].w, 128 * scene.circle_k)
            renderer.begin((0, 0, 0))
            renderer.draw(first)
            assert renderer.read_rgb().max() == 0
            renderer.draw(second)
            assert renderer.read_rgb().max() == 255
        finally:
            renderer.release()


def test_argon_follow_gradient_border_fill_and_base_container_size():
    rgba = bake_argon_follow(256)
    assert rgba[20, 128, 0] > rgba[235, 128, 0]
    assert rgba[20, 128, 0] < rgba[128, 252, 0]  # fill weaker than border
    assert 55 < rgba[128, 128, 0] < 75
    scene, renderer = _scene()
    try:
        scene._advance(1350)
        sprites = scene._follow_sprites(scene.objects[0], 1350, 200, 200, (.1, .75, 1), 400)
        assert sprites[0].w == 2 * scene.radius_px * 2.4
        assert sprites[0].color == (.1, .75, 1, 1)
    finally:
        renderer.release()


class Bodies:
    def __init__(self):
        self.ranges = []
        self.alphas = []
    def build_body(self, points, radius, style, snake):
        self.ranges.append(snake)
        return None
    def build_merged(self, items, radius, style):
        self.ranges.extend(snake for points, snake in items)
        return None
    def draw_body(self, body, fbo, alpha=1):
        self.alphas.append(alpha)


def test_scene_head_miss_and_reconciliation_cannot_snake_out_in_either_body_mode():
    bm = _map("256,192,1000,2,0,L|376:192,1,120\n")
    obj = bm.hit_objects[0]
    frames = _frames([(900, 0, 0, 4)] + [(t, *obj.position_at(t), 4) for t in range(1000, 1801, 10)])
    meta = dataclasses.replace(_meta(1, 0, 0, 0), game_version=30000017)
    for merge in (False, True):
        for reconcile in (False, True):
            sim = StdRuleset(bm, frames, meta, reconcile=reconcile).run()
            assert sim.verdict_for(obj).kind is (JudgmentKind.HIT300 if reconcile else JudgmentKind.MISS)
            spy = Bodies()
            scene, renderer = _scene(bm, frames, sim=sim, merge=merge, bodies=spy)
            try:
                scene.frame_rgb(1500)
                assert spy.ranges == [(0, 1)]
                assert not scene._slider_visual(obj).head_hit_at(1500)
            finally:
                renderer.release()


def test_tail_fixed_endpoint_parity_entrance_and_argon_empty_tail():
    for spans in (1, 2, 3, 4):
        bm = _map(f"256,192,1000,2,0,L|376:192,{spans},120\n")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new("RGBA", (128, 128), (255, 255, 255, 255)).save(root / "sliderendcircle.png")
            scene, renderer = _scene(bm, skin_dir=root)
            try:
                time = 700 if spans == 1 else 1000 + (spans - 2) * 600 + 100
                scene._advance(time)
                calls = []
                renderer.draw = lambda sprites: calls.extend(sprites)
                scene._draw_slider(bm.hit_objects[0], time, [])
                tails = [s for s in calls if s.texture_key == "sk_sliderendcircle"]
                assert tails and tails[0].color[3] > 0
                expected = scene.cam.to_screen(*bm.hit_objects[0].get_stacked_position_at(
                    bm.hit_objects[0].get_end_time(), bm.diff))
                assert (tails[0].x, tails[0].y) == expected
            finally:
                renderer.release()
    scene, renderer = _scene()
    try:
        scene._advance(700)
        calls = []
        scene._plain_circle_sprites = lambda *args, **kwargs: calls.append(kwargs.get("role")) or []
        scene._draw_slider(scene.objects[0], 700, [])
        assert "slider_end" not in calls
    finally:
        renderer.release()


def test_first_arrow_no_premature_tip_and_successful_repeat_freezes_seek_safely():
    bm = _map("256,192,1000,2,0,L|376:192,3,120\n")
    scene, renderer = _scene(bm)
    try:
        scene._advance(1700)
        obj = scene.objects[0]
        arrows = scene._slider_marks[id(obj)][1]
        pts = scene._slider_paths[id(obj)]
        def at(time, tail=True):
            return scene._arrow_sprites(time, arrows, tail, 400, 400, pts, .5, None, obj)
        assert at(599) == []  # first marker's preempt/3 delay
        first = at(675)[0]
        assert (first.x, first.y) == pts[-1]
        assert not at(900, False)  # later repeat index 1 starts at slider start
        frozen, later, rewind = at(2250, False)[0], at(2400, False)[0], at(2250, False)[0]
        assert (frozen.x, frozen.y, frozen.rotation) == (later.x, later.y, later.rotation)
        assert (frozen.x, frozen.y) == pts[0]  # body start is retracting meanwhile
        assert frozen == rewind
    finally:
        renderer.release()


def test_scene_late_head_gate_flags_and_parent_body_fade_in_both_modes():
    bm = _map("256,192,1000,2,0,L|376:192,1,120\n")
    obj = bm.hit_objects[0]
    frames = _frames([(1100, 256, 192, 4)] + _ball_frames(obj, 1110, 1800))
    for merge in (False, True):
        for snaking_out in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                spy = Bodies()
                scene, renderer = _scene(bm, frames, skin_dir=Path(folder),
                                         merge=merge, bodies=spy, snaking_out=snaking_out)
                try:
                    scene.frame_rgb(1075)
                    assert spy.ranges[-1] == (0, 1)
                    scene.frame_rgb(1125)
                    expected = (125 / 600, 1) if snaking_out else (0, 1)
                    assert spy.ranges[-1] == expected
                    scene.frame_rgb(1620)
                    expected_alpha = (1 - 20 / 240) * (.5 if snaking_out else 1)
                    assert abs(spy.alphas[-1] - expected_alpha) < 1e-9
                finally:
                    renderer.release()


def test_scene_no_replay_perfect_fallback_and_truncated_follow_not_invented():
    for no_replay, frames in ((True, []), (False, _frames([(1000, 256, 192, 4)]))):
        scene, renderer = _scene(frames=frames, no_replay=no_replay)
        try:
            scene.frame_rgb(1300)
            state = scene._slider_visual(scene.objects[0])
            assert state.head_hit_at(1300)
            assert state.follow.at(1300).alpha == 1
            if not no_replay:
                assert state.parent is None  # EOF cannot establish remaining parts/parent
            scene.frame_rgb(1675)
            assert scene._active  # retained through parent/follow retirement
            assert state.follow.at(1675).alpha == (1 / 32 if no_replay else 1)
        finally:
            renderer.release()


def test_legacy_repeat_circle_own_entrance_fade_and_traceable_hide():
    bm = _map("256,192,1000,2,0,L|376:192,3,120\n")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (128, 128), (255, 255, 255, 255)).save(root / "sliderendcircle.png")
        scene, renderer = _scene(bm, skin_dir=root)
        try:
            scene._advance(1700)
            obj = scene.objects[0]
            pts = scene._slider_paths[id(obj)]
            arrows = scene._slider_marks[id(obj)][1]
            assert not scene._repeat_circle_sprites(obj, 599, arrows, pts, (1, 0, 0))
            first = scene._repeat_circle_sprites(obj, 800, arrows, pts, (1, 0, 0))
            assert len(first) == 1 and first[0].color[3] == .5
            both = scene._repeat_circle_sprites(obj, 1000, arrows, pts, (1, 0, 0))
            assert len(both) == 2 and all(s.color[3] == 1 for s in both)
            faded = scene._repeat_circle_sprites(obj, 1750, arrows, pts, (1, 0, 0))
            assert abs(faded[0].color[3] - .25) < 1e-9
            scene.traceable = True
            calls = []
            scene._plain_circle_sprites = lambda *args, **kw: calls.append(kw.get("role")) or []
            scene._draw_slider(obj, 1750, [])
            assert "slider_end" not in calls
        finally:
            renderer.release()


def test_hidden_circles_vanish_but_pending_reverse_arrow_and_follow_remain():
    bm = _map("256,192,1000,2,0,L|376:192,3,120\n")
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (128, 128), (255, 255, 255, 255)).save(root / "sliderendcircle.png")
        scene, renderer = _scene(bm, skin_dir=root, mods=8)
        try:
            scene._advance(1300)
            obj = scene.objects[0]
            pts = scene._slider_paths[id(obj)]
            arrows = scene._slider_marks[id(obj)][1]
            circles = scene._repeat_circle_sprites(obj, 1300, arrows, pts, (1, 0, 0))
            assert not circles
            assert scene._arrow_sprites(1300, arrows, True, 400, 400, pts, 1, None, obj)
            assert not scene._arrow_sprites(1650, arrows, True, 400, 400, pts, 1, None, obj)
            assert scene._follow_sprites(obj, 1300, 256, 192, (1, 0, 0), 400)
            scene.frame_rgb(1300)  # exercise the complete modded GL passes
        finally:
            renderer.release()


def test_sub_path_curves_overlap_tiny_and_ball_endpoint_coordinates():
    objects = ("B|300:100|400:280|450:192,1,250", "L|456:192|256:192|276:192,1,420",
               "L|256.01:192,1,0.01")
    for suffix in objects:
        bm = _map(f"256,192,1000,2,0,{suffix}\n")
        slider = bm.hit_objects[0]
        points = slider.multi_curve.path
        for begin, end in ((0, .5), (.2, .8), (.5, .5), (0, 1)):
            clipped = sub_path(points, begin, end)
            assert clipped and all(math.isfinite(v) for point in clipped for v in point)
            expected_start = slider.multi_curve.position_at(begin)
            expected_end = slider.multi_curve.position_at(end)
            assert math.dist(clipped[0], expected_start) < 1e-6
            assert math.dist(clipped[-1], expected_end) < 1e-6
        if slider.part_len > 0:
            ball = slider.position_at(1000 + .5 * slider.part_len)
            assert math.dist(ball, sub_path(points, 0, .5)[-1]) < 1e-6
    # Parser intentionally omits zero-length sliders. Audit the existing
    # geometry function directly for degenerate/duplicate polylines.
    for points in ([(256, 192)], [(256, 192), (256, 192)], []):
        assert sub_path(points, .2, .8) == points[:1]
