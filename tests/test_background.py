"""Background dim envelope (§4.10) + cover-fit math — pure CPU."""
from __future__ import annotations

from osu_std_renderer.beatmap.pause import Pause
from osu_std_renderer.render.background import (GLIDE_MS, DimEnvelope,
                                                build_dim_envelope,
                                                cover_size, smoothstep)

INTRO, NORMAL, BREAKS = 0.0, 0.9, 0.3
PRE = 540.0


def test_cover_size_fills_frame():
    # wide image into 16:9 frame → height-limited? 2000x500 into 1280x720:
    # scale = max(1280/2000=0.64, 720/500=1.44) = 1.44 → 2880x720
    w, h = cover_size(1280, 720, 2000, 500)
    assert abs(w - 2880.0) < 1e-9 and abs(h - 720.0) < 1e-9
    # tall image → width-limited
    w, h = cover_size(1280, 720, 500, 2000)
    assert abs(w - 1280.0) < 1e-9 and abs(h - 5120.0) < 1e-9
    # exact aspect → exact frame
    w, h = cover_size(1280, 720, 1920, 1080)
    assert abs(w - 1280.0) < 1e-9 and abs(h - 720.0) < 1e-9
    # cover never leaves a gap on either axis
    for iw, ih in ((640, 480), (3840, 1600), (100, 100)):
        w, h = cover_size(1280, 720, iw, ih)
        assert w >= 1280.0 - 1e-9 and h >= 720.0 - 1e-9


def test_smoothstep_shape():
    assert smoothstep(-1.0) == 0.0 and smoothstep(0.0) == 0.0
    assert smoothstep(1.0) == 1.0 and smoothstep(2.0) == 1.0
    assert abs(smoothstep(0.5) - 0.5) < 1e-12
    assert smoothstep(0.25) < 0.25 and smoothstep(0.75) > 0.75  # eased


def test_intro_hold_then_glide_to_gameplay():
    # first object 2212, preempt 540 → approach begins at 1672; the glide
    # completes EXACTLY there (never a half-dimmed first approach)
    env = build_dim_envelope(INTRO, NORMAL, BREAKS, [2212.0, 5000.0], PRE, [])
    fs = 2212.0 - PRE
    assert env.level(-1000.0) == INTRO
    assert env.level(fs - GLIDE_MS) == INTRO
    mid = env.level(fs - GLIDE_MS / 2.0)
    assert abs(mid - (INTRO + (NORMAL - INTRO) * 0.5)) < 1e-9
    assert env.level(fs) == NORMAL
    assert env.level(60_000.0) == NORMAL


def test_break_glide_and_return_for_next_object():
    # break 20s→30s, next object at 35s (approach 34.46s > break end →
    # the return anchors on the BREAK END)
    starts = [2212.0, 19_000.0, 35_000.0]
    env = build_dim_envelope(INTRO, NORMAL, BREAKS, starts, PRE,
                             [Pause(20_000.0, 30_000.0)])
    assert env.level(19_500.0) == NORMAL          # before the break
    assert env.level(20_000.0) == NORMAL          # glide starts here
    assert env.level(20_000.0 + GLIDE_MS) == BREAKS
    assert env.level(25_000.0) == BREAKS          # held through the break
    assert env.level(30_000.0 - GLIDE_MS) == BREAKS
    assert env.level(30_000.0) == NORMAL          # back at break end
    assert env.level(34_000.0) == NORMAL


def test_break_return_before_next_approach():
    # next object at 30.2s → its approach begins at 29.66s, INSIDE the
    # break → the return anchors there ("back for the next object")
    starts = [2212.0, 19_000.0, 30_200.0]
    env = build_dim_envelope(INTRO, NORMAL, BREAKS, starts, PRE,
                             [Pause(20_000.0, 30_000.0)])
    anchor = 30_200.0 - PRE
    assert env.level(anchor) == NORMAL
    assert env.level(anchor - GLIDE_MS) == BREAKS
    mid = env.level(anchor - GLIDE_MS / 2.0)
    assert abs(mid - (BREAKS + (NORMAL - BREAKS) * 0.5)) < 1e-9


def test_short_break_skipped():
    # a break too short to fit both glides → dim stays at NORMAL
    starts = [2212.0, 19_000.0, 21_500.0]
    env = build_dim_envelope(INTRO, NORMAL, BREAKS, starts, PRE,
                             [Pause(20_000.0, 20_800.0)])
    assert env.level(20_400.0) == NORMAL
    assert env.level(21_000.0) == NORMAL


def test_no_objects_holds_gameplay_dim():
    env = build_dim_envelope(INTRO, NORMAL, BREAKS, [], PRE, [])
    assert env.level(0.0) == NORMAL


def test_envelope_drops_overlapping_glides():
    # second glide starts inside the first → dropped, level continuous
    env = DimEnvelope(0.0, [(100.0, 600.0, 0.9), (300.0, 800.0, 0.1)])
    assert env.level(600.0) == 0.9
    assert env.level(10_000.0) == 0.9   # dropped glide never applies
    # degenerate (zero-length) glide dropped too
    env = DimEnvelope(0.2, [(100.0, 100.0, 0.9)])
    assert env.level(500.0) == 0.2
