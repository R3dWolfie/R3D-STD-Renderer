"""Original PNG ModIcons: source colours, geometry, caching and GPU contracts."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageFont

from osu_std_renderer.render import perf
from osu_std_renderer.render.gl import SpriteRenderer, plan_texture_batches
from osu_std_renderer.render.hud import build_mod_pills, pack_mod_pill_rows
from osu_std_renderer.render.mod_icons import (
    ASSET_DIR, EXTENDED_ICON_WIDTH, LAZER_MOD_ICONS, LazerModIcons,
    MOD_TYPE_HEX, fit_canvas, mod_colours, rate_information,
)
from osu_std_renderer.render.textures import TextureBank
from tests.test_hud_layout import Recorder, render
from tests.test_legacy_mod_icons import hud_with_mods, mod_sprites, png


@pytest.fixture(scope="module")
def bank():
    return TextureBank(Recorder())


def test_original_asset_inventory_bytes_attribution_and_no_font_files():
    inventory = json.loads((ASSET_DIR / "inventory.json").read_text())
    expected = {f"{name}.png" for name, _ in LAZER_MOD_ICONS.values()}
    expected |= {"mod-icon.png", "mod-icon-extender.png"}
    assert {a["file"] for a in inventory["assets"]} == expected
    assert len(expected) == 50
    for asset in inventory["assets"]:
        raw = (ASSET_DIR / asset["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == asset["sha256"]
        assert inventory["source_commit"] in asset["url"]
        assert asset["url"].endswith(asset["upstream_path"])
        assert asset["modifications"] == "None; original PNG bytes."
        with Image.open(ASSET_DIR / asset["file"]) as image:
            assert image.mode in {"LA", "RGBA"}  # originals retain their alpha
    assert {p.suffix for p in ASSET_DIR.iterdir()} == {".png", ".json", ".md", ".txt"}
    notice = (ASSET_DIR / "THIRD_PARTY_NOTICES.txt").read_text()
    assert "CC BY-NC 4.0" in notice and "ppy" in notice
    assert "MIT" in notice and "https://creativecommons.org/licenses/by-nc/4.0/" in notice


@pytest.mark.parametrize("acr,name,category", [
    ("HD", "hidden", "DifficultyIncrease"), ("EZ", "easy", "DifficultyReduction"),
    ("CL", "classic", "Conversion"), ("DA", "difficulty-adjust", "Conversion"),
    ("RX", "relax", "Automation"), ("WU", "wind-up", "Fun"),
    ("WD", "wind-down", "Fun"), ("TD", "touch-device", "System"),
    ("V2", "score-v2", "System"), ("SV2", "score-v2", "System"),
])
def test_verified_icon_mapping_and_all_six_source_categories(acr, name, category):
    assert LAZER_MOD_ICONS[acr] == ("mod-" + name, category)
    assert {category for _, category in LAZER_MOD_ICONS.values()} == set(MOD_TYPE_HEX)


def test_source_colour_interpolation_is_linear_and_extender_uses_darken():
    background, foreground, extender = mod_colours("HD")
    assert background == pytest.approx((1, .4, .4))
    # Values obtained from actual compiled osu.Framework Colour4 interpolation.
    assert foreground == pytest.approx((.34919018, .119319014, .119319014), abs=1e-7)
    assert extender == pytest.approx((1/3.8, .4/3.8, .4/3.8))
    assert mod_colours("CL")[0] == pytest.approx((140/255, 102/255, 1))
    assert mod_colours("WU")[0] == pytest.approx((1, 102/255, 171/255))
    assert mod_colours("V2")[0] == pytest.approx((1, 204/255, 34/255))


@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080)])
@pytest.mark.parametrize("scale", [1, 1.25])
def test_48_unit_component_original_aspect_alpha_and_resolution_scaling(bank, tmp_path, resolution, scale):
    hud, _ = hud_with_mods(bank, tmp_path, ["HD", "CL"],
                          renderer=Recorder(*resolution), hud_scale=scale, hud_opacity=.4)
    badges = hud._measure_mod_badges()
    assert [(b.width, b.height, b.lazer) for b in badges] == [(48*scale, 48*scale, True)]*2
    sprites = mod_sprites(hud)
    assert [s.texture_key for s in sprites] == [
        "lz_mod-icon", "lz_mod-hidden", "lz_mod-icon", "lz_mod-classic"]
    bg, glyph = sprites[:2]
    unit = resolution[1]/768*scale
    assert (bg.w, bg.h) == pytest.approx((48*unit, 48*100/135*unit))
    expected = fit_canvas(bank.mod_icons.size["mod-hidden"], 48*131/135, 48*92/135)
    assert (glyph.w, glyph.h) == pytest.approx(tuple(v*unit for v in expected))
    assert (glyph.x, glyph.y) == (bg.x, bg.y)
    assert sprites[2].x-bg.x == pytest.approx(48*unit)
    assert bg.color == pytest.approx((*mod_colours("HD")[0], .4))
    assert glyph.color == pytest.approx((*mod_colours("HD")[1], .4))
    assert all(s.uv_off == (0, 0) and s.uv_scale == (1, 1) and not s.additive for s in sprites)


@pytest.mark.parametrize("acr,rate,label", [("DT", 1.3, "1.30x"), ("NC", 1.25, "1.25x"), ("HT", .9, "0.90x")])
@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080)])
def test_custom_rate_extender_retains_metadata_with_existing_r3d_text(bank, tmp_path, monkeypatch, acr, rate, label, resolution):
    hud, _ = hud_with_mods(bank, tmp_path, [acr, "CL"], rate=rate,
                          renderer=Recorder(*resolution), hud_scale=1.25)
    traced = []
    original = hud._lrun
    def trace(out, text, x, y, height, *args):
        traced.append((text, x, y, height))
        return original(out, text, x, y, height, *args)
    monkeypatch.setattr(hud, "_lrun", trace)
    sprites = mod_sprites(hud)
    badge = hud._measure_mod_badges()[0]
    assert badge.width == pytest.approx(EXTENDED_ICON_WIDTH*hud.es)
    ext = next(s for s in sprites if s.texture_key == "lz_mod-icon-extender")
    bg = next(s for s in sprites if s.texture_key == "lz_mod-icon")
    assert ext.x-bg.x == pytest.approx((116-40)*.6*hud.es*hud.lk)
    assert ext.color == pytest.approx((*mod_colours(acr)[2], hud.op))
    rates=[item for item in traced if item[0] == label]
    assert len(rates) == 1
    text, _, _, height = rates[0]
    glyphs=[s for s in sprites if (s.texture_key or '').startswith('glyph_')]
    assert [s.texture_key for s in glyphs] == ['glyph_'+c for c in label]
    x=min(s.x-s.w/2 for s in glyphs)/hud.lk
    y=(glyphs[0].y-glyphs[0].h/2)/hud.lk
    assert 0 < height <= 20.4*hud.es
    text_center = x+hud._lrun_width(label, height)/2
    assert text_center-bg.x/hud.lk == pytest.approx((122-40)*.6*hud.es)
    assert y+height/2 == pytest.approx(bg.y/hud.lk)
    assert x >= (bg.x+bg.w/2)/hud.lk-1e-6
    assert x+hud._lrun_width(label, height) <= (ext.x+ext.w/2)/hud.lk+1e-6
    # The source representation is not mutated by presentation or seeking.
    assert hud._mod_acrs[0].text.endswith("×")
    assert rate_information(hud._mod_acrs[0]) == label


def test_missing_individual_glyph_keeps_component_slot_and_readable_acronym(tmp_path):
    for name in ("mod-icon", "mod-icon-extender", "mod-classic"):
        (tmp_path / f"{name}.png").write_bytes((ASSET_DIR / f"{name}.png").read_bytes())
    icons = LazerModIcons(Recorder(), tmp_path)
    labels, sprites = [], []
    icons.draw(sprites, "HD", 10, 20, 1, .7, "",
               lambda out, *args: labels.append(args), lambda text, h: len(text)*h/2)
    assert icons.has("HD") and not icons.has("ZZ")
    assert [s.texture_key for s in sprites] == ["lz_mod-icon"]
    assert sprites[0].x == 34 and sprites[0].w == 48
    assert labels[0][0] == "HD" and labels[0][3] == pytest.approx(13.2)


def test_per_mod_skin_priority_unknown_acronym_and_no_frame_io(bank, tmp_path, monkeypatch):
    png(tmp_path, "selection-mod-hidden", (80, 30))
    hud, rec = hud_with_mods(bank, tmp_path, ["HD", "CL", "ZZ"], show_progress=False)
    initial = [(b.width, b.height, b.lazer) for b in hud._measure_mod_badges()]
    def unexpected(*args, **kwargs):
        pytest.fail("per-frame image access/upload is forbidden")
    monkeypatch.setattr(Image, "open", unexpected)
    monkeypatch.setattr(rec, "upload_texture", unexpected)
    for time in (2300, 1200, 5000, 2300):
        sprites = mod_sprites(hud)
        assert [s.texture_key for s in sprites if s.texture_key in {
            "sk_selection-mod-hidden", "lz_mod-classic", "pill"}] == [
            "sk_selection-mod-hidden", "lz_mod-classic", "pill"]
        render(hud, rec, time)
        assert [(b.width, b.height, b.lazer) for b in hud._measure_mod_badges()] == initial
    hud.s.show_mods = False
    assert not mod_sprites(hud)


def test_texture_uploads_are_original_rgba_and_setup_only_without_fonts(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("ModIcon artwork must never load fonts")
    monkeypatch.setattr(ImageFont, "truetype", unexpected)
    rec = Recorder()
    icons = LazerModIcons(rec)
    assert len(rec.uploaded) == 50
    for key, rgba in rec.uploaded.items():
        with Image.open(ASSET_DIR / (key[3:]+".png")) as image:
            assert np.array_equal(rgba, np.asarray(image.convert("RGBA")))
    before = {key: id(value) for key, value in rec.uploaded.items()}
    for _ in range(3):
        icons.draw([], "HD", 0, 0, 1, 1, "", lambda *a: None, lambda *a: 0)
    assert {key: id(value) for key, value in rec.uploaded.items()} == before


def test_source_order_suppression_and_pair_spacing_preserve_wrapping(bank, tmp_path):
    hud, _ = hud_with_mods(bank, tmp_path, ["DA", "NC", "DT", "PF", "SD", "CL", "WU", "ZZ"])
    assert [p.acr for p in hud._mod_acrs] == ["DA", "NC", "PF", "CL", "WU", "ZZ"]
    keys = [s.texture_key for s in mod_sprites(hud)]
    assert "lz_mod-double-time" not in keys and "lz_mod-sudden-death" not in keys
    assert keys.count("pill") == 1
    assert pack_mod_pill_rows([48, 48, 48, 40], 8, 144, gaps=[0, 0, 8]) == [[0, 1, 2], [3]]


def test_actual_metal_upload_path_receives_source_rgba_and_linear_sampling():
    path = Path(__file__).resolve().parents[1] / "osu_std_renderer/render/metal/renderer.py"
    cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == "MetalSpriteRenderer")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "upload_texture")
    ns = dict(np=np, mc=SimpleNamespace(MIP=1, CLAMP=2, RGBA8=3))
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), ns)
    uploaded = []
    def create(width, height, fmt, rgba, mipmaps, flags):
        uploaded.append((width, height, fmt, rgba, mipmaps, flags))
        return len(uploaded)
    metal = SimpleNamespace(core=SimpleNamespace(tex_create=create), _tex={}, _tex_shape={})
    metal.upload_texture = lambda key, rgba, **kw: ns["upload_texture"](metal, key, rgba, **kw)
    LazerModIcons(metal)
    assert len(uploaded) == 50
    for key, texture in metal._tex.items():
        width, height, fmt, rgba, mipmaps, flags = uploaded[texture-1]
        assert fmt == 3 and not mipmaps and flags == 0
        assert rgba.shape == (height, width, 4) and rgba.dtype == np.uint8
        with Image.open(ASSET_DIR / (key[3:]+".png")) as image:
            assert np.array_equal(rgba, np.asarray(image.convert("RGBA")))


def test_actual_gl_alpha_tints_and_metal_sprite_serialization():
    renderer = SpriteRenderer(768, 768)
    try:
        icons = LazerModIcons(renderer)
        sprites = []
        icons.draw(sprites, "HD", 40, 40, 2, .5, "", lambda *a: None, lambda *a: 0)
        renderer.begin((0, 0, 0))
        renderer.draw(sprites)
        pixels = renderer.read_rgb()
        # Interior backing away from the symbol; source red at half opacity.
        assert pixels[88, 52] == pytest.approx((127.5, 51, 51), abs=2)
        assert not pixels[40, 40].any()  # original transparent corner padding
        path = Path(__file__).resolve().parents[1] / "osu_std_renderer/render/metal/renderer.py"
        cls = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == "MetalSpriteRenderer")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
        ns = dict(np=np, perf=perf, plan_texture_batches=plan_texture_batches, _UNITS=16)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), ns)
        metal = SimpleNamespace(post_xform=None, _tex={s.texture_key: i+1 for i, s in enumerate(sprites)}, _white=0, _scene=[])
        ns["draw"](metal, sprites)
        op, instances, textures, additive = metal._scene[0]
        assert op == "sprites" and not additive and textures == [1, 2]
        for row, sprite in zip(instances, sprites):
            assert row[:5] == pytest.approx([sprite.x, sprite.y, sprite.w, sprite.h, 0])
            assert row[5:9] == pytest.approx(sprite.color)
            assert row[9:13] == pytest.approx([0, 0, 1, 1])
    finally:
        renderer.release()
