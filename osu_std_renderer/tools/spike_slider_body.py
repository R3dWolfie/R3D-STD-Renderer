"""Phase-0 slider-body spike — renders the acceptance cases to PNGs.

    .venv/bin/python -m osu_std_renderer.tools.spike_slider_body \
        --out /home/foof/tmp_stdspike \
        [--cache /var/mnt/ASUStor-Samsung/R3DManiaORDRBot/beatmaps]

Cases (APPROACH.md §6.3 + the spike brief):
  1. curved Bezier S-snake, CS4, reference hitcircle ring at the head
  2. self-overlap union stress: crossing linear zigzag, kick-back stub,
     aspire-style continuously self-overlapping zigzag — over a light
     background so double-darkening would be obvious
  3. perfect-circle (P) arc + tight-curvature hairpin + donut-degenerate arc
  4. a real slider from the bot's beatmap cache at its real position/CS,
     plus a copy with the path polyline overlaid (geometry alignment)
  5. snaking: the same slider at 25/50/75/100% path progress

Also prints per-case build_body timing (GPU-synced, ms).
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..beatmap.objects.slider import Slider
from ..beatmap.difficulty import Difficulty
from ..beatmap.parser import parse_beatmap_file, parse_objects
from ..curves import StdSliderPath
from ..render.context import create_context
from ..render.gl import SpriteRenderer
from ..render.playfield import PlayfieldCamera
from ..render.slider_body import (BodyStyle, DEFAULT_COMBO_COLORS,
                                  SliderBodyRenderer)

W, H = 1920, 1080


def _osu_radius(cs: float) -> float:
    return Difficulty(5, 5, cs, 5).circle_radius


def _screen_path(cam: PlayfieldCamera, path):
    return [cam.to_screen(x, y) for (x, y) in path]


def _save(sr: SpriteRenderer, out: Path, name: str) -> Image.Image:
    img = Image.fromarray(sr.read_rgb())
    img.save(out / name)
    print(f"  wrote {out / name}")
    return img


def _ring(img: Image.Image, cx: float, cy: float, r: float, color, width=2):
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)


def _time_build(bodies: SliderBodyRenderer, pts, radius, style, n=50) -> float:
    bodies.build_body(pts, radius, style)  # warm-up
    bodies.ctx.finish()
    t0 = time.perf_counter()
    for _ in range(n):
        bodies.build_body(pts, radius, style)
    bodies.ctx.finish()
    return (time.perf_counter() - t0) / n * 1000.0


def _path(curve: str, x0: float, y0: float, length=None):
    return StdSliderPath(curve, x0, y0, length).path


def case1_snake(sr, bodies, cam, out):
    print("case 1: bezier S-snake (CS4)")
    path = _path("B|170:60|300:330|450:80", 30, 300)
    pts = _screen_path(cam, path)
    radius = cam.len_to_screen(_osu_radius(4.0))
    style = BodyStyle(body_color=DEFAULT_COMBO_COLORS[2])
    sr.begin(clear=(0.08, 0.08, 0.10))
    body = bodies.build_body(pts, radius, style)
    bodies.draw_body(body, sr.fbo)
    img = _save(sr, out, "case1_snake.png")
    # reference hitcircle ring at the head, exactly CircleRadius:
    hx, hy = pts[0]
    _ring(img, hx, hy, radius, (255, 60, 60))
    img.save(out / "case1_snake_ring.png")
    print(f"  build_body: {_time_build(bodies, pts, radius, style):.2f} ms "
          f"({len(pts)} path points)")


def case2_overlap(sr, bodies, cam, out):
    print("case 2: self-overlap union (light bg — darkened patches would show)")
    sr.begin(clear=(0.45, 0.45, 0.48))
    # crossing linear zigzag (sharp joins + two crossings)
    zig = _screen_path(cam, _path("L|420:80|60:80|420:300", 60, 300))
    r4 = cam.len_to_screen(_osu_radius(4.0))
    b = bodies.build_body(zig, r4, BodyStyle(body_color=DEFAULT_COMBO_COLORS[0]))
    bodies.draw_body(b, sr.fbo)
    # kick-back stub: 40px path, CS2 radius (path shorter than the radius)
    stub = _screen_path(cam, _path("L|300:40", 260, 40))
    r2 = cam.len_to_screen(_osu_radius(2.0))
    b = bodies.build_body(stub, r2, BodyStyle(body_color=DEFAULT_COMBO_COLORS[1]))
    bodies.draw_body(b, sr.fbo)
    # aspire-style zigzag: amplitude 22px < CS4 radius → continuous overlap
    anchors = "|".join(f"{100 + i * 30}:{360 + (22 if i % 2 else -22)}"
                       for i in range(1, 11))
    zz = _screen_path(cam, _path("B|" + anchors, 70, 338))
    b = bodies.build_body(zz, r4, BodyStyle(body_color=DEFAULT_COMBO_COLORS[3]))
    bodies.draw_body(b, sr.fbo)
    _save(sr, out, "case2_overlap.png")
    print(f"  build_body (zigzag): {_time_build(bodies, zz, r4, BodyStyle()):.2f} ms")


def case3_arc_hairpin(sr, bodies, cam, out):
    print("case 3: perfect-circle arc + hairpin + donut-degenerate arc")
    sr.begin(clear=(0.08, 0.08, 0.10))
    r4 = cam.len_to_screen(_osu_radius(4.0))
    arc = _screen_path(cam, _path("P|256:64|390:320", 120, 320))
    b = bodies.build_body(arc, r4, BodyStyle(body_color=DEFAULT_COMBO_COLORS[1]))
    bodies.draw_body(b, sr.fbo)
    # hairpin: out-and-back arms ~40px apart (< 2R → the fold overlaps)
    hp = _screen_path(cam, _path("B|430:150|430:190|80:180", 80, 140))
    b = bodies.build_body(hp, r4, BodyStyle(body_color=DEFAULT_COMBO_COLORS[3]))
    bodies.draw_body(b, sr.fbo)
    # arc radius 40 < CircleRadius(CS4)=36.5·scl … tube swallows the hole
    donut = _screen_path(cam, _path("P|216:302|256:342", 256, 262))
    b = bodies.build_body(donut, r4, BodyStyle(body_color=DEFAULT_COMBO_COLORS[2]))
    bodies.draw_body(b, sr.fbo)
    _save(sr, out, "case3_arc_hairpin.png")
    print(f"  build_body (hairpin): {_time_build(bodies, hp, r4, BodyStyle()):.2f} ms")


def _iter_osu_files(cache: Path):
    """The cache is danser-songs-dir shaped: md5-named mapset directories,
    each holding the set's .osu diffs (§2.5 OsuSongsDir)."""
    for entry in sorted(cache.iterdir()):
        if entry.is_file() and entry.suffix == ".osu":
            yield entry
        elif entry.is_dir():
            try:
                yield from sorted(entry.glob("*.osu"))
            except OSError:
                continue


def _find_real_slider(cache: Path, max_std_files: int = 60):
    """Longest curvy slider across std maps in the bot's beatmap cache
    (bounded scan — the cache lives on CIFS)."""
    best = None  # (score, beatmap, slider, name)
    seen_std = 0
    for f in _iter_osu_files(cache):
        try:
            with open(f, "rb") as fh:
                head = fh.read(2048).decode("utf-8", "replace")
        except OSError:
            continue
        norm = head.replace("Mode:0", "Mode: 0")
        if "Mode:" in norm and "Mode: 0" not in norm:
            continue
        try:
            bm = parse_beatmap_file(f)
            if bm.mode != 0:
                continue
            parse_objects(f, bm, stack_enabled=False)
        except Exception:  # noqa: BLE001
            continue
        seen_std += 1
        for obj in bm.hit_objects:
            if not isinstance(obj, Slider) or obj.multi_curve is None:
                continue
            if len(obj.multi_curve.path) < 12:
                continue  # want a *curvy* one
            score = obj.pixel_length
            if best is None or score > best[0]:
                best = (score, bm, obj, f.parent.name)
        if seen_std >= max_std_files or (best and best[0] > 600):
            break
    return best


def case45_real(sr, bodies, cam, out, cache: Path):
    found = _find_real_slider(cache)
    if not found:
        print("case 4/5: SKIPPED — no std slider found in cache", cache)
        return
    score, bm, sl, md5 = found
    print(f"case 4: real slider — {bm.artist} - {bm.name} [{bm.difficulty_name}] "
          f"(md5 {md5}) CS{bm.diff.base_cs:g}, pixelLength {sl.pixel_length:g}, "
          f"{len(sl.multi_curve.path)} path points")
    radius = cam.len_to_screen(bm.diff.circle_radius)
    pts = _screen_path(cam, sl.multi_curve.path)
    combo = (tuple(c / 255 for c in bm.combo_colors[0])
             if bm.combo_colors else DEFAULT_COMBO_COLORS[0])
    style = BodyStyle(body_color=combo)

    sr.begin(clear=(0.08, 0.08, 0.10))
    body = bodies.build_body(pts, radius, style)
    bodies.draw_body(body, sr.fbo)
    img = _save(sr, out, "case4_real_slider.png")
    # geometry-alignment copy: polyline + head ring drawn over the render
    d = ImageDraw.Draw(img)
    d.line([tuple(p) for p in pts], fill=(0, 255, 90), width=1)
    _ring(img, pts[0][0], pts[0][1], radius, (255, 60, 60))
    img.save(out / "case4_real_slider_overlay.png")
    print(f"  wrote {out / 'case4_real_slider_overlay.png'}")
    print(f"  build_body: {_time_build(bodies, pts, radius, style):.2f} ms")

    print("case 5: snaking 25/50/75/100%")
    for frac in (0.25, 0.5, 0.75, 1.0):
        sr.begin(clear=(0.08, 0.08, 0.10))
        body = bodies.build_body(pts, radius, style, snake=(0.0, frac))
        bodies.draw_body(body, sr.fbo)
        _save(sr, out, f"case5_snake_{int(frac * 100):03d}.png")
    return pts, radius, style


def snake_mp4(sr, bodies, out: Path, pts, radius, style,
              seconds=3.0, fps=60) -> None:
    """Optional: the snake growing, as a short mp4 (proves per-frame
    rebuild cost is a non-issue)."""
    from ..record.encode import FfmpegPipe, build_ffmpeg_cmd, probe_encoder
    dst = out / "snake_grow.mp4"
    cmd = build_ffmpeg_cmd(encoder=probe_encoder(), resolution=(W, H),
                           fps=fps, output_path=dst)
    n = int(seconds * fps)
    t0 = time.perf_counter()
    with FfmpegPipe(cmd) as pipe:
        for i in range(n):
            frac = min(1.0, (i + 1) / (n * 0.85))   # grow, then hold
            sr.begin(clear=(0.08, 0.08, 0.10))
            body = bodies.build_body(pts, radius, style, snake=(0.0, frac))
            bodies.draw_body(body, sr.fbo)
            pipe.push(sr.read_rgb())
    dt = time.perf_counter() - t0
    print(f"  wrote {dst} ({n} frames in {dt:.1f}s = {n / dt:.0f} fps "
          "incl. readback+encode)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("/home/foof/tmp_stdspike"))
    ap.add_argument("--cache", type=Path,
                    default=Path("/var/mnt/ASUStor-Samsung/R3DManiaORDRBot/beatmaps"))
    ap.add_argument("--mp4", action="store_true",
                    help="also encode a snake-in mp4 of the real slider")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    ctx = create_context()
    sr = SpriteRenderer(W, H, ctx=ctx)
    bodies = SliderBodyRenderer(ctx, W, H)
    cam = PlayfieldCamera(W, H)

    case1_snake(sr, bodies, cam, args.out)
    case2_overlap(sr, bodies, cam, args.out)
    case3_arc_hairpin(sr, bodies, cam, args.out)
    real = None
    if args.cache.is_dir():
        real = case45_real(sr, bodies, cam, args.out, args.cache)
    else:
        print("case 4/5: SKIPPED — cache dir missing:", args.cache)
    if args.mp4 and real is not None:
        snake_mp4(sr, bodies, args.out, *real)

    bodies.release()
    sr.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
