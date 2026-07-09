"""Settings-surface HUD elements — mod acronyms/pills, the live hit
counter, aim-error points, and the scoreboard stub. Pure CPU."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from osu_std_renderer.render.hud import (HudData, ModPill, build_aim_points,
                                         build_mod_pills, mod_pill_color,
                                         mods_to_acronyms)
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


# --- build_mod_pills: the full display set (lazer set + custom-rate) --------------

def test_build_mod_pills_from_lazer_mods_all_acronyms():
    # the FULL lazer acronym list, in the .osr blob's order, one pill each —
    # including lazer-only mods the 32-bit bitmask can't encode (CL/DA)
    pills = build_mod_pills(0x48, ["HD", "DT"])
    assert pills == [ModPill("HD", "HD"), ModPill("DT", "DT")]
    # CL (Classic) has NO bitmask bit — only lazer_mods can surface it
    assert build_mod_pills(0, ["CL"]) == [ModPill("CL", "CL")]
    # DA (Difficulty Adjust) likewise, kept in order alongside a bitmask mod
    assert build_mod_pills(0x10, ["HR", "DA"]) == [
        ModPill("HR", "HR"), ModPill("DA", "DA")]
    # RX surfaced from the blob (a lazer-only display path)
    assert build_mod_pills(0, ["RX", "HD"]) == [
        ModPill("RX", "RX"), ModPill("HD", "HD")]


def test_build_mod_pills_custom_rate_suffix():
    # a non-default speed_change puts a compact "1.3×" on the DT/NC/HT pill,
    # while the acr (colour key) stays the bare acronym
    assert build_mod_pills(0x40, ["DT"], 1.3) == [ModPill("DT 1.3×", "DT")]
    assert build_mod_pills(0x240, ["NC"], 1.3) == [ModPill("NC 1.3×", "NC")]
    assert build_mod_pills(0x100, ["HT"], 0.9) == [ModPill("HT 0.9×", "HT")]
    # trailing zeros trimmed (1.25 keeps both, 1.50 → "1.5")
    assert build_mod_pills(0x40, ["DT"], 1.25)[0].text == "DT 1.25×"
    assert build_mod_pills(0x40, ["DT"], 1.5)[0].text == "DT 1.5×"
    # a DEFAULT-rate DT (rate_override None) stays a plain badge — the suffix
    # only appears for a genuinely custom rate
    assert build_mod_pills(0x40, ["DT"], None) == [ModPill("DT", "DT")]
    # a rate on a non-rate mod is ignored (only DT/NC/HT/DC take a suffix)
    assert build_mod_pills(0x8, ["HD"], 1.3) == [ModPill("HD", "HD")]


def test_build_mod_pills_wind_up_down_badge():
    # WU/WD arrive as their own acronyms and render as a plain compact badge
    assert build_mod_pills(0, ["WU"]) == [ModPill("WU", "WU")]
    assert build_mod_pills(0, ["WD"]) == [ModPill("WD", "WD")]
    assert build_mod_pills(0, ["HD", "WU"]) == [
        ModPill("HD", "HD"), ModPill("WU", "WU")]


def test_build_mod_pills_legacy_bitmask_fallback():
    # no lazer_mods (a pure-stable replay) → derive from the 32-bit bitmask,
    # exactly mods_to_acronyms wrapped as ModPill(acr, acr)
    for m in (0x8, 0x18, 0x40 | 0x200, 0x1 | 0x8 | 0x10 | 0x40 | 0x200 | 0x400):
        assert build_mod_pills(m) == [ModPill(a, a)
                                      for a in mods_to_acronyms(m)]
    # NC swallows DT, PF swallows SD on the bitmask path too
    assert build_mod_pills(0x40 | 0x200) == [ModPill("NC", "NC")]
    assert build_mod_pills(0x20 | 0x4000) == [ModPill("PF", "PF")]


def test_build_mod_pills_nomod_unchanged():
    # nomod → no pills, on both the lazer and legacy paths
    assert build_mod_pills(0) == []
    assert build_mod_pills(0, []) == []


def test_build_mod_pills_lazer_dedup_and_swallow():
    # defensive: a blob NC+DT (or PF+SD) collapses to one pill; duplicates drop
    assert build_mod_pills(0, ["NC", "DT"]) == [ModPill("NC", "NC")]
    assert build_mod_pills(0, ["PF", "SD"]) == [ModPill("PF", "PF")]
    assert build_mod_pills(0, ["HD", "HD"]) == [ModPill("HD", "HD")]


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
