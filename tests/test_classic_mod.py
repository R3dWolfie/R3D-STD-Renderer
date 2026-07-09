"""osu!(lazer) Classic mod (CL) support: reading the lazer mod-acronym list
from the .osr's appended ScoreInfo blob, and forcing the STABLE judgment
engine (OsuModClassic — ClassicNoteLock + NoSliderHeadAccuracy) plus the
AlwaysPlayTailSample / FadeHitCircleEarly toggles when CL is present. Pure
CPU (no GL). The .osr fixtures are synthesized: osrparse packs a valid legacy
replay, then a lazer ScoreInfo blob (4-byte LE length + LZMA1 JSON) is
appended — the exact tail lazer's LegacyScoreEncoder writes."""
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
from osu_std_renderer.beatmap.objects import Slider
from osu_std_renderer.record.hitsounds import collect_hitsound_events
from osu_std_renderer.replay import parse_replay
from osu_std_renderer.replay.replay import KEY_K1, ReplayMeta, StdFrame
from osu_std_renderer.replay.lazer_mods import (
    has_classic_mod, parse_lazer_score_info, read_lazer_mod_acronyms)
from osu_std_renderer.render.scene import miss_fade_alpha
from osu_std_renderer.ruleset import JudgmentKind, StdRuleset

LAZER_GV = 30_000_016     # a real lazer game_version (>= 30M)
STABLE_GV = 20_251_128    # an 8-digit stable YYYYMMDD version

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:ClassicTest
Artist:Unit
Creator:R3D
Version:CL

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:9
SliderMultiplier:1.0
SliderTickRate:1

[TimingPoints]
0,500,4,2,1,60,1,0

[HitObjects]
"""

SLIDER_LINE = "256,192,1000,2,0,L|506:192,1,250,0|0,0:0|0:0,0:0:0:0:\n"
LEAD_CIRCLE = "256,192,500,1,0,0:0:0:0:\n"


def _map(objects: str) -> object:
    d = Path(tempfile.mkdtemp(prefix="stdcl_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER + objects, encoding="utf-8")
    return load_full(p)


def _frames(entries) -> list[StdFrame]:
    fr = [StdFrame(time_ms=0, x=0.0, y=0.0, keys=0)]
    fr += [StdFrame(time_ms=t, x=float(x), y=float(y), keys=k)
           for t, x, y, k in entries]
    return fr


def _ball_frames(slider, t0, t1, step=10, keys=KEY_K1):
    out, t = [], t0
    while t <= t1:
        x, y = slider.position_at(t)
        out.append((t, x, y, keys))
        t += step
    return out


def _meta(game_version: int, has_cl: bool,
          mods: tuple[str, ...] = ()) -> ReplayMeta:
    return ReplayMeta(
        mode=0, beatmap_md5="", player_name="t", mods=0, score=0,
        max_combo=0, count_300=1, count_100=0, count_50=0, count_geki=0,
        count_katu=0, count_miss=0, accuracy=100.0, grade="SS",
        game_version=game_version, lazer_mods=mods, has_classic_mod=has_cl)


# --- .osr fixture synthesis -------------------------------------------------------

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


# --- 1. reading the lazer mod list from the ScoreInfo blob -------------------------

def test_read_lazer_mods_from_scoreinfo_blob():
    d = Path(tempfile.mkdtemp(prefix="stdclosr_"))
    cl = _write_osr(d, "cl.osr", [{"acronym": "HD"}, {"acronym": "CL"}])
    noc = _write_osr(d, "no.osr", [{"acronym": "HD"}, {"acronym": "DT"}])
    nomod = _write_osr(d, "nm.osr", [])
    stable = _write_osr(d, "st.osr", None, blob=False)   # no trailing blob

    assert read_lazer_mod_acronyms(cl) == ["HD", "CL"]
    assert read_lazer_mod_acronyms(noc) == ["HD", "DT"]
    assert read_lazer_mod_acronyms(nomod) == []
    assert read_lazer_mod_acronyms(stable) is None       # genuine stable .osr

    assert has_classic_mod(cl) is True
    assert has_classic_mod(noc) is False
    assert has_classic_mod(nomod) is False
    assert has_classic_mod(stable) is False

    # the parse round-trips the whole ScoreInfo dict (the bot's technique)
    info = parse_lazer_score_info(cl)
    assert info is not None and info["rank"] == "S"
    assert [m["acronym"] for m in info["mods"]] == ["HD", "CL"]


def test_parse_replay_sets_has_classic_mod():
    d = Path(tempfile.mkdtemp(prefix="stdclmeta_"))
    cl = _write_osr(d, "cl.osr", [{"acronym": "CL"}])
    noc = _write_osr(d, "no.osr", [{"acronym": "DT"}])
    stable = _write_osr(d, "st.osr", None, blob=False)

    _, m_cl = parse_replay(cl)
    assert m_cl.has_classic_mod is True
    assert m_cl.lazer_mods == ("CL",)
    assert m_cl.game_version == LAZER_GV

    _, m_no = parse_replay(noc)
    assert m_no.has_classic_mod is False and m_no.lazer_mods == ("DT",)

    _, m_st = parse_replay(stable)
    assert m_st.has_classic_mod is False and m_st.lazer_mods == ()


# --- 2. engine selection: CL forces stable regardless of game_version --------------

def _engine(meta, lazer=None):
    bm = _map("100,100,1000,1,0,0:0:0:0:\n")
    fr = _frames([(1000, 100, 100, KEY_K1), (1030, 100, 100, 0)])
    return StdRuleset(bm, fr, meta, lazer=lazer, reconcile=False).run()


def test_cl_forces_stable_engine_at_lazer_version():
    r = _engine(_meta(LAZER_GV, has_cl=True, mods=("CL",)))
    assert r.lazer is False        # ClassicNoteLock + NoSliderHeadAccuracy path
    assert r.classic is True
    assert "stable (Classic mod)" in r.report_lines()[0]


def test_no_cl_lazer_replay_stays_lazer():
    r = _engine(_meta(LAZER_GV, has_cl=False))
    assert r.lazer is True         # StartTimeOrderedHitPolicy path unchanged
    assert r.classic is False
    assert "ruleset[lazer]" in r.report_lines()[0]


def test_stable_version_replay_is_stable_without_classic_flag():
    # a genuine pre-lazer stable replay: stable engine, but NOT via CL
    r = _engine(_meta(STABLE_GV, has_cl=False))
    assert r.lazer is False and r.classic is False
    assert "stable (Classic mod)" not in r.report_lines()[0]


def test_explicit_lazer_override_is_respected():
    # `lazer=` is a manual override that wins over the CL auto-detect, but
    # the classic-mod visual/audio flag still reflects the mod's presence.
    r = _engine(_meta(LAZER_GV, has_cl=True, mods=("CL",)), lazer=True)
    assert r.lazer is True
    assert r.classic is True


# --- 3. OsuModClassic toggles: AlwaysPlayTailSample + FadeHitCircleEarly -----------

def test_always_play_tail_sample():
    """OsuModClassic.AlwaysPlayTailSample: a MISSED slider tail still plays
    its tail sample under CL (SamplePlaysOnlyOnHit=false); off the mod it is
    silent."""
    bm = _map(LEAD_CIRCLE + SLIDER_LINE)
    _, slider = bm.hit_objects
    assert isinstance(slider, Slider)
    # head + ticks tracked, cursor gone before the tail judge time → tail miss
    fr = _frames([(500, 256, 192, KEY_K1), (530, 256, 192, 0),
                  (1000, 256, 192, KEY_K1)]
                 + _ball_frames(slider, 1010, 2050)
                 + [(2060, 0, 0, KEY_K1)])

    sim_plain = StdRuleset(bm, fr, _meta(LAZER_GV, has_cl=False),
                           reconcile=False).run()
    sim_cl = StdRuleset(bm, fr, _meta(LAZER_GV, has_cl=True, mods=("CL",)),
                        reconcile=False).run()
    v = sim_cl.verdict_for(slider)
    tail = next(p for p in v.parts if p.kind == "tail")
    assert tail.hit is False and tail.time == 2250     # tail genuinely missed

    os_plain, _ = collect_hitsound_events(bm, sim_plain)
    os_cl, _ = collect_hitsound_events(bm, sim_cl)
    at_tail_plain = [o for o in os_plain if abs(o.time_ms - 2250) < 1]
    at_tail_cl = [o for o in os_cl if abs(o.time_ms - 2250) < 1]
    assert at_tail_plain == []          # no sound on a missed tail off-mod
    assert len(at_tail_cl) >= 1         # AlwaysPlayTailSample fires it


def test_fade_hit_circle_early():
    """OsuModClassic.FadeHitCircleEarly: a missed circle fades from start+ok
    (100 window) to start+meh (deadline), reaching zero AT the miss moment,
    instead of holding full to the window close and quick-fading after."""
    start, preempt, fade_in = 1000.0, 600.0, 400.0
    ok, meh = 83.5, 129.5               # OD7 windows
    deadline = start + meh

    # off-mod (lazer default): full until the deadline, then a quick fade
    assert miss_fade_alpha(start + ok, start, preempt, fade_in, deadline) == 1.0
    assert miss_fade_alpha(deadline, start, preempt, fade_in, deadline) == 1.0

    # classic: already fading between start+ok and the deadline, 0 at deadline
    a_start = miss_fade_alpha(start + ok, start, preempt, fade_in, deadline,
                              classic=True, ok=ok)
    a_mid = miss_fade_alpha(start + ok + (meh - ok) / 2, start, preempt,
                            fade_in, deadline, classic=True, ok=ok)
    a_end = miss_fade_alpha(deadline, start, preempt, fade_in, deadline,
                            classic=True, ok=ok)
    assert a_start == 1.0
    assert 0.0 < a_mid < 1.0
    assert a_end == 0.0
    # earlier fade than the default: classic alpha < default alpha mid-window
    default_mid = miss_fade_alpha(start + ok + (meh - ok) / 2, start, preempt,
                                  fade_in, deadline)
    assert a_mid < default_mid


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("all classic-mod tests passed")
