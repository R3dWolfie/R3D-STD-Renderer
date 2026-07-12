"""Replay Smoke (key bit 16) — CPU tests of the pure lifecycle math in
render/effects.py (smoke_segments / smoke_point_alpha), mirroring how
test_markers.py / test_scene.py exercise the other effect helpers directly.

The GPU path (SmokeSegment -> Sprite additive dabs) is covered by the scene
GPU smoke test pattern in test_scene.py (skips gracefully without EGL); these
tests are the always-runnable ground truth for placement + fade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from osu_std_renderer.render.effects import (
    SMOKE_DEFAULT_WIDTH_OSU,
    SMOKE_FINAL_FADE_MS,
    SMOKE_FINAL_SPEED,
    SMOKE_INITIAL_ALPHA,
    SMOKE_INITIAL_FADE_MS,
    SMOKE_KEY_BIT,
    SMOKE_REFADE_ALPHA,
    SMOKE_REFADE_SPEED,
    smoke_point_alpha,
    smoke_segments,
)

# std button bits, so tests prove Smoke is independent of clicks
KEY_M1 = 1
KEY_K1 = 4

INTERVAL = SMOKE_DEFAULT_WIDTH_OSU * 7.0 / 8.0    # pointInterval, osu!px


@dataclass(frozen=True)
class F:
    """Minimal StdFrame stand-in (time_ms, x, y, keys)."""
    time_ms: int
    x: float
    y: float
    keys: int


# --- segment building ---------------------------------------------------------------


def test_no_smoke_bit_yields_empty():
    """THE BYTE-IDENTICAL GUARANTEE: a replay with no Smoke bit (even with
    clicks held) produces zero segments -> the scene's draw never fires."""
    frames = [F(i * 16, 100.0 + i, 100.0, KEY_M1 | KEY_K1) for i in range(50)]
    assert smoke_segments(frames, INTERVAL) == []


def test_empty_frames_yields_empty():
    assert smoke_segments([], INTERVAL) == []


def test_press_emits_a_dab_at_press_position():
    frames = [
        F(0, 100.0, 100.0, 0),
        F(16, 200.0, 150.0, SMOKE_KEY_BIT),      # press (no movement yet)
        F(32, 200.0, 150.0, 0),                  # release
    ]
    segs = smoke_segments(frames, INTERVAL)
    assert len(segs) == 1
    seg = segs[0]
    assert seg.start_ms == 16.0
    assert seg.end_ms == 32.0
    assert len(seg.pts) >= 1
    x, y, spawn, _ang, _set = seg.pts[0]
    assert (x, y, spawn) == (200.0, 150.0, 16.0)   # first dab at press pos/time


def test_smoke_bit_ignores_simultaneous_clicks():
    """Bit 16 is read independently of the 4 button bits."""
    frames = [
        F(0, 0.0, 0.0, KEY_M1),
        F(16, 10.0, 0.0, KEY_M1 | SMOKE_KEY_BIT),   # smoke pressed while clicking
        F(32, 10.0, 0.0, KEY_K1),                   # smoke released, still clicking
    ]
    segs = smoke_segments(frames, INTERVAL)
    assert len(segs) == 1
    assert segs[0].start_ms == 16.0 and segs[0].end_ms == 32.0


def test_never_released_ends_at_last_frame():
    frames = [
        F(0, 0.0, 0.0, 0),
        F(16, 50.0, 50.0, SMOKE_KEY_BIT),
        F(500, 60.0, 50.0, SMOKE_KEY_BIT),          # still held at replay end
    ]
    segs = smoke_segments(frames, INTERVAL)
    assert len(segs) == 1
    assert segs[0].end_ms == 500.0


def test_two_separate_strokes():
    frames = [
        F(0, 0.0, 0.0, SMOKE_KEY_BIT),
        F(16, 0.0, 0.0, 0),
        F(32, 0.0, 0.0, 0),
        F(48, 100.0, 100.0, SMOKE_KEY_BIT),
        F(64, 100.0, 100.0, 0),
    ]
    segs = smoke_segments(frames, INTERVAL)
    assert len(segs) == 2
    assert segs[0].start_ms == 0.0 and segs[0].end_ms == 16.0
    assert segs[1].start_ms == 48.0 and segs[1].end_ms == 64.0


def test_distance_resampled_spacing():
    """A long straight held drag emits ~ distance/interval dabs, spaced by
    the pointInterval (SmokeSegment.AddPosition)."""
    # hold smoke, sweep 0 -> 800 osu!px in x over many frames
    frames = [F(0, 0.0, 0.0, SMOKE_KEY_BIT)]
    for i in range(1, 41):
        frames.append(F(i * 4, i * 20.0, 0.0, SMOKE_KEY_BIT))   # 800 px total
    frames.append(F(200, 800.0, 0.0, 0))                        # release
    seg = smoke_segments(frames, INTERVAL)[0]
    dist = 800.0
    expected = dist / INTERVAL
    # first dab is at the press (before travel); the rest track the sweep
    assert abs(len(seg.pts) - (expected + 1)) <= 3
    xs = [p[0] for p in seg.pts]
    assert xs == sorted(xs)                     # monotonically advancing
    # consecutive spacing ~ INTERVAL (ignore the press dab at index 0)
    gaps = [xs[i + 1] - xs[i] for i in range(1, len(xs) - 1)]
    for g in gaps:
        assert abs(g - INTERVAL) < 1e-6


def test_determinism_seeded_per_segment():
    """Angles/settle come from a per-segment RNG seeded by segment index, so
    re-running produces byte-identical strokes (reproducible renders)."""
    frames = [F(0, 0.0, 0.0, SMOKE_KEY_BIT)]
    for i in range(1, 20):
        frames.append(F(i * 8, i * 15.0, i * 3.0, SMOKE_KEY_BIT))
    frames.append(F(200, 300.0, 60.0, 0))
    a = smoke_segments(frames, INTERVAL)[0].pts
    b = smoke_segments(frames, INTERVAL)[0].pts
    assert a == b


def test_times_parallel_and_sorted():
    frames = [F(0, 0.0, 0.0, SMOKE_KEY_BIT)]
    for i in range(1, 30):
        frames.append(F(i * 5, i * 25.0, 0.0, SMOKE_KEY_BIT))
    frames.append(F(200, 800.0, 0.0, 0))
    seg = smoke_segments(frames, INTERVAL)[0]
    assert seg.times == [p[2] for p in seg.pts]
    assert seg.times == sorted(seg.times)


def test_kill_ms_formula():
    """LifetimeEnd = end + final_fade + trunc/re_fade_speed + trunc/final_speed
    (SmokeSegment.FinishDrawing), trunc = min(4000, end-start)."""
    start, end = 100.0, 100.0 + 10000.0     # long hold -> trunc caps at 4000
    frames = [F(int(start), 0.0, 0.0, SMOKE_KEY_BIT),
              F(int(end), 5.0, 0.0, 0)]
    seg = smoke_segments(frames, INTERVAL)[0]
    trunc = min(SMOKE_INITIAL_FADE_MS, end - start)     # == 4000
    expect = (end + SMOKE_FINAL_FADE_MS
              + trunc / SMOKE_REFADE_SPEED + trunc / SMOKE_FINAL_SPEED)
    assert math.isclose(seg.kill_ms, expect, rel_tol=0, abs_tol=1e-6)


# --- alpha lifecycle ----------------------------------------------------------------

START, END = 0.0, 2000.0        # a 2 s held stroke


def test_alpha_full_white_at_exact_spawn():
    """Faithful to lazer: a dab sampled at its exact spawn instant is at the
    Color4.White default (1.0) — the bright leading edge at the cursor."""
    a = smoke_point_alpha(pt_ms=1000.0, t=1000.0, start_ms=START, end_ms=END)
    assert math.isclose(a, 1.0, abs_tol=1e-6)


def test_alpha_settles_to_initial_after_one_tick():
    """One tick past spawn the dab drops to ~initial_alpha (0.6) and fades."""
    a = smoke_point_alpha(pt_ms=1000.0, t=1001.0, start_ms=START, end_ms=END)
    assert math.isclose(a, SMOKE_INITIAL_ALPHA, abs_tol=1e-3)
    assert a < SMOKE_INITIAL_ALPHA            # already fading, just barely


def test_alpha_linear_initial_fade():
    """Halfway through the 4 s initial fade -> half of 0.6, while held."""
    # dab at t=0, sampled 2000 ms later, still before release... use a long hold
    long_end = 10000.0
    a = smoke_point_alpha(pt_ms=0.0, t=SMOKE_INITIAL_FADE_MS / 2.0,
                          start_ms=0.0, end_ms=long_end)
    assert math.isclose(a, SMOKE_INITIAL_ALPHA * 0.5, abs_tol=1e-3)


def test_alpha_fades_to_zero_after_initial_window():
    long_end = 10000.0
    a = smoke_point_alpha(pt_ms=0.0, t=SMOKE_INITIAL_FADE_MS + 1.0,
                          start_ms=0.0, end_ms=long_end)
    assert a <= 1e-6


def test_alpha_head_bright_after_release_then_burns_away():
    """The newest dab (spawned at END) is bright right after release (the
    'flash then burn away' look), and is fully gone by kill time."""
    a_release = smoke_point_alpha(pt_ms=END, t=END + 40.0,
                                  start_ms=START, end_ms=END)
    assert a_release >= SMOKE_INITIAL_ALPHA           # bright leading edge
    assert a_release <= SMOKE_REFADE_ALPHA + 1e-6
    trunc = min(SMOKE_INITIAL_FADE_MS, END - START)
    kill = (END + SMOKE_FINAL_FADE_MS
            + trunc / SMOKE_REFADE_SPEED + trunc / SMOKE_FINAL_SPEED)
    assert smoke_point_alpha(pt_ms=END, t=kill + 1.0,
                             start_ms=START, end_ms=END) <= 1e-6


def test_alpha_zero_long_after_kill():
    a = smoke_point_alpha(pt_ms=END, t=END + SMOKE_FINAL_FADE_MS + 6000.0,
                          start_ms=START, end_ms=END)
    assert a <= 1e-6


def test_alpha_never_out_of_range():
    for pt in (0.0, 500.0, 1500.0, END):
        for t in range(0, 14000, 137):
            a = smoke_point_alpha(pt, float(t), START, END)
            assert -1e-9 <= a <= 1.0 + 1e-9
