"""Relax (RX) auto-tap synthesis + Autopilot (AP) path.

A Relax .osr carries cursor motion but zero key presses (osu! auto-taps under
OsuModRelax), so the press-edge driven judgment/HUD pipeline sees no clicks and
the raw sim misses everything. replay/relax.py synthesizes the presses the mod
injects; these tests prove the synthesized frames make the SAME judgment
pipeline resolve hits/combo/tracking WITHOUT any original key edge, that
reconcile still snaps the counts exact, and that the non-RX / Autopilot paths
are untouched (AP keeps its real presses; it is only fail-immune). Pure CPU."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.beatmap.objects import Slider, Spinner
from osu_std_renderer.replay import (MOD_RELAX, StdFrame, is_relax_meta,
                                      synthesize_relax_frames)
from osu_std_renderer.replay.replay import (KEY_K1, KEY_K2, ReplayMeta,
                                            detect_fail_time)
from osu_std_renderer.ruleset import JudgmentKind, StdRuleset
from osu_std_renderer.ruleset.ruleset import press_edges

MOD_AUTOPILOT = 0x2000
STABLE_GV = 20_251_128

MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:RelaxTest
Artist:Unit
Creator:R3D
Version:RX

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

# five well-separated circles (no stacking): (x, y, t)
CIRCLES = [(100, 100, 1000), (200, 120, 1500), (300, 140, 2000),
           (150, 260, 2500), (350, 280, 3000)]
CIRCLE_LINES = "".join(f"{x},{y},{t},1,0,0:0:0:0:\n" for x, y, t in CIRCLES)
SLIDER_LINE = "256,192,1000,2,0,L|506:192,1,250,0|0,0:0|0:0,0:0:0:0:\n"
SPINNER_LINE = "256,192,1000,12,0,3000,0:0:0:0:\n"


def _map(objects: str):
    d = Path(tempfile.mkdtemp(prefix="stdrx_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER + objects, encoding="utf-8")
    return load_full(p)


def _meta(mods: int, counts=(5, 0, 0, 0), max_combo=5,
          game_version=STABLE_GV, lazer_mods=()) -> ReplayMeta:
    c300, c100, c50, cmiss = counts
    return ReplayMeta(
        mode=0, beatmap_md5="", player_name="t", mods=mods, score=0,
        max_combo=max_combo, count_300=c300, count_100=c100, count_50=c50,
        count_geki=0, count_katu=0, count_miss=cmiss, accuracy=100.0,
        grade="SS", game_version=game_version, lazer_mods=lazer_mods)


def _keyless_frames_over_circles() -> list[StdFrame]:
    """Cursor visits each circle around its hit time; NO keys pressed (exactly
    what a Relax .osr contains). Cursor parked far away between objects."""
    fr = [StdFrame(time_ms=0, x=5.0, y=5.0, keys=0)]
    for x, y, t in CIRCLES:
        for dt in (-40, -20, 0, 20, 40):
            fr.append(StdFrame(time_ms=t + dt, x=float(x), y=float(y), keys=0))
        fr.append(StdFrame(time_ms=t + 120, x=5.0, y=5.0, keys=0))
    fr.sort(key=lambda f: f.time_ms)
    return fr


# --- 1. RX detection --------------------------------------------------------------

def test_is_relax_detects_legacy_bitmask():
    assert MOD_RELAX == 0x80
    assert is_relax_meta(_meta(MOD_RELAX)) is True
    assert is_relax_meta(_meta(MOD_RELAX | 0x8)) is True          # RX+Hidden
    assert is_relax_meta(_meta(MOD_RELAX | 0x40 | 0x8)) is True   # RX+DT+HD


def test_is_relax_detects_lazer_acronym():
    # a lazer replay whose legacy bitmask somehow lacks the bit still trips on
    # the RX acronym from the ScoreInfo list.
    assert is_relax_meta(_meta(0, lazer_mods=("HD", "RX"))) is True


def test_is_relax_false_for_non_rx():
    assert is_relax_meta(_meta(0)) is False
    assert is_relax_meta(_meta(MOD_AUTOPILOT)) is False           # Autopilot
    assert is_relax_meta(_meta(0x8 | 0x40)) is False              # HD+DT
    assert is_relax_meta(None) is False


# --- 2. synthesis writes alternating presses --------------------------------------

def test_synthesis_injects_alternating_presses():
    bm = _map(CIRCLE_LINES)
    fr = _keyless_frames_over_circles()
    assert all(f.keys == 0 for f in fr)             # the RX input is keyless

    out = synthesize_relax_frames(fr, bm)
    assert len(out) == len(fr)                       # same timeline, keys added
    assert any(f.keys for f in out)                  # presses now exist

    edges = press_edges(out)
    assert len(edges) == len(CIRCLES)                # one tap per circle
    # OsuModRelax alternates the button each press (wasLeft toggle)
    assert [e.channel for e in edges] == [1, 2, 1, 2, 1]
    # each tap lands within the scoring window of its circle
    for e, (x, y, t) in zip(edges, CIRCLES):
        assert abs(e.time_ms - t) <= 40


def test_synthesis_is_noop_on_empty_frames():
    assert synthesize_relax_frames([], _map(CIRCLE_LINES)) == []


# --- 3. the hit VISUAL is judgment-driven, not key-edge-driven ---------------------

def test_keyless_frames_miss_everything_without_synthesis():
    """Baseline: a Relax .osr fed straight in (keys==0) misses every object in
    the raw sim and never builds combo — the bug this fix addresses."""
    bm = _map(CIRCLE_LINES)
    fr = _keyless_frames_over_circles()
    sim = StdRuleset(bm, fr, _meta(MOD_RELAX), reconcile=False).run()
    assert sim.sim_counts == (0, 0, 0, len(CIRCLES))
    assert sim.sim_max_combo == 0
    for v in sim.verdicts.values():
        assert v.kind is JudgmentKind.MISS
        assert v.hit_time is None


def test_synthesized_frames_hit_at_judged_times():
    """After synthesis the raw sim (NO reconcile) judges each circle a hit at
    its own near-on-time moment, and combo advances — circles explode / popups
    fire / the counter steps entirely off the synthesized judgment timeline."""
    bm = _map(CIRCLE_LINES)
    fr = synthesize_relax_frames(_keyless_frames_over_circles(), bm)
    sim = StdRuleset(bm, fr, _meta(MOD_RELAX), reconcile=False).run()
    assert sim.sim_counts == (5, 0, 0, 0)            # all Great, no reconcile
    assert sim.sim_max_combo == len(CIRCLES)
    objs = sorted(bm.hit_objects, key=lambda o: o.get_start_time())
    for o, (x, y, t) in zip(objs, CIRCLES):
        v = sim.verdict_for(o)
        assert v.kind is JudgmentKind.HIT300
        assert v.hit_time is not None
        assert abs(v.hit_time - t) <= 40            # near the object's own time
    # combo timeline actually steps (the HUD counter has something to show)
    assert max(c for _, c in sim.combo_timeline) == len(CIRCLES)


def test_reconcile_counts_stay_exact_under_relax():
    """Whatever the synthesized sim produces, reconcile snaps the DISPLAYED
    counts to the .osr's authoritative totals (here 3/1/1/0)."""
    bm = _map(CIRCLE_LINES)
    fr = synthesize_relax_frames(_keyless_frames_over_circles(), bm)
    meta = _meta(MOD_RELAX, counts=(3, 1, 1, 0), max_combo=5)
    sim = StdRuleset(bm, fr, meta, reconcile=True).run()
    assert sim.final_counts == (3, 1, 1, 0)


# --- 4. slider follow-tracking + spinner spin-up engage via synthesized holds ------

def test_synthesis_holds_through_slider_and_tracks():
    bm = _map(SLIDER_LINE)
    slider = next(o for o in bm.hit_objects if isinstance(o, Slider))
    # keyless cursor: on the head at t=1000, then following the ball across the
    # slide (get_stacked_position_at), then away.
    fr = [StdFrame(0, 5.0, 5.0, 0)]
    t = 1000
    while t <= int(slider.get_end_time()):
        bx, by = slider.get_stacked_position_at(float(t), bm.diff)
        fr.append(StdFrame(t, float(bx), float(by), 0))
        t += 15
    fr.append(StdFrame(int(slider.get_end_time()) + 200, 5.0, 5.0, 0))

    out = synthesize_relax_frames(fr, bm)
    # a key is held across the body (not just a 1-frame tap)
    held_during = sum(1 for f in out
                      if 1000 <= f.time_ms <= slider.get_end_time() and f.keys)
    assert held_during > 5
    sim = StdRuleset(bm, out, _meta(MOD_RELAX, counts=(1, 0, 0, 0), max_combo=2),
                     reconcile=False).run()
    v = sim.verdict_for(slider)
    assert v.hit_time is not None                    # head tapped
    assert v.tracking                                # follow-area tracking ran
    assert v.kind is JudgmentKind.HIT300             # all parts tracked → 300


def test_synthesis_holds_through_spinner():
    bm = _map(SPINNER_LINE)
    spinner = next(o for o in bm.hit_objects if isinstance(o, Spinner))
    # keyless cursor circling the playfield centre during the spinner
    import math
    fr = [StdFrame(0, 256.0, 100.0, 0)]
    t = int(spinner.get_start_time())
    while t <= int(spinner.get_end_time()):
        # ~1 rad per 10 ms frame (< pi, no aliasing) → tens of revolutions,
        # comfortably above any required-spin count for the span.
        a = (t - spinner.get_start_time()) / 10.0
        fr.append(StdFrame(t, 256.0 + 90.0 * math.cos(a),
                           192.0 + 90.0 * math.sin(a), 0))
        t += 10
    out = synthesize_relax_frames(fr, bm)
    held = sum(1 for f in out
               if spinner.get_start_time() <= f.time_ms <= spinner.get_end_time()
               and f.keys)
    assert held > 10                                 # held across the spin span
    sim = StdRuleset(bm, out, _meta(MOD_RELAX, counts=(1, 0, 0, 0), max_combo=1),
                     reconcile=False).run()
    v = sim.verdict_for(spinner)
    assert v.kind is not JudgmentKind.MISS           # spun up (not a dead spinner)


# --- 5. non-RX frames are never synthesized (byte-identical path) ------------------

def test_non_rx_frames_are_untouched_by_the_pipeline():
    # is_relax_meta gates synthesis in cli.main; a normal replay is never
    # passed to synthesize_relax_frames, so its frames are the parse output
    # verbatim. Guard the gate here.
    assert is_relax_meta(_meta(0)) is False
    assert is_relax_meta(_meta(MOD_AUTOPILOT)) is False


# --- 6. Autopilot: real presses judged normally, and fail-immune -------------------

def test_autopilot_keeps_real_presses_and_judges_normally():
    """AP auto-AIMS but the player taps, so an AP .osr HAS key presses — it is
    not synthesized and routes through the ordinary click path."""
    bm = _map(CIRCLE_LINES)
    # AP frames already carry the taps (keys set on the hit frames)
    fr = [StdFrame(0, 5.0, 5.0, 0)]
    for x, y, t in CIRCLES:
        fr.append(StdFrame(t, float(x), float(y), KEY_K1))
        fr.append(StdFrame(t + 30, float(x), float(y), 0))
    fr.sort(key=lambda f: f.time_ms)
    assert not is_relax_meta(_meta(MOD_AUTOPILOT))   # no synthesis for AP
    sim = StdRuleset(bm, fr, _meta(MOD_AUTOPILOT), reconcile=False).run()
    assert sim.sim_counts == (5, 0, 0, 0)            # all hit via real presses
    assert sim.sim_max_combo == len(CIRCLES)


def test_autopilot_is_fail_immune():
    """OsuModAutopilot implements IApplicableFailOverride (PerformFail=>false):
    a zeroed lifebar never trips a fail under AP. Relax deliberately is NOT
    fail-immune (you can still fail while relaxing)."""
    dead = SimpleNamespace(
        mods=MOD_AUTOPILOT,
        life_bar_graph=[SimpleNamespace(time=500, life=0.0)])
    assert detect_fail_time(dead) is None            # AP overrides failing
    relaxing = SimpleNamespace(
        mods=MOD_RELAX,
        life_bar_graph=[SimpleNamespace(time=500, life=0.0)])
    assert detect_fail_time(relaxing) == 500.0       # RX can still fail


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("PASS", _name)
    print("all relax tests passed")
