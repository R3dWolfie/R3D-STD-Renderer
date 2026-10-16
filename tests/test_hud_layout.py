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

    def draw_hit_error_meter(self, state, opacity=1.0):
        self.hit_error_frame = state
        self.hit_error_opacity = opacity


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
        hud.ui_w_l-3)
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
            forced.ui_w_l-3)


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
        hud.ui_w_l-3)


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
    # Removing accuracy calibration retains its native margin/gap; the
    # owner-selected shared alignment still applies to both counters.
    hud.layout=dataclasses.replace(hud.layout, legacy_accuracy_offset=(0.0,0.0))
    sprites=render(hud,rec)
    score,acc,pie=(hud.bounds[n] for n in ('score','accuracy','progress'))
    assert score.right==pytest.approx(acc.right)
    assert acc.right==pytest.approx(hud.ui_w_l-17*scale)
    assert acc.top-score.bottom==pytest.approx(9*scale)
    # Stable's constructor reference is fixed, even for fonts whose native
    # overlap yields unusual/off-viewport advances. It is not current ink.
    assert pie.right-pie.left==pytest.approx(42*scale)
    anchor=tuple((pie.left+pie.right)/2 if i==0 else (pie.top+pie.bottom)/2 for i in (0,1))
    hud.data.acc_at=lambda *args: .9768
    render(hud,rec)
    assert tuple(((hud.bounds['progress'].left+hud.bounds['progress'].right)/2,
                  (hud.bounds['progress'].top+hud.bounds['progress'].bottom)/2))==pytest.approx(anchor)
    for r in (score,acc):
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
def test_shared_right_calibration_preserves_accuracy_vertical_and_glyph_metrics(
        bank, skins, resolution, scale, override):
    hud,rec=make_hud(bank,folder=skins/'complete',renderer=Recorder(*resolution),
                     hud_scale=scale,renderer_default_font_and_ranks=override,
                     show_progress=False)
    layout=hud.layout
    hud.layout=dataclasses.replace(layout,legacy_accuracy_offset=(0.0,0.0))
    before=[]
    hud._legacy_score_block(before,2300)
    original=dict(hud.bounds)
    hud.layout=layout
    after=[]
    hud._legacy_score_block(after,2300)
    assert len(before)==len(after)
    for old,new in zip(before,after):
        score=old.y+old.h/2 <= original['score'].bottom*hud.lk+1e-6
        assert new.x-old.x==pytest.approx(14*scale*resolution[1]/768)
        assert new.y-old.y==pytest.approx((0 if score else -4)*scale*resolution[1]/768)
        assert dataclasses.replace(new,x=old.x,y=old.y)==old
    assert hud.bounds['score'].right==pytest.approx(hud.bounds['accuracy'].right)
    assert hud.bounds['accuracy'].right==pytest.approx(hud.ui_w_l-3*scale)
    assert hud.bounds['score'].top==0
    assert hud.bounds['score'].bottom==original['score'].bottom
    assert hud.bounds['accuracy'].top-hud.bounds['score'].bottom==pytest.approx(5*scale)
    assert ARGON.legacy_accuracy_offset==(0,0)
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
    # fixed as accuracy grows; stable's pie retains its constructor anchor.
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
        # Historic 00.00% reference may overlap the extra 100% digit.
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
    assert full['progress']==fraction['progress']
    assert full['grade']==fraction['grade']


SCORE_GROWTH_CASES = [(0,'000000'), (1,'000001'), (999999,'999999'),
                      (1000000,'1000000'), (1001273,'1001273'),
                      (9999999,'9999999'), (10000000,'10000000')]


@pytest.fixture(scope='module')
def blank_score_skin(tmp_path_factory):
    folder=tmp_path_factory.mktemp('blank-score')
    (folder/'skin.ini').write_text('[Fonts]\nScorePrefix: transparent\nScoreOverlap: 3\n')
    for i in range(10):
        factor=2 if i%2 else 1
        Image.new('RGBA',(30*factor,48*factor)).save(
            folder/f'transparent-{i}{"@2x" if factor==2 else ""}.png')
    return folder


def growth_hud(bank,skins,blank_score_skin,skin,resolution,scale):
    if skin=='owner':
        folder=Path(__file__).resolve().parents[1]/'test-render/skin'
        if not folder.is_dir():pytest.skip('owner extracted skin is a local QA input')
    elif skin=='blank-digits':folder=blank_score_skin
    elif skin in ('asset-fallback','font-override'):folder=skins/'empty' if skin=='asset-fallback' else skins/'complete'
    elif skin=='legacy-defaults':folder=None
    else:folder=skins/skin
    hud,rec=make_hud(bank,folder=folder,renderer=Recorder(*resolution),
                     hud_scale=scale,legacy_defaults=skin=='legacy-defaults',
                     renderer_default_font_and_ranks=skin=='font-override',
                     show_hit_counter=True)
    if skin=='asset-fallback':
        hud.sk=SkinElements(Skin(skin_dir=folder,fallback_dir=skins/'digits-only'),rec)
        assert hud.sk.skin.find_texture('numeric/score-5').source.name=='FALLBACK'
    hud._pin=1
    return hud,rec


def score_sprites(hud,sprites):
    rect=hud.bounds['score']
    keys={g[0] for g in hud._legacy_glyphs('score','0123456789')[0]}
    return [s for s in sprites if s.y-s.h/2>=rect.top*hud.lk-1e-6
            and s.y+s.h/2<=rect.bottom*hud.lk+1e-6
            and s.texture_key in keys]


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale',[.75,1,1.25])
@pytest.mark.parametrize('skin',['owner','complete','digits-only','empty','blank-digits',
                                 'asset-fallback','legacy-defaults','font-override'])
def test_legacy_minimum_score_grows_left_with_native_glyphs_and_shared_right(
        bank,skins,blank_score_skin,resolution,scale,skin):
    hud,rec=growth_hud(bank,skins,blank_score_skin,skin,resolution,scale)
    states={}
    fixed_right=resolution[0]-3*scale*resolution[1]/768
    stable_bounds=None
    for value,text in SCORE_GROWTH_CASES+[(1999999,'1999999')]:
        hud.data.score_at=lambda *args,v=value:v
        sprites=render(hud,rec,5000)
        digits=score_sprites(hud,sprites)
        expected,cell,_,overlap=hud._legacy_glyphs('score',text)
        assert [s.texture_key for s in digits]==[g[0] for g in expected]
        assert len(digits)==len(text)
        score,acc=hud.bounds['score'],hud.bounds['accuracy']
        assert score.right*hud.lk==pytest.approx(fixed_right)
        assert acc.right==pytest.approx(score.right)
        for sp,(_,w,h,_) in zip(digits,expected):
            # No safety fit at ordinary six, seven or eight-digit lengths.
            assert (sp.w,sp.h)==pytest.approx((w*.96*scale*hud.lk,h*.96*scale*hud.lk))
            assert sp.uv_off==(0,0) and sp.uv_scale==(1,1)
            assert sp.x-sp.w/2>=0 and sp.x+sp.w/2<=resolution[0]
        other={k:v for k,v in hud.bounds.items() if k!='score'}
        if stable_bounds is not None:assert other==stable_bounds
        stable_bounds=other
        # The stable overlay includes glow padding; its bounding quad may
        # cross score ink. Other approved components retain collision checks.
        for name in ('accuracy','grade','mods','hits','keys'):
            assert not score.intersects(hud.bounds[name]),name
        states[text]=(digits,score)
    # Same suffix glyphs remain byte-for-byte the same sprites. The seventh
    # leading character alone expands left by the fixed '5' advance/overlap.
    for short,long in [('000000','1000000'),('999999','1999999')]:
        six,six_rect=states[short];seven,seven_rect=states[long]
        for old,new in zip(six,seven[1:]):
            assert new.x==pytest.approx(old.x,abs=1e-9)
            assert dataclasses.replace(new,x=old.x)==old
        _,cell,_,overlap=hud._legacy_glyphs('score',long)
        assert six_rect.left-seven_rect.left==pytest.approx(max(cell-overlap,0)*.96*scale)
    if skin=='blank-digits':
        assert all(not rec.uploaded[f'sk_score_{i}'][:,:,3].any() for i in range(10))
    if skin in ('complete','digits-only','asset-fallback','blank-digits'):
        assert hud.sk.score_digit_sizes['5']==(30,48)  # mixed @2x normalises
    if skin=='owner':
        assert hud.sk.info.score_prefix=='Assets/score/score'
        assert hud.sk.info.score_overlap==5


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale',[.75,1,1.25])
def test_legacy_rolling_score_crosses_million_without_anchor_or_scale_jump(
        bank,skins,blank_score_skin,resolution,scale):
    from osu_std_renderer.render.hud import HudData,LEGACY_SCORE_ROLL_MS
    from osu_std_renderer.ruleset import JudgmentEvent,JudgmentKind
    hud,rec=growth_hud(bank,skins,blank_score_skin,'owner',resolution,scale)
    events=[JudgmentEvent(1000,JudgmentKind.HIT300,0,100,100,1,999999,1),
            JudgmentEvent(3000,JudgmentKind.HIT300,1,200,100,2,1001273,1)]
    hud.data=HudData(SimpleNamespace(events=events,verdicts={},combo_timeline=[],
                                     final_max_combo=2,lazer=True))
    sizes={};lengths=[]
    for t in (2999,3000,3000.1,3000.2,3016.6666667,3500,4000):
        value=round(hud.data.score_at(t,LEGACY_SCORE_ROLL_MS))
        digits=score_sprites(hud,render(hud,rec,t))
        text=f'{value:06d}'
        assert [s.texture_key for s in digits]==[f'sk_score_{ch}' for ch in text]
        assert hud.bounds['score'].right==pytest.approx(hud.ui_w_l-3*scale)
        assert hud.bounds['accuracy'].right==pytest.approx(hud.bounds['score'].right)
        assert digits[-1].x+digits[-1].w/2==pytest.approx(resolution[0]-3*scale*hud.lk)
        for sp in digits:
            properties=(sp.y,sp.w,sp.h,sp.uv_off,sp.uv_scale)
            if sp.texture_key in sizes:assert properties==sizes[sp.texture_key]
            sizes[sp.texture_key]=properties
        lengths.append(len(digits))
    assert lengths[:3]==[6,6,6] and lengths[3:]==[7,7,7,7]
    assert text=='1001273'
    # The query remains deterministic after out-of-order seeks.
    assert round(hud.data.score_at(3000,LEGACY_SCORE_ROLL_MS))==999999


@pytest.mark.parametrize('overlap',[-8,3])
def test_legacy_growth_retains_nonuniform_glyphs_punctuation_and_overlap(bank,tmp_path,overlap):
    (tmp_path/'skin.ini').write_text(f'[Fonts]\nScorePrefix: native\nScoreOverlap: {overlap}\n')
    for i in range(10):
        factor=2 if i%2 else 1
        Image.new('RGBA',((25+i)*factor,48*factor),'white').save(
            tmp_path/f'native-{i}{"@2x" if factor==2 else ""}.png')
    Image.new('RGBA',(9,48),'white').save(tmp_path/'native-dot.png')
    Image.new('RGBA',(15,48),'white').save(tmp_path/'native-percent.png')
    hud,rec=make_hud(bank,folder=tmp_path)
    hud._pin=1
    states=[]
    for n in (0,1000000):
        hud.data.score_at=lambda *args,v=n:v
        states.append(score_sprites(hud,render(hud,rec)))
    for old,new in zip(states[0],states[1][1:]):
        assert new.x==pytest.approx(old.x,abs=1e-9)
        assert dataclasses.replace(new,x=old.x)==old
    delta=states[0][0].x-states[1][0].x
    assert delta==pytest.approx((30-overlap)*.96*hud.lk)
    glyphs,cell,_,actual_overlap=hud._legacy_glyphs('score','5.5%')
    advances,_,_,_=hud._legacy_metrics(glyphs,cell,actual_overlap)
    assert advances==[30,9,30,15]  # punctuation excluded from digit cells


def test_legacy_pathological_long_score_fits_four_three_without_losing_digits(bank,tmp_path):
    (tmp_path/'skin.ini').write_text('[Fonts]\nScorePrefix: huge\nScoreOverlap: -12\n')
    for i in range(10):Image.new('RGBA',(400+i,500+i),'white').save(tmp_path/f'huge-{i}.png')
    hud,rec=make_hud(bank,folder=tmp_path,renderer=Recorder(1024,768))
    hud._pin=1
    hud.data.score_at=lambda *args:1234567890123456
    digits=score_sprites(hud,render(hud,rec))
    text='1234567890123456'
    assert [s.texture_key for s in digits]==[f'sk_score_{ch}' for ch in text]
    assert len(digits)==16
    assert hud.bounds['score'].right==pytest.approx(hud.bounds['accuracy'].right)
    assert hud.bounds['score'].left>=8-1e-6 and hud.bounds['score'].bottom<=180+1e-6
    assert all(s.w<400*.96 for s in digits)  # deliberate viewport fitting


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
            assert (grade.left+grade.right)/2==pytest.approx(hud._legacy_grade_anchor[0])
            assert (grade.top+grade.bottom)/2==pytest.approx(hud._legacy_grade_anchor[1])
            if scale<=1:
                assert hud._legacy_grade_anchor==pytest.approx((hud.ui_w_l-31,247))
                assert grade.top>=hud.bounds['hits'].bottom+8*scale
                assert grade.bottom+8*scale<=hud.bounds['keys'].top
            assert 0<=grade.left<grade.right<=hud.ui_w_l
            assert 0<=grade.top<grade.bottom<=768
            for name in ('score','accuracy','progress','mods','hits','keys'):
                if name in hud.bounds:assert not grade.intersects(hud.bounds[name]),name


@pytest.mark.parametrize('resolution', [(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale', [.75,1,1.25])
@pytest.mark.parametrize('skin', ['varied-art','empty','owner'])
def test_grade_pivot_is_independent_of_accuracy_score_and_rank_art(
        bank, skins, tmp_path, resolution, scale, skin):
    if skin == 'owner':
        folder=Path(__file__).resolve().parents[1]/'test-render/skin'
        if not folder.is_dir():pytest.skip('owner extracted skin is a local QA input')
    elif skin == 'varied-art':
        folder=fixtures(tmp_path)/'complete'
        for rank,size in [('X',(60,38)),('S',(90,28)),('A',(25,50)),
                          ('B',(100,120)),('C',(12,16)),('D',(40,32))]:
            Image.new('RGBA',size,'white').save(folder/f'ranking-{rank}-small.png')
    else:
        folder=skins/'empty'
    hud,rec=make_hud(bank,folder=folder,renderer=Recorder(*resolution),
                     hud_scale=scale,show_hit_counter=True)
    pivot=None
    states=[(1,'SS'),(.9999,'S'),(.9768,'S'),(.9,'A'),(.8,'B'),(.7,'C'),(.0699,'D')]
    for progress in (False,True):
        hud.s.show_progress=progress
        for i,(acc,rank) in enumerate(states):
            # This isolates layout from scoring: coherent numeric/rank targets
            # change while fonts, profile and resolution remain identical.
            hud.data.acc_at=lambda *args,a=acc:a
            hud.data.grade_at=lambda t,g=rank:g
            hud.data.score_at=lambda *args,n=i:n*1234567+3473
            hud.s.show_grade=False
            without=render(hud,rec,2300)
            native=dict(hud.bounds)
            hud.s.show_grade=True
            with_grade=render(hud,rec,2300)
            if pivot is None:pivot=hud._legacy_grade_anchor
            assert hud._legacy_grade_anchor==pytest.approx(pivot)
            grade=hud.bounds['grade']
            assert (grade.left+grade.right)/2==pytest.approx(pivot[0])
            assert (grade.top+grade.bottom)/2==pytest.approx(pivot[1])
            assert 0<=grade.left<grade.right<=hud.ui_w_l
            assert 0<=grade.top<grade.bottom<=768
            assert {k:v for k,v in hud.bounds.items() if k!='grade'}==native
            for name,rect in native.items():
                if name in ('score','accuracy','progress','mods','hits','keys'):
                    assert not grade.intersects(rect),name
            # A visibility toggle adds only grade sprites. Existing opacity,
            # textures, dimensions, UVs, native pie and other components persist.
            added=[sp for sp in with_grade if sp not in without]
            assert added and [sp for sp in with_grade if sp not in added]==without
            if hud.sk.has('ranking-X-small' if rank=='SS' else f'ranking-{rank}-small'):
                sprite=added[0]
                element='ranking-X-small' if rank=='SS' else f'ranking-{rank}-small'
                w,h=hud.sk.size[element]
                assert sprite.texture_key==f'sk_{element}'
                assert sprite.w/sprite.h==pytest.approx(w/h)
    render(hud,rec,999)
    assert 'grade' not in hud.bounds


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale',[.75,1,1.25])
@pytest.mark.parametrize('hits',[False,True])
@pytest.mark.parametrize('mods',[(),('DT',),('HR','DT','HD'),
                                 ('HD','HR','DT','FL','CL','DA','MR','TC')])
def test_legacy_grade_clears_counter_mods_and_keys_without_moving_them(
        bank,skins,resolution,scale,hits,mods):
    hud,rec=make_hud(bank,folder=skins/'complete',renderer=Recorder(*resolution),
                     hud_scale=scale,show_hit_counter=hits)
    hud._mod_acrs=build_mod_pills(0,mods)
    pivot=None
    for rank,counts in [('SS',(0,0,0,0)),('A',(3,0,0,0)),('D',(0,0,0,3))]:
        hud.data.grade_at=lambda t,g=rank:g
        hud.data.counts_at=lambda t,c=counts:c
        hud.s.show_grade=False
        off=render(hud,rec,2300)
        native=dict(hud.bounds)
        hud.s.show_grade=True
        on=render(hud,rec,2300)
        current=hud._legacy_grade_anchor
        if pivot is None:pivot=current
        assert current==pytest.approx(pivot)
        grade=hud.bounds['grade']
        assert (grade.left+grade.right)/2==pytest.approx(current[0])
        assert (grade.top+grade.bottom)/2==pytest.approx(current[1])
        assert {k:v for k,v in hud.bounds.items() if k!='grade'}==native
        added=[sp for sp in on if sp not in off]
        assert [sp for sp in on if sp not in added]==off
        for name in ('score','accuracy','progress','mods','hits','keys'):
            if name in native:assert not grade.intersects(native[name]),name
        if scale<=1 and len(mods)<=3:
            expected_y=247 if mods else 247-48*scale
            assert current==pytest.approx((hud.ui_w_l-31,expected_y))
            if hits:assert grade.top>=native['hits'].bottom+8*scale
            assert grade.bottom+8*scale<=native['keys'].top


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('scale',[.75,1])
@pytest.mark.parametrize('skin',['complete','empty'])
@pytest.mark.parametrize('override',[False,True])
def test_legacy_grade_tracks_visible_mod_counter_flow(
        bank,skins,resolution,scale,skin,override):
    hud,rec=make_hud(bank,folder=skins/skin,renderer=Recorder(*resolution),
                     hud_scale=scale,renderer_default_font_and_ranks=override)
    states={}
    for name,mods,visible in [('none',(),True),('single',('DT',),True),
                             ('three',('HR','DT','HD'),True),
                             ('hidden',('DT',),False)]:
        hud._mod_acrs=build_mod_pills(0,mods)
        hud.s.show_mods=visible
        # Switch configurations on one HUD to exercise cache invalidation;
        # hiding the counter itself must not move the grade's configured slot.
        hud.s.show_hit_counter=True
        render(hud,rec,2300)
        states[name]=(hud.bounds['grade'],hud.bounds['hits'])
        hud.s.show_hit_counter=False
        render(hud,rec,2300)
        assert hud.bounds['grade']==states[name][0]
        assert not hud.bounds['grade'].intersects(hud.bounds['keys'])
    grade0,hits0=states['none']
    for name in ('single','three'):
        grade,hits=states[name]
        # Test the dependency against the existing counter geometry, rather
        # than just replacing the old fixed-Y expectation with a new number.
        counter_shift=hits.top-hits0.top
        assert counter_shift==pytest.approx(48*scale)
        assert grade.top-grade0.top==pytest.approx(counter_shift)
        assert grade.bottom-hits.bottom==pytest.approx(grade0.bottom-hits0.bottom)
        assert (grade.left,grade.right)==(grade0.left,grade0.right)
    assert states['hidden']==states['none']


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
def test_owner_default_grade_anchor_is_same_with_counter_on_or_off(bank,resolution):
    folder=Path(__file__).resolve().parents[1]/'test-render/skin'
    if not folder.is_dir():pytest.skip('owner skin is a local QA input')
    hud,rec=make_hud(bank,folder=folder,renderer=Recorder(*resolution))
    hud._mod_acrs=build_mod_pills(64)
    hud.data.grade_at=lambda t:'S'
    before=[]
    hud._grade_badge(before,2300,100,100)
    for hits in (False,True):
        hud.s.show_hit_counter=hits
        sprites=render(hud,rec,2300)
        assert hud._legacy_grade_anchor==pytest.approx((hud.ui_w_l-31,247))
        rank=next(sp for sp in sprites if sp.texture_key=='sk_ranking-S-small')
        assert dataclasses.replace(rank,x=before[0].x,y=before[0].y)==before[0]
        if hits:
            assert hud.bounds['keys'].top-hud.bounds['hits'].bottom==pytest.approx(98.04)
            assert hud.bounds['grade'].top-hud.bounds['hits'].bottom==pytest.approx(9.04)


@pytest.mark.parametrize('skin', ['complete','owner'])
def test_reconciled_slider_accuracy_selects_matching_rank_art(bank, skins, skin):
    from tests.test_ruleset import _multi_slider, _perfect_slider_frames, _lazer_stats_meta
    from osu_std_renderer.ruleset import StdRuleset
    from osu_std_renderer.render.hud import HudData
    folder=skins/'complete' if skin=='complete' else Path(__file__).resolve().parents[1]/'test-render/skin'
    if not folder.is_dir():pytest.skip('owner extracted skin is a local QA input')
    bm=_multi_slider(8);frames=_perfect_slider_frames(bm)
    meta=_lazer_stats_meta(8,large_tick_hit=14,large_tick_miss=2,
                          slider_tail_hit=5,max_large_tick=16,max_slider_tail=8,max_combo=24)
    sim=StdRuleset(bm,frames,meta).run()
    rec=Recorder();sk=SkinElements(Skin(skin_dir=folder),rec)
    hud=StdHud(rec,bank,StdRenderSettings(skin_dir=folder),sim,frames,bm,skin_elems=sk)
    assert sim.final_counts==(8,0,0,0) and HudData(sim).final_acc()==.875
    assert hud.data.grade_at(100000)=='B'
    out=[];hud._grade_badge(out,100000,1230,74)
    assert 'sk_ranking-X-small' not in {sp.texture_key for sp in out}
    if skin=='owner':
        assert len(out)==1 and out[0].texture_key=='sk_ranking-B-small'
        # Also check the packet's 97.68% S case through the popup-only
        # compatibility input, where the old independent count authority failed.
        sim.accuracy_timeline=()
        sim.events[-1]=dataclasses.replace(sim.events[-1],acc_after=.9768)
        hud.data=HudData(sim);out=[];hud._grade_badge(out,100000,1230,74)
        assert len(out)==1 and out[0].texture_key=='sk_ranking-S-small'
    else:
        assert out and all(not (sp.texture_key or '').startswith('sk_ranking') for sp in out)


def test_break_overlay_uses_same_nested_raw_accuracy_and_rank(bank, skins):
    from tests.test_ruleset import _multi_slider, _frames
    from osu_std_renderer.ruleset import StdRuleset
    from osu_std_renderer.replay.replay import KEY_K1
    from osu_std_renderer.render.hud import HudData
    bm=_multi_slider(2)
    sim=StdRuleset(bm,_frames([(1000,256,192,KEY_K1),(1010,256,192,0)]),
                   lazer=True,reconcile=False).run()
    point=next(p for p in sim.accuracy_timeline if p.kind=='tick')
    hud,_=make_hud(bank,folder=skins/'complete')
    hud.data=HudData(sim);calls=[]
    hud.break_overlay=SimpleNamespace(draw=lambda *args,**kwargs:calls.append((args,kwargs)))
    hud._draw_break_overlay(point.time_ms)
    assert calls[-1]==((point.time_ms,300/330,'A'),{'lazer_display':True})


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
