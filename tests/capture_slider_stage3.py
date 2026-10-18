"""Bounded GL evidence; works with the pre-Stage-3 checkout via PYTHONPATH.

python -m tests.capture_slider_stage3 --output /absolute/qa/folder --label after
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw
from osrparse import GameMode, Key, Mod, Replay, ReplayEventOsu

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.render.gl import SpriteRenderer
from osu_std_renderer.render.playfield import PlayfieldCamera
from osu_std_renderer.render.scene import StdScene
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.render.slider_body import SliderBodyRenderer
from osu_std_renderer.render.textures import TextureBank
from osu_std_renderer.replay.replay import StdFrame, parse_replay
from osu_std_renderer.ruleset import StdRuleset
from osu_std_renderer.skin.skin import Skin
from tests.test_ruleset import MAP_HEADER

CASES = ((650, "first-marker-entrance"), (1250, "press"),
         (1375, "release"), (1735, "reacquire"), (2270, "end-body-fade"),
         (2350, "end-follow-fade"), (4660, "late-short"),
         (8350, "repeat-freeze"), (8450, "repeat-freeze-later"),
         (12750, "missed-head-no-snaking-out"))
COLOUR = (26 / 255, 191 / 255, 1)


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    map_path = root / "stage3.osu"
    header = MAP_HEADER.replace("audio.mp3", "audio.wav").replace("RulesetTest", "Stage3VisualQA")
    header = header.replace("[HitObjects]", "[Colours]\nCombo1: 26,191,255\n\n[HitObjects]")
    objects = ("256,192,1000,2,0,B|300:100|400:280|450:192,1,250\n"
               "256,192,4500,2,0,L|266:192,1,10\n"
               "256,192,7000,2,0,B|300:100|400:280|450:192,3,120\n"
               "256,192,11000,2,0,L|356:192,4,100\n")
    map_path.write_text(header + objects, encoding="utf-8")
    with wave.open(str(root / "audio.wav"), "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 8000 * 14)
    bm = load_full(map_path)
    first, short, repeat, missed = bm.hit_objects
    frames = []
    for time in range(0, 13401, 10):
        x, y, keys = 0, 0, 0
        if 1000 <= time <= 2300:
            x, y = first.position_at(time)
            keys = 0 if 1350 <= time < 1650 else 4
        elif 4620 <= time <= 4800:
            x, y = short.position_at(short.get_start_time() if time == 4620 else time)
            keys = 4
        elif 7000 <= time <= 8900:
            x, y = repeat.position_at(time)
            keys = 4
        elif 10900 <= time <= 13200:
            # Hold before the head, outside it; move to the ball without a
            # new press. Nested results can hit while the head remains missed.
            x, y = missed.position_at(time) if time >= 11000 else (0, 0)
            keys = 4
        frames.append(StdFrame(time, x, y, keys))
    result = StdRuleset(bm, frames, lazer=False, reconcile=False).run()
    c300, c100, c50, miss = result.final_counts
    replay = Replay(GameMode.STD, 20200101, hashlib.md5(map_path.read_bytes()).hexdigest(),
                    "Stage3Synthetic", "", c300, c100, c50, 0, 0, miss, 0,
                    result.sim_max_combo, False, Mod.NoMod, [],
                    datetime(2026, 10, 11, tzinfo=timezone.utc),
                    [ReplayEventOsu(f.time_ms - (frames[i-1].time_ms if i else 0),
                                    f.x, f.y, Key(f.keys)) for i, f in enumerate(frames)],
                    0, 0)
    replay.write_path(root / "stage3.osr")
    # Use decoded OSR inputs in BOTH captures, including format precision.
    frames, meta = parse_replay(root / "stage3.osr")
    result = StdRuleset(bm, frames, meta, reconcile=False).run()
    (root / "replay-frames.json").write_text(json.dumps([dataclasses.asdict(f) for f in frames], indent=2))
    (root / "model-events.json").write_text(json.dumps([
        dataclasses.asdict(result.verdict_for(obj).slider_visuals) for obj in bm.hit_objects
    ], indent=2))
    for variant in ("legacy", "missing-follow"):
        skin = root / variant
        skin.mkdir(exist_ok=True)
        (skin / "skin.ini").write_text("[General]\nName: Stage3 QA\nVersion: 2.7\nAnimationFramerate: 10\n"
                                      "[Colours]\nCombo1: 26,191,255\n")
        if variant == "legacy":
            for index in (0, 1):
                image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
                ImageDraw.Draw(image).ellipse((5 + index*5, 5 + index*5, 250 - index*5, 250 - index*5),
                                             outline=(255, 255, 255, 255), width=8)
                image.save(skin / f"sliderfollowcircle-{index}.png")
                if index == 0:
                    image.save(skin / "sliderfollowcircle.png")  # baseline's single-frame loader
    with zipfile.ZipFile(root / "stage3.osz", "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in ("stage3.osu", "audio.wav"):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, (root / name).read_bytes())
    return bm, frames, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label", required=True, choices=("before", "after"))
    args = parser.parse_args()
    root = args.output.resolve()
    bm, frames, result = fixture(root / "fixture")
    manifest = {"label": args.label, "resolution": [1280, 720], "files": [],
                "ruleset": "stable input; chronological lazer source-model visuals",
                "settings": "SnakingIn/Out on; merge/HD/TC/FR off; no HUD/cursor/approach/numbers/popups"}
    for variant in ("argon", "legacy", "missing-follow"):
        renderer = SpriteRenderer(1280, 720)
        try:
            bank = TextureBank(renderer)
            skin = (SkinElements(Skin(skin_dir=root / "fixture" / variant), renderer)
                    if variant != "argon" else None)
            bodies = SliderBodyRenderer(renderer.ctx, 1280, 720)
            scene = StdScene(bm, frames, PlayfieldCamera(1280, 720), renderer, bodies, bank,
                             judgments=result, skin_elems=skin, combo_colors=(COLOUR,),
                             draw_cursor=False, draw_approach_circles=False, draw_combo_numbers=False,
                             draw_follow_points=False, draw_judgment_popups=False, draw_hit_lighting=False)
            folder = root / args.label / variant
            folder.mkdir(parents=True, exist_ok=True)
            for time, case in CASES:
                path = folder / f"{time:05d}-{case}.png"
                Image.fromarray(scene.frame_rgb(time)).save(path)
                manifest["files"].append({"variant": variant, "time_ms": time, "case": case,
                                           "path": str(path.relative_to(root)),
                                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        finally:
            renderer.release()
    (root / f"{args.label}-manifest.json").write_text(json.dumps(manifest, indent=2))
    viewer = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Stage 3 slider comparison</title><style>
body { background:#151619; color:#eee; font:16px system-ui; margin:20px; }
header { position:sticky; top:0; background:#151619; padding:12px 0; }
select,button { font:inherit; padding:6px; margin-right:12px; }
img { display:block; width:1280px; height:720px; max-width:none; }
</style><header><strong>Stage 3 · original PNG pixels · 1280×720</strong>
<p><select id="variant"><option>argon</option><option>legacy</option>
<option>missing-follow</option></select><select id="time"></select>
<button id="toggle">Toggle before / after (B)</button><span id="label"></span></p>
Use 100% browser zoom. Toggle the same frame to compare; scroll if needed.</header>
<img id="frame" alt="Slider comparison frame"><script>
const cases = CASES_JSON;
const variant = document.querySelector('#variant'), time = document.querySelector('#time');
let version = 'after';
cases.forEach(([ms,name],i) => time.add(new Option(`${ms} ms · ${name}`,i)));
time.value = 2;
function show() {
 const [ms,name] = cases[Number(time.value)];
 document.querySelector('#frame').src = `${version}/${variant.value}/${String(ms).padStart(5,'0')}-${name}.png`;
 document.querySelector('#label').textContent = `${version.toUpperCase()} · ${variant.value}`;
}
function toggle() { version = version === 'after' ? 'before' : 'after'; show(); }
variant.onchange = time.onchange = show;
document.querySelector('#toggle').onclick = toggle;
document.addEventListener('keydown', e => { if (e.key.toLowerCase() === 'b') toggle(); });
show();</script></html>"""
    (root / "comparison.html").write_text(viewer.replace("CASES_JSON", json.dumps(CASES)))
    print(f"{args.label}: {len(manifest['files'])} native-resolution GL PNGs in {root}")


if __name__ == "__main__":
    main()
