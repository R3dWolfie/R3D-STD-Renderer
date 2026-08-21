"""Mod-visual unit tests — OsuModHidden fades + OsuModFlashlight overlay
(render/mods.py). Timings/constants cross-checked against ppy/osu:
OsuModHidden.cs (FADE_OUT_DURATION_MULTIPLIER=0.3, fade starts at
StartTime-Preempt+TimeFadeIn), OsuModFlashlight.cs (GetSizeFor 100/200
breakpoints, DefaultFlashlightSize=180), ModFlashlight.cs
(FLASHLIGHT_FADE_DURATION=800)."""
from __future__ import annotations

from dataclasses import dataclass

from osu_std_renderer.render.mods import (
    FLASHLIGHT_DEFAULT_SIZE, FLASHLIGHT_FADE_DURATION, MOD_FLASHLIGHT,
    MOD_HIDDEN, build_flashlight_timeline, flashlight_size_at,
    flashlight_size_for, hidden_circle_fade, hidden_slider_fade)


def _close(a: float, b: float, eps: float = 1e-6) -> bool:
    return abs(a - b) <= eps


def test_mod_bits_match_osu_bitmask():
    assert MOD_HIDDEN == 8          # 1<<3
    assert MOD_FLASHLIGHT == 1024   # 1<<10


def test_hidden_circle_fade_starts_after_fade_in_and_is_linear():
    start, preempt, tfi = 1000.0, 600.0, 400.0
    fade_start = start - preempt + tfi          # 800
    dur = preempt * 0.3                          # 180
    # solid (factor 1) until fade-out begins
    assert _close(hidden_circle_fade(700.0, start, preempt, tfi), 1.0)
    assert _close(hidden_circle_fade(fade_start, start, preempt, tfi), 1.0)
    # linear 1→0 over preempt*0.3
    assert _close(hidden_circle_fade(fade_start + dur * 0.5,
                                     start, preempt, tfi), 0.5)
    assert _close(hidden_circle_fade(fade_start + dur, start, preempt, tfi), 0.0)
    # clamped past the end
    assert _close(hidden_circle_fade(fade_start + dur + 50.0,
                                     start, preempt, tfi), 0.0)


def test_hidden_slider_fade_runs_until_end_time_not_over_long_tail():
    # BUG #2 fix: the body fade lasts EndTime - fadeStart = Duration +
    # (Preempt - TimeFadeIn), NOT the old Duration + Preempt*0.3. fadeStart
    # = StartTime - Preempt + TimeFadeIn, so the fade completes exactly at
    # slider EndTime.
    start, end, preempt, tfi = 1000.0, 1600.0, 600.0, 400.0
    fade_start = start - preempt + tfi            # 800
    dur = end - fade_start                         # 800 (== Duration 600 + 200)
    assert _close(dur, (end - start) + (preempt - tfi))
    assert not _close(dur, (end - start) + preempt * 0.3)   # not the old tail
    assert _close(hidden_slider_fade(fade_start, start, end, preempt, tfi), 1.0)
    # Easing.Out (OutQuad): alpha = (1-p)^2 at p=0.5 -> 0.25
    mid = hidden_slider_fade(fade_start + dur * 0.5, start, end, preempt, tfi)
    assert _close(mid, 0.25)
    # fully faded exactly at EndTime (fade_start + dur == end)
    assert _close(fade_start + dur, end)
    assert _close(hidden_slider_fade(end, start, end, preempt, tfi), 0.0)


def test_hidden_slider_fade_duration_across_ar_and_hr():
    # duration = EndTime - fadeStart = sliderDuration + (Preempt - TimeFadeIn)
    def dur_of(start, end, preempt, tfi):
        return end - (start - preempt + tfi)
    short, long = 120.0, 3000.0
    start = 1000.0
    # AR10 (also the HR/HDHR AR-cap): Preempt 450, TimeFadeIn 400 -> +50 ms
    for sd in (short, long):
        end = start + sd
        d = dur_of(start, end, 450.0, 400.0)
        assert _close(d, sd + 50.0)
        assert not _close(d, sd + 450.0 * 0.3)     # NOT the old +135 ms
        assert _close(hidden_slider_fade(end, start, end, 450.0, 400.0), 0.0)
    # AR5: Preempt 1200, TimeFadeIn 800 -> +400 ms
    for sd in (short, long):
        end = start + sd
        assert _close(dur_of(start, end, 1200.0, 800.0), sd + 400.0)
    # AR<5 (AR3): Preempt 1500, TimeFadeIn 800 -> +700 ms
    for sd in (short, long):
        end = start + sd
        assert _close(dur_of(start, end, 1500.0, 800.0), sd + 700.0)


def test_flashlight_size_for_combo_breakpoints():
    assert flashlight_size_for(0) == FLASHLIGHT_DEFAULT_SIZE
    assert flashlight_size_for(100) == FLASHLIGHT_DEFAULT_SIZE          # >100, not >=
    assert flashlight_size_for(101) == FLASHLIGHT_DEFAULT_SIZE * 0.9
    assert flashlight_size_for(200) == FLASHLIGHT_DEFAULT_SIZE * 0.9
    assert flashlight_size_for(201) == FLASHLIGHT_DEFAULT_SIZE * 0.8
    assert flashlight_size_for(984) == FLASHLIGHT_DEFAULT_SIZE * 0.8


@dataclass
class _Ev:
    time_ms: float
    combo_after: int


def test_flashlight_timeline_and_800ms_lerp():
    # combo climbs past 100 at t=1000 and past 200 at t=5000
    events = [_Ev(500, 50), _Ev(1000, 101), _Ev(3000, 150),
              _Ev(5000, 201), _Ev(9000, 300)]
    tl = build_flashlight_timeline(events)
    # only two size changes: 180→162 at 1000, 162→144 at 5000
    assert len(tl) == 2
    assert tl[0][0] == 1000 and _close(tl[0][1], 180.0) and _close(tl[0][2], 162.0)
    assert tl[1][0] == 5000 and _close(tl[1][1], 162.0) and _close(tl[1][2], 144.0)
    # before any change: default size
    assert _close(flashlight_size_at(tl, 0.0), 180.0)
    # at the change instant: still the from-size
    assert _close(flashlight_size_at(tl, 1000.0), 180.0)
    # halfway through the 800ms transform: lerp 180→162
    assert _close(flashlight_size_at(tl, 1000.0 + FLASHLIGHT_FADE_DURATION * 0.5),
                  171.0)
    # transform complete
    assert _close(flashlight_size_at(tl, 1000.0 + FLASHLIGHT_FADE_DURATION), 162.0)
    assert _close(flashlight_size_at(tl, 4000.0), 162.0)
    # second transform tail
    assert _close(flashlight_size_at(tl, 9000.0), 144.0)
