"""Gameplay-polish visuals (branch gameplay-polish):

  * Argon circle KiaiFlash trigger from the beatmap timing/effect points
    (render/scene.argon_kiai_flash_strength) — the additive combo-tinted disc
    pulsing on each red-line beat DURING kiai only.
  * Spinner bonus popup value/timing (render/spinner: spinner_bonus_thresholds,
    bonus_spin_events, bonus_popup_state, SpinnerTrack.time_at_rotation).

All pure — no GL, no scoring side effects (visual-only)."""
from __future__ import annotations

import math

from osu_std_renderer.beatmap.objects.timing import Timings
from osu_std_renderer.render.scene import (KIAI_FLASH_OPACITY,
                                           argon_kiai_flash_strength)
from osu_std_renderer.render.spinner import (BONUS_LIFE_MS, BONUS_MAX_LIFE_MS,
                                             BONUS_MAX_SCALE, BONUS_POP_SCALE,
                                             SPINNER_BONUS_SCORE_PER_TICK,
                                             SpinnerTrack, bonus_popup_state,
                                             bonus_spin_events,
                                             spinner_bonus_thresholds)


def _timings(kiai: bool, bl: float = 500.0) -> Timings:
    """One red line at t=0 with beat length bl and the given kiai flag."""
    tm = Timings()
    tm.add_point(0.0, bl, 1, 1, 1.0, 4, inherited=False, kiai=kiai)
    tm.finalize_points()
    return tm


# --- Argon KiaiFlash ----------------------------------------------------------

def test_kiai_flash_zero_outside_kiai():
    tm = _timings(kiai=False)
    for t in (0.0, 120.0, 500.0, 730.0, 1000.0):
        assert argon_kiai_flash_strength(t, -1e6, tm) == 0.0


def test_kiai_flash_peaks_on_the_beat():
    tm = _timings(kiai=True, bl=500.0)   # beats at 0, 500, 1000, ...
    # exactly on a beat the FadeTo(0.25) has just completed → full opacity
    assert math.isclose(argon_kiai_flash_strength(500.0, -1e6, tm),
                        KIAI_FLASH_OPACITY, rel_tol=1e-9)
    assert math.isclose(argon_kiai_flash_strength(1000.0, -1e6, tm),
                        KIAI_FLASH_OPACITY, rel_tol=1e-9)


def test_kiai_flash_in_bounds_and_falls_off():
    tm = _timings(kiai=True, bl=500.0)
    # OutSine fall after the beat is monotonically decreasing
    prev = argon_kiai_flash_strength(500.0, -1e6, tm)
    for t in (560.0, 640.0, 760.0, 900.0):
        cur = argon_kiai_flash_strength(t, -1e6, tm)
        assert 0.0 <= cur <= KIAI_FLASH_OPACITY + 1e-12
        assert cur < prev + 1e-12
        prev = cur


def test_kiai_flash_rises_into_the_beat():
    tm = _timings(kiai=True, bl=500.0)
    # the trough where beat 0's OutSine tail meets beat 500's ramp is ~0
    assert argon_kiai_flash_strength(420.0, -1e6, tm) < 1e-6
    # EarlyActivationMilliseconds=80: the ramp climbs over the 80 ms into beat 500
    lo = argon_kiai_flash_strength(440.0, -1e6, tm)           # rising
    hi = argon_kiai_flash_strength(490.0, -1e6, tm)
    assert 0.0 < lo < hi <= KIAI_FLASH_OPACITY


def test_kiai_flash_gated_by_spawn():
    tm = _timings(kiai=True, bl=500.0)
    # a piece that appears at 490 misses beat 500's activation (fired at 420),
    # so no flash on that beat; the next beat (1000, activation 920) does flash
    assert argon_kiai_flash_strength(500.0, 490.0, tm) == 0.0
    assert math.isclose(argon_kiai_flash_strength(1000.0, 490.0, tm),
                        KIAI_FLASH_OPACITY, rel_tol=1e-9)


# --- spinner bonus thresholds -------------------------------------------------

def test_bonus_thresholds():
    # sec=5: SpinsRequired=int(0.6*5)=3, +gap(2) → 5; MaxBonus=int(1.4*5)-3-2=2
    assert spinner_bonus_thresholds(0.6, 1.4, 5000.0) == (5, 2)


def test_bonus_thresholds_no_bonus_when_rates_equal():
    # max spins == clear spins → no bonus ticks
    req, mx = spinner_bonus_thresholds(0.6, 0.6, 5000.0)
    assert req == 5 and mx == 0


# --- spinner bonus events -----------------------------------------------------

def _linear_track(revs: float, dur_ms: float = 5000.0) -> SpinnerTrack:
    """A perfectly linear spin reaching `revs` full turns over dur_ms — spin k
    completes at k/revs*dur_ms."""
    total = revs * 2.0 * math.pi
    return SpinnerTrack(0.0, dur_ms, [0.0, dur_ms], [0.0, total], [0.0, total],
                        required_rot=5.0 * 2.0 * math.pi)


def test_time_at_rotation_interpolates():
    tr = _linear_track(10.0)             # 10 revs over 5000 ms → 500 ms/rev
    assert math.isclose(tr.time_at_rotation(2.0 * math.pi), 500.0, rel_tol=1e-9)
    assert math.isclose(tr.time_at_rotation(7.0 * 2.0 * math.pi), 3500.0,
                        rel_tol=1e-9)
    assert tr.time_at_rotation(11.0 * 2.0 * math.pi) is None   # never reached


def test_bonus_events_cumulative_then_max():
    tr = _linear_track(10.0)             # completes 10 spins
    events = bonus_spin_events(tr, spins_required_for_bonus=5, max_bonus_spins=2)
    # first bonus at spin 6 (=50), spin 7 hits the cap (=100, MAX), then MAX
    # re-pops on every further spin (8,9,10)
    assert events == [
        (3000.0, 50, False),
        (3500.0, 100, True),
        (4000.0, 100, True),
        (4500.0, 100, True),
        (5000.0, 100, True),
    ]
    assert SPINNER_BONUS_SCORE_PER_TICK == 50


def test_bonus_events_none_when_max_bonus_zero():
    tr = _linear_track(10.0)
    assert bonus_spin_events(tr, spins_required_for_bonus=5,
                             max_bonus_spins=0) == []


def test_bonus_events_stop_before_required():
    tr = _linear_track(4.0)              # only 4 spins, bonus needs >5
    assert bonus_spin_events(tr, 5, 2) == []


# --- spinner bonus popup animation --------------------------------------------

def test_bonus_popup_pops_then_fades():
    a0, s0 = bonus_popup_state(0.0, is_max=False)
    assert math.isclose(a0, 1.0) and math.isclose(s0, BONUS_POP_SCALE)
    # settles toward 1.0 and fades linearly
    a1, s1 = bonus_popup_state(750.0, is_max=False)
    assert 0.0 < a1 < 1.0
    assert s1 < BONUS_POP_SCALE          # scaling down 1.5 → 1.0
    assert bonus_popup_state(BONUS_LIFE_MS, is_max=False) is None


def test_bonus_popup_max_grows_and_short_life():
    a0, s0 = bonus_popup_state(0.0, is_max=True)
    assert math.isclose(s0, BONUS_POP_SCALE)
    a1, s1 = bonus_popup_state(250.0, is_max=True)
    assert s1 > BONUS_POP_SCALE          # scaling UP 1.5 → 2.8
    assert s1 <= BONUS_MAX_SCALE + 1e-9
    assert bonus_popup_state(BONUS_MAX_LIFE_MS, is_max=True) is None
    # MAX fades faster than a normal bonus
    assert bonus_popup_state(600.0, is_max=False) is not None
    assert bonus_popup_state(600.0, is_max=True) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
