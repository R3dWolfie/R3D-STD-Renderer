"""Effect lifecycle math (render/effects.py) + the background helpers
(blur/parallax/flash) + snaking-out ranges. Pure CPU."""
from __future__ import annotations

import math

import numpy as np

from osu_std_renderer.beatmap.pause import Pause
from osu_std_renderer.render.background import (FLASH_AMOUNT, PARALLAX_SCALE,
                                                blur_background, flash_factor,
                                                parallax_offset)
from osu_std_renderer.render.effects import (LOGO_FADE_OUT_MS,
                                             LOGO_MIN_WINDOW_MS,
                                             RIPPLE_LIFE_MS,
                                             SEIZURE_DURATION_S,
                                             SEIZURE_FADE_MS, TRI_ALPHA,
                                             WARN_BLINK_MS, WARN_WINDOW_MS,
                                             break_resume_anchors,
                                             fade_to_black_alpha, logo_alpha,
                                             logo_scale, rainbow_rgb,
                                             ripple_events, ripple_states,
                                             seizure_alpha, triangle_field,
                                             triangle_states, triangle_y,
                                             warning_arrow_alpha)
from osu_std_renderer.render.scene import snake_range


class _Frame:
    def __init__(self, t, x, y, keys):
        self.time_ms, self.x, self.y, self.keys = t, x, y, keys


# --- triangles -----------------------------------------------------------------

def test_triangle_field_deterministic_and_bounded():
    a = triangle_field(seed=7)
    b = triangle_field(seed=7)
    assert a == b                          # same seed → same render
    assert a != triangle_field(seed=8)
    for x, size, speed, shade, phase in a:
        assert 0.0 <= x < 1.0 and 0.0 <= phase < 1.0
        assert 0.0 < size < 0.5 and speed > 0.0 and 0.0 < shade <= 1.0


def test_triangles_drift_upward_and_wrap():
    f = triangle_field(seed=1)
    x, size, speed, shade, phase = f[0]
    y0 = triangle_y(phase, speed, size, 0.0)
    y1 = triangle_y(phase, speed, size, 100.0)   # 100 ms later
    # moving UP (decreasing y) unless it wrapped past the top
    assert y1 < y0 or y1 > y0 + 0.5
    # a full wrap period returns to the start
    span = 1.0 + 2.0 * size
    period_ms = span / speed * 1000.0
    assert abs(triangle_y(phase, speed, size, period_ms) - y0) < 1e-9
    # states carry the subtle alpha and the field's shade
    st = triangle_states(f, 1234.0)
    assert len(st) == len(f)
    assert all(s[4] == TRI_ALPHA for s in st)


# --- logo ---------------------------------------------------------------------

def test_logo_envelope_anchors_on_gameplay():
    t0, game_in = 0.0, 3000.0
    assert logo_alpha(-1.0, t0, game_in) is None          # before start
    a_mid = logo_alpha(1500.0, t0, game_in)
    assert a_mid is not None and a_mid > 0.8              # held
    # fade-out ENDS exactly at gameplay_in
    a_late = logo_alpha(game_in - LOGO_FADE_OUT_MS / 2.0, t0, game_in)
    assert a_late is not None and a_late < a_mid
    assert logo_alpha(game_in, t0, game_in) is None
    # short windows show nothing
    assert logo_alpha(100.0, 0.0, LOGO_MIN_WINDOW_MS - 1.0) is None
    # settle scale eases 1.06 → 1.0
    assert logo_scale(0.0, 0.0) > logo_scale(600.0, 0.0) == 1.0


# --- seizure card ---------------------------------------------------------------

def test_seizure_card_opaque_then_fades():
    dur = SEIZURE_DURATION_S * 1000.0
    assert seizure_alpha(-1.0, 0.0) is None
    assert seizure_alpha(0.0, 0.0) == 1.0
    assert seizure_alpha(dur - SEIZURE_FADE_MS - 1.0, 0.0) == 1.0
    mid = seizure_alpha(dur - SEIZURE_FADE_MS / 2.0, 0.0)
    assert mid is not None and 0.4 < mid < 0.6
    assert seizure_alpha(dur, 0.0) is None


# --- fade out -------------------------------------------------------------------

def test_fade_to_black_ramp():
    assert fade_to_black_alpha(999.0, 1000.0, 2000.0) == 0.0
    assert fade_to_black_alpha(2000.0, 1000.0, 2000.0) == 0.5
    assert fade_to_black_alpha(3000.0, 1000.0, 2000.0) == 1.0
    assert fade_to_black_alpha(9999.0, 1000.0, 2000.0) == 1.0   # held
    assert fade_to_black_alpha(5000.0, 1000.0, 0.0) == 0.0      # len 0 → off


# --- warning arrows ---------------------------------------------------------------

def test_break_anchors_match_dim_envelope_semantics():
    starts = [2000.0, 19_000.0, 30_200.0]
    pre = 540.0
    anchors = break_resume_anchors([Pause(20_000.0, 30_000.0)], starts, pre)
    assert anchors == [min(30_000.0, 30_200.0 - pre)]
    # short breaks skipped
    assert break_resume_anchors([Pause(20_000.0, 21_000.0)], starts,
                                pre) == []


def test_warning_arrows_blink_inside_window_only():
    anchor = 30_000.0
    anchors = [anchor]
    assert warning_arrow_alpha(anchor - WARN_WINDOW_MS - 1.0, anchors) == 0.0
    assert warning_arrow_alpha(anchor + 1.0, anchors) == 0.0
    # ON during the first half of a blink period, OFF the second half
    t_on = anchor - WARN_WINDOW_MS + WARN_BLINK_MS * 0.25
    t_off = anchor - WARN_WINDOW_MS + WARN_BLINK_MS * 0.75
    assert warning_arrow_alpha(t_on, anchors) > 0.9
    assert warning_arrow_alpha(t_off, anchors) == 0.0


# --- ripples --------------------------------------------------------------------

def test_ripples_fire_on_press_edges_only():
    frames = [_Frame(0, 100, 100, 0), _Frame(10, 101, 101, 1),   # edge
              _Frame(20, 102, 102, 1),                            # held
              _Frame(30, 103, 103, 0),                            # release
              _Frame(40, 104, 104, 2)]                            # edge
    evs = ripple_events(frames)
    assert [e[0] for e in evs] == [10.0, 40.0]
    times = [e[0] for e in evs]
    live = ripple_states(evs, times, 15.0)
    assert len(live) == 1
    x, y, scale, alpha = live[0]
    assert (x, y) == (101.0, 101.0) and 0.0 < scale < 1.0 and alpha > 0.0
    # grows and fades over the life; gone after
    s1 = ripple_states(evs, times, 10.0 + RIPPLE_LIFE_MS * 0.2)[0]
    s2 = ripple_states(evs, times, 10.0 + RIPPLE_LIFE_MS * 0.8)[0]
    assert s2[2] > s1[2] and s2[3] < s1[3]
    # the first ripple expires while the second (t=40) still lives…
    live = ripple_states(evs, times, 10.0 + RIPPLE_LIFE_MS + 1)
    assert len(live) == 1 and live[0][0] == 104.0
    # …and everything is gone after the last one's life
    assert ripple_states(evs, times, 40.0 + RIPPLE_LIFE_MS + 1) == []


# --- rainbow --------------------------------------------------------------------

def test_rainbow_cycles_hue():
    r0 = rainbow_rgb(0.0, cycle_ms=1000.0)
    r_half = rainbow_rgb(500.0, cycle_ms=1000.0)
    r_full = rainbow_rgb(1000.0, cycle_ms=1000.0)
    assert all(0.0 <= c <= 1.0 for c in r0 + r_half + r_full)
    assert r0 != r_half                       # hue moved
    assert all(abs(a - b) < 1e-9 for a, b in zip(r0, r_full))  # periodic


# --- background helpers ------------------------------------------------------------

def test_blur_background_softens_and_fails_soft():
    # 216 rows → radius = blur·2.2·(216/1080) ≈ 4.4 px at blur 10 (the
    # radius scales with the image so a preset value reads the same at
    # any source resolution)
    rgba = np.zeros((216, 216, 4), dtype=np.uint8)
    rgba[..., 3] = 255
    rgba[80:136, 80:136, :3] = 255            # hard white square
    out = blur_background(rgba, 10)
    assert out.shape == rgba.shape
    # the hard edge is now soft: bleed just outside, loss just inside
    assert out[77, 108, 0] > 0
    assert out[82, 108, 0] < 255
    # a smaller preset value blurs LESS (edge keeps more energy)
    softer = blur_background(rgba, 2)
    assert softer[82, 108, 0] > out[82, 108, 0]
    # 0 = untouched (same object allowed)
    assert np.array_equal(blur_background(rgba, 0), rgba)


def test_parallax_opposes_cursor_and_never_exposes_edges():
    W, H = 1920.0, 1080.0
    bw, bh = 1920.0, 1200.0                    # cover size ≥ frame
    # cursor at the right edge → bg slides LEFT
    dx, dy = parallax_offset(1.0, 0.0, bw, bh, W, H)
    assert dx < 0.0 and dy == 0.0
    # the shift never exceeds the oversize slack on either axis
    slack_x = (bw * PARALLAX_SCALE - W) / 2.0
    slack_y = (bh * PARALLAX_SCALE - H) / 2.0
    for nx in (-1.0, -0.3, 0.0, 0.7, 1.0):
        for ny in (-1.0, 0.0, 1.0):
            dx, dy = parallax_offset(nx, ny, bw, bh, W, H)
            assert abs(dx) <= slack_x + 1e-6
            assert abs(dy) <= slack_y + 1e-6
    # centred cursor → no shift
    assert parallax_offset(0.0, 0.0, bw, bh, W, H) == (0.0, 0.0)


def test_flash_factor_peaks_on_beat_and_decays():
    assert abs(flash_factor(0.0) - (1.0 + FLASH_AMOUNT)) < 1e-9
    assert flash_factor(0.5) < flash_factor(0.1)
    assert abs(flash_factor(1.0) - 1.0) < 1e-9


# --- audio fade (§4.10 FadeOutTime, audio side) --------------------------------------

def test_audio_fade_out_ramps_and_silences():
    from osu_std_renderer.record.audio import SAMPLE_RATE, AudioMixer
    m = AudioMixer(1000.0)
    m.buf[:] = 1.0
    m.fade_out(0.0, 500.0)
    assert m.buf[0, 0] > 0.99                       # ramp starts at 1
    mid = m.buf[int(0.25 * SAMPLE_RATE), 0]
    assert 0.4 < mid < 0.6                          # halfway down mid-ramp
    assert m.buf[int(0.75 * SAMPLE_RATE), 0] == 0.0  # silent after
    assert m.buf[-1, 0] == 0.0
    # degenerate window → no-op
    m2 = AudioMixer(100.0)
    m2.buf[:] = 0.5
    m2.fade_out(50.0, 50.0)
    assert float(m2.buf.max()) == 0.5


def test_audio_silence_before_preroll():
    from osu_std_renderer.record.audio import SAMPLE_RATE, AudioMixer
    m = AudioMixer(1000.0)
    m.buf[:] = 1.0
    m.silence_before(500.0, fade_ms=100.0)
    assert m.buf[0, 0] == 0.0                        # pre-roll silent
    assert m.buf[int(0.39 * SAMPLE_RATE), 0] == 0.0  # up to the fade
    mid = m.buf[int(0.45 * SAMPLE_RATE), 0]
    assert 0.3 < mid < 0.7                           # fade-in ramp
    assert m.buf[int(0.6 * SAMPLE_RATE), 0] == 1.0   # untouched after
    m2 = AudioMixer(100.0)
    m2.buf[:] = 0.5
    m2.silence_before(0.0)                           # no pre-roll → no-op
    assert float(m2.buf.min()) == 0.5


# --- snaking out -------------------------------------------------------------------

def test_snake_range_forward_final_span_retracts_start():
    # 1-span slider (no repeats): the whole slide IS the final span
    start, plen, pre = 1000.0, 400.0, 600.0
    a, b = snake_range(start - pre, start, plen, 1, pre)      # spawn
    assert a == 0.0 and b == 0.0
    a, b = snake_range(start, start, plen, 1, pre)            # at the hit
    assert a == 0.0 and b == 1.0
    a, b = snake_range(start + 200.0, start, plen, 1, pre)    # mid-slide
    assert abs(a - 0.5) < 1e-9 and b == 1.0                   # start edge moves
    a, b = snake_range(start + 400.0, start, plen, 1, pre)    # at the end
    assert a == 1.0 and b == 1.0                              # fully retracted


def test_snake_range_reverse_final_span_retracts_end():
    # 2 spans: final span runs tail→head → the END edge retracts
    start, plen, pre = 1000.0, 400.0, 600.0
    a, b = snake_range(start + 200.0, start, plen, 2, pre)    # span 0
    assert a == 0.0 and b == 1.0                              # full body
    a, b = snake_range(start + 600.0, start, plen, 2, pre)    # span 1 mid
    assert a == 0.0 and abs(b - 0.5) < 1e-9
    a, b = snake_range(start + 800.0, start, plen, 2, pre)
    assert a == 0.0 and b == 0.0


def test_snake_range_flags_off():
    start, plen, pre = 1000.0, 400.0, 600.0
    # snaking_out off → classic full body through the slide
    a, b = snake_range(start + 200.0, start, plen, 1, pre, snaking_out=False)
    assert a == 0.0 and b == 1.0
    # snaking_in off → body full from spawn
    a, b = snake_range(start - pre, start, plen, 1, pre, snaking_in=False)
    assert a == 0.0 and b == 1.0
    # 3 spans: spans 0/1 keep the full body, span 2 (forward) retracts
    a, b = snake_range(start + plen * 1.5, start, plen, 3, pre)
    assert (a, b) == (0.0, 1.0)
    a, b = snake_range(start + plen * 2.5, start, plen, 3, pre)
    assert abs(a - 0.5) < 1e-9 and b == 1.0
