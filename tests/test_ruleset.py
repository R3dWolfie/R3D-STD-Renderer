"""Judgment simulation tests — hit-window evaluation, press-edge
detection, stable notelock ordering, slider tracking + the three distinct
slider-part miss kinds (head / tick / tail), spinner model, reconcile
snap. Pure CPU (no GL)."""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from osu_std_renderer.beatmap import load_full
from osu_std_renderer.beatmap.objects import Slider
from osu_std_renderer.replay.replay import KEY_K1, KEY_K2, KEY_M1, StdFrame
from osu_std_renderer.ruleset import (
    MISS_WINDOW, JudgmentKind, OsuHitWindows, StdRuleset, press_edges,
)

# OD7 windows (lazer floor-0.5): great 37.5, ok 83.5, meh 129.5
MAP_HEADER = """osu file format v14

[General]
AudioFilename: audio.mp3
StackLeniency: 0.7
Mode: 0

[Metadata]
Title:RulesetTest
Artist:Unit
Creator:R3D
Version:Judge

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
    d = Path(tempfile.mkdtemp(prefix="stdruleset_"))
    p = d / "test.osu"
    p.write_text(MAP_HEADER + objects, encoding="utf-8")
    return load_full(p)


def _meta(c300: int, c100: int, c50: int, miss: int, max_combo: int = 0):
    from osu_std_renderer.replay.replay import ReplayMeta
    return ReplayMeta(
        mode=0, beatmap_md5="", player_name="test", mods=0, score=0,
        max_combo=max_combo, count_300=c300, count_100=c100, count_50=c50,
        count_geki=0, count_katu=0, count_miss=miss, accuracy=0.0, grade="A")


def _frames(entries) -> list[StdFrame]:
    """[(t, x, y, keys)] → frames (always prepend an idle frame)."""
    fr = [StdFrame(time_ms=0, x=0.0, y=0.0, keys=0)]
    fr += [StdFrame(time_ms=t, x=float(x), y=float(y), keys=k)
           for t, x, y, k in entries]
    return fr


# --- hit windows -----------------------------------------------------------------

def test_hit_windows_od5_od7():
    hw5 = OsuHitWindows(5.0)
    assert (hw5.great, hw5.ok, hw5.meh) == (49.5, 99.5, 149.5)
    hw7 = OsuHitWindows(7.0)
    assert (hw7.great, hw7.ok, hw7.meh) == (37.5, 83.5, 129.5)
    assert OsuHitWindows.MISS_WINDOW == 400.0 == MISS_WINDOW


def test_window_evaluation_boundaries():
    hw = OsuHitWindows(7.0)
    assert hw.result_for(0.0) is JudgmentKind.HIT300
    assert hw.result_for(37.5) is JudgmentKind.HIT300
    assert hw.result_for(-37.5) is JudgmentKind.HIT300
    assert hw.result_for(37.6) is JudgmentKind.HIT100
    assert hw.result_for(83.5) is JudgmentKind.HIT100
    assert hw.result_for(83.6) is JudgmentKind.HIT50
    assert hw.result_for(129.5) is JudgmentKind.HIT50
    assert hw.result_for(129.6) is None
    assert hw.result_for(-200.0) is None
    assert hw.can_be_hit(129.5) and not hw.can_be_hit(129.6)


# --- press-edge detection -----------------------------------------------------------

def test_press_edges_held_keys_do_not_reclick():
    fr = _frames([
        (10, 5, 5, KEY_K1),                 # ch1 down
        (20, 5, 5, KEY_K1 | KEY_M1),        # M1 joins K1 — SAME channel, no edge
        (30, 5, 5, KEY_K1 | KEY_K2),        # ch2 down (ch1 still held)
        (40, 5, 5, 0),                      # all up
        (50, 5, 5, KEY_K2),                 # ch2 again
    ])
    presses = press_edges(fr)
    assert [(p.time_ms, p.channel) for p in presses] == [(10, 1), (30, 2), (50, 2)]


# --- circle judgment by timing delta -------------------------------------------------

def _one_circle_run(click_t: float | None, pos=(100, 100)):
    bm = _map("100,100,1000,1,0,0:0:0:0:\n")
    entries = []
    if click_t is not None:
        entries = [(click_t, pos[0], pos[1], KEY_K1), (click_t + 20, pos[0], pos[1], 0)]
    else:
        entries = [(2000, 400, 300, 0)]
    sim = StdRuleset(bm, _frames(entries)).run()
    v = sim.verdict_for(bm.hit_objects[0])
    return sim, v


def test_circle_window_tiers():
    for click_t, want in ((1010, JudgmentKind.HIT300),
                          (1050, JudgmentKind.HIT100),
                          (1100, JudgmentKind.HIT50)):
        sim, v = _one_circle_run(click_t)
        assert v.kind is want, (click_t, v.kind)
        assert v.hit_time == click_t
        assert v.delta == click_t - 1000


def test_circle_miss_at_window_close():
    sim, v = _one_circle_run(None)
    assert v.kind is JudgmentKind.MISS
    assert v.hit_time is None
    assert v.deadline == 1000 + 129.5
    assert sim.sim_counts == (0, 0, 0, 1)
    # miss popup at the window close
    ev = sim.events[0]
    assert ev.kind is JudgmentKind.MISS and ev.time_ms == v.deadline
    assert ev.combo_after == 0


def test_click_outside_radius_does_not_hit():
    # CS4 radius ≈ 36.5 osu!px; click 60px away → no target → miss
    sim, v = _one_circle_run(1000, pos=(160, 100))
    assert v.kind is JudgmentKind.MISS


# --- notelock (stable LegacyHitPolicy) ------------------------------------------------

def test_notelock_blocks_later_object_while_earlier_alive():
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "300,300,1100,1,0,0:0:0:0:\n")
    a, b = bm.hit_objects
    # click B in its window at t=1090 while A (deadline 1129.5) is unjudged
    # → notelock Shake, nothing judged; A misses at 1129.5; a second click
    # at t=1150 (A now judged) hits B with delta 50 → 100.
    fr = _frames([
        (1090, 300, 300, KEY_K1),
        (1120, 300, 300, 0),
        (1150, 300, 300, KEY_K1),
        (1200, 300, 300, 0),
    ])
    sim = StdRuleset(bm, fr).run()
    va, vb = sim.verdict_for(a), sim.verdict_for(b)
    assert va.kind is JudgmentKind.MISS
    assert vb.kind is JudgmentKind.HIT100 and vb.hit_time == 1150
    assert sim.shakes == 1


def test_notelock_click_routes_to_earliest_when_overlapping():
    # two stacked-in-time circles, same position: the click must hit A (the
    # EARLIEST unjudged), not B
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "100,100,1080,1,0,0:0:0:0:\n")
    a, b = bm.hit_objects
    fr = _frames([(1005, 100, 100, KEY_K1), (1030, 100, 100, 0)])
    sim = StdRuleset(bm, fr).run()
    assert sim.verdict_for(a).kind is JudgmentKind.HIT300
    assert sim.verdict_for(b).kind is JudgmentKind.MISS


def test_early_click_shakes_without_consuming():
    bm = _map("100,100,1000,1,0,0:0:0:0:\n")
    fr = _frames([
        (800, 100, 100, KEY_K1),    # 200ms early: inside hittable, outside meh
        (850, 100, 100, 0),
        (1000, 100, 100, KEY_K1),   # perfect
        (1050, 100, 100, 0),
    ])
    sim = StdRuleset(bm, fr).run()
    v = sim.verdict_for(bm.hit_objects[0])
    assert v.kind is JudgmentKind.HIT300 and v.hit_time == 1000
    assert sim.shakes == 1


# --- sliders: tracking + the three distinct miss kinds --------------------------------

# L slider: start 1000, velocity 0.2 px/ms, span 1250ms → end 2250,
# ticks at t=1500 (x=356) and t=2000 (x=456), tail (legacy last 2214).
SLIDER_LINE = "256,192,1000,2,0,L|506:192,1,250,0|0,0:0|0:0,0:0:0:0:\n"
LEAD_CIRCLE = "256,192,500,1,0,0:0:0:0:\n"       # combo seed before the slider


def _slider_map():
    bm = _map(LEAD_CIRCLE + SLIDER_LINE)
    circle, slider = bm.hit_objects
    assert isinstance(slider, Slider)
    return bm, circle, slider


def _ball_frames(slider, t0, t1, step=10, keys=KEY_K1):
    out = []
    t = t0
    while t <= t1:
        x, y = slider.position_at(t)
        out.append((t, x, y, keys))
        t += step
    return out


def _lead_click():
    return [(500, 256, 192, KEY_K1), (530, 256, 192, 0)]


def test_slider_perfect_300():
    bm, circle, slider = _slider_map()
    fr = _frames(_lead_click()
                 + [(1000, 256, 192, KEY_K1)]
                 + _ball_frames(slider, 1010, 2250))
    sim = StdRuleset(bm, fr).run()
    v = sim.verdict_for(slider)
    assert v.kind is JudgmentKind.HIT300
    assert [p.kind for p in v.parts] == ["head", "tick", "tick", "tail"]
    assert all(p.hit for p in v.parts)
    rec = v.slider_record
    assert rec.head_hit and rec.tail_hit and not rec.sliderbreaks
    assert rec.head_delta == 0.0
    assert len(rec.ticks) == 2
    # tracking covers the slide; no breaks
    assert v.tracked_at(1500) and v.tracked_at(2200)
    assert not v.breaks
    # combo: circle 1, head 2, ticks 3/4, tail 5
    slider_ev = [e for e in sim.events if e.object_id == 1][0]
    assert slider_ev.combo_after == 5
    assert slider_ev.time_ms == 2250


def test_slider_head_missed_only_is_break_no_misscount():
    """Head missed, ticks+tail tracked: combo RESETS at the head's miss
    moment (sliderbreak), result downgrades to 100 (3/4), miss count 0."""
    bm, circle, slider = _slider_map()
    # key held from t=900 far away (press edge routes to nothing), cursor
    # arrives on the ball without a new click → head never hit
    fr = _frames([(900, 0, 0, KEY_K1)] + _ball_frames(slider, 1005, 2250))
    sim = StdRuleset(bm, fr).run()
    v = sim.verdict_for(slider)
    rec = v.slider_record
    assert not rec.head_hit and rec.head_missed_at == 1000 + 129.5
    assert rec.tail_hit and all(p.hit for p in rec.ticks)
    assert rec.sliderbreaks == []            # head break tracked separately
    assert v.kind is JudgmentKind.HIT100     # 3/4 parts
    # miss count NOT incremented by the head (slider aggregate is the 100)
    assert sim.sim_counts[3] == 1            # only the lead circle missed
    # combo: lead circle missed (no click on it… it WAS clicked) —
    slider_ev = [e for e in sim.events if e.object_id == 1][0]
    # circle miss at 629.5 → 0; head miss resets (already 0); ticks 1/2, tail 3
    assert slider_ev.combo_after == 3


def test_slider_tick_missed_is_sliderbreak():
    """Only tick #2 missed: combo resets AT the tick (sliderbreak), tail
    still caught → 3/4 = 100; the break is in sliderbreaks."""
    bm, circle, slider = _slider_map()
    away = [(t, 0, 0, KEY_K1) for t in range(1600, 2100, 10)]
    fr = _frames(_lead_click()
                 + [(1000, 256, 192, KEY_K1)]
                 + _ball_frames(slider, 1010, 1590)
                 + away
                 + _ball_frames(slider, 2100, 2250))
    sim = StdRuleset(bm, fr).run()
    v = sim.verdict_for(slider)
    rec = v.slider_record
    assert rec.head_hit and rec.tail_hit
    assert [p.hit for p in rec.ticks] == [True, False]
    assert rec.sliderbreaks == [2000.0]
    assert v.breaks == [2000.0]
    assert v.kind is JudgmentKind.HIT100     # 3/4
    # combo: circle 1, head 2, tick1 3 — RESET at tick2 → 0 — tail 1
    slider_ev = [e for e in sim.events if e.object_id == 1][0]
    assert slider_ev.combo_after == 1
    # ball detach visible: not tracking mid-gap, tracking again at the tail
    assert not v.tracked_at(1900) and v.tracked_at(2230)


def test_slider_tail_missed_is_lenient():
    """Only the tail missed: NO combo reset, no sliderbreak — result
    downgrades to 100 (3/4), combo keeps building through the slider."""
    bm, circle, slider = _slider_map()
    fr = _frames(_lead_click()
                 + [(1000, 256, 192, KEY_K1)]
                 + _ball_frames(slider, 1010, 2050)
                 + [(2060, 0, 0, KEY_K1)])   # gone before the tail judge time
    sim = StdRuleset(bm, fr).run()
    v = sim.verdict_for(slider)
    rec = v.slider_record
    assert rec.head_hit and all(p.hit for p in rec.ticks)
    assert not rec.tail_hit and rec.tail_missed_at == 2250
    assert rec.sliderbreaks == [] and v.breaks == []
    assert v.kind is JudgmentKind.HIT100     # 3/4
    # combo: circle 1, head 2, tick1 3, tick2 4, tail missed → stays 4
    slider_ev = [e for e in sim.events if e.object_id == 1][0]
    assert slider_ev.combo_after == 4
    # the three kinds produce distinct log lines
    lines = "\n".join(sim.report_lines())
    assert "tail_missed@2250ms" in lines and "sliderbreak" not in lines


def test_slider_all_missed_is_miss():
    bm, circle, slider = _slider_map()
    fr = _frames([(3000, 0, 0, KEY_K1)])
    sim = StdRuleset(bm, fr).run()
    assert sim.verdict_for(slider).kind is JudgmentKind.MISS


# --- lazer engine variants (auto-selected for lazer .osr, game_version ≥ 30M) -----------

def test_lazer_notelock_force_misses_previous():
    """StartTimeOrderedHitPolicy: hitting B after A's start time is allowed
    and force-misses A at that moment (no stable shake-cascade)."""
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "300,300,1100,1,0,0:0:0:0:\n")
    a, b = bm.hit_objects
    fr = _frames([(1050, 300, 300, KEY_K1), (1090, 300, 300, 0)])
    sim = StdRuleset(bm, fr, lazer=True).run()
    va, vb = sim.verdict_for(a), sim.verdict_for(b)
    assert vb.kind is JudgmentKind.HIT100 and vb.hit_time == 1050  # −50 → Ok
    assert va.kind is JudgmentKind.MISS
    assert va.deadline == 1050              # force-missed AT the B hit
    assert sim.shakes == 0
    # the same click under stable rules shakes instead
    stable = StdRuleset(bm, fr, lazer=False).run()
    assert stable.verdict_for(b).kind is JudgmentKind.MISS
    assert stable.shakes == 1


def test_lazer_blocks_before_previous_start():
    """Before the previous object's start time the hit is still blocked."""
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "300,300,1100,1,0,0:0:0:0:\n")
    fr = _frames([(990, 300, 300, KEY_K1), (1020, 300, 300, 0)])
    sim = StdRuleset(bm, fr, lazer=True).run()
    assert sim.verdict_for(bm.hit_objects[1]).kind is JudgmentKind.MISS
    assert sim.shakes == 1


def test_lazer_slider_head_is_timing_judged():
    """lazer: the slider's counted judgment is the head's timing tier —
    a +50ms head with perfect tracking is a 100 (stable would call the
    aggregate 300); a missed head is a MISS count."""
    bm, circle, slider = _slider_map()
    fr = _frames(_lead_click()
                 + [(1050, 256, 192, KEY_K1)]
                 + _ball_frames(slider, 1060, 2250))
    sim = StdRuleset(bm, fr, lazer=True).run()
    v = sim.verdict_for(slider)
    assert v.kind is JudgmentKind.HIT100 and v.delta == 50
    assert all(p.hit for p in v.parts[1:])       # tracking still full
    # popup at the head, at the click moment (lazer convention)
    ev = [e for e in sim.events if e.object_id == 1][0]
    assert ev.time_ms == 1050 and (ev.x, ev.y) == v.pos

    missed = StdRuleset(bm, _frames([(900, 0, 0, KEY_K1)]
                                    + _ball_frames(slider, 1005, 2250)),
                        lazer=True).run()
    assert missed.verdict_for(slider).kind is JudgmentKind.MISS


# --- spinner (simplified model) --------------------------------------------------------

def test_spinner_spin_vs_no_spin():
    # OD7 → SpinnerRatio 6.0; 1s spinner needs 6 rotations
    bm = _map("256,192,1000,12,0,2000,0:0:0:0:\n")
    spins = []
    for i in range(101):
        ang = 2.0 * math.pi * 8.0 * i / 100.0
        spins.append((1000 + i * 10, 256 + 100 * math.cos(ang),
                      192 + 100 * math.sin(ang), KEY_K1))
    sim = StdRuleset(bm, _frames(spins)).run()
    assert sim.verdict_for(bm.hit_objects[0]).kind is JudgmentKind.HIT300

    idle = StdRuleset(bm, _frames([(1500, 256, 100, KEY_K1)])).run()
    assert idle.verdict_for(bm.hit_objects[0]).kind is JudgmentKind.MISS


# --- reconcile snap ----------------------------------------------------------------------

def test_reconcile_snaps_to_replay_counts():
    """Sim says 3×300 but the replay recorded 2/1/0/0 → the least-confident
    (largest-delta) hit is relabeled to 100; totals match exactly."""
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "100,100,2000,1,0,0:0:0:0:\n"
              "100,100,3000,1,0,0:0:0:0:\n")
    fr = _frames([
        (1010, 100, 100, KEY_K1), (1040, 100, 100, 0),
        (2020, 100, 100, KEY_K1), (2060, 100, 100, 0),
        (3030, 100, 100, KEY_K1), (3070, 100, 100, 0),
    ])
    sim = StdRuleset(bm, fr, _meta(2, 1, 0, 0, max_combo=3)).run()
    assert sim.sim_counts == (3, 0, 0, 0)          # the honesty metric
    assert sim.final_counts == (2, 1, 0, 0)        # snapped to the replay
    assert sim.relabeled == 1
    kinds = [sim.verdict_for(o).kind for o in bm.hit_objects]
    assert kinds == [JudgmentKind.HIT300, JudgmentKind.HIT300,
                     JudgmentKind.HIT100]          # worst delta (30ms) flipped


def test_reconcile_promotes_phantom_hit():
    """Sim missed a circle the replay says was hit → phantom 300 on time."""
    bm = _map("100,100,1000,1,0,0:0:0:0:\n")
    sim = StdRuleset(bm, _frames([(2000, 400, 300, 0)]),
                     _meta(1, 0, 0, 0, max_combo=1)).run()
    assert sim.sim_counts == (0, 0, 0, 1)
    assert sim.final_counts == (1, 0, 0, 0)
    v = sim.verdict_for(bm.hit_objects[0])
    assert v.kind is JudgmentKind.HIT300
    assert v.hit_time == 1000 and v.delta == 0.0   # mania's 0ms-offset trick


def test_reconcile_noop_when_counts_match():
    bm = _map("100,100,1000,1,0,0:0:0:0:\n"
              "100,100,2000,1,0,0:0:0:0:\n")
    fr = _frames([
        (1010, 100, 100, KEY_K1), (1040, 100, 100, 0),
        (2100, 100, 100, KEY_K1), (2140, 100, 100, 0),
    ])
    sim = StdRuleset(bm, fr, _meta(1, 0, 1, 0, max_combo=2)).run()
    assert sim.sim_counts == (1, 0, 1, 0)
    assert sim.final_counts == (1, 0, 1, 0)
    assert sim.relabeled == 0


# --- engine auto-selection from the .osr game_version (LegacyHitPolicy gate) -----------

def test_engine_autoselected_from_game_version():
    """The stable LegacyHitPolicy path vs lazer StartTimeOrderedHitPolicy is
    picked from the replay's game_version (< LAZER_GAME_VERSION == stable).
    Guards the version gate so an old .osr never silently runs the lazer
    engine (and vice-versa)."""
    from types import SimpleNamespace
    bm = _map("256,192,1000,1,0,0:0:0:0:\n")
    assert StdRuleset.LAZER_GAME_VERSION == 30_000_000
    # a real stable .osr (e.g. 20191107) → stable
    stable = StdRuleset(bm, [], SimpleNamespace(game_version=20191107))
    assert stable.lazer is False
    # just under the threshold → still stable
    assert StdRuleset(bm, [], SimpleNamespace(game_version=29999999)).lazer is False
    # a lazer .osr (30000016) → lazer
    lazer = StdRuleset(bm, [], SimpleNamespace(game_version=30000016))
    assert lazer.lazer is True
    # at the threshold → lazer
    assert StdRuleset(bm, [], SimpleNamespace(game_version=30_000_000)).lazer is True
    # no meta at all → default stable
    assert StdRuleset(bm, []).lazer is False
    # explicit override always wins over the version heuristic
    assert StdRuleset(bm, [], SimpleNamespace(game_version=20191107),
                      lazer=True).lazer is True
    assert StdRuleset(bm, [], SimpleNamespace(game_version=30000016),
                      lazer=False).lazer is False


# --- lazer slider-part (tick/tail) combo reconcile -------------------------------------
# The note reconcile only snaps 300/100/50/miss (circles + slider HEADS); a
# lazer .osr's ScoreInfo additionally carries the slider tick/tail judgement
# counts (LargeTick / SliderTail), which drive combo INDEPENDENTLY. These tests
# prove the reconcile snaps those counts exactly and lands the headline max
# combo on the replay's real value — and that a stable replay is untouched.

_MULTI_SL = "256,192,{st},2,0,L|506:192,1,250,0|0,0:0|0:0,0:0:0:0:"


def _multi_slider(n: int, gap: int = 3000):
    """n back-to-back L sliders (each = head + 2 ticks + tail = 4 combo)."""
    return _map("\n".join(_MULTI_SL.format(st=1000 + i * gap)
                           for i in range(n)) + "\n")


def _perfect_slider_frames(bm):
    """Click every head (a fresh press edge, key released between sliders) and
    track the ball perfectly → every tick/tail hit at baseline."""
    ents = []
    for obj in bm.hit_objects:
        st, en = obj.get_start_time(), obj.get_end_time()
        ents.append((st - 80, 256, 192, 0))          # release → new press edge
        ents.append((st, 256, 192, KEY_K1))          # head click
        t = st + 5
        while t <= en:
            x, y = obj.position_at(t)
            ents.append((t, x, y, KEY_K1))
            t += 10
    return _frames(sorted(ents, key=lambda e: e[0]))


def _lazer_stats_meta(n, *, large_tick_hit, large_tick_miss, slider_tail_hit,
                      max_large_tick, max_slider_tail, max_combo):
    from osu_std_renderer.replay.replay import ReplayMeta
    from osu_std_renderer.replay.lazer_mods import LazerStatistics
    return ReplayMeta(
        mode=0, beatmap_md5="", player_name="t", mods=0, score=0,
        max_combo=max_combo, count_300=n, count_100=0, count_50=0,
        count_geki=0, count_katu=0, count_miss=0, accuracy=0.0, grade="A",
        game_version=30_000_017,
        lazer_statistics=LazerStatistics(
            large_tick_hit=large_tick_hit, large_tick_miss=large_tick_miss,
            slider_tail_hit=slider_tail_hit, max_large_tick=max_large_tick,
            max_slider_tail=max_slider_tail))


def test_lazer_statistics_from_info_parse():
    from osu_std_renderer.replay.lazer_mods import (
        LazerStatistics, lazer_statistics_from_info)
    info = {
        "statistics": {"great": 1004, "ok": 118, "meh": 8, "miss": 39,
                       "large_tick_hit": 69, "large_tick_miss": 22,
                       "slider_tail_hit": 147, "ignore_hit": 264},
        "maximum_statistics": {"great": 1169, "large_tick_hit": 91,
                               "slider_tail_hit": 273, "ignore_hit": 273}}
    assert lazer_statistics_from_info(info) == LazerStatistics(
        large_tick_hit=69, large_tick_miss=22, slider_tail_hit=147,
        max_large_tick=91, max_slider_tail=273)
    # None cases: not a dict / missing dicts / a slider-less map (no large
    # ticks and no tails in maximum_statistics → nothing to reconcile).
    assert lazer_statistics_from_info(None) is None
    assert lazer_statistics_from_info({"statistics": {}}) is None
    assert lazer_statistics_from_info(
        {"statistics": {"great": 5}, "maximum_statistics": {"great": 5}}
    ) is None


def test_lazer_slider_parts_reconcile_hits_target_counts_and_max_combo():
    """8 perfect sliders (baseline combo 32). Feed ScoreInfo stats of 2
    LargeTickMisses + 5 SliderTailHits (of 8) and a real max combo of 24: the
    sim must snap the tick/tail COUNTS exactly, break combo ONLY on the tick
    misses (tail misses are IgnoreMiss = no break), and land final_max_combo
    on 24."""
    n = 8
    bm = _multi_slider(n)
    fr = _perfect_slider_frames(bm)

    base = StdRuleset(bm, fr).run()               # perfect FC baseline
    assert base.sim_max_combo == 32
    parts = [p for o in bm.hit_objects for p in base.verdict_for(o).parts]
    assert sum(1 for p in parts if p.kind in ("tick", "repeat")) == 16
    assert sum(1 for p in parts if p.kind == "tail") == 8

    meta = _lazer_stats_meta(
        n, large_tick_hit=14, large_tick_miss=2, slider_tail_hit=5,
        max_large_tick=16, max_slider_tail=8, max_combo=24)
    sim = StdRuleset(bm, fr, meta).run()

    vp = [(o, p) for o in bm.hit_objects for p in sim.verdict_for(o).parts]
    tick_miss = sum(1 for _o, p in vp
                    if p.kind in ("tick", "repeat") and not p.hit)
    tail_hit = sum(1 for _o, p in vp if p.kind == "tail" and p.hit)
    tail_miss = sum(1 for _o, p in vp if p.kind == "tail" and not p.hit)
    # counts snapped EXACTLY to the ScoreInfo statistics
    assert (tick_miss, tail_hit, tail_miss) == (2, 5, 3)
    # the ONLY combo-break slider parts are tick/repeat misses (LargeTickMiss
    # BreaksCombo); the 3 tail misses are IgnoreMiss → they never break combo,
    # so the sliderbreak tally == the tick-miss count, NOT tick+tail.
    assert sum(len(sim.verdict_for(o).breaks) for o in bm.hit_objects) == 2
    # the position pass lands the HEADLINE max combo on the replay's real value
    assert sim.final_max_combo == 24 == sim.real_max_combo
    # note tiers untouched (every head hit → 8×300)
    assert sim.final_counts == (8, 0, 0, 0)
    assert any("reconciled to ScoreInfo" in ln for ln in sim.report_lines())


def test_stable_replay_slider_parts_untouched():
    """A stable replay carries no ScoreInfo: the tick/tail reconcile never runs
    (self.lazer False gates it), so the cursor-tracking guess stands — every
    perfectly-tracked part stays a hit and no reconcile line is emitted."""
    n = 4
    bm = _multi_slider(n)
    fr = _perfect_slider_frames(bm)
    sim = StdRuleset(bm, fr, _meta(n, 0, 0, 0, max_combo=16)).run()
    assert sim.lazer is False
    # the reconcile is never invoked → its honesty line is absent from the log
    assert not any("reconciled to ScoreInfo" in ln for ln in sim.report_lines())
    # perfect tracking → every tick/tail hit, unmodified by any reconcile
    assert all(p.hit for o in bm.hit_objects
               for p in sim.verdict_for(o).parts)


# --- general max-combo position pass (all engines) ------------------------------------
# reconcile_to_counts snaps the 300/100/50/miss COUNTS exactly but scatters the
# resulting combo-breaks by cursor-quality rank, so the longest run (the shown
# max combo) can drift from — or break — the replay's real max combo. The general
# position pass relocates object-level (circle/spinner, lazer slider-head) misses,
# count-preserving, until the longest run equals the real value, on BOTH engines.

def _circle_map(n: int, spacing: int = 500, start: int = 1000) -> object:
    """n circles, spacing ms apart (>> the meh window, so a skipped circle
    misses cleanly without notelocking its neighbour)."""
    return _map("\n".join(f"256,192,{start + i * spacing},1,0,0:0:0:0:"
                          for i in range(n)) + "\n")


def _click_circles(bm, skip=(), late=None):
    """Click every circle on time (release→press edge each) except `skip`
    (left to miss); `late` = {idx: +ms} nudges a click's timing (worse quality,
    still in-window)."""
    late = late or {}
    ents = []
    for i, o in enumerate(bm.hit_objects):
        if i in skip:
            continue
        st = o.get_start_time()
        ents.append((st - 80, 256, 192, 0))
        ents.append((st + late.get(i, 0), 256, 192, KEY_K1))
    return _frames(sorted(ents, key=lambda e: e[0]))


def _lazer_meta_nostats(c300, c100, c50, miss, max_combo):
    """A lazer .osr (game_version ≥ threshold) that carries NO ScoreInfo
    slider-part stats — the reconcile_slider_parts path is skipped, so only the
    general max-combo pass shapes combo."""
    from osu_std_renderer.replay.replay import ReplayMeta
    return ReplayMeta(
        mode=0, beatmap_md5="", player_name="t", mods=0, score=0,
        max_combo=max_combo, count_300=c300, count_100=c100, count_50=c50,
        count_geki=0, count_katu=0, count_miss=miss, accuracy=0.0, grade="A",
        game_version=30_000_017, lazer_statistics=None)


def test_maxcombo_pass_lands_stable_play_on_real_value():
    """12 perfectly-clicked circles (raw = 12×300, raw combo 12). The replay
    records 10/0/0/2 with a real max combo of 6: reconcile alone would drop the
    two misses on the latest (worst-ranked) circles → a run of 10, but the
    general pass relocates them so the longest run is EXACTLY 6, counts intact."""
    bm = _circle_map(12)
    fr = _click_circles(bm)
    sim = StdRuleset(bm, fr, _meta(10, 0, 0, 2, max_combo=6)).run()
    assert sim.lazer is False
    assert sim.sim_counts == (12, 0, 0, 0)          # honesty: raw saw a full FC
    assert sim.sim_max_combo == 12
    assert sim.final_counts == (10, 0, 0, 2)        # counts stay exact
    assert sim.final_max_combo == 6 == sim.real_max_combo
    assert any("max-combo position pass" in ln for ln in sim.report_lines())


def test_maxcombo_pass_lands_lazer_no_stats_play_on_real_value():
    """Same play on the LAZER engine with NO ScoreInfo (reconcile_slider_parts
    can't run) — the general pass still lands the max combo on the real value."""
    bm = _circle_map(12)
    fr = _click_circles(bm)
    sim = StdRuleset(bm, fr, _lazer_meta_nostats(10, 0, 0, 2, max_combo=6)).run()
    assert sim.lazer is True
    assert not any("reconciled to ScoreInfo" in ln for ln in sim.report_lines())
    assert sim.final_counts == (10, 0, 0, 2)
    assert sim.final_max_combo == 6 == sim.real_max_combo


def test_maxcombo_pass_never_breaks_an_already_correct_combo():
    """Guard for the hard invariant: when the RAW sim's max combo already equals
    the replay's real max combo, the reconcile must NOT make it worse. Here the
    raw sim misses circle #3 (raw combo 8 = the real value), but the replay
    records a SECOND miss (10/0/0/2). Naive count-reconcile drops that miss on
    the worst-ranked mid-run circle (#8), splitting the run down to 4 — the exact
    regression this pass exists to prevent. The pass must restore the longest run
    to 8, never leaving the already-correct combo broken."""
    bm = _circle_map(12)
    fr = _click_circles(bm, skip={3}, late={8: 25})
    sim = StdRuleset(bm, fr, _meta(10, 0, 0, 2, max_combo=8)).run()
    assert sim.sim_counts == (11, 0, 0, 1)          # raw: one genuine miss (#3)
    assert sim.sim_max_combo == 8 == sim.real_max_combo   # raw combo already right
    assert sim.final_counts == (10, 0, 0, 2)        # counts snapped exactly
    # the invariant: an already-correct raw combo is NOT broken by reconcile
    assert sim.final_max_combo == 8 == sim.real_max_combo
