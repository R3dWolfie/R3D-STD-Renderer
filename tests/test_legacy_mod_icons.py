"""Legacy skin mod substitutions, shared layout, and real sprite contracts."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from osu_std_renderer.render import perf
from osu_std_renderer.render.gl import SpriteRenderer, plan_texture_batches
from osu_std_renderer.render.hud import (
    MOD_PILL_H, MOD_PILL_ROW_MAX_W, build_mod_pills,
)
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.mod_icons import GAMEPLAY_ICON_SIZE
from osu_std_renderer.render.textures import TextureBank
from osu_std_renderer.skin.mod_icons import LEGACY_MOD_TEXTURES
from osu_std_renderer.skin.skin import Skin, Source, TextureFile
from tests.test_hud_layout import Recorder, make_hud, render, assert_mod_column


@pytest.fixture(scope="module")
def bank():
    return TextureBank(Recorder())


def png(folder, stem, size=(96, 48), color=(190, 80, 30, 128)):
    folder.mkdir(exist_ok=True, parents=True)
    path = folder / f"{stem}.png"
    Image.new("RGBA", size, color).save(path)
    return path


def hud_with_mods(bank, folder, acronyms, *, rate=None, renderer=None, **settings):
    hud, recorder = make_hud(bank, folder=folder, renderer=renderer, **settings)
    hud._mod_acrs = build_mod_pills(0, acronyms, rate)
    return hud, recorder


def mod_sprites(hud):
    out = []
    hud._mod_pills(out, 2300)
    return out


def test_mapping_matches_stable_enum_names_and_keeps_unsupported_mods_safe():
    # Player.cs:801 lowercases the names in SharedEnums.cs:7-38. Acronyms
    # are cross-checked with Mania and lazer Mod*.Acronym, not filenames.
    expected = {
        "NF": "nofail", "EZ": "easy", "HD": "hidden", "HR": "hardrock",
        "SD": "suddendeath", "DT": "doubletime", "RX": "relax", "HT": "halftime",
        "NC": "nightcore", "FL": "flashlight", "AT": "autoplay", "SO": "spunout",
        "AP": "relax2", "PF": "perfect", "FI": "fadein", "RD": "random",
        "CN": "cinema", "TP": "target", "KC": "keycoop",
        **{f"{n}K": f"key{n}" for n in range(1, 10)},
    }
    assert LEGACY_MOD_TEXTURES == {a: f"selection-mod-{name}" for a, name in expected.items()}
    assert not (set(LEGACY_MOD_TEXTURES) & {"CL", "DA", "DC", "WU", "WD", "V2", "TD", "MR"})


def test_loader_preserves_source_priority_case_2x_and_authored_rgba(tmp_path):
    selected, fallback, local = (tmp_path / name for name in ("skin", "fallback", "local"))
    png(selected, "selection-mod-hidden", (30, 20))
    high = png(selected, "SeLeCtIoN-MoD-HiDdEn@2X", (180, 100), (70, 90, 180, 103))
    png(fallback, "selection-mod-hidden@2x", (600, 600))
    png(selected, "selection-mod-doubletime", (72, 40))
    png(fallback, "selection-mod-doubletime@2x", (400, 400))
    png(fallback, "selection-mod-hardrock", (81, 31))
    png(local, "selection-mod-hardrock@2x", (500, 500))
    png(local, "selection-mod-easy@2x", (140, 60))
    # None of these beatmap pixels may enter Standard's skin texture chain.
    outside = png(tmp_path / "beatmap", "selection-mod-nofail", (16, 16))
    skin = Skin(selected, fallback, local)
    skin._maps[Source.BEATMAP] = {outside.name: outside}
    recorder = Recorder()
    elements = SkinElements(skin, recorder)
    assert skin.find_texture("selection-mod-hidden").path == high
    assert skin.get_source("selection-mod-hidden") == Source.SKIN
    assert skin.get_source("selection-mod-hardrock") == Source.FALLBACK
    assert skin.get_source("selection-mod-easy") == Source.LOCAL
    assert elements.size["selection-mod-hidden"] == (90, 50)
    assert elements.size["selection-mod-doubletime"] == (72, 40)
    assert elements.size["selection-mod-hardrock"] == (81, 31)
    assert elements.size["selection-mod-easy"] == (70, 30)
    assert not elements.has("selection-mod-nofail")
    assert skin.find_texture("selection-mod-nofail", Source.BEATMAP) is None
    assert np.array_equal(recorder.uploaded["sk_selection-mod-hidden"], np.asarray(Image.open(high)))


@pytest.mark.parametrize("acronym", ["HD", "DT", "RX", "AP", "AT", "SO", "CN", "TP"])
def test_skin_art_replaces_only_its_own_pill(bank, tmp_path, acronym):
    stem = LEGACY_MOD_TEXTURES[acronym]
    png(tmp_path, stem, (120, 40))
    # An invented lazer filename must not override an unsupported mod.
    png(tmp_path, "selection-mod-cl")
    hud, _ = hud_with_mods(bank, tmp_path, [acronym, "CL"], hud_opacity=.4)
    sprites = mod_sprites(hud)
    image = next(s for s in sprites if s.texture_key == "sk_" + stem)
    assert sum(s.texture_key == "pill" for s in sprites) == 0
    assert sum(s.texture_key == "lz_mod-classic" for s in sprites) == 1
    assert sum((s.texture_key or "").startswith("sk_selection-mod-") for s in sprites) == 1
    assert image.color == (1, 1, 1, .4)
    assert image.uv_off == (0, 0) and image.uv_scale == (1, 1)
    assert image.w / image.h == pytest.approx(3)
    assert image.h / hud.lk == pytest.approx(40)  # this asset's authored height
    assert not image.additive and image.rotation == 0


def test_lazer_order_suppression_and_exclusive_source_are_preserved(bank, tmp_path, monkeypatch):
    for acronym in ("HD", "DT", "NC", "SD", "PF", "RX", "AP"):
        png(tmp_path, LEGACY_MOD_TEXTURES[acronym])
    hud, _ = hud_with_mods(bank, tmp_path, ["DA", "NC", "DT", "RX", "PF", "SD", "AP", "HD", "CL"])
    labels = []
    original = hud._lrun

    def trace(out, text, *args, **kwargs):
        labels.append(text)
        return original(out, text, *args, **kwargs)

    monkeypatch.setattr(hud, "_lrun", trace)
    sprites = mod_sprites(hud)
    assert [s.texture_key for s in sprites if (s.texture_key or "").startswith("sk_selection-mod-")] == [
        "sk_" + LEGACY_MOD_TEXTURES[a] for a in ("NC", "RX", "PF", "AP", "HD")]
    assert not any(label in {'DA','CL','NC','DT','PF','SD','RX','AP','HD'} for label in labels)
    assert [s.texture_key for s in sprites if s.texture_key in (
        "lz_mod-difficulty-adjust", "lz_mod-classic")] == [
        "lz_mod-difficulty-adjust", "lz_mod-classic"]
    assert sum(s.texture_key == "pill" for s in sprites) == 0
    hud.mods = 0x8
    hud._mod_acrs = build_mod_pills(hud.mods, ["CL"])
    assert not any((s.texture_key or "").startswith("sk_") for s in mod_sprites(hud))
    hud._mod_acrs = build_mod_pills(0x240 | 0x4020)
    assert [s.texture_key for s in mod_sprites(hud)] == [
        "sk_selection-mod-perfect", "sk_selection-mod-nightcore"]


@pytest.mark.parametrize("kind", ["missing", "wrong-name", "corrupt", "blank"])
def test_missing_unreadable_and_intentionally_blank_icons(bank, tmp_path, kind):
    if kind == "wrong-name":
        png(tmp_path, "selection-mod-hd")
    elif kind == "corrupt":
        (tmp_path / "selection-mod-hidden.png").write_bytes(b"not a PNG")
    elif kind == "blank":
        png(tmp_path, "selection-mod-hidden", color=(255, 100, 80, 0))
    hud, recorder = hud_with_mods(bank, tmp_path, ["HD"])
    sprites = mod_sprites(hud)
    if kind == "blank":
        assert hud.sk.has("selection-mod-hidden")
        assert "selection-mod-hidden" in hud.sk.empty
        assert [s.texture_key for s in sprites] == ["sk_selection-mod-hidden"]
        assert not recorder.uploaded["sk_selection-mod-hidden"][..., 3].any()
    else:
        assert not hud.sk.has("selection-mod-hidden")
        assert sum(s.texture_key == "lz_mod-hidden" for s in sprites) == 1
        assert not any(s.texture_key == "pill" for s in sprites)
    assert hud._mod_rows == 1


def test_blank_icon_keeps_drawable_grade_and_keys_but_reclaims_counter_clearance(bank, tmp_path):
    png(tmp_path, "selection-mod-hidden")
    opaque, rec = hud_with_mods(bank, tmp_path, ["HD", "CL"], show_hit_counter=True)
    render(opaque, rec, 2300)
    png(tmp_path, "selection-mod-hidden", color=(190, 80, 30, 0))
    blank, rec = hud_with_mods(bank, tmp_path, ["HD", "CL"], show_hit_counter=True)
    render(blank, rec, 2300)
    assert {n:r for n,r in blank.bounds.items() if n not in ('mods','hits','grade')} == {
        n:r for n,r in opaque.bounds.items() if n not in ('mods','hits','grade')}
    assert blank.bounds['hits'].top < opaque.bounds['hits'].top
    assert blank._mod_stack_offset() < opaque._mod_stack_offset()


@pytest.mark.parametrize("acronym,rate,label", [("DT", 1.3, "1.3×"), ("NC", 1.25, "1.25×"), ("HT", .9, "0.9×")])
def test_custom_rate_label_is_adjacent_and_measured_for_wrapping(bank, tmp_path, monkeypatch, acronym, rate, label):
    # A very wide native canvas exceeds the row cap without being shrunk.
    png(tmp_path, LEGACY_MOD_TEXTURES[acronym], (1000, 100))
    hud, _ = hud_with_mods(bank, tmp_path, [acronym, "ZZ"], rate=rate)
    labels = []
    original = hud._lrun

    def trace(out, text, x, y, h, *args, **kwargs):
        labels.append((text, x, y, h))
        return original(out, text, x, y, h, *args, **kwargs)

    monkeypatch.setattr(hud, "_lrun", trace)
    sprites = mod_sprites(hud)
    icon = next(s for s in sprites if s.texture_key == "sk_" + LEGACY_MOD_TEXTURES[acronym])
    rate_text = next(item for item in labels if item[0] == label)
    assert [item[0] for item in labels if item[0] in (label,"ZZ")] == [label,"ZZ"]
    assert icon.w / icon.h == pytest.approx(10)
    assert (icon.w, icon.h) == pytest.approx((1000*hud.lk, 100*hud.lk))
    rate_sprites=[s for s in sprites if (s.texture_key or '').startswith('glyph_')][:len(label)]
    assert min(s.x-s.w/2 for s in rate_sprites) > icon.x+icon.w/2
    assert rate_text[1] + hud._lrun_width(label, rate_text[3]) <= hud.ui_w_l - 20 + 1e-6
    assert hud._mod_rows == 2  # oversize art keeps its own row; ZZ wraps
    assert sum(s.texture_key == "pill" for s in sprites) == 1


@pytest.mark.parametrize("legacy_defaults", [False, True])
def test_no_selected_skin_uses_lazer_icons_even_with_fallback_skin_art(bank, tmp_path, legacy_defaults):
    png(tmp_path, "selection-mod-hidden")
    hud, recorder = hud_with_mods(bank, None, ["HD"], legacy_defaults=legacy_defaults)
    original = mod_sprites(hud)
    hud.sk = SkinElements(Skin(fallback_dir=tmp_path), recorder)
    assert mod_sprites(hud) == original
    assert sum(s.texture_key == "lz_mod-hidden" for s in original) == 1
    assert not any(s.texture_key == "pill" for s in original)


def test_partial_skin_uses_configured_fallback_then_local_art(bank, tmp_path):
    selected, fallback, local = (tmp_path / name for name in ("skin", "fallback", "local"))
    selected.mkdir()
    png(fallback, "selection-mod-hidden")
    png(local, "selection-mod-doubletime")
    hud, recorder = hud_with_mods(bank, selected, ["HD", "DT", "CL"])
    hud.sk = SkinElements(Skin(selected, fallback, local), recorder)
    sprites = mod_sprites(hud)
    assert [s.texture_key for s in sprites if (s.texture_key or "").startswith("sk_selection-mod-")] == [
        "sk_selection-mod-hidden", "sk_selection-mod-doubletime"]
    assert sum(s.texture_key == "lz_mod-classic" for s in sprites) == 1


def test_visibility_off_and_repeated_draws_never_resolve_or_upload_icons(bank, tmp_path, monkeypatch):
    png(tmp_path, "selection-mod-hidden")
    hud, recorder = hud_with_mods(bank, tmp_path, ["HD", "CL"], show_progress=False)
    render(hud, recorder, 2300)  # Establish the existing accuracy/keys anchors.

    def forbidden(*args, **kwargs):
        raise AssertionError("Per-frame skin resolution/upload is forbidden")

    monkeypatch.setattr(Skin, "find_texture", forbidden)
    monkeypatch.setattr(TextureFile, "load_rgba", forbidden)
    monkeypatch.setattr(recorder, "upload_texture", forbidden)
    first = mod_sprites(hud)
    for time in (5000, 1200, 2300):
        render(hud, recorder, time)  # Includes the grade's geometry probe.
        assert mod_sprites(hud) == first
    hud.s.show_mods = False
    assert mod_sprites(hud) == []
    assert hud._mod_rows == 0 and hud._mod_stack_offset() == 0
    render(hud, recorder)
    assert "mods" not in hud.bounds


@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080)])
@pytest.mark.parametrize("scale", [1.0, 1.25])
@pytest.mark.parametrize("acronyms", [("HD",), ("HD", "DT", "CL"),
                                     ("HD", "CL", "DT", "DA", "HR", "RX", "AP", "WU", "WD", "FL", "EZ", "NF")])
def test_mixed_rows_grade_counter_and_keys_share_geometry(bank, tmp_path, resolution, scale, acronyms):
    for acronym in ("HD", "DT", "HR", "RX", "AP", "FL", "EZ", "NF"):
        png(tmp_path, LEGACY_MOD_TEXTURES[acronym], (144, 64))
    hud, recorder = hud_with_mods(bank, tmp_path, acronyms, rate=1.3,
                                 renderer=Recorder(*resolution), hud_scale=scale,
                                 show_hit_counter=True, show_progress=False)
    anchor = None
    for time in (1200, 5000, 2300, 1200):
        render(hud, recorder, time)
        b = hud.bounds
        if anchor is not None:
            assert hud._legacy_grade_anchor == anchor
        anchor = hud._legacy_grade_anchor
        assert_mod_column(hud)
        assert b["hits"].top >= max(r.bottom for r in hud._mod_layout_rects())+8*scale-1e-6
        assert all(0 <= r.left <= r.right <= hud.ui_w_l for r in hud._mod_layout_rects())
        for name in ("hits", "grade"):
            assert not b[name].intersects(b["keys"]), name
        for name in ("score", "accuracy", "hits"):
            assert not b["grade"].intersects(b[name]), name
        for rect in hud._mod_layout_rects():
            assert not b['grade'].intersects(rect)
        rows = hud._mod_rows
        # Every icon/pill occupies one slot, with the same row centres.
        slots = [s for s in mod_sprites(hud) if s.texture_key in ("pill", "lz_mod-icon")
                 or (s.texture_key or "").startswith("sk_selection-mod-")]
        assert len(slots) == len(acronyms)
        assert len({s.y for s in slots}) == rows
        actual_heights = []
        for y in sorted({s.y for s in slots}):
            row = [s for s in slots if s.y == y]
            actual_heights.append(max(GAMEPLAY_ICON_SIZE*scale if s.texture_key == "lz_mod-icon"
                                      else s.h/hud.lk for s in row))
            assert (row[-1].x+row[-1].w/2 - row[0].x+row[0].w/2) / hud.lk <= MOD_PILL_ROW_MAX_W*scale+1e-6
            for left, right in zip(row, row[1:]):
                assert left.x+left.w/2 <= right.x-right.w/2+1e-6
        assert hud._mod_row_heights == pytest.approx(actual_heights)
        assert b['hits'].top == pytest.approx(
            max(r.bottom for r in hud._mod_layout_rects())+8*scale)


@pytest.mark.parametrize("logical,size720,size1080", [
    ((100, 100), (93.75, 93.75), (140.625, 140.625)),
    ((96, 48), (90, 45), (135, 67.5)),
    ((120, 40), (112.5, 37.5), (168.75, 56.25)),
])
@pytest.mark.parametrize("density", [1, 2], ids=["1x", "2x"])
@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080)])
@pytest.mark.parametrize("scale", [1.0, 1.25])
@pytest.mark.parametrize("blank", [False, True])
def test_native_mod_icon_pixels_and_collision_reservation(
        bank, tmp_path, logical, size720, size1080, density, resolution, scale, blank):
    stem = "selection-mod-hidden" + ("@2x" if density == 2 else "")
    raw_size = tuple(n*density for n in logical)
    png(tmp_path, stem, raw_size, (190, 80, 30, 0 if blank else 128))
    hud, recorder = hud_with_mods(bank, tmp_path, ["HD", "ZZ"],
                                 renderer=Recorder(*resolution), hud_scale=scale,
                                 show_hit_counter=True, show_progress=False)
    render(hud, recorder, 2300)
    sprites = mod_sprites(hud)
    icon = next(s for s in sprites if s.texture_key == "sk_selection-mod-hidden")
    pill = next(s for s in sprites if s.texture_key == "pill")
    expected = size720 if resolution[1] == 720 else size1080
    assert (icon.w, icon.h) == pytest.approx(tuple(n*scale for n in expected))
    assert hud.sk.size["selection-mod-hidden"] == logical
    assert recorder.uploaded[icon.texture_key].shape[:2] == raw_size[::-1]
    assert icon.color == (1, 1, 1, 1)
    assert icon.uv_off == (0, 0) and icon.uv_scale == (1, 1)
    assert icon.y == pytest.approx(pill.y)  # adjacent fallback stays centred
    assert pill.h == pytest.approx(MOD_PILL_H*resolution[1]/768*scale)
    assert icon.x+icon.w/2 < pill.x-pill.w/2
    assert hud._mod_rows == 1
    # A blank native canvas still occupies its unchanged draw slot; the
    # ordinary 40-unit fallback pill, centred in that row, clears the counts.
    clearance = (max(logical[1],40)/2+20 if blank else max(logical[1],40))+8
    assert_mod_column(hud)
    assert hud.bounds['hits'].top == pytest.approx(
        max(r.bottom for r in hud._mod_layout_rects())+8*scale)
    for name in ("hits", "grade"):
        assert not hud.bounds[name].intersects(hud.bounds["keys"]), name
    for name in ("score", "accuracy", "hits"):
        assert not hud.bounds["grade"].intersects(hud.bounds[name]), name
    for rect in hud._mod_layout_rects():
        assert not hud.bounds['grade'].intersects(rect)
    if blank:
        assert "selection-mod-hidden" in hud.sk.empty
        assert not recorder.uploaded[icon.texture_key][..., 3].any()
    before = (hud.bounds["mods"], hud._legacy_grade_anchor)
    for time in (5000, 1200, 2300):
        render(hud, recorder, time)
        assert (hud.bounds["mods"], hud._legacy_grade_anchor) == before


def test_short_native_canvas_is_not_enlarged_to_the_pill_height(bank, tmp_path):
    png(tmp_path, "selection-mod-hidden", (24, 16))
    hud, _ = hud_with_mods(bank, tmp_path, ["HD", "ZZ"])
    sprites = mod_sprites(hud)
    icon = next(s for s in sprites if s.texture_key == "sk_selection-mod-hidden")
    pill = next(s for s in sprites if s.texture_key == "pill")
    assert (icon.w, icon.h) == pytest.approx((22.5, 15))
    assert pill.h == pytest.approx(37.5)
    assert hud._legacy_mod_stack().grade.top == pytest.approx(max(r.bottom for r in hud._mod_layout_rects())+8)


@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080)])
def test_native_height_moves_following_grade_but_preserves_native_components(
        bank, tmp_path, resolution):
    png(tmp_path, "selection-mod-hidden", (100, 40))
    small, rec = hud_with_mods(bank, tmp_path, ["HD"],
                              renderer=Recorder(*resolution), show_progress=False)
    render(small, rec, 2300)
    png(tmp_path, "selection-mod-hidden", (100, 100))
    tall, rec = hud_with_mods(bank, tmp_path, ["HD"],
                             renderer=Recorder(*resolution), show_progress=False)
    render(tall, rec, 2300)
    assert tall._legacy_grade_anchor[1]-small._legacy_grade_anchor[1] == pytest.approx(60)
    for name in ("score", "accuracy", "keys", "health"):
        assert tall.bounds[name] == small.bounds[name], name


@pytest.mark.parametrize("resolution", [(1280, 720), (1920, 1080)])
@pytest.mark.parametrize("native_height", [96, 130])
def test_native_rows_stack_counts_above_grade_without_visibility_shifts(
        bank, tmp_path, resolution, native_height):
    png(tmp_path, "selection-mod-hidden", (100, native_height))
    hud, recorder = hud_with_mods(bank, tmp_path, ["HD"],
                                 renderer=Recorder(*resolution),
                                 show_hit_counter=True, show_progress=False)
    # Start hidden too: counts must obtain the same grade reservation even
    # before grade has ever drawn or populated its geometry cache.
    hud.s.show_grade = False
    render(hud, recorder, 2300)
    counter = hud.bounds["hits"]
    assert_mod_column(hud)
    with_counter=hud._legacy_grade_pivot()
    for visible in (True, False, True):
        hud.s.show_grade = visible
        render(hud, recorder, 2300)
        assert hud.bounds["hits"] == counter
        if visible:
            assert hud._legacy_grade_anchor == with_counter
            assert counter.bottom+8 <= hud._legacy_mod_stack().grade.top+1e-6
            assert not counter.intersects(hud.bounds["grade"])
    for hits in (False, True):
        hud.s.show_hit_counter = hits
        render(hud, recorder, 2300)
        if hits:
            assert hud._legacy_grade_anchor == with_counter
        else:
            assert hud._legacy_grade_anchor[1] < with_counter[1]


def test_real_gl_preserves_rgb_alpha_orientation_padding_and_metal_commands(bank, tmp_path):
    rgba = np.zeros((40, 80, 4), dtype=np.uint8)
    colors = [(240, 80, 40, 128), (30, 200, 80, 200),
              (40, 60, 220, 255), (250, 230, 50, 64)]
    for (x, y), color in zip(((8, 8), (40, 8), (8, 20), (40, 20)), colors):
        rgba[y:y+12, x:x+32] = color
    Image.fromarray(rgba).save(tmp_path / "selection-mod-hidden.png")
    renderer = SpriteRenderer(1280, 720)
    try:
        hud, _ = hud_with_mods(bank, tmp_path, ["HD"], renderer=renderer, hud_opacity=.5)
        sprites = mod_sprites(hud)
        assert len(sprites) == 1
        icon = sprites[0]
        assert icon.w / icon.h == pytest.approx(2)
        renderer.begin((0, 0, 0))
        renderer.draw(sprites)
        pixels = renderer.read_rgb()

        def sample(u, v):
            x = int(icon.x-icon.w/2+u*icon.w)
            y = int(icon.y-icon.h/2+v*icon.h)
            return pixels[y, x].astype(float)

        assert not sample(.03, .5).any()  # authored transparent left padding
        for uv, color in zip(((.3, .35), (.7, .35), (.3, .65), (.7, .65)), colors):
            expected = np.array(color[:3]) * (color[3]/255) * .5
            assert sample(*uv) == pytest.approx(expected, abs=2)

        # Exercise the actual Metal serializer on Linux, without claiming
        # native Metal pixels. No backend-specific icon drawing is involved.
        path = Path(__file__).resolve().parents[1] / "osu_std_renderer/render/metal/renderer.py"
        cls = next(n for n in ast.parse(path.read_text()).body
                   if isinstance(n, ast.ClassDef) and n.name == "MetalSpriteRenderer")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
        ns = dict(np=np, perf=perf, plan_texture_batches=plan_texture_batches, _UNITS=16)
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), ns)
        metal = SimpleNamespace(post_xform=None, _tex={icon.texture_key: 7}, _white=0, _scene=[])
        ns["draw"](metal, sprites)
        op, instances, textures, additive = metal._scene[0]
        assert op == "sprites" and textures == [7] and not additive
        assert instances.shape == (1, 14)
        assert instances[0, :5] == pytest.approx([icon.x, icon.y, icon.w, icon.h, 0])
        assert instances[0, 5:9] == pytest.approx([1, 1, 1, .5])
        assert instances[0, 9:13] == pytest.approx([0, 0, 1, 1])
    finally:
        renderer.release()
