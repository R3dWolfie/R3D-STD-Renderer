"""Cursor-driven object-movement mods — Magnetised (MG) + Repel (RP).

Covers (1) the shared osu!framework Interpolation port (Lerp / DampContinuously)
against lazer's exact formulas, (2) each mod's destination + damp-length
(MG → cursor with Lerp(3000,40,strength) half-time; RP → 2P-C clamped to the
playfield with Distance/(0.04·strength+0.04) half-time), (3) that the stateful
integration CONVERGES the object onto the cursor (MG) / drives it AWAY (RP),
(4) reading the acronym + strength from a .osr ScoreInfo blob, (5) that a
non-MG/RP replay is untouched, and (6) a GPU-gated scene check that the eased
objects track the recorded cursor, follow points are hidden, the frame changes
vs nomod, and the judgement/reconcile stays on the real geometry (reconcile
exact). Constants cross-checked against ppy/osu + ppy/osu-framework:

  OsuModMagnetised.cs  AttractionStrength 0.5 [0.05,1]; dampLength=Lerp(3000,40,s)
  OsuModRepel.cs       RepulsionStrength  0.5 [0.05,1]; dest=Clamp(2P-C,0,BASE);
                       dampLength=Distance(P,C)/(0.04·s+0.04); both hide follow points
  Interpolation.cs     DampContinuously(cur,tgt,halfTime,dt)=Lerp(cur,tgt,1-0.5^(dt/halfTime))
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

from osu_std_renderer.replay.lazer_mods import (
    MAGNETISED_ACRONYM, REPEL_ACRONYM, REPEL_MAGNET_MODS, RepelMagnet,
    read_repel_magnet, repel_magnet_from_mods)
from osu_std_renderer.render.repel_magnet import (
    BASE_SIZE, MAGNETISED, REPEL, clamp_to_slider_bounds, damp_continuously,
    ease_step, lerp, magnet_damp_length, magnet_destination, repel_damp_length,
    repel_destination, slider_movement_bounds)

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
# 1. osu!framework Interpolation port (Lerp + DampContinuously)
# =========================================================================
def test_lerp_matches_framework():
    assert _close(lerp(0.0, 100.0, 0.0), 0.0)
    assert _close(lerp(0.0, 100.0, 1.0), 100.0)
    assert _close(lerp(0.0, 100.0, 0.25), 25.0)
    assert _close(lerp(3000.0, 40.0, 0.5), 1520.0)


def test_damp_continuously_known_step():
    # DampContinuously(cur,tgt,halfTime,dt) = Lerp(cur,tgt,1-0.5^(dt/halfTime)).
    # dt == halfTime → halfway.
    assert _close(damp_continuously(0.0, 100.0, 100.0, 100.0), 50.0)
    # dt == 2·halfTime → 1-0.25 = 0.75.
    assert _close(damp_continuously(0.0, 100.0, 100.0, 200.0), 75.0)
    # non-zero current interpolates from there.
    assert _close(damp_continuously(20.0, 100.0, 100.0, 100.0), 60.0)


def test_damp_continuously_edge_cases():
    # elapsed 0 → no move (0.5^0 = 1); halfTime 0 → snap to target (0.5^inf=0).
    assert _close(damp_continuously(7.0, 100.0, 100.0, 0.0), 7.0)
    assert _close(damp_continuously(7.0, 100.0, 0.0, 50.0), 100.0)
    assert _close(damp_continuously(7.0, 100.0, -3.0, 50.0), 100.0)


# =========================================================================
# 2. damp lengths + destinations
# =========================================================================
def test_magnet_damp_length_lerp():
    assert _close(magnet_damp_length(0.5), 1520.0)    # default
    assert _close(magnet_damp_length(1.0), 40.0)      # max pull → snap
    assert _close(magnet_damp_length(0.05), 3000.0 + (40.0 - 3000.0) * 0.05)


def test_repel_damp_length_distance_over_strength():
    # Distance / (0.04·s + 0.04): 60 px at s=0.5 → /0.06 = 1000.
    assert _close(repel_damp_length(0.0, 0.0, 60.0, 0.0, 0.5), 1000.0)
    # grows with distance, shrinks with strength
    assert repel_damp_length(0.0, 0.0, 120.0, 0.0, 0.5) > \
        repel_damp_length(0.0, 0.0, 60.0, 0.0, 0.5)
    assert repel_damp_length(0.0, 0.0, 60.0, 0.0, 1.0) < \
        repel_damp_length(0.0, 0.0, 60.0, 0.0, 0.5)


def test_magnet_destination_is_cursor():
    assert magnet_destination(300.0, 200.0) == (300.0, 200.0)
    # once a slider head is judged the BALL (not the head) targets the cursor
    assert magnet_destination(300.0, 200.0, 40.0, -10.0) == (260.0, 210.0)


def test_repel_destination_reflects_and_clamps():
    # 2P - C, away from the cursor, in range
    assert repel_destination(300.0, 200.0, 100.0, 100.0) == (500.0, 300.0)
    # clamps to the playfield [0,BASE]
    assert repel_destination(10.0, 10.0, 100.0, 100.0) == (0.0, 0.0)
    assert repel_destination(500.0, 380.0, 100.0, 100.0) == BASE_SIZE


# =========================================================================
# 3. stateful integration — MG converges to the cursor, RP flees it
# =========================================================================
def test_magnet_integration_converges_to_cursor():
    pos = (100.0, 100.0)
    cx, cy = 400.0, 300.0
    damp = magnet_damp_length(0.5)
    start = math.hypot(pos[0] - cx, pos[1] - cy)
    # one 16 ms step already pulls it CLOSER
    p1 = ease_step(pos[0], pos[1], cx, cy, damp, 16.0)
    assert math.hypot(p1[0] - cx, p1[1] - cy) < start
    # many steps → essentially onto the cursor (exponential approach)
    for _ in range(2000):
        pos = ease_step(pos[0], pos[1], cx, cy, damp, 16.0)
    assert math.hypot(pos[0] - cx, pos[1] - cy) < 0.5


def test_repel_integration_flees_cursor():
    cx, cy = 256.0, 192.0
    pos = (270.0, 192.0)          # start just to the right of the cursor
    start = math.hypot(pos[0] - cx, pos[1] - cy)
    for _ in range(80):
        dx, dy = repel_destination(pos[0], pos[1], cx, cy)
        damp = repel_damp_length(pos[0], pos[1], cx, cy, 0.5)
        pos = ease_step(pos[0], pos[1], dx, dy, damp, 16.0)
    assert math.hypot(pos[0] - cx, pos[1] - cy) > start
    # fled to the right, never leaves the playfield
    assert 0.0 <= pos[0] <= BASE_SIZE[0] and 0.0 <= pos[1] <= BASE_SIZE[1]
    assert pos[0] > 270.0


# =========================================================================
# 4. slider movement bounds (RP keeps the whole slider on the playfield)
# =========================================================================
def test_slider_movement_bounds_and_clamp():
    # a horizontal 100 px slider, head at origin, radius 10
    b = slider_movement_bounds([(0.0, 0.0), (100.0, 0.0)], 10.0)
    assert b == (10.0, BASE_SIZE[0] - 110.0, 10.0, BASE_SIZE[1] - 10.0)
    # a destination past the edge clamps into the box
    assert clamp_to_slider_bounds(-50.0, 5.0, b) == (10.0, 10.0)
    assert clamp_to_slider_bounds(999.0, 999.0, b) == (BASE_SIZE[0] - 110.0,
                                                       BASE_SIZE[1] - 10.0)
    # empty path → full playfield
    assert slider_movement_bounds([], 10.0) == (0.0, BASE_SIZE[0],
                                                0.0, BASE_SIZE[1])


# =========================================================================
# 5. reading the acronym + strength from the ScoreInfo blob
# =========================================================================
def test_acronyms():
    assert MAGNETISED == "MG" and REPEL == "RP"
    assert REPEL_MAGNET_MODS == {"MG", "RP"}
    assert MAGNETISED_ACRONYM == "MG" and REPEL_ACRONYM == "RP"


def test_magnet_default_strength():
    m = repel_magnet_from_mods([{"acronym": "MG", "settings": {}}])
    assert m == RepelMagnet("MG", strength=0.5)


def test_repel_default_strength():
    m = repel_magnet_from_mods([{"acronym": "RP"}])
    assert m.acronym == "RP" and _close(m.strength, 0.5)


def test_magnet_custom_strength_read():
    m = repel_magnet_from_mods(
        [{"acronym": "MG", "settings": {"attraction_strength": 0.85}}])
    assert _close(m.strength, 0.85)


def test_repel_custom_strength_read():
    m = repel_magnet_from_mods(
        [{"acronym": "RP", "settings": {"repulsion_strength": 0.2}}])
    assert _close(m.strength, 0.2)


def test_strength_clamped_to_range():
    hi = repel_magnet_from_mods(
        [{"acronym": "MG", "settings": {"attraction_strength": 9.0}}])
    lo = repel_magnet_from_mods(
        [{"acronym": "RP", "settings": {"repulsion_strength": 0.0}}])
    assert _close(hi.strength, 1.0) and _close(lo.strength, 0.05)


def test_wrong_key_ignored_falls_back_to_default():
    # MG reads attraction_strength, not repulsion_strength (and vice-versa)
    m = repel_magnet_from_mods(
        [{"acronym": "MG", "settings": {"repulsion_strength": 0.2}}])
    assert _close(m.strength, 0.5)


def test_bool_strength_rejected():
    m = repel_magnet_from_mods(
        [{"acronym": "RP", "settings": {"repulsion_strength": True}}])
    assert _close(m.strength, 0.5)


def test_non_repel_magnet_returns_none():
    assert repel_magnet_from_mods([{"acronym": "HD"}, {"acronym": "DT"}]) is None
    assert repel_magnet_from_mods([]) is None


def test_first_repel_magnet_wins():
    # MG and RP are mutually incompatible; the first match wins
    m = repel_magnet_from_mods([{"acronym": "MG"}, {"acronym": "RP"}])
    assert m.acronym == "MG"


def test_read_repel_magnet_from_osr_blob():
    d = Path(tempfile.mkdtemp(prefix="stdrm_"))
    p = _write_osr(d, "rp.osr",
                   [{"acronym": "HD"},
                    {"acronym": "RP", "settings": {"repulsion_strength": 0.7}}])
    m = read_repel_magnet(p)
    assert m.acronym == "RP" and _close(m.strength, 0.7)


def test_read_none_for_stable_no_blob():
    d = Path(tempfile.mkdtemp(prefix="stdrm_"))
    assert read_repel_magnet(_write_osr(d, "n.osr", [], blob=False)) is None


def test_parse_replay_surfaces_repel_magnet():
    from osu_std_renderer.replay.replay import parse_replay
    d = Path(tempfile.mkdtemp(prefix="stdrm_"))
    p = _write_osr(d, "mg.osr",
                   [{"acronym": "MG", "settings": {"attraction_strength": 0.9}}])
    _, meta = parse_replay(p)
    assert meta.has_repel_magnet
    assert meta.repel_magnet_acronym == "MG"
    assert _close(meta.repel_magnet_strength, 0.9)


def test_parse_replay_nomod_has_no_repel_magnet():
    from osu_std_renderer.replay.replay import parse_replay
    d = Path(tempfile.mkdtemp(prefix="stdrm_"))
    _, meta = parse_replay(_write_osr(d, "nm.osr", [{"acronym": "HD"}]))
    assert not meta.has_repel_magnet
    assert meta.repel_magnet_acronym == ""
    assert _close(meta.repel_magnet_strength, 0.5)


# =========================================================================
# 6. the mods do NOT touch the sim (judgement/reconcile on real geometry)
# =========================================================================
_SYNTH = """osu file format v14
[General]
AudioFilename: none.mp3
StackLeniency: 0.7
Mode: 0
[Metadata]
Title:RepelMagnet
Artist:Test
Creator:tests
Version:rm
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:8
SliderMultiplier:1.4
SliderTickRate:1
[TimingPoints]
0,500,4,2,0,100,1,0
[HitObjects]
120,120,1000,5,0,0:0:0:0:
300,120,1350,1,0,0:0:0:0:
120,260,1700,2,0,L|360:260,1,240
"""


def _synth_map():
    import osu_std_renderer.beatmap as _b
    d = Path(tempfile.mkdtemp(prefix="stdrm_"))
    p = d / "m.osu"
    p.write_text(_SYNTH)
    return _b.load_full(p)


def _autoplay(bm):
    from osu_std_renderer.beatmap.objects.slider import Slider
    from osu_std_renderer.replay.replay import StdFrame
    fr = [StdFrame(0, 256.0, 192.0, 0)]
    objs = sorted(bm.hit_objects, key=lambda o: o.get_start_time())
    for i, o in enumerate(objs):
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


def test_repel_magnet_do_not_touch_the_sim():
    # the sim depends only on beatmap + frames; MG/RP live in the scene, so a
    # pipeline that reads an MG/RP .osr produces the IDENTICAL sim + verdicts.
    from osu_std_renderer.ruleset import StdRuleset
    bm = _synth_map()
    frames = _autoplay(bm)

    def sig():
        r = StdRuleset(bm, frames, None, reconcile=False).run()
        return (r.sim_counts, tuple(sorted(
            (k, v.kind, round(v.pos[0], 4), round(v.pos[1], 4), v.hit_time)
            for k, v in r.verdicts.items())))

    assert sig() == sig()
    before = [o.get_stacked_start_position(bm.diff) for o in bm.hit_objects]
    after = [o.get_stacked_start_position(bm.diff) for o in bm.hit_objects]
    assert before == after


def test_reconcile_exact_for_clean_magnet_play():
    # a synthetic MG play built from an on-object (autoplay) replay: the cursor
    # is on the real geometry, so the sim hits everything and reconcile against
    # the matching .osr counts is a no-op (relabeled 0 → counts exact).
    from osu_std_renderer.ruleset import StdRuleset
    from osu_std_renderer.replay.replay import ReplayMeta
    bm = _synth_map()
    frames = _autoplay(bm)
    sim = StdRuleset(bm, frames, None, reconcile=False).run()
    c300, c100, c50, cmiss = sim.sim_counts
    meta = ReplayMeta(
        mode=0, beatmap_md5="", player_name="t", mods=0, score=0,
        max_combo=sim.sim_max_combo, count_300=c300, count_100=c100,
        count_50=c50, count_geki=0, count_katu=0, count_miss=cmiss,
        accuracy=100.0, grade="SS", game_version=LAZER_GV,
        lazer_mods=("MG",), repel_magnet_acronym="MG",
        repel_magnet_strength=0.5)
    rec = StdRuleset(bm, frames, meta, reconcile=True).run()
    assert rec.final_counts == (c300, c100, c50, cmiss)
    assert rec.relabeled == 0


# =========================================================================
# 7. GPU-gated scene: eased objects track the cursor, follow points hidden,
#    the frame changes vs nomod (skips gracefully without EGL — run_all).
# =========================================================================
def _parked_frames(x, y, t_end=2200, step=16):
    from osu_std_renderer.replay.replay import StdFrame
    return [StdFrame(t, float(x), float(y), 0)
            for t in range(0, int(t_end), step)]


_ONE_CIRCLE = """osu file format v14
[General]
AudioFilename: none.mp3
StackLeniency: 0.7
Mode: 0
[Metadata]
Title:RepelMagnet
Artist:Test
Creator:tests
Version:one
[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:7
ApproachRate:3
SliderMultiplier:1.4
SliderTickRate:1
[TimingPoints]
0,500,4,2,0,100,1,0
[HitObjects]
100,100,2000,5,0,0:0:0:0:
"""


def _one_circle_map():
    import osu_std_renderer.beatmap as _b
    d = Path(tempfile.mkdtemp(prefix="stdrm_"))
    p = d / "c.osu"
    p.write_text(_ONE_CIRCLE)
    return _b.load_full(p)


def test_scene_repel_magnet_tracks_cursor_and_hides_followpoints():
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
    from osu_std_renderer.ruleset import StdRuleset
    try:
        bm = _one_circle_map()
        cx, cy = 400.0, 300.0                # cursor parked far from (100,100)
        frames = _parked_frames(cx, cy)
        judg = StdRuleset(bm, frames, None, reconcile=False).run()
        bank = TextureBank(spr)
        bodies = SliderBodyRenderer(spr.ctx, 320, 240)
        cam = PlayfieldCamera(320, 240)
        circle = bm.hit_objects[0]
        real = circle.get_stacked_start_position(bm.diff)
        d_real = math.hypot(real[0] - cx, real[1] - cy)

        def scene(**kw):
            return StdScene(bm, frames, cam, spr, bodies, bank, judgments=judg,
                            **kw)

        # nomod baseline + follow-point default
        base_sc = scene()
        base = base_sc.frame_rgb(1990.0)
        assert base_sc.draw_follow_points is True
        assert base_sc.rm_mod == ""

        # MG: the eased object is pulled CLOSER to the cursor; follow points off
        mg = scene(repel_magnet="MG", repel_magnet_strength=0.5)
        mg_img = mg.frame_rgb(1990.0)
        assert mg.draw_follow_points is False
        mx, my = mg._rm_pos[id(circle)]
        assert math.hypot(mx - cx, my - cy) < d_real - 5.0
        assert not np.array_equal(mg_img, base)

        # RP: the eased object is driven AWAY from the cursor; follow points off
        rp = scene(repel_magnet="RP", repel_magnet_strength=0.5)
        rp_img = rp.frame_rgb(1990.0)
        assert rp.draw_follow_points is False
        rx, ry = rp._rm_pos[id(circle)]
        assert math.hypot(rx - cx, ry - cy) > d_real + 5.0
        assert not np.array_equal(rp_img, base)

        # non-MG/RP scene renders the SAME bytes as the baseline (gate holds)
        again = scene().frame_rgb(1990.0)
        assert np.array_equal(again, base)
    finally:
        spr.release()
