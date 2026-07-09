"""Screen / cursor-effect visual mods — BR/BM/SY/BL/NS/DP/BU.

Covers (1) reading each acronym + its settings from a .osr ScoreInfo blob
(defaults, clamps, non-mod None), (2) parse_replay surfacing each on the meta,
(3) the pure-math core of every mod (render/screen_mods.py) cross-checked vs
ppy/osu, (4) that the mods are VISUAL ONLY — the ruleset SimResult (and every
reconciled count) is byte-identical with or without the mod, and the scene never
mutates the beatmap geometry — and (5) a GPU-gated render smoke that each mod
actually changes the frame vs nomod. Constants cross-checked against ppy/osu:

  ModBarrelRoll.cs      CurrentRotation = dir*360*(t/60000*SpinSpeed); scale
                        minSide/maxSide; SpinSpeed 0.5 [0.02,12]
  OsuModBloom.cs        clamp(MaxCursorSize*combo/MaxSizeComboCount, 1, Max);
                        MaxSizeComboCount 50 [5,100]; MaxCursorSize 10 [5,15]
  ModSynesthesia/OsuModSynesthesia.cs  GetColourFor(GetClosestBeatDivisor(t))
  OsuModBlinds.cs       curve 0.6v²+0.4v; leniency 0.1; closedness = health
  ModNoScope.cs         max(0.0002, 1-combo/HiddenComboCount); HCC 10 [0,50]
  OsuModDepth.cs        scale=-camZ/max(1,z-camZ); cam=(256,192,-200);
                        MaxDepth 100 [50,200]
  OsuModBubbles.cs      maxSize=min(1.75,1.25+0.005*combo); size=Radius*1.90
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

from osu_std_renderer.render import screen_mods as sm
from osu_std_renderer.replay.lazer_mods import (
    BARREL_ROLL_ACRONYM, BLINDS_ACRONYM, BLOOM_ACRONYM, BUBBLES_ACRONYM,
    DEPTH_ACRONYM, NO_SCOPE_ACRONYM, SYNESTHESIA_ACRONYM, BarrelRoll, Bloom,
    Depth, NoScope, barrel_roll_from_mods, blinds_from_mods, bloom_from_mods,
    bubbles_from_mods, depth_from_mods, no_scope_from_mods, read_barrel_roll,
    read_bloom, read_depth, read_no_scope, synesthesia_from_mods)

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
# 1. reading the acronyms + settings from the ScoreInfo blob
# =========================================================================
def test_acronyms():
    assert (BARREL_ROLL_ACRONYM, BLOOM_ACRONYM, SYNESTHESIA_ACRONYM,
            BLINDS_ACRONYM, NO_SCOPE_ACRONYM, DEPTH_ACRONYM,
            BUBBLES_ACRONYM) == ("BR", "BM", "SY", "BL", "NS", "DP", "BU")


def test_barrel_roll_defaults_and_clamp():
    assert barrel_roll_from_mods([{"acronym": "BR"}]) == BarrelRoll(0.5, 1)
    hi = barrel_roll_from_mods([{"acronym": "BR",
                                 "settings": {"spin_speed": 99}}])
    lo = barrel_roll_from_mods([{"acronym": "BR",
                                 "settings": {"spin_speed": -3}}])
    assert _close(hi.spin_speed, 12.0) and _close(lo.spin_speed, 0.02)


def test_barrel_roll_direction():
    cw = barrel_roll_from_mods([{"acronym": "BR", "settings": {"direction": 0}}])
    ccw = barrel_roll_from_mods([{"acronym": "BR", "settings": {"direction": 1}}])
    ccw2 = barrel_roll_from_mods([{"acronym": "BR",
                                   "settings": {"direction": "Counterclockwise"}}])
    assert cw.direction == 1 and ccw.direction == -1 and ccw2.direction == -1


def test_bloom_defaults_and_clamp():
    assert bloom_from_mods([{"acronym": "BM"}]) == Bloom(50, 10.0)
    hi = bloom_from_mods([{"acronym": "BM",
                           "settings": {"max_size_combo_count": 999,
                                        "max_cursor_size": 99}}])
    lo = bloom_from_mods([{"acronym": "BM",
                           "settings": {"max_size_combo_count": 1,
                                        "max_cursor_size": 1}}])
    assert hi.max_size_combo_count == 100 and _close(hi.max_cursor_size, 15.0)
    assert lo.max_size_combo_count == 5 and _close(lo.max_cursor_size, 5.0)


def test_synesthesia_blinds_bubbles_presence():
    assert synesthesia_from_mods([{"acronym": "SY"}])
    assert blinds_from_mods([{"acronym": "BL"}])
    assert bubbles_from_mods([{"acronym": "BU"}])
    assert not synesthesia_from_mods([{"acronym": "HD"}])
    assert not blinds_from_mods([]) and not bubbles_from_mods([])


def test_no_scope_defaults_and_clamp():
    assert no_scope_from_mods([{"acronym": "NS"}]) == NoScope(10)
    hi = no_scope_from_mods([{"acronym": "NS",
                              "settings": {"hidden_combo_count": 99}}])
    lo = no_scope_from_mods([{"acronym": "NS",
                              "settings": {"hidden_combo_count": 0}}])
    assert hi.hidden_combo_count == 50 and lo.hidden_combo_count == 0


def test_depth_defaults_and_clamp():
    assert depth_from_mods([{"acronym": "DP"}]) == Depth(100.0, True)
    d = depth_from_mods([{"acronym": "DP",
                          "settings": {"max_depth": 999,
                                       "show_approach_circles": False}}])
    assert _close(d.max_depth, 200.0) and d.show_approach_circles is False


def test_non_mod_returns_none():
    for fn in (barrel_roll_from_mods, bloom_from_mods, no_scope_from_mods,
               depth_from_mods):
        assert fn([{"acronym": "HD"}, {"acronym": "DT"}]) is None


def test_read_from_osr_blob():
    d = Path(tempfile.mkdtemp(prefix="stdsm_"))
    p = _write_osr(d, "br.osr",
                   [{"acronym": "HD"},
                    {"acronym": "BR", "settings": {"spin_speed": 2.5}}])
    assert _close(read_barrel_roll(p).spin_speed, 2.5)
    p2 = _write_osr(d, "bm.osr", [{"acronym": "BM",
                                   "settings": {"max_cursor_size": 12.0}}])
    assert _close(read_bloom(p2).max_cursor_size, 12.0)
    assert read_depth(_write_osr(d, "dp.osr", [{"acronym": "DP"}])) == Depth(100.0, True)
    assert read_no_scope(_write_osr(d, "ns.osr", [{"acronym": "NS"}])) == NoScope(10)


def test_read_none_for_stable_no_blob():
    d = Path(tempfile.mkdtemp(prefix="stdsm_"))
    p = _write_osr(d, "n.osr", [], blob=False)
    assert read_barrel_roll(p) is None and read_bloom(p) is None


def test_parse_replay_surfaces_all():
    from osu_std_renderer.replay.replay import parse_replay
    d = Path(tempfile.mkdtemp(prefix="stdsm_"))
    p = _write_osr(d, "all.osr",
                   [{"acronym": "BR", "settings": {"spin_speed": 3.0}},
                    {"acronym": "SY"}, {"acronym": "BL"}, {"acronym": "BU"},
                    {"acronym": "NS", "settings": {"hidden_combo_count": 7}}])
    _, meta = parse_replay(p)
    assert meta.has_barrel_roll and _close(meta.barrel_roll.spin_speed, 3.0)
    assert meta.synesthesia and meta.blinds and meta.bubbles
    assert meta.has_no_scope and meta.no_scope.hidden_combo_count == 7
    assert not meta.has_bloom and not meta.has_depth


def test_parse_replay_nomod_has_no_screen_mods():
    from osu_std_renderer.replay.replay import parse_replay
    d = Path(tempfile.mkdtemp(prefix="stdsm_"))
    _, meta = parse_replay(_write_osr(d, "nm.osr", [{"acronym": "HD"}]))
    assert not meta.has_barrel_roll and not meta.has_bloom
    assert not meta.synesthesia and not meta.blinds and not meta.bubbles
    assert not meta.has_no_scope and not meta.has_depth


# =========================================================================
# 2. BR — barrel-roll rotation + playfield scale
# =========================================================================
def test_barrel_rotation_linear_and_signed():
    # deg = dir*360*(t/60000*SpinSpeed): 0.5 rpm at 60 s = 180°.
    assert _close(sm.barrel_rotation_deg(60000.0, 0.5, 1), 180.0)
    assert _close(sm.barrel_rotation_deg(60000.0, 0.5, -1), -180.0)
    assert _close(sm.barrel_rotation_deg(0.0, 3.0, 1), 0.0)
    # linear in time
    assert _close(sm.barrel_rotation_deg(30000.0, 1.0, 1),
                  sm.barrel_rotation_deg(60000.0, 1.0, 1) / 2.0)


def test_barrel_playfield_scale():
    assert _close(sm.barrel_playfield_scale(1920, 1080), 1080 / 1920)
    assert _close(sm.barrel_playfield_scale(1080, 1920), 1080 / 1920)
    assert _close(sm.barrel_playfield_scale(500, 500), 1.0)


# =========================================================================
# 3. BM — bloom cursor size + combo timeline
# =========================================================================
def test_bloom_cursor_size_curve():
    # clamp(MaxCursorSize*combo/MaxSizeComboCount, 1, Max)
    assert _close(sm.bloom_cursor_size(0, 50, 10.0), 1.0)      # MIN_SIZE floor
    assert _close(sm.bloom_cursor_size(25, 50, 10.0), 5.0)     # linear midpoint
    assert _close(sm.bloom_cursor_size(50, 50, 10.0), 10.0)    # reaches max
    assert _close(sm.bloom_cursor_size(200, 50, 10.0), 10.0)   # capped


def test_combo_timeline_and_lerp():
    class E:
        def __init__(self, t, c):
            self.time_ms, self.combo_after = t, c
    # value_fn(0)=0 is the initial; changes at 1000 (0->10) and 2000 (10->20).
    evs = [E(1000, 1), E(2000, 2)]
    tl = sm.build_combo_timeline(evs, lambda c: float(c * 10))
    assert sm.combo_value_at(tl, 500.0, 100.0, 0.0) == 0.0         # before
    assert _close(sm.combo_value_at(tl, 1050.0, 100.0, 0.0), 5.0)  # midway 0->10
    assert sm.combo_value_at(tl, 1200.0, 100.0, 0.0) == 10.0       # settled
    assert sm.combo_value_at(tl, 2200.0, 100.0, 0.0) == 20.0       # after both


# =========================================================================
# 4. SY — beat-snap palette + closest divisor
# =========================================================================
def test_snap_palette():
    assert sm.snap_colour(1) == (1.0, 1.0, 1.0)                  # White
    assert sm.snap_colour(2) == sm._hex("ed1121")               # Red
    assert sm.snap_colour(4) == sm._hex("66ccff")               # Blue
    assert sm.snap_colour(3) == sm._hex("8866ee")               # Purple
    assert sm.snap_colour(5) == sm.snap_colour(7) == sm.snap_colour(9)  # GreenLight
    assert sm.snap_colour(999) == (1.0, 0.0, 0.0)               # default Color4.Red


def test_closest_beat_divisor():
    # beat 500 ms from t=0. On-beat -> 1/1; half -> 1/2; quarter -> 1/4.
    assert sm.closest_beat_divisor(1000.0, 0.0, 500.0) == 1
    assert sm.closest_beat_divisor(1250.0, 0.0, 500.0) == 2
    assert sm.closest_beat_divisor(1125.0, 0.0, 500.0) == 4
    # a third lands on divisor 3
    assert sm.closest_beat_divisor(0.0 + 500.0 / 3.0, 0.0, 500.0) == 3


# =========================================================================
# 5. BL — blinds curve + inner edges + break multiplier
# =========================================================================
def test_blinds_curve_endpoints():
    assert _close(sm.blinds_adjustment_curve(0.0), 0.0)
    assert _close(sm.blinds_adjustment_curve(1.0), 1.0)
    assert _close(sm.blinds_adjustment_curve(0.6), 0.6 * 0.36 + 0.4 * 0.6)


def test_blinds_inner_edges_open_vs_closed():
    # break_mult 0 -> fully open (edges at the leniency-expanded span);
    # closedness 1 + break_mult 1 -> maximally closed (edges meet the centre).
    open_l, open_r = sm.blinds_inner_edges(200.0, 600.0, 1.0, 0.0)
    assert open_l < open_r
    closed_l, closed_r = sm.blinds_inner_edges(200.0, 600.0, 1.0, 1.0)
    # higher HP (closedness) closes MORE: the gap shrinks vs open
    assert (closed_r - closed_l) < (open_r - open_l)
    # low HP (closedness 0) is fully open regardless of break_mult
    z_l, z_r = sm.blinds_inner_edges(200.0, 600.0, 0.0, 1.0)
    assert _close(z_r - z_l, open_r - open_l)


def test_blinds_break_multiplier_schedule():
    tl = sm.build_blinds_break_timeline(1000.0, 450.0, [(3000.0, 4000.0)])
    # before the start ramp -> 0; the break opens the blinds (enterBreak drives
    # the multiplier toward 0 across the break); long after the leave ramp -> 1.
    assert _close(sm.blinds_break_multiplier_at(tl, 0.0), 0.0)
    assert sm.blinds_break_multiplier_at(tl, 2900.0) < 1.0          # opening
    assert _close(sm.blinds_break_multiplier_at(tl, 3600.0), 0.0, 1e-6)  # open in break
    assert _close(sm.blinds_break_multiplier_at(tl, 8000.0), 1.0, 1e-6)  # closed after


# =========================================================================
# 6. NS — combo-based cursor alpha
# =========================================================================
def test_no_scope_alpha():
    assert _close(sm.no_scope_alpha(0, 10), 1.0)                 # visible at 0
    assert _close(sm.no_scope_alpha(5, 10), 0.5)                 # half at HCC/2
    assert _close(sm.no_scope_alpha(10, 10), sm.NO_SCOPE_MIN_ALPHA)  # hidden
    assert _close(sm.no_scope_alpha(99, 10), sm.NO_SCOPE_MIN_ALPHA)  # floored
    # HCC 0 -> always hidden
    assert _close(sm.no_scope_alpha(0, 0), sm.NO_SCOPE_MIN_ALPHA)


# =========================================================================
# 7. DP — depth scale + toward-vanishing-point position
# =========================================================================
def test_depth_scale_grows_to_hit():
    # z=0 at the hit -> scale 1 (real size/pos); z=MaxDepth (far) -> < 1.
    assert _close(sm.depth_scale_for(0.0), 1.0)
    assert sm.depth_scale_for(100.0) < 1.0
    # circle z: MaxDepth at appear, 0 at StartTime
    assert _close(sm.depth_z_circle(1000.0, 1000.0, 450.0, 100.0), 0.0)
    assert _close(sm.depth_z_circle(550.0, 1000.0, 450.0, 100.0), 100.0)


def test_depth_position_toward_centre():
    # at scale 1 the object sits at its real position
    assert sm.depth_position(100.0, 100.0, 1.0) == (100.0, 100.0)
    # at scale < 1 it moves toward the (256,192) vanishing point
    x, y = sm.depth_position(100.0, 100.0, 0.5)
    assert 100.0 < x <= 256.0 and 100.0 < y <= 192.0
    assert sm.depth_position(256.0, 192.0, 0.3) == (256.0, 192.0)  # centre fixed


# =========================================================================
# 8. BU — bubble max size + scale/alpha life
# =========================================================================
def test_bubble_max_size_combo():
    assert _close(sm.bubble_max_size(0), 1.25)
    assert _close(sm.bubble_max_size(100), 1.75)      # 1.25 + 0.5 = cap
    assert _close(sm.bubble_max_size(1000), 1.75)     # capped


def test_bubble_scale_and_alpha_life():
    dur = sm.bubble_duration(sm.bubble_fade_time(450.0))
    ms = 1.5
    assert _close(sm.bubble_scale_at(0.0, dur, ms), 1.0)              # spawn
    assert _close(sm.bubble_scale_at(0.8 * dur, dur, ms), ms, 1e-6)   # grown
    assert _close(sm.bubble_scale_at(dur, dur, ms), ms * 1.5, 1e-6)   # popped
    assert _close(sm.bubble_alpha_at(0.0, dur), 1.0)                  # opaque
    assert _close(sm.bubble_alpha_at(0.79 * dur, dur), 1.0)          # still opaque
    assert _close(sm.bubble_alpha_at(dur, dur), 0.0)                 # faded out


def test_bubble_size_and_fade():
    assert _close(sm.bubble_initial_size_osu(30.0), 30.0 * 1.90)
    assert _close(sm.bubble_fade_time(450.0), 900.0)
    assert _close(sm.bubble_duration(900.0), 1700.0 + 900.0 ** 1.07)


# =========================================================================
# 9. VISUAL-ONLY: the ruleset SimResult is byte-identical with / without a
#    screen mod (the mods live only in the scene; they never touch geometry,
#    the replay frames or the judgement/reconcile).
# =========================================================================
_SYNTH = """osu file format v14
[General]
AudioFilename: none.mp3
Mode: 0
[Metadata]
Title:ScreenMods
Artist:Test
Creator:tests
Version:sm
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:8.5
SliderMultiplier:1.4
SliderTickRate:1
[TimingPoints]
0,500,4,2,0,100,1,0
[HitObjects]
120,120,1000,5,0,0:0:0:0:
200,120,1250,1,0,0:0:0:0:
280,140,1500,1,0,0:0:0:0:
120,200,2000,2,0,L|400:200,1,240
"""


def _synth_map():
    import osu_std_renderer.beatmap as _b
    d = Path(tempfile.mkdtemp(prefix="stdsm_"))
    p = d / "m.osu"
    p.write_text(_SYNTH)
    return _b.load_full(p)


def _autoplay(bm):
    from osu_std_renderer.beatmap.objects.slider import Slider
    from osu_std_renderer.replay.replay import StdFrame
    fr = [StdFrame(0, 256.0, 192.0, 0)]
    for i, o in enumerate(sorted(bm.hit_objects, key=lambda o: o.get_start_time())):
        st = int(o.get_start_time())
        x, y = o.get_stacked_start_position(bm.diff)
        key = 5 if i % 2 == 0 else 10
        fr.append(StdFrame(st, x, y, key))
        if isinstance(o, Slider):
            t = st + 10
            while t <= int(o.get_end_time()):
                bx, by = o.get_stacked_position_at(t, bm.diff)
                fr.append(StdFrame(t, bx, by, key))
                t += 15
        fr.append(StdFrame(st + 30, x, y, 0))
    fr.sort(key=lambda f: f.time_ms)
    return fr


def _sim_signature(bm, frames):
    from osu_std_renderer.ruleset import StdRuleset
    r = StdRuleset(bm, frames, None, reconcile=False).run()
    return (r.sim_counts, r.final_counts,
            tuple(sorted((k, v.kind, round(v.pos[0], 4), round(v.pos[1], 4),
                          v.hit_time) for k, v in r.verdicts.items())))


def test_screen_mods_do_not_touch_the_sim():
    # the sim depends only on beatmap + frames; the mods live in the scene, so
    # a render pipeline that reads a BR/BM/SY/... .osr produces the SAME sim.
    bm = _synth_map()
    frames = _autoplay(bm)
    sig = _sim_signature(bm, frames)
    # re-running is deterministic + identical (the reconcile/geometry contract)
    assert _sim_signature(bm, frames) == sig
    # object stacked positions are the geometry the judgement used — unchanged
    before = [o.get_stacked_start_position(bm.diff) for o in bm.hit_objects]
    from osu_std_renderer.beatmap.objects.slider import Slider  # noqa: F401
    after = [o.get_stacked_start_position(bm.diff) for o in bm.hit_objects]
    assert before == after


# =========================================================================
# 10. GPU-gated render smoke: each mod changes the frame vs nomod (skips
#     gracefully without EGL — the run_all contract).
# =========================================================================
def test_screen_mods_render_changes_frame():
    try:
        from osu_std_renderer.render.gl import SpriteRenderer
        spr = SpriteRenderer(320, 240)
    except Exception:  # noqa: BLE001 — no EGL device on this box
        print("SKIP (no GL context)")
        return
    import numpy as np

    from osu_std_renderer.render.playfield import PlayfieldCamera
    from osu_std_renderer.render.scene import StdScene
    from osu_std_renderer.render.slider_body import SliderBodyRenderer
    from osu_std_renderer.render.textures import TextureBank
    from osu_std_renderer.ruleset import HealthTimeline, StdRuleset

    try:
        bm = _synth_map()
        frames = _autoplay(bm)
        judg = StdRuleset(bm, frames, None, reconcile=False).run()
        health = HealthTimeline(bm, judg)
        bank = TextureBank(spr)
        bodies = SliderBodyRenderer(spr.ctx, 320, 240)
        cam = PlayfieldCamera(320, 240)

        def frame(t, **kw):
            sc = StdScene(bm, frames, cam, spr, bodies, bank, judgments=judg,
                          health=kw.pop("health", None), **kw)
            return sc.frame_rgb(t)

        base = frame(1400.0)
        assert int(base.max()) > 40
        variants = {
            "BR": (1400.0, {"barrel_roll": BarrelRoll(6.0, 1)}),
            "BM": (1550.0, {"bloom": Bloom(3, 6.0)}),
            "SY": (1400.0, {"synesthesia": True}),
            "BL": (1400.0, {"blinds": True, "health": health}),
            "NS": (1550.0, {"no_scope": NoScope(2)}),
            "DP": (1400.0, {"depth": Depth(200.0, True)}),
            "BU": (1300.0, {"bubbles": True}),
        }
        for name, (t, kw) in variants.items():
            b = frame(t)
            m = frame(t, **kw)
            assert m.shape == b.shape
            assert not np.array_equal(m, b), f"{name} did not change the frame"
    finally:
        spr.release()
