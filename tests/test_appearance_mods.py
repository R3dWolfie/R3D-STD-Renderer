"""Approach/circle-appearance mods — FR (Freeze Frame), AD (Approach Different),
TC (Traceable).

Covers (1) reading each acronym + AD's settings from a .osr ScoreInfo blob,
(2) the pure approach-circle math (AD's per-style easing table + scale curve;
FR's extended preempt + rescaled approach ring), (3) that the mods are VISUAL
ONLY (the scene never mutates the beatmap difficulty / object geometry, so the
judgement/reconcile is untouched), and (4) that a non-mod replay is unchanged
(the default dispatch equals the legacy §2.5 approach math). Constants
cross-checked against ppy/osu (MIT):

  OsuModFreezeFrame.cs        TimePreempt += StartTime-lastNewComboTime;
                              approach ScaleTo(4*Preempt/orig).ScaleTo(1,Preempt)
  OsuModApproachDifferent.cs  Scale 4 [1.5,10]; Style Gravity; ScaleTo(Scale)
                              .ScaleTo(1, Preempt, EASING[Style])
  OsuModTraceable.cs          hide CirclePiece ("only the approach circle");
                              slider body outline-only; hide slider tail
"""
from __future__ import annotations

import json
import lzma
import struct
import tempfile
from datetime import datetime
from pathlib import Path

from osrparse import GameMode, Key, Mod, Replay
from osrparse.replay import ReplayEventOsu

from osu_std_renderer.render import appearance_mods as am
from osu_std_renderer.replay.lazer_mods import (
    APPROACH_DIFFERENT_SCALE_DEFAULT, APPROACH_DIFFERENT_STYLES,
    ApproachDifferent, approach_different_from_mods, freeze_frame_from_mods,
    read_approach_different, read_freeze_frame, read_traceable,
    traceable_from_mods)

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
def test_freeze_frame_presence():
    assert freeze_frame_from_mods([{"acronym": "FR", "settings": {}}]) is True
    assert freeze_frame_from_mods([{"acronym": "HD"}]) is False
    assert freeze_frame_from_mods([]) is False


def test_traceable_presence():
    assert traceable_from_mods([{"acronym": "TC"}]) is True
    assert traceable_from_mods([{"acronym": "HD"}]) is False


def test_approach_different_default_scale_and_style():
    ad = approach_different_from_mods([{"acronym": "AD", "settings": {}}])
    assert ad == ApproachDifferent(scale=4.0, style="gravity")
    assert _close(APPROACH_DIFFERENT_SCALE_DEFAULT, 4.0)


def test_approach_different_custom_scale_read():
    ad = approach_different_from_mods(
        [{"acronym": "AD", "settings": {"scale": 6.5}}])
    assert _close(ad.scale, 6.5) and ad.style == "gravity"


def test_approach_different_scale_clamped_to_range():
    # Scale range [1.5, 10]
    hi = approach_different_from_mods([{"acronym": "AD", "settings": {"scale": 99}}])
    lo = approach_different_from_mods([{"acronym": "AD", "settings": {"scale": 0.1}}])
    assert _close(hi.scale, 10.0) and _close(lo.scale, 1.5)


def test_approach_different_style_by_int_index():
    # the serialised enum integer indexes APPROACH_DIFFERENT_STYLES in order
    for i, name in enumerate(APPROACH_DIFFERENT_STYLES):
        ad = approach_different_from_mods(
            [{"acronym": "AD", "settings": {"style": i}}])
        assert ad.style == name


def test_approach_different_style_by_name():
    ad = approach_different_from_mods(
        [{"acronym": "AD", "settings": {"style": "Accelerate2"}}])
    assert ad.style == "accelerate2"


def test_approach_different_style_unknown_defaults_gravity():
    ad = approach_different_from_mods(
        [{"acronym": "AD", "settings": {"style": 999}}])
    assert ad.style == "gravity"
    ad2 = approach_different_from_mods(
        [{"acronym": "AD", "settings": {"style": "wobble"}}])
    assert ad2.style == "gravity"


def test_non_appearance_mod_returns_none_or_false():
    assert approach_different_from_mods([{"acronym": "HD"}]) is None
    assert freeze_frame_from_mods([{"acronym": "AD"}]) is False
    assert traceable_from_mods([{"acronym": "AD"}]) is False


def test_read_from_osr_blob():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fr = _write_osr(d, "fr.osr", [{"acronym": "FR"}])
        tc = _write_osr(d, "tc.osr", [{"acronym": "TC"}])
        ad = _write_osr(d, "ad.osr",
                        [{"acronym": "AD", "settings": {"scale": 7, "style": 4}}])
        assert read_freeze_frame(fr) is True
        assert read_traceable(tc) is True
        got = read_approach_different(ad)
        assert _close(got.scale, 7.0) and got.style == "accelerate1"
        # cross-checks: FR osr has no TC/AD, etc.
        assert read_traceable(fr) is False
        assert read_approach_different(fr) is None


def test_read_none_for_stable_no_blob():
    with tempfile.TemporaryDirectory() as d:
        p = _write_osr(Path(d), "stable.osr", [], blob=False)
        assert read_freeze_frame(p) is False
        assert read_traceable(p) is False
        assert read_approach_different(p) is None


# =========================================================================
# 2. parse_replay surfaces the flags on ReplayMeta
# =========================================================================
def test_parse_replay_surfaces_appearance_mods():
    from osu_std_renderer.replay.replay import parse_replay
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        fr = _write_osr(d, "fr.osr", [{"acronym": "FR"}])
        tc = _write_osr(d, "tc.osr", [{"acronym": "TC"}])
        ad = _write_osr(d, "ad.osr",
                        [{"acronym": "AD", "settings": {"scale": 5.5,
                                                        "style": "decelerate3"}}])
        _, m_fr = parse_replay(fr)
        _, m_tc = parse_replay(tc)
        _, m_ad = parse_replay(ad)
        assert m_fr.freeze_frame and not m_fr.traceable
        assert m_fr.approach_scale is None and not m_fr.has_approach_different
        assert m_tc.traceable and not m_tc.freeze_frame
        assert m_ad.has_approach_different
        assert _close(m_ad.approach_scale, 5.5) and m_ad.approach_style == "decelerate3"


def test_parse_replay_nomod_has_no_appearance_mods():
    from osu_std_renderer.replay.replay import parse_replay
    with tempfile.TemporaryDirectory() as d:
        p = _write_osr(Path(d), "nomod.osr", [])
        _, m = parse_replay(p)
        assert not m.freeze_frame and not m.traceable
        assert m.approach_scale is None and not m.has_approach_different


# =========================================================================
# 3. AD — the AnimationStyle easing table + the scale curve
# =========================================================================
def test_ad_easing_table_is_complete_and_mapped():
    # every enum member has an easing; the documented mapping holds
    assert set(am.AD_EASINGS) == set(APPROACH_DIFFERENT_STYLES)
    assert am.AD_EASINGS["linear"] is am.ease_none
    assert am.AD_EASINGS["gravity"] is am.ease_in_back
    assert am.AD_EASINGS["inout1"] is am.ease_in_out_cubic
    assert am.AD_EASINGS["inout2"] is am.ease_in_out_quint
    assert am.AD_EASINGS["accelerate1"] is am.ease_in_quad
    assert am.AD_EASINGS["accelerate2"] is am.ease_in_cubic
    assert am.AD_EASINGS["accelerate3"] is am.ease_in_quint
    assert am.AD_EASINGS["decelerate1"] is am.ease_out_quad
    assert am.AD_EASINGS["decelerate2"] is am.ease_out_cubic
    assert am.AD_EASINGS["decelerate3"] is am.ease_out_quint


def test_ad_easings_endpoints():
    # every easing is 0 at p=0 and 1 at p=1 (transforms start/settle exactly)
    for name, fn in am.AD_EASINGS.items():
        assert _close(fn(0.0), 0.0), name
        assert _close(fn(1.0), 1.0), name


def test_ad_easing_formulas_midpoint():
    # spot-check the osu!framework formulas at p=0.5
    assert _close(am.ease_none(0.5), 0.5)
    assert _close(am.ease_in_quad(0.5), 0.25)
    assert _close(am.ease_in_cubic(0.5), 0.125)
    assert _close(am.ease_in_quint(0.5), 0.03125)
    assert _close(am.ease_out_quad(0.5), 0.75)
    assert _close(am.ease_out_cubic(0.5), 1.0 - 0.5 ** 3)
    assert _close(am.ease_out_quint(0.5), 1.0 - 0.5 ** 5)
    assert _close(am.ease_in_out_cubic(0.5), 0.5)      # symmetric midpoint
    assert _close(am.ease_in_out_quint(0.5), 0.5)
    # InBack (Gravity): p²·((s+1)p − s) with s = 1.70158
    assert _close(am.ease_in_back(0.5), 0.25 * (2.70158 * 0.5 - 1.70158))


def test_ad_in_back_dips_below_zero_early():
    # the "gravity" pull-back: InBack is negative for small p (the ring bulges
    # slightly LARGER than Scale before shrinking)
    assert am.ease_in_back(0.1) < 0.0


def test_approach_different_scale_endpoints():
    # scale starts at Scale (progress 0) and settles to 1 (progress 1)
    start, preempt, scale = 1000.0, 600.0, 8.0
    at_appear = am.approach_different_scale(start - preempt, start, preempt,
                                            scale, "linear")
    assert _close(at_appear, 8.0)
    # just before the hit -> ~1 (gone exactly at start)
    near_hit = am.approach_different_scale(start - 1e-6, start, preempt,
                                           scale, "linear")
    assert _close(near_hit, 1.0, eps=1e-4)
    # outside the life window -> None
    assert am.approach_different_scale(start, start, preempt, scale, "linear") is None
    assert am.approach_different_scale(start - preempt - 1, start, preempt,
                                       scale, "linear") is None


def test_approach_different_scale_linear_equals_default_when_scale_is_four():
    # Linear style + Scale 4 reproduces the unmodded §2.5 4->1 linear ring
    start, preempt = 1000.0, 600.0
    for t in (500.0, 700.0, 900.0, 999.0):
        ad = am.approach_different_scale(t, start, preempt, 4.0, "linear")
        w = (t - (start - preempt)) / preempt
        legacy = am.APPROACH_START_SCALE - (am.APPROACH_START_SCALE - 1.0) * w
        assert _close(ad, legacy)


def test_approach_different_scale_gravity_overshoots_scale():
    # with Gravity the ring first grows ABOVE Scale (InBack < 0 early)
    start, preempt, scale = 1000.0, 600.0, 8.0
    early = am.approach_different_scale(start - preempt + 30.0, start, preempt,
                                        scale, "gravity")
    assert early > scale


# =========================================================================
# 4. FR — extended preempt (whole combo appears together) + approach rescale
# =========================================================================
def test_freeze_preempt_formula():
    # first-of-combo (start == combo start) keeps the original preempt; a later
    # object gets orig + (start - comboStart)
    assert _close(am.freeze_preempt(600.0, 1000.0, 1000.0), 600.0)
    assert _close(am.freeze_preempt(600.0, 1300.0, 1000.0), 900.0)


class _Obj:
    def __init__(self, start, new_combo, spinner=False):
        self.start = start
        self.new_combo = new_combo
        self._spin = spinner

    def get_start_time(self):
        return self.start


def test_freeze_preempts_whole_combo_shares_appear_time():
    orig = 600.0
    objs = [_Obj(1000, True), _Obj(1300, False),   # combo 1
            _Obj(2000, True), _Obj(2300, False)]    # combo 2
    pre = am.freeze_preempts(objs, orig, lambda o: o._spin)
    # every object's APPEAR time = start - preempt
    appear = [o.get_start_time() - pre[id(o)] for o in objs]
    assert _close(appear[0], appear[1])            # combo 1 together
    assert _close(appear[2], appear[3])            # combo 2 together
    assert _close(appear[0], 1000 - orig)
    assert _close(appear[2], 2000 - orig)
    # combo starts keep the original preempt; later objects get a longer one
    assert _close(pre[id(objs[0])], orig)
    assert _close(pre[id(objs[1])], orig + 300)


def test_freeze_preempts_skips_spinner():
    orig = 600.0
    objs = [_Obj(1000, True), _Obj(1500, False, spinner=True)]
    pre = am.freeze_preempts(objs, orig, lambda o: o._spin)
    assert id(objs[1]) not in pre                  # spinner not extended


def test_freeze_frame_scale_endpoints_and_original_appear():
    orig, start = 600.0, 1300.0
    preempt = 900.0                                # a 2nd-in-combo object
    # initial scale = 4 * preempt/orig
    at_appear = am.freeze_frame_scale(start - preempt, start, preempt, orig)
    assert _close(at_appear, am.APPROACH_START_SCALE * preempt / orig)
    # settles to 1 by the hit
    assert _close(am.freeze_frame_scale(start - 1e-6, start, preempt, orig),
                  1.0, eps=1e-3)
    # linear (Easing.None) init->1 over the extended preempt: at the object's
    # ORIGINAL appear time (progress 1 - orig/preempt) the ring is 5 - orig/preempt
    # (ppy/osu FR's rescale keeps it roughly normal-sized despite the longer
    # preempt — it is NOT exactly 4 and the rate is not preserved).
    init = am.APPROACH_START_SCALE * preempt / orig
    at_orig = am.freeze_frame_scale(start - orig, start, preempt, orig)
    p = (preempt - orig) / preempt
    assert _close(at_orig, init + (1.0 - init) * p)
    assert _close(at_orig, 5.0 - orig / preempt)
    assert 1.0 < at_orig < init
    # outside the (extended) life window -> None
    assert am.freeze_frame_scale(start, start, preempt, orig) is None
    assert am.freeze_frame_scale(start - preempt - 1, start, preempt, orig) is None


# =========================================================================
# 5. scene integration (needs a GL context — skips cleanly without one)
# =========================================================================
_APPEAR_OSU = """osu file format v14

[General]
AudioFilename: none.mp3
Mode: 0

[Metadata]
Title:Appear
Artist:Test
Creator:tests
Version:appear

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,100,1,0

[HitObjects]
256,192,1000,5,0,0:0:0:0:
200,192,1300,1,0,0:0:0:0:
256,100,2000,5,0,0:0:0:0:
200,100,2300,1,0,0:0:0:0:
"""


def _build_scene(**kwargs):
    """A real StdScene over _APPEAR_OSU, or None if no EGL device is present.
    Returns (scene, beatmap, camera, spr) — caller releases spr."""
    try:
        from osu_std_renderer.render.gl import SpriteRenderer
        spr = SpriteRenderer(320, 240)
    except Exception:  # noqa: BLE001 — no EGL device on this box
        return None
    from osu_std_renderer.beatmap import load_full
    from osu_std_renderer.render.playfield import PlayfieldCamera
    from osu_std_renderer.render.scene import StdScene
    from osu_std_renderer.render.slider_body import SliderBodyRenderer
    from osu_std_renderer.render.textures import TextureBank
    from osu_std_renderer.replay.replay import StdFrame
    with tempfile.NamedTemporaryFile("w", suffix=".osu", delete=False) as fh:
        fh.write(_APPEAR_OSU)
        osu_path = Path(fh.name)
    try:
        bm = load_full(osu_path)
        bank = TextureBank(spr)
        bodies = SliderBodyRenderer(spr.ctx, 320, 240)
        cam = PlayfieldCamera(320, 240)
        frames = [StdFrame(0, 256.0, 192.0, 0), StdFrame(3000, 100.0, 100.0, 1)]
        scene = StdScene(bm, frames, cam, spr, bodies, bank, **kwargs)
        return scene, bm, cam, spr
    finally:
        osu_path.unlink(missing_ok=True)


def _objs(bm):
    return sorted(bm.hit_objects, key=lambda o: o.get_start_time())


def test_traceable_hides_circle_keeps_approach():
    built = _build_scene(traceable=True)
    if built is None:
        print("SKIP (no GL context)")
        return
    scene, bm, cam, spr = built
    try:
        obj = _objs(bm)[0]                          # circle at t=1000
        start = obj.get_start_time()
        t = start - 100.0                           # mid-approach (visible)
        approach: list = []
        sprites = scene._head_sprites(
            obj, t, obj.get_stacked_start_position(scene.diff), approach)
        # TC: the whole CirclePiece (fill + ring + number) is gone...
        assert sprites == [], f"TC should draw no circle sprites, got {sprites}"
        # ...but the approach circle is still traced (IRequiresApproachCircles)
        assert len(approach) == 1
    finally:
        spr.release()


def test_nomod_circle_has_fill_and_approach_baseline():
    built = _build_scene()
    if built is None:
        print("SKIP (no GL context)")
        return
    scene, bm, cam, spr = built
    try:
        obj = _objs(bm)[0]
        start = obj.get_start_time()
        t = start - 100.0
        approach: list = []
        sprites = scene._head_sprites(
            obj, t, obj.get_stacked_start_position(scene.diff), approach)
        assert len(sprites) >= 2, "nomod circle draws fill + ring (+ number)"
        assert len(approach) == 1
    finally:
        spr.release()


def test_freeze_frame_combo_appears_together():
    """The 2nd object of a combo (t=1300) has its approach ring on screen at
    t=500 under FR (it appears with the combo at the combo start ~400) but NOT
    under nomod (it would appear at 1300-preempt ~700)."""
    nomod = _build_scene()
    if nomod is None:
        print("SKIP (no GL context)")
        return
    scene_n, bm_n, cam_n, spr_n = nomod
    try:
        obj_b = _objs(bm_n)[1]                       # 2nd of combo 1, t=1300
        pos = obj_b.get_stacked_start_position(scene_n.diff)
        ap_n: list = []
        scene_n._head_sprites(obj_b, 500.0, pos, ap_n)
        assert ap_n == [], "nomod: 2nd object not yet appeared at t=500"
    finally:
        spr_n.release()
    fr = _build_scene(freeze_frame=True)
    scene_f, bm_f, cam_f, spr_f = fr
    try:
        obj_b = _objs(bm_f)[1]
        pos = obj_b.get_stacked_start_position(scene_f.diff)
        ap_f: list = []
        scene_f._head_sprites(obj_b, 500.0, pos, ap_f)
        assert len(ap_f) == 1, "FR: 2nd object's approach ring already on screen"
    finally:
        spr_f.release()


def test_approach_different_scene_uses_custom_scale():
    built = _build_scene(approach_scale=8.0, approach_style="linear")
    if built is None:
        print("SKIP (no GL context)")
        return
    scene, bm, cam, spr = built
    try:
        obj = _objs(bm)[0]
        start = obj.get_start_time()
        preempt = scene._preempt_for(obj)
        t = start - preempt + 30.0                   # just after appear
        geo = scene._approach_geometry(obj, t, start, preempt,
                                       scene.diff.time_fade_in)
        assert geo is not None
        ad_scale = geo[0]
        # nomod (default 4->1) at the same t is much smaller than AD Scale 8
        w = 30.0 / preempt
        nomod_scale = 4.0 - 3.0 * w
        assert ad_scale > nomod_scale + 2.0
    finally:
        spr.release()


def test_appearance_mods_are_visual_only():
    """VISUAL ONLY: FR/AD/TC never mutate the beatmap difficulty or object
    geometry, so the reconcile (positions + hit windows + replay) is EXACT."""
    base = _build_scene()
    if base is None:
        print("SKIP (no GL context)")
        return
    scene_b, bm_b, cam_b, spr_b = base
    base_preempt = scene_b.diff.preempt
    base_pos = [o.get_stacked_start_position(scene_b.diff) for o in _objs(bm_b)]
    spr_b.release()
    for kwargs in ({"freeze_frame": True},
                   {"traceable": True},
                   {"approach_scale": 7.0, "approach_style": "gravity"}):
        built = _build_scene(**kwargs)
        scene, bm, cam, spr = built
        try:
            # difficulty preempt is the ruleset's value, untouched
            assert _close(scene.diff.preempt, base_preempt)
            # object positions identical to nomod
            for o, bp in zip(_objs(bm), base_pos):
                p = o.get_stacked_start_position(scene.diff)
                assert _close(p[0], bp[0]) and _close(p[1], bp[1])
        finally:
            spr.release()


def test_default_approach_geometry_matches_legacy():
    """Non-mod byte-identical: with all appearance-mod flags off, the scene's
    approach dispatch reproduces the legacy §2.5 approach_scale_alpha exactly."""
    built = _build_scene()
    if built is None:
        print("SKIP (no GL context)")
        return
    from osu_std_renderer.render.scene import approach_scale_alpha
    scene, bm, cam, spr = built
    try:
        obj = _objs(bm)[0]
        start = obj.get_start_time()
        preempt, fade_in = scene._preempt_for(obj), scene.diff.time_fade_in
        for t in (start - preempt + 1.0, start - 200.0, start - 50.0, start - 1.0):
            geo = scene._approach_geometry(obj, t, start, preempt, fade_in)
            legacy = approach_scale_alpha(t, start, preempt, fade_in)
            assert geo is not None and legacy is not None
            assert _close(geo[0], legacy[0]) and _close(geo[1], legacy[1])
    finally:
        spr.release()
