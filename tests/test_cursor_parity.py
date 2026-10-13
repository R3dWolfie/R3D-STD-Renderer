"""Legacy cursor source selection, GPU compositing and cursor-only passes."""
from __future__ import annotations

import ast
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import MethodType, SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from osu_std_renderer.render import perf
from osu_std_renderer.render.gl import Sprite, SpriteRenderer, plan_texture_batches
from osu_std_renderer.render.playfield import PlayfieldCamera
from osu_std_renderer.render.scene import StdScene, sparse_trail_times
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.slider_body import SliderBodyRenderer
from osu_std_renderer.render.textures import TextureBank
from osu_std_renderer.replay.replay import StdFrame
from osu_std_renderer.skin.skin import Skin, Source
from tests.test_hud import _map


class Recorder:
    width, height = 1280, 720

    def __init__(self):
        self.uploaded, self.calls = {}, []

    def upload_texture(self, key, rgba, **kwargs):
        self.uploaded[key] = rgba

    def draw(self, sprites):
        self.calls.append(list(sprites))


def cursor_skin(root, middle=False, centre=True):
    root.mkdir(exist_ok=True)
    (root/'skin.ini').write_text(f'[General]\nCursorCentre: {int(centre)}\nCursorRotate: 0\nCursorExpand: 0\n')
    # Native logical sizes 32x32, 20x12 and 8x8, including @2x/aspect.
    Image.new('RGBA', (64,64), (40,100,160,255)).save(root/'cursor@2x.png')
    Image.new('RGBA', (40,24), (200,80,20,128)).save(root/'cursortrail@2x.png')
    if middle:
        Image.new('RGBA', (16,16), (80,180,40,128)).save(root/'cursormiddle@2x.png')
    return root


def make_scene(renderer, skin=None, *, frames=None, bank=None, **kwargs):
    bm = _map('256,192,2000,1,0,0:0:0:0:\n256,192,4000,1,0,0:0:0:0:\n')
    frames = frames or [StdFrame(0,200,192,0), StdFrame(3000,300,192,0)]
    bank = bank or TextureBank(renderer)
    bodies = SliderBodyRenderer(renderer.ctx, renderer.width, renderer.height) if hasattr(renderer,'ctx') else None
    return StdScene(bm,frames,PlayfieldCamera(renderer.width,renderer.height),
                    renderer,bodies,bank,skin_elems=skin,use_skin_cursor=True,**kwargs)


@pytest.mark.parametrize('cursor_provider,middle_provider,trail_provider,blank,force,connected', [
    ('skin',None,'skin',False,False,False),
    ('skin','skin','skin',False,False,True),
    ('skin','fallback','fallback',False,False,False),
    ('skin','local','local',False,False,False),
    ('fallback','fallback','skin',False,False,True),
    ('fallback','skin','local',False,False,False),
    ('local','local','fallback',False,False,True),
    ('skin','skin','fallback',True,False,True),
    ('skin',None,'skin',False,True,True),
    ('skin','fallback','fallback',False,True,True),
    (None,'skin','fallback',False,False,False),
])
def test_cursor_provider_controls_middle_and_trail_mode(
        tmp_path,cursor_provider,middle_provider,trail_provider,blank,force,connected):
    folders={name:tmp_path/name for name in ('skin','fallback','local')}
    for folder in folders.values():folder.mkdir()
    if cursor_provider:
        Image.new('RGBA',(64,64),(40,100,160,255)).save(folders[cursor_provider]/'cursor@2x.png')
    if middle_provider:
        Image.new('RGBA',(16,16),(80,180,40,0 if blank else 128)).save(folders[middle_provider]/'cursormiddle@2x.png')
    Image.new('RGBA',(40,24),(200,80,20,128)).save(folders[trail_provider]/'cursortrail@2x.png')
    rec=Recorder()
    resources=Skin(skin_dir=folders['skin'],fallback_dir=folders['fallback'],local_dir=folders['local'])
    skin=SkinElements(resources,rec)
    scene=make_scene(rec,skin,force_long_trail=force)
    assert scene.long_trail is connected
    same=cursor_provider is not None and cursor_provider==middle_provider
    assert skin.has('cursormiddle') is same
    assert ('sk_cursormiddle' in rec.uploaded) is same
    if same and blank:
        assert 'cursormiddle' in skin.empty
        assert not rec.uploaded['sk_cursormiddle'][:,:,3].any()
    assert resources.get_source('cursortrail')==getattr(Source,trail_provider.upper())
    assert skin.size['cursortrail']==(20,12)
    if cursor_provider:
        sprites=scene._cursor_sprites(1800)
        trail=[s for s in sprites if s.texture_key=='sk_cursortrail']
        assert trail and all(s.additive is connected for s in trail)
        assert all(s.color[:3]==(1,1,1) for s in sprites)
        assert sprites[-1].texture_key==('sk_cursormiddle' if same else 'sk_cursor')
        merged=scene._merge_cursor_sprites(scene.frames,(.5,.7,.9),1800,1700)
        assert all(s.additive is connected for s in merged if s.texture_key=='sk_cursortrail')
        assert all(s.color[:3]==(.5,.7,.9) for s in merged)
        assert merged[-1].color[3]==pytest.approx(.8)  # existing end fade


@pytest.mark.parametrize('centre',[False,True])
@pytest.mark.parametrize('connected',[False,True])
def test_cursor_geometry_fade_and_seek_are_preserved(tmp_path,centre,connected):
    rec=Recorder();skin=SkinElements(Skin(skin_dir=cursor_skin(tmp_path/'skin',connected,centre)),rec)
    scene=make_scene(rec,skin,cursor_scale=1.25,cursor_trail_scale=.75)
    expected=scene._cursor_sprites(1800)
    for t in (2400,500,1800,2000,1800):
        scene._cursor_sprites(t)
    assert scene._cursor_sprites(1800)==expected
    k=720/768*1.25
    body=next(s for s in expected if s.texture_key=='sk_cursor')
    assert (body.w,body.h)==pytest.approx((32*k,32*k))
    trail=[s for s in expected if s.texture_key=='sk_cursortrail']
    assert all((s.w,s.h)==pytest.approx((20*.75*k,12*.75*k)) for s in trail)
    if not connected:
        assert [s.color[3] for s in trail]==pytest.approx([.85*a for _,a in sparse_trail_times(1800)])
    # CursorCentre=0 still hangs body/trail at the pointer's top-left;
    # native middle's existing centre placement is left unchanged.
    sx,sy=scene.cam.to_screen(260,192)  # interpolation at t=1800
    assert (body.x,body.y)==pytest.approx((sx,sy) if centre else (sx+16*k,sy+16*k))


def normal(dst, rgba, opacity=1):
    alpha=rgba[:,:,3:4]/255*opacity
    return np.rint(rgba[:,:,:3]*alpha+dst*(1-alpha)).clip(0,255).astype('uint8')


@pytest.mark.parametrize('middle',[False,True])
def test_intentionally_blank_cursor_layers_never_select_procedural_glow(tmp_path,middle):
    folder=cursor_skin(tmp_path/'skin',middle)
    for asset in folder.glob('*.png'):
        with Image.open(asset) as im:
            rgba=np.array(im.convert('RGBA'));rgba[:,:,3]=0
        Image.fromarray(rgba).save(asset)
    r=SpriteRenderer(1280,720)
    try:
        skin=SkinElements(Skin(skin_dir=folder),r);scene=make_scene(r,skin)
        assert scene.use_skin_cursor and scene.long_trail is middle
        assert {'cursor','cursortrail'}<=skin.empty
        sprites=scene._cursor_sprites(1800)
        assert all(s.texture_key.startswith('sk_cursor') for s in sprites)
        for bg in (32,192):
            r.begin((bg/255,)*3);scene._draw_cursor_sprites(sprites)
            assert np.all(r.read_rgb()==bg)
    finally:r.release()


def test_textured_cursor_matches_decoded_png_alpha_on_dark_and_bright(tmp_path):
    rgba=np.full((32,32,4),(40,100,160,255),dtype='uint8')
    rgba[:4,:,3]=128;rgba[-4:,:,3]=128;rgba[:,:4,3]=128;rgba[:,-4:,3]=128
    rgba[[0,-1],:]=(255,255,255,0);rgba[:,[0,-1]]=(255,255,255,0)
    path=tmp_path/'cursor.png';Image.fromarray(rgba).save(path)
    decoded=Skin(skin_dir=tmp_path).find_texture('cursor').load_rgba()
    r=SpriteRenderer(1280,720)
    try:
        r.upload_texture('sk_cursor',decoded,mipmaps=False)
        for bg in (32,192):
            r.begin((bg/255,)*3);r.draw([Sprite(640,360,32,32,'sk_cursor')])
            actual=r.read_rgb()[344:376,624:656]
            expected=normal(np.full((32,32,3),bg,dtype='uint8'),decoded)
            assert np.abs(actual.astype(int)-expected.astype(int)).max()<=1
            assert actual[16,16].tolist()==[40,100,160]
            assert actual[0,0].tolist()==[bg,bg,bg]
    finally:r.release()


def composition_textures(renderer):
    body=np.full((32,32,4),(40,100,160,255),dtype='uint8')
    body[:4,:,3]=128;body[-4:,:,3]=128;body[:,:4,3]=128;body[:,-4:,3]=128
    body[[0,-1],:,3]=0;body[:,[0,-1],3]=0
    trail=np.full((32,32,4),(200,80,20,128),dtype='uint8')
    middle=np.full((16,16,4),(80,180,40,128),dtype='uint8')
    middle[[0,-1],:,3]=0;middle[:,[0,-1],3]=0
    for key,data in [('sk_cursortrail',trail),('sk_cursor',body),('sk_cursormiddle',middle)]:
        renderer.upload_texture(key,data,mipmaps=False)
    return body,trail,middle


def composition_sprites(connected):
    return [Sprite(640,360,32,32,'sk_cursortrail',(1,1,1,.85),additive=connected),
            Sprite(640,360,32,32,'sk_cursor'),Sprite(640,360,16,16,'sk_cursormiddle')]


def composition_oracle(bg,connected,textures):
    body,trail,middle=textures
    image=np.full((32,32,3),bg,dtype='uint8')
    if connected:
        image=np.rint(image+trail[:,:,:3]*(trail[:,:,3:4]/255*.85)).clip(0,255).astype('uint8')
    else:image=normal(image,trail,.85)
    image=normal(image,body)
    image[8:24,8:24]=normal(image[8:24,8:24],middle)
    return image


def _check_gl_composition(connected,bg):
    r=SpriteRenderer(1280,720)
    try:
        textures=composition_textures(r);sprites=composition_sprites(connected)
        scene=object.__new__(StdScene);scene.spr=r
        r.begin((bg/255,)*3);scene._draw_cursor_sprites(sprites)
        actual=r.read_rgb()[344:376,624:656]
        expected=composition_oracle(bg,connected,textures)
        assert np.abs(actual.astype(int)-expected.astype(int)).max()<=1
        assert actual[16,16].tolist()==pytest.approx([60,140,100],abs=1)
        assert actual[10,5].tolist()==[40,100,160]  # opaque body hides trail
        if connected:
            r.begin((bg/255,)*3);r.draw(sprites)
            old=r.read_rgb()[344:376,624:656]
            assert np.abs(old.astype(int)-expected.astype(int)).max()>70
            assert old[16,16,0]>actual[16,16,0]+70
        # Transparent body/middle edges preserve the exact trail mode.
        other=composition_oracle(bg,not connected,textures)
        assert np.abs(actual[0,0].astype(int)-other[0,0].astype(int)).max()>10
    finally:r.release()


@pytest.mark.parametrize('multitex',[False,True])
def test_actual_gl_trail_then_body_then_middle_matches_alpha_oracle(multitex):
    # Backend mode also fixes shader/vertex layout constants at import time.
    # Use a real process configuration, not a partial module monkeypatch.
    env=dict(os.environ,R3D_STD_MULTITEX=str(int(multitex)))
    code=('from tests.test_cursor_parity import _check_gl_composition\n'
          'for connected in (False,True):\n'
          '    for bg in (32,192): _check_gl_composition(connected,bg)\n')
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                          env=env,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_cursor_passes_preserve_bloom_no_scope_post_transform_and_argon_batch(tmp_path):
    rec=Recorder();skin=SkinElements(Skin(skin_dir=cursor_skin(tmp_path/'skin',True)),rec)
    scene=make_scene(rec,skin)
    base=scene._base_cursor_sprites(1800)
    scene._bm_factor=lambda t:2;scene._ns_factor=lambda t:.25
    wrapped=scene._cursor_sprites(1800)
    px,py=scene.cam.to_screen(260,192)
    for old,new in zip(base,wrapped):
        assert (new.x,new.y)==pytest.approx((px+2*(old.x-px),py+2*(old.y-py)))
        assert (new.w,new.h)==pytest.approx((old.w*2,old.h*2))
        assert new.color==(*old.color[:3],old.color[3]*.25)
        assert new.additive==old.additive and new.uv_scale==old.uv_scale
    scene._draw_cursor_sprites(wrapped)
    assert len(rec.calls)==2
    assert all(s.additive for s in rec.calls[0]) and all(not s.additive for s in rec.calls[1])
    assert [s for call in rec.calls for s in call]==wrapped
    procedural=[Sprite(640,360,10,10,'glow',additive=True),Sprite(640,360,8,8,'argon_cursor')]
    rec.calls.clear();scene._draw_cursor_sprites(procedural)
    assert rec.calls==[procedural]  # existing Argon/procedural backend order


def test_metal_serializer_records_cursor_pass_order_and_one_post_transform():
    path=Path(__file__).resolve().parents[1]/'osu_std_renderer/render/metal/renderer.py'
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='MetalSpriteRenderer')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='draw')
    ns=dict(np=np,perf=perf,plan_texture_batches=plan_texture_batches,_UNITS=16)
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),ns)
    seen=[]
    def post(sp):seen.append(sp);return replace(sp,x=sp.x+7,y=sp.y-3)
    renderer=SimpleNamespace(post_xform=post,_tex={'sk_cursortrail':1,'sk_cursor':2,'sk_cursormiddle':3},_white=0,_scene=[])
    renderer.draw=MethodType(ns['draw'],renderer)
    scene=object.__new__(StdScene);scene.spr=renderer
    sprites=composition_sprites(True);scene._draw_cursor_sprites(sprites)
    assert seen==sprites
    assert [op[3] for op in renderer._scene]==[True,False]
    assert [op[2] for op in renderer._scene]==[[1],[2,3]]
    instances=np.concatenate([op[1] for op in renderer._scene])
    assert instances[:,0].tolist()==[647,647,647]
    assert instances[:,1].tolist()==[357,357,357]
    assert instances[:,8].tolist()==pytest.approx([.85,1,1])


@pytest.mark.parametrize('resolution',[(1280,720),(1920,1080)])
def test_full_scene_cursor_repeat_restart_merge_and_fail(tmp_path,resolution):
    r=SpriteRenderer(*resolution)
    try:
        skin=SkinElements(Skin(skin_dir=cursor_skin(tmp_path/'skin',True)),r);bank=TextureBank(r)
        scene=make_scene(r,skin,bank=bank)
        isolated={t:scene._cursor_sprites(t) for t in (1800,1400,1600,1800)}
        for t in (1400,1600,1800):scene.frame_rgb(t)
        expected=scene.frame_rgb(1800)
        assert np.array_equal(expected,scene.frame_rgb(1800))
        for t in (1800,1400,1600,1800):
            restarted=make_scene(r,skin,bank=bank)
            assert restarted._cursor_sprites(t)==isolated[t]
            frame=restarted.frame_rgb(t)
            if t==1800:assert np.array_equal(frame,expected)
        # Exercise the actual merge/fail scene call sites, not only a list
        # helper. Both must submit homogeneous trail and body/middle passes.
        for mode in ('merge','fail'):
            kwargs=({'merge_cursors':[(scene.frames,(.5,.7,.9),3000)],'draw_cursor':False}
                    if mode=='merge' else {'fail_time_ms':1800})
            sc=make_scene(r,skin,bank=bank,**kwargs)
            calls=[];original=r.draw
            def record(batch):calls.append(list(batch));return original(batch)
            r.draw=record
            try:sc.frame_rgb(1850)
            finally:r.draw=original
            cursor_calls=[batch for batch in calls if any(s.texture_key in ('sk_cursor','sk_cursortrail','sk_cursormiddle') for s in batch)]
            assert len(cursor_calls)==2,mode
            assert all(s.texture_key=='sk_cursortrail' and s.additive for s in cursor_calls[0])
            assert [s.texture_key for s in cursor_calls[1]]==['sk_cursor','sk_cursormiddle']
            merged=sc._merge_cursor_sprites(sc.frames,(.5,.7,.9),1800,3000)
            assert all(s.color[:3]==(.5,.7,.9) for s in merged)
    finally:r.release()


@pytest.mark.skipif(sys.platform!='darwin',reason='native Metal framebuffer requires macOS')
def test_native_metal_cursor_composition_matches_gl():
    from osu_std_renderer.render.metal.renderer import MetalSpriteRenderer
    renderers=[SpriteRenderer(1280,720),MetalSpriteRenderer(1280,720)]
    try:
        for r in renderers:composition_textures(r)
        for connected in (False,True):
            images=[]
            for r in renderers:
                sc=object.__new__(StdScene);sc.spr=r;r.begin((32/255,)*3)
                sc._draw_cursor_sprites(composition_sprites(connected));images.append(r.read_rgb())
            assert np.abs(images[0].astype(int)-images[1].astype(int)).max()<=1
    finally:
        for r in renderers:r.release()
