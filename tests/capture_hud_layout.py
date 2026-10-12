"""Capture actual EGL HUD pixels and per-component source/bounds traces.

Run with PYTHONPATH pointing at a baseline archive for the before label.
Writes only to the requested evidence folder, never the owner's inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.cli import find_osu_file
from osu_std_renderer.render.gl import SpriteRenderer
from osu_std_renderer.render.hud import StdHud
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.textures import TextureBank, _load_font
from osu_std_renderer.replay.replay import parse_replay
from osu_std_renderer.ruleset import HealthTimeline, StdRuleset
from osu_std_renderer.settings import StdRenderSettings
from osu_std_renderer.skin.skin import Skin


def bounds(sprites, unit=1):
    visible = [s for s in sprites if s.color[3] > 0]
    if not visible:
        return None
    quads = []
    for s in visible:
        c, a = abs(math.cos(s.rotation)), abs(math.sin(s.rotation))
        w, h = c * s.w + a * s.h, a * s.w + c * s.h
        quads.append((s.x-w/2, s.y-h/2, s.x+w/2, s.y+h/2))
    return [min(q[0] for q in quads)/unit, min(q[1] for q in quads)/unit,
            max(q[2] for q in quads)/unit, max(q[3] for q in quads)/unit]


def resource_trace(resources, skin, key):
    if not resources or not (key or '').startswith('sk_'):
        return dict(provider='classic-procedural' if (key or '').startswith('lg_') else 'renderer-procedural', asset=key)
    name=key[3:]
    for family,prefix in [('scoreentry','scoreentry'),('score',skin.info.score_prefix or 'score'),
                          ('combo',skin.info.combo_prefix or 'score')]:
        if name.startswith(family+'_'):
            name=prefix+'-'+name[len(family)+1:]
            break
    frame=None
    if '_f' in name:
        name,frame=name.rsplit('_f',1)
    found=resources.find_frames(name, use_dash=True) if name=='scorebar-colour' else []
    tf=(found[int(frame or 0)] if found else resources.find_texture(name))
    return dict(asset=name,provider=tf.source.name if tf else 'unresolved',
                path=str(tf.path.resolve()) if tf else None,two_x=tf.two_x if tf else None,
                frame=int(frame) if frame else 0)


def fixtures(root):
    root.mkdir(parents=True, exist_ok=True)
    for name in ('complete', 'digits-only', 'keys-only', 'empty', 'blank'):
        folder = root / name
        folder.mkdir(exist_ok=True)
        (folder/'skin.ini').write_text('[General]\nName: HUD QA\nVersion: 2.7\n'
                                      'AnimationFramerate: 20\n[Fonts]\n'
                                      'ScorePrefix: numeric/score\nComboPrefix: numeric/score\n'
                                      'ScoreOverlap: 3\nComboOverlap: 3\n')
        if name in ('complete', 'digits-only', 'blank'):
            (folder/'numeric').mkdir(exist_ok=True)
            for ch in '0123456789':
                factor = 2 if int(ch) % 2 else 1
                im = Image.new('RGBA', (30*factor, 48*factor))
                f = _load_font(36*factor)
                ImageDraw.Draw(im).text((2*factor, 0), ch, font=f, fill='white')
                im.save(folder/'numeric'/f'score-{ch}{"@2x" if factor==2 else ""}.png')
        if name in ('complete', 'keys-only', 'blank'):
            for asset, size in [('inputoverlay-key', (46,46)),
                                ('inputoverlay-background', (220,50)),
                                ('scorebar-bg', (300,40)),
                                ('scorebar-colour-0', (270,16)),
                                ('scorebar-colour-1', (270,16)),
                                ('ranking-A-small', (60,38))]:
                im = Image.new('RGBA', size, (0,0,0,0) if name=='blank' else (80,170,210,255))
                im.save(folder/f'{asset}.png')
    return root


def capture(root, label, repo):
    fixture = fixtures(root/'fixtures')
    frames, meta = parse_replay(repo/'test-render/test.osr')
    bm = load_full(find_osu_file(repo/'test-render/beatmap', meta.beatmap_md5), mods=meta.mods)
    result = StdRuleset(bm, frames, meta).run()
    health = HealthTimeline(bm, result)
    cases = [('argon', None, {}), ('complete', fixture/'complete', {}),
             ('partial', fixture/'digits-only', {}), ('keys-only', fixture/'keys-only', {}),
             ('empty', fixture/'empty', {}), ('blank', fixture/'blank', {}),
             ('owner', repo/'test-render/skin', {}),
             ('owner-default-font', repo/'test-render/skin', {'renderer_default_font_and_ranks': True}),
             ('legacy-defaults', None, {'legacy_defaults': True})]
    rows=[]
    for name, skin_path, overrides in cases:
        renderer = SpriteRenderer(1280,720)
        try:
            resources = Skin(skin_dir=skin_path) if skin_path else None
            skin = SkinElements(resources, renderer) if resources else None
            bank = TextureBank(renderer)
            settings = StdRenderSettings(skin_dir=skin_path, show_progress=False,
                                          show_grade=True, **overrides)
            hud = StdHud(renderer, bank, settings, result, frames, bm,
                         skin_elems=skin, health=health, mods=meta.mods)
            hud.pin_final_score(meta.score)
            trace=[]
            methods = {'_argon_score_block': ('TopLeft','TopRight','show_score'),
                       '_argon_accuracy': ('TopRight','TopRight','show_score'),
                       '_legacy_score_block': ('TopRight','TopRight','show_score'),
                       '_legacy_pie': ('TopRight','CentreRight','show_progress'),
                       '_argon_progress': ('BottomCentre','BottomCentre','show_progress'),
                       '_argon_combo': ('BottomLeft','BottomLeft','show_combo'),
                       '_legacy_combo': ('BottomLeft','BottomLeft','show_combo'),
                       '_argon_health': ('TopLeft','TopLeft','show_hp_bar'),
                       '_legacy_health': ('TopLeft','TopLeft','show_hp_bar'),
                       '_argon_key_overlay': ('BottomRight','BottomRight','show_key_overlay'),
                       '_legacy_key_overlay': ('CentreRight','TopRight','show_key_overlay'),
                       '_grade_badge': ('project-extra','right-centre','show_grade'),
                       '_mod_pills': ('project-extra','TopRight','show_mods'),
                       '_hit_counter': ('project-extra','TopRight','show_hit_counter'),
                       '_pp_counter': ('project-extra','TopLeft','show_pp_counter'),
                       '_hit_error': ('profile','profile','show_hit_error_meter'),
                       '_aim_error': ('project-extra','centre','show_aim_error_meter'),
                       '_strain_graph': ('project-extra','BottomCentre','show_strain_graph'),
                       '_watermark': ('project-extra','BottomRight','watermark_text')}
            for method, (anchor,origin,flag) in methods.items():
                original = getattr(hud, method)
                def wrap(out,*args,_orig=original,_method=method,_anchor=anchor,_origin=origin,_flag=flag,**kwargs):
                    start=len(out)
                    ret=_orig(out,*args,**kwargs)
                    sprites=out[start:]
                    keys=sorted({s.texture_key for s in sprites}, key=str)
                    trace.append(dict(component=_method,anchor=_anchor,origin=_origin,
                                      flag=_flag,enabled=bool(getattr(settings,_flag,False)),
                                      visible=bool(sprites),z_range=[start,len(out)],assets=keys,
                                      providers=sorted({('skin/fallback' if (k or '').startswith('sk_') else
                                                        'classic-procedural' if (k or '').startswith('lg_') else
                                                        'renderer-procedural') for k in keys}),
                                      resolved_assets=[resource_trace(resources,skin,k) for k in keys],
                                      bounds_output=bounds(sprites),bounds_768=bounds(sprites,hud.lk)))
                    return ret
                setattr(hud,method,wrap)
            toggles=[(True,False)]
            if name in ('owner','argon'):
                toggles=[(g,p) for g in (False,True) for p in (False,True)]
            for grade,progress in toggles:
                settings.show_grade,settings.show_progress=grade,progress
                for time in (15000.,60000.) if name in ('owner','argon') else (15000.,):
                    trace.clear()
                    renderer.begin((.025,.025,.035))
                    hud.draw(time)
                    path=root/label/f'{name}-grade{int(grade)}-progress{int(progress)}-{int(time)}.png'
                    path.parent.mkdir(parents=True,exist_ok=True)
                    Image.fromarray(renderer.read_rgb()).save(path)
                    rows.append(dict(case=name,time_ms=time,show_grade=grade,show_progress=progress,
                                     layout=getattr(getattr(hud,'layout',None),'name','baseline-hybrid'),
                                     family_flags={k:getattr(hud,k) for k in ('legacy_score','legacy_combo','legacy_health','legacy_keys')},
                                     skin_path=str(skin_path) if skin_path else None,
                                     loaded=sorted(skin.loaded) if skin else [],empty=sorted(skin.empty) if skin else [],
                                     sizes=skin.size if skin else {},score_digits=skin.score_digit_sizes if skin else {},
                                     score_prefix=skin.info.score_prefix if skin else None,
                                     score_overlap=skin.info.score_overlap if skin else None,
                                     components=list(trace),final_values=hud.final_values(),
                                     native_bounds_768={n:dict(left=r.left,top=r.top,right=r.right,bottom=r.bottom)
                                                        for n,r in getattr(hud,'bounds',{}).items()},
                                     path=str(path.relative_to(root)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        finally:
            renderer.release()
    (root/f'{label}-manifest.json').write_text(json.dumps(rows,indent=2))
    print(f'{label}: {len(rows)} EGL HUD PNGs and component traces')


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--label',choices=('before','after'),required=True)
    ap.add_argument('--repo',type=Path,required=True)
    args=ap.parse_args()
    capture(args.output,args.label,args.repo)
