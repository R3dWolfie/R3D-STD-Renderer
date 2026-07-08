"""End-to-end parse verification on a real map (+ optional replay):
counts vs the raw file, finalized timing, combos, stacking, slider paths,
difficulty numbers. Exits non-zero on any mismatch.

    python -m osu_std_renderer.tools.parse_summary MAP.osu [REPLAY.osr]
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..beatmap import load_full
from ..beatmap.objects import Slider, Spinner
from ..beatmap.objects.base import (TYPE_CIRCLE, TYPE_LONGNOTE, TYPE_SLIDER,
                                    TYPE_SPINNER)


def raw_counts(path: Path) -> tuple[int, int, int]:
    """Independent count straight off the file, to assert the parser against."""
    circles = sliders = spinners = 0
    section = ""
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line.strip("[]")
            continue
        if section != "HitObjects" or not line:
            continue
        arr = line.split(",")
        if len(arr) < 5:
            continue
        try:
            t = int(float(arr[3]))
        except ValueError:
            continue
        if t & TYPE_CIRCLE:
            circles += 1
        elif t & (TYPE_SLIDER | TYPE_LONGNOTE):
            sliders += 1
        elif t & TYPE_SPINNER:
            spinners += 1
    return circles, sliders, spinners


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    osu_path = Path(argv[0])
    osr_path = Path(argv[1]) if len(argv) > 1 else None

    bm = load_full(osu_path)
    d = bm.diff

    print(f"== {bm.artist} - {bm.name} [{bm.difficulty_name}] by {bm.creator}")
    print(f"   format v{bm.version}  mode={bm.mode}  md5-dir={osu_path.parent.name}")

    # 1. object counts must match an independent scan of the file
    want = raw_counts(osu_path)
    n_sliders = sum(isinstance(o, Slider) for o in bm.hit_objects)
    n_spinners = sum(isinstance(o, Spinner) for o in bm.hit_objects)
    n_circles = len(bm.hit_objects) - n_sliders - n_spinners
    got = (n_circles, n_sliders, n_spinners)
    ok = got == want
    print(f"   objects parsed c/s/sp = {got}  file = {want}  "
          f"{'OK' if ok else 'MISMATCH'}")
    assert ok, f"object counts differ: parsed {got} vs file {want}"
    assert (bm.circles, bm.sliders, bm.spinners) == want, \
        "pass-1 counts differ from file"

    # 2. timing points finalized
    reds = len(bm.timings.original_points)
    greens = len(bm.timings.points) - reds
    assert bm.timings.has_points(), "no timing points"
    for p in bm.timings.points:
        assert p.beat_length_base > 0, "green line without inherited base"
    print(f"   timing: {reds} red + {greens} green lines, "
          f"BPM {60000 / bm.timings.original_points[0].beat_length:.2f} first, "
          f"{bm.min_bpm:.0f}-{bm.max_bpm:.0f} range — finalized OK")

    # 3. combo numbers assigned
    assert all(o.combo_number >= 1 for o in bm.hit_objects)
    assert bm.hit_objects[0].new_combo and bm.hit_objects[0].combo_set == 0
    max_combo_num = max(o.combo_number for o in bm.hit_objects)
    n_sets = bm.hit_objects[-1].combo_set + 1
    print(f"   combos: {n_sets} sets, longest combo {max_combo_num} — assigned OK")

    # 4. stacking applied
    import math
    threshold = math.floor(d.preempt * bm.stack_leniency)
    stacked = [o for o in bm.hit_objects if o.stack_index_map.get(threshold, 0) != 0]
    assert all(threshold in o.stack_index_map for o in bm.hit_objects), \
        "stacking never ran"
    off_px = (stacked[0].stack_index_map[threshold] * d.circle_radius / 10
              if stacked else 0.0)
    print(f"   stacking: threshold={threshold}ms, {len(stacked)} objects stacked "
          f"(first offset {off_px:.2f}px) — applied OK")

    # 5. slider paths built via sliderpath + velocity/ticks
    sl = next((o for o in bm.hit_objects if isinstance(o, Slider)), None)
    if sl is not None:
        assert sl.multi_curve.path and sl.multi_curve.distance > 0
        assert abs(sl.multi_curve.distance - sl.pixel_length) < 1.0, \
            "path not truncated to pixelLength"
        assert sl.end_time > sl.start_time and sl.score_path
        mid = sl.position_at((sl.start_time + sl.end_time) / 2)
        print(f"   first slider @{sl.start_time:.0f}ms: len={sl.pixel_length:.1f}px "
              f"×{sl.repeat_count}, velocity={sl.velocity:.4f}px/ms, "
              f"tickDistance={sl.tick_distance:.1f}px, "
              f"{len(sl.tick_points)} ticks, {len(sl.tick_reverse)} reverses, "
              f"{len(sl.score_path)} scorePath segs, midpoint=({mid[0]:.1f},{mid[1]:.1f})")
    else:
        print("   (map has no sliders)")

    # 6. difficulty numbers (§2.4)
    print(f"   diff: CS{d.cs:g} → radius {d.circle_radius:.4f}px | "
          f"AR{d.ar:g} → preempt {d.preempt}ms fadeIn {d.time_fade_in:.1f}ms | "
          f"OD{d.od:g} → ±{d.hit300_u:.1f}/±{d.hit100_u:.1f}/±{d.hit50_u:.1f}ms | "
          f"ARreal {d.ar_real:.2f} ODreal {d.od_real:.2f}")

    # 7. optional replay
    if osr_path is not None:
        from ..replay import parse_replay
        frames, meta = parse_replay(osr_path)
        assert frames, "no replay frames"
        f0, fl = frames[0], frames[-1]
        print(f"   replay: {meta.player_name}  mode={meta.mode} mods={meta.mods:#x} "
              f"{meta.count_300}/{meta.count_100}/{meta.count_50}/{meta.count_miss} "
              f"{meta.accuracy}% {meta.grade}")
        print(f"           {len(frames)} frames, first ({f0.x:.1f},{f0.y:.1f})@{f0.time_ms}ms, "
              f"last ({fl.x:.1f},{fl.y:.1f})@{fl.time_ms}ms")
        if meta.beatmap_md5 and meta.beatmap_md5 != osu_path.parent.name:
            print(f"           note: replay md5 {meta.beatmap_md5} != map dir")

    print("PARSE SUMMARY: all assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
