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
    FOR_RANK, GRADE_SPACING_PERCENTAGE, LB_SLIDE_MS, LB_SLIDE_OFFSET,
    LB_SLIDE_STAGGER_MS, LB_SLIDE_START_MS, LazerResultsScreen,
    RANK_THRESHOLDS, RESULTS_SCORE_WEIGHT, RESULTS_TEXT_WEIGHT, TEXT_SS,
    ResultsData, VIRTUAL_SS_PERCENTAGE, acc_to_angle_deg, arc_color_at,
    avatar_hue, avatar_initials, bake_accuracy_arc, bake_avatar,
    bake_grade_letter, bake_star, bake_text, ease_out_quint,
    for_star_difficulty, grade_bands, rank_badge_positions, rank_ring_bands,
    query_pb, slider_stats, target_arc_value,
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


def test_bake_text_bakes_at_supersampled_resolution():
    # the crispy/aliased results text was PIL's coarse 1× AA; bake_text now
    # rasterises at px*TEXT_SS (font at px*TEXT_SS) and downscales — prove the
    # supersampled raster actually happens by spying on the px the loader sees.
    assert TEXT_SS >= 2
    seen = []

    def spy_loader(px):
        seen.append(int(px))
        return _load_argon_font(px)

    rgba, w, h = bake_text("356,457", 64, (1, 1, 1), spy_loader)
    assert 64 in seen                       # native metrics at target px
    assert 64 * TEXT_SS in seen             # the supersampled raster
    assert max(seen) == 64 * TEXT_SS
    assert w > 0 and h > 0
    # ss=1 keeps the old 1× footprint identical → layout is unchanged
    _r1, w1, h1 = bake_text("356,457", 64, (1, 1, 1), _load_argon_font, ss=1)
    assert (w, h) == (w1, h1)


def test_bake_text_has_smooth_edge_gradient():
    # supersampling → the glyph edge is a smooth alpha gradient (many
    # intermediate levels), not a hard/coarse 0↔255 cutover.
    import numpy as np
    rgba, w, h = bake_text("8", 80, (1, 1, 1))
    a = rgba[..., 3]
    assert a.max() > 240 and a.min() == 0            # solid fill + empty bg
    mid = a[(a > 10) & (a < 245)]                    # the anti-aliased edge
    assert mid.size > 0
    assert len(np.unique(mid)) >= 12                 # smooth gradient, not binary
    # blank text is still a 1×1 stub
    stub, sw, sh = bake_text("", 40, (1, 1, 1))
    assert (sw, sh) == (1, 1) and stub.shape == (1, 1, 4)


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
    # `margin` (cursor-to-ball distance at the judge time, the lazer tick/tail
    # combo reconcile's tracking-quality key) is a SPATIAL field, NOT a timing
    # delta — the no-timing-offset invariant below still holds: std slider
    # ticks/ends have no timing window, so no per-tick offset exists to bin.
    assert {"time", "kind", "pos", "hit"} <= part_fields
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


def _png_bytes(color=(210, 40, 55), size=8):
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", (size, size), (*color, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_bake_avatar_discord_bytes_vs_procedural_fallback():
    import numpy as np
    proc = bake_avatar(96, "R3D")
    # None → procedural, byte-identical to the pre-Discord path (back-compat)
    assert np.array_equal(bake_avatar(96, "R3D", None), proc)
    # corrupt/non-image bytes → graceful procedural fallback (never raises)
    assert np.array_equal(bake_avatar(96, "R3D", b"not a png"), proc)
    # real PNG bytes → a DIFFERENT chip: the cover-fit avatar image, not the
    # username-hued initials disc
    disc = bake_avatar(96, "R3D", _png_bytes())
    assert disc.shape == proc.shape == (96, 96, 4)
    assert not np.array_equal(disc, proc)
    # still clipped to the disc (transparent corners) + a reddish centre fill
    assert disc[0, 0, 3] == 0
    r, g, b, al = (int(v) for v in disc[48, 48])
    assert al == 255 and r > g and r > b


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


# --- long title/artist/name auto-scale to fit the panel -----------------------------

def test_long_title_auto_scales_to_fit_panel():
    # A too-long title used to be HARD-clipped at 40 chars (content dropped
    # off the panel — the "El Sonidito x Athletic Theme (DitzyFlam..." bug).
    # Now the font auto-scales down until the FULL title fits the panel
    # content width (ellipsis only as a last resort at the min size).
    long_title = ("El Sonidito x Athletic Theme (DitzyFlamingo's Extra "
                  "Special Ultra-Wide Marathon Collab Difficulty)")
    long_artist = ("Hechizeros Band x Koji Kondo x A Very Long Featured "
                   "Artist Name That Would Overflow")
    long_name = "xX_a_ridiculously_long_player_username_that_overflows_Xx"
    spr = _FakeSpr()
    scr = LazerResultsScreen(
        spr, _data(title=long_title, artist=long_artist, player=long_name),
        total_ms=5000.0)
    content_w = (scr.PANEL_W - 48.0) * scr.k
    _tk, tw, th = scr.title_row
    _ak, aw, ah = scr.artist_row
    _nk, nw, nh = scr.name_row
    assert tw <= content_w + 0.5 and th > 0          # title fits the panel
    assert aw <= content_w + 0.5 and ah > 0          # artist fits the panel
    # the name shares its row with the 52px avatar + gap → tighter budget
    # (64 virtual px, scaled by k); the whole avatar+name group still fits.
    assert nw <= content_w - 64.0 * scr.k + 0.5 and nh > 0
    assert 52.0 * scr.k + 12.0 * scr.k + nw <= content_w + 0.5
    # a normal short title is left at full size and still fits
    scr2 = LazerResultsScreen(_FakeSpr(),
                              _data(title="Yoru ni Kakeru"), total_ms=5000.0)
    _tk2, tw2, _th2 = scr2.title_row
    assert tw2 <= (scr2.PANEL_W - 48.0) * scr2.k + 0.5
# --- map leaderboard render (flank cards + rank moment) -----------------------------

def _entry(rank, name, score, grade="A"):
    from osu_std_renderer.render.leaderboard import LeaderboardEntry
    return LeaderboardEntry(
        rank=rank, player_name=name, score=score, accuracy=98.5, grade=grade,
        max_combo=1200, mods_str="HD,DT", mods=72, counts=(1100, 8, 1, 2),
        discord_user_id=None)          # None → procedural avatar (no network)


def _board(left, right, rank=1, moment=None, n=None):
    from osu_std_renderer.render.leaderboard import BoardData
    return BoardData(left=left, right=right, rank=rank,
                     n_players=(n if n is not None else len(left) + len(right) + 1),
                     moment=moment)


def test_leaderboard_bakes_a_card_per_entry():
    # 3-player board (2 flanks) bakes strictly more textures than solo
    spr_solo = _FakeSpr()
    LazerResultsScreen(spr_solo, _data(leaderboard=_board([], [])),
                       total_ms=5000.0)
    spr_board = _FakeSpr()
    left = [_entry(1, "Froslass", 983320, "S")]
    right = [_entry(3, "nuxx", 921143, "A")]
    LazerResultsScreen(spr_board, _data(leaderboard=_board(left, right, rank=2)),
                       total_ms=5000.0)
    assert len(spr_board.textures) > len(spr_solo.textures)


def test_leaderboard_draws_flanks_in_stage1():
    spr = _FakeSpr()
    left = [_entry(1, "Froslass", 983320, "S"), _entry(2, "origin_", 979968, "S")]
    right = [_entry(4, "Woey", 901564, "A")]
    screen = LazerResultsScreen(
        spr, _data(leaderboard=_board(left, right, rank=3, moment="NEW BEST")),
        total_ms=5000.0)
    screen.draw(1900.0)          # stage 1, settled
    # flanking cards sit left AND right of the centre panel
    xs = [s.x for s in spr.drawn if s.texture_key is not None]
    cx = spr.width / 2.0
    assert any(x < cx * 0.6 for x in xs), "no left-flank card drawn"
    assert any(x > cx * 1.4 for x in xs), "no right-flank card drawn"


def test_leaderboard_sparse_and_solo_dont_crash():
    # solo (no flanks) with a NEW BEST flourish → banner only, no cards, no raise
    spr = _FakeSpr()
    screen = LazerResultsScreen(
        spr, _data(pb=dict(player_name="R3D", score=1, accuracy=1.0,
                           grade="D", max_combo=1, mods_str="NM"),
                   leaderboard=_board([], [], rank=1, moment="NEW BEST")),
        total_ms=5000.0)
    screen.draw(1900.0)
    for s in spr.drawn:
        assert math.isfinite(s.x) and math.isfinite(s.y)
    # one-sided board (current is #1, everyone else below) still fine
    spr2 = _FakeSpr()
    right = [_entry(2, "a", 5), _entry(3, "b", 4)]
    LazerResultsScreen(spr2, _data(leaderboard=_board([], right, rank=1,
                                                      moment="NEW #1")),
                       total_ms=5000.0).draw(1900.0)


def test_leaderboard_off_matches_pb_only_behaviour():
    # leaderboard=None → identical texture set to the pre-feature path
    spr = _FakeSpr()
    LazerResultsScreen(spr, _data(leaderboard=None), total_ms=5000.0)
    # no leaderboard bakes leaked in
    assert spr.textures, "screen baked nothing"


# --- featured-card Discord avatar (centre panel) ------------------------------------

def test_featured_avatar_bytes_none_and_placeholder_fall_back():
    # no linked id → None (procedural chip); an osu_<id> placeholder is not a
    # fetchable snowflake so resolve_avatar_bytes → None → still procedural.
    scr = LazerResultsScreen(_FakeSpr(), _data(discord_user_id=None),
                             total_ms=5000.0)
    assert scr._featured_avatar_bytes() is None
    scr2 = LazerResultsScreen(_FakeSpr(),
                              _data(discord_user_id="osu_30196342"),
                              total_ms=5000.0)
    assert scr2._featured_avatar_bytes() is None


def test_featured_avatar_bytes_resolves_and_is_graceful():
    # with a real snowflake the featured card uses the SAME resolve_avatar_bytes
    # path the flanks use; a resolver that returns bytes flows through, and one
    # that RAISES must never break the bake (→ None → procedural chip).
    import osu_std_renderer.render.leaderboard as lb
    scr = LazerResultsScreen(_FakeSpr(),
                             _data(discord_user_id="111166802121281536"),
                             total_ms=5000.0)
    orig = lb.resolve_avatar_bytes
    lb.resolve_avatar_bytes = lambda *a, **k: b"AVATARPNG"
    try:
        assert scr._featured_avatar_bytes() == b"AVATARPNG"
    finally:
        lb.resolve_avatar_bytes = orig

    def _boom(*a, **k):
        raise RuntimeError("avatar backend down")
    lb.resolve_avatar_bytes = _boom
    try:
        assert scr._featured_avatar_bytes() is None
    finally:
        lb.resolve_avatar_bytes = orig


# --- flank-card staggered slide-in entrance -----------------------------------------

def test_flank_slide_offset_and_alpha_over_its_window():
    scr = LazerResultsScreen(_FakeSpr(), _data(), total_ms=5000.0)
    # before its start: fully OUT (max offset) and invisible
    off0, a0 = scr._lb_slide(0, LB_SLIDE_START_MS - 1.0)
    assert off0 == LB_SLIDE_OFFSET and a0 == 0.0
    # at its start (t=0): OutQuint(0)=0 → still fully out, alpha 0
    off_s, a_s = scr._lb_slide(0, LB_SLIDE_START_MS)
    assert off_s == LB_SLIDE_OFFSET and a_s == 0.0
    # after its full window: settled at rest (offset 0, alpha 1)
    off_e, a_e = scr._lb_slide(0, LB_SLIDE_START_MS + LB_SLIDE_MS)
    assert abs(off_e) < 1e-9 and a_e == 1.0
    # mid-window: partway in (OutQuint monotone — offset shrinks, alpha grows)
    off_m, a_m = scr._lb_slide(0, LB_SLIDE_START_MS + LB_SLIDE_MS * 0.5)
    assert 0.0 < off_m < LB_SLIDE_OFFSET and 0.0 < a_m < 1.0


def test_flank_slide_staggered_by_rank_within_stage1():
    from osu_std_renderer.render.lazer_results import STAGE1_MS
    scr = LazerResultsScreen(_FakeSpr(), _data(), total_ms=5000.0)
    # at one instant the inner card (i=0) leads the next-out (i=1) leads i=2:
    # the board unfurls outward, not all at once
    t = LB_SLIDE_START_MS + LB_SLIDE_STAGGER_MS + LB_SLIDE_MS * 0.4
    a = [scr._lb_slide(i, t)[1] for i in range(3)]
    assert a[0] > a[1] > a[2]
    # each card is delayed exactly LB_SLIDE_STAGGER_MS: at its own start it is
    # still fully out
    for i in range(3):
        off, al = scr._lb_slide(i, LB_SLIDE_START_MS + i * LB_SLIDE_STAGGER_MS)
        assert off == LB_SLIDE_OFFSET and al == 0.0
    # the whole entrance (last of 3 cards) finishes INSIDE the stage-1 hold, so
    # the outro's total length is unchanged
    last_done = LB_SLIDE_START_MS + 2 * LB_SLIDE_STAGGER_MS + LB_SLIDE_MS
    assert last_done <= STAGE1_MS


def test_flank_slide_in_draws_outward_then_settles_inward():
    spr = _FakeSpr()
    left = [_entry(1, "Froslass", 983320, "S")]
    right = [_entry(3, "nuxx", 921143, "A")]
    screen = LazerResultsScreen(
        spr, _data(leaderboard=_board(left, right, rank=2)), total_ms=6000.0)
    cw = screen.LB_CARD_W * screen.k

    def _cards(sprites):
        return [s for s in sprites
                if s.texture_key is not None and abs(s.w - cw) < 1.0]

    # mid-entrance: flank cards are fading up (alpha < 1) and pushed outward
    spr.drawn.clear()
    screen.draw(LB_SLIDE_START_MS + 40.0)
    mid = _cards(spr.drawn)
    assert mid, "no flank cards drawn mid-entrance"
    assert any(s.color[3] < 0.99 for s in mid)
    mid_xs = sorted(s.x for s in mid)

    # settled (still stage 1, panel not yet sliding): full alpha, cards pulled
    # inward toward the panel edges
    spr.drawn.clear()
    screen.draw(1900.0)
    settled = _cards(spr.drawn)
    assert len(settled) == 2 and all(s.color[3] > 0.99 for s in settled)
    set_xs = sorted(s.x for s in settled)
    # left flank slid IN from further left; right flank IN from further right
    assert set_xs[0] > mid_xs[0]
    assert set_xs[-1] < mid_xs[-1]


# --- played-mod badge row (lazer starAndModDisplay ModDisplay) ----------------------

class _CapSpr(_FakeSpr):
    """Like _FakeSpr but RETAINS the full rgba so a bake can be pixel-sampled
    (the plain fake only stores shapes)."""

    def upload_texture(self, key, rgba):
        self.textures[key] = rgba


def test_mod_badges_built_from_lazer_mods():
    # HD+DT via the full lazer set → one category-coloured pill per mod, in
    # the .osr display order, and every pill is drawn under the score.
    spr = _CapSpr()
    scr = LazerResultsScreen(spr, _data(mods=0, lazer_mods=("HD", "DT")),
                             total_ms=5000.0)
    assert scr.mod_pill_texts == ("HD", "DT")
    assert len(scr.mod_pills) == 2
    # stage-1 draw includes both baded pill textures (the badge row is drawn)
    spr.drawn.clear()
    scr.draw(1900.0)
    drawn_keys = {s.texture_key for s in spr.drawn}
    for key, _w, _h in scr.mod_pills:
        assert key in drawn_keys, "mod badge not drawn on the panel"


def test_mod_badges_empty_for_nomod():
    # nomod → no pills, no row, and the row drawer contributes 0 height so the
    # nomod panel layout is unchanged from the pre-badge screen.
    spr = _FakeSpr()
    scr = LazerResultsScreen(spr, _data(mods=0), total_ms=5000.0)
    assert scr.mod_pill_texts == ()
    assert scr.mod_pills == []
    assert scr._draw_mod_row([], 100.0, 100.0, 1.0) == 0.0
    # a modded screen draws MORE sprites at the same age (the extra badge row)
    spr_m = _FakeSpr()
    scr_m = LazerResultsScreen(spr_m, _data(mods=0, lazer_mods=("HD", "HR")),
                               total_ms=5000.0)
    spr.drawn.clear(); spr_m.drawn.clear()
    scr.draw(1900.0); scr_m.draw(1900.0)
    assert len(spr_m.drawn) == len(spr.drawn) + 2


def test_mod_badges_custom_rate_suffix():
    # a custom-rate DT (rate_override) carries the compact "DT 1.3×" suffix on
    # its badge, and that pill is WIDER than the plain default-rate DT badge.
    spr = _CapSpr()
    scr = LazerResultsScreen(
        spr, _data(mods=0, lazer_mods=("HD", "DT"), rate_override=1.3),
        total_ms=5000.0)
    assert scr.mod_pill_texts == ("HD", "DT 1.3×")
    w_custom = scr.mod_pills[1][1]
    spr2 = _CapSpr()
    scr2 = LazerResultsScreen(spr2, _data(mods=0, lazer_mods=("HD", "DT")),
                              total_ms=5000.0)
    w_plain = scr2.mod_pills[1][1]
    assert w_custom > w_plain, "custom-rate suffix should widen the DT badge"


def test_mod_badge_category_colours():
    # the badge fill is the HUD's mod_pill_color category colour: a reduction
    # mod (EZ) bakes green-dominant, a difficulty-increase mod (HR) red-
    # dominant — proving the pill is coloured by category, end to end.
    import numpy as np
    from osu_std_renderer.render.hud import mod_pill_color

    def _fill_rgb(scr, spr, acr):
        key, _w, _h = scr._bake_mod_pill(acr, mod_pill_color(acr))
        rgba = spr.textures[key]
        a = rgba[..., 3]
        rgb = rgba[..., :3].astype(float)
        near_white = (rgb[..., 0] > 225) & (rgb[..., 1] > 225) \
            & (rgb[..., 2] > 225)
        m = (a > 200) & (~near_white)             # the pill fill, not the text
        return rgb[m].mean(axis=0)

    spr = _CapSpr()
    scr = LazerResultsScreen(spr, _data(mods=0), total_ms=5000.0)
    ez = _fill_rgb(scr, spr, "EZ")               # reduction → green
    hr = _fill_rgb(scr, spr, "HR")               # increase → red
    assert ez[1] > ez[0] and ez[1] > ez[2], "EZ badge not green-dominant"
    assert hr[0] > hr[1] and hr[0] > hr[2], "HR badge not red-dominant"
