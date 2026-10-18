"""Standard timing adapter and source-backed Mania HEM regression gates."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib
import math
import sys

import numpy as np
import pytest

from osu_std_renderer.ruleset import (JudgmentKind, ObjectVerdict, OsuHitWindows,
    SliderVisualEvent, SliderVisualTimeline)
from osu_std_renderer.replay.replay import StdFrame
from osu_std_renderer.render.gl import SpriteRenderer, Sprite
from osu_std_renderer.render.hit_error_meter import (BLUE,GREEN,YELLOW,CENTRE_DARK_BLUE,
    hit_window_bands,hit_error_meter_geometry,hit_error_offset_position,
    hit_error_tick_state,hit_error_chevron_position,next_hit_error_chevron_transition,
    next_hit_error_ema)
from osu_std_renderer.render.hit_error_timeline import (TimingObservation,HitErrorEvent,
    HitErrorFrame,HitErrorTimeline,timing_observations)
from osu_std_renderer.render.hit_error_painter import (HitErrorMeterPainter,MeterShape,
    MeterIcon,meter_commands,metal_uniforms,_METAL)
from tests.test_hud_layout import Recorder,make_hud,render
from osu_std_renderer.render.textures import TextureBank


@pytest.mark.parametrize('od,expected',[(0,(79.5,139.5,199.5)),(5,(49.5,99.5,149.5)),(10,(19.5,59.5,99.5))])
def test_actual_standard_windows_project_three_bands(od,expected):
    hw=OsuHitWindows(od);windows=(hw.great,hw.ok,hw.meh)
    bands=hit_window_bands(windows)
    assert tuple(b.window_ms for b in bands)==expected
    assert [b.judgment for b in bands]==['300','100','50']
    assert [b.colour for b in bands]==[BLUE,GREEN,YELLOW]
    assert [b.relative_length for b in bands]==pytest.approx([v/expected[-1] for v in expected])
    assert hit_error_offset_position(-hw.meh,hw.meh)==0
    assert hit_error_offset_position(hw.meh,hw.meh)==1


@pytest.mark.parametrize('width,height,origin',[(1280,720,(518,690)),
    (1920,1080,(777,1035)),(1024,768,(381.8666666667,736))])
def test_viewport_centred_geometry_preserves_mania_size_and_vertical_anchor(width,height,origin):
    g=hit_error_meter_geometry(width,height)
    assert (g.component_x,g.component_top)==pytest.approx(origin)
    assert g.component_x+g.axis_centre==pytest.approx(width/2)
    assert g.natural_width==pytest.approx(244*height/720)
    assert height-g.component_top-g.natural_height==pytest.approx(8*height/720)
    assert np.array(g.marker_segment(.5,1))==pytest.approx(
        np.array(((g.axis_centre,0),(g.axis_centre,14*g.scale))))


@pytest.mark.parametrize('offset,window,expected',[(-200,100,0),(-50,100,.25),(0,100,.5),(50,100,.75),(200,100,1),(float('nan'),100,.5),(1,0,.5),(1,float('inf'),.5)])
def test_signed_mapping_and_invalid_values(offset,window,expected):
    assert hit_error_offset_position(offset,window)==expected


def verdict(kind='circle',time=1010,delta=10,result=JudgmentKind.HIT300,events=None):
    v=ObjectVerdict(kind,result,1000,2000,(100,100),(100,100),hit_time=time,delta=delta)
    if delta is not None:v.quality=abs(delta)
    if events is not None:v.slider_visuals=SliderVisualTimeline('partial',True,False,tuple(events))
    return v


def observation_set(*verdicts,frames=None):
    sim=SimpleNamespace(verdicts=dict(enumerate(verdicts)))
    if frames is None:frames=[StdFrame(900,100,100,0),StdFrame(1010,100,100,4)]
    return timing_observations(sim,frames,OsuHitWindows(5),32)


@pytest.mark.parametrize('time,delta,result,expected',[(1010,10,JudgmentKind.HIT50,'300'),
    (1070,70,JudgmentKind.HIT300,'100'),(1120,120,JudgmentKind.HIT300,'50'),
    (990,-10,JudgmentKind.HIT100,'300')])
def test_colour_comes_from_actual_timing_not_reconciled_tier(time,delta,result,expected):
    frames=[StdFrame(time-1,100,100,0),StdFrame(time,100,100,4)]
    samples=observation_set(verdict(time=time,delta=delta,result=result),frames=frames)
    assert samples==(TimingObservation(time,delta,expected,'circle'),)


def test_raw_slider_head_survives_repaired_aggregate_and_parts_without_mutation():
    head=SliderVisualEvent(1010,0,'head_judged','click_routing',hit=True,
        part_index=0,part_kind='head',channel=1,input_frame_index=1)
    v=verdict('slider',time=None,delta=None,result=JudgmentKind.MISS,events=[head])
    before=repr(v)
    assert observation_set(v)==(TimingObservation(1010,10,'300','slider'),)
    assert repr(v)==before and v.slider_visuals.events[0] is head


@pytest.mark.parametrize('events',[
    [],[SliderVisualEvent(1010,0,'head_judged','click_routing',hit=False)],
    [SliderVisualEvent(1010,0,'part_sampled','part_sample',hit=True,part_kind='tick')],
    [SliderVisualEvent(1010,0,'head_judged','source_model',hit=True)],
    [SliderVisualEvent(float('nan'),0,'head_judged','click_routing',hit=True)],
    [SliderVisualEvent(1010,0,'head_judged','click_routing',hit=True,channel=2)]])
def test_missing_missed_modelled_or_invalid_slider_head_never_invents_marker(events):
    assert observation_set(verdict('slider',events=events))==()


@pytest.mark.parametrize('v',[verdict(time=None),verdict(delta=None),verdict(time=float('nan')),
    verdict(delta=float('inf')),verdict(time=1011),verdict(time=1300,delta=300),
    verdict(result=JudgmentKind.MISS),verdict('spinner'),verdict('slider')])
def test_missing_invalid_untimed_missed_and_nonclick_observations_are_excluded(v):
    assert observation_set(v)==()


def test_repaired_zero_offset_without_real_click_and_held_frames_are_excluded():
    v=verdict(time=1000,delta=0)
    assert observation_set(v)==()  # no input at its synthetic time
    held=[StdFrame(900,100,100,4),StdFrame(1000,100,100,4)]
    assert observation_set(v,frames=held)==()
    assert observation_set(verdict(),frames=[StdFrame(1010,999,999,4)])==()
    repaired=verdict(time=1000,delta=0)
    repaired.quality=float('inf') # raw miss, even when header repair inserts0
    assert observation_set(repaired,frames=[StdFrame(1000,100,100,4)])==()


def test_simultaneous_two_channels_and_no_duplicate_use_of_one_click():
    v1=verdict();v2=verdict()
    assert len(observation_set(v1,v2))==1
    assert len(observation_set(v1,v2,frames=[StdFrame(1010,100,100,12)]))==2
    head=SliderVisualEvent(1010,0,'head_judged','click_routing',hit=True,channel=1)
    samples=observation_set(v1,verdict('slider',events=[head]))
    assert len(samples)==1 and samples[0].object_kind=='slider'


@pytest.mark.parametrize('age,alpha,width',[(-1,0,0),(0,0,0),(50,.58125,.96875),
    (100,.6,1),(2600,.3,.96875),(5100,0,0),(9999,0,0)])
def test_exact_marker_entrance_exit_lifetime(age,alpha,width):
    state=hit_error_tick_state(age)
    assert (state.alpha,state.width_fraction)==pytest.approx((alpha,width))


def test_newest_fifty_and_exact_expiry_but_ema_persists():
    points=[TimingObservation(1000+i,i-30,'300','circle') for i in range(60)]
    tl=HitErrorTimeline(points,(20,60,100));f=tl.at(1100)
    assert len(f.hit_error_events)==50
    assert f.hit_error_events[0].offset_ms==-20
    assert len(tl.at(6110).hit_error_events)==49
    assert tl.at(6160).hit_error_events==()
    assert tl.at(6160).hit_error_ema_ms==tl.averages[-1]
    assert tl.at(999).hit_error_chevron_position is None


def test_interrupted_800ms_outquint_transition_and_simultaneous_hits():
    points=[TimingObservation(1000,100,'50','circle'),TimingObservation(1100,-50,'100','circle'),
        TimingObservation(1100,20,'300','slider')]
    tl=HitErrorTimeline(points,(20,60,100))
    assert tl.averages==pytest.approx((10,4,5.6))
    first_start=.5;first_target=.55
    interrupted=first_start+(first_target-first_start)*(1-(1-100/800)**5)
    assert tl.transitions[1].start_position==pytest.approx(interrupted)
    assert tl.transitions[2].start_position==pytest.approx(interrupted)
    assert tl.at(1900).hit_error_chevron_position==pytest.approx(.528)
    direct=tl.at(1150)
    for t in (0,1000,1001,1099,1100,1500,9999,500,1150):tl.at(t)
    assert tl.at(1150)==direct
    fresh=HitErrorTimeline(points,(20,60,100))
    assert fresh.at(1150)==direct


def diagnostic():
    return HitErrorFrame((20,60,100),(HitErrorEvent(-35,'300',100),
        HitErrorEvent(0,'100',100),HitErrorEvent(80,'50',100)),30,.65)


@pytest.mark.parametrize('width,height',[(1280,720),(1920,1080),(1024,768),
    (3440,1440),(1921,1081)])
def test_zero_marker_bands_icons_and_zero_average_share_viewport_centre(width,height):
    state=replace(diagnostic(),hit_error_events=(),hit_error_chevron_position=.5)
    commands=meter_commands(state,width,height)
    for band in commands[:3]:
        assert band.rect[0]+band.rect[2]/2==pytest.approx(width/2)
    outer_centre,inner_centre=commands[3:5]
    for centre in (outer_centre,inner_centre):
        assert centre.a[0]==pytest.approx(width/2)
        assert centre.b[0]==pytest.approx(width/2)
    early,late,chevron=commands[5:]
    assert (early.centre[0]+late.centre[0])/2==pytest.approx(width/2)
    assert chevron.b[0]==pytest.approx(width/2)


def test_shared_draw_order_opacity_rounded_caps_and_hollow_chevron():
    commands=meter_commands(diagnostic(),1280,720,.4)
    shapes=[c for c in commands if isinstance(c,MeterShape)]
    assert [s.colour[:3] for s in shapes[:4]]==[YELLOW,GREEN,BLUE,BLUE]
    assert [s.additive for s in shapes]==[False]*4+[True]*3+[False]*2
    assert shapes[0].fade==6 and shapes[1].fade==0
    assert shapes[4].radius==2 and shapes[4].colour[3]==pytest.approx(.24)
    assert shapes[-1].mode==2 and shapes[-1].radius==1
    assert shapes[-1].colour[3]==pytest.approx(.38)
    assert isinstance(commands[-2],MeterIcon) and isinstance(commands[-1],MeterShape)
    assert meter_commands(diagnostic(),1280,720,0)==()
    assert meter_commands(replace(diagnostic(),hit_error_windows=()),1280,720)==()


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('hem,ur',[(False,False),(False,True),(True,False),(True,True)])
def test_visibility_profile_selection_independent_ur_and_authorized_position(legacy,hem,ur,tmp_path):
    rec=Recorder();bank=TextureBank(rec)
    hud,_=make_hud(bank,folder=tmp_path if legacy else None,renderer=rec,
        show_hit_error_meter=hem,show_unstable_rate=ur)
    result=render(hud,rec)
    assert ('hit_error' in hud.bounds)==hem
    assert ('unstable_rate' in hud.bounds)==ur
    if hem:
        assert rec.hit_error_frame==hud._hem.at(5000)
    if ur and hem:
        assert hud.bounds['unstable_rate'].bottom < hud.bounds['hit_error'].top
    if ur and not hem:
        current=[];hud._unstable_rate(current,5000)
        hud.s.show_hit_error_meter=True
        previous=[];hud._previous_hit_error(previous,5000)
        hud.s.show_hit_error_meter=False
        assert current==previous[-len(current):]
    hud.op=0
    assert render(hud,rec)==[] and hud.bounds=={}


def test_icon_original_hashes_and_no_disk_resampling():
    from PIL import Image
    root=Path(__file__).resolve().parents[1]/'osu_std_renderer/assets/hud/hit-error-meter'
    for name,sha in [('hare','61713e94571614b5adbee4c149f4703bb3003294c6df356ab1465514607f2979'),
                     ('tortoise','f1b88e43076527be47c141d42d98576257b276c5cfaf8951ea457bb3c8f1c178')]:
        path=root/(name+'.png')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
        assert Image.open(path).size==(100,100)


@pytest.mark.parametrize('width,height',[(1280,720),(1920,1080),(1024,768)])
def test_actual_gpu_bands_fades_round_caps_depth_icons_chevron_cache_and_blend_restore(width,height):
    r=SpriteRenderer(width,height)
    try:
        state=diagnostic();g=hit_error_meter_geometry(width,height)
        def draw(scene):
            r.begin((0,0,0));r.draw_hit_error_meter(scene);return r.read_rgb()
        base=draw(replace(state,hit_error_events=()))
        rgb=draw(state);painter=r._hit_error_painter;program=painter.program;icons=painter.icons
        def pixel(im,x,y):return im[int(g.component_top+y*g.scale),int(g.component_x+x*g.scale)].astype(int)
        for x,colour in [(50,YELLOW),(80,GREEN),(110,BLUE)]:
            assert pixel(base,x,7)==pytest.approx(np.array(colour)*255,abs=1)
        for x in range(22,28):
            sx=int(g.component_x+x*g.scale)
            alpha=max(0,min(1,(sx+.5-g.component_x-g.axis_start)/(6*g.scale)))
            assert pixel(base,x,7)==pytest.approx(np.array(YELLOW)*255*alpha,abs=2)
        assert pixel(rgb,122,7)==pytest.approx(np.array(CENTRE_DARK_BLUE)*255,abs=1)
        # Independent viewport oracle: local geometry must not hide an offset.
        assert rgb[int(g.component_top+7*g.scale),width//2].astype(int)==pytest.approx(
            np.array(CENTRE_DARK_BLUE)*255,abs=1)
        assert pixel(rgb,122,4)[1]>pixel(base,122,4)[1]
        for x,colour in [(87,BLUE),(202,YELLOW)]:
            assert pixel(rgb,x,2)==pytest.approx(np.array(colour)*255*.6,abs=2)
            assert pixel(rgb,x+4,2).max()==0
        assert pixel(rgb,87,-1)[2]>30
        assert pixel(rgb,152,15).max()>100
        assert pixel(rgb,152,19).max()==0
        for cx in (8,236):
            box=rgb[int(g.component_top):int(g.component_top+14*g.scale),
                int(g.component_x+(cx-8)*g.scale):int(g.component_x+(cx+8)*g.scale)]
            assert box.max()>100 and np.count_nonzero(box)>20
        assert np.array_equal(rgb,draw(state))
        assert painter is r._hit_error_painter and painter.program is program and painter.icons is icons
        # Real source-over after the painter, including an additive tick pass.
        r.draw([Sprite(15,15,10,10,None,(1,0,0,.5)),Sprite(15,15,10,10,None,(1,0,0,.5))])
        assert r.read_rgb()[15,15,0]==pytest.approx(192,abs=2)
        uploads=[];upload=r.upload_texture;r.upload_texture=lambda *args,**kw:uploads.append(args)
        draw(state);assert uploads==[];r.upload_texture=upload
    finally:r.release()


def test_metal_shared_commands_abi_and_actual_queue_adapter(monkeypatch):
    import types
    import osu_std_renderer.render.metal as package
    fake=types.ModuleType('osu_std_renderer.render.metal.core')
    fake.RGBA8=0;fake.BLEND_ALPHA=1;fake.BLEND_ADD=2
    monkeypatch.setitem(sys.modules,fake.__name__,fake)
    monkeypatch.setattr(package,'core',fake,raising=False)
    class Core:
        def __init__(self):self.pipes=[];self.draws=[]
        def pipe_create(self,source,vs,fs,fmt,blend,**kwargs):
            self.pipes.append((source,vs,fs,blend,kwargs));return len(self.pipes)
        def draw(self,pipe,count,**kwargs):self.draws.append((pipe,count,kwargs['uniforms']))
    core=Core();rec=Recorder(1920,1080);rec.core=core;rec.queue=[];rec.add_scene=rec.queue.append
    painter=HitErrorMeterPainter(rec);state=diagnostic();painter.draw(state,.4)
    assert len(core.pipes)==2 and len(rec.uploaded)==2
    assert all(p[0]==_METAL and p[4]['safe_math'] for p in core.pipes)
    shapes=[c for c in meter_commands(state,1920,1080,.4) if isinstance(c,MeterShape)]
    assert len(rec.queue)==len(shapes)
    for call in rec.queue:call()
    for (pipe,count,u),command in zip(core.draws,shapes):
        assert count==6 and pipe==(2 if command.additive else 1)
        assert u.dtype==np.float32 and u.shape==(20,) and u.nbytes==80
        assert u[:4].tolist()==[1920,1080,command.mode,-1] # bottom-up Metal convention
        assert np.array_equal(u,metal_uniforms(command,1920,1080))
    assert len(rec.sprites)==2 and all(s.w==s.h==21 for s in rec.sprites)


@pytest.mark.skipif(sys.platform!='darwin',reason='native Metal HEM framebuffer requires macOS')
@pytest.mark.parametrize('width,height',[(1280,720),(1920,1080)])
def test_native_metal_hem_matches_opengl(width,height):
    from osu_std_renderer.render.metal.renderer import MetalSpriteRenderer
    gl=SpriteRenderer(width,height);metal=MetalSpriteRenderer(width,height)
    try:
        for state in (diagnostic(),replace(diagnostic(),hit_error_events=())):
            images=[]
            for r in (gl,metal):
                r.begin((.05,.05,.05));r.draw_hit_error_meter(state);images.append(r.read_rgb().astype(int))
            assert np.max(np.abs(images[0]-images[1]))<=2
    finally:gl.release();metal.release()


def test_simultaneous_raw_slider_priority_preserves_replay_channel_order_for_ema():
    circle=verdict(time=1010,delta=10)
    head=SliderVisualEvent(1010,0,'head_judged','click_routing',hit=True,channel=2)
    slider=verdict('slider',events=[head]);slider.start_time=1020
    samples=observation_set(circle,slider,frames=[StdFrame(1010,100,100,12)])
    assert [s.object_kind for s in samples]==['circle','slider']
    assert [s.offset_ms for s in samples]==[10,-10]
    timeline=HitErrorTimeline(samples,(20,60,100))
    assert timeline.averages==(1,pytest.approx(-.1))


@pytest.mark.parametrize('opacity',[1,.4])
def test_actual_gpu_legacy_argon_same_meter_and_height_scaling(tmp_path,opacity):
    r=SpriteRenderer(1280,720)
    try:
        bank=TextureBank(r);images=[]
        for folder in (None,tmp_path):
            hud,_=make_hud(bank,folder=folder,renderer=r,hud_opacity=opacity,
                show_score=False,show_combo=False,show_grade=False,show_progress=False,
                show_key_overlay=False,show_hp_bar=False,show_mods=False,
                show_hit_counter=False,show_pp_counter=False,show_unstable_rate=False)
            r.begin((0,0,0));hud.draw(2300);images.append(r.read_rgb())
        assert np.array_equal(images[0],images[1])
        # Outside all capsules/dots, the outer yellow band retains HUD alpha.
        assert images[0][697,570].astype(int)==pytest.approx(np.array(YELLOW)*255*opacity,abs=2)
    finally:r.release()


def test_real_reconciled_slider_simulation_uses_frozen_head_clicks():
    from tests.test_ruleset import _multi_slider,_perfect_slider_frames,_lazer_stats_meta
    from osu_std_renderer.ruleset import StdRuleset
    bm=_multi_slider(8);frames=_perfect_slider_frames(bm)
    meta=_lazer_stats_meta(8,large_tick_hit=14,large_tick_miss=2,
        slider_tail_hit=5,max_large_tick=16,max_slider_tail=8,max_combo=24)
    sim=StdRuleset(bm,frames,meta).run()
    before=repr(sim)
    observed=timing_observations(sim,frames,OsuHitWindows(bm.diff.od),bm.diff.get_radius())
    assert len(observed)==8
    assert all(e.judgment=='300' and e.offset_ms==0 and e.object_kind=='slider' for e in observed)
    assert repr(sim)==before


def test_actual_gpu_fresh_timestamp_equals_forward_and_backwards_seeks():
    points=[TimingObservation(1000,-40,'100','circle'),TimingObservation(1100,20,'300','slider'),
            TimingObservation(1200,80,'50','circle')]
    timeline=HitErrorTimeline(points,(20,60,100));r=SpriteRenderer(1280,720)
    try:
        def draw(t):
            r.begin((.1,.1,.1));r.draw_hit_error_meter(timeline.at(t));return r.read_rgb()
        fresh=draw(1250)
        for t in (999,1000,1016,1100,1200,1233,1250,6500,1050,1250):actual=draw(t)
        assert np.array_equal(fresh,actual)
    finally:r.release()
