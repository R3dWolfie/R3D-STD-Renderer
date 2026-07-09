"""Transform-family "fun" mods — GR/DF/SI/WG/TR (per-object entrance animation).

Covers (1) reading the acronym + settings from a .osr ScoreInfo blob, (2) each
mod's transform math at spawn / mid / hit (start state -> identity at the hit
time), (3) that the mods are VISUAL ONLY (WG/TR touch only the position offset,
never a scale/rotation of a HITTABLE quantity; the judged positions are
untouched), and (4) that a non-transform replay is unchanged. Constants
cross-checked against ppy/osu:

  OsuModObjectScaleTween.cs  ScaleTo(StartScale).Then().ScaleTo(1, Preempt, OutSine)
  OsuModGrow.cs              StartScale 0.5 [0, 0.99]
  OsuModDeflate.cs           StartScale 2.0 [1, 25]
  OsuModSpinIn.cs            rotate_offset 360, start (2,0), InOutSine over Preempt
  OsuModWiggle.cs            wiggle_duration 100, Strength 1 [0.1, 2], dist=r·Str·7
  OsuModTransform.cs         appearDistance (Preempt-FadeIn)/2, InOutSine over Preempt+1
"""
from __future__ import annotations

import json
import lzma
import math
import struct
import tempfile
from datetime import datetime
from pathlib import Path

from osrparse import GameMode, Key, Mod, Replay
from osrparse.replay import ReplayEventOsu

from osu_std_renderer.beatmap.dotnet_random import DotNetRandom
from osu_std_renderer.replay.lazer_mods import (
    TRANSFORM_MODS, TransformMod, read_transform, transform_from_mods)
from osu_std_renderer.render import transform_mods as tm

LAZER_GV = 30_000_016


def _close(a: float, b: float, eps: float = 1e-9) -> bool:
    return abs(a - b) <= eps


def _write_osr(dirpath: Path, name: str, mods_json, blob: bool = True) -> Path:
    ev = [ReplayEventOsu(0, 5.0, 5.0, Key(0)),
          ReplayEventOsu(10, 6.0, 6.0, Key.K1),
          ReplayEventOsu(-12345, 0.0, 0.0, Key(0))]
    r = Replay(GameMode.STD, LAZER_GV, "abc", "tester", "h",
               1, 0, 0, 0, 0, 0, 12345, 1, False, Mod.NoMod, None,
               datetime(2026, 1, 1), ev, 0, None)
    base = r.pack()
    if blob:
        raw = json.dumps({"mods": mods_json, "rank": "S",
                          "statistics": {}}).encode("utf-8")
        comp = lzma.compress(raw, format=lzma.FORMAT_ALONE)
        base = base + struct.pack("<i", len(comp)) + comp
    p = dirpath / name
    p.write_bytes(base)
    return p


# =========================================================================
# 1. reading the acronym + settings from the ScoreInfo blob
# =========================================================================
def test_transform_mods_acronym_set():
    assert TRANSFORM_MODS == {"GR", "DF", "SI", "WG", "TR"}


def test_grow_default_start_scale():
    t = transform_from_mods([{"acronym": "GR", "settings": {}}])
    assert t == TransformMod("GR", start_scale=0.5, strength=1.0)


def test_deflate_default_start_scale():
    t = transform_from_mods([{"acronym": "DF"}])
    assert t.acronym == "DF" and _close(t.start_scale, 2.0)


def test_grow_custom_start_scale_read():
    t = transform_from_mods([{"acronym": "GR", "settings": {"start_scale": 0.2}}])
    assert _close(t.start_scale, 0.2)


def test_grow_start_scale_clamped_to_range():
    # GR range [0, 0.99]
    hi = transform_from_mods([{"acronym": "GR", "settings": {"start_scale": 5.0}}])
    lo = transform_from_mods([{"acronym": "GR", "settings": {"start_scale": -3.0}}])
    assert _close(hi.start_scale, 0.99) and _close(lo.start_scale, 0.0)


def test_deflate_start_scale_clamped_to_range():
    # DF range [1, 25]
    hi = transform_from_mods([{"acronym": "DF", "settings": {"start_scale": 99.0}}])
    lo = transform_from_mods([{"acronym": "DF", "settings": {"start_scale": 0.1}}])
    assert _close(hi.start_scale, 25.0) and _close(lo.start_scale, 1.0)


def test_wiggle_strength_read_and_clamped():
    t = transform_from_mods([{"acronym": "WG", "settings": {"strength": 1.6}}])
    assert _close(t.strength, 1.6)
    hi = transform_from_mods([{"acronym": "WG", "settings": {"strength": 9.0}}])
    lo = transform_from_mods([{"acronym": "WG", "settings": {"strength": 0.0}}])
    assert _close(hi.strength, 2.0) and _close(lo.strength, 0.1)


def test_spin_in_and_transform_have_no_settings():
    for acr in ("SI", "TR"):
        t = transform_from_mods([{"acronym": acr}])
        assert t.acronym == acr and _close(t.start_scale, 1.0) \
            and _close(t.strength, 1.0)


def test_non_transform_mod_returns_none():
    assert transform_from_mods([{"acronym": "HD"}, {"acronym": "DT"}]) is None
    assert transform_from_mods([]) is None


def test_bool_start_scale_rejected_falls_back_to_default():
    # a stray JSON bool must not read back as start_scale 1.0
    t = transform_from_mods([{"acronym": "GR",
                              "settings": {"start_scale": True}}])
    assert _close(t.start_scale, 0.5)


def test_first_transform_wins():
    # the five are mutually incompatible; take the first match
    t = transform_from_mods([{"acronym": "TR"}, {"acronym": "GR"}])
    assert t.acronym == "TR"


def test_read_transform_from_osr_blob():
    d = Path(tempfile.mkdtemp(prefix="stdtr_"))
    p = _write_osr(d, "df.osr",
                   [{"acronym": "HD"},
                    {"acronym": "DF", "settings": {"start_scale": 3.0}}])
    t = read_transform(p)
    assert t.acronym == "DF" and _close(t.start_scale, 3.0)


def test_read_transform_none_for_stable_no_blob():
    d = Path(tempfile.mkdtemp(prefix="stdtr_"))
    p = _write_osr(d, "n.osr", [], blob=False)
    assert read_transform(p) is None


def test_parse_replay_surfaces_transform():
    from osu_std_renderer.replay.replay import parse_replay
    d = Path(tempfile.mkdtemp(prefix="stdtr_"))
    p = _write_osr(d, "wg.osr", [{"acronym": "WG",
                                  "settings": {"strength": 1.5}}])
    _, meta = parse_replay(p)
    assert meta.has_transform and meta.transform_acronym == "WG"
    assert _close(meta.transform_strength, 1.5)


def test_parse_replay_nomod_has_no_transform():
    from osu_std_renderer.replay.replay import parse_replay
    d = Path(tempfile.mkdtemp(prefix="stdtr_"))
    p = _write_osr(d, "nm.osr", [{"acronym": "HD"}])
    _, meta = parse_replay(p)
    assert not meta.has_transform and meta.transform_acronym == ""
    assert _close(meta.transform_start_scale, 1.0)
    assert _close(meta.transform_strength, 1.0)


# =========================================================================
# 2. easings
# =========================================================================
def test_ease_out_sine_endpoints():
    assert _close(tm.ease_out_sine(0.0), 0.0)
    assert _close(tm.ease_out_sine(1.0), 1.0)
    assert _close(tm.ease_out_sine(0.5), math.sin(math.pi / 4))


def test_ease_in_out_sine_endpoints_and_midpoint():
    assert _close(tm.ease_in_out_sine(0.0), 0.0)
    assert _close(tm.ease_in_out_sine(1.0), 1.0)
    assert _close(tm.ease_in_out_sine(0.5), 0.5)


def test_easings_clamp_out_of_range():
    assert _close(tm.ease_out_sine(-1.0), 0.0)
    assert _close(tm.ease_out_sine(2.0), 1.0)
    assert _close(tm.ease_in_out_sine(-1.0), 0.0)
    assert _close(tm.ease_in_out_sine(5.0), 1.0)


# =========================================================================
# 3. GR / DF scale: start state -> identity at the hit time
# =========================================================================
START, PREEMPT, FADE_IN = 1000.0, 540.0, 400.0
SPAWN = START - PREEMPT          # 460
HIT = START                      # scale reaches 1 at StartTime


def test_grow_scale_spawn_mid_hit():
    ss = 0.5
    assert _close(tm.grow_deflate_scale(SPAWN, SPAWN, PREEMPT, ss), ss)      # start small
    assert _close(tm.grow_deflate_scale(HIT, SPAWN, PREEMPT, ss), 1.0)       # identity at hit
    mid = tm.grow_deflate_scale(SPAWN + PREEMPT / 2, SPAWN, PREEMPT, ss)
    assert ss < mid < 1.0                                                    # grows monotonically


def test_deflate_scale_spawn_mid_hit():
    ss = 2.0
    assert _close(tm.grow_deflate_scale(SPAWN, SPAWN, PREEMPT, ss), ss)      # start large
    assert _close(tm.grow_deflate_scale(HIT, SPAWN, PREEMPT, ss), 1.0)       # identity at hit
    mid = tm.grow_deflate_scale(SPAWN + PREEMPT / 2, SPAWN, PREEMPT, ss)
    assert 1.0 < mid < ss                                                    # shrinks monotonically


def test_grow_scale_holds_identity_after_hit():
    assert _close(tm.grow_deflate_scale(HIT + 500.0, SPAWN, PREEMPT, 0.5), 1.0)


def test_grow_uses_out_sine_curve():
    ss = 0.5
    p = 0.3
    got = tm.grow_deflate_scale(SPAWN + p * PREEMPT, SPAWN, PREEMPT, ss)
    want = ss + (1.0 - ss) * tm.ease_out_sine(p)
    assert _close(got, want, 1e-9)


# =========================================================================
# 3b. SI spin-in: circle (2,0)+360deg and slider grow-from-0
# =========================================================================
def test_spin_in_circle_spawn_state():
    sx, sy, rot = tm.spin_in_circle(SPAWN, SPAWN, PREEMPT)
    assert _close(sx, 2.0) and _close(sy, 0.0)
    assert _close(rot, math.radians(360.0))


def test_spin_in_circle_identity_at_hit():
    sx, sy, rot = tm.spin_in_circle(HIT, SPAWN, PREEMPT)
    assert _close(sx, 1.0) and _close(sy, 1.0) and _close(rot, 0.0)


def test_spin_in_circle_uses_in_out_sine():
    p = 0.4
    sx, sy, rot = tm.spin_in_circle(SPAWN + p * PREEMPT, SPAWN, PREEMPT)
    e = tm.ease_in_out_sine(p)
    assert _close(sx, 2.0 - e) and _close(sy, e)
    assert _close(rot, math.radians(360.0) * (1.0 - e))


def test_spin_in_slider_grows_from_zero_no_rotation():
    assert _close(tm.spin_in_slider_scale(SPAWN, SPAWN, PREEMPT), 0.0)
    assert _close(tm.spin_in_slider_scale(HIT, SPAWN, PREEMPT), 1.0)
    mid = tm.spin_in_slider_scale(SPAWN + PREEMPT / 2, SPAWN, PREEMPT)
    assert _close(mid, tm.ease_in_out_sine(0.5))


# =========================================================================
# 3c. TR transform: fly-in offset -> 0 at StartTime
# =========================================================================
def test_transform_appear_offset_magnitude_and_angle():
    theta = 1.234
    ox, oy = tm.transform_appear_offset(theta, PREEMPT, FADE_IN)
    appear_distance = (PREEMPT - FADE_IN) / 2.0
    assert _close(math.hypot(ox, oy), appear_distance)
    assert _close(math.atan2(oy, ox), theta)


def test_transform_offset_full_at_appear_and_zero_at_start():
    ox0, oy0 = 30.0, -12.0
    appear_time = START - PREEMPT - 1.0
    move_dur = PREEMPT + 1.0
    ax, ay = tm.transform_offset_at(appear_time, appear_time, move_dur, ox0, oy0)
    assert _close(ax, ox0) and _close(ay, oy0)              # full offset at appear
    # reaches origin exactly at StartTime (appear_time + move_dur)
    zx, zy = tm.transform_offset_at(appear_time + move_dur, appear_time,
                                    move_dur, ox0, oy0)
    assert _close(zx, 0.0) and _close(zy, 0.0)              # identity at StartTime


def test_transform_offset_decays_via_in_out_sine():
    ox0, oy0 = 40.0, 0.0
    appear_time, move_dur = 0.0, 540.0
    p = 0.25
    ax, _ = tm.transform_offset_at(p * move_dur, appear_time, move_dur, ox0, oy0)
    assert _close(ax, ox0 * (1.0 - tm.ease_in_out_sine(p)))


# =========================================================================
# 3d. WG wiggle: deterministic seeded keyframes, offset chain
# =========================================================================
def test_wiggle_seed_is_int_start_time():
    # the keyframe stream must match a fresh DotNetRandom(int(start))
    start, preempt = 1234.0, 540.0
    ev = tm.wiggle_events(start, preempt, 0.0, 1.0)
    rng = DotNetRandom(int(start))
    for (_, gx, gy) in ev:
        angle = rng.next_double() * 2.0 * math.pi
        dist = rng.next_double() * 1.0 * 7.0
        assert _close(gx, dist * math.cos(angle), 1e-9)
        assert _close(gy, dist * math.sin(angle), 1e-9)


def test_wiggle_preempt_keyframe_count():
    # (int)TimePreempt / 100 keyframes for a hit circle (no duration)
    ev = tm.wiggle_events(1000.0, 540.0, 0.0, 1.0)
    assert len(ev) == 5                     # 540 // 100
    ev2 = tm.wiggle_events(1000.0, 900.0, 0.0, 1.0)
    assert len(ev2) == 9


def test_wiggle_slider_adds_duration_keyframes():
    # sliders/spinners keep wiggling for their Duration too
    circle = tm.wiggle_events(1000.0, 500.0, 0.0, 1.0)
    slider = tm.wiggle_events(1000.0, 500.0, 300.0, 1.0)
    assert len(circle) == 5
    assert len(slider) == 5 + 3             # + (int)(300/100)


def test_wiggle_offset_zero_before_spawn_and_deterministic():
    start, preempt = 1000.0, 540.0
    ev = tm.wiggle_events(start, preempt, 0.0, 1.0)
    spawn = start - preempt
    # before the first MoveTo the object sits at the origin (no offset)
    assert tm.wiggle_offset_at(ev, spawn - 50.0) == (0.0, 0.0)
    # deterministic: same seed -> identical offset at any time
    ev2 = tm.wiggle_events(start, preempt, 0.0, 1.0)
    for t in (spawn + 40.0, spawn + 250.0, start):
        assert tm.wiggle_offset_at(ev, t) == tm.wiggle_offset_at(ev2, t)


def test_wiggle_offset_bounded_by_strength():
    # |offset| <= Strength * 7 osu!px at every keyframe
    strength = 1.5
    ev = tm.wiggle_events(1000.0, 540.0, 0.0, strength)
    for (_, gx, gy) in ev:
        assert math.hypot(gx, gy) <= strength * 7.0 + 1e-9


def test_wiggle_first_segment_interpolates_from_origin():
    ev = tm.wiggle_events(1000.0, 540.0, 0.0, 1.0)
    spawn = 1000.0 - 540.0
    t0, gx, gy = ev[0]
    # halfway through the first 100 ms MoveTo -> half of the first target
    hx, hy = tm.wiggle_offset_at(ev, t0 + 50.0)
    assert _close(hx, gx * 0.5, 1e-9) and _close(hy, gy * 0.5, 1e-9)


# =========================================================================
# 4. VISUAL-ONLY: WG/TR are offset-only; GR/DF/SI are scale/rot-only.
#    None of them alters a judged POSITION (the offset never feeds geometry).
# =========================================================================
def test_offset_mods_carry_only_offset():
    # WG/TR ObjTransform must be pure translation (scale/rotation identity)
    ot = tm.ObjTransform(off_x=3.0, off_y=-4.0)
    assert ot.scale_x == 1.0 and ot.scale_y == 1.0 and ot.rotation == 0.0
    assert not ot.force_opaque


def test_scale_mods_carry_no_offset():
    # GR/DF/SI ObjTransform must not move the object (offset identity)
    ot = tm.ObjTransform(scale_x=0.5, scale_y=0.5)
    assert ot.off_x == 0.0 and ot.off_y == 0.0


def test_hides_approach_membership():
    # GR/DF/SI hide the approach circle; WG/TR do not
    assert tm.HIDES_APPROACH == {"GR", "DF", "SI"}
    assert "WG" not in tm.HIDES_APPROACH and "TR" not in tm.HIDES_APPROACH


def test_identity_is_noop():
    i = tm.IDENTITY
    assert (i.scale_x, i.scale_y, i.rotation, i.off_x, i.off_y,
            i.force_opaque) == (1.0, 1.0, 0.0, 0.0, 0.0, False)
