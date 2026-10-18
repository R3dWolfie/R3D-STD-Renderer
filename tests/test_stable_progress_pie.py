"""Independent osu!stable pie metric, clock, geometry and framebuffer oracles."""
from types import SimpleNamespace
import math
import numpy as np
import pytest
from PIL import Image
from osu_std_renderer.render.hud import stable_accuracy_measure, stable_song_progress, stable_progress_lead_in
from osu_std_renderer.render.textures import TextureBank, bake_stable_sector, bake_stable_circularmetre, stable_sector_vertices
from osu_std_renderer.render.gl import SpriteRenderer
from tests.test_hud_layout import Recorder, make_hud, render


def test_source_advance_includes_trailing_overlap_and_first_height():
    # Unlike R3D's drawable hull: wide '0', narrow '5', tall punctuation.
    zero=('0',48,50,True);dot=('.',10,65,False);pct=('%',18,72,False)
    glyphs=[zero,zero,dot,zero,zero,pct]
    assert stable_accuracy_measure(glyphs,30,5)==(118,50)
    assert stable_accuracy_measure(glyphs[:-1]+[None],30,5)==(105,50)
    assert stable_accuracy_measure(glyphs[:-1]+[('blank',18,72,False)],30,5)==(118,50)
    assert stable_accuracy_measure(glyphs,30,-3)==(166,50)
    assert stable_accuracy_measure(glyphs,30,40)==(-92,50)  # source does not clamp advance


def font_skin(root,percent):
    (root/'skin.ini').write_text('[Fonts]\nScorePrefix: custom\nScoreOverlap: 5\n')
    for i in range(10):
        factor=2 if i%2 else 1
        w=30 if i==5 else 24+i
        h=50 if i==0 else 60+i
        Image.new('RGBA',(w*factor,h*factor),'white').save(root/f'custom-{i}{"@2x" if factor==2 else ""}.png')
    Image.new('RGBA',(20,130),'white').save(root/'custom-dot@2x.png')
    if percent!='missing':
        Image.new('RGBA',(36,144),'white' if percent=='present' else (0,0,0,0)).save(root/'custom-percent@2x.png')
    return root


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080),(1024,768)])
@pytest.mark.parametrize('scale',[.75,1,1.25])
@pytest.mark.parametrize('percent',['present','missing','blank'])
def test_resolved_font_static_constructor_anchor(tmp_path,resolution,scale,percent):
    rec=Recorder(*resolution);bank=TextureBank(rec)
    hud,rec=make_hud(bank,folder=font_skin(tmp_path,percent),renderer=rec,hud_scale=scale)
    # @2x 60-wide '5' resolves to30; @2x dot20 resolves to10. Missing
    # punctuation intentionally retains R3D's approved procedural fallback.
    pct=18 if percent!='missing' else bank.legacy_aspect['%']*69
    measure=4*(30-5)+(10-5)+(pct-5)
    expected=(resolution[0]*768/resolution[1]-(9.6+measure*.576+38.4)*scale,
              (48+5+50*.576/2)*scale)
    snapshots=[]
    for t,acc,score,grade in [(5000,1,0,'SS'),(500,.9768,123456789,'A'),(2000,.9999,10,'S'),(-100,0,999999,'D'),(4000,1,1000000,'SS')]:
        hud.data.acc_at=lambda *args,v=acc:v;hud.data.score_at=lambda *args,v=score:v
        hud.data.grade_at=lambda *args,v=grade:v
        hud.s.show_hit_counter=bool(score%2);hud._mod_acrs=[] if score%2 else hud._mod_acrs
        render(hud,rec,t);rect=hud.bounds['progress'];snapshots.append(rect)
        assert ((rect.left+rect.right)/2,(rect.top+rect.bottom)/2)==pytest.approx(expected)
        assert (rect.right-rect.left,rect.bottom-rect.top)==pytest.approx((42*scale,42*scale))
    assert all(r==snapshots[0] for r in snapshots)
    assert hud.sk.score_digit_sizes['5']==(30,65)
    assert hud.bounds['accuracy'].right==pytest.approx(hud.ui_w_l-3*scale)


@pytest.mark.parametrize('t,expected',[(-5000,-2),(-1500,-1),(0,-1105/2605),(1105,0),(116305,.5),(231505,1),(250000,1)])
def test_native_map_clock_and_intro_lead(t,expected):
    assert stable_song_progress(t,1105,231505,1500)==pytest.approx(expected)


def test_pie_only_lead_reads_existing_asset_cache(tmp_path):
    source=tmp_path/'test.osu';source.write_text('osu file format v14\n[General]\nAudioLeadIn: 1500\n[Events]\nAudioLeadIn: 999999\n')
    bm=SimpleNamespace(file='TEST.OSU',_path_cache={'test.osu':source},diff=SimpleNamespace(preempt=750),video_offset=0)
    assert stable_progress_lead_in(bm,1105)==1500
    bm._path_cache=None
    assert stable_progress_lead_in(bm,1105)==695
    bm.video_offset=-2000
    assert stable_progress_lead_in(bm,1105)==2000
    source.write_text('[General]\nAudioLeadIn: bad\n')
    bm._path_cache={'test.osu':source};bm.video_offset=0
    assert stable_progress_lead_in(bm,1105)==695
    assert source.read_text()=='[General]\nAudioLeadIn: bad\n'


@pytest.mark.parametrize('p',[.25,-.25,.3333,-.3333,1,-1,-1.5])
def test_native_fan_endpoint_and_step(p):
    points=stable_sector_vertices(p);start=-math.pi/2;lo=min(start,start+math.tau*p);hi=max(start,start+math.tau*p)
    assert points[0]==(0,0)
    assert points[1]==pytest.approx((math.cos(lo),math.sin(lo)))
    assert points[-1]==pytest.approx((math.cos(hi),math.sin(hi)))
    assert len(points)==math.ceil(abs(p)*40)+2
    assert all(math.hypot(x,y)==pytest.approx(1) for x,y in points[1:])
    assert points[2]==pytest.approx((math.cos(lo+math.tau/40),math.sin(lo+math.tau/40)))
    assert not np.array_equal(bake_stable_sector(.34),bake_stable_sector(16/48))


def test_sector_direction_opacity_and_repeated_intro_fan():
    pos=bake_stable_sector(.25);neg=bake_stable_sector(-.25)
    assert pos[30,96,3]==153 and pos[30,30,3]==0
    assert neg[30,30,3]==153 and neg[30,96,3]==0
    assert np.max(bake_stable_sector(0)[...,3])==0
    assert bake_stable_sector(-2)[30,64,3] in (214,215)
    assert bake_stable_sector(.25,76/255)[30,96,3]==76
    overlay=bake_stable_circularmetre()
    assert overlay.shape==(336,336,4)
    assert overlay[168,168,3]==255 and overlay[168,240,3]==0
    # Ring maximum lies at16 of42 logical units from the centre.
    assert abs(np.argmax(overlay[168,200:,3])+200-(168+16*8))<=1


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080),(1024,768)])
def test_actual_egl_sector_radius_colour_and_overlay(tmp_path,resolution):
    rec=SpriteRenderer(*resolution)
    try:
        bank=TextureBank(rec);hud,_=make_hud(bank,folder=font_skin(tmp_path,'present'),renderer=rec)
        hud._stable_pie_geometry();cx,cy=hud._stable_pie_anchor;lk=resolution[1]/768
        for t,intro in ((hud.first_t+(hud.last_t-hud.first_t)/4,False),
                        ((hud.first_t+hud._stable_pie_lead)*.75-hud._stable_pie_lead,True)):
            out=[];hud._legacy_pie(out,t,cx,cy)
            assert len(out)==2
            assert out[0].w==pytest.approx(32*lk) and out[1].w==pytest.approx(42*lk)
            assert all((s.x,s.y)==pytest.approx((cx*lk,cy*lk)) for s in out)
            assert out[0].color[:3]==pytest.approx((199/255,1,47/255) if intro else (1,1,1))
            rec.begin((0,0,0));rec.draw(out);rgb=rec.read_rgb()
            x=round((cx+(-8 if intro else 8))*lk);y=round((cy-8)*lk)
            assert rgb[y,x].astype(int)==pytest.approx((119,153,28) if intro else (153,153,153),abs=2)
            x=round(cx*lk);y=round(cy*lk)
            assert min(rgb[y,x])>220  # small dot's edge AA depends on pixel alignment
    finally:rec.release()


def test_progress_off_is_lazy_and_has_no_pie_uploads(tmp_path):
    rec=Recorder();bank=TextureBank(rec);hud,_=make_hud(bank,folder=font_skin(tmp_path,'present'),renderer=rec,show_progress=False)
    render(hud,rec)
    assert 'progress' not in hud.bounds
    assert not hasattr(hud,'_stable_pie_anchor')
    assert not any(k.startswith('stable_') for k in rec.uploaded)


def test_owner_pie_uses_native_inset_while_counters_keep_approved_anchor():
    from pathlib import Path
    owner=Path(__file__).resolve().parents[1]/'test-render/skin'
    rec=Recorder(1920,1080);bank=TextureBank(rec)
    hud,_=make_hud(bank,folder=owner,renderer=rec)
    render(hud,rec)
    # Actual assets: digit5=25, dot=25, blank percent=1, overlap=5.
    # 00.00% advances 4*(25-5)+(25-5)+(1-5) =96 logical units.
    assert hud._stable_pie_anchor[0]==pytest.approx(1920*768/1080-9.6-96*.576-38.4)
    assert hud.bounds['score'].right==pytest.approx(hud.ui_w_l-3)
    assert hud.bounds['accuracy'].right==pytest.approx(hud.ui_w_l-3)


def test_actual_egl_repeat_after_seek_and_separate_overlay_opacity(tmp_path):
    rec=SpriteRenderer(1920,1080)
    try:
        bank=TextureBank(rec);hud,_=make_hud(bank,folder=font_skin(tmp_path,'present'),renderer=rec,hud_opacity=.5)
        cx,cy=hud._stable_pie_geometry();images=[]
        for t in (1500,500,1500):
            out=[];hud._legacy_pie(out,t,cx,cy)
            assert out[-1].color==(1,1,1,.5)
            rec.begin((.02,.02,.02));rec.draw(out);images.append(rec.read_rgb())
        assert np.array_equal(images[0],images[-1])
        assert not np.array_equal(images[0],images[1])
    finally:rec.release()
