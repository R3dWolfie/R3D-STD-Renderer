"""Argon league (skinless) gameplay port — ppy/osu master Argon* pieces.

Covers the procedural bakes (geometry/colour), the ArgonJudgementPiece text
selection + colours + transforms, the ArgonSliderBody border sizing, and the
LEAGUE-INTEGRITY split at the sprite level: skinless draws draw the Argon
pieces; a custom skin never touches them (custom-skin renders unchanged).
All pure CPU / numpy — no GL context.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from osu_std_renderer.render import textures as T
from osu_std_renderer.render.scene import (
    ARGON_BALL_DIAM_FRAC, ARGON_GRAD_FRAC, ARGON_JUDGE_COLOR, ARGON_JUDGE_TEXT,
    ARGON_OUTER_GRAD_R, ARGON_SLIDER_BORDER_WIDTH, StdScene,
    argon_judgment_transform,
)
from osu_std_renderer.render.slider_body import border_portion
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.ruleset import JudgmentKind
from osu_std_renderer.skin.skin import Skin


# --- texture bakes: geometry + colour -----------------------------------------

def test_argon_circle_radial_bands():
    """ArgonMainCirclePiece: dark centre (Darken 4 ≈0.2), a bright saturated
    band, a dark gap, grayscale (accent-tintable), opaque centre."""
    rgba = T.bake_argon_circle(320)
    assert rgba.shape == (320, 320, 4) and rgba.dtype == np.uint8
    c = 160
    R = 320 / 2.0 - 2.0
    grey = rgba[..., 0].astype(float)
    # grayscale (r == g == b everywhere)
    assert np.array_equal(rgba[..., 0], rgba[..., 1])
    assert np.array_equal(rgba[..., 1], rgba[..., 2])
    centre = grey[c, c]
    bright = grey[c, int(c + 0.77 * R)]           # outer gradient band
    gap = grey[c, int(c + 0.90 * R)]              # dark gap (under the border)
    assert centre < 80.0                          # dark centre
    assert bright > 180.0                         # saturated band
    assert gap < 80.0                             # dark gap before the border
    # opaque centre, transparent corner
    assert rgba[c, c, 3] == 255
    assert rgba[0, 0, 3] == 0
    # lazer vertical gradient: brighter at the top of the bright band
    top = grey[int(c - 0.77 * R), c]
    bot = grey[int(c + 0.77 * R), c]
    assert top > bot + 8.0


def test_argon_border_is_white_ring():
    rgba = T.bake_argon_border(320)
    assert (rgba[..., :3] == 255).all()           # white (untinted)
    c = 160
    assert rgba[c, c, 3] == 0                      # hollow centre
    assert rgba[c, 312, 3] > 150                   # opaque near the rim


def test_argon_ball_gradient_and_chevron():
    """ArgonSliderBall: vertical accent gradient (bright top) + a dark '>'."""
    rgba = T.bake_argon_ball(256)
    grey = rgba[..., 0].astype(float)
    c = 128
    left = 83                                      # column left of the chevron
    # vertical accent gradient: top brighter than bottom (off the chevron)
    assert grey[64, left] > grey[192, left] + 15.0
    # a dark '>' chevron stroke exists in the centre box (accent.Darken 4)
    assert grey[110:146, 120:166].min() < 90.0


def test_argon_reverse_is_white_pill_with_dark_chevron():
    rgba = T.bake_argon_reverse(256)
    c = 128
    # a pill point right of the chevron (still inside the 40×20 pill) is
    # bright + opaque
    px = rgba[c, 160]
    assert px[3] > 150 and px[0] > 180
    # corners transparent
    assert rgba[4, 4, 3] == 0
    # a dark '>>' chevron stroke exists in the centre box (accent.Darken 4)
    grey = rgba[..., 0].astype(float)
    assert grey[114:142, 118:150].min() < 120.0


def test_argon_cursor_is_pink_ring():
    """ArgonCursor: pink→dark-red ring — coloured, not grayscale."""
    rgba = T.bake_argon_cursor(192)
    c = 96
    R = 192 / 2.0 - 2.0
    px = rgba[c, int(c + 0.92 * R)]               # in the ring band
    assert px[3] > 100                            # visible
    assert int(px[0]) > int(px[1]) and int(px[0]) > int(px[2])   # pinkish (R>G,B)


def test_argon_tick_is_hollow_ring():
    rgba = T.bake_argon_tick(96)
    c = 48
    assert rgba[c, c, 3] == 0                      # hollow (ScorePoint fill α=0)
    assert rgba[c, 88, 3] > 150                    # opaque ring band


def test_argon_follow_has_ring_and_faint_fill():
    rgba = T.bake_argon_follow(256)
    grey = rgba[..., 0].astype(float)
    c = 128
    assert 30.0 < grey[c, c] < 140.0              # faint interior (Alpha 0.3)
    assert grey[c, 252] > 180.0                   # bright ring


# --- ArgonJudgementPiece: text + colour + transforms --------------------------

def test_judgment_text_selection():
    assert ARGON_JUDGE_TEXT[JudgmentKind.HIT300] == "GREAT"
    assert ARGON_JUDGE_TEXT[JudgmentKind.HIT100] == "OK"
    assert ARGON_JUDGE_TEXT[JudgmentKind.HIT50] == "MEH"
    assert ARGON_JUDGE_TEXT[JudgmentKind.MISS] == "MISS"


def test_judgment_colours_match_osucolour_forhitresult():
    def hexrgb(h):
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    assert ARGON_JUDGE_COLOR[JudgmentKind.HIT300] == hexrgb("66ccff")  # Blue
    assert ARGON_JUDGE_COLOR[JudgmentKind.HIT100] == hexrgb("88b300")  # Green
    assert ARGON_JUDGE_COLOR[JudgmentKind.HIT50] == hexrgb("ffcc22")   # Yellow
    assert ARGON_JUDGE_COLOR[JudgmentKind.MISS] == hexrgb("ed1121")    # Red


def test_judgment_transform_hit_fades_and_grows():
    # fades in over 300, whole piece fades out by 800, text scales 1→~1.2
    a0, s0, dy0, r0 = argon_judgment_transform(JudgmentKind.HIT300, 0.0)
    assert a0 == 0.0 and dy0 == 0.0 and r0 == 0.0
    a1, s1, _, _ = argon_judgment_transform(JudgmentKind.HIT300, 150.0)
    assert a1 > 0.0 and s1 > 1.0
    aL, sL, _, _ = argon_judgment_transform(JudgmentKind.HIT300, 799.0)
    assert 0.0 < aL < 0.05                          # nearly faded out
    assert 1.15 < sL <= 1.2
    assert argon_judgment_transform(JudgmentKind.HIT300, 800.0) is None


def test_judgment_transform_miss_drifts_down_and_rotates():
    a1, s1, dy1, r1 = argon_judgment_transform(JudgmentKind.MISS, 200.0)
    a2, s2, dy2, r2 = argon_judgment_transform(JudgmentKind.MISS, 600.0)
    assert dy2 > dy1 > 0.0                          # MoveToOffset (0,100) InQuint
    assert r2 > r1 > 0.0                            # RotateTo 40° InQuint
    assert a2 < a1                                  # FadeOutFromOne
    assert argon_judgment_transform(JudgmentKind.MISS, 800.0) is None
    assert argon_judgment_transform(JudgmentKind.MISS, -1.0) is None


# --- ArgonSliderBody: border sizing + geometry --------------------------------

def test_slider_border_width_maps_to_gradient_thickness():
    # intended_thickness = GRADIENT_THICKNESS/path_radius = 0.2 of path radius
    intended = ARGON_GRAD_FRAC / ARGON_OUTER_GRAD_R
    assert abs(intended - 0.2) < 1e-9
    assert abs(border_portion(ARGON_SLIDER_BORDER_WIDTH) - 0.2) < 5e-3


def test_argon_geometry_constants():
    assert abs(ARGON_OUTER_GRAD_R - 50.0 / 58.0) < 1e-12
    assert abs(ARGON_BALL_DIAM_FRAC - 50.0 / 58.0) < 1e-12
    assert abs(ARGON_GRAD_FRAC - 10.0 / 58.0) < 1e-12


# --- league integrity: sprite-level skinless-vs-skin split --------------------

class _FakeRenderer:
    def __init__(self):
        self.uploaded = {}

    def upload_texture(self, key, rgba):
        self.uploaded[key] = rgba


class _FakeCam:
    screen_w = 1280
    screen_h = 720

    def len_to_screen(self, v):
        return float(v)

    def to_screen(self, x, y):
        return float(x), float(y)


def _bare_scene():
    """A StdScene with only the attributes the tested draw helpers touch."""
    sc = object.__new__(StdScene)
    sc.radius_px = 30.0
    sc.circle_k = 1.0
    sc.cam = _FakeCam()
    sc.bank = type("B", (), {"glyph_aspect": {ch: 0.6 for ch in
                                              "GREATOKMEHMISS"}})()
    return sc


def _skin(tmp: Path) -> SkinElements:
    from PIL import Image as _I
    for n in ("hitcircle", "hitcircleoverlay"):
        _I.new("RGBA", (128, 128), (200, 120, 40, 255)).save(tmp / f"{n}.png")
    for i in range(10):
        _I.new("RGBA", (50, 70), (255, 255, 255, 255)).save(
            tmp / f"default-{i}.png")
    return SkinElements(Skin(skin_dir=tmp), _FakeRenderer())


def test_argon_circle_sprites_keys():
    sc = _bare_scene()
    sc.skin = None
    keys = [s.texture_key for s in
            sc._argon_circle_sprites(100.0, 100.0, (1.0, 0.5, 0.2), 1.0)]
    assert keys == ["argon_circle", "argon_border"]


def test_skinless_circle_is_argon_and_skin_never_is():
    """League integrity: skinless → Argon pieces; a custom skin path emits NO
    argon_* gameplay keys (its render is untouched by this change)."""
    sc = _bare_scene()
    sc.skin = None
    keys = [s.texture_key for s in
            sc._plain_circle_sprites(100.0, 100.0, (1.0, 0.0, 0.0), 1.0)]
    assert any(k.startswith("argon_") for k in keys)
    assert "disc" not in keys and "ring" not in keys

    with tempfile.TemporaryDirectory() as tmp:
        sc.skin = _skin(Path(tmp))
        keys = [s.texture_key for s in
                sc._plain_circle_sprites(100.0, 100.0, (1.0, 0.0, 0.0), 1.0)]
        assert not any((k or "").startswith("argon_") for k in keys)


def test_argon_ball_sprites_keys_and_follow():
    sc = _bare_scene()
    sc.skin = None
    tracked = [s.texture_key for s in
               sc._argon_ball_sprites(50.0, 50.0, (0.2, 0.6, 1.0), 1.0, True)]
    assert tracked == ["argon_follow", "argon_ball", "argon_ball_ring"]
    # follow circle is additive; ball + ring straight-alpha
    sprites = sc._argon_ball_sprites(50.0, 50.0, (0.2, 0.6, 1.0), 1.0, True)
    assert sprites[0].additive and not sprites[1].additive
    # not tracking → no follow circle
    untracked = [s.texture_key for s in
                 sc._argon_ball_sprites(50.0, 50.0, (0.2, 0.6, 1.0), 0.35,
                                        False)]
    assert untracked == ["argon_ball", "argon_ball_ring"]


def test_argon_judgment_run_glyphs_additive_coloured():
    sc = _bare_scene()
    out = []
    col = ARGON_JUDGE_COLOR[JudgmentKind.HIT300]
    sc._argon_judgment_run(out, "GREAT", 200.0, 200.0, 1.0, col, 0.8, 0.0)
    assert [s.texture_key for s in out] == [f"glyph_{c}" for c in "GREAT"]
    assert all(s.additive for s in out)
    assert all(s.color[:3] == col and abs(s.color[3] - 0.8) < 1e-9
               for s in out)
    # zero alpha → nothing drawn
    out2 = []
    sc._argon_judgment_run(out2, "MISS", 0.0, 0.0, 1.0, col, 0.0, 0.0)
    assert out2 == []
