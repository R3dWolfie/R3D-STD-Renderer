"""Debug plot — CPU-only (PIL, no GL): draw the first N seconds of parsed
objects into a PNG. Proves parser → curves → positions end to end:
circles as rings, slider paths as polylines from StdSliderPath positions,
ticks, stacking offsets applied via modify_position, HR flip testable.

    python -m osu_std_renderer.tools.plot_map MAP.osu OUT.png \
        [--seconds 30] [--hr] [--zoom OUT2.png]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from ..beatmap import Mods, load_full
from ..beatmap.objects import Slider, Spinner
from ..render.playfield import PlayfieldCamera

# a readable default combo palette (osu defaults, §3.2)
_PALETTE = [(255, 192, 0), (0, 202, 0), (18, 124, 255), (242, 24, 57),
            (255, 128, 255), (128, 255, 255)]


def plot(osu_path: Path, out_path: Path, *, seconds: float = 30.0,
         hr: bool = False, size: tuple[int, int] = (1600, 1200),
         window: tuple[float, float] | None = None) -> Path:
    bm = load_full(osu_path, mods=Mods.HARD_ROCK if hr else 0)
    cam = PlayfieldCamera(screen_w=size[0], screen_h=size[1])
    d = bm.diff
    r = cam.len_to_screen(d.circle_radius)

    if window is None:
        t0 = min(o.start_time for o in bm.hit_objects)
        window = (t0, t0 + seconds * 1000.0)

    img = Image.new("RGB", size, (18, 18, 24))
    draw = ImageDraw.Draw(img)
    # playfield bounds
    tl = cam.to_screen(0, 0)
    br = cam.to_screen(512, 384)
    draw.rectangle([tl, br], outline=(70, 70, 90), width=2)

    objs = [o for o in bm.hit_objects if window[0] <= o.start_time <= window[1]]
    for obj in reversed(objs):  # later objects under earlier (osu draw order)
        color = _PALETTE[obj.combo_set % len(_PALETTE)]
        if isinstance(obj, Spinner):
            cx, cy = cam.to_screen(256, 192)
            rr = cam.len_to_screen(180)
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline=(120, 120, 140), width=3)
            continue
        if isinstance(obj, Slider):
            # slider path polyline, stacking/HR applied per point
            pts = [cam.to_screen(*obj.modify_position(p, d))
                   for p in obj.multi_curve.path]
            if len(pts) >= 2:
                body_w = max(2, int(r * 2))
                draw.line(pts, fill=tuple(c // 3 for c in color), width=body_w,
                          joint="curve")
                draw.line(pts, fill=tuple(c // 2 for c in color), width=3)
            for tp in obj.tick_points:
                tx, ty = cam.to_screen(*obj.modify_position(tp.pos, d))
                draw.ellipse([tx - 3, ty - 3, tx + 3, ty + 3], fill=(255, 255, 255))
            ex, ey = cam.to_screen(*obj.get_stacked_end_position(d))
            draw.ellipse([ex - r * 0.6, ey - r * 0.6, ex + r * 0.6, ey + r * 0.6],
                         outline=color, width=3)
        # circle / slider head ring
        x, y = cam.to_screen(*obj.get_stacked_start_position(d))
        draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=4)
        stack = obj.get_stack_index(d)
        label = f"{obj.combo_number}" + (f"+{stack}" if stack else "")
        draw.text((x - 4 * len(label), y - 7), label, fill=(240, 240, 240))

    hdr = (f"{bm.artist} - {bm.name} [{bm.difficulty_name}]  "
           f"{'HR ' if hr else ''}CS{d.cs:g} r={d.circle_radius:.1f}px "
           f"AR{d.ar:g} pre={d.preempt}ms  "
           f"t={window[0]:.0f}..{window[1]:.0f}ms  {len(objs)} objects")
    draw.text((14, 10), hdr, fill=(220, 220, 220))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plot_map", description=__doc__)
    ap.add_argument("osu", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--hr", action="store_true")
    ap.add_argument("--zoom", type=Path, default=None,
                    help="also write a zoomed detail (first slider ±4s)")
    args = ap.parse_args(argv)

    out = plot(args.osu, args.out, seconds=args.seconds, hr=args.hr)
    print(f"wrote {out}")

    if args.zoom is not None:
        bm = load_full(args.osu)
        sl = next((o for o in bm.hit_objects if isinstance(o, Slider)), None)
        centre = sl.start_time if sl else bm.hit_objects[0].start_time
        out2 = plot(args.osu, args.zoom, hr=args.hr,
                    window=(centre - 500, centre + 3500))
        print(f"wrote {out2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
