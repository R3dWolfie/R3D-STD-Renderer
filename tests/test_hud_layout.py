"""Source container constraints, provider independence, EGL and Metal contract."""
from __future__ import annotations

import ast
import dataclasses
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from osu_std_renderer.render import perf
from osu_std_renderer.render.gl import SpriteRenderer, plan_texture_batches
from osu_std_renderer.render.hud import StdHud, build_mod_pills
from osu_std_renderer.render.hud_layout import ARGON, LEGACY, HudRect, select_layout
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.textures import TextureBank
from osu_std_renderer.ruleset import HealthTimeline
from osu_std_renderer.settings import StdRenderSettings
from osu_std_renderer.skin.skin import Skin
from tests.capture_hud_layout import fixtures
from tests.test_hud import _map, _frames, _three_circle_run


class Recorder:
    def __init__(self, width=1280, height=720):
        self.width, self.height = width, height
        self.uploaded, self.sprites = {}, []

    def upload_texture(self, key, rgba, **kwargs):
        self.uploaded[key] = rgba

    write_texture = upload_texture

    def draw(self, sprites):
        self.sprites.extend(sprites)


@pytest.fixture(scope='module')
def bank():
    return TextureBank(Recorder())


@pytest.fixture(scope='module')
def skins(tmp_path_factory):
    return fixtures(tmp_path_factory.mktemp('hud-skins'))


def make_hud(bank, *, folder=None, renderer=None, frames=None, **opts):
    renderer = renderer or Recorder()
    skin = SkinElements(Skin(skin_dir=folder), renderer) if folder is not None else None
    bm = _map('100,100,1000,1,0,0:0:0:0:\n200,100,2000,1,0,0:0:0:0:\n300,100,3000,1,0,0:0:0:0:\n')
    sim = _three_circle_run(click_third=True)
    if frames is None:
        frames = _frames([(1000,100,100,4),(1010,100,100,0),
                          (2000,200,100,8),(2010,200,100,0)])
    hud = StdHud(renderer, bank, StdRenderSettings(skin_dir=folder, **opts),
                 sim, frames, bm, skin_elems=skin, health=HealthTimeline(bm,sim),
                 mods=0x18, pp_timeline=[(0,0),(1000,50),(3000,100)])
    hud.pin_final_score(12_345_678)
    return hud, renderer


def render(hud, recorder, t=5000):
    recorder.sprites.clear()
    hud._draw_timed(t)
    return list(recorder.sprites)


@pytest.mark.parametrize('skin', ['complete','digits-only','keys-only','empty','blank'])
def test_partial_or_empty_legacy_never_changes_layout(bank, skins, skin):
    hud, rec = make_hud(bank, folder=skins/skin)
    sprites = render(hud, rec)
    assert hud.layout == LEGACY
    assert 'argon_wedge' not in {s.texture_key for s in sprites}
    assert not any((s.texture_key or '').startswith('aglyph_') for s in sprites)
    assert hud.bounds['score'].right == pytest.approx(
        hud.ui_w_l-10+hud.layout.legacy_score_offset_x)
    assert hud.bounds['keys'].top < 384
    assert 'progress' in hud.bounds and hud.bounds['progress'].top < 200
    if skin in ('keys-only','empty'):
        assert any((s.texture_key or '').startswith('lg_') for s in sprites)
    if skin == 'digits-only':
        keys = {s.texture_key for s in sprites}
        assert 'lg_io_key' in keys and 'lg_io_bg' in keys
        assert 'lg_scorebar_bg' in keys and 'lg_scorebar_colour' in keys


def test_skinless_defaults_and_immutable_profile(bank, skins):
    hud, rec = make_hud(bank, default_skin_dir=skins/'complete')
    assert hud.layout == ARGON
    sprites=render(hud, rec)
    assert any(s.texture_key=='argon_wedge' for s in sprites)
    assert hud.bounds['accuracy'].right == pytest.approx(hud.ui_w_l-20)
    assert hud.bounds['keys'].bottom == pytest.approx(768-66, abs=2)
    assert hud.bounds['progress'].bottom == pytest.approx(758)
    with pytest.raises(dataclasses.FrozenInstanceError):
        hud.layout.name = 'legacy'
    for override in (False,True):
        forced, rec = make_hud(bank, legacy_defaults=True,
                                renderer_default_font_and_ranks=override)
        assert forced.layout == LEGACY
        assert all(forced.bounds.get(k) is None for k in ('score','keys'))
        render(forced, rec)
        assert forced.bounds['score'].right == pytest.approx(
            forced.ui_w_l-10+forced.layout.legacy_score_offset_x)


def test_fallback_provider_and_explicit_path_selection(skins, tmp_path):
    fallback=SkinElements(Skin(fallback_dir=skins/'complete'),Recorder())
    assert select_layout(StdRenderSettings(default_skin_dir=skins/'complete'),fallback)==ARGON
    assert select_layout(StdRenderSettings(skin_dir=skins/'empty'),None)==LEGACY
    # Retain the existing loader's fail-soft missing-path behavior: an
    # explicit legacy path remains Legacy with procedural fallback assets.
    missing=tmp_path/'missing'
    hud,rec=make_hud(TextureBank(Recorder()),folder=missing)
    assert hud.layout==LEGACY
    render(hud,rec)
    assert hud.bounds['score'].right==pytest.approx(
        hud.ui_w_l-10+hud.layout.legacy_score_offset_x)


@pytest.mark.parametrize('resolution', [(1280,720),(1920,1080),(1024,768),(2560,1080)])
@pytest.mark.parametrize('scale', [.75,1,1.25])
@pytest.mark.parametrize('font_size,overlap', [(18,0),(96,-12),(480,400),(80,180)])
def test_actual_score_bounds_accuracy_pie_and_viewport(bank, tmp_path, resolution, scale, font_size, overlap):
    # Mixed @2x, nonuniform widths/heights, deep ScorePrefix and overlap
    # extremes. Eight-digit pinned score must survive with no truncation.
    prefix='deep/custom/score'
    root=tmp_path/'skin'
    (root/'deep/custom').mkdir(parents=True)
    (root/'skin.ini').write_text(f'[Fonts]\nScorePrefix: {prefix}\nScoreOverlap: {overlap}\n')
    for i in range(10):
        factor=2 if i%2 else 1
        size=(round(font_size*.65 + i)*factor, (font_size+i)*factor)
        Image.new('RGBA',size,'white').save(root/f'{prefix}-{i}{"@2x" if factor==2 else ""}.png')
    hud,rec=make_hud(bank,folder=root,renderer=Recorder(*resolution),hud_scale=scale)
    # Retain the upstream geometry checks. Project calibration is covered
    # separately by paired sprite comparisons, rather than replacing these
    # stock source expectations with the owner's provisional numbers.
    hud.layout=dataclasses.replace(hud.layout, legacy_score_offset_x=0.0,
                                   legacy_accuracy_offset=(0.0,0.0))
    sprites=render(hud,rec)
    score,acc,pie=(hud.bounds[n] for n in ('score','accuracy','progress'))
    assert score.right==pytest.approx(hud.ui_w_l-10*scale)
    assert acc.right==pytest.approx(hud.ui_w_l-17*scale)
    assert acc.top-score.bottom==pytest.approx(9*scale)
    assert acc.left-pie.right==pytest.approx(18*scale)
    assert (pie.top+pie.bottom)/2==pytest.approx((acc.top+acc.bottom)/2)
    for r in (score,acc,pie):
        assert r.left>=-1e-6 and r.top>=-1e-6
        assert r.right<=hud.ui_w_l+1e-6 and r.bottom<=768
    score_sprites=[s for s in sprites if (s.texture_key or '').startswith('sk_score_') and s.y+s.h/2<=score.bottom*hud.lk+.001]
    assert len(score_sprites)==8
    assert not hud.bounds['grade'].intersects(pie)
    assert not hud.bounds['mods'].intersects(hud.bounds['grade'])
    for r in (hud.bounds['grade'],hud.bounds['mods']):
        assert r.left>=0 and r.top>=0 and r.right<=hud.ui_w_l and r.bottom<=768
    for extra in ('grade','mods'):
        assert not hud.bounds[extra].intersects(hud.bounds['keys'])


@pytest.mark.parametrize('resolution', [(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale', [.75,1,1.25])
@pytest.mark.parametrize('override', [False,True])
def test_legacy_calibration_moves_measured_runs_without_changing_glyphs(
        bank, skins, resolution, scale, override):
    hud,rec=make_hud(bank,folder=skins/'complete',renderer=Recorder(*resolution),
                     hud_scale=scale,renderer_default_font_and_ranks=override,
                     show_progress=False)
    layout=hud.layout
    hud.layout=dataclasses.replace(layout,legacy_score_offset_x=0.0,
                                   legacy_accuracy_offset=(0.0,0.0))
    before=[]
    hud._legacy_score_block(before,2300)
    original=dict(hud.bounds)
    # The preceding owner calibration: now move accuracy farther right to
    # clear the backing's rounded left edge at 100.00%, retaining score/Y.
    hud.layout=dataclasses.replace(layout,legacy_accuracy_offset=(7.0,-4.0))
    previous=[]
    hud._legacy_score_block(previous,2300)
    hud.layout=layout
    after=[]
    hud._legacy_score_block(after,2300)
    assert len(before)==len(after)
    for old,new in zip(before,after):
        score=old.y+old.h/2 <= original['score'].bottom*hud.lk+1e-6
        dx,dy=(-26,0) if score else (14,-4)
        assert new.x-old.x==pytest.approx(dx*scale*resolution[1]/768)
        assert new.y-old.y==pytest.approx(dy*scale*resolution[1]/768)
        # Texture, alpha, UV crop, aspect, dimensions and character count
        # remain identical; only layout positions may change.
        assert dataclasses.replace(new,x=old.x,y=old.y)==old
    for old,new in zip(previous,after):
        score=old.y+old.h/2 <= original['score'].bottom*hud.lk+1e-6
        dx,dy=(0,0) if score else (7,0)
        assert new.x-old.x==pytest.approx(dx*scale*resolution[1]/768)
        assert new.y-old.y==pytest.approx(dy*scale*resolution[1]/768)
        assert dataclasses.replace(new,x=old.x,y=old.y)==old
    assert hud.bounds['score'].top==0
    assert hud.bounds['score'].bottom==original['score'].bottom
    assert hud.bounds['accuracy'].top-hud.bounds['score'].bottom==pytest.approx(5*scale)
    assert ARGON.legacy_score_offset_x==0 and ARGON.legacy_accuracy_offset==(0,0)
    sprites=render(hud,rec,2300)
    for name in ('score','accuracy','progress','grade','mods'):
        if name in hud.bounds:
            rect=hud.bounds[name]
            assert rect.left>=0 and rect.top>=0
            assert rect.right<=hud.ui_w_l and rect.bottom<=768
    assert not hud.bounds['grade'].intersects(hud.bounds['keys'])
    assert sprites


@pytest.mark.parametrize('resolution', [(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale', [.75,1,1.25])
@pytest.mark.parametrize('override', [False,True])
def test_legacy_full_accuracy_keeps_leading_digit_and_fixed_right_edge(
        bank, skins, resolution, scale, override):
    hud,rec=make_hud(bank,folder=skins/'complete',renderer=Recorder(*resolution),
                     hud_scale=scale,renderer_default_font_and_ranks=override)
    # Exercise the extra integer digit at the 100% boundary, rather than
    # checking offsets alone. Score, right anchor and glyph scale must stay
    # fixed as accuracy grows; the pie and grade follow the measured left edge.
    snapshots=[]
    glyph_keys={g[0] for g in hud._legacy_glyphs('score','100.99%')[0]}
    for value in (.9999,1.0):
        hud.data.acc_at=lambda t,roll_ms,v=value:v
        sprites=render(hud,rec,2300)
        acc=hud.bounds['accuracy']
        readout=[s for s in sprites if s.texture_key in glyph_keys
                 and s.y-s.h/2>=acc.top*hud.lk-1e-6
                 and s.y+s.h/2<=acc.bottom*hud.lk+1e-6]
        snapshots.append((dict(hud.bounds),readout))
        for s in readout:
            assert s.x-s.w/2>=0
            assert s.x+s.w/2<=resolution[0]+1e-6
        assert not acc.intersects(hud.bounds['progress'])
        assert not hud.bounds['grade'].intersects(hud.bounds['progress'])
    (fraction,short),(full,long)=snapshots
    assert len(long)==len(short)+1
    assert long[0].texture_key==('aseg_1' if override else 'sk_score_1')
    assert full['score']==fraction['score']
    assert full['accuracy'].right==pytest.approx(fraction['accuracy'].right)
    assert full['accuracy'].top==pytest.approx(fraction['accuracy'].top)
    assert full['accuracy'].bottom==pytest.approx(fraction['accuracy'].bottom)
    # Fractional digits and punctuation retain their slot dimensions and UVs.
    for old,new in zip(short[-4:],long[-4:]):
        assert (new.w,new.h,new.uv_off,new.uv_scale)==(old.w,old.h,old.uv_off,old.uv_scale)
    shift=full['accuracy'].left-fraction['accuracy'].left
    assert shift<0
    assert full['progress'].left-fraction['progress'].left==pytest.approx(shift)
    assert full['grade'].right-fraction['grade'].right==pytest.approx(shift)


@pytest.mark.parametrize('resolution', [(1280,720),(1920,1080),(2560,1080)])
def test_legacy_health_native_crop_one_component_and_hp_only_toggle(bank, skins, resolution):
    hud,rec=make_hud(bank,folder=skins/'complete',renderer=Recorder(*resolution))
    # A real renderer path with an explicit non-full HP value makes stretching
    # versus horizontal cropping distinguishable and tests width independence.
    hud.health=SimpleNamespace(hp_at=lambda t:.37,last_gain_at=lambda t:None)
    calls=[]
    original=hud._legacy_health
    def traced(out,t):
        start=len(out);original(out,t);calls.append(list(out[start:]))
    hud._legacy_health=traced
    on=render(hud,rec,2300)
    assert len(calls)==1
    health=calls[0]
    assert on[-len(health):]==health
    bg,fill=health
    assert bg.texture_key=='sk_scorebar-bg'
    assert (bg.w/hud.lk,bg.h/hud.lk)==pytest.approx((300,40))
    assert (bg.x-bg.w/2,bg.y-bg.h/2)==pytest.approx((0,0))
    fraction=fill.uv_scale[0]
    assert fraction==pytest.approx(.37,abs=.001)  # finite damping kernel
    assert fill.w/hud.lk==pytest.approx(270*fraction)
    assert fill.h/hud.lk==pytest.approx(16)
    assert fill.uv_scale[1]==1
    assert (fill.x-fill.w/2,fill.y-fill.h/2)==pytest.approx((4.8*hud.lk,16*hud.lk))
    hud.s.show_hp_bar=False
    assert render(hud,rec,2300)==on[:-len(health)]
    assert 'health' not in hud.bounds


def test_default_glyph_override_preserves_all_native_geometry(bank, skins):
    native,r1=make_hud(bank,folder=skins/'complete')
    default,r2=make_hud(bank,folder=skins/'complete',renderer_default_font_and_ranks=True)
    one,two=render(native,r1),render(default,r2)
    for name in ('score','accuracy','progress','keys','health'):
        assert native.bounds[name] == default.bounds[name]
    assert any((s.texture_key or '').startswith('sk_score_') for s in one)
    assert not any((s.texture_key or '').startswith('sk_score_') for s in two)
    assert any((s.texture_key or '').startswith('aseg_') for s in two)
    assert native.final_values() == default.final_values()


def test_font_extra_only_and_blank_rank_do_not_trigger_other_providers(bank, tmp_path):
    (tmp_path/'skin.ini').write_text('[Fonts]\nScorePrefix: sparse\n')
    Image.new('RGBA',(10,60),(0,0,0,0)).save(tmp_path/'sparse-percent.png')
    Image.new('RGBA',(80,80),(0,0,0,0)).save(tmp_path/'ranking-X-small.png')
    hud,rec=make_hud(bank,folder=tmp_path)
    keys={s.texture_key for s in render(hud,rec)}
    assert 'lg_1' in keys and 'sk_score_percent' in keys and 'lg_percent' not in keys
    assert 'sk_ranking-X-small' in keys
    assert not any((k or '').startswith('glyph_S') for k in keys)


@pytest.mark.parametrize('legacy', [False,True])
def test_grade_progress_four_combinations_and_extras(bank, skins, legacy):
    grade_slot=None
    for grade in (False,True):
        for progress in (False,True):
            hud,rec=make_hud(bank,folder=skins/'complete' if legacy else None,
                             show_grade=grade,show_progress=progress,
                             show_hit_counter=True,show_pp_counter=True,watermark_text='R3D')
            render(hud,rec)
            b=hud.bounds
            assert ('grade' in b)==grade
            assert ('progress' in b)==progress
            assert b['mods'].top > b['accuracy'].bottom
            assert b['hits'].top > b['mods'].bottom
            if grade:
                if grade_slot is not None:
                    assert b['grade']==grade_slot
                grade_slot=b['grade']
                for n in ('score','accuracy','progress','mods','hits','keys'):
                    if n in b: assert not b['grade'].intersects(b[n]), n
            if not legacy:
                assert b['pp'].top > b['score'].bottom
            # Each display flag suppresses just its own core component.
            hud.s.show_score=False
            render(hud,rec)
            assert 'score' not in hud.bounds and 'accuracy' not in hud.bounds
            assert ('grade' in hud.bounds)==grade
            assert ('progress' in hud.bounds)==progress


@pytest.mark.parametrize('resolution', [(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale', [.75,1,1.25])
@pytest.mark.parametrize('override', [False,True])
@pytest.mark.parametrize('skin', ['complete','empty'])
def test_legacy_grade_side_slot_preserves_mods_and_other_sprites(
        bank,skins,resolution,scale,override,skin):
    hud,rec=make_hud(bank,folder=skins/skin,renderer=Recorder(*resolution),
                     hud_scale=scale,renderer_default_font_and_ranks=override,
                     show_hit_counter=True)
    grade_span=[]
    original=hud._grade_extra
    def traced(out,t):
        start=len(out);original(out,t);grade_span[:]=[start,len(out)]
    hud._grade_extra=traced
    slot=None
    for progress in (False,True):
        for score in (False,True):
            hud.s.show_progress=progress
            hud.s.show_score=score
            hud.s.show_grade=False
            off=render(hud,rec,2300)
            mods=hud.bounds['mods']
            hud.s.show_grade=True
            on=render(hud,rec,2300)
            start,end=grade_span
            # Grade visibility cannot move the mod row, hit counts, keys,
            # readouts, health or any other sprite, even with readouts hidden.
            assert on[:start]+on[end:]==off
            assert hud.bounds['mods']==mods
            grade=hud.bounds['grade']
            if slot is not None:assert grade==slot
            slot=grade
            accuracy=hud._legacy_accuracy_bounds
            assert (grade.top+grade.bottom)/2==pytest.approx(
                (accuracy.top+accuracy.bottom)/2)
            reserved_pie_left=accuracy.left-(33+18)*scale
            assert reserved_pie_left-grade.right==pytest.approx(12*scale)
            assert 0<=grade.left<grade.right<=hud.ui_w_l
            assert 0<=grade.top<grade.bottom<=768
            for name in ('score','accuracy','progress','mods','hits','keys'):
                if name in hud.bounds:assert not grade.intersects(hud.bounds[name]),name


@pytest.mark.parametrize('channels', [2,4])
def test_legacy_key_top_fixed_for_two_four_held_released(bank, skins, channels):
    frames=_frames([(1000+i*100,100,100,1<<i) for i in range(channels)]+[(1500,100,100,0)])
    hud,rec=make_hud(bank,folder=skins/'empty',frames=frames)
    for time in (1050,1250,1600,1000):
        out=[]
        hud._legacy_key_overlay(out,time)
        bg=next(s for s in out if s.texture_key=='lg_io_bg')
        assert bg.y/hud.lk-bg.w/hud.lk/2==pytest.approx(384-64)
        keys=[s for s in out if s.texture_key=='lg_io_key']
        assert len(keys)==channels
        assert keys[0].y/hud.lk==pytest.approx(384-64+7+46/2)
        if time==1600:
            assert hud.keys.state_at(time)==(0, (1,1,1,1) if channels==4 else (0,0,1,1))
        if time==1000:
            assert hud.keys.state_at(time)==(4, (0,0,1,0))


def test_transparent_assets_are_not_replaced_and_animation_is_preserved(bank, skins):
    hud,rec=make_hud(bank,folder=skins/'blank')
    sprites=render(hud,rec)
    keys={s.texture_key for s in sprites}
    assert 'sk_inputoverlay-key' in keys and 'lg_io_key' not in keys
    assert 'sk_inputoverlay-background' in keys and 'lg_io_bg' not in keys
    assert 'sk_scorebar-bg' in keys and 'lg_scorebar_bg' not in keys
    assert 'sk_scorebar-colour' in keys and 'lg_scorebar_colour' not in keys
    assert np.max(rec.uploaded['sk_inputoverlay-key'][...,3])==0
    # The original animation API selects frame 1; its native fill rectangle
    # retains the first-frame size, matching LegacyHealthDisplay's mask.
    out=[]
    hud._legacy_health(out,5050)
    fill=next(s for s in out if s.texture_key=='sk_scorebar-colour_f1')
    assert fill.h/hud.lk==16


def test_stateless_seek_and_individual_flags(bank, skins):
    hud,rec=make_hud(bank,folder=skins/'digits-only',show_hit_counter=True,
                     show_pp_counter=True,show_aim_error_meter=True,hud_opacity=.4)
    first=render(hud,rec,2300)
    values=hud.final_values()
    for t in (500,5000,1200,4000): render(hud,rec,t)
    assert first==render(hud,rec,2300)
    assert values==hud.final_values()
    for flag,bound in [('show_combo','combo'),('show_key_overlay','keys'),
                       ('show_hp_bar','health'),('show_hit_counter','hits'),
                       ('show_pp_counter','pp'),('show_hit_error_meter','hit_error')]:
        setattr(hud.s,flag,False)
        render(hud,rec)
        assert bound not in hud.bounds
        setattr(hud.s,flag,True)
    hud.op=0
    assert render(hud,rec)==[] and hud.bounds=={}


def test_actual_egl_720p_and_metal_draw_command_contract(skins):
    renderer=SpriteRenderer(1280,720)
    try:
        bank=TextureBank(renderer)
        hud,_=make_hud(bank,folder=skins/'digits-only',renderer=renderer)
        renderer.begin((0,0,0))
        actual=[]
        original=renderer.draw
        def spy(sprites):
            actual.extend(sprites)
            original(sprites)
        renderer.draw=spy
        hud.draw(5000)
        pixels=renderer.read_rgb()
        assert pixels.max()>0 and pixels.shape==(720,1280,3)
        for name in ('score','accuracy','progress'):
            r=hud.bounds[name]
            x0,y0=math.floor(r.left*hud.lk),math.floor(r.top*hud.lk)
            x1,y1=math.ceil(r.right*hud.lk),math.ceil(r.bottom*hud.lk)
            assert pixels[y0:y1,x0:x1].max()>0, name
        # Execute the real Metal draw serializer on this same HUD batch.
        # Linux can verify its command contract, not native Metal pixels.
        path=Path(__file__).resolve().parents[1]/'osu_std_renderer/render/metal/renderer.py'
        cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='MetalSpriteRenderer')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='draw')
        ns=dict(np=np,perf=perf,plan_texture_batches=plan_texture_batches,_UNITS=16)
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),ns)
        keys={s.texture_key for s in actual if s.texture_key is not None}
        rec=SimpleNamespace(post_xform=None,_tex={k:i+1 for i,k in enumerate(keys)},_white=0,_scene=[])
        ns['draw'](rec,actual)
        assert sum(len(op[1]) for op in rec._scene)==len(actual)
        assert all(np.isfinite(op[1]).all() for op in rec._scene)
        assert not any(s.texture_key=='argon_wedge' for s in actual)
    finally:
        renderer.release()
