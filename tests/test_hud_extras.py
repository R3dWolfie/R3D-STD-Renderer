"""Settings-surface HUD elements — mod acronyms/pills, the live hit
counter, aim-error points, and the scoreboard stub. Pure CPU."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from osu_std_renderer.render.hud import (HudData, build_aim_points,
                                         mod_pill_color, mods_to_acronyms)
from osu_std_renderer.render.scoreboard import (ScoreboardEntry, draw,
                                                load_scoreboard_json)
from tests.test_hud import _three_circle_run


# --- mod pills -------------------------------------------------------------------

def test_mods_to_acronyms_standard_derivation():
    assert mods_to_acronyms(0) == []
    assert mods_to_acronyms(0x8) == ["HD"]
    assert mods_to_acronyms(0x10 | 0x8) == ["HR", "HD"]
    # NC sets the DT bit too — DT is swallowed
    assert mods_to_acronyms(0x40 | 0x200) == ["NC"]
    assert mods_to_acronyms(0x40) == ["DT"]
    # PF sets SD — SD is swallowed
    assert mods_to_acronyms(0x20 | 0x4000) == ["PF"]
    # a kitchen-sink stable value: HDHRNCFL + NF
    mods = 0x1 | 0x8 | 0x10 | 0x40 | 0x200 | 0x400
    assert mods_to_acronyms(mods) == ["NF", "HR", "NC", "HD", "FL"]


def test_mod_pill_colors_by_category():
    assert mod_pill_color("EZ") == mod_pill_color("HT")      # reduction
    assert mod_pill_color("HR") == mod_pill_color("DT")      # increase
    assert mod_pill_color("RX") == mod_pill_color("V2")      # automation
    assert mod_pill_color("EZ") != mod_pill_color("HR")
    assert mod_pill_color("HR") != mod_pill_color("RX")


# --- hit counter -------------------------------------------------------------------

def test_counts_at_prefix():
    sim = _three_circle_run(click_third=True)     # three 300s
    d = HudData(sim)
    assert d.counts_at(-1.0) == (0, 0, 0, 0)
    t0 = d.ev_times[0]
    assert d.counts_at(t0) == (1, 0, 0, 0)
    assert d.counts_at(d.ev_times[-1] + 1.0) == (3, 0, 0, 0)


# --- aim error ---------------------------------------------------------------------

class _Frame:
    def __init__(self, t, x, y, keys=0):
        self.time_ms, self.x, self.y, self.keys = t, x, y, keys


def test_build_aim_points_radius_units():
    radius = 32.0
    frames = [_Frame(0, 100.0, 100.0), _Frame(2000, 100.0, 100.0)]
    v_hit = SimpleNamespace(obj_kind="circle", hit_time=1000.0,
                            pos=(84.0, 100.0))       # cursor 16 px right
    v_miss = SimpleNamespace(obj_kind="circle", hit_time=None,
                             pos=(0.0, 0.0))
    v_spin = SimpleNamespace(obj_kind="spinner", hit_time=1500.0,
                             pos=(256.0, 192.0))
    sim = SimpleNamespace(verdicts={1: v_hit, 2: v_miss, 3: v_spin})
    pts = build_aim_points(sim, frames, radius)
    assert len(pts) == 1                    # miss + spinner skipped
    t, dx, dy = pts[0]
    assert t == 1000.0
    assert abs(dx - 0.5) < 1e-9 and abs(dy) < 1e-9   # 16/32 radius units
    # no frames / no radius → empty, never raises
    assert build_aim_points(sim, [], radius) == []
    assert build_aim_points(None, frames, radius) == []


# --- scoreboard stub ---------------------------------------------------------------

def test_scoreboard_loader_parses_the_contract():
    payload = [
        {"username": "B", "score": 900, "combo": 100, "rank": 2,
         "avatar_png": None, "mods": 64},
        {"username": "A", "score": 1000, "combo": 120, "rank": 1},
    ]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sb.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        entries = load_scoreboard_json(p)
    assert entries is not None and len(entries) == 2
    assert entries[0] == ScoreboardEntry(username="A", score=1000,
                                         combo=120, rank=1)
    assert entries[1].mods == 64


def test_scoreboard_fail_soft_and_honest_noop():
    assert load_scoreboard_json(None) is None
    assert load_scoreboard_json(Path("/nonexistent.json")) is None
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_scoreboard_json(p) is None
    # rendering is LOUDLY unimplemented (no silent half-wire)
    try:
        draw()
        raise AssertionError("scoreboard draw() should raise")
    except NotImplementedError:
        pass
