"""FAIL/DEATH handling — the osu! fail sequence for a replay that died.

Covers the three concerns of the fail feature:
  1. detection   (life-bar 0 → fail_time; NoFail/Autopilot → pass; empty
                  life-bar → conservative pass)
  2. animation   (FailAnimationContainer.cs transforms ported: object
                  gravity/rotation/scale, playfield gray, red flash)
  3. results     (grade F colour + stats FROZEN at the death point)
"""
from __future__ import annotations

import math

from osu_std_renderer.replay.replay import (MOD_AUTOPILOT, MOD_NOFAIL,
                                            detect_fail_time)


# --- fakes ---------------------------------------------------------------------------

class _Life:
    def __init__(self, time, life):
        self.time = time
        self.life = life


class _Replay:
    """Duck-typed osrparse.Replay stand-in for detect_fail_time."""
    def __init__(self, mods=0, life=None):
        self.mods = mods
        self.life_bar_graph = life


# --- 1. detection --------------------------------------------------------------------

def test_detect_fail_lifebar_zero_returns_first_death():
    r = _Replay(mods=0, life=[_Life(0, 1.0), _Life(1000, 0.4),
                              _Life(2000, 0.0), _Life(3000, 0.0)])
    assert detect_fail_time(r) == 2000.0


def test_detect_fail_epsilon_near_zero_trips():
    r = _Replay(mods=0, life=[_Life(500, 0.0005)])
    assert detect_fail_time(r) == 500.0


def test_detect_no_fail_when_health_never_zero():
    r = _Replay(mods=0, life=[_Life(0, 1.0), _Life(1000, 0.2),
                              _Life(2000, 0.5)])
    assert detect_fail_time(r) is None


def test_nofail_mod_never_fails():
    # even with the life-bar flatlined at 0, NoFail overrides failing
    r = _Replay(mods=MOD_NOFAIL, life=[_Life(1000, 0.0)])
    assert detect_fail_time(r) is None
    assert MOD_NOFAIL == 0x1


def test_autopilot_mod_never_fails():
    r = _Replay(mods=MOD_AUTOPILOT, life=[_Life(1000, 0.0)])
    assert detect_fail_time(r) is None
    assert MOD_AUTOPILOT == 0x2000


def test_relax_can_still_fail():
    # Relax (0x80) does NOT override failing in osu! — a zero still trips
    r = _Replay(mods=0x80, life=[_Life(1200, 0.0)])
    assert detect_fail_time(r) == 1200.0


def test_empty_lifebar_is_conservative_pass():
    assert detect_fail_time(_Replay(mods=0, life=[])) is None
    assert detect_fail_time(_Replay(mods=0, life=None)) is None


def test_nofail_combined_with_other_mods():
    # HD+DT+NoFail (0x8|0x40|0x1) — NoFail bit still exempts
    r = _Replay(mods=0x49, life=[_Life(1000, 0.0)])
    assert detect_fail_time(r) is None


# --- 2. animation transforms (FailAnimationContainer.cs values) ----------------------

def test_fail_progress_clamps():
    from osu_std_renderer.render.scene import FAIL_DURATION_MS, fail_progress
    assert fail_progress(0.0) == 0.0
    assert fail_progress(FAIL_DURATION_MS) == 1.0
    assert fail_progress(FAIL_DURATION_MS * 2) == 1.0        # clamps
    assert abs(fail_progress(FAIL_DURATION_MS / 2) - 0.5) < 1e-9


def test_fail_object_fade_over_half_duration():
    # HitObjectContainer.FadeOut(duration / 2) = 1250 ms
    from osu_std_renderer.render.scene import fail_object_alpha
    assert fail_object_alpha(0.0) == 1.0
    assert abs(fail_object_alpha(625.0) - 0.5) < 1e-9
    assert fail_object_alpha(1250.0) == 0.0
    assert fail_object_alpha(2500.0) == 0.0


def test_fail_red_flash_fades_from_one_over_1000ms():
    # redFlashLayer Color4.Red.Opacity(0.6) FadeOutFromOne(1000)
    from osu_std_renderer.render.scene import FAIL_RED_ALPHA, fail_red_alpha
    assert abs(fail_red_alpha(0.0) - FAIL_RED_ALPHA) < 1e-9   # 0.6 at t=0
    assert abs(fail_red_alpha(500.0) - 0.3) < 1e-9
    assert fail_red_alpha(1000.0) == 0.0
    assert fail_red_alpha(2500.0) == 0.0


def test_fail_gray_and_scale_values():
    # Content.FadeColour(Gray)=0.5, ScaleTo(0.85), object ScaleTo(0.5)
    from osu_std_renderer.render.scene import (fail_content_scale,
                                               fail_gray_factor,
                                               fail_object_scale)
    assert fail_gray_factor(0.0) == 1.0
    assert abs(fail_gray_factor(1.0) - 0.5) < 1e-9
    assert fail_object_scale(0.0) == 1.0
    assert abs(fail_object_scale(1.0) - 0.5) < 1e-9
    assert fail_content_scale(0.0) == 1.0
    assert abs(fail_content_scale(1.0) - 0.85) < 1e-9        # OutQuart hits 1


def test_fail_content_rotation_is_one_degree():
    from osu_std_renderer.render.scene import fail_content_rotation
    assert fail_content_rotation(0.0) == 0.0
    assert abs(fail_content_rotation(1.0) - math.radians(1.0)) < 1e-9


def test_fail_rotation_in_range_and_deterministic():
    from osu_std_renderer.render.scene import (FAIL_ROT_RANGE_DEG,
                                               fail_rotation_deg)
    for seed in (0.0, 123.0, 45678.0, 999999.0):
        r = fail_rotation_deg(seed)
        assert -FAIL_ROT_RANGE_DEG <= r <= FAIL_ROT_RANGE_DEG
    assert fail_rotation_deg(1234.0) == fail_rotation_deg(1234.0)   # stable


def test_fail_transform_point_identity_at_start():
    from osu_std_renderer.render.scene import fail_transform_point
    x, y = fail_transform_point(100.0, 50.0, pivot=(60.0, 40.0),
                                center=(0.0, 0.0), obj_scale=1.0, obj_rot=0.0,
                                fall_px=0.0, content_scale=1.0, content_rot=0.0)
    assert abs(x - 100.0) < 1e-9 and abs(y - 50.0) < 1e-9


def test_fail_transform_point_gravity_drops_down():
    # pure downward fall: pivot at same point, only gravity
    from osu_std_renderer.render.scene import fail_transform_point
    x, y = fail_transform_point(100.0, 50.0, pivot=(100.0, 50.0),
                                center=(0.0, 0.0), obj_scale=1.0, obj_rot=0.0,
                                fall_px=400.0, content_scale=1.0,
                                content_rot=0.0)
    assert abs(x - 100.0) < 1e-9         # no horizontal drift
    assert abs(y - 450.0) < 1e-9         # y increased by fall_px (down)


def test_fail_transform_sprite_grays_shrinks_fades():
    from osu_std_renderer.render.gl import Sprite
    from osu_std_renderer.render.scene import fail_transform_sprite
    sp = Sprite(10.0, 10.0, 40.0, 40.0, "k", (1.0, 1.0, 1.0, 1.0))
    out = fail_transform_sprite(
        sp, pivot=(10.0, 10.0), center=(0.0, 0.0), obj_scale=0.5, obj_rot=0.0,
        fall_px=0.0, content_scale=0.85, content_rot=0.0, gray=0.5,
        obj_alpha=0.5)
    assert abs(out.w - 40.0 * 0.5 * 0.85) < 1e-6      # object×content scale
    r, g, b, a = out.color
    assert abs(r - 0.5) < 1e-9 and abs(g - 0.5) < 1e-9      # gray tint
    assert abs(a - 0.5) < 1e-9                              # object fade
    assert out.texture_key == "k"                          # preserved


# --- 3. F grade + stats frozen at death ----------------------------------------------

def test_F_grade_colour_is_fail_red():
    from osu_std_renderer.render.hud import GRADE_COLORS
    from osu_std_renderer.render.lazer_results import FOR_RANK
    # the HUD gameplay grade F stays stable fail red (ff5a5a)
    r, g, b = GRADE_COLORS["F"]
    assert abs(r - 1.0) < 1e-6 and abs(g - 0x5a / 255) < 1e-2
    # the results-screen FOR_RANK["F"] is now OsuColour.ForRank(F) = gray 3f3f3f
    # (the EXACT lazer value), distinct from the D red.
    fr, fg, fb = FOR_RANK["F"]
    assert abs(fr - 0x3f / 255) < 1e-6 and abs(fg - 0x3f / 255) < 1e-6 \
        and abs(fb - 0x3f / 255) < 1e-6
    assert FOR_RANK["F"] != FOR_RANK["D"]


def test_F_grade_arc_caps_at_virtual_ss_notch():
    # a non-SS grade's accuracy arc must never close the ring
    from osu_std_renderer.render.lazer_results import (VIRTUAL_SS_PERCENTAGE,
                                                       target_arc_value)
    assert target_arc_value(0.30, "F") <= 1.0 - VIRTUAL_SS_PERCENTAGE
    assert target_arc_value(0.999, "F") <= 1.0 - VIRTUAL_SS_PERCENTAGE


class _Part:
    def __init__(self, kind, hit):
        self.kind = kind
        self.hit = hit


class _Verdict:
    def __init__(self, start_time, parts):
        self.start_time = start_time
        self.parts = parts


class _Sim:
    def __init__(self, verdicts):
        self.verdicts = verdicts


def test_slider_stats_before_filter_drops_post_death_sliders():
    from osu_std_renderer.render.lazer_results import slider_stats
    sim = _Sim({
        1: _Verdict(1000.0, [_Part("tick", True), _Part("tail", True)]),
        2: _Verdict(5000.0, [_Part("tick", True), _Part("tail", True)]),
    })
    # all sliders → 2 ticks + 2 tails
    assert slider_stats(sim) == (2, 2, 2, 2)
    # only the slider that STARTED before death @ 3000
    assert slider_stats(sim, before=3000.0) == (1, 1, 1, 1)
    # death before both → none counted
    assert slider_stats(sim, before=500.0) == (0, 0, 0, 0)


def _fake_sim_for_hud():
    """A minimal SimResult stand-in HudData can consume: a stream of
    circle judgments with a combo that peaks then breaks (a death)."""
    from osu_std_renderer.ruleset.ruleset import JudgmentEvent, JudgmentKind
    K = JudgmentKind
    # (t, kind, combo, score, acc)
    rows = [
        (100, K.HIT300, 1, 1000, 1.0),
        (200, K.HIT300, 2, 2000, 1.0),
        (300, K.HIT100, 3, 2800, 0.94),
        (400, K.MISS,   0, 2800, 0.80),   # break — combo drops
        (500, K.HIT300, 1, 3800, 0.83),
        (600, K.HIT50,  2, 4000, 0.78),
        (700, K.MISS,   0, 4000, 0.70),   # post-death miss (frozen out)
    ]
    ev = [JudgmentEvent(time_ms=t, kind=k, object_id=i, x=0.0, y=0.0,
                        combo_after=c, score_after=s, acc_after=a)
          for i, (t, k, c, s, a) in enumerate(rows)]

    class _S:
        events = ev
        combo_timeline = [(t, c) for (t, _k, c, _s, _a) in rows]
        final_max_combo = 3
        verdicts = {}
    return _S()


def test_hud_frozen_stats_at_death():
    from osu_std_renderer.render.hud import HudData
    d = HudData(_fake_sim_for_hud())
    # freeze at t=650 (after the 600ms judgment, before the 700ms post-death)
    ft = 650.0
    assert d.counts_at(ft) == (3, 1, 1, 1)      # 300s/100s/50s/misses to death
    assert d.max_combo_upto(ft) == 3            # peak BEFORE the death break
    assert d.score_upto(ft) == 4000             # raw score at death
    # the post-death miss @700 must NOT be included
    assert d.counts_at(ft)[3] == 1              # only the one break-miss @400


def test_hud_max_combo_upto_is_peak_not_final():
    from osu_std_renderer.render.hud import HudData
    d = HudData(_fake_sim_for_hud())
    # even though combo later reaches only 2, the peak up to death is 3
    assert d.max_combo_upto(350.0) == 3
    assert d.max_combo_upto(150.0) == 1


# --- fail sound synth ----------------------------------------------------------------

def test_synth_failsound_shape_and_descends():
    import numpy as np
    from osu_std_renderer.record.hitsounds import synth_failsound
    s = synth_failsound()
    assert s.ndim == 2 and s.shape[1] == 2          # stereo
    assert s.dtype == np.float32
    assert np.max(np.abs(s)) > 0.1                  # audible, not silence
    # descending: the impact/attack is louder than the tail (exp decay)
    head = np.mean(np.abs(s[:len(s) // 5]))
    tail = np.mean(np.abs(s[-len(s) // 5:]))
    assert head > tail


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
