"""Spinner visuals + hit-lighting math (render/spinner.py): style
auto-detect, rotation accumulation (held-gated, wrap-safe, lerped),
approach scale, metre bars + SpinnerNoBlink, RPM window/cap, clear time,
prompt/lighting lifecycles — pure, no GL."""
from __future__ import annotations

import math

from osu_std_renderer.beatmap.objects.spinner import RPMS
from osu_std_renderer.render.spinner import (APPROACH_END_SCALE,
                                             APPROACH_START_SCALE,
                                             LIGHTING_FADE_MS,
                                             METRE_BLINK_PERIOD_MS,
                                             RPM_DISPLAY_CAP, SPIN_FADE_MS,
                                             SPIN_HOLD_MS, SpinnerTrack,
                                             clear_alpha_scale,
                                             detect_spinner_style,
                                             lighting_alpha_scale,
                                             metre_bar_count,
                                             required_rotations,
                                             spin_prompt_alpha,
                                             spinner_approach_scale,
                                             wants_lighting)
from osu_std_renderer.replay.replay import KEY_K1, StdFrame
from osu_std_renderer.ruleset import JudgmentKind


def _spin_frames(start: float, end: float, rps: float, *, held: bool = True,
                 step: float = 10.0, cx: float = 256.0, cy: float = 192.0,
                 radius: float = 100.0) -> list[StdFrame]:
    """Synthetic frames circling the centre at `rps` rev/s (clockwise in
    the y-down convention)."""
    frames = []
    t = start
    while t <= end + 1e-9:
        ang = 2.0 * math.pi * rps * (t - start) / 1000.0
        frames.append(StdFrame(int(t), cx + radius * math.cos(ang),
                               cy + radius * math.sin(ang),
                               KEY_K1 if held else 0))
        t += step
    return frames


# --- style auto-detect ----------------------------------------------------------

def test_style_autodetect():
    # spinner-background → OLD, even when new-style sprites also exist
    assert detect_spinner_style({"spinner-background", "spinner-top"}) == "old"
    assert detect_spinner_style(
        {"spinner-background", "spinner-metre", "spinner-circle"}) == "old"
    # no background but any new sprite → NEW
    for name in ("spinner-glow", "spinner-bottom", "spinner-top",
                 "spinner-middle2", "spinner-middle"):
        assert detect_spinner_style({name, "hitcircle"}) == "new"
    # neither → procedural
    assert detect_spinner_style(set()) == "none"
    assert detect_spinner_style(
        {"hitcircle", "spinner-approachcircle", "spinner-rpm"}) == "none"


# --- rotation accumulation --------------------------------------------------------

def test_rotation_accumulates_signed_and_abs():
    # 2 rev/s for 1 s → 2 full turns
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0), 0, 1000,
                                  required_spins=4.0)
    assert abs(tr.rotation(1000) - 4.0 * math.pi) < 0.05
    assert abs(tr.progress(1000) - 0.5) < 0.01     # 2 of 4 required spins
    # halfway through: one turn
    assert abs(tr.rotation(500) - 2.0 * math.pi) < 0.05


def test_rotation_key_gated_and_wrap_safe():
    # not holding → the cursor circles but nothing accumulates
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0, held=False),
                                  0, 1000, required_spins=4.0)
    assert tr.rotation(1000) == 0.0
    assert tr.progress(1000) == 0.0
    # coarse frames stepping ~172.8° per frame still accumulate the short
    # way around (atan2 wrap): 0.48 rev/frame * 25 frames ≈ 12 rev
    frames = _spin_frames(0, 1000, 12.0, step=40.0)
    tr2 = SpinnerTrack.from_frames(frames, 0, 1000, required_spins=20.0)
    assert abs(tr2.rotation(1000) - 12.0 * 2.0 * math.pi) < 0.6


def test_rotation_direction_is_signed():
    frames = [StdFrame(t, 256.0 + 100.0 * math.cos(-2.0 * math.pi * t / 1000.0),
                       192.0 + 100.0 * math.sin(-2.0 * math.pi * t / 1000.0),
                       KEY_K1) for t in range(0, 1001, 10)]
    tr = SpinnerTrack.from_frames(frames, 0, 1000, required_spins=2.0)
    assert tr.rotation(1000) < -6.0                # counter-rotation → negative
    assert tr.progress(1000) > 0.45                # progress uses |delta|


def test_rotation_lerps_between_frames():
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 1.0, step=100.0),
                                  0, 1000, required_spins=1.0)
    r50 = tr.rotation(50)                          # halfway into frame 0→1
    assert 0.0 < r50 < tr.rotation(100)
    assert abs(r50 - tr.rotation(100) / 2.0) < 1e-9


def test_auto_spin_fills_requirement():
    # OD-derived requirement below the 477 RPM auto rate → clears
    dur = 1193.0
    spins = required_rotations(7.0, dur)           # Ohayou: OD9 → ratio 7
    tr = SpinnerTrack.auto(95554.0, 95554.0 + dur, spins)
    assert tr.progress(95554.0 + dur) > 1.0
    ct = tr.clear_time()
    assert ct is not None and 95554.0 < ct < 95554.0 + dur
    assert abs(tr.progress(ct) - 1.0) < 1e-6


def test_clear_time_interpolated_and_none_when_short():
    tr = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0), 0, 1000,
                                  required_spins=1.0)   # 2 spins vs 1 needed
    ct = tr.clear_time()
    assert ct is not None and abs(ct - 500.0) < 10.0
    tr2 = SpinnerTrack.from_frames(_spin_frames(0, 1000, 2.0), 0, 1000,
                                   required_spins=5.0)  # under-spun
    assert tr2.clear_time() is None


# --- approach scale ----------------------------------------------------------------

def test_spinner_approach_scale():
    assert spinner_approach_scale(-500.0, 0.0, 1000.0) == APPROACH_START_SCALE
    assert spinner_approach_scale(0.0, 0.0, 1000.0) == APPROACH_START_SCALE
    mid = spinner_approach_scale(500.0, 0.0, 1000.0)
    assert abs(mid - (APPROACH_START_SCALE + APPROACH_END_SCALE) / 2.0) < 1e-9
    assert abs(spinner_approach_scale(1000.0, 0.0, 1000.0)
               - APPROACH_END_SCALE) < 1e-9
    assert spinner_approach_scale(1001.0, 0.0, 1000.0) is None


# --- metre bars ---------------------------------------------------------------------

def test_metre_bar_count_quantized_and_capped():
    on = 0.0                                    # blink-on phase (t//30 even)
    off = METRE_BLINK_PERIOD_MS                 # blink-off phase
    assert metre_bar_count(0.0, True, on) == 0
    assert metre_bar_count(0.55, True, on) == 5
    assert metre_bar_count(0.999, True, on) == 9   # capped at 99%
    assert metre_bar_count(1.0, True, on) == 10    # cleared pins full
    assert metre_bar_count(1.0, False, off) == 10  # …steady in both modes


def test_metre_blink_flickers_next_bar():
    on, off = 0.0, METRE_BLINK_PERIOD_MS
    assert metre_bar_count(0.55, False, on) == 6   # next bar blinks in…
    assert metre_bar_count(0.55, False, off) == 5  # …and out
    assert metre_bar_count(0.55, True, on) == 5    # SpinnerNoBlink → steady
    assert metre_bar_count(0.0, False, on) == 1    # classic idle flicker


# --- RPM ------------------------------------------------------------------------------

def test_rpm_from_rate_and_cap():
    # 3 rev/s → 180 RPM
    tr = SpinnerTrack.from_frames(_spin_frames(0, 2000, 3.0), 0, 2000,
                                  required_spins=10.0)
    assert abs(tr.rpm(1500) - 180.0) < 6.0
    assert tr.rpm(0) == 0.0
    # the auto-spin constant sits exactly at the display cap
    auto = SpinnerTrack.auto(0, 2000, 10.0)
    assert abs(auto.rpm(1000) - RPM_DISPLAY_CAP) < 0.5
    assert auto.rpm(1000) <= RPM_DISPLAY_CAP
    # RPMS constant really is 477 RPM
    assert abs(RPMS * 60000.0 - RPM_DISPLAY_CAP) < 0.5


# --- prompts ------------------------------------------------------------------------

def test_spin_prompt_and_clear_lifecycle():
    assert spin_prompt_alpha(-400.0, 0.0) == 1.0          # approach: solid
    assert spin_prompt_alpha(SPIN_HOLD_MS, 0.0) == 1.0
    assert 0.0 < spin_prompt_alpha(SPIN_HOLD_MS + SPIN_FADE_MS / 2.0, 0.0) < 1.0
    assert spin_prompt_alpha(SPIN_HOLD_MS + SPIN_FADE_MS + 1.0, 0.0) == 0.0

    assert clear_alpha_scale(100.0, None) is None          # never cleared
    assert clear_alpha_scale(99.0, 100.0) is None          # not yet
    a0, s0 = clear_alpha_scale(100.0, 100.0)
    assert a0 == 0.0 and abs(s0 - 1.6) < 1e-9              # pops in at 1.6×
    a1, s1 = clear_alpha_scale(1000.0, 100.0)
    assert a1 == 1.0 and abs(s1 - 1.0) < 1e-9              # settled


# --- hit lighting ---------------------------------------------------------------------

def test_lighting_lifecycle_and_gating():
    assert lighting_alpha_scale(-1.0) is None
    a0, s0 = lighting_alpha_scale(0.0)
    assert a0 == 1.0 and abs(s0 - 0.8) < 1e-9
    a1, s1 = lighting_alpha_scale(LIGHTING_FADE_MS / 2.0)
    assert abs(a1 - 0.5) < 1e-9 and 0.8 < s1 < 1.2         # expanding out
    assert lighting_alpha_scale(LIGHTING_FADE_MS) is None  # gone

    assert wants_lighting(JudgmentKind.HIT300)
    assert wants_lighting(JudgmentKind.HIT100)
    assert wants_lighting(JudgmentKind.HIT50)
    assert not wants_lighting(JudgmentKind.MISS)           # never on a miss
