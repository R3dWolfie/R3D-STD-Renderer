"""Owner reference: visible mods → four counters → right-aligned grade."""
import numpy as np
import pytest
from PIL import Image

from osu_std_renderer.render.hud import LEGACY_STACK_RIGHT_INSET, build_mod_pills
from osu_std_renderer.render.hud_layout import HudRect
from tests.test_hud_layout import Recorder, make_hud, render, assert_mod_column as assert_column
from tests.test_legacy_mod_icons import hud_with_mods, mod_sprites
from tests.test_lazer_mod_icons import bank
from tests.test_mod_icon_occupancy import icon




@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale',[.75,1,1.25])
@pytest.mark.parametrize('density',[1,2])
@pytest.mark.parametrize('box',[(10,15,90,45),(0,0,100,100),None])
def test_native_content_alignment_full_quads_and_vertical_order(bank,tmp_path,resolution,scale,density,box):
    rgba=icon(tmp_path,density=density,box=box)
    hud,rec=hud_with_mods(bank,tmp_path,['HD'],renderer=Recorder(*resolution),
                          hud_scale=scale,show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert_column(hud)
    sprite,=mod_sprites(hud)
    assert (sprite.w,sprite.h) == pytest.approx((100*scale*resolution[1]/768,)*2)
    assert np.array_equal(rec.uploaded[sprite.texture_key],rgba)
    assert sprite.uv_off == (0,0) and sprite.uv_scale == (1,1)
    assert sprite.color == (1,1,1,1)
    if box:
        visible,=hud._mod_layout_rects()
        assert 8*scale-1e-6 <= visible.top-hud.bounds['accuracy'].bottom <= 12*scale
        assert visible.right == pytest.approx(hud._legacy_mod_stack().grade.right)
        assert visible.bottom-visible.top == pytest.approx((box[3]-box[1])*scale)
    else:
        assert hud._mod_layout_rects() == ()


def test_wide_padding_and_alpha_one_preserve_content_and_size(bank,tmp_path):
    icon(tmp_path,size=(1000,200),box=(700,30,800,80),fringe=(810,89))
    hud,rec=hud_with_mods(bank,tmp_path,['HD'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert_column(hud)
    sprite,=mod_sprites(hud)
    assert (sprite.w,sprite.h) == pytest.approx((937.5,187.5))
    assert hud._mod_layout_rects()[0].bottom-hud._mod_layout_rects()[0].top == pytest.approx(60)


def test_settings_rank_counts_seek_and_paint_order_share_static_layout(bank,tmp_path,monkeypatch):
    icon(tmp_path)
    hud,rec=hud_with_mods(bank,tmp_path,['HD'],show_hit_counter=True,show_progress=False)
    layout=hud._legacy_mod_stack()  # before a single live frame or bound
    original=layout
    def forbidden(*a,**kw):raise AssertionError('per-frame image scan/upload')
    monkeypatch.setattr(Image,'open',forbidden)
    monkeypatch.setattr(np,'flatnonzero',forbidden)
    monkeypatch.setattr(rec,'upload_texture',forbidden)
    for time,rank,n in [(5000,'SS',0),(2300,'A',1),(500,'D',0),(1200,'D',3),(2300,'S',2)]:
        hud.data.grade_at=lambda t,rank=rank:rank
        hud.data.counts_at=lambda t,n=n:(n,)*4
        for visible in (True,False):
            hud.s.show_grade=visible
            render(hud,rec,time)
            assert_column(hud)
            assert hud._legacy_mod_stack() is original
            assert hud.bounds['hits'].right == original.counter.right
            assert hud.bounds['hits'].top == original.counter.top
    hud.s.show_hit_counter=False
    render(hud,rec,2300)
    without=hud._legacy_mod_stack()
    assert without.counter is None
    assert without.grade.top == pytest.approx(max(r.bottom for r in without.visible)+8)
    assert without.quads == original.quads
    assert without.grade.top < original.grade.top


@pytest.mark.parametrize('mods',[
    ['DT','CL','ZZ'],['HD','DT','CL','DA','HR','RX','AP','WU','WD','FL','EZ','NF','ZZ']])
def test_mixed_custom_rate_wrapped_rows_and_overflow_stay_safe(bank,tmp_path,mods):
    icon(tmp_path,'doubletime',size=(100,100),box=(10,15,90,45))
    hud,rec=hud_with_mods(bank,tmp_path,mods,rate=1.3,show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert_column(hud)
    assert [p.acr for p in hud._mod_acrs] == mods
    for rect in hud._mod_layout_rects():
        assert rect.left >= 0 and rect.right <= hud.ui_w_l
        assert rect.top >= 0 and rect.bottom <= 768


def test_overflow_continues_whole_rows_and_tail_left_of_keys(bank,tmp_path):
    icon(tmp_path,'hidden',size=(400,400),box=(0,0,400,400))
    icon(tmp_path,'doubletime',size=(400,350),box=(0,0,400,350))
    hud,rec=hud_with_mods(bank,tmp_path,['HD','DT'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert_column(hud)
    stack=hud._legacy_mod_stack()
    assert stack.overflow == 'columns left of keys'
    assert len({r.right for r in stack.visible}) == 2
    assert stack.counter.bottom+8 == stack.grade.top
    assert stack.grade.bottom <= 760
    assert all(not a.intersects(b) for i,a in enumerate(stack.visible) for b in stack.visible[i+1:])


def test_unfittable_native_art_has_explicit_error_instead_of_rescaling(bank,tmp_path):
    icon(tmp_path,size=(100,1000),box=(0,0,100,1000))
    hud,_=hud_with_mods(bank,tmp_path,['HD'],show_hit_counter=True)
    with pytest.raises(ValueError,match='cannot fit native visible artwork'):
        mod_sprites(hud)


def test_argon_no_mod_and_hidden_mod_paths_use_existing_layout(bank,tmp_path):
    argon,_=make_hud(bank,show_hit_counter=True)
    assert argon._legacy_mod_stack() is None
    hud,rec=hud_with_mods(bank,tmp_path,[],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    before=dict(hud.bounds)
    hud._mod_acrs=build_mod_pills(0,['DT'])
    hud.s.show_mods=False
    render(hud,rec,2300)
    assert hud._legacy_mod_stack() is None and hud.bounds == before


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale',[.75,1,1.25])
@pytest.mark.parametrize('judgment',range(4))
def test_large_judgment_totals_stay_inside_fixed_inset_without_jitter(
        bank,tmp_path,resolution,scale,judgment):
    icon(tmp_path)
    hud,_=hud_with_mods(bank,tmp_path,['HD'],renderer=Recorder(*resolution),
                        hud_scale=scale,show_hit_counter=True,show_progress=False)
    # Only the direct mod/counter painters run here. A range supplies the
    # replay's maximum count without allocating a synthetic billion events.
    maximum=999_999_999
    hud.data.ev_times=range(maximum)
    layout=hud._legacy_mod_stack()
    if not layout.overflow:
        assert layout.counter.right == pytest.approx(hud.ui_w_l-44*scale)
    assert layout.counter.right <= hud.ui_w_l-LEGACY_STACK_RIGHT_INSET*scale+1e-6
    previous_top=None
    for total in (0,9,999,99_999,maximum,0):
        counts=tuple(total if i==judgment else 0 for i in range(4))
        hud.data.counts_at=lambda t,counts=counts:counts
        sprites=[]
        hud._hit_counter(sprites,2300)
        actual=HudRect.sprites(sprites,hud.lk)
        assert actual.left >= layout.counter.left-1e-6
        assert actual.right == pytest.approx(layout.counter.right)
        assert actual.left >= 8*scale
        assert actual.right <= hud.ui_w_l-LEGACY_STACK_RIGHT_INSET*scale+1e-6
        assert actual.top == pytest.approx(layout.counter.top)
        if previous_top is not None:assert actual.top == pytest.approx(previous_top)
        previous_top=actual.top
        assert hud._legacy_mod_stack() is layout


def test_requested_left_shift_preserves_column_order_and_vertical_geometry(bank,tmp_path):
    icon(tmp_path)
    hud,rec=hud_with_mods(bank,tmp_path,['HD'],show_hit_counter=True,show_progress=False)
    render(hud,rec,2300)
    assert_column(hud)
    stack=hud._legacy_mod_stack()
    assert stack.overflow is None
    assert stack.counter.right == pytest.approx(hud.ui_w_l-20-24)
    assert stack.grade.right == stack.counter.right
    assert max(r.right for r in stack.visible) == pytest.approx(stack.counter.right)
    assert stack.counter.bottom+8 == pytest.approx(stack.grade.top)
