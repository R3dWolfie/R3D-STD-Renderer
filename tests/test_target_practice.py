"""Target Practice (TP) — OsuModTargetPractice.cs (ppy/osu, MIT).

TP is a CONVERSION mod: it DISCARDS the map's objects and rebuilds it as seeded
"target" hit circles placed on the beat. A replay is played on the CONVERTED
map, so the render must reproduce lazer's exact conversion (which beats, and
each target's position) or the recorded cursor lands nowhere. Reproducible
because: the seed is persisted in the .osr (IHasSeed, ``Seed.Value ??= RNG.Next``
round-tripped through the ScoreInfo blob, key ``seed``); the RNG is .NET
System.Random (this renderer's bit-exact DotNetRandom); and target positions are
a pure function of (timing, breaks, last-object time, combo structure, radius,
seed) — no original geometry is read.

The load-bearing fidelity check is ``test_tp_first_target_matches_hand_trace``:
it recomputes object 0's placement straight from the C# using DotNetRandom
directly (NOT the module internals) and asserts a bit-for-bit match.

Pure CPU (no GL). Synthetic maps + .osr blobs, like test_pos_mods.
"""
from __future__ import annotations

import json
import lzma
import math
import struct
import tempfile
from datetime import datetime
from pathlib import Path

from osrparse import GameMode, Key, Mod, Replay
from osrparse.replay import ReplayEventOsu

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.beatmap.difficulty import Difficulty
from osu_std_renderer.beatmap.dotnet_random import DotNetRandom
from osu_std_renderer.beatmap.objects.circle import Circle
from osu_std_renderer.beatmap.objects.slider import Slider
from osu_std_renderer.beatmap.objects.spinner import Spinner
from osu_std_renderer.replay import parse_replay
from osu_std_renderer.replay.lazer_mods import (read_target_practice,
                                                target_practice_from_mods)

LAZER_GV = 30_000_016

# Map: circle + slider + circle in one combo, a spinner (its own combo), then a
# trailing circle — enough to exercise multi-combo distance scaling, the
# spinner-forced combo break and GetLastObjectTime = Max(EndTime) (the spinner
# ends at 4000 but the last circle is at 4600). No kiai (effects=0), no breaks.
MAP = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:TP
Artist:Unit
Creator:R3D
Version:TP

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,1,60,1,0

[HitObjects]
100,100,1000,1,0,0:0:0:0:
300,200,1750,2,0,B|360:250|400:200,1,140,0:0|0:0,0:0:0:0:
150,300,2100,1,0,0:0:0:0:
256,192,2850,12,0,4000,0:0:0:0:
190,210,4600,1,0,0:0:0:0:
"""

SEED = 4242

# Frozen layout for SEED=4242 on MAP (captured from the port; the first entry
# is independently re-derived from the C# in test_tp_first_target_matches...).
EXPECT_TIMES = [1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500]
EXPECT_POS = [
    (289.39581298828125, 148.62428283691406),
    (311.63616943359375, 119.68903350830078),
    (346.27899169921875, 108.21045684814453),
    (379.5573425292969, 123.1915512084961),
    (57.43381881713867, 324.7763366699219),
    (36.494956970214844, 36.494956970214844),
    (365.9952392578125, 36.494956970214844),
    (431.6982116699219, 347.5050354003906),
]


def _map(text: str = MAP) -> Path:
    d = Path(tempfile.mkdtemp(prefix="stdtp_"))
    p = d / "tp.osu"
    p.write_text(text, encoding="utf-8")
    return p


def _write_osr(dirpath: Path, name: str, mods_json, blob: bool = True) -> Path:
    ev = [ReplayEventOsu(0, 5.0, 5.0, Key(0)),
          ReplayEventOsu(10, 6.0, 6.0, Key.K1),
          ReplayEventOsu(-12345, 0.0, 0.0, Key(0))]
    r = Replay(GameMode.STD, LAZER_GV, "abc", "tester", "h",
               1, 0, 0, 0, 0, 0, 12345, 1, False, Mod.NoMod, None,
               datetime(2026, 1, 1), ev, 0, None)
    base = r.pack()
    if blob:
        raw = json.dumps({"mods": mods_json, "rank": "S",
                          "statistics": {}}).encode("utf-8")
        comp = lzma.compress(raw, format=lzma.FORMAT_ALONE)
        base = base + struct.pack("<i", len(comp)) + comp
    p = dirpath / name
    p.write_bytes(base)
    return p


# =========================================================================
# 1. reader — seed + metronome from the ScoreInfo blob
# =========================================================================

def test_tp_reader_seed_and_metronome():
    tp = target_practice_from_mods(
        [{"acronym": "TP", "settings": {"seed": 4242, "metronome": False}}])
    assert tp is not None and tp.seed == 4242 and tp.metronome is False

    tp2 = target_practice_from_mods([{"acronym": "TP", "settings": {"seed": 1}}])
    assert tp2.seed == 1 and tp2.metronome is True   # metronome default


def test_tp_reader_none_when_absent_or_seedless():
    assert target_practice_from_mods([{"acronym": "HD", "settings": {}}]) is None
    # A TP with no seed can't be reproduced — surfaces seed None (gated later).
    tp = target_practice_from_mods([{"acronym": "TP", "settings": {}}])
    assert tp is not None and tp.seed is None


def test_read_target_practice_from_osr_and_parse_replay():
    d = Path(tempfile.mkdtemp(prefix="stdtp_osr_"))
    p = _write_osr(d, "tp.osr",
                   [{"acronym": "TP", "settings": {"seed": 777}}])
    tp = read_target_practice(p)
    assert tp is not None and tp.seed == 777

    _, meta = parse_replay(p)
    assert meta.has_target_practice is True
    assert meta.target_practice_seed == 777
    assert meta.target_practice_metronome is True


def test_non_tp_replay_has_no_seed():
    d = Path(tempfile.mkdtemp(prefix="stdtp_non_"))
    p = _write_osr(d, "plain.osr", [{"acronym": "HD"}])
    _, meta = parse_replay(p)
    assert meta.has_target_practice is False
    assert meta.target_practice_seed is None


# =========================================================================
# 2. generation — beats, combo info, playfield, determinism
# =========================================================================

def _targets(seed=SEED, text=MAP, mods=0):
    return load_full(str(_map(text)), mods=mods,
                     target_practice_seed=seed).hit_objects


def test_tp_replaces_map_with_target_circles():
    bm = load_full(str(_map()), mods=0, target_practice_seed=SEED)
    # every object is now a plain circle (no sliders/spinners survive TP)
    assert all(isinstance(o, Circle) for o in bm.hit_objects)
    assert not any(isinstance(o, (Slider, Spinner)) for o in bm.hit_objects)
    assert len(bm.hit_objects) == len(EXPECT_TIMES)


def test_tp_beats_match_generate_beats():
    # 500 ms beat length, first object 1000, last-object time 4600 (circle) ->
    # beats 1000..4500 (4500+500=5000 > 4600), no breaks removed.
    times = [o.start_time for o in _targets()]
    assert times == EXPECT_TIMES


def test_tp_all_targets_inside_playfield():
    for o in _targets():
        x, y = o.start_pos_raw
        assert 0.0 <= x <= 512.0 and 0.0 <= y <= 384.0


def test_tp_combo_info_continuous():
    objs = _targets()
    # first object of the map always starts a combo; spinner splits combos so
    # the copied indices regroup to 0,0,0,0,1,1,1,1.
    assert [o.combo_set for o in objs] == [0, 0, 0, 0, 1, 1, 1, 1]
    assert [o.combo_number for o in objs] == [1, 2, 3, 4, 1, 2, 3, 4]
    assert objs[0].new_combo is True and objs[4].new_combo is True
    assert objs[3].last_in_combo is True and objs[-1].last_in_combo is True


def test_tp_deterministic_per_seed():
    a = _targets(seed=SEED)
    b = _targets(seed=SEED)
    assert len(a) == len(b)
    assert all(x.start_pos_raw == y.start_pos_raw and x.start_time == y.start_time
               for x, y in zip(a, b))


def test_tp_seed_changes_layout():
    a = _targets(seed=SEED)
    b = _targets(seed=9999)
    assert any(x.start_pos_raw != y.start_pos_raw for x, y in zip(a, b))


def test_tp_fixed_seed_regression():
    """A frozen layout guards against accidental drift in the port."""
    objs = _targets(seed=SEED)
    got = [o.start_pos_raw for o in objs]
    assert [o.start_time for o in objs] == EXPECT_TIMES
    assert got == EXPECT_POS


# =========================================================================
# 3. THE fidelity check — object 0 re-derived straight from the C#
# =========================================================================

def _f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def test_tp_first_target_matches_hand_trace():
    """Recompute object 0's placement from OsuModTargetPractice.cs using a raw
    DotNetRandom (bit-exact vs dotnet/runtime). generateBeats/fixComboInfo draw
    no RNG, so the first ``nextSingle`` is the first NextDouble; object 0 has no
    preceding circle, so its do/while runs exactly once. A match means the RNG
    wiring + placement formula reproduce lazer's stream and geometry exactly."""
    objs = _targets(seed=SEED)
    max_combo_index = objs[-1].combo_set

    BASE_X, BASE_Y = 512.0, 384.0
    CENTRE = (256.0, 192.0)
    BORDER_X = _f32(BASE_X * 0.375)
    BORDER_Y = _f32(BASE_Y * 0.375)

    def rotate_towards(initial, dest, ratio):
        ia = math.atan2(initial[1], initial[0])
        da = math.atan2(dest[1], dest[0])
        diff = da - ia
        while diff < -math.pi:
            diff += 2 * math.pi
        while diff > math.pi:
            diff -= 2 * math.pi
        fa = ia + ratio * diff
        length = math.hypot(initial[0], initial[1])
        return (length * math.cos(fa), length * math.sin(fa))

    def rotate_away_from_edge(prev, rel, ratio):
        rrd = 0.0
        if prev[0] < CENTRE[0]:
            rrd = max((BORDER_X - prev[0]) / BORDER_X, rrd)
        else:
            rrd = max((prev[0] - (BASE_X - BORDER_X)) / BORDER_X, rrd)
        if prev[1] < CENTRE[1]:
            rrd = max((BORDER_Y - prev[1]) / BORDER_Y, rrd)
        else:
            rrd = max((prev[1] - (BASE_Y - BORDER_Y)) / BORDER_Y, rrd)
        dest = (CENTRE[0] - prev[0], CENTRE[1] - prev[1])
        return rotate_towards(rel, dest, min(1.0, rrd * ratio))

    diff = Difficulty(ar=9, od=8, cs=4, hp=5)
    diff.set_ar(diff.base_ar * 0.5)                 # TP ApplyToDifficulty
    radius = diff.circle_radius_l

    rng = DotNetRandom(SEED)                        # new Random(Seed.Value.Value)
    two_pi = _f32(_f32(math.pi) * 2.0)

    def next_single(mx=1.0):
        return _f32(rng.next_double() * mx)

    direction = _f32(two_pi * next_single())
    # object 0: distance = mapRange(0,0,maxIdx,radius,333); NewCombo => *1.5
    distance = _f32(radius) if max_combo_index == 0 else _f32(
        (0.0 - 0.0) * (333.0 - _f32(radius)) / (float(max_combo_index) - 0.0)
        + _f32(radius))
    distance = _f32(distance * 1.5)                 # object 0 always NewCombo
    distance = min(380.0, distance)

    last_pos = CENTRE
    rel = (_f32(distance * _f32(math.cos(direction))),
           _f32(distance * _f32(math.sin(direction))))
    rel = rotate_away_from_edge(last_pos, rel, 0.75)
    direction = _f32(math.atan2(rel[1], rel[0]))
    x = _f32(last_pos[0] + rel[0])
    y = _f32(last_pos[1] + rel[1])
    if y < radius:
        y = radius
    elif y > BASE_Y - radius:
        y = BASE_Y - radius
    if x < radius:
        x = radius
    elif x > BASE_X - radius:
        x = BASE_X - radius
    hand_first = (_f32(x), _f32(y))

    assert hand_first == objs[0].start_pos_raw


# =========================================================================
# 4. non-TP byte-identical (the gate) + cursor-on-target reconcile
# =========================================================================

def test_non_tp_load_byte_identical():
    """target_practice_seed=None must leave the map untouched."""
    p = _map()
    plain = load_full(str(p), mods=0)
    gated = load_full(str(p), mods=0, target_practice_seed=None)
    assert len(plain.hit_objects) == len(gated.hit_objects) == 5
    assert all(type(a) is type(b) and a.start_pos_raw == b.start_pos_raw
               and a.start_time == b.start_time
               for a, b in zip(plain.hit_objects, gated.hit_objects))


def _autoplay(bm):
    from osu_std_renderer.replay.replay import StdFrame
    fr = [StdFrame(0, 256.0, 192.0, 0)]
    objs = sorted(bm.hit_objects, key=lambda o: o.get_start_time())
    for i, o in enumerate(objs):
        st = int(o.get_start_time())
        x, y = o.get_stacked_start_position(bm.diff)
        key = 5 if i % 2 == 0 else 10
        fr.append(StdFrame(st, x, y, key))
        fr.append(StdFrame(st + 30, x, y, 0))
    fr.sort(key=lambda f: f.time_ms)
    return fr


def test_tp_reconcile_exact_cursor_on_target():
    """A cursor placed ON each generated target (autoplay) is judged all-300 by
    the normal std ruleset — proving the targets flow through render/judgment
    and the cursor aligns with them (not the discarded original map)."""
    from osu_std_renderer.ruleset import StdRuleset
    from osu_std_renderer.replay.replay import ReplayMeta

    bm = load_full(str(_map()), mods=0, target_practice_seed=SEED)
    frames = _autoplay(bm)
    sim = StdRuleset(bm, frames, None, reconcile=False).run()
    c300, c100, c50, cmiss = sim.sim_counts
    assert cmiss == 0 and c100 == 0 and c50 == 0
    assert c300 == len(bm.hit_objects)

    meta = ReplayMeta(
        mode=0, beatmap_md5="", player_name="t", mods=0, score=0,
        max_combo=sim.sim_max_combo, count_300=c300, count_100=c100,
        count_50=c50, count_geki=0, count_katu=0, count_miss=cmiss,
        accuracy=100.0, grade="SS", game_version=LAZER_GV,
        lazer_mods=("TP",), target_practice_seed=SEED)
    rec = StdRuleset(bm, frames, meta, reconcile=True).run()
    assert rec.final_counts == (c300, c100, c50, cmiss)
    assert rec.relabeled == 0


def test_tp_targets_differ_from_original_geometry():
    """Sanity: the generated targets are NOT the original map's objects — if we
    drew the original map the cursor (on targets) would miss everything."""
    orig = load_full(str(_map()), mods=0).hit_objects
    tp = load_full(str(_map()), mods=0, target_practice_seed=SEED).hit_objects
    orig_pos = {(round(o.start_pos_raw[0], 3), round(o.start_pos_raw[1], 3))
                for o in orig}
    tp_pos = [(round(o.start_pos_raw[0], 3), round(o.start_pos_raw[1], 3))
              for o in tp]
    # essentially none of the target positions coincide with an original object
    assert sum(1 for q in tp_pos if q in orig_pos) == 0
