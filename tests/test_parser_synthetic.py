"""End-to-end parse of a synthetic map: counts, combo numbering (incl.
spinner-forces-new-combo), timing finalization, SV, slider velocity/ticks,
stacking, HR flip."""
import math
import tempfile
from pathlib import Path

from osu_std_renderer.beatmap import Mods, load_full
from osu_std_renderer.beatmap.objects import Slider, Spinner

MAP = """osu file format v14

[General]
AudioFilename: audio.mp3
SampleSet: Soft
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:Test
Artist:Unit
Creator:R3D
Version:Scaffold

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:9
SliderMultiplier:1.0
SliderTickRate:1

[Events]
0,0,"bg.jpg",0,0
2,5000,6000

[TimingPoints]
0,500,4,2,1,60,1,0
2000,-50,4,2,1,60,0,1

[Colours]
Combo1 : 255,0,0
Combo2 : 0,255,0

[HitObjects]
100,100,0,5,0,0:0:0:0:
100,100,100,1,0,0:0:0:0:
100,100,200,1,0,0:0:0:0:
256,192,1000,2,0,L|506:192,1,250,0|2,0:0|0:0,0:0:0:0:
300,200,2500,2,0,P|350:250|400:200,1,100,0|0,0:0|0:0,0:0:0:0:
256,192,3000,12,0,4000,0:0:0:0:
400,300,4500,5,0,0:0:0:0:
"""


def _write_map() -> Path:
    d = Path(tempfile.mkdtemp(prefix="stdscaffold_"))
    p = d / "test.osu"
    p.write_text(MAP, encoding="utf-8")
    return p


def test_full_parse():
    bm = load_full(_write_map())
    assert bm.version == 14
    assert bm.mode == 0
    assert bm.name == "Test" and bm.creator == "R3D"
    assert bm.timings.base_set == 2          # Soft
    assert bm.combo_colors == [(255, 0, 0), (0, 255, 0)]
    assert len(bm.pauses) == 1 and bm.pauses[0].start_time == 5000

    # counts (pass-1 and pass-3 agree)
    assert (bm.circles, bm.sliders, bm.spinners) == (4, 2, 1)
    assert len(bm.hit_objects) == 7

    # timing finalization: green inherits red's base; SV ratio 0.5 → clamped path
    red = bm.timings.get_point_at(0)
    green = bm.timings.get_point_at(2100)
    assert not red.inherited and red.beat_length == 500
    assert green.inherited and green.beat_length_base == 500
    assert abs(green.get_ratio() - 0.5) < 1e-9
    assert abs(green.get_beat_length() - 250) < 1e-9

    # combo numbering: c,c,c,slider(no NC) → set0 nums 1..4; slider2 → num5;
    # spinner (type12: spinner+NC) → set1; spinner FORCES next new → set2
    nums = [(o.combo_set, o.combo_number) for o in bm.hit_objects]
    assert nums == [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 1), (2, 1)]
    assert bm.hit_objects[4].last_in_combo          # before the spinner
    assert bm.hit_objects[-1].last_in_combo         # map end

    # slider on the red line: velocity = 100*1.0/500 = 0.2 px/ms
    sl = bm.hit_objects[3]
    assert isinstance(sl, Slider)
    assert abs(sl.velocity - 0.2) < 1e-9
    assert abs(sl.part_len - 1250) < 1e-6           # 250px / 0.2
    assert abs(sl.end_time - 2250) < 1e-6
    # tickDistance = 100*mult/tickrate = 100px → ticks at 100,200 (250-2 edge)
    assert abs(sl.tick_distance - 100) < 1e-9
    assert len(sl.tick_points) == 2
    assert abs(sl.tick_points[0].time - 1500) < 1e-6
    assert abs(sl.tick_points[1].time - 2000) < 1e-6
    assert sl.edge_sounds == [0, 2]
    # scorePath positionAt: halfway in time = halfway along a linear slider
    mx, my = sl.position_at(1000 + 625)
    assert abs(mx - 381) < 1e-6 and abs(my - 192) < 1e-6

    # slider after the green line: SV 2x → velocity 0.4 px/ms
    sl2 = bm.hit_objects[4]
    assert abs(sl2.velocity - 0.4) < 1e-9
    assert abs(sl2.end_time - (2500 + 100 / 0.4)) < 1e-6

    # spinner end time
    sp = bm.hit_objects[5]
    assert isinstance(sp, Spinner) and sp.end_time == 4000

    # stacking: three circles at (100,100) within threshold → stacks 2,1,0
    threshold = math.floor(bm.diff.preempt * bm.stack_leniency)
    stacks = [o.stack_index_map.get(threshold, 0) for o in bm.hit_objects[:3]]
    assert stacks == [2, 1, 0]
    # offset applied: stackIndex * radius / 10, up-left
    x0, y0 = bm.hit_objects[0].get_stacked_start_position(bm.diff)
    off = 2 * bm.diff.circle_radius / 10
    assert abs(x0 - (100 - off)) < 1e-9 and abs(y0 - (100 - off)) < 1e-9
    x2, y2 = bm.hit_objects[2].get_stacked_start_position(bm.diff)
    assert (x2, y2) == (100.0, 100.0)


def test_hr_flip():
    bm = load_full(_write_map(), mods=Mods.HARD_ROCK)
    obj = bm.hit_objects[2]  # unstacked-after-flip? stacking still applies
    x, y = obj.get_stacked_start_position(bm.diff)
    # vFlip only: X unchanged, Y = 384-100 = 284 (stack offset 0 for this one)
    assert abs(x - 100.0) < 1e-9
    assert abs(y - 284.0) < 1e-9
    # raw geometry untouched
    assert obj.start_pos_raw == (100.0, 100.0)


def test_ar_falls_back_to_od_when_missing():
    text = MAP.replace("ApproachRate:9\n", "")
    d = Path(tempfile.mkdtemp(prefix="stdscaffold_"))
    p = d / "noar.osu"
    p.write_text(text, encoding="utf-8")
    bm = load_full(p)
    assert bm.diff.ar == bm.diff.od == 7
