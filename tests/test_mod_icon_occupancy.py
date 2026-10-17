"""Legacy layout reclaims alpha-only padding without changing drawable sprites."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from osu_std_renderer.render.gl import SpriteRenderer
from osu_std_renderer.render.hud import build_mod_pills
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.skin.skin import Skin, TextureFile
from tests.test_hud_layout import Recorder, render, assert_mod_column
from tests.test_legacy_mod_icons import hud_with_mods, mod_sprites
from tests.test_lazer_mod_icons import bank


def icon(folder, name="hidden", *, density=1, size=(100, 100), box=(10, 15, 90, 45), fringe=None):
    folder.mkdir(parents=True, exist_ok=True)
    w, h = (n*density for n in size)
    # RGB is deliberately dark; only alpha defines occupied content.
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    if box:
        x0,y0,x1,y1 = (int(n*density) for n in box)
        rgba[y0:y1,x0:x1] = (0, 0, 0, 255)
    if fringe:
        x,y = (int(n*density) for n in fringe)
        rgba[y,x] = (255,255,255,1)
    Image.fromarray(rgba).save(folder/f"selection-mod-{name}{'@2x' if density==2 else ''}.png")
    return rgba


@pytest.mark.parametrize("density", [1,2])
@pytest.mark.parametrize("resolution", [(1280,720),(1920,1080)])
@pytest.mark.parametrize("scale", [1,1.25])
def test_padding_moves_only_counter_by_measured_spare_height(bank, tmp_path, density, resolution, scale):
    opaque, padded = tmp_path/'opaque', tmp_path/'padded'
    icon(opaque, density=density, box=(0,0,100,100))
    source = icon(padded, density=density)
    before, rec0 = hud_with_mods(bank, opaque, ['HD'], renderer=Recorder(*resolution),
                                hud_scale=scale, show_hit_counter=True, show_progress=False)
    after, rec1 = hud_with_mods(bank, padded, ['HD'], renderer=Recorder(*resolution),
                               hud_scale=scale, show_hit_counter=True, show_progress=False)
    render(before, rec0, 2300); render(after, rec1, 2300)
    assert after.sk.size['selection-mod-hidden'] == (100,100)
    assert after.sk.ink_bounds['selection-mod-hidden'] == (10,15,90,45)
    assert np.array_equal(rec1.uploaded['sk_selection-mod-hidden'], source)
    old_sprite, = mod_sprites(before)
    new_sprite, = mod_sprites(after)
    assert replace(new_sprite,x=old_sprite.x,y=old_sprite.y) == old_sprite
    assert (new_sprite.w,new_sprite.h) == pytest.approx((100*scale*resolution[1]/768,)*2)
    assert before.bounds['hits'].top-after.bounds['hits'].top == pytest.approx(70*scale)
    assert after.bounds['hits'].top-max(r.bottom for r in after._mod_layout_rects()) == pytest.approx(8*scale)
    assert after.bounds['hits'].top < after.bounds['mods'].bottom
    for hud in (before,after):
        assert_mod_column(hud)
        assert not hud.bounds['hits'].intersects(hud.bounds['grade'])
        for rect in hud._mod_layout_rects():
            assert not rect.intersects(hud.bounds['hits'])
    for name in ('score','accuracy','progress','keys','health'):
        if name in before.bounds:
            assert after.bounds[name] == before.bounds[name], name


@pytest.mark.parametrize("box,fringe,expected", [
    ((0,0,100,100),None,(0,0,100,100)),
    ((10,15,90,45),(95,92),(10,15,96,93)),
    (None,(0,99),(0,99,1,100)),
    (None,None,None),
])
def test_opaque_dark_pixels_alpha_one_fringe_and_blank_are_distinct(bank, tmp_path, box, fringe, expected):
    icon(tmp_path, box=box, fringe=fringe)
    hud, rec = hud_with_mods(bank,tmp_path,['HD'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert hud.sk.ink_bounds['selection-mod-hidden'] == expected
    assert [s.texture_key for s in mod_sprites(hud)] == ['sk_selection-mod-hidden']
    if expected is None:
        assert hud._mod_layout_rects() == ()
        assert hud._legacy_mod_stack().counter.top >= hud.bounds['accuracy'].bottom+8
    else:
        assert_mod_column(hud)
        assert hud.bounds['hits'].top-max(r.bottom for r in hud._mod_layout_rects()) == pytest.approx(8)


@pytest.mark.parametrize("resolution", [(1280,720),(1920,1080)])
@pytest.mark.parametrize("scale", [1,1.25])
def test_mixed_wrapped_rows_include_every_icon_pill_and_rate_label(bank,tmp_path,resolution,scale):
    icon(tmp_path,'hidden',size=(700,180),box=(50,5,650,175))
    icon(tmp_path,'doubletime',size=(100,100),box=(10,10,90,30))
    hud,rec=hud_with_mods(bank,tmp_path,['HD','DT','ZZ','CL','DA','RX','WU'],rate=1.3,
                          renderer=Recorder(*resolution),hud_scale=scale,
                          show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert hud._mod_rows >= 2
    keys={s.texture_key for s in mod_sprites(hud)}
    assert {'sk_selection-mod-hidden','sk_selection-mod-doubletime','pill','lz_mod-classic','glyph_1'} <= keys
    assert hud.bounds['hits'].top == pytest.approx(max(r.bottom for r in hud._mod_layout_rects())+8*scale)
    for rect in hud._mod_layout_rects():
        assert not rect.intersects(hud.bounds['hits'])
        assert not rect.intersects(hud.bounds['grade'])
    assert not hud.bounds['hits'].intersects(hud.bounds['keys'])
    assert not hud.bounds['hits'].intersects(hud.bounds['grade'])


def test_earlier_row_is_reserved_when_final_row_is_fully_transparent(bank,tmp_path):
    icon(tmp_path,'hidden',size=(500,160),box=(0,0,500,150))
    icon(tmp_path,'doubletime',size=(500,200),box=None)
    hud,rec=hud_with_mods(bank,tmp_path,['HD','DT'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert hud._mod_rows == 2 and len(hud._mod_layout_rects()) == 1
    assert_mod_column(hud)
    assert hud.bounds['hits'].top >= hud._mod_layout_rects()[0].bottom+8-1e-6


def test_grade_counts_visibility_and_seeking_do_not_jitter_or_rescan(bank,tmp_path,monkeypatch):
    icon(tmp_path)
    hud,rec=hud_with_mods(bank,tmp_path,['HD'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    initial=hud.bounds['hits']
    expected_top,expected_right=initial.top,initial.right
    grade=hud._legacy_grade_pivot()
    def forbidden(*args,**kwargs):
        pytest.fail('alpha/image scanning or texture upload at render time')
    monkeypatch.setattr(TextureFile,'load_rgba',forbidden)
    monkeypatch.setattr(Image,'open',forbidden)
    monkeypatch.setattr(np,'flatnonzero',forbidden)
    monkeypatch.setattr(rec,'upload_texture',forbidden)
    for time,letter,count in [(5000,'SS',1),(1200,'A',99),(2300,'D',999),(2300,'SS',0)]:
        hud.data.grade_at=lambda t,letter=letter:letter
        hud.data.counts_at=lambda t,count=count:(count,)*4
        for visible in (False,True):
            hud.s.show_grade=visible
            render(hud,rec,time)
            assert hud._legacy_grade_pivot() == grade
            assert (hud.bounds['hits'].top,hud.bounds['hits'].right) == pytest.approx((expected_top,expected_right))
            if visible:assert not hud.bounds['hits'].intersects(hud.bounds['grade'])
        hud.s.show_hit_counter=False
        render(hud,rec,time)
        assert hud._legacy_grade_pivot()[1] < grade[1]
        hud.s.show_hit_counter=True


def test_skin_selection_scale_and_viewport_refresh_layout_cache(bank,tmp_path):
    icon(tmp_path/'padded')
    icon(tmp_path/'opaque',box=(0,0,100,100))
    hud,rec=hud_with_mods(bank,tmp_path/'padded',['HD'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    first=hud.bounds['hits'].top
    hud.sk=SkinElements(Skin(tmp_path/'opaque'),rec)
    render(hud,rec,2300)
    assert hud.bounds['hits'].top-first == pytest.approx(70)
    hud._mod_acrs=build_mod_pills(0,['ZZ'])
    render(hud,rec,2300)
    assert_mod_column(hud)
    hud._mod_acrs=build_mod_pills(0,['HD'])
    render(hud,rec,2300)
    assert_mod_column(hud)
    previous_key=hud._legacy_mod_stack_key
    hud.es=1.25
    hud.ui_w_l+=100
    render(hud,rec,2300)
    assert_mod_column(hud)
    assert hud._legacy_mod_stack_key != previous_key
    hud.s.show_mods=False
    render(hud,rec,2300)
    assert hud._mod_stack_offset() == 0


def test_actual_opengl_drawable_is_identical_before_and_after_occupancy_calculation(bank,tmp_path):
    icon(tmp_path)
    renderer=SpriteRenderer(1280,720)
    try:
        hud,_=hud_with_mods(bank,tmp_path,['HD'],renderer=renderer,show_progress=False)
        sprites=mod_sprites(hud)
        renderer.begin((.3,.2,.1));renderer.draw(sprites)
        before=renderer.read_rgb()
        # Layout consumes metadata only; the very same source texture and
        # native quad are drawn through the existing GL/Metal Sprite path.
        hud._mod_layout_rects();hud._mod_stack_offset()
        renderer.begin((.3,.2,.1));renderer.draw(mod_sprites(hud))
        assert np.array_equal(renderer.read_rgb(),before)
        assert sprites[0].w == sprites[0].h == 93.75
    finally:
        renderer.release()
