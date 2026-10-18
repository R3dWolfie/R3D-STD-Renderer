"""Spinner visuals + hit-lighting math (render/spinner.py): style
auto-detect, rotation accumulation (held-gated, wrap-safe, lerped),
approach scale, metre bars + SpinnerNoBlink, RPM window/cap, clear time,
prompt/lighting lifecycles — pure, no GL."""
from __future__ import annotations

import math

from osu_std_renderer.beatmap.objects.spinner import RPMS
from osu_std_renderer.render.spinner import (APPROACH_END_SCALE,
                                             APPROACH_START_SCALE,
                                             LIGHTING_FADE_MS,
                                             METRE_BLINK_PERIOD_MS,
                                             RPM_DISPLAY_CAP, SPIN_FADE_MS,
                                             SPIN_HOLD_MS, SpinnerTrack,
                                             clear_alpha_scale,
                                             detect_spinner_style,
                                             lighting_alpha_scale,
                                             metre_bar_count,
                                             required_rotations,
                                             spin_prompt_alpha,
                                             spinner_approach_scale,
                                             wants_lighting)
from osu_std_renderer.replay.replay import KEY_K1, StdFrame
from osu_std_renderer.ruleset import JudgmentKind


def _spin_frames(start: float, end: float, rps: float, *, held: bool = True,
                 step: float = 10.0, cx: float = 256.0, cy: float = 192.0,
                 radius: float = 100.0) -> list[StdFrame]:
    """Synthetic frames circling the centre at `rps` rev/s (clockwise in
    the y-down convention)."""
    frames = []
    t = start
    while t <= end + 1e-9:
        ang = 2.0 * math.pi * rps * (t - start) / 1000.0
        frames.append(StdFrame(int(t), cx + radius * math.cos(ang),
                               cy + radius * math.sin(ang),
                               KEY_K1 if held else 0))
        t += step
    return frames


# --- style auto-detect ----------------------------------------------------------

def test_style_autodetect():
    # spinner-background → OLD, even when new-style sprites also exist
    assert detect_spinner_style({"spinner-background", "spinner-top"}) == "old"
    assert detect_spinner_style(
        {"spinner-background", "spinner-metre", "spinner-circle"}) == "old"
    # no background but any new sprite → NEW
    for name in ("spinner-glow", "spinner-bottom", "spinner-top",
                 "spinner-middle2", "spinner-middle"):
        assert detect_spinner_style({name, "hitcircle"}) == "new"
    # neither → procedural
    assert detect_spinner_style(set()) == "none"
    assert detect_spinner_style(
        {"hitcircle", "spinner-approachcircle", "spinner-rpm"}) == "none"


# --- rotation accumulation --------------------------------------------------------

def test_rotation_accumulates_signed_and_abs():
    # 2 rev/s for 1 s → 2 full turns
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0), 0, 1000,
                                  required_spins=4.0)
    assert abs(tr.rotation(1000) - 4.0 * math.pi) < 0.05
    assert abs(tr.progress(1000) - 0.5) < 0.01     # 2 of 4 required spins
    # halfway through: one turn
    assert abs(tr.rotation(500) - 2.0 * math.pi) < 0.05


def test_rotation_key_gated_and_wrap_safe():
    # not holding → the cursor circles but nothing accumulates
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0, held=False),
                                  0, 1000, required_spins=4.0)
    assert tr.rotation(1000) == 0.0
    assert tr.progress(1000) == 0.0
    # coarse frames stepping ~172.8° per frame still accumulate the short
    # way around (atan2 wrap): 0.48 rev/frame * 25 frames ≈ 12 rev
    frames = _spin_frames(0, 1000, 12.0, step=40.0)
    tr2 = SpinnerTrack.from_frames(frames, 0, 1000, required_spins=20.0)
    assert abs(tr2.rotation(1000) - 12.0 * 2.0 * math.pi) < 0.6


def test_rotation_direction_is_signed():
    frames = [StdFrame(t, 256.0 + 100.0 * math.cos(-2.0 * math.pi * t / 1000.0),
                       192.0 + 100.0 * math.sin(-2.0 * math.pi * t / 1000.0),
                       KEY_K1) for t in range(0, 1001, 10)]
    tr = SpinnerTrack.from_frames(frames, 0, 1000, required_spins=2.0)
    assert tr.rotation(1000) < -6.0                # counter-rotation → negative
    assert tr.progress(1000) > 0.45                # progress uses |delta|


def test_rotation_lerps_between_frames():
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 1.0, step=100.0),
                                  0, 1000, required_spins=1.0)
    r50 = tr.rotation(50)                          # halfway into frame 0→1
    assert 0.0 < r50 < tr.rotation(100)
    assert abs(r50 - tr.rotation(100) / 2.0) < 1e-9


def test_auto_spin_fills_requirement():
    # OD-derived requirement below the 477 RPM auto rate → clears
    dur = 1193.0
    spins = required_rotations(7.0, dur)           # Ohayou: OD9 → ratio 7
    tr = SpinnerTrack.auto(95554.0, 95554.0 + dur, spins)
    assert tr.progress(95554.0 + dur) > 1.0
    ct = tr.clear_time()
    assert ct is not None and 95554.0 < ct < 95554.0 + dur
    assert abs(tr.progress(ct) - 1.0) < 1e-6


def test_clear_time_interpolated_and_none_when_short():
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0), 0, 1000,
                                  required_spins=1.0)   # 2 spins vs 1 needed
    ct = tr.clear_time()
    assert ct is not None and abs(ct - 500.0) < 10.0
    tr2 = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0), 0, 1000,
                                   required_spins=5.0)  # under-spun
    assert tr2.clear_time() is None


# --- approach scale ----------------------------------------------------------------

def test_spinner_approach_scale():
    assert spinner_approach_scale(-500.0, 0.0, 1000.0) == APPROACH_START_SCALE
    assert spinner_approach_scale(0.0, 0.0, 1000.0) == APPROACH_START_SCALE
    mid = spinner_approach_scale(500.0, 0.0, 1000.0)
    assert abs(mid - (APPROACH_START_SCALE + APPROACH_END_SCALE) / 2.0) < 1e-9
    assert abs(spinner_approach_scale(1000.0, 0.0, 1000.0)
               - APPROACH_END_SCALE) < 1e-9
    assert spinner_approach_scale(1001.0, 0.0, 1000.0) is None


# --- metre bars ---------------------------------------------------------------------

def test_metre_bar_count_quantized_and_capped():
    on = 0.0                                    # blink-on phase (t//30 even)
    off = 9*METRE_BLINK_PERIOD_MS               # 99% blink-off phase
    assert metre_bar_count(0.0, True, on) == 0
    assert metre_bar_count(0.55, True, on) == 5
    assert metre_bar_count(0.999, True, on) == 9   # capped at 99%
    assert metre_bar_count(1.0, True, on) == 10    # cleared pins full
    assert metre_bar_count(1.0, False, off) == 9   # blinking persists at100%


def test_metre_blink_flickers_next_bar():
    on, off = 0.0, 9*METRE_BLINK_PERIOD_MS
    assert metre_bar_count(0.55, False, on) == 6   # next bar blinks in…
    assert metre_bar_count(0.55, False, off) == 5  # …and out
    assert metre_bar_count(0.55, True, on) == 5    # SpinnerNoBlink → steady
    assert metre_bar_count(0.0, False, on) == 0    # zero fractional remainder


# --- RPM ------------------------------------------------------------------------------

def test_rpm_from_rate_and_cap():
    # 3 rev/s → 180 RPM
    tr = SpinnerTrack.from_frames(_spin_frames(0, 2000, 3.0), 0, 2000,
                                  required_spins=10.0)
    assert abs(tr.rpm(1500) - 180.0) < 6.0
    assert tr.rpm(0) == 0.0
    # the auto-spin constant sits exactly at the display cap
    auto = SpinnerTrack.auto(0, 2000, 10.0)
    assert abs(auto.rpm(1000) - RPM_DISPLAY_CAP) < 0.5
    assert auto.rpm(1000) <= RPM_DISPLAY_CAP
    # RPMS constant really is 477 RPM
    assert abs(RPMS * 60000.0 - RPM_DISPLAY_CAP) < 0.5


# --- prompts ------------------------------------------------------------------------

def test_spin_prompt_and_clear_lifecycle():
    assert spin_prompt_alpha(-400.0, 0.0) == 1.0          # approach: solid
    assert spin_prompt_alpha(SPIN_HOLD_MS, 0.0) == 1.0
    assert 0.0 < spin_prompt_alpha(SPIN_HOLD_MS + SPIN_FADE_MS / 2.0, 0.0) < 1.0
    assert spin_prompt_alpha(SPIN_HOLD_MS + SPIN_FADE_MS + 1.0, 0.0) == 0.0

    assert clear_alpha_scale(100.0, None) is None          # never cleared
    assert clear_alpha_scale(99.0, 100.0) is None          # not yet
    a0, s0 = clear_alpha_scale(100.0, 100.0)
    assert a0 == 0.0 and abs(s0 - 1.6) < 1e-9              # pops in at 1.6×
    a1, s1 = clear_alpha_scale(1000.0, 100.0)
    assert a1 == 1.0 and abs(s1 - 1.0) < 1e-9              # settled


# --- hit lighting ---------------------------------------------------------------------

def test_lighting_lifecycle_and_gating():
    assert lighting_alpha_scale(-1.0) is None
    a0, s0 = lighting_alpha_scale(0.0)
    assert a0 == 1.0 and abs(s0 - 0.8) < 1e-9
    a1, s1 = lighting_alpha_scale(LIGHTING_FADE_MS / 2.0)
    assert abs(a1 - 0.5) < 1e-9 and 0.8 < s1 < 1.2         # expanding out
    assert lighting_alpha_scale(LIGHTING_FADE_MS) is None  # gone

    assert wants_lighting(JudgmentKind.HIT300)
    assert wants_lighting(JudgmentKind.HIT100)
    assert wants_lighting(JudgmentKind.HIT50)
    assert not wants_lighting(JudgmentKind.MISS)           # never on a miss


# --- osu!lazer Legacy sprite/source regressions -------------------------------
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from osu_std_renderer.render.spinner import (
    legacy_spinner_alpha, legacy_spinner_scale, legacy_approach_scale,
    legacy_spin_prompt_alpha, legacy_clear_alpha_scale, legacy_bonus_popup_state)
from osu_std_renderer.render.scene import StdScene
from osu_std_renderer.render.playfield import PlayfieldCamera
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.textures import TextureBank
from osu_std_renderer.render.gl import SpriteRenderer
from osu_std_renderer.skin.skin import Skin, Source
from osu_std_renderer.skin.skin_ini import parse_skin_ini
from tests.test_hud import _map


class SpinnerRecorder:
    def __init__(self,w=1280,h=720):
        self.width,self.height=w,h;self.uploaded={};self.calls=[]
    def upload_texture(self,key,rgba,**kwargs):self.uploaded[key]=rgba
    write_texture=upload_texture
    def draw(self,sprites):self.calls.append(list(sprites))


def _visual_skin(folder,style='new',middle2='present',blank=False):
    folder.mkdir(exist_ok=True)
    (folder/'skin.ini').write_text('[General]\nSpinnerFadePlayfield: 0\n'
        '[Colours]\nSpinnerBackground: 150,180,220\n[Fonts]\nScoreOverlap: 3\n')
    sizes={'spinner-approachcircle':(200,220),'spinner-spin':(100,28),
           'spinner-clear':(80,20),'spinner-rpm':(96,56)}
    if style=='old':sizes.update({'spinner-background':(1024,750),
        'spinner-circle':(128,144),'spinner-metre':(1024,720)})
    else:
        sizes.update({'spinner-top':(80,100),'spinner-bottom':(100,120),
                      'spinner-middle':(30,40),'spinner-glow':(128,128)})
        if middle2!='missing':sizes['spinner-middle2']=(20,24)
    for name,(w,h) in sizes.items():
        alpha=0 if blank or name=='spinner-middle2' and middle2=='blank' else 255
        Image.new('RGBA',(w*2,h*2),(255,255,255,alpha)).save(folder/(name+'@2x.png'))
    for digit in range(10):
        Image.new('RGBA',(40,80),(255,255,255,0 if blank else 255)).save(folder/f'score-{digit}@2x.png')
    return folder


def _visual_scene(folder,renderer,**opts):
    bm=_map('256,192,2000,8,0,6000,0:0:0:0:\n');obj=bm.hit_objects[0]
    frames=_spin_frames(2000,6000,5)
    skin=SkinElements(Skin(skin_dir=folder),renderer)
    scene=StdScene(bm,frames,PlayfieldCamera(renderer.width,renderer.height),
                   renderer,None,TextureBank(renderer),skin_elems=skin,
                   spinner_fade_playfield=False,**opts)
    scene._advance(2000)
    track=SpinnerTrack.from_frames(frames,2000,6000,12)
    scene._spinner_tracks[id(obj)]=track
    scene._spinner_bonus[id(obj)]=scene._build_spinner_bonus(obj,track)
    return scene,obj,track


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
@pytest.mark.parametrize('middle2',['present','missing','blank'])
@pytest.mark.parametrize('progress',[0,.25,.5,.75,1,2])
def test_new_spinner_native_rotation_scale_and_layering(tmp_path,resolution,middle2,progress):
    rec=SpinnerRecorder(*resolution);scene,obj,track=_visual_scene(
        _visual_skin(tmp_path/'skin',middle2=middle2),rec)
    out=[];scene._new_style_sprites(out,4000,2000,6000,
        resolution[0]/2,resolution[1]/2,1.8,.7,progress)
    by_key={s.texture_key:s for s in out}
    top_rotation=.9 if middle2!='missing' else 1.8
    assert by_key['sk_spinner-top'].rotation==pytest.approx(top_rotation)
    assert by_key['sk_spinner-bottom'].rotation==pytest.approx(top_rotation/3)
    expected=['sk_spinner-bottom','sk_spinner-top']
    if middle2!='missing':
        assert by_key['sk_spinner-middle2'].rotation==1.8
        expected+=['sk_spinner-middle2']
    assert [s.texture_key for s in out]==expected+['sk_spinner-middle']
    p=min(progress,1);scale=.8+.2*(1-(1-p)**2)
    for s in out:
        native=scene.skin.size[s.texture_key.removeprefix('sk_')]
        assert (s.w,s.h)==pytest.approx(tuple(x*resolution[1]/768*scale for x in native))
        assert s.w/s.h==pytest.approx(native[0]/native[1])
        assert (s.x,s.y)==(resolution[0]/2,resolution[1]/2)
    assert by_key['sk_spinner-middle'].color==pytest.approx((1,.5,.5,.7))


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
def test_old_spinner_fixed_metre_crop_tint_and_painter_order(tmp_path,resolution):
    rec=SpinnerRecorder(*resolution);scene,_,_=_visual_scene(
        _visual_skin(tmp_path/'skin','old'),rec,spinner_no_blink=True)
    w,h=resolution;out=[];scene._old_style_sprites(out,3000,w/2,h/2,1.7,.55,.8)
    assert [s.texture_key for s in out]==['sk_spinner-background','sk_spinner-circle','sk_spinner-metre']
    bg,disc,metre=out;k=h/768;window=h/480
    assert bg.color==pytest.approx((150/255,180/255,220/255,.8))
    assert disc.rotation==1.7
    # Background height750 and metre height720 deliberately differ from692.
    # Crop is anchored to the window top-left, not either image's centre/foot.
    cut=346;visible=720-cut
    assert metre.uv_off==pytest.approx((0,cut/720))
    assert metre.uv_scale==pytest.approx((1,visible/720))
    assert metre.x-metre.w/2==pytest.approx(w/2-320*window)
    assert metre.y-metre.h/2==pytest.approx((29-8)*window+cut*k)
    assert (metre.w,metre.h)==pytest.approx((1024*k,visible*k))
    assert metre.y+metre.h/2==pytest.approx((29-8)*window+720*k)


@pytest.mark.parametrize('progress,upper_count',[(0,0),(.1,0),(.55,5),(.99,9),(1,9),(2,9)])
def test_metre_fractional_blink_duty_and_completion(progress,upper_count):
    bars=[metre_bar_count(progress,False,i*30) for i in range(10)]
    base=int(min(progress*100,99))//10
    assert all(b in (base,base+1) for b in bars)
    assert sum(b==base+1 for b in bars)==upper_count
    assert len({metre_bar_count(progress,True,i*30) for i in range(10)})==1
    if progress>=1:assert metre_bar_count(progress,True,0)==10


def test_legacy_source_fade_and_cue_curves():
    assert legacy_spinner_alpha(800,2000,6000,400)==0
    assert legacy_spinner_alpha(1600,2000,6000,400)==0
    assert legacy_spinner_alpha(1800,2000,6000,400)==.5
    assert legacy_spinner_alpha(2000,2000,6000,400)==1
    assert legacy_spinner_alpha(6120,2000,6000,400)==.5
    assert legacy_spinner_alpha(6240,2000,6000,400)==0
    assert legacy_approach_scale(1000,2000,6000)==1.86
    assert legacy_approach_scale(4000,2000,6000)==pytest.approx(.98)
    assert legacy_approach_scale(6120,2000,6000)==pytest.approx(.1)
    assert legacy_spin_prompt_alpha(1850,2000,6000,400)==.25
    assert legacy_spin_prompt_alpha(2350,2000,6000,400,2200)==.5
    assert legacy_spin_prompt_alpha(5850,2000,6000,400)==.375
    assert legacy_clear_alpha_scale(4300,4400,6000) is None
    assert legacy_clear_alpha_scale(4400,4400,6000)==(0,2)
    assert legacy_clear_alpha_scale(4640,4400,6000)==pytest.approx((.84,.8))
    assert legacy_clear_alpha_scale(4800,4400,6000)==(1,1)
    assert legacy_clear_alpha_scale(5975,4400,6000)==(.5,1)
    assert legacy_clear_alpha_scale(6000,4400,6000)==(0,1)
    # Late completion starts the absolute transform sequence end-400.
    assert legacy_clear_alpha_scale(5900,5900,6000)[1]==pytest.approx(.875)
    assert legacy_bonus_popup_state(0,False)==(1,2)
    assert legacy_bonus_popup_state(400,False)==pytest.approx((.25,1.46))
    assert legacy_bonus_popup_state(800,False) is None
    assert legacy_bonus_popup_state(0,True)==pytest.approx((1,2.24))
    assert legacy_bonus_popup_state(500,True) is None


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
def test_legacy_window_cues_rpm_and_blank_assets(tmp_path,resolution):
    rec=SpinnerRecorder(*resolution);scene,obj,track=_visual_scene(_visual_skin(tmp_path/'skin'),rec)
    w,h=resolution;window=h/480;out=[]
    scene._legacy_spinner_overlays(out,2000,track,2000,6000,w/2,h/2,1,id(obj))
    by_key={s.texture_key:s for s in out}
    assert by_key['sk_spinner-spin'].y==pytest.approx(356*window)
    rpm=by_key['sk_spinner-rpm']
    assert (rpm.x-rpm.w/2,rpm.y-rpm.h/2)==pytest.approx((w/2-87*window,437*window))
    digits=[s for s in out if s.texture_key.startswith('sk_score_')]
    assert max(s.x+s.w/2 for s in digits)==pytest.approx(w/2+80*window)
    assert min(s.y-s.h/2 for s in digits)==pytest.approx(440*window)
    assert all(s.h==pytest.approx(40*.9*h/768) for s in digits)
    # Native window art must ignore playfield camera scale/shift overrides.
    scene.cam=PlayfieldCamera(w,h,scale=1.25,shift_x=40,shift_y=25)
    rec.calls.clear();scene._draw_spinner(obj,4000)
    top=next(s for call in rec.calls for s in call if s.texture_key=='sk_spinner-top')
    assert (top.x,top.y)==(w/2,h/2)
    blank=_visual_skin(tmp_path/'blank',blank=True)
    bs,bo,bt=_visual_scene(blank,rec);rec.calls.clear();bs._draw_spinner(bo,3000)
    assert not any((s.texture_key or '').startswith('glyph_') for c in rec.calls for s in c)
    assert bs._skinned('spinner-spin') and bs._skinned('spinner-rpm')


def test_spinner_provider_and_background_configuration(tmp_path):
    active=tmp_path/'active';fallback=tmp_path/'fallback';active.mkdir();fallback.mkdir()
    Image.new('RGBA',(80,100),'white').save(active/'spinner-top.png')
    Image.new('RGBA',(1024,692),'white').save(fallback/'spinner-background.png')
    Image.new('RGBA',(20,24),(0,0,0,0)).save(fallback/'spinner-middle2.png')
    rec=SpinnerRecorder();skin=SkinElements(Skin(skin_dir=active,fallback_dir=fallback),rec)
    assert skin.skin.get_source('spinner-top')==Source.SKIN
    assert detect_spinner_style(skin.loaded,Source.FALLBACK,Source.SKIN)=='new'
    assert detect_spinner_style(skin.loaded,Source.SKIN,Source.FALLBACK)=='old'
    assert skin.has('spinner-middle2') and 'spinner-middle2' in skin.empty
    assert parse_skin_ini('').spinner_background==(100,100,100)
    assert parse_skin_ini('[Colours]\nSpinnerBackground: 20,40,60,0').spinner_background==(20,40,60)
    assert parse_skin_ini('SpinnerBackground: invalid').spinner_background==(100,100,100)
    raw=SpinnerTrack.auto(0,2000,12,rpms=.01)
    assert raw.rpm(1000)==477 and raw.rpm(1000,capped=False)==pytest.approx(600)


@pytest.mark.parametrize('blank',[False,True])
def test_actual_egl_new_glow_is_under_fixed_middle_and_blank_stays_blank(tmp_path,blank):
    renderer=SpriteRenderer(1280,720)
    try:
        scene,obj,track=_visual_scene(_visual_skin(tmp_path/'skin',blank=blank),renderer)
        scene.draw_approach_circles=False   # isolate body/glow from this opaque QA ring
        calls=[];draw=renderer.draw
        def record(sprites):calls.append(list(sprites));return draw(sprites)
        renderer.draw=record;renderer.begin((.1,.1,.1))
        baseline=renderer.read_rgb().copy();scene._draw_spinner(obj,4000)
        rgb=renderer.read_rgb()
        if blank:assert np.array_equal(rgb,baseline)
        else:
            assert all(s.additive for s in calls[0])
            assert all(not s.additive for s in calls[1])
            assert rgb[360,640].tolist()==pytest.approx([255,128,128],abs=1)
    finally:renderer.release()
