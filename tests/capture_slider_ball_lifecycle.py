"""Bounded, native-resolution ball QA, including real-replay sprite traces.

Can run this script against the readonly baseline using PYTHONPATH. Each
label writes separate images. Existing Stage 3 fixtures are never modified.
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
from osu_std_renderer.render.playfield import PlayfieldCamera
from osu_std_renderer.render.scene import StdScene
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.slider_body import SliderBodyRenderer
from osu_std_renderer.render.textures import TextureBank
from osu_std_renderer.replay.replay import parse_replay
from osu_std_renderer.ruleset import StdRuleset
from osu_std_renderer.skin.skin import Skin
from tests.capture_slider_stage3 import fixture


def capture(root, label, name, bm, frames, result, skin_path, cases):
    renderer = SpriteRenderer(1280, 720)
    rows = []
    try:
        resources = Skin(skin_dir=skin_path) if skin_path else None
        skin = SkinElements(resources, renderer) if resources else None
        bank = TextureBank(renderer)
        scene = StdScene(bm, frames, PlayfieldCamera(1280, 720), renderer,
                         SliderBodyRenderer(renderer.ctx, 1280, 720), bank,
                         judgments=result, skin_elems=skin,
                         draw_cursor=False, draw_approach_circles=False,
                         draw_combo_numbers=False, draw_follow_points=False,
                         draw_judgment_popups=False, draw_hit_lighting=False)
        variant = "argon" if skin is None else "legacy" if skin.has("sliderb") else "default"
        selected = [str(tf.path.resolve()) for tf in resources.find_frames("sliderb")] if resources else []
        original = renderer.draw
        sprites = []

        def spy(batch):
            sprites.extend(dict(key=s.texture_key, alpha=s.color[3], x=s.x, y=s.y,
                                w=s.w, h=s.h, additive=s.additive) for s in batch)
            original(batch)

        renderer.draw = spy
        for index, role in cases:
            obj = bm.hit_objects[index]
            end = obj.get_end_time()
            verdict = result.verdict_for(obj)
            parent = next((e for e in verdict.slider_visuals.model_events_at(math.inf)
                           if e.kind == "slider_judged"), None)
            # Absolute beatmap-clock 60 fps grid, not rounded integer ms.
            boundary = math.ceil(end * 60 / 1000)
            for offset in (-1, 0, 1, 3, 7, 14):
                frame = boundary + offset
                time = frame * 1000 / 60
                scene._advance(time)
                sprites.clear()
                renderer.begin((0, 0, 0))
                scene._draw_slider(obj, time, [])
                path = root / label / name / f"obj{index:03d}-frame{frame:05d}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(renderer.read_rgb()).save(path)
                rows.append(dict(case=role, object=index, spans=obj.repeat_count,
                                 end_ms=end, frame=frame, time_ms=time,
                                 parent_notification_ms=parent.time_ms if parent else None,
                                 parent_anchor_ms=parent.transform_time_ms if parent else None,
                                 parent_hit=parent.hit if parent else None,
                                 tracked=verdict.tracked_at(min(time, end)), variant=variant,
                                 ball_resources=selected, sprites=list(sprites),
                                 path=str(path.relative_to(root)),
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    finally:
        renderer.release()
    return rows


def write_viewer(root):
    """Toggle identical original PNGs without scaling or video compression."""
    if not all((root / f"{label}-manifest.json").exists() for label in ("before", "after")):
        return
    manifests = {label: json.loads((root / f"{label}-manifest.json").read_text())["files"]
                 for label in ("before", "after")}
    html = '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Slider ball lifecycle comparison</title><style>
body{background:#151619;color:#eee;font:16px system-ui;margin:20px}
header{position:sticky;top:0;background:#151619;padding:8px 0;max-width:1280px}
select,button{font:inherit;padding:5px;margin-right:12px;max-width:900px}
img{display:block;width:1280px;height:720px;max-width:none}
pre{font:13px monospace;white-space:pre-wrap;max-width:1280px}
</style><header><strong>Slider ball · original GL PNGs · 1280×720</strong>
<p><select id="case"></select><button id="toggle">Before / after (B)</button>
<strong id="version"></strong></p><div id="timing"></div>
<small>100% zoom preserves original pixels. The grey follow ring can remain
after the ball disappears. These are R3D comparisons, not native lazer captures.</small>
</header><img id="image" alt="Isolated slider frame"><pre id="trace"></pre><script>
const data=MANIFESTS;
const choice=document.querySelector('#case');
let version='after';
data.after.forEach((r,i)=>choice.add(new Option(
 `${r.path.split('/')[1]} · object ${r.object} · ${r.case} · frame ${r.frame} · ${r.time_ms.toFixed(3)} ms`,i)));
choice.value=data.after.findIndex(r=>r.path.includes('/real/')&&r.object===0&&r.frame===612);
function show(){
 const r=data[version][Number(choice.value)];
 document.querySelector('#image').src=r.path;
 document.querySelector('#version').textContent=version.toUpperCase();
 document.querySelector('#timing').textContent=
  `Ball: ${r.variant} · end ${r.end_ms.toFixed(3)} · notification ${r.parent_notification_ms} · anchor ${r.parent_anchor_ms}`;
 document.querySelector('#trace').textContent='Resolved ball: '+(r.ball_resources.join(', ')||r.variant+' procedural')+'\\n'+
  r.sprites.map(s=>`${s.key}: effective alpha ${s.alpha.toFixed(6)}`).join('\\n');
}
function toggle(){version=version==='after'?'before':'after';show()}
choice.onchange=show;document.querySelector('#toggle').onclick=toggle;
document.addEventListener('keydown',e=>{if(e.key.toLowerCase()==='b')toggle()});show();
</script></html>'''
    (root / "comparison.html").write_text(html.replace("MANIFESTS", json.dumps(manifests)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label", required=True, choices=("before", "after"))
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    bm, frames, result = fixture(root / "fixture")
    # This is a distinct fixture: Stage 3's 'legacy' supplies ONLY follow.
    ball_skin = root / "fixture" / "legacy-ball"
    ball_skin.mkdir(exist_ok=True)
    (ball_skin / "skin.ini").write_text("[General]\nName: Ball lifecycle QA\nVersion: 2.7\n")
    ball = Image.new("RGBA", (128, 128))
    ImageDraw.Draw(ball).ellipse((4, 4, 123, 123), fill=(255, 40, 40, 255))
    ball.save(ball_skin / "sliderb.png")
    Image.open(root / "fixture/legacy/sliderfollowcircle.png").save(ball_skin / "sliderfollowcircle.png")
    rows = []
    for variant, path in (("argon", None), ("default-ball-legacy-follow", root / "fixture/legacy"),
                          ("legacy-ball", ball_skin)):
        rows.extend(capture(root, args.label, variant, bm, frames, result, path,
                            ((0, "tracked-reacquired"), (1, "late-short"), (2, "repeat"))))
    replay = args.repo / "test-render/test.osr"
    frames, meta = parse_replay(replay)
    bm = load_full(find_osu_file(args.repo / "test-render/beatmap", meta.beatmap_md5), mods=meta.mods)
    result = StdRuleset(bm, frames, meta).run()
    rows.extend(capture(root, args.label, "real", bm, frames, result,
                        args.repo / "test-render/skin",
                        ((0, "tracked-body-gone"), (12, "repeat"), (46, "detached"),
                         (712, "missed-head-delayed-parent"))))
    (root / f"{args.label}-manifest.json").write_text(json.dumps(dict(
        label=args.label, resolution=[1280, 720], renderer="GL",
        clock="absolute map frame n * 1000/60 ms; isolated slider; no HUD/cursor",
        authority="chronological source-model surrogate, not native callback/pixel capture",
        files=rows), indent=2))
    write_viewer(root)
    print(f"{args.label}: {len(rows)} PNGs + sprite traces in {root}")


if __name__ == "__main__":
    main()
