"""HUD math tests — acc formula, grade thresholds, UR, standardised-score
monotonicity (perfect run → exactly 1M), key-edge counting, the combo
timeline (mid-slider steps + exact break moments), the score roll/pin and
the mono text layout (no jitter). Pure CPU (no GL)."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.render.hud import (
    ERR_ARROW_N, HudData, KeySeries, combo_pop_scale, grade_for, layout_run,
    rolled, std_accuracy, unstable_rate,
)
from osu_std_renderer.replay.replay import (
    KEY_K1, KEY_K2, KEY_M1, KEY_M2, StdFrame,
)
from osu_std_renderer.ruleset import StdRuleset

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:HudTest
Artist:Unit
Creator:R3D
Version:Hud

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


def _map(objects: str) -> object:
    d = Path(tempfile.mkdtemp(prefix="stdhud_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER + objects, encoding="utf-8")
    return load_full(p)


def _frames(entries) -> list[StdFrame]:
    fr = [StdFrame(time_ms=0, x=0.0, y=0.0, keys=0)]
    fr += [StdFrame(time_ms=t, x=float(x), y=float(y), keys=k)
           for t, x, y, k in entries]
    return fr


# --- accuracy ---------------------------------------------------------------------

def test_std_accuracy():
    assert std_accuracy(0, 0, 0, 0) == 1.0
    assert std_accuracy(10, 0, 0, 0) == 1.0
    assert abs(std_accuracy(589, 8, 0, 2)
               - (300 * 589 + 100 * 8) / (300.0 * 599)) < 1e-12
    assert std_accuracy(0, 0, 0, 5) == 0.0
    assert abs(std_accuracy(0, 3, 0, 0) - 1.0 / 3.0) < 1e-12


# --- grade thresholds --------------------------------------------------------------

def test_grade_thresholds():
    assert grade_for(0, 0, 0, 0) == "SS"          # clean sheet
    assert grade_for(100, 0, 0, 0) == "SS"
    assert grade_for(95, 5, 0, 0) == "S"          # >90% 300s, no 50s/miss
    assert grade_for(95, 4, 1, 0) == "S"          # exactly 1% 50s still S
    assert grade_for(94, 3, 3, 0) == "A"          # >1% 50s kills the S
    assert grade_for(95, 4, 0, 1) == "A"          # a miss kills the S (>90% → A)
    assert grade_for(85, 15, 0, 0) == "A"         # >80% 300s, no miss
    assert grade_for(85, 14, 0, 1) == "B"         # >80% with a miss
    assert grade_for(75, 25, 0, 0) == "B"         # >70% no miss
    assert grade_for(65, 30, 0, 5) == "C"         # >60%
    assert grade_for(50, 30, 10, 10) == "D"


# --- UR -----------------------------------------------------------------------------

def test_unstable_rate():
    assert unstable_rate([]) == 0.0
    assert unstable_rate([12.0]) == 0.0
    assert unstable_rate([5.0, 5.0, 5.0]) == 0.0        # no spread
    # ±10 alternating: population stddev = 10 → UR 100
    assert abs(unstable_rate([10.0, -10.0, 10.0, -10.0]) - 100.0) < 1e-9
    d = [3.0, -7.0, 12.0, 0.0, -2.0]
    mean = sum(d) / len(d)
    want = 10.0 * math.sqrt(sum((x - mean) ** 2 for x in d) / len(d))
    assert abs(unstable_rate(d) - want) < 1e-9


# --- score roll + pop ---------------------------------------------------------------

def test_rolled_tween():
    assert rolled(0, 1000, -5) == 0
    assert abs(rolled(0, 1000, 60) - 500.0) < 1e-9
    assert rolled(0, 1000, 120) == 1000
    assert rolled(0, 1000, 5000) == 1000


def test_combo_pop_scale():
    assert abs(combo_pop_scale(0.0) - 1.15) < 1e-9
    assert combo_pop_scale(120.0) == 1.0
    assert combo_pop_scale(-1.0) == 1.0
    mid = combo_pop_scale(60.0)
    assert 1.0 < mid < 1.15


# --- mono layout (score roll must not jitter) -----------------------------------------

def test_layout_run_mono_stable_width():
    aspects = {"0": 0.55, "1": 0.30, "8": 0.58, ".": 0.2, "%": 0.9}
    adv = max(aspects[c] for c in "018")
    _, w_ones = layout_run("11111111", aspects, 64.0, mono_advance=adv)
    _, w_zeros = layout_run("00000000", aspects, 64.0, mono_advance=adv)
    _, w_mixed = layout_run("18011810", aspects, 64.0, mono_advance=adv)
    assert abs(w_ones - w_zeros) < 1e-9
    assert abs(w_ones - w_mixed) < 1e-9
    # without mono the widths differ (sanity that mono is doing something)
    _, n_ones = layout_run("11111111", aspects, 64.0)
    _, n_zeros = layout_run("00000000", aspects, 64.0)
    assert abs(n_ones - n_zeros) > 1.0
    # non-digits keep natural width even in mono mode
    entries, _ = layout_run("0.0", aspects, 100.0, mono_advance=adv)
    assert abs(entries[1][2] - 20.0) < 1e-9


# --- key-edge counting -----------------------------------------------------------------

def test_key_series_edges_and_deconflation():
    ks = KeySeries(_frames([
        (10, 0, 0, KEY_K1 | KEY_M1),   # stable keyboard tap: M1 rides K1
        (20, 0, 0, KEY_K1 | KEY_M1),   # held — no new edge
        (30, 0, 0, 0),
        (40, 0, 0, KEY_K2),
        (50, 0, 0, 0),
        (60, 0, 0, KEY_K1),
        (70, 0, 0, KEY_K1 | KEY_M2),   # M2 WITHOUT K2 → real mouse press
        (80, 0, 0, 0),
    ]))
    assert ks.total_counts == (2, 1, 0, 1)
    assert ks.used == (True, True, False, True)
    held, counts = ks.state_at(15)
    assert held == 1 and counts == (1, 0, 0, 0)
    held, counts = ks.state_at(75)
    assert held == (1 | 8) and counts == (2, 1, 0, 1)
    held, counts = ks.state_at(5)      # idle lead-in frame
    assert held == 0 and counts == (0, 0, 0, 0)
    assert ks.state_at(-10) == (0, (0, 0, 0, 0))


# --- standardised score + combo timeline over a real sim --------------------------------

def _three_circle_run(click_third: bool):
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "200,100,2000,1,0,0:0:0:0:\n"
              "300,100,3000,1,0,0:0:0:0:\n")
    entries = [(1000, 100, 100, KEY_K1), (1020, 100, 100, 0),
               (2000, 200, 100, KEY_K1), (2020, 200, 100, 0)]
    if click_third:
        entries += [(3000, 300, 100, KEY_K1), (3020, 300, 100, 0)]
    else:
        entries += [(3500, 400, 300, 0)]
    return StdRuleset(bm, _frames(entries)).run()


def test_standardised_score_monotone_and_caps_at_1m():
    sim = _three_circle_run(click_third=True)
    scores = [e.score_after for e in sim.events]
    assert all(b >= a for a, b in zip(scores, scores[1:]))
    assert scores[-1] == 1_000_000          # perfect run pins the formula's max
    accs = [e.acc_after for e in sim.events]
    assert accs[-1] == 1.0


def test_combo_timeline_steps_and_breaks():
    sim = _three_circle_run(click_third=False)
    # increments at the two hits, reset at the third circle's window close
    assert sim.combo_timeline[:2] == [(1000.0, 1), (2000.0, 2)]
    t_break, v = sim.combo_timeline[2]
    assert v == 0 and abs(t_break - (3000.0 + 129.5)) < 1e-6
    d = HudData(sim)
    assert d.combo_at(1500)[0] == 1
    assert d.combo_at(2500)[0] == 2
    assert d.combo_at(3200)[0] == 0
    assert d.max_combo == 2
    b = d.last_break_at(4000)
    assert b is not None and b.prev_combo == 2 and abs(b.time_ms - t_break) < 1e-6
    assert d.last_break_at(3000) is None


def test_hud_data_score_acc_grade_ur():
    sim = _three_circle_run(click_third=False)
    d = HudData(sim)
    # before anything: zeros / clean sheet
    assert d.score_at(500) == 0.0
    assert d.acc_at(500) == 1.0 and d.grade_at(500) == "SS"
    # roll: halfway through the tween after the first judgment
    s1 = sim.events[0].score_after
    assert abs(d.score_at(1000.0 + 60.0) - s1 * 0.5) < 1.0
    assert d.score_at(1000.0 + 500.0) == s1
    # final acc = 2/3 of value weight (300+300+0)/(300*3)
    end = 5000.0
    assert abs(d.acc_at(end) - 2.0 / 3.0) < 1e-9
    assert d.grade_at(end) == "C"           # 66.7% 300s
    # UR from the two on-time hits (delta 0,0) → 0.0; n == 2
    ur, avg, n = d.ur_at(end)
    assert n == 2 and ur == 0.0 and avg == 0.0
    errs = d.errors_in_window(end)
    assert len(errs) == 2
    assert ERR_ARROW_N >= 2


def test_endpoint_pin_scales_display():
    """The pin hook (mania pattern): displayed scores scale so the final
    event lands on the authoritative total — HUD-side only, StdHud-less
    version exercised through the same arithmetic."""
    sim = _three_circle_run(click_third=True)
    d = HudData(sim)
    final = d.ev_scores[-1]
    assert final == 1_000_000
    pin = 727_272 / final
    assert int(round(d.score_at(1e9) * pin)) == 727_272
    mid = d.score_at(2500.0)
    assert 0 < mid * pin < 727_272
