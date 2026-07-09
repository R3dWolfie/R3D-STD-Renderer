"""HUD math tests — acc formula + the final-sample fix (the 84.72-vs-84.73
quirk), score pinning, the Argon rolling/pop behaviour, the LEGACY
displayed-vs-actual two-counter state machine, grade thresholds, UR,
key-edge counting and the mono text layout. Pure CPU (no GL)."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from osu_std_renderer.render.hud import (
    ARGON_ROLL_MS, ERR_ARROW_N, HudData, KeySeries, acc_display_value,
    argon_combo_scale_at, ease_out_quad, grade_for, layout_run,
    legacy_combo_display_timeline, rolled, std_accuracy, unstable_rate,
)
from osu_std_renderer.beatmap import load_full
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


def test_acc_display_matches_replay_meta_derivation():
    """The HUD's displayed percentage uses EXACTLY replay.py's meta
    derivation round(acc·100, 2) — the YOASOBI case: 318500/375900."""
    acc = (300 * 997 + 100 * 184 + 50 * 20) / (300.0 * 1253)
    assert f"{acc_display_value(acc):.2f}" == "84.73"
    # the vio case: 133200/152700 → 87.23 (lazer's floor would say 87.22)
    acc = (300 * 423 + 100 * 63) / (300.0 * 509)
    assert f"{acc_display_value(acc):.2f}" == "87.23"


# --- grade thresholds --------------------------------------------------------------

def test_grade_thresholds():
    # osu!(lazer)-exact: accuracy cutoffs X=1/S=.95/A=.9/B=.8/C=.7/D=0 with
    # OsuScoreProcessor's std override (a Miss caps S/X at A).
    assert grade_for(0, 0, 0, 0) == "SS"          # clean sheet (rank starts X)
    assert grade_for(100, 0, 0, 0) == "SS"        # 100.00%
    assert grade_for(95, 5, 0, 0) == "S"          # 96.67% clean → S
    assert grade_for(94, 3, 3, 0) == "S"          # 95.50% clean → S (acc-based)
    assert grade_for(95, 4, 0, 1) == "A"          # 96.33% but a miss → A
    assert grade_for(85, 15, 0, 0) == "A"         # exactly 90.00% → A
    assert grade_for(85, 14, 0, 1) == "B"         # 89.67% (< 90) → B
    assert grade_for(75, 25, 0, 0) == "B"         # 83.33% → B
    assert grade_for(65, 30, 0, 5) == "C"         # 75.00% → C (miss won't cap C)
    assert grade_for(50, 30, 10, 10) == "D"       # 61.67% → D


# --- UR -----------------------------------------------------------------------------

def test_unstable_rate():
    assert unstable_rate([]) == 0.0
    assert unstable_rate([12.0]) == 0.0
    assert unstable_rate([5.0, 5.0, 5.0]) == 0.0        # no spread
    assert abs(unstable_rate([10.0, -10.0, 10.0, -10.0]) - 100.0) < 1e-9
    d = [3.0, -7.0, 12.0, 0.0, -2.0]
    mean = sum(d) / len(d)
    want = 10.0 * math.sqrt(sum((x - mean) ** 2 for x in d) / len(d))
    assert abs(unstable_rate(d) - want) < 1e-9


# --- rolling (RollingCounter port) ---------------------------------------------------

def test_rolled_tween_eased():
    assert rolled(0, 1000, -5) == 0
    assert rolled(0, 1000, ARGON_ROLL_MS) == 1000
    assert rolled(0, 1000, 5000) == 1000
    # OutQuad: at half time, more than half the distance is covered
    mid = rolled(0, 1000, ARGON_ROLL_MS / 2)
    assert 500 < mid < 1000
    assert abs(mid - 1000 * ease_out_quad(0.5)) < 1e-9
    # monotone
    vals = [rolled(0, 1000, a) for a in range(0, 251, 25)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


# --- Argon combo pop (ArgonComboCounter.Current.BindValueChanged port) ---------------

def test_argon_combo_pop_scale():
    changes = [(1000.0, 0, 1)]
    assert abs(argon_combo_scale_at(changes, 1000.0) - 1.1) < 1e-9
    assert argon_combo_scale_at(changes, 999.0) == 1.0
    assert abs(argon_combo_scale_at(changes, 1500.0) - 1.0) < 1e-6
    mid = argon_combo_scale_at(changes, 1100.0)
    assert 1.0 < mid < 1.1
    # rapid chain clamps at 1.4
    chain = [(1000.0 + i * 10.0, i, i + 1) for i in range(20)]
    assert argon_combo_scale_at(chain, 1190.0) <= 1.4 + 1e-9
    # a combo-breaking miss pops down 0.8× and decays over 2000 ms
    miss = [(1000.0, 30, 0)]
    assert abs(argon_combo_scale_at(miss, 1000.0) - 0.8) < 1e-9
    assert argon_combo_scale_at(miss, 2000.0) < 1.0
    assert abs(argon_combo_scale_at(miss, 3100.0) - 1.0) < 1e-6


# --- LEGACY two-counter state machine -------------------------------------------------

def test_legacy_combo_two_counter_increments():
    """+1 pops a ghost of the NEW value immediately; the displayed count
    commits 160 ms later (big_pop_out_duration − 140) with the small pop."""
    disp, pops, ghosts = legacy_combo_display_timeline(
        [(1000.0, 1), (2000.0, 2)])
    assert ghosts == [(1000.0, 1), (2000.0, 2)]
    assert (1160.0, 1) in disp and (2160.0, 2) in disp
    assert pops == [1160.0, 2160.0]
    # displayed value BEFORE the commit is still the old one
    d = dict()
    val = 0
    for t, v in disp:
        if t <= 1100.0:
            val = v
    assert val == 0


def test_legacy_combo_two_counter_fast_chain_catches_up():
    """Increments faster than the 160 ms commit invalidate the pending
    schedule (scheduledPopOutCurrentId); the displayed count catches up
    one step per change and commits the last one on time."""
    disp, pops, ghosts = legacy_combo_display_timeline(
        [(0.0, 1), (50.0, 2), (100.0, 3)])
    assert [g[1] for g in ghosts] == [1, 2, 3]
    # no commit fired inside the chain, catch-ups at 50/100, final at 260
    assert (50.0, 1) in disp and (100.0, 2) in disp and (260.0, 3) in disp
    assert pops == [260.0]


def test_legacy_combo_rolldown_on_break():
    """A reset ROLLS the displayed count down one step per 20 ms."""
    disp, _pops, _ghosts = legacy_combo_display_timeline(
        [(0.0, 1), (300.0, 2), (600.0, 3), (2000.0, 0)])
    steps = [(t, v) for t, v in disp if t >= 2000.0]
    assert steps == [(2020.0, 2), (2040.0, 1), (2060.0, 0)]


def test_legacy_combo_rolldown_interrupted_by_new_combo():
    """A new increment during the roll snaps the display to 0 first
    (FinishTransforms) and then behaves like a fresh 0→1."""
    disp, _pops, ghosts = legacy_combo_display_timeline(
        [(0.0, 1), (100.0, 2), (200.0, 3), (1000.0, 0), (1030.0, 1)])
    # roll got one step out (1020 → 2), then snapped to 0 at 1030
    assert (1020.0, 2) in disp
    assert (1030.0, 0) in disp
    assert (1030.0, 1) in ghosts
    assert (1190.0, 1) in disp        # commit 160 ms after the increment


# --- mono layout (score roll must not jitter) -----------------------------------------

def test_layout_run_mono_stable_width():
    aspects = {"0": 0.55, "1": 0.30, "8": 0.58, ".": 0.2, "%": 0.9}
    adv = max(aspects[c] for c in "018")
    _, w_ones = layout_run("11111111", aspects, 64.0, mono_advance=adv)
    _, w_zeros = layout_run("00000000", aspects, 64.0, mono_advance=adv)
    _, w_mixed = layout_run("18011810", aspects, 64.0, mono_advance=adv)
    assert abs(w_ones - w_zeros) < 1e-9
    assert abs(w_ones - w_mixed) < 1e-9
    _, n_ones = layout_run("11111111", aspects, 64.0)
    _, n_zeros = layout_run("00000000", aspects, 64.0)
    assert abs(n_ones - n_zeros) > 1.0
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
    assert ks.state_at(-10) == (0, (0, 0, 0, 0))
    # edge times for the press/release animations
    assert ks.press_times[0] == [10, 60]
    assert ks.release_times[0] == [30, 80]
    assert ks.last_press_at(0, 65) == 60
    assert ks.last_release_at(0, 65) == 30
    assert ks.last_press_at(2, 1e9) is None


def test_key_series_ignores_pre_gameplay_presses():
    # m-1: warm-up taps before gameplay_start (the first object) don't COUNT,
    # but the held state + press-edge times still track (a held key still
    # lights up).
    ks = KeySeries(_frames([
        (10, 0, 0, KEY_K1),            # warm-up tap, before gameplay
        (20, 0, 0, 0),
        (30, 0, 0, KEY_K2),            # warm-up tap
        (40, 0, 0, 0),
        (100, 0, 0, KEY_K1),           # first real press at gameplay start
        (110, 0, 0, 0),
    ]), gameplay_start=100.0)
    assert ks.total_counts == (1, 0, 0, 0)     # only the t=100 press counts
    assert ks.state_at(105)[1] == (1, 0, 0, 0)
    assert ks.state_at(35)[1] == (0, 0, 0, 0)  # warm-up not counted
    assert ks.press_times[0] == [10, 100]      # edge times untouched
    assert ks.state_at(15)[0] == 1             # warm-up hold still lights K1


# --- standardised score + combo timeline over a real sim --------------------------------

def _three_circle_run(click_third: bool, third_delta: float = 0.0):
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "200,100,2000,1,0,0:0:0:0:\n"
              "300,100,3000,1,0,0:0:0:0:\n")
    entries = [(1000, 100, 100, KEY_K1), (1020, 100, 100, 0),
               (2000, 200, 100, KEY_K1), (2020, 200, 100, 0)]
    if click_third:
        t3 = int(3000 + third_delta)
        entries += [(t3, 300, 100, KEY_K1), (t3 + 20, 300, 100, 0)]
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
    assert d.score_at(500) == 0.0
    assert d.acc_at(500) == 1.0 and d.grade_at(500) == "SS"
    s1 = sim.events[0].score_after
    assert d.score_at(1000.0 + ARGON_ROLL_MS + 1) == s1     # roll landed
    assert 0 < d.score_at(1000.0 + 50.0) < s1               # mid-roll
    end = 5000.0
    assert abs(d.acc_at(end) - 2.0 / 3.0) < 1e-9
    assert d.grade_at(end) == "D"           # 66.67% acc (< 70%) → D (lazer)
    ur, avg, n = d.ur_at(end)
    assert n == 2 and ur == 0.0 and avg == 0.0
    assert len(d.errors_in_window(end)) == 2
    assert ERR_ARROW_N >= 2


def test_final_acc_reads_last_event_not_a_timestamp():
    """The 84.72-vs-84.73 root cause: a circle hit LATE judges AFTER the
    last object's end time, so sampling acc at last_end+1 ms missed the
    final event. final_acc() must read the event stream's tail."""
    sim = _three_circle_run(click_third=True, third_delta=80.0)
    d = HudData(sim)
    last_obj_end = 3000.0
    assert d.ev_times[-1] > last_obj_end + 1.0      # the quirk's setup
    counts_acc = std_accuracy(*sim.final_counts)
    assert abs(d.final_acc() - counts_acc) < 1e-12
    # the old sampling point disagrees — that WAS the bug
    assert d.acc_at(last_obj_end + 1.0) != d.final_acc()


# --- per-element component selection (the HYBRID rule, owner 2026-07-08) -------------

class _FakeSkin:
    """Just enough of SkinElements for StdHud's selection logic."""

    def __init__(self, loaded):
        self.loaded = set(loaded)
        self.empty = set()
        self.size = {}

    def has(self, name):
        return name in self.loaded


def _hud_with(skin, **setting_overrides):
    from osu_std_renderer.render.hud import StdHud
    from osu_std_renderer.settings import StdRenderSettings

    class _Spr:
        width, height = 1920, 1080

        def upload_texture(self, key, rgba):
            pass

    sim = _three_circle_run(click_third=True)
    bm = _map("100,100,1000,1,0,0:0:0:0:\n")
    settings = StdRenderSettings(**setting_overrides)
    return StdHud(_Spr(), None, settings, sim, [], bm, skin_elems=skin,
                  health=None)


def test_hud_component_selection_hybrid_per_element():
    """The HYBRID rule (owner decision 2026-07-08, supersedes the
    same-day all-legacy correction): PER ELEMENT — the skin's legacy
    component where the skin SHIPS that element's textures, ARGON for
    whatever the skin lacks. Skinless renders stay all-Argon."""
    # skinless → all Argon
    hud = _hud_with(None)
    assert not hud.legacy_score and not hud.legacy_combo
    assert not hud.legacy_health and not hud.legacy_keys
    # a rich skin → all legacy
    hud = _hud_with(_FakeSkin({"score_digits", "combo_digits",
                               "scorebar-colour", "inputoverlay-key"}))
    assert hud.legacy_score and hud.legacy_combo
    assert hud.legacy_health and hud.legacy_keys
    # the owner-skin shape (fonts only, e.g. osu_30196342): skin
    # score/combo fonts render, hp bar + key counter go ARGON
    hud = _hud_with(_FakeSkin({"score_digits", "combo_digits"}))
    assert hud.legacy_score and hud.legacy_combo
    assert not hud.legacy_health and not hud.legacy_keys
    # scorebar only → legacy hp, Argon everything else; either
    # inputoverlay texture is enough for legacy keys
    hud = _hud_with(_FakeSkin({"scorebar-colour"}))
    assert hud.legacy_health
    assert not hud.legacy_score and not hud.legacy_combo
    assert not hud.legacy_keys
    hud = _hud_with(_FakeSkin({"inputoverlay-background"}))
    assert hud.legacy_keys and not hud.legacy_health
    # an EMPTY skin (zero textures) ships nothing → all-Argon HUD by
    # default (gameplay keeps its legacy fallback — unchanged)
    hud = _hud_with(_FakeSkin(set()))
    assert not hud.legacy_score and not hud.legacy_combo
    assert not hud.legacy_health and not hud.legacy_keys


def test_legacy_defaults_forces_all_legacy():
    """legacy_defaults=ON (--legacy-defaults) = the ALL-LEGACY look:
    every HUD element selects its legacy component even skinless / under
    an empty skin — the lg_* bakes carry whatever the skin doesn't ship.
    PRECEDENCE: renderer_default_font_and_ranks wins over it when both
    are set (numbers/ranks go Argon/procedural, the rest stays legacy)."""
    # empty skin + legacy_defaults → all legacy (legacy_only_v2 shape)
    hud = _hud_with(_FakeSkin(set()), legacy_defaults=True)
    assert hud.legacy_score and hud.legacy_combo
    assert hud.legacy_health and hud.legacy_keys
    # skinless + legacy_defaults → still all legacy
    hud = _hud_with(None, legacy_defaults=True)
    assert hud.legacy_score and hud.legacy_combo
    assert hud.legacy_health and hud.legacy_keys
    # precedence: renderer_default_font_and_ranks wins for fonts+ranks
    hud = _hud_with(_FakeSkin(set()), legacy_defaults=True,
                    renderer_default_font_and_ranks=True)
    assert hud.force_default
    assert not hud.legacy_score and not hud.legacy_combo   # Argon numbers
    assert hud.legacy_health and hud.legacy_keys            # stay legacy


def test_renderer_default_font_and_ranks_toggle():
    """renderer_default_font_and_ranks=ON forces the renderer's default
    numbers (Argon) + procedural rank text even under a skin with fonts;
    hp/keys selection is untouched (fonts + ranks only)."""
    sk = _FakeSkin({"score_digits", "combo_digits", "scorebar-colour",
                    "inputoverlay-key", "ranking-S-small"})
    hud = _hud_with(sk)
    assert hud.legacy_score and hud.legacy_combo and not hud.force_default
    hud = _hud_with(sk, renderer_default_font_and_ranks=True)
    assert hud.force_default
    assert not hud.legacy_score and not hud.legacy_combo   # Argon numbers
    assert hud.legacy_health and hud.legacy_keys            # unaffected


def test_hit_error_block_parks_near_the_bottom():
    """Owner item: the hit-error bar + UR meter sit near the bottom edge
    with a small (12-20 UI px) margin — the block's lowest pixel (the UR
    readout baseline) must land inside that band."""
    from osu_std_renderer.render.hud import (ERR_BAND_H, ERR_Y_FROM_BOTTOM,
                                             UI_HEIGHT, UR_H)
    cy = UI_HEIGHT - ERR_Y_FROM_BOTTOM
    block_bottom = cy + ERR_BAND_H / 2.0 + 10.0 + UR_H   # _hit_error layout
    margin = UI_HEIGHT - block_bottom
    assert 12.0 <= margin <= 20.0, margin


def test_endpoint_pin_scales_display():
    """pin math (the mania pattern): displayed scores scale so the final
    event lands exactly on the .osr total — 423154 in the proof render."""
    sim = _three_circle_run(click_third=True)
    d = HudData(sim)
    final = d.final_score()
    assert final == 1_000_000
    pin = 423_154 / final
    assert int(round(d.score_at(1e9) * pin)) == 423_154
    mid = d.score_at(2500.0)
    assert 0 < mid * pin < 423_154


# --- Argon counter growth (fidelity pass 3: overflow-proof, sourced minimum) ---
# The Argon score/combo counters draw a MINIMUM wireframe (RequiredDisplay-
# Digits) and GROW past it — one wireframe cell per lit digit. A >999,999
# pinned classic score (str(), never :06d) and a >999x combo must render every
# digit backed by its own cell, never clipped to a fixed 6-cell width.
# Source: ppy/osu ArgonScoreCounter / ArgonComboCounter
# getDigitsRequiredForDisplayCount() (score min = GameplayScoreCounter
# Standardised RequiredDisplayDigits 6; combo min = DisplayXSymbol ? 2 : 1).
from osu_std_renderer.render.hud import StdHud  # noqa: E402


class _CounterBank:
    argon_seg_advance = 132.0 / 240.0
    glyph_aspect = {ch: 0.6 for ch in "COMBOACURYXP0123456789.%"}
    glyph_mono_advance = 0.6
    argon_glyph_aspect = {ch: 0.6 for ch in "COMBOACURYXP0123456789.%"}
    argon_glyph_mono_advance = 0.6


class _CounterData:
    def __init__(self, score, combo):
        self._s, self._c = score, combo
        self.combo_changes = [(0.0, 0, combo)]

    def score_at(self, t, roll_ms=ARGON_ROLL_MS):
        return float(self._s)

    def combo_displayed_at(self, t, roll_ms=ARGON_ROLL_MS):
        return self._c


def _counter_hud(score, combo):
    h = object.__new__(StdHud)
    h.s = type("S", (), {"show_score": True, "show_combo": True,
                         "show_grade": False})()
    h.es = h.lk = h.k = h.op = 1.0
    h.bank = _CounterBank()
    h.argon_league = True          # these exercise the Argon counters
    h._pin = 1.0
    h.data = _CounterData(score, combo)
    h.ui_w_l = 1366.0
    return h


def _counter_cells(out):
    wire = [s for s in out if s.texture_key in ("argon_wireframe",
                                                "argon_wireframe_dot")]
    lit = [s for s in out if s.texture_key.startswith("aseg_")]
    return wire, lit


def _all_lit_backed(wire, lit):
    return all(any(abs(s.x - w.x) < 1e-6 for w in wire) for s in lit)


def test_argon_score_counter_grows_past_minimum_wireframe():
    # 5-digit score → the 6-cell RequiredDisplayDigits minimum, every lit backed
    h = _counter_hud(52_289, 0)
    out = []
    h._argon_score_block(out, 0.0)
    wire, lit = _counter_cells(out)
    assert len(lit) == 5 and len(wire) == 6
    assert _all_lit_backed(wire, lit)
    # 7-digit pinned classic total → 7 lit + 7 wire (grew past 6, NO overflow)
    h = _counter_hud(1_234_567, 0)
    out = []
    h._argon_score_block(out, 0.0)
    wire, lit = _counter_cells(out)
    assert len(lit) == 7 and len(wire) == 7
    assert _all_lit_backed(wire, lit)
    # 8-digit classic total still grows (never capped)
    h = _counter_hud(12_345_678, 0)
    out = []
    h._argon_score_block(out, 0.0)
    wire, lit = _counter_cells(out)
    assert len(lit) == 8 and len(wire) == 8
    assert _all_lit_backed(wire, lit)


def test_argon_combo_counter_grows_and_holds_minimum():
    # single-digit combo → 2 cells (DisplayXSymbol ? 2 : 1): 1 digit + the x
    h = _counter_hud(0, 7)
    out = []
    h._argon_combo(out, 0.0)
    wire, lit = _counter_cells(out)
    n_num = sum(1 for s in lit if s.texture_key != "aseg_x")
    assert n_num == 1 and len(wire) == 2
    assert _all_lit_backed(wire, lit)
    # 4-digit combo → 5 cells (4 digits + x), grown past the 2-cell floor
    h = _counter_hud(0, 1234)
    out = []
    h._argon_combo(out, 0.0)
    wire, lit = _counter_cells(out)
    n_num = sum(1 for s in lit if s.texture_key != "aseg_x")
    assert n_num == 4 and len(wire) == 5
    assert _all_lit_backed(wire, lit)


# --- fidelity pass 3: ArgonAccuracyCounter whole/fraction/percent ratios -------
# ppy/osu master ArgonAccuracyCounter.cs — the accuracy text is a horizontal
# FillFlow of three ArgonCounterTextComponents: the whole part (full digit
# height, with the ACCURACY label), the '.dd' fractionPart at Scale = 0.5
# (bottom-aligned), and the percentText with NO Scale override → FULL digit
# height, top-aligned with the whole part (Margin.Top == wholePart
# NumberContainer.Y == 12). The previous code mis-sized the '%' at 0.6·h.
class _AccData:
    def __init__(self, acc):
        self._a = acc

    def acc_at(self, t, roll_ms=ARGON_ROLL_MS):
        return self._a


def _acc_hud(acc):
    h = _counter_hud(0, 0)          # reuse the counter harness (bank/es/lk/op)
    h.data = _AccData(acc)
    return h


def test_argon_accuracy_percent_full_height_fraction_half():
    from osu_std_renderer.render.hud import ARGON_DIGIT_H
    h = _acc_hud(1.0)                                   # displays 100.00%
    out = []
    h._argon_accuracy(out, 0.0)
    lit = [s for s in out if s.texture_key
           and s.texture_key.startswith("aseg_")]
    whole_h = ARGON_DIGIT_H * h.es
    pct = [s for s in lit if s.texture_key == "aseg_pct"]
    assert len(pct) == 1                               # exactly one '%'
    # '%' is FULL digit height (not the old 0.6·h)
    assert abs(pct[0].h - whole_h) < 1e-6
    # the whole part "100" runs at full height
    whole = [s for s in lit if s.texture_key != "aseg_pct"
             and abs(s.h - whole_h) < 1e-6]
    assert len(whole) == 3                             # 1, 0, 0
    # the ".00" fraction runs at HALF height (Scale 0.5)
    frac = [s for s in lit if abs(s.h - whole_h * 0.5) < 1e-6]
    assert len(frac) == 3                              # '.', 0, 0
    assert not [s for s in lit if s not in whole + frac + pct]
    # '%' top-aligns with the whole digits (shared top edge)
    whole_top = min(s.y - s.h / 2.0 for s in whole)
    assert abs((pct[0].y - pct[0].h / 2.0) - whole_top) < 1e-6
    # the fraction sits BELOW (bottom-aligned): its top edge is lower
    frac_top = min(s.y - s.h / 2.0 for s in frac)
    assert frac_top > whole_top + 1e-6
