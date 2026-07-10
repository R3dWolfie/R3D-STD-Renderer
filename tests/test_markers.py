"""Reverse arrows / slider ticks / follow points — schedule, orientation,
visibility windows, eligibility and spacing (render/markers.py; plus the
two-mode skin cursor trail helpers from render/scene.py). Pure CPU."""
from __future__ import annotations

import math

from osu_std_renderer.beatmap.objects.circle import Circle
from osu_std_renderer.beatmap.objects.slider import Slider
from osu_std_renderer.beatmap.objects.spinner import Spinner
from osu_std_renderer.beatmap.objects.timing import Timings
from osu_std_renderer.render.markers import (
    ARROW_EXPLODE_MS, ARROW_EXPLODE_SCALE, ARROW_FADE_MS,
    ARROW_PULSE_ROT_RAD, FP_PREEMPT, FP_PREEMPT_MIN, FP_SPACING,
    TICK_FADE_MS, TICK_POP_MS,
    TICK_POP_SCALE, arrow_alpha_scale, arrow_pulse, arrow_rotation,
    beat_phase, followpoint_dots, followpoint_eligible, followpoint_state,
    reverse_arrow_schedule, tick_alpha_scale, tick_schedule,
)
from osu_std_renderer.render.scene import (
    TRAIL_SPRITE_INTERVAL_MS, TRAIL_SPRITE_LIFE_MS, build_distance_trail,
    long_trail_points, skin_trail_is_long, sparse_trail_times,
)
from osu_std_renderer.replay.replay import StdFrame


# --- reverse arrows -----------------------------------------------------------------

def test_reverse_schedule_end_parity():
    # start 1000, partLen 500, 4 spans → arrows r=1..3
    arrows = reverse_arrow_schedule(1000.0, 500.0, 4, spawn=400.0)
    assert [a.r for a in arrows] == [1, 2, 3]
    assert [a.time for a in arrows] == [1500.0, 2000.0, 2500.0]
    # r odd → TAIL (forward span arriving), r even → HEAD
    assert [a.at_tail for a in arrows] == [True, False, True]
    # without a head_hit_time the first two appear with the slider;
    # r=3 appears when r=1 is consumed
    assert arrows[0].appear == 400.0 and arrows[1].appear == 400.0
    assert arrows[2].appear == 1500.0
    # no repeats / degenerate → no arrows
    assert reverse_arrow_schedule(1000.0, 500.0, 1, 400.0) == []
    assert reverse_arrow_schedule(1000.0, 0.0, 4, 400.0) == []


def test_multireverse_arrows_wait_for_the_head_hit():
    """OWNER SPEC: a slider reversing MORE THAN ONCE keeps its arrows
    hidden during the approach — they appear at the head-hit moment
    (lazer would fade the span-0 repeat in during the preempt; the
    owner's behaviour deliberately wins — markers.py docstring)."""
    arrows = reverse_arrow_schedule(1000.0, 500.0, 4, spawn=400.0,
                                    head_hit_time=1012.0)
    assert arrows[0].appear == 1012.0 and arrows[1].appear == 1012.0
    assert arrows[2].appear == 1500.0            # r>2 unchanged
    # hidden during the whole approach, quick-ramping after the hit
    a1 = arrows[0]
    assert arrow_alpha_scale(999.0, a1, 400.0, 400.0) is None
    assert arrow_alpha_scale(1011.0, a1, 400.0, 400.0) is None
    al, sc = arrow_alpha_scale(1012.0 + ARROW_FADE_MS / 2, a1, 400.0, 400.0)
    assert abs(al - 0.5) < 1e-9 and sc == 1.0
    al, _ = arrow_alpha_scale(1012.0 + ARROW_FADE_MS, a1, 400.0, 400.0)
    assert al == 1.0
    # a SINGLE-reverse slider keeps the classic appear-at-spawn rule
    single = reverse_arrow_schedule(1000.0, 500.0, 2, spawn=400.0,
                                    head_hit_time=1012.0)
    assert len(single) == 1 and single[0].appear == 400.0
    # a missed head passes its window close as head_hit_time — the
    # arrows appear when the head resolves
    missed = reverse_arrow_schedule(1000.0, 500.0, 3, spawn=400.0,
                                    head_hit_time=1199.5)
    assert missed[0].appear == 1199.5


def test_arrow_rotation_points_inward():
    path = [(0.0, 0.0), (100.0, 0.0)]
    # tail arrow points back along the path (toward -x)
    assert abs(abs(arrow_rotation(path, at_tail=True)) - math.pi) < 1e-9
    # head arrow points into the path (toward +x)
    assert abs(arrow_rotation(path, at_tail=False)) < 1e-9
    # duplicate end vertices are skipped
    dup = [(0.0, 0.0), (100.0, 0.0), (100.0, 0.0)]
    assert abs(abs(arrow_rotation(dup, at_tail=True)) - math.pi) < 1e-9
    # vertical path, screen y-down: tail at (0,50) → inward is -y
    vert = [(0.0, 0.0), (0.0, 50.0)]
    assert abs(arrow_rotation(vert, at_tail=True) + math.pi / 2) < 1e-9
    # diagonal path: the arrow angle equals the path tangent at the repeat
    # (DrawableSliderRepeat: rotation toward the next distinct curve point).
    diag = [(0.0, 0.0), (30.0, 40.0)]
    # tail arrow points back toward the head → tangent of (head-tail)
    assert abs(arrow_rotation(diag, at_tail=True)
               - math.atan2(-40.0, -30.0)) < 1e-9
    # head arrow points along the path toward the tail → tangent of (tail-head)
    assert abs(arrow_rotation(diag, at_tail=False)
               - math.atan2(40.0, 30.0)) < 1e-9


def test_arrow_pulse_version_gate():
    # v2+: scale pulse 1.3 → 1.0, no rotation
    s, r = arrow_pulse(0.0, legacy=False)
    assert abs(s - 1.3) < 1e-9 and r == 0.0
    s, r = arrow_pulse(1.0, legacy=False)
    assert abs(s - 1.0) < 1e-9
    s_mid, _ = arrow_pulse(0.5, legacy=False)
    assert 1.0 < s_mid < 1.3
    # v1: ±6° wobble, no scale pulse
    s, r = arrow_pulse(0.25, legacy=True)
    assert s == 1.0 and abs(r - ARROW_PULSE_ROT_RAD) < 1e-9
    _, r = arrow_pulse(0.75, legacy=True)
    assert abs(r + ARROW_PULSE_ROT_RAD) < 1e-9
    _, r = arrow_pulse(0.0, legacy=True)
    assert abs(r) < 1e-9


def test_beat_phase_from_red_lines():
    tm = Timings()
    tm.add_point(1000.0, 500.0, 1, 1, 1.0, 4, inherited=False, kiai=False)
    tm.finalize_points()
    assert abs(beat_phase(1000.0, tm)) < 1e-9
    assert abs(beat_phase(1250.0, tm) - 0.5) < 1e-9
    assert abs(beat_phase(2000.0, tm)) < 1e-9


def test_arrow_lifecycle_windows():
    from osu_std_renderer.render.markers import ReverseArrow
    spawn, fin = 400.0, 400.0
    a1 = ReverseArrow(r=1, time=1500.0, appear=spawn, at_tail=True)
    assert arrow_alpha_scale(spawn - 1, a1, spawn, fin) is None
    al, sc = arrow_alpha_scale(spawn + 200.0, a1, spawn, fin)
    assert abs(al - 0.5) < 1e-9 and sc == 1.0     # rides the slider fade-in
    al, sc = arrow_alpha_scale(1500.0, a1, spawn, fin)
    assert al == 1.0
    # consumed + HIT → explosion pop
    al, sc = arrow_alpha_scale(1500.0 + ARROW_EXPLODE_MS / 2, a1, spawn, fin)
    assert abs(al - 0.5) < 1e-9
    assert abs(sc - (1.0 + (ARROW_EXPLODE_SCALE - 1.0) / 2)) < 1e-9
    assert arrow_alpha_scale(1500.0 + ARROW_EXPLODE_MS, a1, spawn, fin) is None
    # consumed + MISSED → vanishes instantly
    assert arrow_alpha_scale(1501.0, a1, spawn, fin, hit=False) is None
    # a mid-slide arrow (r=3) uses the quick fade from its appear time
    a3 = ReverseArrow(r=3, time=2500.0, appear=1500.0, at_tail=True)
    al, _ = arrow_alpha_scale(1500.0 + ARROW_FADE_MS / 2, a3, spawn, fin)
    assert abs(al - 0.5) < 1e-9


# --- slider ticks --------------------------------------------------------------------

def _repeat_slider() -> Slider:
    # 100px linear slider, 2 spans; beatLen 500, mult 1.0 → velocity
    # 0.2 px/ms → span 500 ms; tickRate 2 → tickDistance 50 → ONE tick per
    # span at path progress 0.5
    s = Slider.parse(["0", "0", "1000", "2", "0", "L|100:0", "2", "100",
                      "", ""])
    assert s is not None
    tm = Timings(slider_mult=1.0, tick_rate=2.0)
    tm.add_point(0.0, 500.0, 1, 1, 1.0, 4, inherited=False, kiai=False)
    tm.finalize_points()
    s.set_timing(tm, version=14)
    return s


def test_tick_schedule_times_and_progress():
    s = _repeat_slider()
    marks = tick_schedule(s)
    assert len(marks) == 2
    m0, m1 = marks
    assert m0.span == 0 and abs(m0.time - 1250.0) < 1e-6
    assert m1.span == 1 and abs(m1.time - 1750.0) < 1e-6
    # both sit at path progress 0.5 → position (50, 0)
    assert abs(m0.progress - 0.5) < 1e-6 and abs(m1.progress - 0.5) < 1e-6
    assert abs(m0.pos[0] - 50.0) < 1e-6 and abs(m1.pos[0] - 50.0) < 1e-6
    # reverse marker exists for span 0 only (2 spans → 1 arrow)
    assert len(s.tick_reverse) == 1
    assert abs(s.tick_reverse[0].time - 1500.0) < 1e-6
    assert s.tick_reverse[0].pos == (100.0, 0.0)   # tail end


def test_tick_visibility_windows():
    spawn, fin = 400.0, 400.0
    # span 0: rides the slider fade-in from spawn
    assert tick_alpha_scale(spawn - 1, 1250.0, 0, 1000.0, spawn, fin) is None
    al, sc = tick_alpha_scale(spawn + 200.0, 1250.0, 0, 1000.0, spawn, fin)
    assert abs(al - 0.5) < 1e-9 and sc == 1.0
    # span 1: appears at ITS span start with the quick ramp
    assert tick_alpha_scale(1499.0, 1750.0, 1, 1500.0, spawn, fin) is None
    al, _ = tick_alpha_scale(1500.0 + TICK_FADE_MS / 2, 1750.0, 1, 1500.0,
                             spawn, fin)
    assert abs(al - 0.5) < 1e-9
    # consumed + HIT → tiny pop, then gone
    al, sc = tick_alpha_scale(1750.0 + TICK_POP_MS / 2, 1750.0, 1, 1500.0,
                              spawn, fin)
    assert abs(al - 0.5) < 1e-9
    assert abs(sc - (1.0 + (TICK_POP_SCALE - 1.0) / 2)) < 1e-9
    assert tick_alpha_scale(1750.0 + TICK_POP_MS, 1750.0, 1, 1500.0,
                            spawn, fin) is None
    # consumed + MISSED → vanishes, no pop
    assert tick_alpha_scale(1751.0, 1750.0, 1, 1500.0, spawn, fin,
                            hit=False) is None


# --- follow points -------------------------------------------------------------------

def test_followpoint_eligibility_same_combo_only():
    a = Circle.parse(["0", "0", "1000", "1", "0"])
    b = Circle.parse(["100", "0", "1500", "1", "0"])
    nc = Circle.parse(["100", "0", "1500", "5", "0"])      # new-combo bit
    sp = Spinner.parse(["256", "192", "1500", "8", "0", "2000"])
    assert followpoint_eligible(a, b)
    assert not followpoint_eligible(a, nc)                  # new combo
    assert not followpoint_eligible(sp, b)                  # from spinner
    assert not followpoint_eligible(a, sp)                  # to spinner


def test_followpoint_spacing_and_schedule():
    # AR <= 10 origin (preempt_u >= 450) -> factor 1 -> flat 800 preempt
    dots = followpoint_dots((0.0, 0.0), 1000.0, (200.0, 0.0), 2000.0, 600.0)
    # d = 48, 80, 112, 144 (while d < 200-32)
    assert len(dots) == 4
    fractions = [48 / 200, 80 / 200, 112 / 200, 144 / 200]
    for dot, f in zip(dots, fractions):
        assert abs(dot.x_end - f * 200.0) < 1e-9
        assert abs(dot.x_start - (f - 0.1) * 200.0) < 1e-9
        assert abs(dot.fade_out - (1000.0 + f * 1000.0)) < 1e-9
        assert abs(dot.fade_in - (dot.fade_out - FP_PREEMPT)) < 1e-9
        assert dot.rotation == 0.0
    # all dots retire by (or fading right at) the next object's start
    assert max(d.fade_out for d in dots) < 2000.0
    # too close / no gap in time → no dots
    assert followpoint_dots((0.0, 0.0), 1000.0, (70.0, 0.0), 2000.0, 600.0) == []
    assert followpoint_dots((0.0, 0.0), 1000.0, (200.0, 0.0), 1000.0, 600.0) == []


def test_followpoint_preempt_scaling():
    """Lazer FollowPointConnection.GetFadeTimes scales the connection
    preempt by min(1, start.TimePreempt / PREEMPT_MIN); PREEMPT_MIN=450."""
    assert FP_PREEMPT == 800.0
    assert FP_PREEMPT_MIN == 450.0
    # exactly at PREEMPT_MIN (AR 10) and above: factor 1 -> flat 800, unchanged
    for pre in (FP_PREEMPT_MIN, 540.0, 1200.0):
        dots = followpoint_dots((0.0, 0.0), 1000.0, (200.0, 0.0), 2000.0, pre)
        for dot in dots:
            assert abs(dot.fade_in - (dot.fade_out - FP_PREEMPT)) < 1e-9
    # low preempt (TimePreempt=300, i.e. AR11 via DA extended): shortened preempt
    dots = followpoint_dots((0.0, 0.0), 1000.0, (200.0, 0.0), 2000.0, 300.0)
    assert len(dots) == 4
    expect = FP_PREEMPT * (300.0 / FP_PREEMPT_MIN)          # 800 * 2/3 = 533.33
    for dot in dots:
        assert abs(dot.fade_in - (dot.fade_out - expect)) < 1e-9
        # strictly LATER (larger fade_in) than the flat-800 schedule
        assert dot.fade_in > dot.fade_out - FP_PREEMPT
    # the fade_out schedule itself is untouched by the preempt scaling
    flat = followpoint_dots((0.0, 0.0), 1000.0, (200.0, 0.0), 2000.0, 600.0)
    for d_lo, d_hi in zip(dots, flat):
        assert abs(d_lo.fade_out - d_hi.fade_out) < 1e-9


def test_followpoint_state_lifecycle():
    dots = followpoint_dots((0.0, 0.0), 1000.0, (200.0, 0.0), 2000.0, 600.0)
    dot = dots[0]
    fin = 400.0
    assert followpoint_state(dot.fade_in - 1, dot, fin) is None
    a, x, _, sc = followpoint_state(dot.fade_in + fin / 2, dot, fin)
    assert abs(a - 0.5) < 1e-9
    assert dot.x_start < x <= dot.x_end          # easing toward the dot pos
    assert 1.0 <= sc <= 1.5
    a, x, _, sc = followpoint_state(dot.fade_in + fin, dot, fin)
    assert a == 1.0 and abs(x - dot.x_end) < 1e-9 and abs(sc - 1.0) < 1e-9
    # fading back out from fade_out
    a, *_ = followpoint_state(dot.fade_out + fin / 2, dot, fin)
    assert abs(a - 0.5) < 1e-9
    assert followpoint_state(dot.fade_out + fin, dot, fin) is None


# --- skin cursor trail (two-mode rule) -------------------------------------------------

def test_trail_mode_selection():
    assert skin_trail_is_long(True, False)        # cursormiddle → long
    assert skin_trail_is_long(False, True)        # ForceLongTrail → long
    assert skin_trail_is_long(True, True)
    assert not skin_trail_is_long(False, False)   # sparse 16.67 ms trail


def test_sparse_trail_spacing():
    t = 1000.0
    drops = sparse_trail_times(t)
    # 16.67 ms grid, newest at/below t, fading over 150 ms → ~9 sprites
    # (float boundary at the tail may add/remove one)
    assert 8 <= len(drops) <= 10
    times = [d[0] for d in drops]
    assert all(times[i] < times[i + 1] for i in range(len(times) - 1))
    assert abs(times[-1] - math.floor(t / TRAIL_SPRITE_INTERVAL_MS)
               * TRAIL_SPRITE_INTERVAL_MS) < 1e-9
    assert t - times[-1] < TRAIL_SPRITE_INTERVAL_MS + 1e-9
    for ti, strength in drops:
        assert abs(strength - (1.0 - (t - ti) / TRAIL_SPRITE_LIFE_MS)) < 1e-9
        assert 0.0 < strength <= 1.0
    # consecutive drops are exactly one interval apart
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    assert all(abs(g - TRAIL_SPRITE_INTERVAL_MS) < 1e-9 for g in gaps)
    # drops sit on the ABSOLUTE grid — a slightly later frame reuses the
    # same drop times (nothing slides)
    again = [d[0] for d in sparse_trail_times(t + 3.0)]
    for ti in times[1:]:
        assert any(abs(ti - tj) < 1e-6 for tj in again)


def test_distance_trail_uniform_spacing_at_any_speed():
    """OWNER SPEC: points strictly every N osu!px of cursor TRAVEL —
    interpolated INSIDE replay-frame segments, so a fast flick (huge
    px-per-frame) yields the same spacing as slow movement (a continuous
    ribbon, never separated dots)."""
    frames = [StdFrame(0, 0.0, 0.0, 0)]
    # slow: 1 px/ms over 100 ms (10 px per frame)
    frames += [StdFrame(t, float(t), 0.0, 0) for t in range(10, 101, 10)]
    # fast flick: 200 px inside ONE 10 ms frame gap (20 px/ms)
    frames.append(StdFrame(110, 300.0, 0.0, 0))
    trail = build_distance_trail(frames, spacing_osu=5.0)
    xs = [p[0] for p in trail]
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    assert gaps and all(abs(g - 5.0) < 1e-6 for g in gaps)
    # 300 px of travel → 60 points, times strictly increasing
    assert len(trail) == 60
    ts = [p[2] for p in trail]
    assert all(ts[i] < ts[i + 1] for i in range(len(ts) - 1))
    # flick points carry INTERPOLATED pass times inside (100, 110]
    flick = [p for p in trail if p[0] > 100.0]
    assert flick and all(100.0 < p[2] <= 110.0 for p in flick)


def test_distance_trail_stationary_cursor_emits_nothing():
    """No movement → no points: a parked cursor can never stack trail
    sprites in place (the old brightness pile-up)."""
    frames = [StdFrame(t, 100.0, 100.0, 0) for t in range(0, 200, 10)]
    assert build_distance_trail(frames, spacing_osu=2.0) == []
    assert build_distance_trail([], spacing_osu=2.0) == []


def test_long_trail_window_uniform_alpha_and_cap():
    frames = [StdFrame(0, 0.0, 0.0, 0), StdFrame(100, 100.0, 0.0, 0)]
    trail = build_distance_trail(frames, spacing_osu=10.0)
    times = [p[2] for p in trail]
    pts = long_trail_points(trail, times, 100.0, window_ms=50.0,
                            max_points=2048)
    # only points passed in the last 50 ms; oldest→newest
    assert pts and all(100.0 - 50.0 <= 100.0 - (1.0 - s) * 50.0
                       for _, _, s in pts)
    strengths = [s for _, _, s in pts]
    assert all(strengths[i] < strengths[i + 1]
               for i in range(len(strengths) - 1))
    # strength is EXACTLY the age fade — the only alpha factor (uniform
    # base brightness along the ribbon)
    for (x, _, s), ti in zip(pts, times[-len(pts):]):
        assert abs(s - (1.0 - (100.0 - ti) / 50.0)) < 1e-9
    # newest-N cap
    capped = long_trail_points(trail, times, 100.0, window_ms=50.0,
                               max_points=3)
    assert len(capped) == 3
    assert [p[0] for p in capped] == [p[0] for p in pts[-3:]]
