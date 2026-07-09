"""Lazer results screen (render/lazer_results.py): accuracy-circle arc
math + the virtual-SS notch, grade-boundary bands, pp-component
percentages, timing histogram binning, the PB-card DB query (best-of +
omit-when-none + exclude-current), the style toggle, and a stack draw
against a faked GL renderer (PIL bakes are real; the sprite batch is
mocked — the screen only needs upload_texture/draw/width/height)."""
from __future__ import annotations

import math
import os
import sqlite3
import tempfile

from osu_std_renderer.render.lazer_results import (
    FOR_RANK, GRADE_SPACING_PERCENTAGE, LazerResultsScreen, RANK_THRESHOLDS,
    RESULTS_SCORE_WEIGHT, RESULTS_TEXT_WEIGHT, ResultsData,
    VIRTUAL_SS_PERCENTAGE, acc_to_angle_deg, arc_color_at, avatar_hue,
    avatar_initials, bake_accuracy_arc, bake_avatar, bake_grade_letter,
    bake_star, ease_out_quint, for_star_difficulty, grade_bands,
    rank_badge_positions, rank_ring_bands, query_pb, slider_stats,
    target_arc_value,
)
from osu_std_renderer.render.textures import _load_argon_font
from osu_std_renderer.render.pp import component_pct
from osu_std_renderer.settings import StdRenderSettings


# --- accuracy-circle arc math -------------------------------------------------------

def test_acc_to_angle_starts_at_top_and_wraps_clockwise():
    # PIL degrees: 3 o'clock = 0, clockwise. The ring starts at 12 o'clock.
    assert acc_to_angle_deg(0.0) == 270.0
    assert abs(acc_to_angle_deg(0.25) - 360.0) < 1e-9      # +90° → 3 o'clock
    assert abs(acc_to_angle_deg(1.0) - 630.0) < 1e-9       # full turn
    # clamped
    assert acc_to_angle_deg(-1.0) == 270.0
    assert acc_to_angle_deg(2.0) == 630.0


def test_virtual_ss_notch():
    # a true SS closes the ring; anything else caps below 1.0 so the notch
    # stays open
    assert target_arc_value(1.0, "SS") == 1.0
    assert target_arc_value(0.9999, "S") == 1.0 - VIRTUAL_SS_PERCENTAGE
    assert abs(target_arc_value(0.5, "C") - 0.5) < 1e-9
    # 99.5% S play still can't close the ring
    assert target_arc_value(0.995, "S") == 1.0 - VIRTUAL_SS_PERCENTAGE


def test_ease_out_quint_endpoints():
    assert ease_out_quint(0.0) == 0.0
    assert ease_out_quint(1.0) == 1.0
    assert ease_out_quint(2.0) == 1.0            # clamped
    assert 0.9 < ease_out_quint(0.5) < 1.0       # fast out


# --- grade boundaries ---------------------------------------------------------------

def test_grade_bands_partition_and_colours():
    bands = grade_bands()
    # contiguous cover of [0, 1] with no gaps
    assert bands[0][0] == 0.0
    assert bands[-1][1] == 1.0
    for i in range(1, len(bands)):
        assert bands[i][0] == bands[i - 1][1]
    grades = [g for _lo, _hi, g in bands]
    assert grades == ["D", "C", "B", "A", "S"]
    # the D band owns the whole 0–70% (majority red), matching lazer
    assert bands[0] == (0.0, 0.70, "D")
    # thresholds match the rank table
    assert [lo for lo, _ in RANK_THRESHOLDS] == [0.0, 0.7, 0.8, 0.9, 0.95, 1.0]


# --- rank-gradient arc (Fix 1) ------------------------------------------------------

def _hexf(s):
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0,
            int(s[4:6], 16) / 255.0)


def test_for_rank_matches_lazer_oscolour_forrank():
    # exact hexes from osu.Game/Graphics/OsuColour.cs ForRank(ScoreRank)
    assert FOR_RANK["D"] == _hexf("ff5a5a")
    assert FOR_RANK["C"] == _hexf("ff8e5d")
    assert FOR_RANK["B"] == _hexf("e3b130")
    assert FOR_RANK["A"] == _hexf("88da20")
    assert FOR_RANK["S"] == _hexf("02b5c3")
    assert FOR_RANK["SS"] == _hexf("de31ae")
    # X/SH/XH aliases resolve to the same stop as SS/S
    assert FOR_RANK["X"] == FOR_RANK["SS"]
    assert FOR_RANK["XH"] == FOR_RANK["SS"]
    assert FOR_RANK["SH"] == FOR_RANK["S"]


def test_grade_spacing_percentage_matches_lazer():
    # AccuracyCircle.GRADE_SPACING_PERCENTAGE = 2.0 / 360
    assert GRADE_SPACING_PERCENTAGE == 2.0 / 360.0


def test_rank_ring_bands_layout_and_virtual_ss():
    bands = rank_ring_bands()
    grades = [g for _lo, _hi, g in bands]
    assert grades == ["D", "C", "B", "A", "S", "SS"]
    # contiguous cover of [0,1]
    assert bands[0][0] == 0.0 and bands[-1][1] == 1.0
    for i in range(1, len(bands)):
        assert bands[i][0] == bands[i - 1][1]
    # the S band stops at 1 - VIRTUAL_SS_PERCENTAGE and SS owns 0.99->1.0
    ss_lo = 1.0 - VIRTUAL_SS_PERCENTAGE
    assert bands[4] == (0.95, ss_lo, "S")
    assert bands[5] == (ss_lo, 1.0, "SS")


def test_arc_color_at_maps_accuracy_to_forrank_stop():
    # each accuracy lands in its rank band's ForRank colour
    assert arc_color_at(0.0) == FOR_RANK["D"]
    assert arc_color_at(0.5) == FOR_RANK["D"]
    assert arc_color_at(0.72) == FOR_RANK["C"]
    assert arc_color_at(0.85) == FOR_RANK["B"]
    assert arc_color_at(0.93) == FOR_RANK["A"]
    assert arc_color_at(0.97) == FOR_RANK["S"]
    assert arc_color_at(0.995) == FOR_RANK["SS"]
    assert arc_color_at(1.0) == FOR_RANK["SS"]
    # exact band boundaries belong to the upper band (lo <= acc < hi)
    assert arc_color_at(0.70) == FOR_RANK["C"]
    assert arc_color_at(0.95) == FOR_RANK["S"]


def test_rank_badge_positions_lazer_lerp():
    # lazer AccuracyCircle RankBadge visual positions (Interpolation.Lerp) —
    # NOT the band boundaries, so the six spread cleanly and D (0.35) sits
    # far from SS (1.0) instead of both piling at the top.
    pos = rank_badge_positions()
    assert [g for _p, g in pos] == ["D", "C", "B", "A", "S", "SS"]
    dv = {g: p for p, g in pos}
    assert abs(dv["D"] - 0.35) < 1e-9
    assert abs(dv["C"] - 0.75) < 1e-9
    assert abs(dv["B"] - 0.85) < 1e-9
    assert abs(dv["A"] - 0.9125) < 1e-9
    assert abs(dv["S"] - 0.96) < 1e-9
    assert dv["SS"] == 1.0
    ps = [p for p, _g in pos]
    assert ps == sorted(ps) and len(set(ps)) == 6      # distinct, ordered
    assert dv["SS"] - dv["D"] > 0.6                     # spread, no top pile-up


def test_bake_accuracy_arc_is_cyan_green_gradient():
    # lazer's achieved arc is the FIXED cyan(#7CF6FF top)→green(#BAFFA9
    # bottom) vertical gradient — NOT rank colours. Opaque arc pixels: top is
    # bluer (cyan), bottom is redder (green); no rank-red on the arc.
    import numpy as np
    S = 400
    rgba = bake_accuracy_arc(S, 0.97)
    rgb = rgba[..., :3].astype(float)
    alpha = rgba[..., 3]
    ys, xs = np.where(alpha > 200)
    assert len(ys) > 100
    top = ys < S * 0.35
    bot = ys > S * 0.65
    top_pix = rgb[ys[top], xs[top]]
    bot_pix = rgb[ys[bot], xs[bot]]
    assert top_pix[:, 2].mean() > bot_pix[:, 2].mean() + 30   # cyan: more blue
    assert bot_pix[:, 0].mean() > top_pix[:, 0].mean() + 30   # green: more red
    red = np.array([round(c * 255) for c in FOR_RANK["D"]])
    assert not bool((np.abs(rgb - red).sum(axis=2) <= 12).any())  # not rank-red


def test_bake_star_is_a_star_sprite():
    # a real filled 5-point star (the font has no ★ glyph): centre opaque,
    # square corners transparent.
    star = bake_star(64, (1.0, 0.85, 0.30))
    assert star.shape == (64, 64, 4)
    assert star[32, 32, 3] > 200
    assert (star[2, 2, 3] < 40 and star[2, -3, 3] < 40
            and star[-3, 2, 3] < 40 and star[-3, -3, 3] < 40)


def test_results_always_uses_nunito_client_font():
    # the results screen is lazer CLIENT UI (skin-independent) → always the
    # bundled Nunito (Torus stand-in), never DejaVu, regardless of skin.
    for argon in (True, False):
        scr = LazerResultsScreen(_FakeSpr(), _data(), total_ms=5000.0,
                                 argon_font=argon)
        assert scr._font_loader is _load_argon_font
        assert callable(scr._score_loader)
    # the big score is lighter than the body text (lazer Torus Light score)
    assert RESULTS_SCORE_WEIGHT < RESULTS_TEXT_WEIGHT


def _halo_mean(rgba):
    import numpy as np
    rgb = rgba[..., :3].astype(float)
    a = rgba[..., 3]
    white = (rgb[..., 0] > 235) & (rgb[..., 1] > 235) & (rgb[..., 2] > 235)
    m = (a > 25) & (a < 220) & (~white)          # the soft halo, not the fill
    return rgb[m].mean(axis=0) if m.any() else np.zeros(3)


def test_bake_grade_letter_white_fill_rank_glow():
    import numpy as np
    b_rgba, w, h = bake_grade_letter("B", 120, (1, 1, 1), FOR_RANK["B"])
    assert w > 0 and h > 0
    rgb = b_rgba[..., :3].astype(int)
    alpha = b_rgba[..., 3]
    # a solid WHITE core (the fill) exists
    white = ((rgb[..., 0] > 235) & (rgb[..., 1] > 235) & (rgb[..., 2] > 235)
             & (alpha > 235))
    assert white.any()
    # the halo is warm for B (orange ForRank e3b130): more red than blue;
    # cool for S (blue 02b5c3): more blue than red — each grade glows its own
    hb = _halo_mean(b_rgba)
    hs = _halo_mean(bake_grade_letter("S", 120, (1, 1, 1), FOR_RANK["S"])[0])
    assert hb[0] > hb[2] + 20        # B halo warm/orange
    assert hs[2] > hs[0] + 20        # S halo cool/blue


def test_for_star_difficulty_samples_spectrum():
    import osu_std_renderer.render.lazer_results as LR
    assert for_star_difficulty(2.0) == LR._hex("4fffd5")   # exact stop
    mid = for_star_difficulty(6.25)                        # 5.8..6.7 blend
    c0, c1 = LR._hex("6563de"), LR._hex("18158e")
    for j in range(3):
        assert min(c0[j], c1[j]) - 1e-9 <= mid[j] <= max(c0[j], c1[j]) + 1e-9
    assert for_star_difficulty(-1.0) == LR._hex("aaaaaa")  # clamp low
    assert for_star_difficulty(99.0) == LR._hex("000000")  # clamp high


# --- pp-component percentages -------------------------------------------------------

def test_component_pct_clamps():
    assert component_pct(50.0, 200.0) == 0.25
    assert component_pct(0.0, 124.0) == 0.0
    assert component_pct(300.0, 200.0) == 1.0    # over-max clamps to 1
    assert component_pct(10.0, 0.0) == 0.0       # no maximum → 0
    assert component_pct(None, None) == 0.0


# --- slider stats -------------------------------------------------------------------

class _Part:
    def __init__(self, kind, hit):
        self.kind = kind
        self.hit = hit


class _Verdict:
    def __init__(self, parts):
        self.parts = parts


class _Sim:
    def __init__(self, verdicts):
        self.verdicts = {i: v for i, v in enumerate(verdicts)}


def test_timing_histogram_defers_slider_tick_tint():
    # Fix 2 — documented + enforced deferral. Our slider PartOutcome carries
    # NO timing-delta field (std slider ticks/ends have no timing window), so
    # per-tick/per-end offsets simply do not exist to bin. This matches
    # lazer's HitEventTimingDistributionGraph, which filters
    #   HitObject.HitWindows != HitWindows.Empty
    #     && Result.IsBasic() && Result.IsHit()
    # and thus EXCLUDES slider ticks/repeats/tails from the histogram
    # entirely (and OsuColour.ForHitResult paints them Blue, not green, where
    # shown elsewhere). Deferring the tint is the faithful port, not a hidden
    # data gap.
    from dataclasses import fields
    from osu_std_renderer.ruleset.ruleset import PartOutcome
    part_fields = {f.name for f in fields(PartOutcome)}
    assert part_fields == {"time", "kind", "pos", "hit"}
    assert "delta" not in part_fields and "offset" not in part_fields
    # slider_stats yields only aggregate hit/total counts — no deltas
    sim = _Sim([_Verdict([_Part("head", True), _Part("tick", True),
                          _Part("tail", True)])])
    result = slider_stats(sim)
    assert result == (1, 1, 1, 1)          # (tick_hit,tick_total,end_hit,end_total)
    assert all(isinstance(v, int) for v in result)


def test_slider_stats_counts_ticks_and_ends():
    sim = _Sim([
        _Verdict([_Part("head", True), _Part("tick", True),
                  _Part("tick", False), _Part("repeat", True),
                  _Part("tail", True)]),
        _Verdict([_Part("head", True), _Part("tick", True),
                  _Part("tail", False)]),
    ])
    tick_hit, tick_total, end_hit, end_total = slider_stats(sim)
    assert (tick_hit, tick_total) == (3, 4)      # 2 tick + 1 repeat hit / 4
    assert (end_hit, end_total) == (1, 2)
    assert slider_stats(None) == (0, 0, 0, 0)


# --- procedural avatar (Fix 3) ------------------------------------------------------

def test_avatar_initials():
    assert avatar_initials("R3D") == "R"
    assert avatar_initials("mrekk") == "M"
    assert avatar_initials("R3D wolfie") == "RW"      # two tokens
    assert avatar_initials("cookie_zi") == "CZ"       # underscore-split
    assert avatar_initials("nathan on osu") == "NO"   # first two tokens
    assert avatar_initials("") == "?"
    assert avatar_initials("   ") == "?"


def test_avatar_hue_deterministic_and_process_independent():
    # deterministic across calls; case/whitespace-insensitive
    assert avatar_hue("YOASOBI") == avatar_hue("YOASOBI")
    assert avatar_hue("YOASOBI") == avatar_hue(" yoasobi ")
    # NOT the collision-prone sum(ord): anagrams get different hues
    assert avatar_hue("Red") != avatar_hue("Der")
    assert 0.0 <= avatar_hue("anyone") < 1.0


def test_bake_avatar_deterministic_bytes():
    import numpy as np
    a = bake_avatar(96, "R3Dwolfie")
    b = bake_avatar(96, "R3Dwolfie")
    assert np.array_equal(a, b)                        # byte-identical
    assert a.shape == (96, 96, 4)
    # different usernames → different pixels (with overwhelming probability)
    c = bake_avatar(96, "Green")
    assert not np.array_equal(a, c)
    # the disc corners are transparent (clipped to a circle)
    assert a[0, 0, 3] == 0 and a[0, -1, 3] == 0


# --- PB-card DB query ---------------------------------------------------------------

def _make_db(rows):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE renders (
        replay_md5 TEXT, beatmap_md5 TEXT, player_name TEXT, score INTEGER,
        accuracy REAL, grade TEXT, max_combo INTEGER, mods_str TEXT,
        count_300 INTEGER, count_100 INTEGER, count_50 INTEGER,
        count_miss INTEGER, deleted INTEGER DEFAULT 0)""")
    con.executemany("""INSERT INTO renders
        (replay_md5, beatmap_md5, player_name, score, accuracy, grade,
         max_combo, mods_str, count_300, count_100, count_50, count_miss,
         deleted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    con.close()
    return path


def test_query_pb_picks_best_and_excludes_current():
    path = _make_db([
        ("r1", "MD5A", "R3D", 500000, 90.0, "A", 100, "NM", 1, 0, 0, 0, 0),
        ("r2", "MD5A", "R3D", 800000, 95.0, "S", 200, "NM", 2, 0, 0, 0, 0),
        ("r3", "MD5A", "R3D", 999999, 99.0, "S", 300, "HD", 3, 0, 0, 0, 0),
        ("r4", "MD5B", "R3D", 700000, 92.0, "A", 150, "NM", 1, 0, 0, 0, 0),
    ])
    try:
        # best overall is r3 (999999)
        pb = query_pb(path, "R3D", "MD5A")
        assert pb is not None and pb["score"] == 999999 and pb["grade"] == "S"
        # excluding the current replay (r3) → next best r2
        pb = query_pb(path, "R3D", "MD5A", exclude_replay_md5="r3")
        assert pb["score"] == 800000
        # case-insensitive player match
        assert query_pb(path, "r3d", "MD5A")["score"] == 999999
    finally:
        os.unlink(path)


def test_query_pb_omit_when_none():
    path = _make_db([
        ("r1", "MD5A", "R3D", 500000, 90.0, "A", 100, "NM", 1, 0, 0, 0, 0),
        ("r2", "MD5A", "R3D", 800000, 95.0, "S", 200, "NM", 2, 0, 0, 0, 1),
    ])
    try:
        # no render for this player on this map
        assert query_pb(path, "VI0", "MD5A") is None
        # the only non-current row is deleted → omit (deleted filtered out)
        assert query_pb(path, "R3D", "MD5A", exclude_replay_md5="r1") is None
        # missing DB → fail-soft None (not an exception)
        assert query_pb("/no/such/db.sqlite", "R3D", "MD5A") is None
        # blank inputs → None
        assert query_pb(path, "", "MD5A") is None
    finally:
        os.unlink(path)


# --- style toggle -------------------------------------------------------------------

def test_results_style_default_and_toggle():
    assert StdRenderSettings().results_style == "lazer"
    assert StdRenderSettings.from_preset({"results_style": "r3d"}) \
        .results_style == "r3d"
    # unknown/absent key keeps the default
    assert StdRenderSettings.from_preset({}).results_style == "lazer"


# --- draw smoke (real PIL bakes, faked sprite batch) --------------------------------

class _FakeSpr:
    width, height = 1280, 720

    def __init__(self):
        self.textures = {}
        self.drawn = []

    def upload_texture(self, key, rgba):
        self.textures[key] = rgba.shape

    def draw(self, sprites):
        self.drawn.extend(sprites)


def _data(**kw):
    args = dict(
        player="R3D", grade="C", acc_pct=84.73, score=356457, max_combo=432,
        counts=(997, 184, 20, 52), title="Yoru ni Kakeru", artist="YOASOBI",
        diff_name="Acceptance", creator="Petal", mods=0, stars=6.49,
        pp=45.0, date_str="25 Jun 2026", ur=180.0,
        slider_ticks=(120, 130), slider_ends=(60, 66),
        err_deltas=[-30.0, 5.0, 12.0, -50.0, 80.0, -8.0], windows=(25, 60, 120),
        aim_points=[(0.0, 0.1, -0.2), (1.0, -0.4, 0.3)], perf=None, pb=None)
    args.update(kw)
    return ResultsData(**args)


def test_draw_stage1_and_stage2_run():
    spr = _FakeSpr()
    screen = LazerResultsScreen(spr, _data(), total_ms=5000.0)
    # nothing before start
    screen.draw(-1.0)
    assert spr.drawn == []
    # stage 1 (panel settled) — dim first, at DIM_ALPHA (full fade)
    spr.drawn.clear()
    screen.draw(1900.0)
    assert spr.drawn, "stage 1 drew nothing"
    dim = spr.drawn[0]
    assert dim.texture_key is None and dim.w == spr.width
    # stage 2 (fully expanded) — more sprites than stage 1 (stats panels)
    spr.drawn.clear()
    screen.draw(4800.0)
    n_expanded = len(spr.drawn)
    assert n_expanded > 0
    # all sprites finite + on a sane canvas scale
    for s in spr.drawn:
        assert math.isfinite(s.x) and math.isfinite(s.y)
        assert s.w >= 0 and s.h >= 0


def test_pb_card_only_drawn_when_present():
    # with a PB row the card bakes extra textures; without it, omitted
    spr_no = _FakeSpr()
    LazerResultsScreen(spr_no, _data(pb=None), total_ms=5000.0)
    spr_yes = _FakeSpr()
    pb = dict(player_name="R3D", score=865612, accuracy=96.8, grade="A",
              max_combo=1305, mods_str="NM")
    LazerResultsScreen(spr_yes, _data(pb=pb), total_ms=5000.0)
    assert len(spr_yes.textures) > len(spr_no.textures)
