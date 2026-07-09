"""osu!(lazer) Difficulty Adjust (DA) mod support: reading the per-mod
``settings`` object from the .osr's appended ScoreInfo blob (the legacy `mods`
bitmask can't encode DA), and overriding the beatmap's AR/CS/OD/HP with the DA
values — including the ExtendedLimits path that lifts the 0..10 clamp so AR/OD
can exceed 10. Pure CPU (no GL). The .osr fixtures are synthesized exactly as
in test_classic_mod: osrparse packs a legacy replay, then a lazer ScoreInfo
blob (4-byte LE length + LZMA1 JSON) is appended.

Semantics cited from ppy/osu:
  * osu.Game.Rulesets.Osu/Mods/OsuModDifficultyAdjust.cs — ApproachRate /
    CircleSize (snake_case keys approach_rate / circle_size), MaxValue 10,
    ExtendedMaxValue 11 (AR ExtendedMinValue -10).
  * osu.Game/Rulesets/Mods/ModDifficultyAdjust.cs — OverallDifficulty /
    DrainRate (overall_difficulty / drain_rate), ExtendedLimits BindableBool
    (extended_limits), ApplySettings: ``if (X.Value != null) difficulty.X =
    X.Value.Value`` (absent = keep the beatmap value).
"""
from __future__ import annotations

import json
import lzma
import struct
import tempfile
from datetime import datetime
from pathlib import Path

from osrparse import GameMode, Key, Mod, Replay
from osrparse.replay import ReplayEventOsu

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.beatmap.difficulty import Difficulty, Mods, difficulty_rate
from osu_std_renderer.replay import parse_replay
from osu_std_renderer.replay.lazer_mods import (
    DifficultyAdjust, difficulty_adjust_from_mods, read_difficulty_adjust,
    read_lazer_mods)

LAZER_GV = 30_000_016

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:DATest
Artist:Unit
Creator:R3D
Version:DA

[Difficulty]
HPDrainRate:6
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,1,60,1,0

[HitObjects]
256,192,1000,1,0,0:0:0:0:
"""


def _map():
    d = Path(tempfile.mkdtemp(prefix="stdda_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER, encoding="utf-8")
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


# --- 1. reading DA settings from the ScoreInfo blob -------------------------------

def test_da_settings_parse_present_and_absent_fields():
    d = Path(tempfile.mkdtemp(prefix="stddaosr_"))
    # all four stats + extended
    full = _write_osr(d, "full.osr", [
        {"acronym": "HD"},
        {"acronym": "DA", "settings": {"approach_rate": 10.6,
                                       "circle_size": 7.0,
                                       "overall_difficulty": 10.5,
                                       "drain_rate": 3.0,
                                       "extended_limits": True}}])
    # only AR present — the other three stay None (keep the beatmap value)
    aronly = _write_osr(d, "ar.osr", [
        {"acronym": "DA", "settings": {"approach_rate": 8.0}}])

    da_full = read_difficulty_adjust(full)
    assert da_full == DifficultyAdjust(ar=10.6, cs=7.0, od=10.5, hp=3.0,
                                       extended_limits=True)
    assert da_full.is_empty is False

    da_ar = read_difficulty_adjust(aronly)
    assert da_ar.ar == 8.0
    assert da_ar.cs is None and da_ar.od is None and da_ar.hp is None
    assert da_ar.extended_limits is False


def test_da_empty_settings_is_noop():
    d = Path(tempfile.mkdtemp(prefix="stddaempty_"))
    # DA present but with no stat overrides at all (empty settings object)
    empty = _write_osr(d, "e.osr", [{"acronym": "DA", "settings": {}}])
    # DA present, settings key entirely absent
    bare = _write_osr(d, "b.osr", [{"acronym": "DA"}])
    da_e = read_difficulty_adjust(empty)
    da_b = read_difficulty_adjust(bare)
    assert da_e.is_empty is True and da_b.is_empty is True
    assert da_e == DifficultyAdjust()


def test_no_da_mod_returns_none():
    d = Path(tempfile.mkdtemp(prefix="stdnoda_"))
    noda = _write_osr(d, "n.osr", [{"acronym": "HD"}, {"acronym": "DT"}])
    stable = _write_osr(d, "s.osr", None, blob=False)
    assert read_difficulty_adjust(noda) is None
    assert read_difficulty_adjust(stable) is None


def test_read_lazer_mods_preserves_settings():
    d = Path(tempfile.mkdtemp(prefix="stdlm_"))
    p = _write_osr(d, "m.osr", [
        {"acronym": "HD"},
        {"acronym": "DA", "settings": {"approach_rate": 8.0}}])
    mods = read_lazer_mods(p)
    assert mods == [{"acronym": "HD", "settings": {}},
                    {"acronym": "DA", "settings": {"approach_rate": 8.0}}]
    # the pure extractor agrees with the file reader
    assert difficulty_adjust_from_mods(mods).ar == 8.0


def test_da_num_rejects_booleans():
    # a stray JSON boolean in a numeric slot must NOT read back as 1.0/0.0
    da = difficulty_adjust_from_mods(
        [{"acronym": "DA", "settings": {"approach_rate": True,
                                        "circle_size": 5}}])
    assert da.ar is None          # bool rejected
    assert da.cs == 5.0           # int coerced to float


# --- 2. parse_replay surfaces the DA fields on ReplayMeta -------------------------

def test_parse_replay_da_fields():
    d = Path(tempfile.mkdtemp(prefix="stddameta_"))
    p = _write_osr(d, "p.osr", [
        {"acronym": "DA", "settings": {"approach_rate": 10.6,
                                       "overall_difficulty": 10.5,
                                       "extended_limits": True}}])
    _, meta = parse_replay(p)
    assert meta.da_ar == 10.6 and meta.da_od == 10.5
    assert meta.da_cs is None and meta.da_hp is None
    assert meta.da_extended_limits is True
    assert meta.has_difficulty_adjust is True
    assert meta.difficulty_adjust == {"ar": 10.6, "cs": None, "od": 10.5,
                                      "hp": None, "extended": True}


def test_parse_replay_no_da_leaves_fields_none():
    d = Path(tempfile.mkdtemp(prefix="stddanone_"))
    p = _write_osr(d, "n.osr", [{"acronym": "HD"}])
    _, meta = parse_replay(p)
    assert meta.da_ar is None and meta.da_cs is None
    assert meta.da_od is None and meta.da_hp is None
    assert meta.has_difficulty_adjust is False
    assert meta.difficulty_adjust is None   # the load_full gate


# --- 3. overrides applied to the difficulty --------------------------------------

def test_da_overrides_applied_absent_keeps_beatmap():
    osu = _map()  # base AR9 CS4 OD8 HP6
    base = load_full(osu)
    da = load_full(osu, difficulty_adjust={"ar": 8.0, "cs": None,
                                           "od": None, "hp": None,
                                           "extended": False})
    # AR overridden 9 -> 8 (longer preempt, circles appear EARLIER)
    assert base.diff.ar == 9 and da.diff.ar == 8
    assert da.diff.preempt > base.diff.preempt   # 750 > 600
    # absent CS/OD/HP keep the beatmap values
    assert da.diff.cs == base.diff.cs == 4
    assert da.diff.od == base.diff.od == 8
    assert da.diff.hp == base.diff.hp == 6


def test_da_cs_changes_radius_od_changes_windows():
    osu = _map()
    base = load_full(osu)
    da = load_full(osu, difficulty_adjust={"cs": 7.0, "od": 10.0,
                                           "extended": False})
    assert da.diff.circle_radius < base.diff.circle_radius  # bigger CS = smaller radius
    assert da.diff.hit300 < base.diff.hit300                # tighter OD window


def test_none_difficulty_adjust_is_identical_to_plain():
    osu = _map()
    plain = load_full(osu)
    gated = load_full(osu, difficulty_adjust=None)
    for attr in ("ar", "cs", "od", "hp", "preempt", "circle_radius",
                 "hit300", "hit100", "hit50", "time_fade_in"):
        assert getattr(plain.diff, attr) == getattr(gated.diff, attr)


# --- 4. extended limits: bypass the 0..10 clamp ----------------------------------

def test_extended_limits_bypasses_clamp():
    ar10 = Difficulty(ar=10, od=5, cs=5, hp=5)
    ext = Difficulty(ar=5, od=5, cs=5, hp=5)
    ext.apply_difficulty_adjust(ar=11.0, extended=True)
    # AR11 (extended) extrapolates past AR10 -> SHORTER preempt than AR10
    assert ext.base_ar == 11.0
    assert ext.preempt < ar10.preempt        # 300 < 450
    expected = difficulty_rate(11.0, 1800, 1200, 450)
    assert abs(ext.preempt_u - expected) < 1e-6


def test_non_extended_clamps_to_ten():
    d = Difficulty(ar=5, od=5, cs=5, hp=5)
    d.apply_difficulty_adjust(ar=11.0, extended=False)
    assert d.base_ar == 10.0                 # clamped
    assert d.preempt == Difficulty(ar=10).preempt   # 450


def test_extended_od_beyond_ten_tightens_windows():
    at10 = Difficulty(ar=5, od=10, cs=5, hp=5)
    d = Difficulty(ar=5, od=5, cs=5, hp=5)
    d.apply_difficulty_adjust(od=11.0, extended=True)
    assert d.base_od == 11.0
    assert d.hit300_u < at10.hit300_u        # OD11 window < OD10 (20ms)


# --- 5. DA + rate mod: DA sets base, DT scales on top ----------------------------

def test_da_then_dt_applies_rate_on_top_of_da_base():
    # DA base AR8, then DT: the effective AR (ar_real) reflects DT's clock rate
    # applied to the DA-set base preempt — DA changes the base, the rate mod
    # scales it afterwards (matches osu; DA & DT are compatible).
    d = Difficulty(ar=9, od=9, cs=5, hp=5)
    d.set_mods(Mods.DOUBLE_TIME)
    d.apply_difficulty_adjust(ar=8.0)        # DA overrides the base AR
    assert d.base_ar == 8.0
    assert d.speed == 1.5                     # DT preserved through re-calc
    # preempt is map-time from the DA base (AR8 = 750); DT scales at draw time
    assert d.preempt == 750
    # ar_real = effective AR after DT on the DA base preempt
    from osu_std_renderer.beatmap.difficulty import diff_from_rate
    assert abs(d.ar_real - diff_from_rate(750 / 1.5, 1800, 1200, 450)) < 1e-9
    assert d.ar_real > 8.0                    # DT raises effective AR


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("all difficulty-adjust tests passed")
