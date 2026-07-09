"""LAZER RESULTS SCREEN — the osu!(lazer) ranking screen ported to the std
renderer (owner spec 2026-07; the std default outro, R3D card behind
`results_style=r3d`).

PORTED (layout/animation semantics only — MIT, ppy/osu, no ppy assets;
every texture is procedurally baked in-repo, fonts = the bundled Nunito, our
lazer-Torus stand-in — the results screen is CLIENT UI, skin-independent, so
it uses the lazer font family, NOT the DejaVu of the skinnable gameplay HUD):

  osu.Game/Screens/Ranking/ScorePanel.cs
  osu.Game/Screens/Ranking/Expanded/ExpandedPanelMiddleContent.cs
  osu.Game/Screens/Ranking/Expanded/Accuracy/AccuracyCircle.cs
  osu.Game/Screens/Ranking/Expanded/Accuracy/GradedCircles.cs
  osu.Game/Screens/Ranking/Expanded/Accuracy/RankBadge.cs
      — ported 1:1 to the reference: the achieved-accuracy arc is the thick
        outer "Accuracy circle", a FIXED vertical CYAN→GREEN gradient
        (#7CF6FF→#BAFFA9, NOT rank-coloured), sweeping 0→acc over
        ACCURACY_TRANSFORM_DURATION with a light tip dash; behind/inside it
        the THIN GradedCircles rank ring shows the OsuColour.ForRank bands
        (D ff5a5a, C ff8e5d, B e3b130, A 88da20, S 02b5c3, X/SS de31ae) with
        the GRADE_SPACING_PERCENTAGE (2/360) notches and the SS/X band owning
        the 0.99→1.0 virtual-SS slice; six RankBadge pills sit OUTSIDE at
        their Interpolation.Lerp visual positions (rank_badge_positions), so
        they spread D→C→B→A→S→SS around the ring without piling up; the white
        centre rank letter punches in at the end of the sweep.
  osu.Game/Screens/Ranking/Expanded/StarRatingDisplay + AccuracyStatistic
  osu.Game/Screens/Ranking/Statistics/PerformanceBreakdown.cs   (bars)
  osu.Game.Rulesets.Osu/Statistics/{HitEventTimingDistribution,
      AccuracyHeatmap}.cs                                (histogram, scatter)

TWO STAGES (owner: "appear then open from screenshot 1 → 2"):
  Stage 1  the centred score panel fades in; the accuracy circle sweeps,
           the grade punches, the score rolls (RollingCounter). Settles
           and holds.
  Stage 2  the panel slides LEFT (OutQuint) while the three statistics
           panels unfold from the right, staggered — Performance
           Breakdown, Timing Distribution, Accuracy Heatmap. Held for the
           remainder of results_screen_time.

Everything is laid out in a 1080-height virtual space (uw = w / k wide,
k = h / 1080) and scaled to the frame, exactly like the HUD. Static text
and the ring base are baked once; the accuracy arc and the rolling score
re-bake only while they change (the HUD hp-bar re-upload pattern).

HONEST DEVIATIONS (documented, owner-visible):
  * no avatar source (osu!API is off-limits) → a procedural avatar keyed on
    the username (hash→hue + centred initials + ring);
  * the timing distribution tints by hit-error window (300/100/50), matching
    lazer's HitEventTimingDistributionGraph — which stacks each bin's basic
    judgments coloured via OsuColour.ForHitResult (Great→Blue 66ccff,
    Ok→Green 88b300, Meh→Yellow ffcc22). It does NOT separately tint slider
    ticks/ends: that graph FILTERS its events with
        HitObject.HitWindows != HitWindows.Empty
        && Result.IsBasic() && Result.IsHit()
    which excludes slider ticks/repeats/tails (empty hit windows, non-basic
    results) outright — and where those results DO appear elsewhere
    ForHitResult paints them Blue (66ccff, same as Great), not green. In the
    std ruleset slider ticks/ends carry no timing window, so our PartOutcome
    holds no per-tick/per-end delta to bin. The slider-tick/end tint is thus
    DEFERRED as the faithful choice (adding it would be both un-portable and
    un-lazer) — not a data gap we papered over. (osu.Game/Screens/Ranking/
    Statistics/HitEventTimingDistributionGraph.cs; OsuColour.ForHitResult.)
  * the accuracy % / max-combo / hit-result cells appear with the panel
    (only the score rolls) — the prominent RollingCounter, kept cheap.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .gl import Sprite
from .hud import (BAND_50, BAND_100, BAND_300, build_mod_pills,
                  mod_pill_color)
from .results import histogram_bins, mods_string
from .textures import ARGON_FONT_PATH, _load_argon_font, _load_font

UH = 1080.0                    # virtual design height (HUD convention)

# --- timeline (ms from results start) -----------------------------------------------
FADE_MS = 320.0                # panel opacity ramp
SWEEP_DELAY_MS = 260.0         # accuracy arc sweep start
SWEEP_MS = 1150.0              # AccuracyCircle ACCURACY_TRANSFORM_DURATION (scaled)
BADGE_MS = 340.0               # rank badge (grade letter) punch duration
STAGE1_MS = 2000.0            # panel settles + holds until here, then opens
OPEN_MS = 900.0                # panel slide + stats unfold (OutQuint)
STAGGER_MS = 160.0             # per-stats-panel unfold stagger
MIN_TOTAL_MS = 3800.0         # floor so both stages + a hold always fit

DIM_ALPHA = 0.72               # scene dim under the results screen

# mod badges (the played-mods row under the score — lazer's starAndModDisplay
# ModDisplay in ExpandedPanelMiddleContent). Category colour comes from the
# HUD's mod_pill_color so the badges match the gameplay HUD pills exactly.
MOD_PILL_VH = 34.0             # results mod-badge height (virtual px)
MOD_PILL_TEXT_VPX = 19.0       # acronym text size (virtual px)
MOD_PILL_GAP_V = 8.0           # inter-badge gap (virtual px)
MOD_PILL_ALPHA = 235           # pill fill alpha (0..255), lazer-ish opacity

VIRTUAL_SS_PERCENTAGE = 0.01   # AccuracyCircle: the reserved SS notch

# rank accuracy thresholds (osu.Game.Rulesets.Scoring, std) — the LOWER
# bound of each grade's band; the ring colours [t, next) with the grade
# you earn at that accuracy.
RANK_THRESHOLDS = [
    (0.00, "D"),
    (0.70, "C"),
    (0.80, "B"),
    (0.90, "A"),
    (0.95, "S"),
    (1.00, "SS"),
]

# hit-result colours (lazer OsuColour.ForHitResult, the repo's ported set)
RESULT_COLORS = {
    "GREAT": BAND_300,
    "OK": BAND_100,
    "MEH": BAND_50,
    "MISS": (0.95, 0.25, 0.30),
}


def _hex(s: str) -> tuple[float, float, float]:
    """'#rrggbb' / 'rrggbb' → linear-free 0..1 RGB tuple."""
    s = s.lstrip("#")
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0,
            int(s[4:6], 16) / 255.0)


# The rank ring colours are lazer's OsuColour.ForRank — the EXACT hexes from
# osu.Game/Graphics/OsuColour.cs (ForRank), which AccuracyCircle's GradedCircles
# paints as the D→C→B→A→S→X bands around the ring. (These differ from the
# gameplay GRADE_COLORS legacy palette; ForRank is lazer's ranking-screen set.)
FOR_RANK = {
    "D": _hex("ff5a5a"),   # ScoreRank.D
    "C": _hex("ff8e5d"),   # ScoreRank.C
    "B": _hex("e3b130"),   # ScoreRank.B
    "A": _hex("88da20"),   # ScoreRank.A
    "S": _hex("02b5c3"),   # ScoreRank.S / SH
    "SS": _hex("de31ae"),  # ScoreRank.X / XH  (SS)
    # F = FAIL: lazer's ScoreRank enum has no F (it bottoms at D), but
    # osu!STABLE shows a red F for a failed play — the owner's pick. Reuse
    # the ForRank fail red (== ScoreRank.D ff5a5a) for the centre letter +
    # its rank glow; target_arc_value caps a non-SS at the virtual-SS notch.
    "F": _hex("ff5a5a"),   # osu!stable fail red
}
# alias the lazer rank-letter keys the meta layer may hand us
FOR_RANK["X"] = FOR_RANK["SS"]
FOR_RANK["SSH"] = FOR_RANK["SS"]
FOR_RANK["XH"] = FOR_RANK["SS"]
FOR_RANK["SH"] = FOR_RANK["S"]

# AccuracyCircle.GRADE_SPACING_PERCENTAGE = 2.0 / 360 — GradedCircles insets
# each band by half of this on each side, opening the boundary notches.
GRADE_SPACING_PERCENTAGE = 2.0 / 360.0

# AccuracyCircle "Accuracy circle": the achieved-accuracy arc is a FIXED
# vertical gradient (NOT rank-coloured) — Color4Extensions.FromHex #7CF6FF
# (top) → #BAFFA9 (bottom). The rank colours live on the thin inner
# GradedCircles ring behind it.
ARC_GRAD_TOP = _hex("7CF6FF")
ARC_GRAD_BOT = _hex("BAFFA9")

# accuracy-circle geometry as a fraction of the (square) bake canvas S.
# Outer thick achieved arc, thin inner graded rank ring, badge pills outside.
ACC_ARC_R = 0.350         # achieved-arc centreline radius
ACC_ARC_W = 0.066         # achieved-arc thickness
ACC_GRAD_R = 0.293        # inner graded-ring centreline (≈0.8× → sits inside)
ACC_GRAD_W = 0.020        # inner graded-ring thickness (thin)
ACC_BADGE_R = 0.435       # badge-pill centre radius (outside the arc)
ACC_BADGE_W = 0.088       # badge-pill width
ACC_BADGE_H = 0.050       # badge-pill height

# results-screen text weights (Nunito variable-font `wght`, our lazer
# OsuFont.Torus stand-in). Body/labels are Medium; the big rolling score is
# Light — matching lazer's thin geometric score counter (OsuFont.Torus Light).
RESULTS_TEXT_WEIGHT = 500        # Nunito Medium
RESULTS_SCORE_WEIGHT = 330       # Nunito Light (thin, big)

# Supersample factors for the procedurally-baked results text/shapes. PIL's
# default 1× rasteriser gives too-coarse anti-aliasing — the settled panel
# read "crispy"/stair-stepped when the owner zoomed in (score digits, thin
# labels). Every text/shape element is instead rasterised at N× the target
# px (font + geometry at N×) and downscaled to the native footprint with
# LANCZOS → smooth AA that holds up zoomed and at any output scale. The
# native (w, h) footprint is unchanged, so layout/positioning is identical.
# Text is the priority (TEXT_SS); the accuracy ring/arc are larger and the
# arc re-bakes while it sweeps, so a lighter factor bounds their cost
# (SHAPE_SS) while still smoothing the big curve.
TEXT_SS = 3
SHAPE_SS = 2


def _nunito_loader(weight: int):
    """A (px)->font loader for the bundled Nunito at a given variable `wght`
    (lazer Torus stand-in). Falls back to DejaVu if the asset is missing so a
    stripped checkout still renders."""
    from PIL import ImageFont

    def _load(px: int):
        try:
            f = ImageFont.truetype(ARGON_FONT_PATH, max(int(px), 6))
        except OSError:
            return _load_font(max(int(px), 6))
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # noqa: BLE001 — non-variable build → default face
            pass
        return f

    return _load


# OsuColour.ForStarDifficulty gradient stops (star, hex) — the star-rating
# pill background colour. (osu.Game/Graphics/OsuColour.cs ForStarDifficulty.)
STAR_SPECTRUM = [
    (0.1, "aaaaaa"), (0.1, "4290fb"), (1.25, "4fc0ff"), (2.0, "4fffd5"),
    (2.5, "7cff4f"), (3.3, "f6f05c"), (4.2, "ff8068"), (4.9, "ff3c71"),
    (5.8, "6563de"), (6.7, "18158e"), (7.7, "000000"), (9.0, "000000"),
]


# --- pure helpers (unit-tested) -----------------------------------------------------

def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_quint(p: float) -> float:
    p = _clamp01(p)
    return 1.0 - (1.0 - p) ** 5


def ease_out_cubic(p: float) -> float:
    p = _clamp01(p)
    return 1.0 - (1.0 - p) ** 3


def grade_bands() -> list[tuple[float, float, str]]:
    """[(acc_lo, acc_hi, grade)] partition of [0,1] by rank threshold —
    the colour of each ring band = the grade earned in that accuracy
    range."""
    out = []
    for i, (lo, g) in enumerate(RANK_THRESHOLDS[:-1]):
        hi = RANK_THRESHOLDS[i + 1][0]
        out.append((lo, hi, g))
    return out


def rank_ring_bands() -> list[tuple[float, float, str]]:
    """[(acc_lo, acc_hi, grade)] — lazer's GradedCircles band layout for the
    ring (AccuracyCircle.cs). Same rank thresholds as grade_bands(), but the
    S band stops at 1 − VIRTUAL_SS_PERCENTAGE and a distinct SS/X band owns
    the final 0.99→1.0 slice (the virtual-SS notch). This is the layout the
    rank-colour arc + graded background use."""
    ss_lo = 1.0 - VIRTUAL_SS_PERCENTAGE            # 0.99
    return [
        (0.00, 0.70, "D"),
        (0.70, 0.80, "C"),
        (0.80, 0.90, "B"),
        (0.90, 0.95, "A"),
        (0.95, ss_lo, "S"),
        (ss_lo, 1.00, "SS"),
    ]


def arc_color_at(acc: float) -> tuple[float, float, float]:
    """The rank colour painted on the achieved arc at accuracy `acc` — the
    ForRank stop for whichever band `acc` lands in (AccuracyCircle paints the
    ring through the D→C→B→A→S→X rank colours)."""
    acc = _clamp01(acc)
    for lo, hi, g in rank_ring_bands():
        if lo <= acc < hi:
            return FOR_RANK[g]
    return FOR_RANK["SS"]


def rank_badge_positions() -> list[tuple[float, str]]:
    """[(visual_acc, grade)] — the badge placement from lazer AccuracyCircle.cs.
    Each RankBadge sits at its band's Interpolation.Lerp VISUAL position (NOT
    the band boundary), so the six badges spread cleanly around the ring
    instead of piling up at the thresholds:

        RankBadge(accuracyD, Lerp(accuracyD, accuracyC, 0.5),  D)
        RankBadge(accuracyC, Lerp(accuracyC, accuracyB, 0.5),  C)
        RankBadge(accuracyB, Lerp(accuracyB, accuracyA, 0.5),  B)
        RankBadge(accuracyA, Lerp(accuracyA, accuracyS, 0.25), A)
        RankBadge(accuracyS, Lerp(accuracyS, accuracyX−VSS, 0.25), S)
        RankBadge(accuracyX, accuracyX, X/SS)

    (accuracyD 0.0, C 0.70, B 0.80, A 0.90, S 0.95, X 1.0.)"""
    ss_lo = 1.0 - VIRTUAL_SS_PERCENTAGE            # accuracyX − VIRTUAL_SS
    return [
        (_lerp(0.00, 0.70, 0.5), "D"),             # 0.35
        (_lerp(0.70, 0.80, 0.5), "C"),             # 0.75
        (_lerp(0.80, 0.90, 0.5), "B"),             # 0.85
        (_lerp(0.90, 0.95, 0.25), "A"),            # 0.9125
        (_lerp(0.95, ss_lo, 0.25), "S"),           # 0.96
        (1.00, "SS"),                              # accuracyX
    ]


def for_star_difficulty(stars: float) -> tuple[float, float, float]:
    """The star-rating pill colour — a linear sample of OsuColour's
    ForStarDifficulty gradient (STAR_SPECTRUM)."""
    s = max(float(stars), 0.0)
    if s <= STAR_SPECTRUM[0][0]:
        return _hex(STAR_SPECTRUM[0][1])
    for i in range(1, len(STAR_SPECTRUM)):
        s0, h0 = STAR_SPECTRUM[i - 1]
        s1, h1 = STAR_SPECTRUM[i]
        if s <= s1:
            t = 0.0 if s1 == s0 else (s - s0) / (s1 - s0)
            c0, c1 = _hex(h0), _hex(h1)
            return (_lerp(c0[0], c1[0], t), _lerp(c0[1], c1[1], t),
                    _lerp(c0[2], c1[2], t))
    return _hex(STAR_SPECTRUM[-1][1])


def target_arc_value(acc_frac: float, grade: str) -> float:
    """AccuracyCircle target fill: a true SS closes the ring, everything
    else caps at 1 − VIRTUAL_SS_PERCENTAGE so the SS notch stays open."""
    acc_frac = _clamp01(acc_frac)
    if grade in ("SS", "X", "SSH", "XH"):
        return acc_frac
    return min(1.0 - VIRTUAL_SS_PERCENTAGE, acc_frac)


def acc_to_angle_deg(acc: float) -> float:
    """Accuracy [0,1] → PIL degrees measured from 3 o'clock clockwise,
    starting the ring at 12 o'clock (top). 0% = top, 100% = full turn."""
    return 270.0 + _clamp01(acc) * 360.0


def slider_stats(sim, before: float | None = None
                 ) -> tuple[int, int, int, int]:
    """(tick_hit, tick_total, end_hit, end_total) over every slider
    verdict's part outcomes — the SLIDER TICK n/N + SLIDER END n/N rows.
    ticks include repeats (tick-equivalent in the ruleset).

    `before` (a FAIL death time in ms): count only sliders that STARTED
    before the death point — the parts the player actually reached."""
    tick_hit = tick_total = end_hit = end_total = 0
    if sim is None:
        return 0, 0, 0, 0
    for v in sim.verdicts.values():
        if before is not None and v.start_time >= before:
            continue
        for p in v.parts:
            if p.kind in ("tick", "repeat"):
                tick_total += 1
                if p.hit:
                    tick_hit += 1
            elif p.kind == "tail":
                end_total += 1
                if p.hit:
                    end_hit += 1
    return tick_hit, tick_total, end_hit, end_total


def query_pb(db_path, player_name: str, beatmap_md5: str,
             exclude_replay_md5: str | None = None) -> dict | None:
    """The player's best PREVIOUS render of this map from the R3D render
    DB (read-only) — the LOCAL-ONLY PB card (owner decision, no osu!API).
    Highest non-deleted score for player_name+beatmap_md5, optionally
    excluding the current replay. None when there's no prior render (the
    card is then omitted) or the DB is unavailable (fail-soft)."""
    if not player_name or not beatmap_md5:
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                              timeout=2.0)
    except Exception:  # noqa: BLE001 — DB missing/locked → no card
        return None
    try:
        cur = con.cursor()
        sql = ("SELECT player_name, score, accuracy, grade, max_combo, "
               "mods_str, count_300, count_100, count_50, count_miss "
               "FROM renders WHERE player_name = ? COLLATE NOCASE "
               "AND beatmap_md5 = ? AND deleted = 0")
        params: list = [player_name, beatmap_md5]
        if exclude_replay_md5:
            sql += " AND replay_md5 != ?"
            params.append(exclude_replay_md5)
        sql += " ORDER BY score DESC LIMIT 1"
        row = cur.execute(sql, params).fetchone()
    except Exception:  # noqa: BLE001 — schema drift → no card
        return None
    finally:
        con.close()
    if row is None:
        return None
    keys = ("player_name", "score", "accuracy", "grade", "max_combo",
            "mods_str", "count_300", "count_100", "count_50", "count_miss")
    return dict(zip(keys, row))


# --- procedural bakes (PIL → HxWx4 uint8) -------------------------------------------

def _to_rgba(img: "Image.Image"):
    import numpy as np
    return np.asarray(img.convert("RGBA"), dtype="u1").copy()


def bake_text(text: str, px: int, color, loader=_load_font,
              ss: int = TEXT_SS) -> tuple:
    """One baked text line → (rgba, w, h). Blank text → 1×1 stub.

    Supersampled: the glyphs are rasterised at `px*ss` with the font at
    `px*ss`, then LANCZOS-downscaled to the native footprint — high-quality
    smooth AA that stays clean when the results panel is viewed zoomed / at
    any output scale (PIL's default 1× AA read as "crispy"/stair-stepped).
    The returned (w, h) is the native footprint, so layout/positioning is
    unchanged from the un-supersampled bake. `ss=1` = the old 1× path."""
    import numpy as np
    if not text:
        return np.zeros((1, 1, 4), dtype="u1"), 1, 1
    px = max(int(px), 6)
    # native footprint (unchanged layout metrics + downscale target)
    font = loader(px)
    try:
        x0, y0, x1, y1 = font.getbbox(text)
    except AttributeError:
        w0, h0 = font.getsize(text)          # type: ignore[attr-defined]
        x0, y0, x1, y1 = 0, 0, w0, h0
    pad = max(px // 12, 2)
    w = max(x1 - x0, 1) + 2 * pad
    h = max(y1 - y0, 1) + 2 * pad
    rgb = tuple(int(round(c * 255)) for c in color)
    ss = max(int(ss), 1)
    if ss == 1:
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((pad - x0, pad - y0), text, font=font,
                                 fill=(*rgb, 255))
        return _to_rgba(img), w, h
    # supersampled raster at px*ss, LANCZOS-downscaled to the native (w, h)
    fb = loader(px * ss)
    try:
        bx0, by0, bx1, by1 = fb.getbbox(text)
    except AttributeError:
        bw0, bh0 = fb.getsize(text)          # type: ignore[attr-defined]
        bx0, by0, bx1, by1 = 0, 0, bw0, bh0
    padb = pad * ss
    Wb = max(bx1 - bx0, 1) + 2 * padb
    Hb = max(by1 - by0, 1) + 2 * padb
    big = Image.new("RGBA", (Wb, Hb), (0, 0, 0, 0))
    ImageDraw.Draw(big).text((padb - bx0, padb - by0), text, font=fb,
                             fill=(*rgb, 255))
    img = big.resize((w, h), Image.LANCZOS)
    return _to_rgba(img), w, h


def bake_round_panel(w: int, h: int, radius: int, top, bot,
                     alpha: float, border=None):
    """Rounded panel with a vertical top→bot gradient at `alpha`, an
    optional 1px border. Baked once."""
    import numpy as np
    w = max(int(w), 2)
    h = max(int(h), 2)
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = grad.load()
    a = int(round(_clamp01(alpha) * 255))
    for y in range(h):
        f = y / max(h - 1, 1)
        r = int(round(_lerp(top[0], bot[0], f) * 255))
        g = int(round(_lerp(top[1], bot[1], f) * 255))
        b = int(round(_lerp(top[2], bot[2], f) * 255))
        for x in range(w):
            px[x, y] = (r, g, b, a)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=radius, fill=255)
    grad.putalpha(mask)
    if border is not None:
        br = tuple(int(round(c * 255)) for c in border)
        ImageDraw.Draw(grad).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=radius, outline=(*br, 160), width=2)
    return _to_rgba(grad)


def _name_hash(name: str) -> int:
    """Stable, process-independent hash of a username → non-negative int.
    Uses hashlib (NOT builtin hash(), which is PYTHONHASHSEED-salted, and
    NOT sum(ord) which collides on anagrams) so an avatar is deterministic
    across runs/machines."""
    import hashlib
    key = (name or "?").strip().lower().encode("utf-8", "ignore")
    return int(hashlib.md5(key).hexdigest(), 16)


def avatar_hue(name: str) -> float:
    """Deterministic hue in [0,1) derived from the username hash."""
    return (_name_hash(name) % 360) / 360.0


def avatar_initials(name: str) -> str:
    """1–2 uppercase initials: first letters of the first two word-tokens,
    else the first character of a single token ('?' when empty)."""
    import re
    name = (name or "").strip()
    if not name:
        return "?"
    tokens = [t for t in re.split(r"[\s_.\-]+", name) if t]
    if len(tokens) >= 2:
        return (tokens[0][0] + tokens[1][0]).upper()
    return tokens[0][0].upper()


def bake_avatar(px: int, name: str, *_ignored):
    """Procedural avatar chip (no osu!API): a deterministic username-hued
    disc with a subtle vertical shade, a soft ring, and the centred
    initial(s) in the repo font — the osu! default-avatar feel. Deterministic:
    identical `name` → byte-identical chip. (Extra positional args are
    accepted and ignored for back-compat with the old (px, initial, seed)
    signature.)"""
    px = max(int(px), 8)
    h = _name_hash(name)
    hue = (h % 360) / 360.0
    sat = 0.42 + ((h >> 9) % 18) / 100.0            # 0.42..0.59, hash-varied
    c0 = _hsv(hue, sat, 0.62)                       # top (lighter)
    c1 = _hsv((hue + 0.06) % 1.0, min(sat + 0.08, 1.0), 0.34)   # bottom (dark)
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(px):
        f = y / max(px - 1, 1)
        col = tuple(int(round(_lerp(c0[i], c1[i], f) * 255)) for i in range(3))
        d.line([(0, y), (px, y)], fill=(*col, 255))
    # clip to a disc
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, px - 1, px - 1], fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    # soft ring (a lighter tint of the hue)
    ring = _hsv(hue, max(sat - 0.16, 0.0), 0.92)
    rc = tuple(int(round(c * 255)) for c in ring)
    lw = max(int(px * 0.045), 2)
    off = lw * 0.5
    d.ellipse([off, off, px - 1 - off, px - 1 - off],
              outline=(*rc, 150), width=lw)
    # centred initial(s)
    ini = avatar_initials(name)
    font = _load_font(int(px * (0.46 if len(ini) >= 2 else 0.56)))
    try:
        x0, y0, x1, y1 = font.getbbox(ini)
    except AttributeError:
        x1, y1 = font.getsize(ini); x0 = y0 = 0     # type: ignore
    d.text(((px - (x1 - x0)) / 2 - x0, (px - (y1 - y0)) / 2 - y0), ini,
           font=font, fill=(255, 255, 255, 240))
    return _to_rgba(img)


def _hsv(h: float, s: float, v: float) -> tuple[float, float, float]:
    import colorsys
    return colorsys.hsv_to_rgb(h, s, v)


def bake_grade_letter(text: str, px: int, fill, glow, loader=_load_font,
                      ss: int = TEXT_SS):
    """The AccuracyCircle centre rank letter: WHITE fill with a soft
    rank-coloured outer glow — lazer DrawableRank's coloured EdgeEffect/Glow
    (osu.Game/Scoring/Drawables/DrawableRank + the AccuracyCircle centre). A
    tight bright edge + a wider soft halo in `glow` (the ForRank colour),
    then the white glyph on top. Supersampled at `px*ss` (glyph + blur radii)
    then LANCZOS-downscaled to the native footprint so the white glyph edge
    stays smooth when zoomed. Returns (rgba, w, h)."""
    from PIL import ImageFilter
    px = max(int(px), 8)
    ss = max(int(ss), 1)
    # native footprint (return size + downscale target)
    font = loader(px)
    try:
        x0, y0, x1, y1 = font.getbbox(text)
    except AttributeError:
        x1, y1 = font.getsize(text); x0 = y0 = 0     # type: ignore
    tw = max(x1 - x0, 1)
    th = max(y1 - y0, 1)
    pad = max(int(px * 0.42), 8)
    W = tw + 2 * pad
    H = th + 2 * pad
    # supersampled build (font, pads, blur radii all ×ss)
    bpx = px * ss
    fb = loader(bpx)
    try:
        bx0, by0, bx1, by1 = fb.getbbox(text)
    except AttributeError:
        bx1, by1 = fb.getsize(text); bx0 = by0 = 0   # type: ignore
    btw = max(bx1 - bx0, 1)
    bth = max(by1 - by0, 1)
    padb = pad * ss
    Wb = btw + 2 * padb
    Hb = bth + 2 * padb
    ox, oy = padb - bx0, padb - by0
    gc = tuple(int(round(c * 255)) for c in glow)
    glyph = Image.new("RGBA", (Wb, Hb), (0, 0, 0, 0))
    ImageDraw.Draw(glyph).text((ox, oy), text, font=fb, fill=(*gc, 255))
    tight = glyph.filter(ImageFilter.GaussianBlur(max(bpx * 0.045, 1)))
    wide = glyph.filter(ImageFilter.GaussianBlur(max(bpx * 0.11, 2)))
    out = Image.new("RGBA", (Wb, Hb), (0, 0, 0, 0))
    for layer in (wide, wide, tight, tight):          # stack → a bright halo
        out = Image.alpha_composite(out, layer)
    fc = tuple(int(round(c * 255)) for c in fill)
    ImageDraw.Draw(out).text((ox, oy), text, font=fb, fill=(*fc, 255))
    if ss != 1:
        out = out.resize((W, H), Image.LANCZOS)
    return _to_rgba(out), W, H


def bake_star(px: int, color):
    """A filled 5-point star sprite (the repo font has no ★ glyph, so the
    star-rating icon is drawn, not typed). Supersampled for clean edges."""
    S = max(int(px), 8)
    ss = 4
    big = Image.new("RGBA", (S * ss, S * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    cx = cy = S * ss / 2.0
    r_out = S * ss * 0.5
    r_in = r_out * 0.42
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        ang = math.radians(-90 + i * 36)          # first point straight up
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    col = tuple(int(round(c * 255)) for c in color)
    d.polygon(pts, fill=(*col, 255))
    big = big.resize((S, S), Image.LANCZOS)
    return _to_rgba(big)


def bake_accuracy_base(px: int, ss: int = SHAPE_SS):
    """The AccuracyCircle background, ported 1:1 (osu.Game/Screens/Ranking/
    Expanded/Accuracy/{AccuracyCircle,GradedCircles,RankBadge}.cs):

      * a dim gray "Background circle" (full ring) behind the achieved arc;
      * the THIN inner GradedCircles rank ring — the ForRank bands
        (D→C→B→A→S→X) with the GRADE_SPACING notches, sitting INSIDE the
        achieved arc (≈0.8× radius);
      * the six RankBadge pills OUTSIDE the ring, each at its band's
        Interpolation.Lerp visual position (rank_badge_positions) so they
        spread cleanly (D lower-right … S/SS top).

    Baked once (accuracy-independent). The bright cyan→green achieved arc is
    a separate sprite (bake_accuracy_arc) drawn over this. Supersampled at
    `px*ss` then LANCZOS-downscaled so the ring notches and the RankBadge
    letters stay smooth when the panel is viewed zoomed."""
    S_target = max(int(px), 64)
    ss = max(int(ss), 1)
    S = S_target * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2.0
    half_gap = GRADE_SPACING_PERCENTAGE / 2.0
    # dim gray "Background circle" (OsuColour.Gray(47), alpha 0.5), full ring
    R = ACC_ARC_R * S
    W = max(int(round(ACC_ARC_W * S)), 2)
    d.arc([cx - R, cy - R, cx + R, cy + R], 0, 360, fill=(47, 47, 47, 128),
          width=W)
    # thin inner GradedCircles rank ring (ForRank bands + boundary notches)
    Rg = ACC_GRAD_R * S
    Wg = max(int(round(ACC_GRAD_W * S)), 2)
    gbox = [cx - Rg, cy - Rg, cx + Rg, cy + Rg]
    for lo, hi, g in rank_ring_bands():
        col = tuple(int(round(c * 255)) for c in FOR_RANK[g])
        a0 = acc_to_angle_deg(lo + half_gap)
        a1 = acc_to_angle_deg(hi - half_gap)
        if a1 > a0:
            d.arc(gbox, a0, a1, fill=(*col, 255), width=Wg)
    # RankBadge pills at their Lerp visual positions, outside the ring
    for vis, g in rank_badge_positions():
        ang = math.radians(acc_to_angle_deg(vis))
        bx = cx + ACC_BADGE_R * S * math.cos(ang)
        by = cy + ACC_BADGE_R * S * math.sin(ang)
        _badge_pill(img, bx, by, S, g)
    if ss != 1:
        img = img.resize((S_target, S_target), Image.LANCZOS)
    return _to_rgba(img)


def _badge_pill(img, cx, cy, S, grade) -> None:
    """One RankBadge: a small rounded pill in the rank's ForRank colour with
    the rank letter, plus a soft drop shadow (the DrawableRank look)."""
    d = ImageDraw.Draw(img)
    w = ACC_BADGE_W * S
    h = ACC_BADGE_H * S
    col = tuple(int(round(c * 255)) for c in FOR_RANK.get(grade,
                                                          (0.8, 0.8, 0.85)))
    sh = max(h * 0.10, 1.0)
    d.rounded_rectangle([cx - w / 2, cy - h / 2 + sh, cx + w / 2,
                         cy + h / 2 + sh], radius=h / 2, fill=(0, 0, 0, 70))
    d.rounded_rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                        radius=h / 2, fill=(*col, 255))
    label = "SS" if grade in ("SS", "X", "XH") else grade
    font = _load_font(max(int(h * (0.72 if len(label) == 1 else 0.56)), 6))
    try:
        x0, y0, x1, y1 = font.getbbox(label)
    except AttributeError:
        x1, y1 = font.getsize(label); x0 = y0 = 0    # type: ignore
    d.text((cx - (x1 - x0) / 2 - x0, cy - (y1 - y0) / 2 - y0), label,
           font=font, fill=(255, 255, 255, 245))


_ARC_GRAD_CACHE: dict = {}


def _arc_gradient_rgb(S: int):
    """Cached S×S vertical cyan→green gradient RGB (progress-independent) so
    the per-frame arc re-bake only re-masks."""
    g = _ARC_GRAD_CACHE.get(S)
    if g is None:
        import numpy as np
        ys = np.linspace(0.0, 1.0, S).reshape(S, 1)
        top = np.array(ARC_GRAD_TOP)
        bot = np.array(ARC_GRAD_BOT)
        col = top + (bot - top) * ys                 # (S,3) per row
        rgb = np.repeat(col[:, None, :], S, axis=1)  # (S,S,3)
        g = np.clip(np.round(rgb * 255), 0, 255).astype("u1")
        _ARC_GRAD_CACHE[S] = g
    return g


def bake_accuracy_arc(px: int, progress_acc: float, color=None,
                      ss: int = SHAPE_SS):
    """The achieved-accuracy arc (0 → progress_acc) — lazer's "Accuracy
    circle": a FIXED vertical cyan→green gradient (#7CF6FF→#BAFFA9), NOT rank
    colours. A thin light dash marks the sweeping tip. `color` ignored
    (signature compat). Re-baked per progress bucket while sweeping.
    Supersampled at `px*ss` then LANCZOS-downscaled so the big curve reads
    smooth (not stair-stepped) when the panel is viewed zoomed."""
    import numpy as np
    S_target = max(int(px), 64)
    ss = max(int(ss), 1)
    S = S_target * ss
    cx = cy = S / 2.0
    R = ACC_ARC_R * S
    W = max(int(round(ACC_ARC_W * S)), 2)
    mask = Image.new("L", (S, S), 0)
    if progress_acc > 0.0005:
        ImageDraw.Draw(mask).arc(
            [cx - R, cy - R, cx + R, cy + R], acc_to_angle_deg(0.0),
            acc_to_angle_deg(progress_acc), fill=255, width=W)
    rgb = _arc_gradient_rgb(S)
    out = np.dstack([rgb, np.asarray(mask, dtype="u1")]).copy()
    img = Image.fromarray(out, "RGBA")
    if progress_acc > 0.0005:
        d = ImageDraw.Draw(img)
        ang = math.radians(acc_to_angle_deg(progress_acc))
        r0 = R - W / 2.0 - 1
        r1 = R + W / 2.0 + 1
        d.line([(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)),
                (cx + r1 * math.cos(ang), cy + r1 * math.sin(ang))],
               fill=(255, 255, 255, 235), width=max(int(W * 0.16), 2))
    if ss != 1:
        img = img.resize((S_target, S_target), Image.LANCZOS)
    return _to_rgba(img)


def bake_heatmap(px: int, points, ring_frac: float = 0.62):
    """The AccuracyHeatmap: click offsets (radius-units) scattered in a
    circle, additive translucent dots (density reads as brightness), a
    crosshair + edge ring. `points` = [(t, dx, dy)] in circle-radius
    units (1.0 = rim). Baked once."""
    import numpy as np
    S = max(int(px), 64)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2.0
    R = S * 0.46
    r_unit = R * ring_frac
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(10, 12, 18, 150),
              outline=(120, 130, 150, 90), width=2)
    d.ellipse([cx - r_unit, cy - r_unit, cx + r_unit, cy + r_unit],
              outline=(200, 210, 230, 70), width=2)
    d.line([(cx - R * 0.9, cy), (cx + R * 0.9, cy)], fill=(200, 210, 230, 50))
    d.line([(cx, cy - R * 0.9), (cx, cy + R * 0.9)], fill=(200, 210, 230, 50))
    dot = max(S * 0.017, 2.0)
    dots = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    for _t, dx, dy in points or []:
        mag = math.hypot(dx, dy)
        px_ = cx + max(-1.0, min(1.0, dx * ring_frac)) * R
        py_ = cy + max(-1.0, min(1.0, dy * ring_frac)) * R
        col = (BAND_300 if mag <= 0.5 else BAND_100 if mag <= 1.0 else BAND_50)
        c = tuple(int(round(v * 255)) for v in col)
        dd.ellipse([px_ - dot, py_ - dot, px_ + dot, py_ + dot],
                   fill=(*c, 120))
    img = Image.alpha_composite(img, dots)
    return _to_rgba(img)


# --- map leaderboard: compact ranked flank cards ------------------------------------
# The owner mockup: the featured (current-play) panel flanked by compact cards
# of the OTHER renders of this map. Each card reuses the results identity —
# Nunito (the client-font loader), the FOR_RANK grade colours + grade glow,
# the RESULT_COLORS judgment palette — so the flanks read as the same UI as
# the centre panel. Baked ONCE per entry (nothing rolls), one texture/card.

LB_CARD_W = 214.0                  # compact card size (virtual 1080-space px)
LB_CARD_H = 596.0


def bake_avatar_square(px: int, name: str, avatar_bytes: bytes | None = None,
                       radius_frac: float = 0.2):
    """A rounded-SQUARE avatar chip (the mockup shape). From Discord PNG bytes
    (cover-fit + rounded-square mask) when available, else the procedural
    username-hued square with centred initials — the same deterministic
    fallback as bake_avatar, so a missing/unfetchable avatar still reads as
    that player. Never raises (bad bytes → procedural)."""
    px = max(int(px), 16)
    rad = max(int(px * radius_frac), 2)
    img = None
    if avatar_bytes:
        try:
            from io import BytesIO
            src = Image.open(BytesIO(avatar_bytes)).convert("RGBA")
            sw, sh = src.size
            scale = px / max(min(sw, sh), 1)
            nw, nh = max(int(sw * scale + 0.5), px), max(int(sh * scale + 0.5), px)
            src = src.resize((nw, nh), Image.LANCZOS)
            lft, top = (nw - px) // 2, (nh - px) // 2
            img = src.crop((lft, top, lft + px, top + px))
        except Exception:  # noqa: BLE001 — corrupt/animated → procedural
            img = None
    if img is None:
        h = _name_hash(name)
        hue = (h % 360) / 360.0
        sat = 0.42 + ((h >> 9) % 18) / 100.0
        c0 = _hsv(hue, sat, 0.62)
        c1 = _hsv((hue + 0.06) % 1.0, min(sat + 0.08, 1.0), 0.34)
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        dd = ImageDraw.Draw(img)
        for y in range(px):
            f = y / max(px - 1, 1)
            col = tuple(int(round(_lerp(c0[i], c1[i], f) * 255)) for i in range(3))
            dd.line([(0, y), (px, y)], fill=(*col, 255))
        ini = avatar_initials(name)
        font = _load_font(int(px * (0.42 if len(ini) >= 2 else 0.52)))
        try:
            x0, y0, x1, y1 = font.getbbox(ini)
        except AttributeError:
            x1, y1 = font.getsize(ini); x0 = y0 = 0     # type: ignore
        dd.text(((px - (x1 - x0)) / 2 - x0, (px - (y1 - y0)) / 2 - y0), ini,
                font=font, fill=(255, 255, 255, 235))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=rad,
                                           fill=255)
    img.putalpha(mask)
    return _to_rgba(img)


def _pil_text(draw, font, text, x, y, fill, align="l"):
    """Draw one text run at (x, top y) with l/r/m horizontal alignment,
    measured via getbbox (no anchor dependency). Returns text height."""
    try:
        x0, y0, x1, y1 = font.getbbox(text)
    except AttributeError:
        x1, y1 = font.getsize(text); x0 = y0 = 0        # type: ignore
    w = x1 - x0
    tx = x - x0 if align == "l" else (x - w - x0 if align == "r"
                                      else x - w / 2 - x0)
    col = tuple(int(round(c * 255)) for c in fill)
    draw.text((tx, y - y0), text, font=font, fill=(*col, 255))
    return y1 - y0


def bake_lb_card(entry, avatar_bytes, W_px: int, H_px: int, k: float,
                 font_loader, score_loader):
    """Bake one compact leaderboard card to RGBA — rank header, rounded-square
    avatar, name, the Great/OK/Meh/Miss rows, Max Combo, Accuracy, mods
    badges, the big score, and the ForRank grade pill at the bottom. `entry`
    is a leaderboard.LeaderboardEntry."""
    W = max(int(W_px), 60)
    H = max(int(H_px), 120)
    rad = max(int(18 * k), 4)
    base = bake_round_panel(W, H, rad, (0.13, 0.14, 0.19), (0.06, 0.06, 0.09),
                            0.95, border=(0.30, 0.33, 0.40))
    img = Image.fromarray(base, "RGBA")
    d = ImageDraw.Draw(img)
    pad = int(15 * k)

    def font(v):
        return font_loader(max(int(v * k), 8))

    y = pad
    # rank header (gold for #1)
    rcol = (1.0, 0.84, 0.4) if entry.rank == 1 else (0.80, 0.84, 0.94)
    _pil_text(d, font(24), f"#{entry.rank}", W / 2, y, rcol, "m")
    y += int(32 * k)
    # rounded-square avatar
    av = min(W - 2 * pad, int(118 * k))
    ax = (W - av) // 2
    av_rgba = bake_avatar_square(av, entry.player_name, avatar_bytes)
    img.alpha_composite(Image.fromarray(av_rgba, "RGBA"), (ax, int(y)))
    y += av + int(8 * k)
    # player name
    _pil_text(d, font(21), _clip(entry.player_name, 12), W / 2, y,
              (0.96, 0.97, 1.0), "m")
    y += int(32 * k)
    lx, rx = pad, W - pad
    for lbl, val, col in (("Great", entry.counts[0], RESULT_COLORS["GREAT"]),
                          ("OK", entry.counts[1], RESULT_COLORS["OK"]),
                          ("Meh", entry.counts[2], RESULT_COLORS["MEH"]),
                          ("Miss", entry.counts[3], RESULT_COLORS["MISS"])):
        _pil_text(d, font(16), lbl, lx, y, (0.62, 0.66, 0.76), "l")
        _pil_text(d, font(16), str(val), rx, y, col, "r")
        y += int(23 * k)
    y += int(8 * k)
    _pil_text(d, font(15), "Max Combo", lx, y, (0.62, 0.66, 0.76), "l")
    _pil_text(d, font(15), f"{entry.max_combo}x", rx, y, (0.96, 0.86, 0.42), "r")
    y += int(23 * k)
    _pil_text(d, font(15), "Accuracy", lx, y, (0.62, 0.66, 0.76), "l")
    _pil_text(d, font(15), f"{entry.accuracy:.2f}%", rx, y, (0.96, 0.86, 0.42), "r")
    y += int(30 * k)
    # mods badges (small pills) — reuse the .osr mods string
    ms = (entry.mods_str or "").strip()
    if ms and ms.upper() != "NM":
        mods = [m for m in ms.split(",") if m][:5]
        mf = font(13)
        pill_h = int(22 * k)
        gap = int(6 * k)
        widths = []
        for m in mods:
            try:
                bb = mf.getbbox(m); w = bb[2] - bb[0]
            except AttributeError:
                w = mf.getsize(m)[0]                     # type: ignore
            widths.append(w + int(14 * k))
        total = sum(widths) + gap * (len(mods) - 1)
        mx = W / 2 - total / 2
        for m, w in zip(mods, widths):
            d.rounded_rectangle([mx, y, mx + w, y + pill_h], radius=pill_h // 2,
                                fill=(88, 70, 140, 235))
            _pil_text(d, mf, m, mx + w / 2, y + int(3 * k), (0.96, 0.95, 1.0), "m")
            mx += w + gap
    # big score + grade pill anchored to the bottom
    sy = H - int(84 * k)
    _pil_text(d, score_loader(max(int(30 * k), 10)), f"{entry.score:,}", W / 2,
              sy, (1.0, 1.0, 1.0), "m")
    # grade pill (ForRank colour)
    gcol = FOR_RANK.get(entry.grade, (0.8, 0.8, 0.85))
    glabel = "SS" if entry.grade in ("SS", "X", "XH", "SSH") else entry.grade
    gpw = int(58 * k)
    gph = int(30 * k)
    gpx = W / 2 - gpw / 2
    gpy = H - int(42 * k)
    gc = tuple(int(round(c * 255)) for c in gcol)
    d.rounded_rectangle([gpx, gpy, gpx + gpw, gpy + gph], radius=gph // 2,
                        fill=(*gc, 255))
    lum = 0.299 * gcol[0] + 0.587 * gcol[1] + 0.114 * gcol[2]
    gfg = (0.06, 0.06, 0.09) if lum > 0.62 else (1.0, 1.0, 1.0)
    _pil_text(d, font(20), glabel, W / 2, gpy + int(4 * k), gfg, "m")
    return _to_rgba(img)


def bake_moment_pill(text: str, k: float, font_loader):
    """The rank-moment ribbon — a bright rounded pill. NEW #1 = gold, NEW BEST
    = cyan (the arc gradient's cyan). Returns (rgba, w, h)."""
    up = "NEW #1" if "#1" in text else "NEW BEST"
    bg = (1.0, 0.80, 0.28) if "#1" in text else ARC_GRAD_TOP
    font = font_loader(max(int(20 * k), 9))
    try:
        x0, y0, x1, y1 = font.getbbox(up)
    except AttributeError:
        x1, y1 = font.getsize(up); x0 = y0 = 0          # type: ignore
    tw, th = x1 - x0, y1 - y0
    padx, pady = int(16 * k), int(8 * k)
    W = tw + 2 * padx
    H = th + 2 * pady
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bgc = tuple(int(round(c * 255)) for c in bg)
    d.rounded_rectangle([0, 0, W - 1, H - 1], radius=H // 2, fill=(*bgc, 255))
    d.text((padx - x0, pady - y0), up, font=font, fill=(20, 18, 30, 255))
    return _to_rgba(img), float(W), float(H)


# --- data bundle -------------------------------------------------------------------

@dataclass
class ResultsData:
    player: str
    grade: str
    acc_pct: float                 # display percentage (0..100)
    score: int
    max_combo: int
    counts: tuple[int, int, int, int]      # 300/100/50/miss
    title: str
    artist: str
    diff_name: str
    creator: str
    mods: int
    stars: float | None
    pp: float | None
    date_str: str
    ur: float
    slider_ticks: tuple[int, int]          # (hit, total)
    slider_ends: tuple[int, int]           # (hit, total)
    err_deltas: list
    windows: tuple[float, float, float]    # (great, ok, meh) ms
    aim_points: list                       # [(t, dx, dy)]
    perf: object | None                    # pp.PerfBreakdown | None
    pb: dict | None                        # query_pb() row | None
    leaderboard: object | None = None      # leaderboard.BoardData | None
    # played mods for the badge row: the FULL lazer acronym set
    # (meta.lazer_mods, CL/DA/WU/… in the .osr blob's display order) + the
    # custom clock rate (meta.rate_override) for the "DT 1.3×" suffix. Default
    # empty/None → the legacy 32-bit `mods` bitmask drives the badges, and a
    # pure-nomod play yields no badges (no row).
    lazer_mods: tuple = ()                 # meta.lazer_mods (display order)
    rate_override: float | None = None     # meta.rate_override custom rate


class LazerResultsScreen:
    """Bakes the panel once, animates the two-stage reveal per frame."""

    def __init__(self, spr, data: ResultsData, total_ms: float,
                 speed: float = 1.0, argon_font: bool = False):
        self.spr = spr
        self.d = data
        self.w, self.h = float(spr.width), float(spr.height)
        # the results screen is lazer CLIENT UI (skin-independent) → always the
        # bundled Nunito (Torus stand-in), NOT DejaVu, regardless of the
        # gameplay skin. Body/labels Medium; the big rolling score Light.
        # (`argon_font` kept for signature compat; no longer selects the face.)
        self._font_loader = _load_argon_font                 # Nunito Medium
        self._score_loader = _nunito_loader(RESULTS_SCORE_WEIGHT)  # Nunito Light
        self.k = self.h / UH
        self.uw = self.w / self.k              # virtual width
        # the scene feeds age in MAP ms; the timeline is WALL ms → divide by
        # the rate mod so the reveal runs at real time under DT/HT
        self.speed = float(speed) if speed and speed > 0 else 1.0
        self.total_ms = max(float(total_ms), MIN_TOTAL_MS)
        self._n = 0
        self._arc_key = None
        self._arc_bucket = -1.0
        self._score_val = -1
        self.acc_frac = _clamp01(data.acc_pct / 100.0)
        # centre grade letter matches the ForRank ring (AccuracyCircle uses
        # the rank colour for the badge too)
        self.grade_rgb = FOR_RANK.get(data.grade, (0.8, 0.8, 0.85))
        self._bake_static()

    # -- baking ------------------------------------------------------------------

    def _put(self, rgba, prefix="lr") -> str:
        key = f"{prefix}_{self._n}"
        self._n += 1
        self.spr.upload_texture(key, rgba)
        return key

    def _text(self, text: str, px_virtual: float, color):
        rgba, w, h = bake_text(text, int(px_virtual * self.k), color,
                               self._font_loader)
        return (self._put(rgba), float(w), float(h))

    def _fit_text(self, text: str, px_virtual: float, color,
                  max_w_virtual: float, min_px_virtual: float | None = None):
        """Bake a text row SIZED TO FIT `max_w_virtual`: scale the font down
        from `px_virtual` toward `min_px_virtual` until the rendered width
        fits, and only if it STILL overflows at the min size, ellipsis-
        truncate to fit. Replaces the old hard char-clip that dropped long
        title/artist/name content off the edge of the panel. Width is
        measured with the exact metrics bake_text() uses, so the returned
        row is guaranteed to fit."""
        text = text or ""
        if min_px_virtual is None:
            min_px_virtual = max(px_virtual * 0.62, 12.0)
        max_w = max_w_virtual * self.k
        px = float(px_virtual)
        step = max((px_virtual - min_px_virtual) / 12.0, 1.0)
        chosen = min_px_virtual
        while px >= min_px_virtual:
            fpx = max(int(round(px * self.k)), 6)
            if _bake_width(self._font_loader(fpx), text, fpx) <= max_w:
                chosen = px
                break
            px -= step
        fpx = max(int(round(chosen * self.k)), 6)
        font = self._font_loader(fpx)
        if _bake_width(font, text, fpx) > max_w:
            text = _ellipsize(font, text, max_w, fpx)
        rgba, w, h = bake_text(text, fpx, color, self._font_loader)
        return (self._put(rgba), float(w), float(h))

    def _bake_star_pill(self):
        """The StarRatingDisplay pill: a rounded ForStarDifficulty-coloured
        pill with the procedural star icon + the star number. Text/star flip
        to dark on light backgrounds. Returns (key, w, h)."""
        stars = self.d.stars
        txt = f"{stars:.2f}" if stars is not None else "--"
        bg = for_star_difficulty(stars if stars is not None else 0.0)
        lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        fg = (0.06, 0.06, 0.09) if lum > 0.6 else (1.0, 1.0, 1.0)
        fpx = max(int(22 * self.k), 8)
        # native footprint (layout metrics + downscale target)
        font = self._font_loader(fpx)
        try:
            x0, y0, x1, y1 = font.getbbox(txt)
        except AttributeError:
            x1, y1 = font.getsize(txt); x0 = y0 = 0     # type: ignore
        tw, th = x1 - x0, y1 - y0
        star_sz = int(fpx * 0.92)
        padx = int(fpx * 0.5)
        pady = int(fpx * 0.32)
        gap = int(fpx * 0.26)
        H = max(th, star_sz) + 2 * pady
        W = padx + star_sz + gap + tw + padx
        # supersampled build (pill/star/number at TEXT_SS×), LANCZOS-downscaled
        # to (W, H) so the pill edge, star and star-number stay smooth zoomed.
        ss = TEXT_SS
        fpxb = fpx * ss
        fb = self._font_loader(fpxb)
        try:
            bx0, by0, bx1, by1 = fb.getbbox(txt)
        except AttributeError:
            bx1, by1 = fb.getsize(txt); bx0 = by0 = 0   # type: ignore
        twb, thb = bx1 - bx0, by1 - by0
        star_szb = star_sz * ss
        padxb, padyb, gapb = padx * ss, pady * ss, gap * ss
        Hb = max(thb, star_szb) + 2 * padyb
        Wb = padxb + star_szb + gapb + twb + padxb
        img = Image.new("RGBA", (Wb, Hb), (0, 0, 0, 0))
        dd = ImageDraw.Draw(img)
        bgc = tuple(int(round(c * 255)) for c in bg)
        dd.rounded_rectangle([0, 0, Wb - 1, Hb - 1], radius=Hb // 2,
                             fill=(*bgc, 255))
        star_rgba = bake_star(star_szb, fg)
        img.alpha_composite(Image.fromarray(star_rgba, "RGBA"),
                            (padxb, (Hb - star_szb) // 2))
        fgc = tuple(int(round(c * 255)) for c in fg)
        dd.text((padxb + star_szb + gapb - bx0, (Hb - thb) // 2 - by0), txt,
                font=fb, fill=(*fgc, 255))
        img = img.resize((W, H), Image.LANCZOS)
        return (self._put(_to_rgba(img)), float(W), float(H))

    def _bake_mod_pill(self, text: str, color):
        """A single played-mod badge for the results panel: a rounded,
        category-coloured pill with the acronym (+ any custom-rate suffix like
        "DT 1.3×") in white. Fixed height (MOD_PILL_VH), width fits the text —
        so a badge row lines up, mirroring the gameplay HUD mod pills but
        sized for the ranking screen. Supersampled at TEXT_SS× and
        LANCZOS-downscaled so the edge/glyphs stay crisp through the ≥1080p
        results bake. Returns (key, w, h)."""
        k = self.k
        H = max(int(round(MOD_PILL_VH * k)), 12)
        fpx = max(int(round(MOD_PILL_TEXT_VPX * k)), 8)
        padx = int(round(fpx * 0.62))
        ss = TEXT_SS
        Hb = H * ss
        fb = self._font_loader(fpx * ss)
        try:
            bx0, by0, bx1, by1 = fb.getbbox(text)
        except AttributeError:
            bx1, by1 = fb.getsize(text); bx0 = by0 = 0    # type: ignore
        twb, thb = bx1 - bx0, by1 - by0
        padxb = padx * ss
        Wb = twb + 2 * padxb
        W = max(int(round(Wb / ss)), 8)
        img = Image.new("RGBA", (Wb, Hb), (0, 0, 0, 0))
        dd = ImageDraw.Draw(img)
        bgc = tuple(int(round(c * 255)) for c in color)
        dd.rounded_rectangle([0, 0, Wb - 1, Hb - 1], radius=Hb // 2,
                             fill=(*bgc, MOD_PILL_ALPHA))
        # vertically centre the glyphs within the fixed-height pill
        ty = (Hb - thb) // 2 - by0
        dd.text((padxb - bx0, ty), text, font=fb, fill=(255, 255, 255, 255))
        img = img.resize((W, H), Image.LANCZOS)
        return (self._put(_to_rgba(img)), float(W), float(H))

    def _bake_static(self) -> None:
        k = self.k
        d = self.d
        # panel geometry (virtual px)
        self.PANEL_W = 560.0
        self.PANEL_H = 940.0
        self.center_cx = self.uw / 2.0
        self.left_cx = self.PANEL_W / 2.0 + 70.0
        self.panel_cy = UH / 2.0
        # main panel bg
        self.panel_key = self._put(bake_round_panel(
            int(self.PANEL_W * k), int(self.PANEL_H * k), int(26 * k),
            (0.12, 0.13, 0.17), (0.05, 0.05, 0.07), 0.93,
            border=(0.3, 0.33, 0.4)))
        # avatar + header — auto-scale to fit the panel width (never a hard
        # char-clip that would drop title/artist/name content off the edge).
        # Title/artist span the panel content width; the name shares its row
        # with the 52px avatar + a 12px gap, so it gets a tighter budget.
        self.avatar_key = self._put(bake_avatar(int(52 * k), d.player or "?"))
        content_w = self.PANEL_W - 48.0                 # panel inner width
        self.name_row = self._fit_text(d.player or "Player", 30, (1, 1, 1),
                                       content_w - 64.0)   # avatar+gap budget
        self.title_row = self._fit_text(d.title or "", 34, (0.95, 0.96, 1.0),
                                        content_w)
        self.artist_row = self._fit_text(d.artist or "", 24,
                                         (0.72, 0.75, 0.85), content_w)
        # accuracy circle
        self.ACC_DISP = 380.0                 # canvas display size (virtual)
        cbake = int(self.ACC_DISP * k)
        self.acc_base_key = self._put(bake_accuracy_base(cbake))
        # centre rank letter: WHITE fill + a soft rank-coloured glow (lazer
        # DrawableRank's coloured EdgeEffect) — glow = ForRank[grade].
        _gl_rgba, _gl_w, _gl_h = bake_grade_letter(
            d.grade, int(150 * k), (0.99, 0.99, 1.0),
            FOR_RANK.get(d.grade, (0.8, 0.8, 0.85)), self._font_loader)
        self.grade_letter = (self._put(_gl_rgba), float(_gl_w), float(_gl_h))
        self.target_arc = target_arc_value(self.acc_frac, d.grade)
        # score baked lazily (rolls)
        self.score_row = self._score_text(0)
        # star-rating pill (procedural star icon — the font has no ★ glyph),
        # then diff name + creator
        self.star_pill = self._bake_star_pill()
        # played-mod badge row (lazer starAndModDisplay ModDisplay): one
        # category-coloured pill per active mod, from the SAME build_mod_pills
        # the gameplay HUD uses (full lazer set incl. CL/DA/WU + custom-rate
        # "DT 1.3×"), so the badges match the HUD. Nomod → empty → no row.
        _mp = build_mod_pills(d.mods, d.lazer_mods, d.rate_override)
        self.mod_pill_texts = tuple(p.text for p in _mp)   # labels (introspect)
        self.mod_pills = [
            self._bake_mod_pill(p.text, mod_pill_color(p.acr)) for p in _mp]
        self.diff_row = self._text(_clip(d.diff_name, 28), 26, (0.9, 0.92, 1.0))
        self.creator_row = self._text(f"mapped by {_clip(d.creator, 22)}", 22,
                                      (0.65, 0.68, 0.78))
        # stats grid
        pp_txt = (f"{d.pp:.0f}" if d.pp is not None else "--")
        self._stat_a = [
            ("ACCURACY", f"{d.acc_pct:.2f}%", (0.95, 0.96, 1.0)),
            ("MAX COMBO", f"{d.max_combo}x", (0.95, 0.96, 1.0)),
            ("PP", pp_txt, (0.6, 0.86, 1.0)),
        ]
        c300, c100, c50, cmiss = d.counts
        self._stat_b = [
            ("GREAT", str(c300), RESULT_COLORS["GREAT"]),
            ("OK", str(c100), RESULT_COLORS["OK"]),
            ("MEH", str(c50), RESULT_COLORS["MEH"]),
            ("MISS", str(cmiss), RESULT_COLORS["MISS"]),
        ]
        th, tt = d.slider_ticks
        eh, et = d.slider_ends
        self._stat_c = [
            ("SLIDER TICK", f"{th}/{tt}", (0.85, 0.88, 0.95)),
            ("SLIDER END", f"{eh}/{et}", (0.85, 0.88, 0.95)),
        ]
        self._grid_a = [self._grid_cell(lbl, val, col)
                        for lbl, val, col in self._stat_a]
        self._grid_b = [self._grid_cell(lbl, val, col)
                        for lbl, val, col in self._stat_b]
        self._grid_c = [self._grid_cell(lbl, val, col)
                        for lbl, val, col in self._stat_c]
        self.date_row = self._text(f"Played on {d.date_str}", 20,
                                   (0.6, 0.63, 0.72))

        # --- stats panels (stage 2) -----------------------------------------
        self.STATS_X0 = self.left_cx + self.PANEL_W / 2.0 + 40.0
        self.STATS_W = self.uw - self.STATS_X0 - 64.0
        self.STATS_H = (self.PANEL_H - 2 * 24.0) / 3.0
        self.stats_panel_key = self._put(bake_round_panel(
            int(self.STATS_W * k), int(self.STATS_H * k), int(20 * k),
            (0.11, 0.12, 0.16), (0.05, 0.05, 0.07), 0.93,
            border=(0.28, 0.31, 0.38)))
        self.perf_title = self._text("PERFORMANCE", 22, (0.8, 0.83, 0.92))
        self.timing_title = self._text("TIMING DISTRIBUTION", 22,
                                       (0.8, 0.83, 0.92))
        self.heat_title = self._text("ACCURACY", 22, (0.8, 0.83, 0.92))
        # perf bar labels + percents
        self._perf_rows = []
        if d.perf is not None:
            for lbl, pct in (("Aim", d.perf.aim_pct),
                             ("Speed", d.perf.speed_pct),
                             ("Accuracy", d.perf.acc_pct)):
                self._perf_rows.append((
                    self._text(lbl, 20, (0.85, 0.88, 0.95)),
                    self._text(f"{pct * 100:.0f}%", 20, (1, 1, 1)), pct))
            self.perf_pp_row = self._text(
                f"Achieved {d.perf.achieved_pp:.0f}pp  /  "
                f"Maximum {d.perf.max_pp:.0f}pp", 18, (0.62, 0.66, 0.76))
        else:
            self.perf_pp_row = self._text("performance unavailable (no rosu)",
                                          18, (0.62, 0.66, 0.76))
        # timing distribution
        self.HIST_BINS = 39
        self.hist_rng = max(d.windows[2], 1.0)
        self.bins = histogram_bins(list(d.err_deltas), self.HIST_BINS,
                                   self.hist_rng)
        self.hist_peak = max(self.bins) if any(self.bins) else 0
        self.timing_stat = self._text(
            f"UR {d.ur:.0f}   ±{self.hist_rng:.0f}ms", 18,
            (0.62, 0.66, 0.76))
        # heatmap
        hbake = int(self.STATS_H * 0.74 * k)
        self.heat_key = self._put(bake_heatmap(hbake, d.aim_points))
        self.heat_over = self._text("Overshoot", 16, (0.6, 0.63, 0.72))
        n_hit = len(d.aim_points)
        avg_mag = (sum(math.hypot(dx, dy) for _t, dx, dy in d.aim_points)
                   / n_hit) if n_hit else 0.0
        self.heat_stat = self._text(f"mean {avg_mag:.2f}R", 18,
                                    (0.62, 0.66, 0.76))

        # --- PB card (stage 1, left of the panel) ---------------------------
        self.pb_parts = None
        if d.pb is not None:
            self.PB_W = 300.0
            self.PB_H = 178.0
            self.pb_bg = self._put(bake_round_panel(
                int(self.PB_W * k), int(self.PB_H * k), int(18 * k),
                (0.14, 0.15, 0.20), (0.06, 0.06, 0.09), 0.95,
                border=(0.35, 0.38, 0.46)))
            pb = d.pb
            pb_grade = pb.get("grade", "?")
            self.pb_parts = {
                "rank": self._text("#1", 30, (1.0, 0.82, 0.35)),
                "label": self._text("PERSONAL BEST", 16, (0.62, 0.66, 0.76)),
                "avatar": self._put(bake_avatar(
                    int(40 * k), pb.get("player_name", "?"))),
                "name": self._text(_clip(pb.get("player_name", ""), 16), 22,
                                   (0.95, 0.96, 1.0)),
                "grade": self._text(pb_grade, 40,
                                    FOR_RANK.get(pb_grade, (0.8, 0.8, 0.85))),
                "score": self._text(f"{int(pb.get('score', 0)):,}", 30,
                                    (1, 1, 1)),
                "acc": self._text(f"{float(pb.get('accuracy', 0.0)):.2f}%  "
                                  f"{int(pb.get('max_combo', 0))}x", 18,
                                  (0.72, 0.75, 0.85)),
            }

        # --- map leaderboard (stage 1, flanking the featured panel) ---------
        self._bake_leaderboard()

    def _bake_leaderboard(self) -> None:
        """Bake the flanking ranked cards + the rank-moment banner (owner
        mockup 2026-07-09). A no-op when there's no board (leaderboard off or
        no OTHER renders of this map) — the single PB card path stays intact.
        Adapts to the card count: 0 flanks → nothing extra; N → N cards split
        onto the two sides. Avatars resolve here (Discord → cache → procedural
        fallback), never blocking or crashing the bake."""
        self._lb_board = self.d.leaderboard
        self._lb_left: list = []
        self._lb_right: list = []
        self._lb_rank_row = None
        self._lb_moment = None
        board = self._lb_board
        if board is None:
            return
        k = self.k
        self.LB_CARD_W = LB_CARD_W
        self.LB_CARD_H = LB_CARD_H
        cw, ch = int(LB_CARD_W * k), int(LB_CARD_H * k)
        try:
            from .leaderboard import resolve_avatar_bytes
        except Exception:  # noqa: BLE001 — module gone → all procedural
            def resolve_avatar_bytes(*_a, **_kw):
                return None

        def _one(entry):
            try:
                avb = resolve_avatar_bytes(entry.discord_user_id)
            except Exception:  # noqa: BLE001 — avatars never break a bake
                avb = None
            return (self._put(bake_lb_card(entry, avb, cw, ch, k,
                                           self._font_loader,
                                           self._score_loader)), entry)

        self._lb_left = [_one(e) for e in board.left]
        self._lb_right = [_one(e) for e in board.right]
        if board.left or board.right:
            self._lb_rank_row = self._text(
                f"#{board.rank} on {_clip(self.d.title, 22)}", 22,
                (0.9, 0.92, 1.0))
        if board.moment:
            rgba, w, h = bake_moment_pill(board.moment, k, self._font_loader)
            self._lb_moment = (self._put(rgba), w, h)

    def _grid_cell(self, label, value, color):
        return (self._text(label, 16, (0.6, 0.63, 0.73)),
                self._text(value, 32, color))

    def _score_text(self, value: int):
        rgba, w, h = bake_text(f"{value:,}", int(64 * self.k), (1, 1, 1),
                               self._score_loader)
        if self._score_val < 0:
            self._score_key = self._put(rgba)
        else:
            self.spr.upload_texture(self._score_key, rgba)
        self._score_val = value
        return (self._score_key, float(w), float(h))

    # -- per-frame draw ----------------------------------------------------------

    def draw(self, age_ms: float) -> None:
        age_ms = age_ms / self.speed          # MAP ms → WALL ms
        if age_ms <= 0.0:
            return
        out: list[Sprite] = []
        k = self.k
        fade = _clamp01(age_ms / FADE_MS)
        # scene dim
        out.append(Sprite(self.w / 2.0, self.h / 2.0, self.w, self.h, None,
                          (0, 0, 0, DIM_ALPHA * fade)))

        # open progress (panel slide, stage 2)
        open_p = ease_out_quint((age_ms - STAGE1_MS) / OPEN_MS) \
            if age_ms > STAGE1_MS else 0.0
        panel_cx = _lerp(self.center_cx, self.left_cx, open_p)

        # stage-1 flanks: the map leaderboard (owner mockup) when there are
        # OTHER renders, else the single PB card (unchanged). Both fade out as
        # the panel opens into stage 2.
        stage1_a = fade * (1.0 - _clamp01((age_ms - STAGE1_MS)
                                          / (OPEN_MS * 0.6)))
        if stage1_a > 0.003:
            if self._lb_left or self._lb_right:
                self._draw_leaderboard(out, panel_cx, stage1_a)
            elif self.pb_parts is not None:
                self._draw_pb(out, panel_cx, stage1_a)
            self._draw_lb_banner(out, panel_cx, stage1_a)

        # stats panels (stage 2)
        if age_ms > STAGE1_MS:
            self._draw_stats_panels(out, age_ms)

        # main score panel
        self._draw_panel(out, age_ms, panel_cx, fade)
        self.spr.draw(out)

    def _blit(self, out, row, cx, top_y, a, scale=1.0):
        """Draw a baked (key,w,h) row centred at cx, its TOP at top_y*.
        Returns the row height in screen px."""
        key, w, h = row
        w *= scale
        h *= scale
        out.append(Sprite(cx, top_y + h / 2.0, w, h, key, (1, 1, 1, a)))
        return h

    def _draw_panel(self, out, age_ms, panel_cx, fade) -> None:
        k = self.k
        cx = panel_cx * k
        # panel bg
        out.append(Sprite(cx, self.panel_cy * k, self.PANEL_W * k,
                          self.PANEL_H * k, self.panel_key, (1, 1, 1, fade)))
        a = fade
        # vertically centre the content block
        top = (self.panel_cy - self.PANEL_H / 2.0 + 40.0) * k
        y = top
        # header: avatar + name (centred group)
        av = 52.0 * k
        nk, nw, nh = self.name_row
        group_w = av + 12 * k + nw
        gx = cx - group_w / 2.0
        out.append(Sprite(gx + av / 2.0, y + av / 2.0, av, av, self.avatar_key,
                          (1, 1, 1, a)))
        out.append(Sprite(gx + av + 12 * k + nw / 2.0, y + av / 2.0, nw, nh,
                          nk, (1, 1, 1, a)))
        y += av + 16 * k
        y += self._blit(out, self.title_row, cx, y, a) + 4 * k
        y += self._blit(out, self.artist_row, cx, y, a) + 20 * k
        # accuracy circle
        acc_d = self.ACC_DISP * k
        circ_cy = y + acc_d / 2.0
        out.append(Sprite(cx, circ_cy, acc_d, acc_d, self.acc_base_key,
                          (1, 1, 1, a)))
        self._draw_acc_arc(out, age_ms, cx, circ_cy, acc_d, a)
        self._draw_grade(out, age_ms, cx, circ_cy, a)
        y += acc_d + 6 * k
        # score (rolls with the sweep)
        self._roll_score(age_ms)
        y += self._blit(out, self.score_row, cx, y, a) + 12 * k
        # played-mod badge row (lazer starAndModDisplay, just under the score).
        # Nomod → _draw_mod_row returns 0 and adds nothing → layout identical
        # to the no-mods panel (the nomod screen is unchanged from before).
        mod_h = self._draw_mod_row(out, cx, y, a)
        if mod_h > 0.0:
            y += mod_h + 14 * k
        # star / diff / creator centred row
        y += self._draw_star_row(out, cx, y, a) + 22 * k
        # stats grid
        y += self._draw_grid_row(out, self._grid_a, cx, y, a,
                                 self.PANEL_W * 0.86) + 16 * k
        y += self._draw_grid_row(out, self._grid_b, cx, y, a,
                                 self.PANEL_W * 0.86) + 14 * k
        y += self._draw_grid_row(out, self._grid_c, cx, y, a,
                                 self.PANEL_W * 0.62) + 16 * k
        self._blit(out, self.date_row, cx, y, a)

    def _draw_mod_row(self, out, cx, top_y, a) -> float:
        """The played-mod badge row, centred at `cx` with its TOP at `top_y`.
        Ported placement: lazer's ExpandedPanelMiddleContent puts the mods in
        a `starAndModDisplay` FillFlowContainer (StarRatingDisplay + ModDisplay)
        directly under the TotalScoreCounter; we give the badges their own
        centred row in that same spot (our star row already carries the diff
        name + creator that lazer's row does not). Category-coloured via the
        HUD's mod_pill_color. Empty (nomod / no lazer mods) → 0 height, so the
        panel is byte-identical to the pre-badge layout. Returns row height."""
        if not self.mod_pills:
            return 0.0
        k = self.k
        gap = MOD_PILL_GAP_V * k
        total = sum(w for _key, w, _h in self.mod_pills) \
            + gap * (len(self.mod_pills) - 1)
        h = max(hh for _key, _w, hh in self.mod_pills)
        x = cx - total / 2.0
        for key, w, hh in self.mod_pills:
            out.append(Sprite(x + w / 2.0, top_y + h / 2.0, w, hh, key,
                              (1, 1, 1, a)))
            x += w + gap
        return h

    def _draw_star_row(self, out, cx, top_y, a) -> float:
        k = self.k
        sk, sw, sh = self.star_pill
        dk, dw, dh = self.diff_row
        ck, cw, ch = self.creator_row
        gap = 14 * k
        total = sw + gap + dw + gap + cw
        h = max(sh, dh, ch)
        x = cx - total / 2.0
        for key, w, hh in (self.star_pill, self.diff_row, self.creator_row):
            out.append(Sprite(x + w / 2.0, top_y + h / 2.0, w, hh, key,
                              (1, 1, 1, a)))
            x += w + gap
        return h

    def _draw_grid_row(self, out, cells, cx, top_y, a, span_virtual) -> float:
        k = self.k
        span = span_virtual * k
        n = len(cells)
        col_w = span / n
        x0 = cx - span / 2.0
        row_h = 0.0
        for i, (label, value) in enumerate(cells):
            ccx = x0 + col_w * (i + 0.5)
            lk, lw, lh = label
            out.append(Sprite(ccx, top_y + lh / 2.0, lw, lh, lk,
                              (1, 1, 1, a)))
            vk, vw, vh = value
            out.append(Sprite(ccx, top_y + lh + 6 * k + vh / 2.0, vw, vh, vk,
                              (1, 1, 1, a)))
            row_h = max(row_h, lh + 6 * k + vh)
        return row_h

    def _draw_acc_arc(self, out, age_ms, cx, cy, acc_d, a) -> None:
        sweep = ease_out_cubic((age_ms - SWEEP_DELAY_MS) / SWEEP_MS) \
            if age_ms > SWEEP_DELAY_MS else 0.0
        prog = self.target_arc * sweep
        bucket = round(prog, 3)
        if bucket != self._arc_bucket:
            rgba = bake_accuracy_arc(int(self.ACC_DISP * self.k), prog,
                                     self.grade_rgb)
            if self._arc_key is None:
                self._arc_key = self._put(rgba)
            else:
                self.spr.upload_texture(self._arc_key, rgba)
            self._arc_bucket = bucket
        out.append(Sprite(cx, cy, acc_d, acc_d, self._arc_key, (1, 1, 1, a)))

    def _draw_grade(self, out, age_ms, cx, cy, a) -> None:
        badge_start = SWEEP_DELAY_MS + SWEEP_MS - 130.0
        if age_ms < badge_start:
            return
        p = ease_out_cubic((age_ms - badge_start) / BADGE_MS)
        scale = _lerp(1.42, 1.0, p)
        ga = a * _clamp01(p * 1.4)
        gk, gw, gh = self.grade_letter
        out.append(Sprite(cx, cy, gw * scale, gh * scale, gk, (1, 1, 1, ga)))

    def _roll_score(self, age_ms) -> None:
        sweep = ease_out_cubic((age_ms - SWEEP_DELAY_MS) / SWEEP_MS) \
            if age_ms > SWEEP_DELAY_MS else 0.0
        val = int(round(self.d.score * sweep))
        if val != self._score_val:
            self.score_row = self._score_text(val)

    # -- stats panels ------------------------------------------------------------

    def _panel_unfold(self, age_ms, idx):
        start = STAGE1_MS + 120.0 + idx * STAGGER_MS
        return ease_out_quint((age_ms - start) / OPEN_MS) \
            if age_ms > start else 0.0

    def _draw_stats_panels(self, out, age_ms) -> None:
        k = self.k
        top0 = self.panel_cy - self.PANEL_H / 2.0
        for idx, drawer in enumerate((self._draw_perf, self._draw_timing,
                                      self._draw_heat)):
            s = self._panel_unfold(age_ms, idx)
            if s <= 0.003:
                continue
            pcy = (top0 + self.STATS_H * (idx + 0.5) + idx * 24.0) * k
            left = self.STATS_X0 * k
            drawn_w = self.STATS_W * k * s
            pcx = left + drawn_w / 2.0
            out.append(Sprite(pcx, pcy, drawn_w, self.STATS_H * k,
                              self.stats_panel_key, (1, 1, 1, min(s * 1.3, 1))))
            content_a = _clamp01((s - 0.55) / 0.45)
            if content_a > 0.01:
                full_cx = (self.STATS_X0 + self.STATS_W / 2.0) * k
                drawer(out, full_cx, (top0 + idx * (self.STATS_H + 24.0)) * k,
                       content_a)

    def _draw_perf(self, out, cx, top_y, a) -> None:
        k = self.k
        pad = 26 * k
        left = cx - self.STATS_W * k / 2.0 + pad
        right = cx + self.STATS_W * k / 2.0 - pad
        tk, tw, th = self.perf_title
        out.append(Sprite(left + tw / 2.0, top_y + 22 * k + th / 2.0, tw, th,
                          tk, (1, 1, 1, a)))
        y = top_y + 22 * k + th + 20 * k
        bar_x = left + 130 * k
        bar_w = right - bar_x - 60 * k
        bar_h = 16 * k
        row_gap = (self.STATS_H * k - 22 * k - th - 20 * k - 40 * k) / \
            max(len(self._perf_rows), 1)
        colors = (BAND_300, BAND_100, BAND_50)
        for i, (label, pctrow, pct) in enumerate(self._perf_rows):
            ry = y + i * row_gap
            lk, lw, lh = label
            out.append(Sprite(left + lw / 2.0, ry + bar_h / 2.0, lw, lh, lk,
                              (1, 1, 1, a)))
            # track
            out.append(Sprite(bar_x + bar_w / 2.0, ry + bar_h / 2.0, bar_w,
                              bar_h, None, (0.2, 0.22, 0.28, 0.7 * a)))
            fw = max(bar_w * _clamp01(pct), 2.0)
            col = colors[i % 3]
            out.append(Sprite(bar_x + fw / 2.0, ry + bar_h / 2.0, fw, bar_h,
                              None, (*col, 0.95 * a)))
            pk, pw, ph = pctrow
            out.append(Sprite(right - pw / 2.0, ry + bar_h / 2.0, pw, ph, pk,
                              (1, 1, 1, a)))
        ppk, ppw, pph = self.perf_pp_row
        out.append(Sprite(cx, top_y + self.STATS_H * k - 20 * k - pph / 2.0,
                          ppw, pph, ppk, (1, 1, 1, a)))

    def _draw_timing(self, out, cx, top_y, a) -> None:
        k = self.k
        pad = 26 * k
        tk, tw, th = self.timing_title
        out.append(Sprite(cx - self.STATS_W * k / 2.0 + pad + tw / 2.0,
                          top_y + 22 * k + th / 2.0, tw, th, tk, (1, 1, 1, a)))
        hist_w = self.STATS_W * k - 2 * pad
        hist_h = self.STATS_H * k - (22 * k + th) - 54 * k
        base_y = top_y + 22 * k + th + 16 * k + hist_h
        x0 = cx - hist_w / 2.0
        # centre line
        out.append(Sprite(cx, base_y - hist_h / 2.0, 2.0, hist_h, None,
                          (1, 1, 1, 0.5 * a)))
        if self.hist_peak > 0:
            cell_w = hist_w / self.HIST_BINS
            bar_w = max(cell_w - 2.0, 2.0)
            great, ok, _meh = self.d.windows
            for i, count in enumerate(self.bins):
                if count == 0:
                    continue
                centre = -self.hist_rng + (i + 0.5) * (2 * self.hist_rng
                                                       / self.HIST_BINS)
                am = abs(centre)
                col = (BAND_300 if am <= great else
                       BAND_100 if am <= ok else BAND_50)
                bh = hist_h * count / self.hist_peak
                bx = x0 + i * cell_w + cell_w / 2.0
                out.append(Sprite(bx, base_y - bh / 2.0, bar_w, bh, None,
                                  (*col, 0.95 * a)))
        sk, sw, sh = self.timing_stat
        out.append(Sprite(cx, base_y + 22 * k, sw, sh, sk, (1, 1, 1, a)))

    def _draw_heat(self, out, cx, top_y, a) -> None:
        k = self.k
        pad = 26 * k
        tk, tw, th = self.heat_title
        out.append(Sprite(cx - self.STATS_W * k / 2.0 + pad + tw / 2.0,
                          top_y + 22 * k + th / 2.0, tw, th, tk, (1, 1, 1, a)))
        hd = self.STATS_H * k * 0.74
        hcx = cx - self.STATS_W * k * 0.18
        hcy = top_y + self.STATS_H * k / 2.0 + 8 * k
        out.append(Sprite(hcx, hcy, hd, hd, self.heat_key, (1, 1, 1, a)))
        ok, ow, oh = self.heat_over
        out.append(Sprite(hcx, hcy - hd / 2.0 - 4 * k, ow, oh, ok,
                          (1, 1, 1, 0.85 * a)))
        stk, stw, sth = self.heat_stat
        out.append(Sprite(cx + self.STATS_W * k * 0.26, hcy, stw, sth, stk,
                          (1, 1, 1, a)))

    def _draw_leaderboard(self, out, panel_cx, a) -> None:
        """The flanking ranked cards, centred vertically on the featured panel
        and marching outward from its two edges — higher ranks to the LEFT,
        lower to the RIGHT (owner mockup). The card nearest each edge is the
        rank closest in score to the current play, so the board reads
        contiguously across the centre panel."""
        k = self.k
        cy = self.panel_cy * k
        cw = self.LB_CARD_W * k
        ch = self.LB_CARD_H * k
        gap = 22.0 * k
        pl = (panel_cx - self.PANEL_W / 2.0) * k
        pr = (panel_cx + self.PANEL_W / 2.0) * k
        # left group drawn reversed so the innermost card is rank R-1
        for i, (key, _e) in enumerate(reversed(self._lb_left)):
            ccx = pl - gap - cw / 2.0 - i * (cw + gap)
            out.append(Sprite(ccx, cy, cw, ch, key, (1, 1, 1, a)))
        for i, (key, _e) in enumerate(self._lb_right):
            ccx = pr + gap + cw / 2.0 + i * (cw + gap)
            out.append(Sprite(ccx, cy, cw, ch, key, (1, 1, 1, a)))

    def _draw_lb_banner(self, out, panel_cx, a) -> None:
        """The rank-moment ribbon above the featured panel: '#X on <map>' with
        the NEW #1 / NEW BEST pill beside it when earned."""
        if self._lb_rank_row is None and self._lb_moment is None:
            return
        k = self.k
        cx = panel_cx * k
        y = (self.panel_cy - self.PANEL_H / 2.0 - 20.0) * k    # centreline
        rw = rh = 0.0
        rk = None
        if self._lb_rank_row is not None:
            rk, rw, rh = self._lb_rank_row
        mw = self._lb_moment[1] if self._lb_moment else 0.0
        gap = 12.0 * k if (self._lb_rank_row and self._lb_moment) else 0.0
        total = rw + gap + mw
        x = cx - total / 2.0
        if rk is not None:
            out.append(Sprite(x + rw / 2.0, y, rw, rh, rk, (1, 1, 1, a)))
            x += rw + gap
        if self._lb_moment:
            mk, mwv, mhv = self._lb_moment
            out.append(Sprite(x + mwv / 2.0, y, mwv, mhv, mk, (1, 1, 1, a)))

    def _draw_pb(self, out, panel_cx, a) -> None:
        k = self.k
        # PB card sits to the LEFT of the panel's stage-1 (centre) position
        pcx = (self.center_cx - self.PANEL_W / 2.0 - 40.0 - self.PB_W / 2.0) * k
        pcy = (self.panel_cy - self.PANEL_H / 2.0 + self.PB_H / 2.0 + 30.0) * k
        p = self.pb_parts
        out.append(Sprite(pcx, pcy, self.PB_W * k, self.PB_H * k, self.pb_bg,
                          (1, 1, 1, a)))
        left = pcx - self.PB_W * k / 2.0 + 18 * k
        top = pcy - self.PB_H * k / 2.0 + 16 * k
        rk, rw, rh = p["rank"]
        out.append(Sprite(left + rw / 2.0, top + rh / 2.0, rw, rh, rk,
                          (1, 1, 1, a)))
        lk, lw, lh = p["label"]
        out.append(Sprite(left + rw + 10 * k + lw / 2.0, top + rh / 2.0, lw,
                          lh, lk, (1, 1, 1, 0.85 * a)))
        # avatar + name
        ay = top + rh + 12 * k
        av = 40.0 * k
        out.append(Sprite(left + av / 2.0, ay + av / 2.0, av, av, p["avatar"],
                          (1, 1, 1, a)))
        nk, nw, nh = p["name"]
        out.append(Sprite(left + av + 10 * k + nw / 2.0, ay + av / 2.0, nw, nh,
                          nk, (1, 1, 1, a)))
        # grade (right)
        gk, gw, gh = p["grade"]
        gx = pcx + self.PB_W * k / 2.0 - 20 * k - gw / 2.0
        out.append(Sprite(gx, ay + av / 2.0, gw, gh, gk, (1, 1, 1, a)))
        # score + acc bottom
        by = ay + av + 8 * k
        sk, sw, sh = p["score"]
        out.append(Sprite(left + sw / 2.0, by + sh / 2.0, sw, sh, sk,
                          (1, 1, 1, a)))
        ack, acw, ach = p["acc"]
        out.append(Sprite(left + acw / 2.0, by + sh + 2 * k + ach / 2.0, acw,
                          ach, ack, (1, 1, 1, 0.9 * a)))


def _clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n - 1] + "…"


def _bake_width(font, text: str, px: int) -> int:
    """The exact pixel width bake_text() produces for `text` at screen `px`
    with `font` (glyph bbox + the same symmetric padding) — used by
    _fit_text to size the font without a full rasterise."""
    if not text:
        return 1
    try:
        x0, _y0, x1, _y1 = font.getbbox(text)
    except AttributeError:
        w0, _h0 = font.getsize(text)          # type: ignore[attr-defined]
        x0, x1 = 0, w0
    pad = max(int(px) // 12, 2)
    return max(x1 - x0, 1) + 2 * pad


def _ellipsize(font, text: str, max_w: float, px: int) -> str:
    """Last-resort trim: drop characters from the end and append … until the
    result fits `max_w` (only reached when the font is already at its minimum
    size and the string is still too long)."""
    if _bake_width(font, text, px) <= max_w:
        return text
    while len(text) > 1:
        text = text[:-1]
        cand = text.rstrip() + "…"
        if _bake_width(font, cand, px) <= max_w:
            return cand
    return "…"
