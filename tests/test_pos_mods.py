"""Position mods — Mirror (MR) and Random (RD).

MR (osu.Game.Rulesets.Osu/Mods/OsuModMirror.cs +
OsuHitObjectGenerationUtils.Reflect*AlongPlayfield): reflect every object about
the playfield centre — Horizontal x->512-x, Vertical y->384-y, Both. Reflection
is an isometry, so the stack indices are the game's un-mirrored ones, and MR
runs BEFORE stacking (IApplicableToHitObject, before PostProcess).

RD (osu.Game.Rulesets.Osu/Mods/OsuModRandom.cs + the Reposition utils): a
.NET System.Random seeded from the ScoreInfo blob repositions the objects AFTER
stacking (IApplicableToBeatmap, after PostProcess). The .NET PRNG is verified
bit-for-bit against dotnet/runtime's own Random.ExpectedValues test vectors.

Pure CPU (no GL). Synthetic maps + .osr blobs, exactly as test_difficulty_adjust.
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
from osu_std_renderer.beatmap.dotnet_random import DotNetRandom
from osu_std_renderer.beatmap.objects.base import stack_threshold
from osu_std_renderer.beatmap.objects.slider import Slider
from osu_std_renderer.beatmap.objects.spinner import Spinner
from osu_std_renderer.beatmap.mods_position import (apply_random,
                                                    PLAYFIELD_DIAGONAL)
from osu_std_renderer.replay import parse_replay
from osu_std_renderer.replay.lazer_mods import (mirror_from_mods,
                                                random_from_mods, read_mirror,
                                                read_random)

LAZER_GV = 30_000_016

# A map with: a stack (two circles at the same spot), a slider, several combos
# and a spinner — enough to exercise MR stacking-invariance and RD's slider
# flip / spinner-skip / combo-section branches.
MAP = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:PosTest
Artist:Unit
Creator:R3D
Version:MRRD

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
100,100,1120,1,0,0:0:0:0:
250,120,1500,5,0,0:0:0:0:
300,200,1750,2,0,B|360:250|400:200,1,140,0:0|0:0,0:0:0:0:
150,300,2100,1,0,0:0:0:0:
400,300,2350,5,0,0:0:0:0:
430,160,2600,1,0,0:0:0:0:
256,192,2850,12,0,4000,0:0:0:0:
190,210,4200,1,0,0:0:0:0:
210,230,4350,1,0,0:0:0:0:
"""


# A single-circle map isolates OBJECT 0's reposition (no later object can shift
# it back via applyDecreasingShift), so its final position == the pure i==0
# reposition — used by the independent hand-trace below.
MAP_ONE = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:One
Artist:Unit
Creator:R3D
Version:One

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
"""


def _map():
    d = Path(tempfile.mkdtemp(prefix="stdpos_"))
    p = d / "test.osu"
    p.write_text(MAP, encoding="utf-8")
    return p


def _map_one():
    d = Path(tempfile.mkdtemp(prefix="stdpos1_"))
    p = d / "one.osu"
    p.write_text(MAP_ONE, encoding="utf-8")
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


def _reflect(p, fx, fy):
    return (512.0 - p[0] if fx else p[0], 384.0 - p[1] if fy else p[1])


# =========================================================================
# 1. The .NET System.Random port — bit-for-bit vs dotnet/runtime's own tests
# =========================================================================
# dotnet/runtime  src/libraries/System.Runtime/tests/System.Runtime.Extensions.Tests
#   /System/Random.cs  ->  Random.ExpectedValues: the first ten `new Random(seed)
#   .Next()` values for seeds 0.. — the authoritative reference stream.
_DOTNET_NEXT = {
    0: [1559595546, 1755192844, 1649316166, 1198642031, 442452829,
        1200195957, 1945678308, 949569752, 2099272109, 587775847],
    1: [534011718, 237820880, 1002897798, 1657007234, 1412011072,
        929393559, 760389092, 2026928803, 217468053, 1379662799],
    2: [1655911537, 867932563, 356479430, 2115372437, 234085668,
        658591161, 1722583523, 956804207, 483147644, 24066104],
    3: [630327709, 1498044246, 1857544709, 426253993, 1203643911,
        387788763, 537294307, 2034163258, 748827235, 815953056],
    19: [1400855637, 842412939, 104785409, 1317646300, 1684190270,
         349917689, 900019674, 2092038898, 704733397, 601242406],
}


def test_dotnet_random_matches_dotnet_runtime_vectors():
    for seed, expected in _DOTNET_NEXT.items():
        r = DotNetRandom(seed)
        assert [r.next() for _ in expected] == expected, f"seed {seed}"


def test_dotnet_random_next_double_range_and_relation():
    r = DotNetRandom(1337)
    for _ in range(200):
        d = r.next_double()
        assert 0.0 <= d < 1.0
    # NextDouble() == Next()/int.MaxValue on the same stream point
    a = DotNetRandom(5)
    b = DotNetRandom(5)
    assert abs(a.next_double() - b.next() / 2147483647.0) < 1e-18


def test_dotnet_random_negative_seed_uses_abs():
    # subtraction = Math.Abs(seed); -5 and +5 are NOT equal but int.MinValue is
    # special. Just assert determinism + that a negative seed is accepted.
    assert DotNetRandom(-5).next() == DotNetRandom(-5).next()
    assert DotNetRandom(-2147483648).next() >= 0


# =========================================================================
# 2. Mirror (MR) — reader
# =========================================================================
def test_mirror_reader_int_string_absent_and_none():
    # integer form (Newtonsoft default: MirrorType 0/1/2)
    assert mirror_from_mods([{"acronym": "MR", "settings": {"reflection": 0}}]) == "horizontal"
    assert mirror_from_mods([{"acronym": "MR", "settings": {"reflection": 1}}]) == "vertical"
    assert mirror_from_mods([{"acronym": "MR", "settings": {"reflection": 2}}]) == "both"
    # string form tolerated
    assert mirror_from_mods([{"acronym": "MR", "settings": {"reflection": "Both"}}]) == "both"
    # absent setting -> enum default Horizontal (OsuModMirror new Bindable<>())
    assert mirror_from_mods([{"acronym": "MR", "settings": {}}]) == "horizontal"
    assert mirror_from_mods([{"acronym": "MR"}]) == "horizontal"
    # no MR mod
    assert mirror_from_mods([{"acronym": "HD"}]) is None


def test_parse_replay_surfaces_mirror():
    d = Path(tempfile.mkdtemp(prefix="stdmr_"))
    p = _write_osr(d, "mr.osr", [{"acronym": "MR", "settings": {"reflection": 1}}])
    assert read_mirror(p) == "vertical"
    _, meta = parse_replay(p)
    assert meta.mirror_reflection == "vertical" and meta.has_mirror is True
    # nomod stays empty
    n = _write_osr(d, "n.osr", [{"acronym": "HD"}])
    _, m2 = parse_replay(n)
    assert m2.mirror_reflection == "" and m2.has_mirror is False


# =========================================================================
# 3. Mirror (MR) — position math, slider paths, stacking invariance
# =========================================================================
def _load(**kw):
    return load_full(_map(), mods=0, **kw)


def test_mirror_reflects_every_raw_position_exactly():
    base = _load()
    for axis, (fx, fy) in {"horizontal": (True, False),
                           "vertical": (False, True),
                           "both": (True, True)}.items():
        mr = _load(mirror_reflection=axis)
        assert len(mr.hit_objects) == len(base.hit_objects)
        for ob, om in zip(base.hit_objects, mr.hit_objects):
            assert om.start_pos_raw == _reflect(ob.start_pos_raw, fx, fy)
            assert om.end_pos_raw == _reflect(ob.end_pos_raw, fx, fy)


def test_mirror_reflects_slider_path_control_geometry():
    base = _load()
    for axis, (fx, fy) in {"horizontal": (True, False),
                           "vertical": (False, True),
                           "both": (True, True)}.items():
        mr = _load(mirror_reflection=axis)
        for ob, om in zip(base.hit_objects, mr.hit_objects):
            if isinstance(ob, Slider):
                assert len(om.multi_curve.path) == len(ob.multi_curve.path)
                for a, b in zip(ob.multi_curve.path, om.multi_curve.path):
                    assert b == _reflect(a, fx, fy)
                # length is invariant under reflection
                assert abs(om.multi_curve.distance - ob.multi_curve.distance) < 1e-9


def test_mirror_stack_indices_identical_to_baseline():
    base = _load()
    thr = stack_threshold(base.diff, base.stack_leniency)
    # the two coincident circles at (100,100) must actually stack (>0 somewhere)
    base_stacks = [o.stack_index_map.get(thr, 0) for o in base.hit_objects]
    assert any(s != 0 for s in base_stacks), "map should produce a stack"
    for axis in ("horizontal", "vertical", "both"):
        mr = _load(mirror_reflection=axis)
        mr_stacks = [o.stack_index_map.get(thr, 0) for o in mr.hit_objects]
        assert mr_stacks == base_stacks   # isometry -> identical indices


def test_mirror_gate_empty_is_byte_identical():
    base = _load()
    same = _load(mirror_reflection="")
    for a, b in zip(base.hit_objects, same.hit_objects):
        assert a.start_pos_raw == b.start_pos_raw
        assert a.end_pos_raw == b.end_pos_raw


# =========================================================================
# 4. Random (RD) — reader
# =========================================================================
def test_random_reader_seed_and_angle():
    rd = random_from_mods([{"acronym": "RD", "settings": {"seed": 4242,
                                                          "angle_sharpness": 4.5}}])
    assert rd.seed == 4242 and rd.angle_sharpness == 4.5
    # angle absent -> default 7
    rd2 = random_from_mods([{"acronym": "RD", "settings": {"seed": 1}}])
    assert rd2.seed == 1 and rd2.angle_sharpness == 7.0
    assert random_from_mods([{"acronym": "HD"}]) is None


def test_parse_replay_surfaces_random():
    d = Path(tempfile.mkdtemp(prefix="stdrd_"))
    p = _write_osr(d, "rd.osr", [{"acronym": "RD", "settings": {"seed": 777,
                                                                "angle_sharpness": 3.0}}])
    rd = read_random(p)
    assert rd.seed == 777
    _, meta = parse_replay(p)
    assert meta.random_seed == 777 and meta.random_angle_sharpness == 3.0
    assert meta.has_random is True
    n = _write_osr(d, "n.osr", [{"acronym": "HD"}])
    _, m2 = parse_replay(n)
    assert m2.random_seed is None and m2.has_random is False


# =========================================================================
# 5. Random (RD) — reposition behaviour
# =========================================================================
def test_random_gate_none_is_byte_identical():
    base = _load()
    same = _load(random_seed=None)
    for a, b in zip(base.hit_objects, same.hit_objects):
        assert a.start_pos_raw == b.start_pos_raw
        assert a.end_pos_raw == b.end_pos_raw


def test_random_is_deterministic_per_seed():
    a = _load(random_seed=1337)
    b = _load(random_seed=1337)
    for x, y in zip(a.hit_objects, b.hit_objects):
        assert x.start_pos_raw == y.start_pos_raw


def test_random_seed_changes_positions():
    a = _load(random_seed=1337)
    b = _load(random_seed=9999)
    moved = sum(1 for x, y in zip(a.hit_objects, b.hit_objects)
                if x.start_pos_raw != y.start_pos_raw)
    assert moved >= 5


def test_random_moves_objects_off_original():
    base = _load()
    rd = _load(random_seed=1337)
    moved = sum(1 for a, b in zip(base.hit_objects, rd.hit_objects)
                if isinstance(a, Spinner) is False
                and a.start_pos_raw != b.start_pos_raw)
    assert moved >= 5


def test_random_keeps_heads_in_playfield_and_spinner_fixed():
    base = _load()
    rd = _load(random_seed=1337)
    for ob, om in zip(base.hit_objects, rd.hit_objects):
        if isinstance(om, Spinner):
            # spinners are skipped by RepositionHitObjects
            assert om.start_pos_raw == ob.start_pos_raw
            continue
        x, y = om.start_pos_raw
        assert -1.0 <= x <= 513.0 and -1.0 <= y <= 385.0


def test_random_rebuilds_slider_score_paths():
    rd = _load(random_seed=1337)
    for o in rd.hit_objects:
        if isinstance(o, Slider):
            assert o.multi_curve is not None and o.multi_curve.path
            assert o.score_path            # rebuilt after the move
            assert o.end_time > o.start_time


def test_random_first_object_hand_trace():
    """Independent trace of OBJECT 0 from the raw .NET Random stream:
    section getRandomOffset (2 gaussian draws, unused for obj0's position) ->
    i==0 distance = nd*192, angle = nd*2pi-pi -> RotateAwayFromEdge@centre is
    the identity -> clamp. Reproduces OsuModRandom for the first object without
    reusing the port's reposition code. A single-circle map is used so no later
    object can shift object 0 back (applyDecreasingShift)."""
    seed = 1337
    rd = load_full(_map_one(), mods=0, random_seed=seed)
    o0 = rd.hit_objects[0]
    radius = rd.diff.circle_radius_l
    rng = DotNetRandom(seed)
    _ = 1.0 - rng.next_double()          # RandomGaussian x1
    _ = 1.0 - rng.next_double()          # RandomGaussian x2  (sectionOffset)
    dist = rng.next_double() * 384.0 / 2.0
    ang = rng.next_double() * 2.0 * math.pi - math.pi
    px = 256.0 + dist * math.cos(ang)
    py = 192.0 + dist * math.sin(ang)
    exp = (min(max(px, radius), 512.0 - radius),
           min(max(py, radius), 384.0 - radius))
    assert abs(o0.start_pos_raw[0] - exp[0]) < 1e-9
    assert abs(o0.start_pos_raw[1] - exp[1]) < 1e-9


def test_random_stacking_runs_before_reposition_indices_preserved():
    """RD runs AFTER stacking (lazer order), so the stack indices are the
    ORIGINAL-position ones and survive the move."""
    base = _load()
    rd = _load(random_seed=1337)
    thr = stack_threshold(base.diff, base.stack_leniency)
    base_stacks = [o.stack_index_map.get(thr, 0) for o in base.hit_objects]
    rd_stacks = [o.stack_index_map.get(thr, 0) for o in rd.hit_objects]
    assert any(s != 0 for s in base_stacks)
    assert rd_stacks == base_stacks       # pre-RD StackHeight kept


# =========================================================================
# 6. Regression snapshot — guards the RD algorithm/RNG stream against drift.
# =========================================================================
# The first non-spinner object positions for seed 1337 on the fixed MAP above,
# captured from the verified port (RNG proven vs dotnet vectors; object 0
# proven by the independent hand-trace). A change here means the RD number
# stream or reposition math moved.
_RD_SNAPSHOT_SEED = 1337
_RD_SNAPSHOT = [
    (201.06404906703239, 228.74994606598008),
    (201.06404906703239, 240.21983806345034),
    (207.6800306775517, 100.50696388921278),
]


def test_random_regression_snapshot():
    rd = _load(random_seed=_RD_SNAPSHOT_SEED)
    got = [o.start_pos_raw for o in rd.hit_objects
           if not isinstance(o, Spinner)][:len(_RD_SNAPSHOT)]
    for (gx, gy), (ex, ey) in zip(got, _RD_SNAPSHOT):
        assert abs(gx - ex) < 1e-6 and abs(gy - ey) < 1e-6


def test_playfield_diagonal_is_lengthfast_not_exact_640():
    # OsuModRandom uses BASE_SIZE.LengthFast (Quake fast inverse sqrt), which is
    # NOT exactly 640; guard the approximation we reproduce.
    assert abs(PLAYFIELD_DIAGONAL - 640.0) < 1.0
    assert PLAYFIELD_DIAGONAL != 640.0
