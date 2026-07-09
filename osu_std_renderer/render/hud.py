"""Gameplay HUD — a port of osu!lazer's CURRENT default HUD with lazer's
own skin architecture (MIT, ppy/osu master; class + file cited per
element):

COMPONENT SELECTION (owner decision 2026-07-08, the HYBRID rule —
supersedes the same-day all-legacy-under-any-skin correction): selection
is PER ELEMENT — a custom skin's LEGACY component is used for each HUD
element the skin actually SHIPS textures for; every element the skin
LACKS uses the ARGON component. Skinless renders stay all-Argon:

    score + accuracy   skin ships the ScorePrefix digit set →
                       LegacyScoreCounter/LegacyAccuracyCounter (+ the
                       legacy progress pie); otherwise → ArgonScore-
                       Counter/ArgonAccuracyCounter (+ the Argon
                       progress strip)
    combo              skin ships the ComboPrefix digit set →
                       LegacyDefaultComboCounter; otherwise →
                       ArgonComboCounter
    hp bar             skin ships scorebar-colour/-bg →
                       LegacyHealthDisplay; otherwise →
                       ArgonHealthDisplay
    key overlay        skin ships inputoverlay-key/-background →
                       LegacyKeyCounterDisplay; otherwise →
                       ArgonKeyCounterDisplay
    grade badge        skin ranking-*-small when shipped, procedural
                       text otherwise (shared element, unchanged)

Inside a SELECTED legacy component, individual textures the skin doesn't
provide still come from the classic lg_* bakes (per-char font extras
etc.) — a selected legacy component never mixes in Argon pieces.

`legacy_defaults` (settings/CLI --legacy-defaults) forces the ALL-LEGACY
look: EVERY HUD element uses its legacy component (skin textures where
shipped, lg_* classic bakes otherwise — no Argon anywhere, even
skinless). Gameplay elements already fall back skin-else-legacy in every
mode; the synth hitsound bank follows the same league
(record/hitsounds.synth_style_for).

`renderer_default_font_and_ranks` (settings/CLI) forces the renderer's
own default (Argon) NUMBERS and procedural RANK text even under a skin
that ships fonts/rank images (fonts + ranks only — hp/keys keep their
league). PRECEDENCE: it WINS over legacy_defaults when both are set.

THE TWO COMPONENT SETS (element classes unchanged):

  ARGON (lazer's default skin):
    ArgonScoreCounter        osu.Game/Screens/Play/HUD/ArgonScoreCounter.cs
                             top-left block, right-aligned at x=250 over two
                             ArgonWedgePiece backdrops, 6-digit wireframe
                             cells behind the digits (WireframeOpacity .25),
                             rolling 250 ms Easing.Out
    ArgonAccuracyCounter     …/ArgonAccuracyCounter.cs — top-right, big
                             whole part + half-size ".dd" + %, "ACCURACY"
                             label (Blue0 #99ddff), rolling 250 ms
    ArgonComboCounter        …/ArgonComboCounter.cs — bottom-left (36,-66)
                             ×1.3, "COMBO" label, "<n>x", pop ×1.1 on
                             increment / ×0.8 + red flash (2 s) on a miss,
                             scale clamped [0.6, 1.4], eased back OutQuint
    ArgonHealthDisplay       …/ArgonHealthDisplay.cs + ArgonHealthDisplayParts/*
                             + osu-resources sh_ArgonBarPath(Utils/Background)
                             — the curved bar at (50,20), w=300, BarHeight 30:
                             the shader's path SDF is ported to a numpy
                             field (corner arcs ≈ quadratic Béziers, ≤0.2 px
                             off); damped 50 ms; miss = red glow segment
                             held 500 ms then eased back (300 ms OutQuint)
    ArgonKeyCounterDisplay   osu.Game/Screens/Play/ArgonKeyCounter(Display).cs
                             — bottom-right above the song progress, 52.5×45
                             cells: top indicator pill (press: drop 4 px/60 ms,
                             release: 250 ms OutQuart), name in Blue0 (white
                             while held), running count below
    ArgonSongProgress        …/ArgonSongProgress(Bar/Graph).cs — bottom
                             strip at 90 % width: time elapsed/remaining
                             row, faint density graph (200 buckets, tiered),
                             10 px rounded bar (bg gray(0.2)@0.3, white fill)

  LEGACY (lazer's legacy-skin components, used only when the skin ships
  the element's textures; classic-look lg_* bakes fill gaps INSIDE a
  selected component):
    LegacyScoreCounter       osu.Game/Skinning/LegacyScoreCounter.cs —
                             top-right ×0.96, margin 10, ScorePrefix font
                             (FixedWidth by '5', ScoreOverlap spacing),
                             6 leading digits (standardised), roll 1 s Out
    LegacyAccuracyCounter    …/LegacyAccuracyCounter.cs — under the score,
                             ×0.576, margins 9/17, same font, "0.00%"
    LegacySongProgress       …/LegacySongProgress.cs — the little pie left
                             of the accuracy (white 60 %, intro counts down
                             mirrored in (199,255,47)), ring + centre dot
    LegacyDefaultComboCounter …/LegacyDefaultComboCounter.cs — bottom-left
                             margin 10 ×1.28, ComboPrefix font, the TWO
                             counter behaviour: displayed count trails the
                             real one — +1 pops an additive ghost (×1.56→1,
                             α .6→0, 300 ms) and commits 160 ms later with
                             the small 1→1.1→1 pop; a break ROLLS the
                             displayed count down at 20 ms/step
    LegacyHealthDisplay      …/LegacyHealthDisplay.cs — scorebar-bg at
                             (0,0) (drawn LAST: stable's front-most health
                             quirk), scorebar-colour fill cropped to hp at
                             (7.5,7.8)×1.6 (new style, marker present) or
                             (3,10)×1.6 (old style), colour-0.. animation,
                             fill colour white→black→red under 50 %/20 %,
                             marker/ki-danger pieces with bulge + flash
    LegacyKeyCounterDisplay  …/LegacyKeyCounter(Display).cs — right edge:
                             inputoverlay-background rotated 90°, 46 px key
                             cells ×0.75 while held (160 ms Out), tints
                             #ffde00 (K) / #f8009e (M), InputOverlayText
                             colour, counts in the scoreentry font

SHARED (house elements, both paths): the hit-error meter + UR readout
(owner-directed: parked near the BOTTOM edge, ~16 UI px margin —
ERR_Y_FROM_BOTTOM), the combo-break edge vignette, the grade badge
(skin ranking-*-small when provided and renderer_default_font_and_ranks
is off, procedural text otherwise; behind show_grade).

HEALTH comes from ruleset/health.py — the lazer OsuHealthProcessor /
DrainingHealthProcessor port (what lazer itself pairs with BOTH health
displays).

SCORE PIN: pin_final_score(total) rescales the whole displayed score
curve so the LAST judgment lands exactly on the .osr's recorded total
(the mania reconcile pattern); the CLI wires it to meta.score.

ACCURACY: event-stream accuracy equals the replay's derived accuracy
exactly (the final-sample bug — sampling at last_object_end+1 ms missed
late-hit final events — is fixed by reading the LAST event, not a
timestamp). Display rounds to 2 dp with Python round() — the same
derivation replay.py uses for meta.accuracy — NOT lazer's
FormatUtils.FormatAccuracy floor (deliberate owner-decided deviation so
the HUD always matches the .osr-derived number).

Everything stays STATELESS per frame: rolls/damps/pops are pure
functions of the precomputed timelines (bounded-window replays for the
pop chains, exponential-kernel damping for the health smoothing), so
--dump-frames at any time shows exactly what the video shows.

SETTINGS-SURFACE ADDITIONS (2026-07, this phase — house elements, both
component paths):
  mod pills          show_mods — procedural rounded pills with the mod
                     acronyms (NC swallows DT, PF swallows SD — the
                     standard derivation), category-coloured, stacked
                     right-aligned under the accuracy block
  pp counter         show_pp_counter — render/pp.py's rosu gradual
                     timeline, rolled like the counters, top-left under
                     the hp bar; HIDES itself when rosu/the timeline is
                     unavailable (fail-soft)
  hit counter        show_hit_counter — live 300/100/50/miss column
                     under the mod pills (judgment-band colours)
  aim error meter    show_aim_error_meter — danser-style cursor-offset-
                     at-click scatter panel left of the hit-error bar:
                     crosshair + circle-edge ring + 10 s-fading dots +
                     the mean-offset stat (offsets in circle-radius
                     units — build_aim_points)
  strain graph       show_strain_graph — bottom fill graph over the map
                     (rosu strains or the density proxy, pp.py), swept
                     by progress, drawn UNDER the other HUD elements
  watermark          watermark_text — small corner text, bottom-right

Settings honored: show_score (score+acc), show_combo, show_hp_bar,
show_grade, show_key_overlay, show_progress, show_hit_error_meter,
show_unstable_rate, show_mods, show_pp_counter, show_hit_counter,
show_aim_error_meter, show_strain_graph, watermark_text, hud_scale,
hud_opacity, combo_break_flash. Still accepted-and-ignored (honest
list): progress_style (Argon/legacy progress replaces the danser
pie/bar styles), show_scoreboard/scoreboard_avatars (render/
scoreboard.py documents the osu!API hand-off stub).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

import numpy as np

from ..replay.replay import KEY_K1, KEY_K2, KEY_M1, KEY_M2
from ..ruleset import JudgmentKind, OsuHitWindows
from .gl import Sprite

UI_HEIGHT = 1080.0             # our virtual UI space (previous phases)
LAZER_UI_HEIGHT = 768.0        # lazer's HUD space (DrawSizePreserving 768)
KL = UI_HEIGHT / LAZER_UI_HEIGHT   # lazer px → UI px

# --- shared / house constants -------------------------------------------------------
ERR_HALF_W = 220.0             # hit-error bar half width (= the 50 window)
ERR_BAND_H = 8.0
ERR_TICK_W = 3.0
ERR_TICK_H = 22.0
ERR_TICK_FADE_MS = 10_000.0    # §4.6 HitErrorMeter.PointFadeOutTime = 10 s
ERR_ARROW_N = 10               # moving average over the last N hits
# owner-directed (2026-07): the hit-error bar + UR readout sit near the
# BOTTOM edge — the UR text's baseline ends ~16 UI px above the frame
# (56 = 16 margin + band_h/2 + 10 gap + UR_H). Was 132 (above the Argon
# progress strip).
ERR_Y_FROM_BOTTOM = 56.0
UR_H = 26.0

# Argon league — ArgonHitErrorMeter placement: vertical bars mirrored at the
# left+right screen edges (delta on the vertical axis, centred).
ARGON_ERR_MARGIN = 26.0        # bar centre inset from each screen edge (UI px)
ARGON_ERR_HALF_H = 96.0        # half the bar height (= the 50 window)
ARGON_ERR_BAR_W = 8.0          # zone bar width
ARGON_ERR_TICK_LEN = 22.0      # judgement-line length (horizontal)
ARGON_ERR_TICK_TH = 3.0        # judgement-line thickness

BREAK_FLASH_MS = 220.0
BREAK_FLASH_MIN_COMBO = 10
BREAK_FLASH_ALPHA = 0.38

BAND_300 = (0.38, 0.72, 1.00)
BAND_100 = (0.42, 0.88, 0.47)
BAND_50 = (0.95, 0.76, 0.36)

GRADE_COLORS = {
    "SS": (0.94, 0.86, 0.47),
    "S": (0.94, 0.86, 0.47),
    "A": (0.43, 0.86, 0.51),
    "B": (0.43, 0.71, 0.86),
    "C": (0.78, 0.51, 0.86),
    "D": (0.86, 0.43, 0.43),
}
GRADE_RANKING_ELEMENT = {      # grade → legacy small badge element
    "SS": "ranking-X-small", "S": "ranking-S-small", "A": "ranking-A-small",
    "B": "ranking-B-small", "C": "ranking-C-small", "D": "ranking-D-small",
}

_TRACKING = 0.05               # procedural-glyph letter-spacing

# --- Argon constants (values straight from the cited classes) -----------------------
BLUE0 = (0x99 / 255.0, 0xDD / 255.0, 0xFF / 255.0)      # OsuColour.Blue0
ARGON_ROLL_MS = 250.0          # Argon counters' RollingDuration
ARGON_DIGIT_H = 30.0           # argon-counter glyph height (240 px × 0.125)
ARGON_WIRE_ALPHA = 0.25        # WireframeOpacity default
ARGON_LABEL_H = 12.0           # OsuFont.Torus 12 bold labels
ARGON_LABEL_GAP = 12.0         # NumberContainer.Y with a label
ARGON_SCORE_RIGHT_X = 250.0    # score right edge (components_x_offset+200)
ARGON_SCORE_TOP_Y = 50.0       # wedge2.y(20) + 30
ARGON_SCORE_DIGITS = 6         # standardised RequiredDisplayDigits
ARGON_ACC_POS = (-20.0, 20.0)  # TopRight
ARGON_COMBO_POS = (36.0, -66.0)          # BottomLeft (ruleset layout)
ARGON_COMBO_SCALE = 1.3
ARGON_POP_UP = 1.1             # scale factor on increment
ARGON_POP_DOWN = 0.8           # on any decrease
ARGON_POP_MIN, ARGON_POP_MAX = 0.6, 1.4
ARGON_POP_MS = 500.0
ARGON_MISS_FLASH_MS = 2000.0
ARGON_KEY_W, ARGON_KEY_H = 52.5, 45.0    # 35×30 × scale_factor 1.5
ARGON_KEY_SPACING = 2.0
ARGON_KEY_LINE_H = 4.5         # line_height 3 × 1.5
ARGON_KEY_PRESS_OFFSET = 4.0
ARGON_KEY_NAME_H = 15.0        # 10 × 1.5
ARGON_KEY_COUNT_H = 21.0       # 14 × 1.5
ARGON_KEYS_POS = (-60.0, -66.0)          # BottomRight (hitError.Width+10, 66)
ARGON_PROGRESS_BAR_H = 10.0
ARGON_PROGRESS_WIDTH = 0.9     # Scale = (0.9, 1)
ARGON_PROGRESS_BOTTOM = 10.0   # Position (0, -padding)
ARGON_INFO_H = 14.0
GRAPH_BUCKETS = 100            # display_granularity 200, halved (quad budget)
GRAPH_TIERS = 5

# ArgonHealthDisplay geometry/colours
HP_POS = (50.0, 20.0)
HP_WIDTH = 300.0
HP_BAR_HEIGHT = 30.0
HP_MAIN_RADIUS = 10.0          # MAIN_PATH_RADIUS
HP_GLOW_RADIUS = 40.0          # glow_path_radius
HP_MAIN_GLOW_PORTION = 0.6
HP_GLOW_COLOUR = (0x7E / 255.0, 0xD7 / 255.0, 0xFD / 255.0, 0.5)
HP_MISS_BAR = (255 / 255.0, 93 / 255.0, 93 / 255.0)
HP_MISS_GLOW = (253 / 255.0, 0.0, 0.0)
HP_LINE_Y = 30.0               # healthLine: health.y + MAIN_PATH_RADIUS
HP_LINE_SIZE = (45.0, 3.0)
HP_DAMP_HALF_LIFE = 50.0       # Interpolation.DampContinuously(…, 50, …)
HP_MISS_HOLD_MS = 500.0
HP_MISS_EASE_MS = 300.0

# --- legacy constants ---------------------------------------------------------------
LEGACY_SCORE_SCALE = 0.96
LEGACY_SCORE_MARGIN_X = 10.0
LEGACY_SCORE_ROLL_MS = 1000.0
LEGACY_ACC_SCALE = 0.6 * 0.96
LEGACY_ACC_MARGIN = (17.0, 9.0)          # horizontal, vertical
LEGACY_ACC_ROLL_MS = 375.0     # PercentageCounter.RollingDuration
LEGACY_PIE_SIZE = 33.0
LEGACY_PIE_GAP = 18.0
LEGACY_PIE_INTRO_COLOR = (199 / 255.0, 1.0, 47 / 255.0)
LEGACY_PIE_ALPHA = 153 / 255.0
LEGACY_COMBO_SCALE = 1.28
LEGACY_COMBO_MARGIN = 10.0
LEGACY_COMBO_ROLL_STEP_MS = 20.0         # rolling_duration per element
LEGACY_COMBO_BIG_POP_MS = 300.0
LEGACY_COMBO_SMALL_POP_MS = 100.0
LEGACY_COMBO_COMMIT_MS = 160.0           # big_pop_out_duration − 140
LEGACY_COMBO_FADE_MS = 100.0
LEGACY_COMBO_HEIGHT_RATIO = 0.625        # font_height_ratio
LEGACY_FILL_POS_NEW = (7.5 * 1.6, 7.8 * 1.6)
LEGACY_FILL_POS_OLD = (3.0 * 1.6, 10.0 * 1.6)
LEGACY_EPIC_CUTOFF = 0.5
LEGACY_KEY_CELL = 46.0
LEGACY_KEY_SPACING = 1.8
LEGACY_KEY_FLOW_OFFSET = (-1.5, 7.0)
LEGACY_KEY_PRESS_SCALE = 0.75
LEGACY_KEY_PRESS_MS = 160.0
LEGACY_KEY_ACTIVE_TOP = (1.0, 0xDE / 255.0, 0.0)        # #ffde00 (K1/K2)
LEGACY_KEY_ACTIVE_BOTTOM = (0xF8 / 255.0, 0.0, 0x9E / 255.0)  # #f8009e
LEGACY_FONT_H = 45.0           # textures.LEGACY_FONT_HEIGHT

KEY_LABELS = ("K1", "K2", "M1", "M2")
ARGON_KEY_ORDER = (0, 1, 2, 3)

# --- mod pills (§4.6 Gameplay.Mods) ---------------------------------------------
# (bit, acronym) in display order; NC swallows DT, PF swallows SD (the
# standard derivation — NC/PF set both bits in the .osr)
_MOD_DEFS = (
    (0x2, "EZ"), (0x1, "NF"), (0x100, "HT"),
    (0x10, "HR"), (0x20, "SD"), (0x4000, "PF"), (0x40, "DT"), (0x200, "NC"),
    (0x8, "HD"), (0x400, "FL"),
    (0x80, "RX"), (0x2000, "AP"), (0x1000, "SO"), (0x20000000, "V2"),
    (0x4, "TD"),
)
_MOD_REDUCTION = {"EZ", "NF", "HT", "SO"}
_MOD_AUTOMATION = {"RX", "AP", "V2", "TD"}
MOD_COLOR_REDUCTION = (0.45, 0.78, 0.36)     # greens (lazer's reduction)
MOD_COLOR_INCREASE = (0.90, 0.32, 0.42)      # reds (difficulty increase)
MOD_COLOR_AUTOMATION = (0.36, 0.62, 0.92)    # blues (automation/special)
MOD_PILL_H = 22.0              # lazer px
MOD_PILL_PAD_X = 9.0
MOD_PILL_GAP = 5.0
MOD_TEXT_FRAC = 0.60           # text height / pill height

HITC_ROW_H = 19.0              # hit-counter row height (lazer px)
HITC_LABELS = ("300", "100", "50", "X")

PP_TOP_Y = 92.0                # pp counter: top-left under the hp bar
PP_LEFT_X = 50.0
PP_DIGIT_H = 24.0

AIM_PANEL_R = 66.0             # aim-error panel radius, 1080-UI px
AIM_RING_FRAC = 0.62           # circle-edge ring radius / panel radius
AIM_DOT_PX = 7.0
AIM_GAP_FROM_ERR = 150.0       # panel centre left of the err-bar edge

STRAIN_H = 46.0                # strain graph max column height (lazer px)
STRAIN_BOTTOM_GAP = 26.0       # above the Argon progress strip
STRAIN_MAX_COLS = 160

WATERMARK_H = 12.0             # lazer px


def mods_to_acronyms(mods: int) -> list[str]:
    """Active mod acronyms in display order. NC includes the DT bit and
    PF the SD bit — the implied halves are dropped (standard behaviour)."""
    out = [acr for bit, acr in _MOD_DEFS if mods & bit]
    if "NC" in out and "DT" in out:
        out.remove("DT")
    if "PF" in out and "SD" in out:
        out.remove("SD")
    return out


def mod_pill_color(acr: str) -> tuple[float, float, float]:
    if acr in _MOD_REDUCTION:
        return MOD_COLOR_REDUCTION
    if acr in _MOD_AUTOMATION:
        return MOD_COLOR_AUTOMATION
    return MOD_COLOR_INCREASE


def build_aim_points(sim, frames, circle_radius: float,
                     ) -> list[tuple[float, float, float]]:
    """[(hit_time, dx, dy)] time-sorted — the cursor's offset from the
    object centre AT THE CLICK, in circle-radius units (1.0 = the rim),
    for every hit circle/slider head (§4.6 AimErrorMeter's data). Misses
    have no click to measure — skipped, like danser."""
    from ..replay.replay import cursor_at as _cursor_at
    if sim is None or not frames or circle_radius <= 0:
        return []
    pts: list[tuple[float, float, float]] = []
    for v in sim.verdicts.values():
        if v.obj_kind not in ("circle", "slider") or v.hit_time is None:
            continue
        x, y, _ = _cursor_at(frames, v.hit_time)
        pts.append((v.hit_time, (x - v.pos[0]) / circle_radius,
                    (y - v.pos[1]) / circle_radius))
    pts.sort(key=lambda p: p[0])
    return pts


# --- easing (osu!framework Easing.*) -------------------------------------------------

def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def ease_out_quad(p: float) -> float:
    p = _clamp01(p)
    return 1.0 - (1.0 - p) * (1.0 - p)


def ease_out_quart(p: float) -> float:
    p = _clamp01(p)
    return 1.0 - (1.0 - p) ** 4


def ease_out_quint(p: float) -> float:
    p = _clamp01(p)
    return 1.0 - (1.0 - p) ** 5


def ease_in_quad(p: float) -> float:
    p = _clamp01(p)
    return p * p


# --- pure math (unit-tested, no GL) ---------------------------------------------

def std_accuracy(c300: int, c100: int, c50: int, cmiss: int) -> float:
    """Standard osu! accuracy over object judgments (0..1)."""
    total = c300 + c100 + c50 + cmiss
    if total == 0:
        return 1.0
    return (300 * c300 + 100 * c100 + 50 * c50) / (300.0 * total)


def acc_display_value(acc: float) -> float:
    """The DISPLAYED percentage: round(acc·100, 2) — the exact derivation
    replay.py uses for meta.accuracy, so the HUD and the replay line can
    never disagree. (lazer's FormatAccuracy FLOORS instead — deliberate
    deviation, owner-decided: the .osr-derived number wins.)"""
    return round(acc * 100.0, 2)


def grade_for(c300: int, c100: int, c50: int, cmiss: int) -> str:
    """osu!(lazer)-EXACT rank. Ported from
    osu.Game/Rulesets/Scoring/ScoreProcessor.RankFromScore (accuracy
    cutoffs X=1 / S=.95 / A=.9 / B=.8 / C=.7 / D=0) with the std override
    osu.Game.Rulesets.Osu/Scoring/OsuScoreProcessor.RankFromScore, which
    downgrades S/X to A when any Miss is present. Silver (HD/FL SH/XH)
    variants are a display concern, skipped."""
    total = c300 + c100 + c50 + cmiss
    if total == 0:
        return "SS"          # nothing judged yet — the rank Bindable starts X
    acc = std_accuracy(c300, c100, c50, cmiss)
    # ScoreProcessor.RankFromScore (accuracy is 0..1; X requires exactly 1)
    if acc >= 1.0:
        rank = "SS"
    elif acc >= 0.95:
        rank = "S"
    elif acc >= 0.9:
        rank = "A"
    elif acc >= 0.8:
        rank = "B"
    elif acc >= 0.7:
        rank = "C"
    else:
        rank = "D"
    # OsuScoreProcessor override: a miss caps S/X at A
    if cmiss > 0 and rank in ("S", "SS"):
        rank = "A"
    return rank


def unstable_rate(deltas) -> float:
    """UR = 10 × the population stddev of the signed hit deltas."""
    n = len(deltas)
    if n < 2:
        return 0.0
    mean = sum(deltas) / n
    var = sum((d - mean) ** 2 for d in deltas) / n
    return 10.0 * math.sqrt(max(var, 0.0))


def rolled(prev: float, target: float, age_ms: float,
           roll_ms: float = ARGON_ROLL_MS, ease=ease_out_quad) -> float:
    """RollingCounter<T> value tween: prev → target over roll_ms after a
    change, eased (Argon counters: 250 ms Easing.Out(Quad); legacy score:
    1000 ms)."""
    if age_ms >= roll_ms or roll_ms <= 0:
        return target
    if age_ms <= 0:
        return prev
    return prev + (target - prev) * ease(age_ms / roll_ms)


def argon_combo_scale_at(changes: list[tuple[float, int, int]],
                         t: float) -> float:
    """ArgonComboCounter's NumberContainer scale at time t, replayed
    statelessly over the bounded recent window (pops fully decay after
    ARGON_POP_MS): each combo CHANGE multiplies the current eased scale by
    1.1 (increase) / 0.8 (decrease), clamped [0.6, 1.4], then eases back
    to 1 over 500 ms OutQuint (2000 ms on a combo-breaking miss).
    `changes` = [(time, old, new)] time-sorted."""
    window = ARGON_MISS_FLASH_MS + ARGON_POP_MS
    lo = bisect.bisect_left([c[0] for c in changes], t - window)
    scale, since, dur = 1.0, None, ARGON_POP_MS
    for time, old, new in changes[lo:]:
        if time > t:
            break
        if since is not None:
            p = ease_out_quint((time - since) / dur)
            scale = scale + (1.0 - scale) * p
        was_miss = old > 1 and new == 0
        factor = ARGON_POP_UP if new > old else ARGON_POP_DOWN
        scale = min(max(scale * factor, ARGON_POP_MIN), ARGON_POP_MAX)
        since, dur = time, (ARGON_MISS_FLASH_MS if was_miss
                            else ARGON_POP_MS)
    if since is None:
        return 1.0
    p = ease_out_quint((t - since) / dur)
    return scale + (1.0 - scale) * p


def legacy_combo_display_timeline(changes: list[tuple[float, int]],
                                  ) -> tuple[list[tuple[float, int]],
                                             list[float],
                                             list[tuple[float, int]]]:
    """LegacyDefaultComboCounter's displayed-vs-actual TWO-counter state
    machine, replayed offline over the full combo change stream:

      +1 increment  → an additive GHOST of the new value pops (×1.56→1,
                      α 0.6→0 over 300 ms) while the displayed count still
                      shows the old value; the displayed count commits
                      160 ms later (big_pop_out_duration − 140) with the
                      small 1→1.1→1 pop — unless another change lands
                      first (the scheduledPopOutCurrentId invalidation).
      reset to 0    → the displayed count ROLLS down one step per 20 ms
                      (rolling_duration), cut short (snapped to 0) by the
                      next change.

    Returns (displayed_events [(t, value)], small_pop_times, ghosts
    [(t, value)]) — all time-sorted, consumed by bisect at draw time."""
    disp_events: list[tuple[float, int]] = [(-math.inf, 0)]
    pops: list[float] = []
    ghosts: list[tuple[float, int]] = []
    disp = 0
    prev_actual = 0
    prev_was_roll = False
    for i, (t, v) in enumerate(changes):
        next_t = changes[i + 1][0] if i + 1 < len(changes) else math.inf
        if prev_was_roll:
            # a change during/after a roll: FinishTransforms snaps the
            # displayed count to the roll target (0)
            if disp != 0:
                disp = 0
                disp_events.append((t, 0))
        if v == 0:
            # onCountRolling: roll displayed → 0, 20 ms per step
            steps = disp
            for k in range(1, steps + 1):
                st = t + k * LEGACY_COMBO_ROLL_STEP_MS
                if st >= next_t:
                    break
                disp = steps - k
                disp_events.append((st, disp))
            prev_was_roll = True
        elif v == prev_actual + 1:
            # onCountIncrement: catch-up + ghost + delayed commit
            if disp < prev_actual:
                disp += 1
                disp_events.append((t, disp))
            ghosts.append((t, v))
            commit = t + LEGACY_COMBO_COMMIT_MS
            if commit < next_t and disp < v:
                disp += 1
                disp_events.append((commit, disp))
                pops.append(commit)
            prev_was_roll = False
        else:
            disp = v
            disp_events.append((t, disp))
            prev_was_roll = False
        prev_actual = v
    return disp_events, pops, ghosts


def layout_run(text: str, aspects: dict[str, float], height: float,
               tracking: float = _TRACKING,
               mono_advance: float | None = None,
               ) -> tuple[list[tuple[str, float, float]], float]:
    """[(char, center_x_from_run_left, draw_width)], total_width.

    mono_advance (aspect units, i.e. width/height): DIGITS occupy a fixed
    cell of that width so rolling numbers don't jitter; other glyphs use
    their natural width. Spaces advance 0.45×height and draw nothing."""
    gap = tracking * height
    x = 0.0
    out: list[tuple[str, float, float]] = []
    for ch in text:
        if ch == " ":
            x += 0.45 * height
            continue
        w = aspects.get(ch, 0.6) * height
        cell = w
        if mono_advance is not None and ch.isdigit():
            cell = mono_advance * height
        out.append((ch, x + cell / 2.0, w))
        x += cell + gap
    total = max(x - gap, 0.0) if out or text else 0.0
    return out, total


def damped(sample_fn, t: float, half_life_ms: float,
           window_mult: float = 8.0, steps: int = 24) -> float:
    """Stateless Interpolation.DampContinuously: the damped value is the
    exponential-kernel average of the target's recent history —
    y(t) = e^(−λT)·x(t−T) + Σ λ·e^(−λs)·x(t−s)·Δs over the last
    T = window_mult half-lives (residual < 0.4 %)."""
    lam = math.log(2.0) / max(half_life_ms, 1e-6)
    T = window_mult * half_life_ms
    h = T / steps
    y = math.exp(-lam * T) * sample_fn(t - T)
    for i in range(steps):
        s = (i + 0.5) * h
        y += lam * math.exp(-lam * s) * h * sample_fn(t - s)
    return y


# --- replay key channels (K1/K2/M1/M2, de-conflated) ------------------------------

class KeySeries:
    """Per-frame key-overlay state: which of the 4 channels is held, the
    cumulative press count per channel (press EDGES only), and the press/
    release edge TIMES per channel (the key-counter animations key off
    edge ages). Stable sets M1|K1 together for keyboard taps, so a channel
    counts as MOUSE only when its M bit is set WITHOUT the K bit."""

    def __init__(self, frames, gameplay_start: float = -float("inf")):
        # m-1: presses BEFORE gameplay_start (the first object's start) are
        # warm-up taps — danser/stable start the key counters at 0 and never
        # count them. The held state + edge times still track (so a key still
        # lights up if held), but the displayed COUNT ignores pre-gameplay
        # press edges.
        self.times: list[float] = []
        self.held: list[int] = []          # 4-bit mask: 1=K1 2=K2 4=M1 8=M2
        self.counts: list[tuple[int, int, int, int]] = []
        self.press_times: list[list[float]] = [[], [], [], []]
        self.release_times: list[list[float]] = [[], [], [], []]
        prev = 0
        c = [0, 0, 0, 0]
        for f in frames:
            k1 = 1 if f.keys & KEY_K1 else 0
            k2 = 1 if f.keys & KEY_K2 else 0
            m1 = 1 if (f.keys & KEY_M1) and not k1 else 0
            m2 = 1 if (f.keys & KEY_M2) and not k2 else 0
            mask = k1 | (k2 << 1) | (m1 << 2) | (m2 << 3)
            edges = mask & ~prev
            drops = prev & ~mask
            counts_press = f.time_ms >= gameplay_start
            for ch in range(4):
                if edges & (1 << ch):
                    if counts_press:
                        c[ch] += 1
                    self.press_times[ch].append(f.time_ms)
                if drops & (1 << ch):
                    self.release_times[ch].append(f.time_ms)
            prev = mask
            self.times.append(f.time_ms)
            self.held.append(mask)
            self.counts.append((c[0], c[1], c[2], c[3]))
        self.total_counts = tuple(c) if frames else (0, 0, 0, 0)
        self.used = tuple(v > 0 for v in self.total_counts)

    def state_at(self, t: float) -> tuple[int, tuple[int, int, int, int]]:
        i = bisect.bisect_right(self.times, t) - 1
        if i < 0:
            return 0, (0, 0, 0, 0)
        return self.held[i], self.counts[i]

    def last_press_at(self, ch: int, t: float) -> float | None:
        i = bisect.bisect_right(self.press_times[ch], t) - 1
        return self.press_times[ch][i] if i >= 0 else None

    def last_release_at(self, ch: int, t: float) -> float | None:
        i = bisect.bisect_right(self.release_times[ch], t) - 1
        return self.release_times[ch][i] if i >= 0 else None


# --- precomputed HUD timeline (no GL; queried statelessly per frame) ---------------

@dataclass
class BreakInfo:
    time_ms: float
    prev_combo: int


class HudData:
    """Arrays the per-frame draws bisect into, built once from the ruleset
    SimResult: score/acc/grade per object judgment, the part-level combo
    timeline (+ the Argon pop change list and the LEGACY displayed-count
    two-counter timeline), break moments, and the signed hit-delta series
    with prefix sums for O(log n) live UR."""

    def __init__(self, sim):
        ev = sim.events                      # time-sorted by construction
        self.ev_times = [e.time_ms for e in ev]
        self.ev_scores = [e.score_after for e in ev]
        self.ev_accs = [e.acc_after for e in ev]
        self.ev_grades: list[str] = []
        self.ev_counts: list[tuple[int, int, int, int]] = []
        c = {JudgmentKind.HIT300: 0, JudgmentKind.HIT100: 0,
             JudgmentKind.HIT50: 0, JudgmentKind.MISS: 0}
        for e in ev:
            c[e.kind] += 1
            self.ev_counts.append((c[JudgmentKind.HIT300],
                                   c[JudgmentKind.HIT100],
                                   c[JudgmentKind.HIT50],
                                   c[JudgmentKind.MISS]))
            self.ev_grades.append(grade_for(
                c[JudgmentKind.HIT300], c[JudgmentKind.HIT100],
                c[JudgmentKind.HIT50], c[JudgmentKind.MISS]))

        tl = sim.combo_timeline
        self.ct_times = [t for t, _ in tl]
        self.ct_combos = [v for _, v in tl]
        self.max_combo = sim.final_max_combo
        self.breaks: list[BreakInfo] = []
        self.combo_changes: list[tuple[float, int, int]] = []
        prev = 0
        for t, v in tl:
            self.combo_changes.append((t, prev, v))
            if v == 0 and prev > 0:
                self.breaks.append(BreakInfo(t, prev))
            prev = v
        self.bk_times = [b.time_ms for b in self.breaks]
        # legacy two-counter displayed timeline (+ pops/ghosts)
        (self.lg_disp_events, self.lg_pops,
         self.lg_ghosts) = legacy_combo_display_timeline(tl)
        self._lg_disp_times = [t for t, _ in self.lg_disp_events]
        self._lg_ghost_times = [t for t, _ in self.lg_ghosts]

        hits: list[tuple[float, float]] = []
        for v in sim.verdicts.values():
            if (v.obj_kind in ("circle", "slider")
                    and v.hit_time is not None and v.delta is not None):
                hits.append((v.hit_time, v.delta))
        hits.sort(key=lambda h: h[0])
        self.err_times = [h[0] for h in hits]
        self.err_deltas = [h[1] for h in hits]
        self._s1 = [0.0]
        self._s2 = [0.0]
        for d in self.err_deltas:
            self._s1.append(self._s1[-1] + d)
            self._s2.append(self._s2[-1] + d * d)

    # -- queries ------------------------------------------------------------------

    def score_at(self, t: float, roll_ms: float = ARGON_ROLL_MS) -> float:
        i = bisect.bisect_right(self.ev_times, t) - 1
        if i < 0:
            return 0.0
        prev = self.ev_scores[i - 1] if i > 0 else 0
        return rolled(prev, self.ev_scores[i], t - self.ev_times[i],
                      roll_ms)

    def acc_at(self, t: float, roll_ms: float = ARGON_ROLL_MS) -> float:
        i = bisect.bisect_right(self.ev_times, t) - 1
        if i < 0:
            return 1.0
        prev = self.ev_accs[i - 1] if i > 0 else 1.0
        return rolled(prev, self.ev_accs[i], t - self.ev_times[i], roll_ms)

    def final_acc(self) -> float:
        """The end-state accuracy: the LAST event's value — NOT a sample
        at some timestamp. (The 84.72-vs-84.73 quirk: circles hit LATE
        judge after the last object's END time, so sampling at
        last_end+1 ms missed the final event.)"""
        return self.ev_accs[-1] if self.ev_accs else 1.0

    def final_score(self) -> int:
        return self.ev_scores[-1] if self.ev_scores else 0

    def grade_at(self, t: float) -> str:
        i = bisect.bisect_right(self.ev_times, t) - 1
        return self.ev_grades[i] if i >= 0 else "SS"

    def counts_at(self, t: float) -> tuple[int, int, int, int]:
        """(300s, 100s, 50s, misses) judged so far — the hit counter."""
        i = bisect.bisect_right(self.ev_times, t) - 1
        return self.ev_counts[i] if i >= 0 else (0, 0, 0, 0)

    def combo_at(self, t: float) -> tuple[int, float]:
        """(combo, ms since it last changed)."""
        i = bisect.bisect_right(self.ct_times, t) - 1
        if i < 0:
            return 0, math.inf
        return self.ct_combos[i], t - self.ct_times[i]

    def combo_displayed_at(self, t: float,
                           roll_ms: float = ARGON_ROLL_MS) -> int:
        """ArgonComboCounter's DisplayedCount: the actual combo rolled
        over roll_ms OutQuad per change."""
        i = bisect.bisect_right(self.ct_times, t) - 1
        if i < 0:
            return 0
        prev = self.ct_combos[i - 1] if i > 0 else 0
        v = rolled(float(prev), float(self.ct_combos[i]),
                   t - self.ct_times[i], roll_ms)
        return int(round(v))

    def legacy_combo_displayed_at(self, t: float) -> int:
        i = bisect.bisect_right(self._lg_disp_times, t) - 1
        return self.lg_disp_events[i][1] if i >= 0 else 0

    def legacy_combo_pop_at(self, t: float) -> float:
        """The small 1→1.1→1 displayed-count pop (100 ms, In then Out)."""
        i = bisect.bisect_right(self.lg_pops, t) - 1
        if i < 0:
            return 1.0
        age = t - self.lg_pops[i]
        half = LEGACY_COMBO_SMALL_POP_MS / 2.0
        if age < 0 or age >= LEGACY_COMBO_SMALL_POP_MS:
            return 1.0
        if age < half:
            return 1.0 + 0.1 * ease_in_quad(age / half)
        return 1.1 - 0.1 * ease_out_quad((age - half) / half)

    def legacy_combo_ghost_at(self, t: float) -> tuple[int, float] | None:
        """(value, age_ms) of the live additive pop-out ghost, if any."""
        i = bisect.bisect_right(self._lg_ghost_times, t) - 1
        if i < 0:
            return None
        gt, gv = self.lg_ghosts[i]
        age = t - gt
        if age >= LEGACY_COMBO_BIG_POP_MS:
            return None
        return gv, age

    def last_break_at(self, t: float) -> BreakInfo | None:
        i = bisect.bisect_right(self.bk_times, t) - 1
        return self.breaks[i] if i >= 0 else None

    def ur_at(self, t: float) -> tuple[float, float, int]:
        """(live UR, moving avg of the last ERR_ARROW_N deltas, n_hits)."""
        n = bisect.bisect_right(self.err_times, t)
        if n < 1:
            return 0.0, 0.0, 0
        k = min(n, ERR_ARROW_N)
        avg = (self._s1[n] - self._s1[n - k]) / k
        if n < 2:
            return 0.0, avg, n
        mean = self._s1[n] / n
        var = self._s2[n] / n - mean * mean
        return 10.0 * math.sqrt(max(var, 0.0)), avg, n

    def errors_in_window(self, t: float,
                         window_ms: float = ERR_TICK_FADE_MS):
        """[(age_ms, delta)] for hits inside the fade window, oldest first."""
        lo = bisect.bisect_left(self.err_times, t - window_ms)
        hi = bisect.bisect_right(self.err_times, t)
        return [(t - self.err_times[i], self.err_deltas[i])
                for i in range(lo, hi)]


# --- ArgonHealthDisplay bar field (sh_ArgonBarPathUtils.h port) -----------------------

class ArgonBarField:
    """The Argon health bar's path SDF, ported from osu-resources
    sh_ArgonBarPathUtils.h `getBarTexturePosition` into precomputed numpy
    fields: per pixel the DISTANCE to the full path and the arc-length
    PROGRESS of the closest point. A sub-range [a, b] (the shader's
    progressRange) is then exact via the connected-chain identity:
    closest point on the clipped path = closest point on the full path
    when its progress ∈ [a, b], else the nearer clip endpoint.

    The two corner arcs are approximated by quadratic Béziers through the
    tangent intersections p2/p3 (max deviation ≈ 0.2 px at radius 10 —
    invisible at HUD scale; noted honestly)."""

    def __init__(self, width_l: float, height_l: float, px_per_unit: float,
                 margin_l: float):
        self.w, self.h = width_l, height_l
        self.margin = margin_l
        self.scale = px_per_unit
        pts = self._path_points(width_l, height_l, HP_MAIN_RADIUS)
        seg = np.diff(pts, axis=0)
        seg_len = np.hypot(seg[:, 0], seg[:, 1])
        cum = np.concatenate([[0.0], np.cumsum(seg_len)])
        total = max(cum[-1], 1e-9)
        self._pts = pts
        self._cum_n = cum / total            # normalized progress per point

        gw = int(round((width_l + 2 * margin_l) * px_per_unit))
        gh = int(round((height_l + 2 * margin_l) * px_per_unit))
        self.gw, self.gh = gw, gh
        yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float64)
        self.xx = (xx + 0.5) / px_per_unit - margin_l   # lazer units
        self.yy = (yy + 0.5) / px_per_unit - margin_l

        d_best = np.full((gh, gw), 1e9)
        s_best = np.zeros((gh, gw))
        for i in range(len(pts) - 1):
            ax, ay = pts[i]
            bx, by = pts[i + 1]
            ux, uy = bx - ax, by - ay
            ll = ux * ux + uy * uy
            if ll < 1e-12:
                continue
            tt = np.clip(((self.xx - ax) * ux + (self.yy - ay) * uy) / ll,
                         0.0, 1.0)
            dd = np.hypot(self.xx - (ax + tt * ux),
                          self.yy - (ay + tt * uy))
            closer = dd < d_best
            d_best = np.where(closer, dd, d_best)
            s_here = self._cum_n[i] + tt * (self._cum_n[i + 1]
                                            - self._cum_n[i])
            s_best = np.where(closer, s_here, s_best)
        self.d = d_best
        self.s = s_best

    @staticmethod
    def _path_points(w: float, h: float, r: float,
                     corner_samples: int = 24) -> np.ndarray:
        """getBarTexturePosition's composite path as a dense polyline:
        top line → corner arc (Bézier) → slash → corner arc → bottom
        line. Constants straight from the shader."""
        p1 = (min(r, w * 0.5), min(r, h * 0.5))
        p4 = (max(w - r, w * 0.5), max(h - r, h * 0.5))
        if abs(p4[1] - p1[1]) < 1e-9:
            return np.array([p1, p4])
        curve_start_offset = 70.0
        curve_end_offset = 40.0
        curve_smoothness = 10.0
        top_width = max(w - r - curve_start_offset, p1[0]) - p1[0]
        bottom_width = p4[0] - max(w - r - curve_end_offset, p1[0])
        if top_width < bottom_width:
            top_width = bottom_width = (top_width + bottom_width) * 0.5
        p2 = (p1[0] + top_width, p1[1])
        p3 = (p4[0] - bottom_width, p4[1])
        slash_len = math.hypot(p3[0] - p2[0], p3[1] - p2[1])
        ro = min(min(top_width, slash_len * 0.5), curve_smoothness)
        arc1_start = (p2[0] - ro, p2[1])
        arc1_end = (p2[0] + (p3[0] - p2[0]) * ro / slash_len,
                    p2[1] + (p3[1] - p2[1]) * ro / slash_len)
        arc2_start = (p2[0] + (p3[0] - p2[0]) * (1 - ro / slash_len),
                      p2[1] + (p3[1] - p2[1]) * (1 - ro / slash_len))
        arc2_end = (p3[0] + ro, p3[1])

        def bezier(a, ctrl, b):
            ts = np.linspace(0.0, 1.0, corner_samples)[1:-1]
            return [((1 - u) ** 2 * a[0] + 2 * (1 - u) * u * ctrl[0]
                     + u * u * b[0],
                     (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * ctrl[1]
                     + u * u * b[1]) for u in ts]

        pts = [p1, arc1_start]
        pts += bezier(arc1_start, p2, arc1_end)
        pts += [arc1_end, arc2_start]
        pts += bezier(arc2_start, p3, arc2_end)
        pts += [arc2_end, p4]
        return np.array(pts)

    def pos(self, s: float) -> tuple[float, float]:
        s = _clamp01(s)
        x = float(np.interp(s, self._cum_n, self._pts[:, 0]))
        y = float(np.interp(s, self._cum_n, self._pts[:, 1]))
        return x, y

    def sub_distance(self, a: float, b: float) -> np.ndarray:
        """Distance field to the sub-path [a, b] (b ≥ a)."""
        if b <= a + 1e-9:
            px, py = self.pos(a)
            return np.hypot(self.xx - px, self.yy - py)
        inside = (self.s >= a) & (self.s <= b)
        pa, pb = self.pos(a), self.pos(b)
        cap = np.minimum(np.hypot(self.xx - pa[0], self.yy - pa[1]),
                         np.hypot(self.xx - pb[0], self.yy - pb[1]))
        return np.where(inside, self.d, cap)

    def bar_rgba(self, a: float, b: float, radius: float,
                 glow_portion: float, bar_rgb, glow_rgba,
                 xgrad: bool = False, alpha_mult: float = 1.0,
                 ) -> np.ndarray:
        """sh_ArgonBarPath.fs getColour over the sub-path field: solid
        barColour core, 1 px blend, then the glow falloff (mix^8)."""
        D = np.clip(self.sub_distance(a, b), 0.0, radius)
        agp = radius * glow_portion
        core = np.clip((radius - agp - D), 0.0, 1.0)       # 1 px blend edge
        mixv = np.clip(1.0 - (D - radius + agp) / max(agp, 1e-9), 0.0, 1.0)
        glow_a = glow_rgba[3] * mixv ** 8
        rgb = np.empty((self.gh, self.gw, 3))
        alpha = core * 1.0 + (1.0 - core) * glow_a
        for c in range(3):
            rgb[..., c] = core * bar_rgb[c] + (1.0 - core) * glow_rgba[c]
        if xgrad:
            g = 0.8 + 0.2 * np.clip((self.xx / max(self.w, 1e-9)), 0.0, 1.0)
            alpha = alpha * g
        alpha = alpha * alpha_mult
        out = np.empty((self.gh, self.gw, 4), dtype=np.uint8)
        out[..., :3] = np.round(rgb * 255.0).astype(np.uint8)
        out[..., 3] = np.round(np.clip(alpha, 0.0, 1.0) * 255.0
                               ).astype(np.uint8)
        return out

    def background_rgba(self) -> np.ndarray:
        """sh_ArgonBarPathBackground.fs: dark→light band + white rim."""
        r = HP_MAIN_RADIUS
        D = self.sub_distance(0.0, 1.0)
        rel = np.clip(D / r, 0.0, 1.5) / 1.5
        col = np.empty((self.gh, self.gw, 4))
        col[..., 0] = rel
        col[..., 1] = rel
        col[..., 2] = rel
        col[..., 3] = 0.2 + (0.8 - 0.2) * rel
        rim = np.clip(D - (r - 2.0), 0.0, 1.0)
        for c in range(3):
            col[..., c] = col[..., c] * (1 - rim) + 1.0 * rim
        col[..., 3] = col[..., 3] * (1 - rim) + 1.0 * rim
        fade = np.clip(D - (r - 1.0), 0.0, 1.0)
        col[..., 3] *= (1.0 - fade)
        out = np.empty((self.gh, self.gw, 4), dtype=np.uint8)
        out[..., :3] = np.round(col[..., :3] * 255.0).astype(np.uint8)
        out[..., 3] = np.round(col[..., 3] * 255.0).astype(np.uint8)
        return out


# --- the HUD ------------------------------------------------------------------------

class StdHud:
    """Draws the HUD over the scene each frame (called by StdScene after
    the cursor — §5.3 draw order ends `cursors → HUD`). Component set per
    the module docstring: PER ELEMENT, the skin's legacy component where
    the skin ships it, Argon otherwise (legacy_defaults → all legacy)."""

    def __init__(self, sprites, bank, settings, judgments, frames, beatmap,
                 skin_elems=None, health=None, mods: int = 0,
                 pp_timeline=None, aim_points=None, strain=None):
        self.spr = sprites
        self.bank = bank
        self.s = settings
        self.sk = skin_elems
        self.health = health
        # -- component selection (module docstring, owner decision
        # 2026-07-08 — the HYBRID rule): PER ELEMENT, the skin's legacy
        # component where the skin SHIPS that element's textures, ARGON
        # for whatever the skin lacks; skinless → all Argon.
        # legacy_defaults forces every element legacy (lg_* bakes fill
        # everything, even skinless). renderer_default_font_and_ranks
        # forces the renderer's default numbers/ranks (Argon +
        # procedural rank text) and WINS over legacy_defaults —
        # fonts+ranks only, hp/keys keep their league.
        sk = skin_elems
        self.force_default = bool(getattr(
            settings, "renderer_default_font_and_ranks", False))
        self.legacy_defaults = bool(getattr(
            settings, "legacy_defaults", False))
        fonts_ok = not self.force_default

        def ships(*names: str) -> bool:
            return sk is not None and any(sk.has(n) for n in names)

        if self.legacy_defaults:
            self.legacy_score = fonts_ok
            self.legacy_combo = fonts_ok
            self.legacy_health = True
            self.legacy_keys = True
        else:
            self.legacy_score = fonts_ok and ships("score_digits")
            self.legacy_combo = fonts_ok and ships("combo_digits")
            self.legacy_health = ships("scorebar-colour", "scorebar-bg")
            self.legacy_keys = ships("inputoverlay-key",
                                     "inputoverlay-background")
        # skinless "Argon league": the hit-error meter becomes Argon's
        # vertical SIDE bars (ArgonHitErrorMeter, left+right, mirrored);
        # any custom skin keeps the bottom-centre house meter unchanged.
        self.argon_league = sk is None and not self.legacy_defaults
        # -- settings-surface data (mod pills / pp / aim / strain) --------
        self.mods = int(mods)
        self._mod_acrs = (mods_to_acronyms(self.mods)
                          if getattr(settings, "show_mods", True) else [])
        self.pp_pts = pp_timeline            # [(t, pp)] | None (hidden)
        self._pp_times = ([p[0] for p in pp_timeline]
                          if pp_timeline else [])
        self.aim_pts = aim_points or []      # [(t, dx, dy)] radius units
        self._aim_times = [p[0] for p in self.aim_pts]
        self.strain = strain                 # pp.StrainSeries | None
        self.k = sprites.height / UI_HEIGHT       # screen px per UI px
        self.lk = self.k * KL                     # screen px per LAZER px
        self.ui_w = sprites.width / self.k
        self.ui_w_l = sprites.width / self.lk
        self.es = float(getattr(settings, "hud_scale", 1.0))
        self.op = float(getattr(settings, "hud_opacity", 1.0))
        self.data = HudData(judgments)
        self.hw = OsuHitWindows(beatmap.diff.od)
        starts = [o.get_start_time() for o in beatmap.hit_objects]
        ends = [o.get_end_time() for o in beatmap.hit_objects]
        self.first_t = min(starts) if starts else 0.0
        self.last_t = max(ends) if ends else 1.0
        # m-1: key counters ignore warm-up taps before the first object.
        self.keys = KeySeries(frames, gameplay_start=self.first_t)
        # m-8: grade badge + hit-error strip stay hidden until the first
        # judgment (danser shows neither from frame 0).
        self._first_ev_t = (self.data.ev_times[0] if self.data.ev_times
                            else float("inf"))
        # M-3c: the scorebar hides across [Events] breaks (stable behaviour).
        self._breaks = [(float(p.start_time), float(p.end_time))
                        for p in beatmap.pauses]
        self._pin = 1.0
        self._graph = self._density_buckets(starts, ends)
        self._hp_field: ArgonBarField | None = None
        if (not self.legacy_health and self.health is not None
                and getattr(settings, "show_hp_bar", True)):
            self._hp_field = ArgonBarField(
                HP_WIDTH, HP_BAR_HEIGHT + 2 * HP_MAIN_RADIUS,
                self.lk * self.es,
                HP_GLOW_RADIUS - HP_MAIN_RADIUS)
            self.spr.upload_texture("hud_hp_bg",
                                    self._hp_field.background_rgba())

    # -- endpoint pin (the mania pattern) ------------------------------------------
    def pin_final_score(self, final_total: int) -> None:
        """Scale every displayed score so the last judgment lands exactly
        on the authoritative final total — the .osr's recorded score (the
        CLI wires meta.score straight in). Display-only."""
        last = self.data.final_score()
        if last > 0 and final_total > 0:
            self._pin = final_total / last

    def ev_final_score(self) -> int:
        return self.data.final_score()

    def final_values(self) -> dict:
        """The end-state numbers (for the CLI's final-values check line).
        Score/acc come from the LAST EVENT (not a timestamp sample — the
        final-sample fix), so they match the replay's derived values."""
        d = self.data
        end = max(self.last_t, d.ev_times[-1] if d.ev_times else 0.0) + 1.0
        ur, _avg, _n = d.ur_at(end)
        return {
            "score": int(round(d.final_score() * self._pin)),
            "acc": d.final_acc(),
            "combo": d.combo_at(end)[0],
            "max_combo": d.max_combo,
            "ur": ur,
            "grade": d.grade_at(end),
            "hp": self.health.final_hp if self.health is not None else None,
        }

    # -- generic text helpers ---------------------------------------------------------

    def _run(self, out: list[Sprite], text: str, x_left: float, y_top: float,
             h: float, color, alpha: float, mono: bool = False) -> float:
        """Procedural-glyph run in UI px; returns the run width (UI px)."""
        mono_adv = self.bank.glyph_mono_advance if mono else None
        entries, total = layout_run(text, self.bank.glyph_aspect, h,
                                    mono_advance=mono_adv)
        k = self.k
        for ch, cx, w in entries:
            out.append(Sprite((x_left + cx) * k, (y_top + h / 2.0) * k,
                              w * k, h * k, f"glyph_{ch}", (*color, alpha)))
        return total

    def _run_width(self, text: str, h: float, mono: bool = False) -> float:
        mono_adv = self.bank.glyph_mono_advance if mono else None
        _, total = layout_run(text, self.bank.glyph_aspect, h,
                              mono_advance=mono_adv)
        return total

    def _lrun(self, out, text, x_l, y_l, h_l, color, alpha,
              mono: bool = False) -> float:
        """Procedural-glyph run in LAZER px; returns width in lazer px."""
        w = self._run(out, text, x_l * KL, y_l * KL, h_l * KL, color, alpha,
                      mono=mono)
        return w / KL

    def _lrun_width(self, text: str, h_l: float, mono: bool = False) -> float:
        return self._run_width(text, h_l * KL, mono=mono) / KL

    # -- legacy font layout (LegacySpriteText semantics) --------------------------------

    def _legacy_glyphs(self, font: str, text: str):
        """[(texture_key, w_l, h_l)] per char at LEGACY-px sizes +
        (advance policy): skin ScorePrefix/ComboPrefix/scoreentry set when
        loaded; classic lg_* bakes for anything the skin doesn't provide
        (per char — stable's per-file default-skin fallback). FixedWidth:
        digits advance by the '5' cell (FixedWidthReferenceCharacter),
        ',','.','%','x' excluded. Spacing = −FontOverlap of the ACTIVE
        skin font (0 for the baked classic set)."""
        sk = self.sk
        digit_sizes = extra_sizes = {}
        overlap = 0.0
        if sk is not None:
            if font == "score" and sk.has("score_digits"):
                digit_sizes = sk.score_digit_sizes
                extra_sizes = sk.score_extra_sizes
                overlap = sk.info.score_overlap
            elif font == "combo" and sk.has("combo_digits"):
                digit_sizes = sk.combo_digit_sizes
                extra_sizes = sk.combo_extra_sizes
                overlap = sk.info.combo_overlap
            elif font == "scoreentry" and sk.has("scoreentry_digits"):
                digit_sizes = sk.scoreentry_digit_sizes
                extra_sizes = {}
        skinned = bool(digit_sizes)
        ref_h = (digit_sizes["5"][1] if skinned else LEGACY_FONT_H)
        fixed_w = (digit_sizes["5"][0] if skinned
                   else self.bank.legacy_aspect["5"] * LEGACY_FONT_H)
        suffix = {".": "dot", ",": "comma", "%": "percent", "x": "x"}
        glyphs = []
        for ch in text:
            if ch.isdigit() and skinned:
                w, h = digit_sizes[ch]
                glyphs.append((f"sk_{font}_{ch}", w, h, True))
            elif ch in extra_sizes:
                w, h = extra_sizes[ch]
                glyphs.append((f"sk_{font}_{suffix[ch]}", w, h, False))
            else:
                # classic-look fallback, scaled to the active digit height
                asp = self.bank.legacy_aspect.get(ch, 0.6)
                h = ref_h
                glyphs.append((f"lg_{suffix.get(ch, ch)}", asp * h, h,
                               ch.isdigit()))
        return glyphs, fixed_w, ref_h, overlap

    def _legacy_text(self, out: list[Sprite], font: str, text: str,
                     x_l: float, y_top_l: float, scale: float, alpha: float,
                     color=(1.0, 1.0, 1.0), align: str = "right",
                     extra_scale: float = 1.0,
                     pivot: tuple[float, float] | None = None,
                     additive: bool = False) -> tuple[float, float]:
        """One LegacySpriteText run. x_l = right edge (align='right') or
        left edge; y_top_l = top. Sizes in lazer px × scale. Returns
        (width_l, height_l) of the laid run (pre extra_scale). extra_scale
        scales around `pivot` (lazer px, absolute) — the combo pops."""
        glyphs, fixed_w, ref_h, overlap = self._legacy_glyphs(font, text)
        adv = []
        for key, w, h, is_digit in glyphs:
            adv.append(fixed_w if is_digit else w)
        total_w = sum(adv) - overlap * max(len(glyphs) - 1, 0)
        x0 = (x_l - total_w * scale) if align == "right" else x_l
        px_l, py_l = pivot if pivot is not None else (x0, y_top_l)
        lk = self.lk
        x = x0
        for (key, w, h, is_digit), a in zip(glyphs, adv):
            cx = x + a * scale / 2.0
            cy = y_top_l + ref_h * scale / 2.0
            # apply extra_scale around the pivot
            cx = px_l + (cx - px_l) * extra_scale
            cy = py_l + (cy - py_l) * extra_scale
            out.append(Sprite(cx * lk, cy * lk,
                              w * scale * extra_scale * lk,
                              h * scale * extra_scale * lk,
                              key, (*color, alpha), additive=additive))
            x += (a - overlap) * scale
        return total_w * scale, ref_h * scale

    def _legacy_text_size(self, font: str, text: str,
                          scale: float) -> tuple[float, float]:
        glyphs, fixed_w, ref_h, overlap = self._legacy_glyphs(font, text)
        adv = [fixed_w if is_digit else w for _, w, _, is_digit in glyphs]
        total_w = sum(adv) - overlap * max(len(glyphs) - 1, 0)
        return total_w * scale, ref_h * scale

    # -- frame draw --------------------------------------------------------------------

    def draw(self, t: float) -> None:
        if self.op <= 0.0:
            return
        out: list[Sprite] = []
        # strain graph first — UNDER every other HUD element
        self._strain_graph(out, t)
        # per-element skin-else-Argon (module docstring hybrid rule;
        # legacy_defaults flips every flag legacy)
        if self.legacy_score:
            self._legacy_score_block(out, t)   # includes the legacy pie
        else:
            self._argon_score_block(out, t)
            self._argon_accuracy(out, t)
            self._argon_progress(out, t)
        if self.legacy_combo:
            self._legacy_combo(out, t)
        else:
            self._argon_combo(out, t)
        if not self.legacy_health:
            self._argon_health(out, t)
        if self.legacy_keys:
            self._legacy_key_overlay(out, t)
        else:
            self._argon_key_overlay(out, t)
        self._hit_error(out, t)
        # settings-surface house elements (both component paths)
        self._mod_pills(out, t)
        self._hit_counter(out, t)
        self._pp_counter(out, t)
        self._aim_error(out, t)
        self._watermark(out)
        self._break_flash(out, t)
        if self.legacy_health:
            # stable draws the scorebar in FRONT of everything (LegacySkin's
            # "hacky full screen area health bars" comment) — last.
            self._legacy_health(out, t)
        if out:
            self.spr.draw(out)

    # ==================== ARGON components (skinless default) =======================

    ARGON_SEG_LIT = {".": "aseg_dot", "%": "aseg_pct", "x": "aseg_x"}

    def _argon_seg_width(self, n: int, h_l: float) -> float:
        """Width (lazer px) of an n-cell Argon 7-segment run at height h_l."""
        return self.bank.argon_seg_advance * h_l * n

    def _argon_seg_run(self, out, text: str, right_x_l: float,
                       top_y_l: float, h_l: float, alpha: float,
                       color=(1, 1, 1), wire_n: int | None = None,
                       scale: float = 1.0, pivot=None) -> float:
        """Right-aligned Argon counter run drawn as procedural 7-segment
        bars (ArgonCounterTextComponent). The unlit "wireframes" backing
        (all-segments '8' / dot, WireframeOpacity 0.25) and the lit glyph
        come from the SAME segment geometry on the SAME fixed-width cell,
        so lit + ghost register by construction (the phantom-8 fix). Digits
        / x / % are fixed-width; '.' shares the cell (a centred dot).
        wire_n adds leading unlit cells (RequiredDisplayDigits). scale/pivot
        drive the combo/score pop. Returns the full run width (lazer px)."""
        cw = self.bank.argon_seg_advance * h_l
        n_lit = len(text)
        n_wire = max(wire_n if wire_n is not None else n_lit, n_lit)
        cy = top_y_l + h_l / 2.0
        if pivot is None:
            pivot = (right_x_l - n_wire * cw, cy)
        wire_a = ARGON_WIRE_ALPHA * self.op

        def place(cx_l: float, key: str, col, a: float) -> None:
            gx = pivot[0] + (cx_l - pivot[0]) * scale
            gy = pivot[1] + (cy - pivot[1]) * scale
            out.append(Sprite(gx * self.lk, gy * self.lk,
                              cw * scale * self.lk, h_l * scale * self.lk,
                              key, (*col, a)))

        # unlit wireframe cells (right-aligned, n_wire of them)
        for i in range(n_wire):
            cx = right_x_l - (n_wire - i) * cw + cw / 2.0
            lit_idx = i - (n_wire - n_lit)
            ch = text[lit_idx] if 0 <= lit_idx < n_lit else None
            wkey = "argon_wireframe_dot" if ch == "." else "argon_wireframe"
            place(cx, wkey, color, wire_a)
        # lit glyphs (right-aligned over the rightmost cells)
        for j, ch in enumerate(text):
            cx = right_x_l - (n_lit - j) * cw + cw / 2.0
            key = self.ARGON_SEG_LIT.get(ch, f"aseg_{ch}")
            place(cx, key, color, alpha)
        return n_wire * cw

    def _argon_score_block(self, out, t: float) -> None:
        """ArgonScoreCounter + the two ArgonWedgePiece backdrops
        (ArgonSkin.cs default layout: wedges 380×72 at (-50,15)/(-46,20),
        score right edge x=250, y=50, ShowLabel false, 6 wireframe
        digits)."""
        s = self.s
        if not s.show_score:
            return
        es, lk = self.es, self.lk
        # wedges (canvas includes the shear overhang 0.8·H to the left)
        from .textures import WEDGE_H, WEDGE_SHEAR, WEDGE_W
        cw = (WEDGE_W + WEDGE_SHEAR * WEDGE_H) * es
        ch = WEDGE_H * es
        for px, py in ((-50.0, 15.0), (-46.0, 20.0)):
            cx = (px - WEDGE_SHEAR * WEDGE_H) * es + cw / 2.0
            cy = py * es + ch / 2.0
            out.append(Sprite(cx * lk, cy * lk, cw * lk, ch * lk,
                              "argon_wedge", (1, 1, 1, self.op)))
        score = int(round(self.data.score_at(t) * self._pin))
        text = str(max(score, 0))
        self._argon_seg_run(
            out, text, ARGON_SCORE_RIGHT_X * es, ARGON_SCORE_TOP_Y * es,
            ARGON_DIGIT_H * es, self.op,
            wire_n=max(ARGON_SCORE_DIGITS, len(text)))

    def _argon_accuracy(self, out, t: float) -> None:
        """ArgonAccuracyCounter: TopRight (-20, 20) — whole part + '.dd'
        at ×0.5 + '%', 'ACCURACY' label in Blue0, wireframes ###/.##."""
        s = self.s
        if not s.show_score:
            return
        es = self.es
        acc = self.data.acc_at(t)
        disp = acc_display_value(acc)
        whole = int(disp)
        frac = int(round((disp - whole) * 100))
        whole_txt, frac_txt = str(whole), f".{frac:02d}"
        h = ARGON_DIGIT_H * es
        hh = h * 0.5                       # fractionPart Scale = 0.5
        hpct = hh * 1.2
        right = self.ui_w_l + ARGON_ACC_POS[0] * es
        top = ARGON_ACC_POS[1] * es
        num_top = top + ARGON_LABEL_GAP * es
        # right-to-left: '%', the '.dd' fraction (×0.5), then the whole part
        w_pct = self._argon_seg_width(1, hpct)
        self._argon_seg_run(out, "%", right, num_top + (h - hpct), hpct,
                            0.95 * self.op)
        frac_right = right - w_pct - 2.0 * es
        w_frac = self._argon_seg_width(len(frac_txt), hh)
        self._argon_seg_run(out, frac_txt, frac_right, num_top + (h - hh), hh,
                            0.95 * self.op)
        whole_right = frac_right - w_frac - 1.0 * es
        w_whole = self._argon_seg_run(out, whole_txt, whole_right, num_top, h,
                                      0.95 * self.op, wire_n=3)
        # label above the whole part, left-aligned (Torus-12 stand-in)
        label_left = whole_right - w_whole
        self._lrun(out, "ACCURACY", label_left, top, ARGON_LABEL_H * es,
                   BLUE0, 0.95 * self.op)
        if s.show_grade:
            self._grade_badge(out, t, label_left - 14.0 * es,
                              num_top + h / 2.0)

    def _argon_combo(self, out, t: float) -> None:
        """ArgonComboCounter: BottomLeft (36, -66) ×1.3 — '<n>x' with the
        'COMBO' label, pop ×1.1/×0.8 clamp [0.6,1.4] eased OutQuint,
        FlashColour red over 2 s on a combo-breaking miss."""
        if not self.s.show_combo:
            return
        es = self.es
        sc = ARGON_COMBO_SCALE * es
        d = self.data
        displayed = d.combo_displayed_at(t)
        text = f"{displayed}x"
        h = ARGON_DIGIT_H * sc
        label_h = ARGON_LABEL_H * sc
        block_h = (ARGON_LABEL_GAP + ARGON_DIGIT_H) * sc
        x = ARGON_COMBO_POS[0] * es
        bottom = LAZER_UI_HEIGHT + ARGON_COMBO_POS[1] * es
        top = bottom - block_h
        num_top = top + ARGON_LABEL_GAP * sc
        # red flash on miss (FlashColour(Color4.Red, 2000, OutQuint))
        color = (1.0, 1.0, 1.0)
        lo = bisect.bisect_right([c[0] for c in d.combo_changes], t) - 1
        if lo >= 0:
            ct, old, new = d.combo_changes[lo]
            if old > 1 and new == 0:
                p = 1.0 - ease_out_quint((t - ct) / ARGON_MISS_FLASH_MS)
                color = (1.0, 1.0 - 0.75 * p, 1.0 - 0.75 * p)
        scale = argon_combo_scale_at(d.combo_changes, t)
        # NumberContainer scales from its TopLeft (component anchor)
        pivot = (x, num_top)
        cw = self.bank.argon_seg_advance * h
        right_x = x + len(text) * cw       # left edge lands at x
        self._argon_seg_run(out, text, right_x, num_top, h, 0.95 * self.op,
                            color=color, scale=scale, pivot=pivot)
        self._lrun(out, "COMBO", x, top, label_h, BLUE0, 0.95 * self.op)

    def _argon_health(self, out, t: float) -> None:
        """ArgonHealthDisplay: bar values damped 50 ms; on a health-losing
        judgment the glow segment holds the pre-miss value 500 ms in red
        then eases back over 300 ms OutQuint; hit flashes lighten the end
        glow; plus the BoxElement health line at (0,30)."""
        if not getattr(self.s, "show_hp_bar", True):
            return
        hp = self.health
        field = self._hp_field
        if hp is None or field is None:
            return
        es, lk = self.es, self.lk
        hp_now = damped(hp.hp_at, t, HP_DAMP_HALF_LIFE)
        glow_val = hp_now
        red = False
        miss = hp.last_miss_at(t)
        if miss is not None:
            age = t - miss[0]
            if age < HP_MISS_HOLD_MS + HP_MISS_EASE_MS:
                target = max(miss[1], hp_now)
                if age < HP_MISS_HOLD_MS:
                    glow_val, red = target, True
                else:
                    p = ease_out_quint((age - HP_MISS_HOLD_MS)
                                       / HP_MISS_EASE_MS)
                    glow_val = target + (hp_now - target) * p
                    red = True
                if hp.hp_at(t) >= glow_val - 1e-6:
                    red = False           # finishMissDisplay
                    glow_val = hp_now
        alpha_main = 1.0 if hp_now > 1e-3 else 0.0
        # glow colour flash on hits (glowBar white 30 ms → back 300 ms)
        glow_rgba = HP_GLOW_COLOUR
        gain = hp.last_gain_at(t)
        if not red and gain is not None:
            age = t - gain
            if 0.0 <= age < 330.0:
                wmix = 1.0 - ease_out_quint(max(age - 30.0, 0.0) / 300.0)
                glow_rgba = (
                    glow_rgba[0] + (1 - glow_rgba[0]) * wmix,
                    glow_rgba[1] + (1 - glow_rgba[1]) * wmix,
                    glow_rgba[2] + (1 - glow_rgba[2]) * wmix,
                    glow_rgba[3] + (1 - glow_rgba[3]) * wmix)
        bar_rgb = (1.0, 1.0, 1.0)
        seg_lo, seg_hi = min(hp_now, glow_val), max(glow_val, hp_now)
        if red:
            gbar_rgb, ggl = HP_MISS_BAR, (*HP_MISS_GLOW, 0.5)
        else:
            gbar_rgb, ggl = (1.0, 1.0, 1.0), glow_rgba
        glow_tex = field.bar_rgba(
            seg_lo, seg_hi, HP_GLOW_RADIUS,
            (HP_GLOW_RADIUS - HP_MAIN_RADIUS
             * (1.0 - HP_MAIN_GLOW_PORTION)) / HP_GLOW_RADIUS,
            gbar_rgb, ggl, xgrad=True, alpha_mult=0.9)
        main_tex = field.bar_rgba(0.0, hp_now, HP_MAIN_RADIUS,
                                  HP_MAIN_GLOW_PORTION, bar_rgb,
                                  glow_rgba, alpha_mult=alpha_main)
        self.spr.upload_texture("hud_hp_glow", glow_tex)
        self.spr.upload_texture("hud_hp_main", main_tex)
        # content top-left at HP_POS minus the main radius padding row
        x0 = (HP_POS[0] - field.margin) * es
        y0 = (HP_POS[1] - field.margin) * es
        w = field.gw
        h = field.gh
        cx = x0 * lk + w / 2.0
        cy = y0 * lk + h / 2.0
        out.append(Sprite(cx, cy, float(w), float(h), "hud_hp_bg",
                          (1, 1, 1, self.op)))
        out.append(Sprite(cx, cy, float(w), float(h), "hud_hp_glow",
                          (1, 1, 1, self.op), additive=True))
        out.append(Sprite(cx, cy, float(w), float(h), "hud_hp_main",
                          (1, 1, 1, self.op), additive=True))
        # BoxElement health line (CornerRadius .5): (0, 30), 45×3
        lw, lh = HP_LINE_SIZE
        out.append(Sprite((lw / 2.0) * es * lk, HP_LINE_Y * es * lk,
                          lw * es * lk, lh * es * lk, "pill",
                          (1, 1, 1, self.op)))

    def _argon_key_overlay(self, out, t: float) -> None:
        """ArgonKeyCounterDisplay: horizontal row, BottomRight above the
        song progress; per counter the indicator pill drops 4 px on press
        (60 ms OutQuint) and returns over 250 ms OutQuart, the key name
        flashes white while held (back to Blue0 over 200 ms OutQuart),
        the running count sits below."""
        if not self.s.show_key_overlay:
            return
        es, lk = self.es, self.lk
        used = self.keys.used
        channels = [0, 1] + [ch for ch in (2, 3) if used[ch]]
        if not (used[0] or used[1]) and (used[2] or used[3]):
            channels = [ch for ch in (2, 3) if used[ch]]
        n = len(channels)
        w_cell, h_cell = ARGON_KEY_W * es, ARGON_KEY_H * es
        total_w = n * w_cell + (n - 1) * ARGON_KEY_SPACING * es
        right = self.ui_w_l + ARGON_KEYS_POS[0] * es
        bottom = LAZER_UI_HEIGHT + ARGON_KEYS_POS[1] * es
        x0 = right - total_w
        top = bottom - h_cell
        held, counts = self.keys.state_at(t)
        for idx, ch in enumerate(channels):
            cx0 = x0 + idx * (w_cell + ARGON_KEY_SPACING * es)
            pressed = bool(held & (1 << ch))
            press_t = self.keys.last_press_at(ch, t)
            release_t = self.keys.last_release_at(ch, t)
            # indicator pill: y offset + alpha
            if pressed and press_t is not None:
                age = t - press_t
                dy = ARGON_KEY_PRESS_OFFSET * ease_out_quint(age / 60.0)
                ind_alpha = 0.5 + 0.5 * _clamp01(age / 10.0)
                name_white = _clamp01(age / 10.0)
            else:
                age = t - release_t if release_t is not None else math.inf
                p = ease_out_quart(age / 250.0)
                dy = ARGON_KEY_PRESS_OFFSET * (1.0 - p)
                ind_alpha = 1.0 - 0.5 * p
                name_white = 1.0 - ease_out_quart(
                    age / 200.0) if release_t is not None else 0.0
            ind_h = ARGON_KEY_LINE_H * es
            out.append(Sprite((cx0 + w_cell / 2.0) * lk,
                              (top + dy * es + ind_h / 2.0) * lk,
                              w_cell * lk, ind_h * lk, "pill",
                              (1, 1, 1, ind_alpha * self.op)))
            name_col = tuple(BLUE0[i] + (1.0 - BLUE0[i]) * name_white
                             for i in range(3))
            pad_top = ind_h + ARGON_KEY_PRESS_OFFSET * es
            self._lrun(out, KEY_LABELS[ch], cx0 + 3.0 * es,
                       top + pad_top + 2.0 * es,
                       ARGON_KEY_NAME_H * es, name_col, 0.95 * self.op)
            count = counts[ch]
            self._lrun(out, f"{count:,}", cx0 + 3.0 * es,
                       bottom - ARGON_KEY_COUNT_H * es - 1.0 * es,
                       ARGON_KEY_COUNT_H * es, (1, 1, 1), 0.95 * self.op,
                       mono=True)

    def _density_buckets(self, starts, ends) -> list[int]:
        """ArgonSongProgressGraph.Objects: object density over
        display_granularity buckets (each object spans start..end)."""
        if not starts:
            return []
        first, last = min(starts), max(ends)
        interval = (last - first + 1) / GRAPH_BUCKETS
        vals = [0] * GRAPH_BUCKETS
        for s, e in zip(starts, ends):
            i0 = int((s - first) / interval)
            i1 = int((e - first) / interval)
            for i in range(max(i0, 0), min(i1, GRAPH_BUCKETS - 1) + 1):
                vals[i] += 1
        return vals

    def _argon_progress(self, out, t: float) -> None:
        """ArgonSongProgress: 90 %-width bottom strip — SongProgressInfo
        row (elapsed / remaining), the faint tiered density graph and the
        10 px rounded bar (bg gray(0.2) α.3, fill gray(0.9))."""
        if not getattr(self.s, "show_progress", True):
            return
        es, lk = self.es, self.lk
        bar_h = ARGON_PROGRESS_BAR_H * es
        width = self.ui_w_l * ARGON_PROGRESS_WIDTH
        x0 = (self.ui_w_l - width) / 2.0
        bottom = LAZER_UI_HEIGHT - ARGON_PROGRESS_BOTTOM * es
        frac = ((t - self.first_t)
                / max(self.last_t - self.first_t, 1.0))
        frac = _clamp01(frac) if t >= self.first_t else 0.0
        # density graph (in the bar strip, under the fills; approximation
        # of SegmentedGraph's tier stack: column alpha ∝ filled tiers)
        if self._graph:
            vmax = max(self._graph) or 1
            bw = width / len(self._graph)
            gh_max = bar_h
            for i, v in enumerate(self._graph):
                if v <= 0:
                    continue
                tiers = max(1, round(v / vmax * GRAPH_TIERS))
                gh = gh_max * tiers / GRAPH_TIERS
                out.append(Sprite((x0 + (i + 0.5) * bw) * lk,
                                  (bottom - gh / 2.0) * lk,
                                  bw * lk * 0.9, gh * lk, None,
                                  (0.2, 0.2, 0.2,
                                   0.45 * self.op), additive=True))
        # bar background + fill (RoundedBar pills)
        out.append(Sprite((x0 + width / 2.0) * lk,
                          (bottom - bar_h / 2.0) * lk,
                          width * lk, bar_h * lk, "pill",
                          (0.2, 0.2, 0.2, 0.3 * self.op)))
        if frac > 0.003:
            fw = width * frac
            out.append(Sprite((x0 + fw / 2.0) * lk,
                              (bottom - bar_h / 2.0) * lk,
                              fw * lk, bar_h * lk, "pill",
                              (0.9, 0.9, 0.9, 0.95 * self.op)))
        # info row: elapsed (left) / remaining (right), Torus-ish 14
        info_h = ARGON_INFO_H * es
        info_y = bottom - bar_h * 2.0 - info_h - 2.0 * es
        cur = _fmt_time((t - self.first_t) / 1000.0)
        left = _fmt_time((self.last_t - t) / 1000.0)
        self._lrun(out, cur, x0, info_y, info_h, (1, 1, 1),
                   0.9 * self.op, mono=True)
        wl = self._lrun_width(left, info_h, mono=True)
        self._lrun(out, left, x0 + width - wl, info_y, info_h,
                   (1, 1, 1), 0.9 * self.op, mono=True)

    # ==================== LEGACY components (custom-skin path) ======================

    def _legacy_score_block(self, out, t: float) -> None:
        """LegacyScoreCounter (TopRight ×0.96, margin 10, 6 leading
        digits, 1 s Easing.Out roll) + LegacyAccuracyCounter (×0.576,
        margins 9/17, '0.00%') + LegacySongProgress (the pie left of the
        accuracy) + the grade badge."""
        s = self.s
        if not s.show_score:
            return
        es = self.es
        score = int(round(self.data.score_at(t, LEGACY_SCORE_ROLL_MS)
                          * self._pin))
        text = f"{max(score, 0):06d}"
        right = self.ui_w_l - LEGACY_SCORE_MARGIN_X * es
        sw, sh = self._legacy_text(
            out, "score", text, right, 0.0, LEGACY_SCORE_SCALE * es,
            self.op)
        acc = self.data.acc_at(t, LEGACY_ACC_ROLL_MS)
        disp = acc_display_value(acc)
        acc_text = f"{disp:.2f}%"
        acc_right = self.ui_w_l - LEGACY_ACC_MARGIN[0] * es
        acc_top = sh + LEGACY_ACC_MARGIN[1] * es
        aw, ah = self._legacy_text(
            out, "score", acc_text, acc_right, acc_top,
            LEGACY_ACC_SCALE * es, 0.95 * self.op)
        # LegacySongProgress pie: right edge at (acc left − 18)
        if getattr(s, "show_progress", True):
            pie_d = LEGACY_PIE_SIZE * es
            pie_cx = acc_right - aw - LEGACY_PIE_GAP * es - pie_d / 2.0
            pie_cy = acc_top + ah / 2.0
            self._legacy_pie(out, t, pie_cx, pie_cy, pie_d)
            badge_right = pie_cx - pie_d / 2.0 - 12.0 * es
        else:
            badge_right = acc_right - aw - 12.0 * es
        if s.show_grade:
            self._grade_badge(out, t, badge_right, acc_top + ah / 2.0)

    def _legacy_pie(self, out, t: float, cx_l: float, cy_l: float,
                    d_l: float) -> None:
        """LegacySongProgress: white 60 % pie (intro: (199,255,47) counting
        DOWN, mirrored), 33 px circle border + centre dot."""
        lk = self.lk
        intro = t < self.first_t
        if intro:
            frac = _clamp01(1.0 - (max(t, 0.0) / max(self.first_t, 1.0)))
            color, flip = LEGACY_PIE_INTRO_COLOR, True
        else:
            frac = _clamp01((t - self.first_t)
                            / max(self.last_t - self.first_t, 1.0))
            color, flip = (1.0, 1.0, 1.0), False
        pie_d = d_l * 0.92
        from .textures import PIE_STEPS
        idx = min(int(frac * PIE_STEPS), PIE_STEPS - 1)
        key = "disc" if frac >= 1.0 else (f"pie_{idx:02d}" if idx > 0
                                          else None)
        if key is not None:
            out.append(Sprite(cx_l * lk, cy_l * lk, pie_d * lk, pie_d * lk,
                              key, (*color, LEGACY_PIE_ALPHA * self.op),
                              uv_off=(1.0, 0.0) if flip else (0.0, 0.0),
                              uv_scale=(-1.0, 1.0) if flip else (1.0, 1.0)))
        out.append(Sprite(cx_l * lk, cy_l * lk, d_l * lk, d_l * lk,
                          "pie_ring", (1, 1, 1, 0.9 * self.op)))
        dot = 4.0 * self.es
        out.append(Sprite(cx_l * lk, cy_l * lk, dot * lk, dot * lk,
                          "dot", (1, 1, 1, 0.9 * self.op)))

    def _legacy_combo(self, out, t: float) -> None:
        """LegacyDefaultComboCounter: bottom-left margin 10 ×1.28, the
        displayed/actual two-counter (HudData.legacy_combo_*) — additive
        ghost ×1.56→1 α.6→0 over 300 ms on increments, small 1→1.1→1 pop
        on the delayed commit, 20 ms/step roll-down on breaks; hidden
        while the displayed count is 0."""
        if not self.s.show_combo:
            return
        d = self.data
        es = self.es
        sc = LEGACY_COMBO_SCALE * es
        displayed = d.legacy_combo_displayed_at(t)
        x = LEGACY_COMBO_MARGIN * es
        bottom = LAZER_UI_HEIGHT - LEGACY_COMBO_MARGIN * es
        _, ref_h = self._legacy_text_size("combo", "0x", sc)
        top = bottom - ref_h
        # pop pivot: 62.5 % of the font height (updateLayout's
        # font_height_ratio anchor)
        pivot = (x, top + LEGACY_COMBO_HEIGHT_RATIO * ref_h)
        ghost = d.legacy_combo_ghost_at(t)
        alpha = 0.0
        if displayed > 0:
            alpha = 1.0
        elif ghost is not None:
            alpha = 1.0
        else:
            # fade out over 100 ms after the roll-down reaches 0
            i = bisect.bisect_right(d._lg_disp_times, t) - 1
            if i >= 0 and d.lg_disp_events[i][1] == 0 and i > 0:
                age = t - d.lg_disp_events[i][0]
                alpha = max(0.0, 1.0 - age / LEGACY_COMBO_FADE_MS)
        if alpha > 0.0:
            pop = d.legacy_combo_pop_at(t)
            self._legacy_text(out, "combo", f"{displayed}x", x, top, sc,
                              0.95 * alpha * self.op, align="left",
                              extra_scale=pop, pivot=pivot)
        if ghost is not None:
            gv, age = ghost
            p = age / LEGACY_COMBO_BIG_POP_MS
            gscale = 1.56 - 0.56 * p
            galpha = 0.6 * (1.0 - p)
            self._legacy_text(out, "combo", f"{gv}x", x, top, sc,
                              galpha * self.op, align="left",
                              extra_scale=gscale,
                              pivot=(pivot[0] + 3.0 * es, pivot[1]),
                              additive=True)

    def _scorebar_break_alpha(self, t: float) -> float:
        """M-3c: the scorebar fades out across [Events] breaks and back in as
        gameplay resumes (stable hides it during breaks). 1.0 outside breaks,
        0.0 in the middle, with a short ramp at each edge. Breaks shorter than
        SCOREBAR_BREAK_MIN_MS are ignored (nothing to hide for)."""
        fade = 300.0
        min_break = 1500.0
        for bs, be in self._breaks:
            if be - bs < min_break or not (bs <= t < be):
                continue
            hidden = min(_clamp01((t - bs) / fade), _clamp01((be - t) / fade))
            return 1.0 - hidden
        return 1.0

    def _legacy_health(self, out, t: float) -> None:
        """LegacyHealthDisplay: scorebar-bg at (0,0) native size, the
        colour fill cropped to hp (fill width eased ~OutQuint 200 ms →
        kernel damp), scorebar-colour-0.. animation frames, new-style
        (marker) vs old-style (ki/kidanger/kidanger2) pieces, fill/marker
        tint white→black→red under the 50 %/20 % cutoffs, marker bulge on
        gains + additive flash. All sizes = native logical px in the
        768-space (legacy sprite convention); missing pieces come from the
        classic lg_* bakes."""
        if not getattr(self.s, "show_hp_bar", True) or self.health is None:
            return
        op = self.op * self._scorebar_break_alpha(t)   # M-3c break-hide
        if op <= 0.0:
            return
        sk = self.sk
        es, lk = self.es, self.lk
        hp_now = _clamp01(damped(self.health.hp_at, t, 40.0))

        def sk_ok(name: str) -> bool:
            return (sk is not None and sk.has(name)
                    and name not in sk.empty)

        # background
        if sk_ok("scorebar-bg"):
            bw, bh = sk.size["scorebar-bg"]
            bg_key = "sk_scorebar-bg"
        else:
            from .textures import LEGACY_BAR_BG_SIZE
            bw, bh = LEGACY_BAR_BG_SIZE
            bg_key = "lg_scorebar_bg"
        out.append(Sprite(bw / 2.0 * es * lk, bh / 2.0 * es * lk,
                          bw * es * lk, bh * es * lk, bg_key,
                          (1, 1, 1, op)))
        new_style = sk_ok("scorebar-marker")
        # fill (cropped to hp fraction, animation frames honored)
        if sk_ok("scorebar-colour"):
            fw, fh = sk.size["scorebar-colour"]
            fill_key = sk.frame_key("scorebar-colour", t)
        else:
            from .textures import LEGACY_BAR_FILL_SIZE
            fw, fh = LEGACY_BAR_FILL_SIZE
            fill_key = "lg_scorebar_colour"
        fx, fy = (LEGACY_FILL_POS_NEW if new_style else LEGACY_FILL_POS_OLD)
        fill_rgb = _legacy_fill_colour(hp_now) if new_style else (1, 1, 1)
        if hp_now > 1e-3:
            vis_w = fw * hp_now
            out.append(Sprite((fx + vis_w / 2.0) * es * lk,
                              (fy + fh / 2.0) * es * lk,
                              vis_w * es * lk, fh * es * lk, fill_key,
                              (*fill_rgb, op),
                              uv_scale=(hp_now, 1.0)))
        # marker / ki pieces at the fill end. M-3b: only when the SKIN SHIPS
        # the piece — a skin that ships the bar but no marker/ki (BTMC) gets
        # NO marker (danser draws none), not the procedural lg_* dot.
        if new_style:
            mw, mh = sk.size["scorebar-marker"]
            m_key = "sk_scorebar-marker"
            m_rgb = _legacy_fill_colour(hp_now)
            m_add = hp_now >= LEGACY_EPIC_CUTOFF
        else:
            # M-3b: only when the skin ships AT LEAST ONE ki piece. A skin
            # with the bar but no marker/ki at all (BTMC) draws NO marker —
            # not the procedural lg_ki dot danser never shows. Skins that ship
            # some ki tiers keep the classic per-tier fallback (default bake
            # for a missing tier, mirroring stable's default-skin fallback).
            if not (sk_ok("scorebar-ki") or sk_ok("scorebar-kidanger")
                    or sk_ok("scorebar-kidanger2")):
                return
            if hp_now < 0.2:
                name, lg = "scorebar-kidanger2", "lg_kidanger2"
            elif hp_now < LEGACY_EPIC_CUTOFF:
                name, lg = "scorebar-kidanger", "lg_kidanger"
            else:
                name, lg = "scorebar-ki", "lg_ki"
            if sk_ok(name):
                mw, mh = sk.size[name]
                m_key = f"sk_{name}"
            else:
                from .textures import LEGACY_KI_SIZE
                mw = mh = LEGACY_KI_SIZE
                m_key = lg
            m_rgb = (1.0, 1.0, 1.0)
            m_add = False
        mx = (fx + fw * hp_now) * es
        my = (fy + (fh / 2.0 if new_style else 0.0)) * es
        gain = self.health.last_gain_at(t)
        m_scale = 0.8
        if gain is not None:
            age = t - gain
            if 0.0 <= age < 150.0:
                m_scale = 1.2 - 0.4 * (age / 150.0)   # Bulge 1.2→0.8
        out.append(Sprite(mx * lk, my * lk, mw * m_scale * es * lk,
                          mh * m_scale * es * lk, m_key,
                          (*m_rgb, op), additive=m_add))
        if gain is not None:
            age = t - gain
            if 0.0 <= age < 120.0:      # marker Flash: explode ghost
                epic = hp_now >= LEGACY_EPIC_CUTOFF
                gsc = m_scale * (1.0 + (1.0 if epic else 0.6)
                                 * ease_out_quad(age / 120.0))
                galpha = (1.0 - age / 120.0) * op
                out.append(Sprite(mx * lk, my * lk, mw * gsc * es * lk,
                                  mh * gsc * es * lk, m_key,
                                  (*m_rgb, galpha), additive=True))

    def _legacy_key_overlay(self, out, t: float) -> None:
        """LegacyKeyCounterDisplay: inputoverlay-background rotated 90° at
        the right edge (stable's placement: column vertically centred),
        46 px cells scaled ×0.75 while held (160 ms Out), key tint
        #ffde00 / #f8009e, InputOverlayText-coloured name → running count
        (scoreentry font) after the first press."""
        if not self.s.show_key_overlay:
            return
        sk = self.sk
        es, lk = self.es, self.lk
        used = self.keys.used
        channels = [0, 1] + [ch for ch in (2, 3) if used[ch]]
        if not (used[0] or used[1]) and (used[2] or used[3]):
            channels = [ch for ch in (2, 3) if used[ch]]
        n = len(channels)
        cell = LEGACY_KEY_CELL * es
        gap = LEGACY_KEY_SPACING * es
        total_h = n * cell + (n - 1) * gap
        cy0 = LAZER_UI_HEIGHT / 2.0 - total_h / 2.0
        right = self.ui_w_l

        def sk_ok(name: str) -> bool:
            return (sk is not None and sk.has(name)
                    and name not in sk.empty)

        # rotated background strip (Scale (1.05, 1), Rotation 90)
        if sk_ok("inputoverlay-background"):
            bw, bh = sk.size["inputoverlay-background"]
            bg_key = "sk_inputoverlay-background"
        else:
            from .textures import LEGACY_IO_BG_SIZE
            bw, bh = LEGACY_IO_BG_SIZE
            bg_key = "lg_io_bg"
        strip_len = bw * 1.05 * es
        strip_w = bh * es
        out.append(Sprite((right - strip_w / 2.0) * lk,
                          (cy0 + LEGACY_KEY_FLOW_OFFSET[1] * es
                           + strip_len / 2.0 - 4.0 * es) * lk,
                          strip_len * lk, strip_w * lk, bg_key,
                          (1, 1, 1, self.op), rotation=math.pi / 2.0))
        io_text = (0, 0, 0)
        if sk is not None:
            io_text = tuple(c / 255.0 for c in sk.info.input_overlay_text)
        held, counts = self.keys.state_at(t)
        key_cx = right - cell / 2.0 + LEGACY_KEY_FLOW_OFFSET[0] * es
        for row, ch in enumerate(channels):
            cy = (cy0 + LEGACY_KEY_FLOW_OFFSET[1] * es
                  + row * (cell + gap) + cell / 2.0)
            pressed = bool(held & (1 << ch))
            press_t = self.keys.last_press_at(ch, t)
            release_t = self.keys.last_release_at(ch, t)
            if pressed and press_t is not None:
                p = ease_out_quad((t - press_t) / LEGACY_KEY_PRESS_MS)
                scale = 1.0 + (LEGACY_KEY_PRESS_SCALE - 1.0) * p
            elif release_t is not None:
                p = ease_out_quad((t - release_t) / LEGACY_KEY_PRESS_MS)
                scale = LEGACY_KEY_PRESS_SCALE \
                    + (1.0 - LEGACY_KEY_PRESS_SCALE) * p
            else:
                scale = 1.0
            tint = (1.0, 1.0, 1.0)
            if pressed:
                # LegacyKeyCounterDisplay tints by ROW: the first two
                # visible counters yellow, the rest magenta
                tint = (LEGACY_KEY_ACTIVE_TOP if row < 2
                        else LEGACY_KEY_ACTIVE_BOTTOM)
            if sk_ok("inputoverlay-key"):
                kw, kh = sk.size["inputoverlay-key"]
                k_key = "sk_inputoverlay-key"
            else:
                from .textures import LEGACY_IO_KEY_SIZE
                kw = kh = LEGACY_IO_KEY_SIZE
                k_key = "lg_io_key"
            out.append(Sprite(key_cx * lk, cy * lk,
                              kw * scale * es * lk, kh * scale * es * lk,
                              k_key, (*tint, self.op)))
            count = counts[ch]
            if count > 0:
                text = str(count)
                tw, th = self._legacy_text_size("scoreentry", text,
                                                scale * es * 0.0 + 1.0)
                target_h = 16.0 * scale * es
                m = target_h / th
                self._legacy_text(out, "scoreentry", text,
                                  key_cx + tw * m / 2.0,
                                  cy - target_h / 2.0, m, self.op,
                                  color=io_text)
            else:
                lh = 15.0 * scale * es
                lw = self._lrun_width(KEY_LABELS[ch], lh)
                self._lrun(out, KEY_LABELS[ch], key_cx - lw / 2.0,
                           cy - lh / 2.0, lh, io_text, self.op)

    # ==================== shared elements ==========================================

    def _top_right_stack_y(self) -> float:
        """Top of the right-edge secondary stack (mod pills, hit counter)
        — under the accuracy block (Argon) or the score+acc+pie block
        (legacy), in lazer px."""
        return (104.0 if self.legacy_score else 68.0) * self.es

    def _mod_pills(self, out, t: float) -> None:
        """§4.6 Gameplay.Mods: procedural rounded pills with the active
        mod acronyms, right-aligned under the accuracy block, category-
        coloured (reduction green / increase red / automation blue) —
        lazer-style mod display without the icon sheet."""
        if not getattr(self.s, "show_mods", True) or not self._mod_acrs:
            return
        es, lk = self.es, self.lk
        h = MOD_PILL_H * es
        text_h = h * MOD_TEXT_FRAC
        top = self._top_right_stack_y()
        x_right = self.ui_w_l - 20.0 * es
        for acr in reversed(self._mod_acrs):     # lay right-to-left
            tw = self._lrun_width(acr, text_h)
            pw = tw + 2.0 * MOD_PILL_PAD_X * es
            cx = x_right - pw / 2.0
            color = mod_pill_color(acr)
            out.append(Sprite(cx * lk, (top + h / 2.0) * lk,
                              pw * lk, h * lk, "pill",
                              (*color, 0.88 * self.op)))
            self._lrun(out, acr, cx - tw / 2.0,
                       top + (h - text_h) / 2.0, text_h,
                       (1.0, 1.0, 1.0), 0.95 * self.op)
            x_right -= pw + MOD_PILL_GAP * es

    def _hit_counter(self, out, t: float) -> None:
        """§4.6 HitCounter: the live 300/100/50/miss column under the mod
        pills, counts mono right-aligned, judgment-band colours."""
        if not getattr(self.s, "show_hit_counter", False):
            return
        es = self.es
        counts = self.data.counts_at(t)
        colors = (BAND_300, BAND_100, BAND_50, (0.95, 0.25, 0.30))
        row_h = HITC_ROW_H * es
        text_h = row_h * 0.78
        top = self._top_right_stack_y()
        if self._mod_acrs and getattr(self.s, "show_mods", True):
            top += (MOD_PILL_H + 8.0) * es
        right = self.ui_w_l - 20.0 * es
        for i, (label, n, color) in enumerate(
                zip(HITC_LABELS, counts, colors)):
            y = top + i * row_h
            num = str(n)
            nw = self._lrun_width(num, text_h, mono=True)
            self._lrun(out, num, right - nw, y, text_h,
                       (1.0, 1.0, 1.0), 0.92 * self.op, mono=True)
            lw = self._lrun_width(label, text_h * 0.85)
            self._lrun(out, label, right - nw - 8.0 * es - lw,
                       y + text_h * 0.075, text_h * 0.85, color,
                       0.92 * self.op)

    def _pp_counter(self, out, t: float) -> None:
        """§4.6 PPCounter: the rosu gradual pp (render/pp.py), rolled like
        the Argon counters, top-left under the hp bar. Hidden when the
        timeline is unavailable (rosu missing / map failed) — fail-soft."""
        if not getattr(self.s, "show_pp_counter", False) \
                or not self.pp_pts:
            return
        from .pp import pp_at
        es = self.es
        v = pp_at(self.pp_pts, self._pp_times, t)
        num = str(int(round(v)))
        h = PP_DIGIT_H * es
        x = PP_LEFT_X * es
        y = PP_TOP_Y * es
        w = self._lrun(out, num, x, y, h, (1.0, 1.0, 1.0),
                       0.95 * self.op, mono=True)
        self._lrun(out, "pp", x + w + 3.0 * es, y + h * 0.32, h * 0.62,
                   BLUE0, 0.9 * self.op)

    def _aim_error(self, out, t: float) -> None:
        """§4.6 AimErrorMeter (danser-style; lazer ships none): scatter
        panel of the cursor offset AT each click, in circle-radius units
        — crosshair + ring at the circle edge + dots fading over the
        hit-error window + the mean |offset| stat underneath."""
        if not getattr(self.s, "show_aim_error_meter", False) \
                or not self.aim_pts:
            return
        es, k = self.es, self.k
        cx = self.ui_w / 2.0 - (ERR_HALF_W + AIM_GAP_FROM_ERR) * es
        cy = UI_HEIGHT - (AIM_PANEL_R + 34.0) * es
        R = AIM_PANEL_R * es
        r_unit = R * AIM_RING_FRAC              # 1.0 radius = circle edge
        out.append(Sprite(cx * k, cy * k, 2 * R * k, 2 * R * k, "disc",
                          (0.06, 0.07, 0.10, 0.55 * self.op)))
        out.append(Sprite(cx * k, cy * k, 2 * r_unit * k, 2 * r_unit * k,
                          "approach", (1.0, 1.0, 1.0, 0.35 * self.op)))
        for w_px, h_px in ((2 * R * 0.92, 1.6 * es), (1.6 * es, 2 * R * 0.92)):
            out.append(Sprite(cx * k, cy * k, w_px * k, h_px * k, None,
                              (1.0, 1.0, 1.0, 0.22 * self.op)))
        lo = bisect.bisect_left(self._aim_times, t - ERR_TICK_FADE_MS)
        hi = bisect.bisect_right(self._aim_times, t)
        n = 0
        mag_sum = 0.0
        for i in range(lo, hi):
            ti, dx, dy = self.aim_pts[i]
            fade = 1.0 - (t - ti) / ERR_TICK_FADE_MS
            if fade <= 0.0:
                continue
            mag = math.hypot(dx, dy)
            n += 1
            mag_sum += mag
            color = (BAND_300 if mag <= 0.5
                     else BAND_100 if mag <= 1.0 else BAND_50)
            px = cx + max(-1.0, min(1.0, dx * AIM_RING_FRAC)) * R
            py = cy + max(-1.0, min(1.0, dy * AIM_RING_FRAC)) * R
            d = AIM_DOT_PX * es
            out.append(Sprite(px * k, py * k, d * k, d * k, "dot",
                              (*color, 0.9 * fade * self.op)))
        if n > 0:
            text = f"{mag_sum / n:.2f}R"
            th = 20.0 * es
            tw = self._run_width(text, th, mono=True)
            self._run(out, text, cx - tw / 2.0, cy + R + 6.0 * es, th,
                      (0.86, 0.90, 1.0), 0.85 * self.op, mono=True)

    def _strain_graph(self, out, t: float) -> None:
        """§4.6 StrainGraph: bottom fill graph over the map — rosu strain
        sections (or the labelled density proxy), max-pooled to
        STRAIN_MAX_COLS columns, progress-swept (played part in Blue0,
        the rest dim grey). Drawn first → under all other HUD."""
        st = self.strain
        if not getattr(self.s, "show_strain_graph", False) or st is None \
                or not st.values:
            return
        es, lk = self.es, self.lk
        vals = st.values
        if len(vals) > STRAIN_MAX_COLS:
            pool = -(-len(vals) // STRAIN_MAX_COLS)
            vals = [max(vals[i:i + pool])
                    for i in range(0, len(vals), pool)]
        else:
            pool = 1
        vmax = max(vals) or 1.0
        width = self.ui_w_l * ARGON_PROGRESS_WIDTH
        x0 = (self.ui_w_l - width) / 2.0
        bottom = LAZER_UI_HEIGHT - STRAIN_BOTTOM_GAP * es
        hmax = STRAIN_H * es
        col_w = width / len(vals)
        sect = st.section_ms * st.speed * pool     # map-ms per column
        cur = (t - st.first_t) / sect if sect > 0 else 0.0
        for i, v in enumerate(vals):
            if v <= 0:
                continue
            gh = max(hmax * (v / vmax), 1.5 * es)
            played = i < cur
            color = (BLUE0 if played else (0.55, 0.55, 0.60))
            alpha = (0.50 if played else 0.25) * self.op
            out.append(Sprite((x0 + (i + 0.5) * col_w) * lk,
                              (bottom - gh / 2.0) * lk,
                              col_w * 0.88 * lk, gh * lk, None,
                              (*color, alpha)))

    def _watermark(self, out) -> None:
        """R3D watermark_text: small corner text, bottom-right (clear of
        the Argon progress strip's 5 % side margin and the legacy combo)."""
        text = (getattr(self.s, "watermark_text", "") or "").upper()
        if not text:
            return                # (uppercased: the glyph bank is caps-only)
        es = self.es
        h = WATERMARK_H * es
        w = self._lrun_width(text, h)
        self._lrun(out, text, self.ui_w_l - w - 8.0 * es,
                   LAZER_UI_HEIGHT - h - 4.0 * es, h,
                   (1.0, 1.0, 1.0), 0.5 * self.op)

    def _grade_badge(self, out, t: float, right_x_l: float,
                     cy_l: float) -> None:
        """In-HUD grade: the skin's ranking-*-small badge when it ships
        one (legacy small grades, native size), the procedural coloured
        text otherwise. renderer_default_font_and_ranks forces the
        procedural text even under a skin. Behind show_grade; silver
        (HD/FL) variants are not simulated."""
        if t < self._first_ev_t:
            return                     # m-8: no SS badge before the 1st judgment
        grade = self.data.grade_at(t)
        sk = self.sk
        el = GRADE_RANKING_ELEMENT.get(grade)
        if (not self.force_default and sk is not None and el is not None
                and sk.has(el) and el not in sk.empty):
            w, h = sk.size[el]
            es, lk = self.es, self.lk
            out.append(Sprite((right_x_l - w * es / 2.0) * lk, cy_l * lk,
                              w * es * lk, h * es * lk, f"sk_{el}",
                              (1, 1, 1, 0.95 * self.op)))
            return
        gh = 24.0 * self.es
        color = GRADE_COLORS.get(grade, (0.8, 0.8, 0.85))
        gw = self._lrun_width(grade, gh)
        self._lrun(out, grade, right_x_l - gw, cy_l - gh / 2.0, gh,
                   color, 0.95 * self.op)

    def _hit_error(self, out: list[Sprite], t: float) -> None:
        """House hit-error meter + UR (kept from the previous phase; not a
        lazer-skin element). Raised above the Argon progress strip."""
        s, d = self.s, self.data
        if not s.show_hit_error_meter:
            return
        if t < self._first_ev_t:
            return                     # m-8: no hit-error strip pre-gameplay
        if self.argon_league:          # Argon vertical side bars
            self._argon_hit_error(out, t)
            return
        k = self.k
        es = self.es
        cx = self.ui_w / 2.0
        cy = UI_HEIGHT - ERR_Y_FROM_BOTTOM
        half_w = ERR_HALF_W * es
        band_h = ERR_BAND_H * es
        meh = self.hw.meh
        for win, color, a in ((meh, BAND_50, 0.55),
                              (self.hw.ok, BAND_100, 0.6),
                              (self.hw.great, BAND_300, 0.7)):
            bw = 2.0 * half_w * (win / meh)
            out.append(Sprite(cx * k, cy * k, bw * k, band_h * k,
                              None, (*color, a * self.op)))
        out.append(Sprite(cx * k, cy * k, 3.0 * es * k, 26.0 * es * k,
                          None, (1, 1, 1, 0.9 * self.op)))
        for age, delta in d.errors_in_window(t):
            fade = 1.0 - age / ERR_TICK_FADE_MS
            if fade <= 0.0:
                continue
            x = cx + max(-1.0, min(1.0, delta / meh)) * half_w
            a = abs(delta)
            color = (BAND_300 if a <= self.hw.great
                     else BAND_100 if a <= self.hw.ok else BAND_50)
            out.append(Sprite(x * k, cy * k, ERR_TICK_W * es * k,
                              ERR_TICK_H * es * k,
                              None, (*color, 0.85 * fade * self.op)))
        ur, avg, n = d.ur_at(t)
        if n > 0:
            ax = cx + max(-1.0, min(1.0, avg / meh)) * half_w
            ay = cy - band_h / 2.0 - 12.0 * es
            out.append(Sprite(ax * k, ay * k, 16.0 * es * k, 12.0 * es * k,
                              "tri_down", (1, 1, 1, 0.9 * self.op)))
        if s.show_unstable_rate and n >= 2:
            uh = UR_H * es
            text = f"UR {ur:.1f}"
            w = self._run_width(text, uh, mono=True)
            self._run(out, text, cx - w / 2.0,
                      cy + band_h / 2.0 + 10.0 * es, uh,
                      (0.86, 0.90, 1.0), 0.9 * self.op, mono=True)

    def _argon_hit_error(self, out: list[Sprite], t: float) -> None:
        """ArgonHitErrorMeter placement: two vertical bars mirrored at the
        left+right screen edges. Delta maps to the VERTICAL axis (0 centred);
        nested 300/100/50 zones by height; a judgement line per hit fading
        over PointFadeOutTime; the moving-average arrow points at the bar."""
        s, d = self.s, self.data
        k, es = self.k, self.es
        meh = self.hw.meh
        half_h = ARGON_ERR_HALF_H * es
        bar_w = ARGON_ERR_BAR_W * es
        tick_len = ARGON_ERR_TICK_LEN * es
        tick_th = ARGON_ERR_TICK_TH * es
        cy = UI_HEIGHT / 2.0
        errs = list(d.errors_in_window(t))
        ur, avg, n = d.ur_at(t)
        for side in (-1, 1):                       # left bar, right bar
            cx = (ARGON_ERR_MARGIN if side < 0
                  else self.ui_w - ARGON_ERR_MARGIN)
            for win, color, a in ((meh, BAND_50, 0.55),
                                  (self.hw.ok, BAND_100, 0.6),
                                  (self.hw.great, BAND_300, 0.7)):
                bh = 2.0 * half_h * (win / meh)
                out.append(Sprite(cx * k, cy * k, bar_w * k, bh * k,
                                  None, (*color, a * self.op)))
            out.append(Sprite(cx * k, cy * k, tick_len * k, tick_th * k,
                              None, (1, 1, 1, 0.9 * self.op)))
            for age, delta in errs:
                fade = 1.0 - age / ERR_TICK_FADE_MS
                if fade <= 0.0:
                    continue
                y = cy + max(-1.0, min(1.0, delta / meh)) * half_h
                mag = abs(delta)
                color = (BAND_300 if mag <= self.hw.great
                         else BAND_100 if mag <= self.hw.ok else BAND_50)
                out.append(Sprite(cx * k, y * k, tick_len * k, tick_th * k,
                                  None, (*color, 0.85 * fade * self.op)))
            if n > 0:
                ay = cy + max(-1.0, min(1.0, avg / meh)) * half_h
                ax = cx - side * (bar_w / 2.0 + 7.0 * es)
                rot = -math.pi / 2.0 if side < 0 else math.pi / 2.0
                out.append(Sprite(ax * k, ay * k, 12.0 * es * k,
                                  14.0 * es * k, "tri_down",
                                  (1, 1, 1, 0.9 * self.op), rotation=rot))
        if s.show_unstable_rate and n >= 2:
            uh = UR_H * es
            text = f"UR {ur:.1f}"
            w = self._run_width(text, uh, mono=True)
            self._run(out, text, self.ui_w / 2.0 - w / 2.0,
                      UI_HEIGHT - ERR_Y_FROM_BOTTOM, uh,
                      (0.86, 0.90, 1.0), 0.9 * self.op, mono=True)

    def _break_flash(self, out: list[Sprite], t: float) -> None:
        if not getattr(self.s, "combo_break_flash", True):
            return
        brk = self.data.last_break_at(t)
        if brk is None or brk.prev_combo < BREAK_FLASH_MIN_COMBO:
            return
        age = t - brk.time_ms
        if not (0.0 <= age < BREAK_FLASH_MS):
            return
        alpha = BREAK_FLASH_ALPHA * (1.0 - age / BREAK_FLASH_MS)
        w, h = self.spr.width, self.spr.height
        out.append(Sprite(w / 2.0, h / 2.0, float(w), float(h),
                          "vignette", (0.92, 0.16, 0.20, alpha)))


def _legacy_fill_colour(hp: float) -> tuple[float, float, float]:
    """LegacyHealthDisplay.getFillColour: white above 0.5, fading to black
    toward 0.2, then black→red below 0.2 (linear stand-in for
    InterpolateNonLinear)."""
    if hp < 0.2:
        p = _clamp01((0.2 - hp) / 0.2)
        return (p, 0.0, 0.0)
    if hp < LEGACY_EPIC_CUTOFF:
        p = _clamp01((0.5 - hp) / 0.5)
        g = 1.0 - p
        return (g, g, g)
    return (1.0, 1.0, 1.0)


def _fmt_time(seconds: float) -> str:
    """SongProgressInfo.formatTime: m:ss with a leading '-' when
    negative."""
    neg = seconds < 0
    s = abs(seconds)
    m = int(s // 60)
    return f"{'-' if neg else ''}{m}:{int(s % 60):02d}"
