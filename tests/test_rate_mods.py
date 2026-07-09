"""osu!(lazer) custom-rate mods: DT/NC/HT/DC carry a per-play ``speed_change``
(ModRateAdjust.SpeedChange) that the legacy 32-bit `mods` bitmask cannot encode
(it only knows the FIXED 1.5x/0.75x). Read it from the .osr's appended
ScoreInfo blob, drive the engine clock rate off it (timeline + ar_real/od_real
+ GetModifiedTime), and pitch the audio for NC/DC while DT/HT stay tempo-only.
Pure CPU (no GL). Fixtures synthesized exactly as in test_difficulty_adjust.

Semantics cited from ppy/osu (master):
  * osu.Game/Rulesets/Mods/ModRateAdjust.cs — abstract SpeedChange +
    ApplyToRate(time, rate) => rate * SpeedChange.Value; ApplyToTrack via the
    RateAdjustModHelper uses AdjustableProperty.Tempo (pitch preserved).
  * ModDoubleTime.cs — SpeedChange BindableDouble(1.5){Min 1.01, Max 2,
    Precision 0.01}; ModHalfTime.cs — BindableDouble(0.75){Min 0.5, Max 0.99}.
  * ModNightcore.cs / ModDaycore.cs — override ApplyToTrack to add BOTH
    Frequency (freqAdjust = SpeedChange.Default, i.e. the fixed classic pitch)
    and Tempo (tempoAdjust = value / SpeedChange.Default) → the audio PITCHES.
  * the settings key is snake_case ``speed_change`` in the ScoreInfo JSON.
WU/WD (continuously varying rate) is out of scope — a separate item.
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
from osu_std_renderer.beatmap.difficulty import Mods
from osu_std_renderer.record.audio import SAMPLE_RATE, rate_audio_filter
from osu_std_renderer.replay import parse_replay
from osu_std_renderer.replay.lazer_mods import (
    RATE_ADJUST_DEFAULTS, RATE_ADJUST_RANGE, RateAdjust,
    rate_adjust_from_mods, read_rate_adjust)
from osu_std_renderer.ruleset import StdRuleset

LAZER_GV = 30_000_016

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
Mode: 0

[Metadata]
Title:RateTest
Artist:Unit
Creator:R3D
Version:Rate

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
256,192,1000,1,0,0:0:0:0:
"""


def _map() -> Path:
    d = Path(tempfile.mkdtemp(prefix="stdrate_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER, encoding="utf-8")
    return p


def _write_osr(dirpath: Path, name: str, mods_json, *,
               bitmask: Mod = Mod.NoMod, blob: bool = True) -> Path:
    ev = [ReplayEventOsu(0, 5.0, 5.0, Key(0)),
          ReplayEventOsu(1000, 256.0, 192.0, Key.K1),
          ReplayEventOsu(-12345, 0.0, 0.0, Key(0))]
    r = Replay(GameMode.STD, LAZER_GV, "abc", "tester", "h",
               1, 0, 0, 0, 0, 0, 12345, 1, False, bitmask, None,
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


def _close(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


# --- 1. reading speed_change from the ScoreInfo blob -----------------------------

def test_speed_change_present_and_absent_defaults():
    # explicit custom rate on each rate mod
    assert rate_adjust_from_mods(
        [{"acronym": "DT", "settings": {"speed_change": 1.3}}]).speed == 1.3
    assert rate_adjust_from_mods(
        [{"acronym": "HT", "settings": {"speed_change": 0.6}}]).speed == 0.6
    # absent speed_change -> the mod's default (== the fixed bitmask rate)
    assert rate_adjust_from_mods([{"acronym": "DT"}]).speed == 1.5
    assert rate_adjust_from_mods([{"acronym": "NC", "settings": {}}]).speed == 1.5
    assert rate_adjust_from_mods([{"acronym": "HT"}]).speed == 0.75
    assert rate_adjust_from_mods([{"acronym": "DC", "settings": {}}]).speed == 0.75
    # the module tables agree with lazer's SettingSource defaults/ranges
    assert RATE_ADJUST_DEFAULTS == {"DT": 1.5, "NC": 1.5, "HT": 0.75, "DC": 0.75}
    assert RATE_ADJUST_RANGE["DT"] == (1.01, 2.0)
    assert RATE_ADJUST_RANGE["HT"] == (0.5, 0.99)


def test_speed_change_clamped_to_mod_range():
    # DT/NC clamp to [1.01, 2.0]
    assert rate_adjust_from_mods(
        [{"acronym": "DT", "settings": {"speed_change": 3.0}}]).speed == 2.0
    assert rate_adjust_from_mods(
        [{"acronym": "NC", "settings": {"speed_change": 1.0}}]).speed == 1.01
    # HT/DC clamp to [0.5, 0.99]
    assert rate_adjust_from_mods(
        [{"acronym": "HT", "settings": {"speed_change": 0.3}}]).speed == 0.5
    assert rate_adjust_from_mods(
        [{"acronym": "DC", "settings": {"speed_change": 1.5}}]).speed == 0.99


def test_pitch_flag_nc_dc_vs_dt_ht():
    # NC/DC change pitch (Frequency); DT/HT are tempo-only (pitch preserved)
    assert rate_adjust_from_mods([{"acronym": "NC"}]).pitch is True
    assert rate_adjust_from_mods([{"acronym": "DC"}]).pitch is True
    assert rate_adjust_from_mods([{"acronym": "DT"}]).pitch is False
    assert rate_adjust_from_mods([{"acronym": "HT"}]).pitch is False


def test_is_default_flag_marks_the_fixed_bitmask_rate():
    # a plain (no speed_change) DT/NC/HT/DC == the fixed bitmask rate -> default
    assert rate_adjust_from_mods([{"acronym": "DT"}]).is_default is True
    assert rate_adjust_from_mods(
        [{"acronym": "DT", "settings": {"speed_change": 1.5}}]).is_default is True
    # a genuine custom rate is NOT default
    assert rate_adjust_from_mods(
        [{"acronym": "DT", "settings": {"speed_change": 1.3}}]).is_default is False
    assert rate_adjust_from_mods(
        [{"acronym": "HT", "settings": {"speed_change": 0.6}}]).is_default is False


def test_no_rate_mod_returns_none():
    assert rate_adjust_from_mods([{"acronym": "HD"}, {"acronym": "FL"}]) is None
    assert rate_adjust_from_mods([]) is None


def test_speed_change_rejects_booleans():
    # a stray JSON bool in the numeric slot must NOT read back as 1.0 -> default
    ra = rate_adjust_from_mods(
        [{"acronym": "DT", "settings": {"speed_change": True}}])
    assert ra.speed == 1.5 and ra.is_default is True


def test_read_rate_adjust_from_osr_and_stable_none():
    d = Path(tempfile.mkdtemp(prefix="stdrateosr_"))
    custom = _write_osr(d, "c.osr",
                        [{"acronym": "HD"},
                         {"acronym": "DT", "settings": {"speed_change": 1.35}}])
    stable = _write_osr(d, "s.osr", None, blob=False)
    ra = read_rate_adjust(custom)
    assert ra == RateAdjust(acronym="DT", speed=1.35, pitch=False,
                            is_default=False)
    assert read_rate_adjust(stable) is None      # no ScoreInfo blob


# --- 2. parse_replay surfaces rate_override / rate_pitch on ReplayMeta -----------

def test_parse_replay_custom_dt_rate_no_pitch():
    d = Path(tempfile.mkdtemp(prefix="stdratemeta_"))
    p = _write_osr(d, "dt.osr",
                   [{"acronym": "DT", "settings": {"speed_change": 1.3}}],
                   bitmask=Mod.DoubleTime)
    _, meta = parse_replay(p)
    assert meta.rate_override == 1.3
    assert meta.rate_pitch is False              # DT = tempo-only
    assert meta.has_rate_override is True


def test_parse_replay_custom_nc_rate_pitches():
    d = Path(tempfile.mkdtemp(prefix="stdratenc_"))
    p = _write_osr(d, "nc.osr",
                   [{"acronym": "NC", "settings": {"speed_change": 1.7}}],
                   bitmask=Mod(Mod.DoubleTime | Mod.Nightcore))
    _, meta = parse_replay(p)
    assert meta.rate_override == 1.7
    assert meta.rate_pitch is True               # NC = pitch


def test_parse_replay_default_rate_leaves_override_none():
    # A plain (default-rate) DT/NC carries NO override -> the legacy bitmask
    # path renders it (byte-identical). This is the standard-rate gate.
    d = Path(tempfile.mkdtemp(prefix="stdratedef_"))
    dt = _write_osr(d, "dt.osr", [{"acronym": "DT"}], bitmask=Mod.DoubleTime)
    nc = _write_osr(d, "nc.osr", [{"acronym": "NC"}],
                    bitmask=Mod(Mod.DoubleTime | Mod.Nightcore))
    for p in (dt, nc):
        _, meta = parse_replay(p)
        assert meta.rate_override is None
        assert meta.rate_pitch is False
        assert meta.has_rate_override is False


def test_parse_replay_stable_bitmask_dt_no_override():
    # A stable replay (bitmask DT, no ScoreInfo blob) is UNCHANGED: fixed 1.5x,
    # no override.
    d = Path(tempfile.mkdtemp(prefix="stdratestable_"))
    p = _write_osr(d, "s.osr", None, bitmask=Mod.DoubleTime, blob=False)
    _, meta = parse_replay(p)
    assert meta.mods & int(Mods.DOUBLE_TIME)
    assert meta.rate_override is None and meta.rate_pitch is False


# --- 3. the custom rate flows through Difficulty (timeline + AR/OD) --------------

def test_effective_rate_used_over_bitmask():
    osu = _map()
    dt = load_full(osu, mods=Mods.DOUBLE_TIME)                 # fixed 1.5
    custom = load_full(osu, mods=Mods.DOUBLE_TIME, speed_override=1.3)
    assert _close(dt.diff.speed, 1.5)
    assert _close(custom.diff.speed, 1.3)                      # override wins
    # ar_real / od_real are derived from the effective speed, so they DIFFER
    # from the fixed-1.5 DT (1.3x is gentler -> lower ar_real / od_real)
    assert custom.diff.ar_real < dt.diff.ar_real
    assert custom.diff.od_real < dt.diff.od_real
    # and the effective ar_real matches recomputing at 1.3 by hand
    from osu_std_renderer.beatmap.difficulty import diff_from_rate
    expect = diff_from_rate(custom.diff.preempt_u / 1.3, 1800, 1200, 450)
    assert _close(custom.diff.ar_real, expect)


def test_get_modified_time_follows_custom_speed():
    osu = _map()
    bm = load_full(osu, mods=Mods.DOUBLE_TIME, speed_override=1.3)
    assert _close(bm.diff.get_modified_time(1300.0), 1300.0 / 1.3)


def test_legacy_bitmask_speed_unchanged_without_override():
    # No speed_override -> the bitmask rate is untouched (1.5 / 0.75 / 1.0).
    osu = _map()
    assert _close(load_full(osu, mods=Mods.DOUBLE_TIME).diff.speed, 1.5)
    assert _close(load_full(osu, mods=Mods.HALF_TIME).diff.speed, 0.75)
    assert _close(load_full(osu).diff.speed, 1.0)


def test_set_custom_speed_none_restores_bitmask_rate():
    osu = _map()
    bm = load_full(osu, mods=Mods.DOUBLE_TIME)
    bm.diff.set_custom_speed(1.3)
    assert _close(bm.diff.speed, 1.3)
    bm.diff.set_custom_speed(None)                # restore the bitmask rate
    assert _close(bm.diff.speed, 1.5)


def test_custom_rate_end_to_end_from_replay_meta():
    # parse_replay -> load_full(speed_override=meta.rate_override): the wired
    # render path a custom-rate replay takes.
    d = Path(tempfile.mkdtemp(prefix="stdratee2e_"))
    p = _write_osr(d, "dt.osr",
                   [{"acronym": "DT", "settings": {"speed_change": 1.42}}],
                   bitmask=Mod.DoubleTime)
    _, meta = parse_replay(p)
    bm = load_full(_map(), mods=meta.mods, speed_override=meta.rate_override)
    assert _close(bm.diff.speed, 1.42)


# --- 4. audio: NC/DC pitch vs DT/HT tempo (and standard rates byte-identical) ----

def test_audio_filter_dt_ht_are_tempo_only_and_unchanged():
    # DT/HT (pitch=False) -> the SAME atempo string the pre-custom-rate engine
    # emitted, so a standard 1.5/0.75 decode is byte-identical.
    assert rate_audio_filter(1.5, pitch=False) == "atempo=1.5"
    assert rate_audio_filter(0.75, pitch=False) == "atempo=0.75"
    assert rate_audio_filter(1.3, pitch=False) == "atempo=1.3"   # custom DT
    assert rate_audio_filter(1.0, pitch=False) == ""             # nomod: no -af


def test_audio_filter_nc_dc_pitch_with_the_rate():
    # NC pitch pinned to the classic 1.5x default (asetrate) + tempo remainder
    f = rate_audio_filter(1.3, pitch=True)
    assert f.startswith(f"asetrate={int(round(SAMPLE_RATE * 1.5))},"
                        f"aresample={SAMPLE_RATE}")
    assert ",atempo=" in f                        # tempo = 1.3/1.5 != 1
    # at exactly the default rate the tempo remainder is 1 -> asetrate only
    f15 = rate_audio_filter(1.5, pitch=True)
    assert f15 == f"asetrate={int(round(SAMPLE_RATE * 1.5))},aresample={SAMPLE_RATE}"
    # DC pins pitch to the 0.75x daycore default
    fdc = rate_audio_filter(0.6, pitch=True)
    assert fdc.startswith(f"asetrate={int(round(SAMPLE_RATE * 0.75))},")


def test_audio_filter_nc_vs_dt_differ_dt_matches_baseline():
    # the core proof: at the same 1.3x rate NC pitches, DT does not
    assert rate_audio_filter(1.3, pitch=True) != rate_audio_filter(1.3, pitch=False)
    assert rate_audio_filter(1.3, pitch=False) == "atempo=1.3"


# --- 5. reconcile stays exact under a custom rate --------------------------------

def test_reconcile_exact_with_custom_rate():
    from osu_std_renderer.replay.replay import ReplayMeta, StdFrame
    bm = load_full(_map(), mods=Mods.DOUBLE_TIME, speed_override=1.3)
    # a single 300 on the lone circle at map-time 1000 (frame times are MAP
    # time — rate mods don't compress them)
    frames = [StdFrame(time_ms=0, x=0.0, y=0.0, keys=0),
              StdFrame(time_ms=1000, x=256.0, y=192.0, keys=1)]
    meta = ReplayMeta(mode=0, beatmap_md5="", player_name="t", mods=0, score=0,
                      max_combo=1, count_300=1, count_100=0, count_50=0,
                      count_geki=0, count_katu=0, count_miss=0, accuracy=100.0,
                      grade="SS", rate_override=1.3)
    sim = StdRuleset(bm, frames, meta, reconcile=True).run()
    assert sim.final_counts == (1, 0, 0, 0)       # reconciled == .osr counts
