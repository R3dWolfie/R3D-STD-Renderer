"""LAZER RESULTS SCREEN — the osu!(lazer) ranking screen ported to the std
renderer (owner spec 2026-07; the std default outro, R3D card behind
`results_style=r3d`).

PORTED (layout/animation semantics only — MIT, ppy/osu, no ppy assets;
every texture is procedurally baked in-repo, fonts = the repo's DejaVu):

  osu.Game/Screens/Ranking/ScorePanel.cs
  osu.Game/Screens/Ranking/Expanded/ExpandedPanelMiddleContent.cs
  osu.Game/Screens/Ranking/Expanded/Accuracy/AccuracyCircle.cs
  osu.Game/Screens/Ranking/Expanded/Accuracy/GradedCircles.cs
      — the graded ring: OsuColour.ForRank bands (D ff5a5a, C ff8e5d,
        B e3b130, A 88da20, S 02b5c3, X/SS de31ae) at the score-processor
        accuracy thresholds, with the GRADE_SPACING_PERCENTAGE (2/360)
        boundary notches; the achieved-accuracy arc SWEEPS in over
        ACCURACY_TRANSFORM_DURATION painted THROUGH those rank colours
        (each band's slice takes its ForRank stop); the virtual-SS notch
        (a non-SS play caps its arc at 1 − VIRTUAL_SS_PERCENTAGE so only a
        true SS closes the ring, and the SS/X band owns the 0.99→1.0
        slice); and the rank badge (grade letter) punching in at the end of
        the sweep.
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
from .hud import BAND_50, BAND_100, BAND_300
from .results import histogram_bins, mods_string
from .textures import _load_argon_font, _load_font

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
}
# alias the lazer rank-letter keys the meta layer may hand us
FOR_RANK["X"] = FOR_RANK["SS"]
FOR_RANK["SSH"] = FOR_RANK["SS"]
FOR_RANK["XH"] = FOR_RANK["SS"]
FOR_RANK["SH"] = FOR_RANK["S"]

# AccuracyCircle.GRADE_SPACING_PERCENTAGE = 2.0 / 360 — GradedCircles insets
# each band by half of this on each side, opening the boundary notches.
GRADE_SPACING_PERCENTAGE = 2.0 / 360.0


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


def slider_stats(sim) -> tuple[int, int, int, int]:
    """(tick_hit, tick_total, end_hit, end_total) over every slider
    verdict's part outcomes — the SLIDER TICK n/N + SLIDER END n/N rows.
    ticks include repeats (tick-equivalent in the ruleset)."""
    tick_hit = tick_total = end_hit = end_total = 0
    if sim is None:
        return 0, 0, 0, 0
    for v in sim.verdicts.values():
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


def bake_text(text: str, px: int, color, loader=_load_font) -> tuple:
    """One baked text line → (rgba, w, h). Blank text → 1×1 stub."""
    import numpy as np
    if not text:
        return np.zeros((1, 1, 4), dtype="u1"), 1, 1
    font = loader(max(int(px), 6))
    try:
        x0, y0, x1, y1 = font.getbbox(text)
    except AttributeError:
        w0, h0 = font.getsize(text)          # type: ignore[attr-defined]
        x0, y0, x1, y1 = 0, 0, w0, h0
    pad = max(px // 12, 2)
    w = max(x1 - x0, 1) + 2 * pad
    h = max(y1 - y0, 1) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rgb = tuple(int(round(c * 255)) for c in color)
    ImageDraw.Draw(img).text((pad - x0, pad - y0), text, font=font,
                             fill=(*rgb, 255))
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


def bake_avatar(px: int, initial: str, seed: int):
    """Procedural avatar chip: a two-tone disc with the player's initial
    (no osu!API avatar — honest placeholder)."""
    px = max(int(px), 8)
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    hue = (seed % 360) / 360.0
    c0 = _hsv(hue, 0.45, 0.55)
    c1 = _hsv((hue + 0.08) % 1.0, 0.5, 0.32)
    for y in range(px):
        f = y / max(px - 1, 1)
        col = tuple(int(round(_lerp(c0[i], c1[i], f) * 255)) for i in range(3))
        d.line([(0, y), (px, y)], fill=(*col, 255))
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, px - 1, px - 1], fill=255)
    img.putalpha(mask)
    ch = (initial or "?")[:1].upper()
    font = _load_font(int(px * 0.56))
    try:
        x0, y0, x1, y1 = font.getbbox(ch)
    except AttributeError:
        x1, y1 = font.getsize(ch); x0 = y0 = 0     # type: ignore
    d.text(((px - (x1 - x0)) / 2 - x0, (px - (y1 - y0)) / 2 - y0), ch,
           font=font, fill=(255, 255, 255, 235))
    return _to_rgba(img)


def _hsv(h: float, s: float, v: float) -> tuple[float, float, float]:
    import colorsys
    return colorsys.hsv_to_rgb(h, s, v)


def bake_accuracy_base(px: int):
    """The AccuracyCircle background: dark track ring, the ForRank graded
    bands (dimmed — GradedCircles), the boundary notches (GRADE_SPACING),
    and a rank-letter badge at each band. Baked once (accuracy-independent).
    Canvas is padded so the badges fit outside the ring."""
    import numpy as np
    S = max(int(px), 64)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2.0
    outer_r = S * 0.40
    ring_w = int(round(S * 0.085))
    bbox = [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r]
    half_gap = GRADE_SPACING_PERCENTAGE / 2.0
    # dark track
    d.arc(bbox, 0, 360, fill=(30, 32, 40, 255), width=ring_w)
    # graded bands (dimmed — the bright achieved arc paints over them). Each
    # band is inset by half the grade spacing on each end → the notch gaps.
    for lo, hi, g in rank_ring_bands():
        col = tuple(int(round(c * 200)) for c in FOR_RANK[g])
        a0 = acc_to_angle_deg(lo + half_gap)
        a1 = acc_to_angle_deg(hi - half_gap)
        if a1 > a0:
            d.arc(bbox, a0, a1, fill=(*col, 150), width=ring_w)
    # rank badges at each band's lower bound (ForRank-coloured)
    badge_r = S * 0.465
    for lo, _hi, g in rank_ring_bands():
        ang = math.radians(acc_to_angle_deg(lo))
        bx = cx + badge_r * math.cos(ang)
        by = cy + badge_r * math.sin(ang)
        _badge(img, bx, by, S * 0.052, g)
    return _to_rgba(img)


def _notch(d, cx, cy, outer_r, ring_w, ang_deg, color) -> None:
    ang = math.radians(ang_deg)
    r0 = outer_r - ring_w
    r1 = outer_r + 1
    d.line([(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)),
            (cx + r1 * math.cos(ang), cy + r1 * math.sin(ang))],
           fill=color, width=max(int(ring_w * 0.10), 2))


def _badge(img, bx, by, r, grade) -> None:
    d = ImageDraw.Draw(img)
    col = tuple(int(round(c * 255)) for c in FOR_RANK.get(grade,
                                                          (0.8, 0.8, 0.85)))
    d.ellipse([bx - r, by - r, bx + r, by + r], fill=(*col, 235))
    label = "SS" if grade == "SS" else grade
    font = _load_font(int(r * (1.0 if len(label) == 1 else 0.72)))
    try:
        x0, y0, x1, y1 = font.getbbox(label)
    except AttributeError:
        x1, y1 = font.getsize(label); x0 = y0 = 0    # type: ignore
    d.text((bx - (x1 - x0) / 2 - x0, by - (y1 - y0) / 2 - y0), label,
           font=font, fill=(20, 20, 26, 255))


def bake_accuracy_arc(px: int, progress_acc: float, color=None):
    """The bright achieved-accuracy arc (0 → progress_acc), painted through
    lazer's rank colours: each band's slice takes its ForRank stop
    (AccuracyCircle / GradedCircles), with the GRADE_SPACING boundary notches
    between fully-filled bands and a rounded white cap dot at the sweeping
    tip. `color` is ignored (kept for signature compat). Re-baked only while
    sweeping."""
    import numpy as np
    S = max(int(px), 64)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2.0
    outer_r = S * 0.40
    ring_w = int(round(S * 0.085))
    bbox = [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r]
    half_gap = GRADE_SPACING_PERCENTAGE / 2.0
    if progress_acc > 0.0005:
        for lo, hi, g in rank_ring_bands():
            if progress_acc <= lo:
                break
            filled_hi = min(hi, progress_acc)
            seg_lo = lo + (half_gap if lo > 0.0 else 0.0)
            # notch before the NEXT band only where this band is fully filled;
            # the growing tip keeps no gap
            seg_hi = filled_hi - (half_gap if filled_hi >= hi - 1e-9 else 0.0)
            if seg_hi <= seg_lo:
                continue
            col = tuple(int(round(c * 255)) for c in FOR_RANK[g])
            d.arc(bbox, acc_to_angle_deg(seg_lo), acc_to_angle_deg(seg_hi),
                  fill=(*col, 255), width=ring_w)
        # bright cap dot at the sweeping tip
        ang = math.radians(acc_to_angle_deg(progress_acc))
        tx = cx + outer_r * math.cos(ang)
        ty = cy + outer_r * math.sin(ang)
        cap = ring_w * 0.62
        d.ellipse([tx - cap, ty - cap, tx + cap, ty + cap],
                  fill=(255, 255, 255, 255))
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


class LazerResultsScreen:
    """Bakes the panel once, animates the two-stage reveal per frame."""

    def __init__(self, spr, data: ResultsData, total_ms: float,
                 speed: float = 1.0, argon_font: bool = False):
        self.spr = spr
        self.d = data
        self.w, self.h = float(spr.width), float(spr.height)
        # skinless (Argon league) → bundled OFL font; custom skin → DejaVu.
        self._font_loader = _load_argon_font if argon_font else _load_font
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
        # avatar + header
        self.avatar_key = self._put(bake_avatar(
            int(52 * k), d.player or "?", sum(map(ord, d.player or "?"))))
        self.name_row = self._text(d.player or "Player", 30, (1, 1, 1))
        self.title_row = self._text(_clip(d.title, 40), 34, (0.95, 0.96, 1.0))
        self.artist_row = self._text(_clip(d.artist, 48), 24,
                                     (0.72, 0.75, 0.85))
        # accuracy circle
        self.ACC_DISP = 380.0                 # canvas display size (virtual)
        cbake = int(self.ACC_DISP * k)
        self.acc_base_key = self._put(bake_accuracy_base(cbake))
        self.grade_letter = self._text(d.grade, 150, self.grade_rgb)
        self.target_arc = target_arc_value(self.acc_frac, d.grade)
        # score baked lazily (rolls)
        self.score_row = self._score_text(0)
        # star / diff / creator row
        star_txt = (f"★ {d.stars:.2f}" if d.stars is not None
                    else "★ --")
        self.star_row = self._text(star_txt, 26, (1.0, 0.82, 0.35))
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
                    int(40 * k), pb.get("player_name", "?"),
                    sum(map(ord, pb.get("player_name", "?"))))),
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

    def _grid_cell(self, label, value, color):
        return (self._text(label, 16, (0.6, 0.63, 0.73)),
                self._text(value, 32, color))

    def _score_text(self, value: int):
        rgba, w, h = bake_text(f"{value:,}", int(64 * self.k), (1, 1, 1))
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

        # PB card (fades out as the panel opens)
        if self.pb_parts is not None:
            pb_a = fade * (1.0 - _clamp01((age_ms - STAGE1_MS)
                                          / (OPEN_MS * 0.6)))
            if pb_a > 0.003:
                self._draw_pb(out, panel_cx, pb_a)

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

    def _draw_star_row(self, out, cx, top_y, a) -> float:
        k = self.k
        sk, sw, sh = self.star_row
        dk, dw, dh = self.diff_row
        ck, cw, ch = self.creator_row
        gap = 14 * k
        total = sw + gap + dw + gap + cw
        h = max(sh, dh, ch)
        x = cx - total / 2.0
        for key, w, hh in (self.star_row, self.diff_row, self.creator_row):
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
