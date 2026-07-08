"""First real-skin support: per-element skin-vs-procedural fallback, @2x
logical sizing, digit layout, empty-texture (hit300) semantics — no GL
(SkinElements only needs upload_texture on the renderer)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from osu_std_renderer.render.skin_elements import (CIRCLE_TEX_RADIUS,
                                                   SkinElements,
                                                   circle_pixel_scale,
                                                   layout_skin_digits)
from osu_std_renderer.skin.skin import Skin


class FakeRenderer:
    def __init__(self):
        self.uploaded: dict[str, np.ndarray] = {}

    def upload_texture(self, key: str, rgba: np.ndarray) -> None:
        self.uploaded[key] = rgba


def _png(path: Path, w: int, h: int, alpha: int = 255) -> None:
    Image.new("RGBA", (w, h), (200, 120, 40, alpha)).save(path)


def _make_skin(d: Path, *, digits: bool = True,
               drop_digit: str | None = None) -> None:
    _png(d / "hitcircle.png", 128, 128)
    _png(d / "hitcircleoverlay.png", 128, 128)
    _png(d / "approachcircle@2x.png", 256, 256)      # @2x → logical 128
    _png(d / "sliderb.png", 118, 118)
    _png(d / "hit0-0.png", 200, 100)                 # dashed animation frame
    _png(d / "hit300.png", 160, 80, alpha=0)         # fully transparent
    if digits:
        for i in range(10):
            if drop_digit is not None and str(i) == drop_digit:
                continue
            _png(d / f"default-{i}.png", 50, 70)


def test_element_loading_fallback_and_2x():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _make_skin(d)
        fake = FakeRenderer()
        elems = SkinElements(Skin(skin_dir=d), fake)
        # loaded from the skin
        for name in ("hitcircle", "hitcircleoverlay", "approachcircle",
                     "sliderb", "hit0", "hit300", "digits"):
            assert elems.has(name), name
        # absent → procedural fallback (per element, not all-or-nothing)
        for name in ("cursor", "cursortrail", "cursormiddle",
                     "sliderfollowcircle", "hit50", "hit100"):
            assert not elems.has(name), name
        # @2x: logical size = pixel size / 2
        assert elems.size["approachcircle"] == (128.0, 128.0)
        assert elems.size["hitcircle"] == (128.0, 128.0)
        assert elems.size["sliderb"] == (118.0, 118.0)
        # dashed animation frame 0 resolved
        assert elems.size["hit0"] == (200.0, 100.0)
        # fully transparent texture = loaded but BLANKED (no popup)
        assert "hit300" in elems.empty
        assert "hit0" not in elems.empty
        # uploads landed under sk_ keys
        assert "sk_hitcircle" in fake.uploaded
        assert "sk_digit_7" in fake.uploaded


def test_digits_all_or_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _make_skin(d, drop_digit="7")
        elems = SkinElements(Skin(skin_dir=d), FakeRenderer())
        assert not elems.has("digits")   # 9/10 digits → procedural set
        assert elems.has("hitcircle")    # other elements unaffected


def test_circle_pixel_scale_convention():
    # 128 px @1x hitcircle must span the circle's screen diameter exactly
    radius_px = 53.4
    k = circle_pixel_scale(radius_px)
    assert abs(128.0 * k - 2.0 * radius_px) < 1e-9
    # a 256 px @2x file has logical size 128 → identical on-screen size
    logical_2x = 256.0 * 0.5
    assert abs(logical_2x * k - 2.0 * radius_px) < 1e-9
    assert CIRCLE_TEX_RADIUS == 64.0


def test_layout_skin_digits_overlap_math():
    sizes = {ch: (50.0, 70.0) for ch in "0123456789"}
    # negative overlap (the default -2) = a 2px gap between digits
    run = layout_skin_digits(123, sizes, -2.0)
    assert [r[0] for r in run] == ["1", "2", "3"]
    total = 3 * 50.0 + 2 * 2.0
    assert abs(run[0][1] - (-total / 2.0 + 25.0)) < 1e-9   # centred
    assert abs((run[1][1] - run[0][1]) - 52.0) < 1e-9      # advance 50+2
    # positive overlap pulls digits together
    run = layout_skin_digits(11, sizes, 10.0)
    assert abs((run[1][1] - run[0][1]) - 40.0) < 1e-9
    # single digit sits dead centre
    run = layout_skin_digits(5, sizes, -2.0)
    assert len(run) == 1 and abs(run[0][1]) < 1e-9
    # sizes carried through
    assert run[0][2] == 50.0 and run[0][3] == 70.0


def test_new_marker_elements_load_and_fall_back():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _make_skin(d)
        _png(d / "reversearrow.png", 100, 60)
        _png(d / "sliderscorepoint.png", 16, 16)
        _png(d / "followpoint.png", 30, 12)
        elems = SkinElements(Skin(skin_dir=d), FakeRenderer())
        for name in ("reversearrow", "sliderscorepoint", "followpoint"):
            assert elems.has(name), name
        assert elems.size["reversearrow"] == (100.0, 60.0)
        # absent in a bare skin → procedural fallback per element
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _png(d / "hitcircle.png", 128, 128)
        elems = SkinElements(Skin(skin_dir=d), FakeRenderer())
        for name in ("reversearrow", "sliderscorepoint", "followpoint"):
            assert not elems.has(name), name


def test_followpoint_frames_and_animation_cycling():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "skin.ini").write_text("Version: 2.5\nAnimationFramerate: 10\n",
                                    encoding="utf-8")
        for i in range(3):
            _png(d / f"followpoint-{i}.png", 30, 12)
        for i in range(2):
            _png(d / f"sliderb{i}.png", 118, 118)
        fake = FakeRenderer()
        elems = SkinElements(Skin(skin_dir=d), fake)
        assert elems.frame_counts["followpoint"] == 3
        assert elems.frame_counts["sliderb"] == 2
        assert "sk_followpoint" in fake.uploaded
        assert "sk_followpoint_f1" in fake.uploaded
        assert "sk_followpoint_f2" in fake.uploaded
        # AnimationFramerate 10 → 100 ms per frame, cycling
        assert elems.frame_key("followpoint", 0.0) == "sk_followpoint"
        assert elems.frame_key("followpoint", 150.0) == "sk_followpoint_f1"
        assert elems.frame_key("followpoint", 250.0) == "sk_followpoint_f2"
        assert elems.frame_key("followpoint", 320.0) == "sk_followpoint"
        assert elems.frame_key("sliderb", 150.0) == "sk_sliderb_f1"
        # single-frame / unknown elements always take the base key
        assert elems.frame_key("hitcircle", 999.0) == "sk_hitcircle"


def test_slider_circle_specialisations_most_specific():
    # skin ships sliderstartcircle+sliderendcircle → heads/tails use them,
    # overlays fall back per-asset to hitcircleoverlay
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _png(d / "hitcircle.png", 128, 128)
        _png(d / "hitcircleoverlay.png", 128, 128)
        _png(d / "sliderstartcircle.png", 128, 128)
        _png(d / "sliderendcircle.png", 128, 128)
        _png(d / "sliderendcircleoverlay.png", 128, 128)
        elems = SkinElements(Skin(skin_dir=d), FakeRenderer())
        assert elems.circle_elements("hit") == ("hitcircle",
                                                "hitcircleoverlay")
        assert elems.circle_elements("slider_head") == (
            "sliderstartcircle", "hitcircleoverlay")
        assert elems.circle_elements("slider_end") == (
            "sliderendcircle", "sliderendcircleoverlay")
    # no specialisations → heads/tails reuse hitcircle(+overlay)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _png(d / "hitcircle.png", 128, 128)
        elems = SkinElements(Skin(skin_dir=d), FakeRenderer())
        assert elems.circle_elements("slider_head") == ("hitcircle", None)
        assert elems.circle_elements("slider_end") == ("hitcircle", None)
    # nothing at all → procedural (None, None)
    with tempfile.TemporaryDirectory() as tmp:
        elems = SkinElements(Skin(skin_dir=Path(tmp)), FakeRenderer())
        assert elems.circle_elements("hit") == (None, None)
        assert elems.circle_elements("slider_end") == (None, None)


def test_skin_ini_combo_colors_flow_through_skin():
    # Combo1..N in skin.ini replace the defaults (§3.2) — the CLI feeds
    # skin_info.combo_colors straight into the scene
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "skin.ini").write_text(
            "[General]\nName: T\nVersion: 2.5\n"
            "[Colours]\nCombo1: 10,20,30\nCombo2: 40,50,60\n",
            encoding="utf-8")
        _png(d / "hitcircle.png", 128, 128)
        skin = Skin(skin_dir=d)
        assert skin.info.combo_colors == [(10, 20, 30), (40, 50, 60)]
