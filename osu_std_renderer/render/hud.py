"""Gameplay HUD — RENDER_PLAN.md §4.6 elements, drawn in the §5.3 virtual
**1080p UI camera** (ScaledHeight=1080, width by aspect): every layout
constant here is authored in 1080p UI px and multiplied by
`screen_h / 1080` at draw time, so the HUD looks identical at 720p/1080p/4K.

Elements (each behind its §4.6 Show flag in settings.py; sizes scale with
`hud_scale`, all alphas with `hud_opacity`):

  Score       top-right, 8 mono digits, lazer-standardised value from the
              ruleset's JudgmentEvent stream (same formula the bot's
              versus_telemetry ships), rolling ~120 ms tween per change.
              `pin_final_score()` is the endpoint-pin hook: scale every
              displayed value so the LAST event lands on an authoritative
              total (the mania pattern) — exposed, not auto-wired yet.
  Accuracy    under the score, live 2-decimal percentage per judgment.
  Grade       left of the accuracy line, live SS/S/A/B/C/D from the
              running counts (mania grade colour family).
  Progress    §4.6 Score.ProgressBar: "pie" (quantized pie masks, left of
              the score) or "bar" (thin top-of-frame bar).
  Combo       bottom-left "123x", pop-scale 1.15→1 ease on increment,
              red fade/shrink of the lost value on a break. Steps on the
              ruleset's part-level combo_timeline, so mid-slider ticks
              count up live and breaks land at the REAL break moment.
  HitError    bottom-centre bar: window bands from the map's real OD
              (OsuHitWindows), per-hit tick marks at their signed delta
              fading over ~10 s (§4.6 PointFadeOutTime), moving-average
              arrow, live UR readout (10× the running stddev) under it.
              Deltas include slider-head hits (what the game shows).
  KeyOverlay  right edge: K1/K2 always, M1/M2 only when the replay ever
              presses a mouse-only button (stable conflates M into K for
              keyboard taps — de-conflated here). Squares light + shrink
              while held; label is replaced by the running press count
              after the first press.
  BreakFlash  combo-break feedback: a subtle red EDGE vignette pulse
              (~220 ms) when a combo ≥ BREAK_FLASH_MIN_COMBO is lost —
              the mania full-frame red wash, reshaped. `combo_break_flash`
              turns it off.

NOT here yet (later phases, honest list): HP bar (the ruleset defers HP
drain entirely), PP counter (no live PP model in-repo; `show_pp_counter`
is accepted and ignored), mod pills, hit counter, aim-error meter
(`show_aim_error_meter` default-off per the plan), strain graph,
scoreboard.

Everything is STATELESS per frame (bisect over precomputed arrays), so
--dump-frames at arbitrary times renders the exact same HUD the video
shows at that moment.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from ..replay.replay import KEY_K1, KEY_K2, KEY_M1, KEY_M2
from ..ruleset import JudgmentKind, OsuHitWindows
from .gl import Sprite

UI_HEIGHT = 1080.0

MARGIN = 32.0                  # UI px, screen-edge padding
SCORE_H = 64.0                 # score digit height
SCORE_DIGITS = 8
ACC_H = 34.0
GRADE_H = 36.0
COMBO_H = 72.0
COMBO_X_FRAC = 0.55            # the 'x' suffix height / number height
SCORE_ROLL_MS = 120.0          # score tween per change
COMBO_POP_MS = 120.0           # pop-scale 1.15→1
COMBO_POP_AMOUNT = 0.15
COMBO_BREAK_FADE_MS = 300.0    # lost-combo red fade/shrink
BREAK_FLASH_MS = 220.0         # vignette pulse length
BREAK_FLASH_MIN_COMBO = 10     # smaller losses don't pulse
BREAK_FLASH_ALPHA = 0.38       # peak vignette alpha (edge-weighted mask)

ERR_HALF_W = 220.0             # hit-error bar half width (= the 50 window)
ERR_BAND_H = 8.0
ERR_TICK_W = 3.0
ERR_TICK_H = 22.0
ERR_TICK_FADE_MS = 10_000.0    # §4.6 HitErrorMeter.PointFadeOutTime = 10 s
ERR_ARROW_N = 10               # moving average over the last N hits
ERR_Y_FROM_BOTTOM = 64.0       # band centre above the bottom edge
UR_H = 26.0

KEY_SQ = 56.0
KEY_GAP = 10.0
KEY_MARGIN = 24.0
KEY_LABELS = ("K1", "K2", "M1", "M2")
KEY_COLORS = (                 # pressed fill tints (K = R3D red family, M = blue)
    (1.00, 0.36, 0.42), (1.00, 0.36, 0.42),
    (0.42, 0.62, 1.00), (0.42, 0.62, 1.00),
)
KEY_IDLE = (0.62, 0.62, 0.72)

PIE_R = 26.0                   # progress pie radius
PIE_STEPS = 48                 # must match textures.PIE_STEPS

# same visual family as the judgment popups (scene.POPUP_COLORS)
BAND_300 = (0.38, 0.72, 1.00)
BAND_100 = (0.42, 0.88, 0.47)
BAND_50 = (0.95, 0.76, 0.36)

GRADE_COLORS = {               # mania v2 _GRADE_COLOURS, normalized
    "SS": (0.94, 0.86, 0.47),
    "S": (0.94, 0.86, 0.47),
    "A": (0.43, 0.86, 0.51),
    "B": (0.43, 0.71, 0.86),
    "C": (0.78, 0.51, 0.86),
    "D": (0.86, 0.43, 0.43),
}

_TRACKING = 0.05               # letter-spacing, fraction of glyph height


# --- pure math (unit-tested, no GL) ---------------------------------------------

def std_accuracy(c300: int, c100: int, c50: int, cmiss: int) -> float:
    """Standard osu! accuracy over object judgments (0..1)."""
    total = c300 + c100 + c50 + cmiss
    if total == 0:
        return 1.0
    return (300 * c300 + 100 * c100 + 50 * c50) / (300.0 * total)


def grade_for(c300: int, c100: int, c50: int, cmiss: int) -> str:
    """std grade thresholds (osu! wiki; the replay.py formula on live
    counts). Silver (HD/FL) variants are a display concern, skipped."""
    total = c300 + c100 + c50 + cmiss
    if total == 0:
        return "SS"          # nothing judged yet — a clean sheet
    if c300 == total:
        return "SS"
    r300 = c300 / total
    r50 = c50 / total
    if r300 > 0.9 and r50 <= 0.01 and cmiss == 0:
        return "S"
    if (r300 > 0.8 and cmiss == 0) or r300 > 0.9:
        return "A"
    if (r300 > 0.7 and cmiss == 0) or r300 > 0.8:
        return "B"
    if r300 > 0.6:
        return "C"
    return "D"


def unstable_rate(deltas) -> float:
    """UR = 10 × the population stddev of the signed hit deltas."""
    n = len(deltas)
    if n < 2:
        return 0.0
    mean = sum(deltas) / n
    var = sum((d - mean) ** 2 for d in deltas) / n
    return 10.0 * math.sqrt(max(var, 0.0))


def rolled(prev: float, target: float, age_ms: float,
           roll_ms: float = SCORE_ROLL_MS) -> float:
    """Value tween: prev → target over roll_ms after a change."""
    if age_ms >= roll_ms or roll_ms <= 0:
        return target
    if age_ms <= 0:
        return prev
    return prev + (target - prev) * (age_ms / roll_ms)


def combo_pop_scale(age_ms: float, dur_ms: float = COMBO_POP_MS,
                    amount: float = COMBO_POP_AMOUNT) -> float:
    """1.15→1 ease-out right after an increment."""
    if age_ms < 0 or age_ms >= dur_ms:
        return 1.0
    p = age_ms / dur_ms
    return 1.0 + amount * (1.0 - p) ** 2


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


# --- replay key channels (K1/K2/M1/M2, de-conflated) ------------------------------

class KeySeries:
    """Per-frame key-overlay state: which of the 4 channels is held and the
    cumulative press count per channel (press EDGES only — held frames
    never re-count). Stable sets M1|K1 together for keyboard taps, so a
    channel counts as MOUSE only when its M bit is set WITHOUT the K bit."""

    def __init__(self, frames):
        self.times: list[float] = []
        self.held: list[int] = []          # 4-bit mask: 1=K1 2=K2 4=M1 8=M2
        self.counts: list[tuple[int, int, int, int]] = []
        prev = 0
        c = [0, 0, 0, 0]
        for f in frames:
            k1 = 1 if f.keys & KEY_K1 else 0
            k2 = 1 if f.keys & KEY_K2 else 0
            m1 = 1 if (f.keys & KEY_M1) and not k1 else 0
            m2 = 1 if (f.keys & KEY_M2) and not k2 else 0
            mask = k1 | (k2 << 1) | (m1 << 2) | (m2 << 3)
            edges = mask & ~prev
            for ch in range(4):
                if edges & (1 << ch):
                    c[ch] += 1
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


# --- precomputed HUD timeline (no GL; queried statelessly per frame) ---------------

@dataclass
class BreakInfo:
    time_ms: float
    prev_combo: int


class HudData:
    """Arrays the per-frame draws bisect into, built once from the ruleset
    SimResult: score/acc/grade per object judgment, the part-level combo
    timeline, break moments, and the signed hit-delta series (circles +
    slider heads) with prefix sums for O(log n) live UR."""

    def __init__(self, sim):
        ev = sim.events                      # time-sorted by construction
        self.ev_times = [e.time_ms for e in ev]
        self.ev_scores = [e.score_after for e in ev]
        self.ev_accs = [e.acc_after for e in ev]
        self.ev_grades: list[str] = []
        c = {JudgmentKind.HIT300: 0, JudgmentKind.HIT100: 0,
             JudgmentKind.HIT50: 0, JudgmentKind.MISS: 0}
        for e in ev:
            c[e.kind] += 1
            self.ev_grades.append(grade_for(
                c[JudgmentKind.HIT300], c[JudgmentKind.HIT100],
                c[JudgmentKind.HIT50], c[JudgmentKind.MISS]))

        tl = sim.combo_timeline
        self.ct_times = [t for t, _ in tl]
        self.ct_combos = [v for _, v in tl]
        self.max_combo = sim.final_max_combo
        self.breaks: list[BreakInfo] = []
        prev = 0
        for t, v in tl:
            if v == 0 and prev > 0:
                self.breaks.append(BreakInfo(t, prev))
            prev = v
        self.bk_times = [b.time_ms for b in self.breaks]

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

    def score_at(self, t: float) -> float:
        i = bisect.bisect_right(self.ev_times, t) - 1
        if i < 0:
            return 0.0
        prev = self.ev_scores[i - 1] if i > 0 else 0
        return rolled(prev, self.ev_scores[i], t - self.ev_times[i])

    def acc_at(self, t: float) -> float:
        i = bisect.bisect_right(self.ev_times, t) - 1
        return self.ev_accs[i] if i >= 0 else 1.0

    def grade_at(self, t: float) -> str:
        i = bisect.bisect_right(self.ev_times, t) - 1
        return self.ev_grades[i] if i >= 0 else "SS"

    def combo_at(self, t: float) -> tuple[int, float]:
        """(combo, ms since it last changed)."""
        i = bisect.bisect_right(self.ct_times, t) - 1
        if i < 0:
            return 0, math.inf
        return self.ct_combos[i], t - self.ct_times[i]

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


# --- the HUD ------------------------------------------------------------------------

class StdHud:
    """Draws the §4.6 HUD over the scene each frame (called by StdScene
    after the cursor — §5.3 draw order ends `cursors → HUD`)."""

    def __init__(self, sprites, bank, settings, judgments, frames, beatmap):
        self.spr = sprites
        self.bank = bank
        self.s = settings
        self.k = sprites.height / UI_HEIGHT       # screen px per UI px
        self.ui_w = sprites.width / self.k
        self.es = float(getattr(settings, "hud_scale", 1.0))
        self.op = float(getattr(settings, "hud_opacity", 1.0))
        self.data = HudData(judgments)
        self.keys = KeySeries(frames)
        self.hw = OsuHitWindows(beatmap.diff.od)
        starts = [o.get_start_time() for o in beatmap.hit_objects]
        ends = [o.get_end_time() for o in beatmap.hit_objects]
        self.first_t = min(starts) if starts else 0.0
        self.last_t = max(ends) if ends else 1.0
        self._pin = 1.0

    # -- endpoint pin (the mania pattern) ------------------------------------------
    def pin_final_score(self, final_total: int) -> None:
        """Scale every displayed score so the last judgment lands exactly on
        an authoritative final total (e.g. the .osr's recorded score once
        the service decides it's trustworthy for the engine in play).
        Callable any time before/again during rendering; display-only."""
        last = self.ev_final_score()
        if last > 0 and final_total > 0:
            self._pin = final_total / last

    def ev_final_score(self) -> int:
        return self.data.ev_scores[-1] if self.data.ev_scores else 0

    def final_values(self) -> dict:
        """The end-state numbers (for the CLI's final-values check line)."""
        d = self.data
        end = self.last_t + 1.0
        ur, _avg, _n = d.ur_at(end)
        return {
            "score": int(round(self.ev_final_score() * self._pin)),
            "acc": d.acc_at(end),
            "combo": d.combo_at(end)[0],
            "max_combo": d.max_combo,
            "ur": ur,
            "grade": d.grade_at(end),
        }

    # -- text helper -----------------------------------------------------------------
    def _run(self, out: list[Sprite], text: str, x_left: float, y_top: float,
             h: float, color, alpha: float, mono: bool = False) -> float:
        """Lay `text` (UI px) left-to-right; returns the run width (UI px)."""
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

    # -- frame draw --------------------------------------------------------------------
    def draw(self, t: float) -> None:
        if self.op <= 0.0:
            return
        out: list[Sprite] = []
        score_w = self._score_block(out, t)
        self._progress(out, t, score_w)
        self._combo(out, t)
        self._hit_error(out, t)
        self._key_overlay(out, t)
        self._break_flash(out, t)
        if out:
            self.spr.draw(out)

    # -- elements ------------------------------------------------------------------------

    def _score_block(self, out: list[Sprite], t: float) -> float:
        """Score + accuracy + grade, top-right. Returns the score run width
        (UI px) so the progress pie can sit left of it."""
        s, d = self.s, self.data
        if not s.show_score:
            return 0.0
        rx = self.ui_w - MARGIN
        h = SCORE_H * self.es
        text = f"{int(round(d.score_at(t) * self._pin)):0{SCORE_DIGITS}d}"
        w = self._run_width(text, h, mono=True)
        self._run(out, text, rx - w, 24.0, h, (1, 1, 1), 1.0 * self.op,
                  mono=True)

        ah = ACC_H * self.es
        acc_text = f"{d.acc_at(t) * 100.0:.2f}%"
        aw = self._run_width(acc_text, ah, mono=True)
        ay = 24.0 + h + 10.0
        self._run(out, acc_text, rx - aw, ay, ah, (0.92, 0.92, 0.96),
                  0.95 * self.op, mono=True)

        if s.show_grade:
            gh = GRADE_H * self.es
            grade = d.grade_at(t)
            color = GRADE_COLORS.get(grade, (0.8, 0.8, 0.85))
            gw = self._run_width(grade, gh)
            self._run(out, grade, rx - aw - 24.0 - gw, ay + (ah - gh) / 2.0,
                      gh, color, 0.95 * self.op)
        return w

    def _progress(self, out: list[Sprite], t: float, score_w: float) -> None:
        s = self.s
        if not getattr(s, "show_progress", True):
            return
        frac = (t - self.first_t) / max(self.last_t - self.first_t, 1.0)
        frac = min(max(frac, 0.0), 1.0)
        k = self.k
        if getattr(s, "progress_style", "pie") == "bar":
            bh = 6.0 * self.es
            out.append(Sprite(self.ui_w * k / 2.0, bh * k / 2.0,
                              self.ui_w * k, bh * k,
                              None, (0.16, 0.16, 0.2, 0.5 * self.op)))
            if frac > 0.0:
                out.append(Sprite(self.ui_w * frac * k / 2.0, bh * k / 2.0,
                                  self.ui_w * frac * k, bh * k,
                                  None, (1, 1, 1, 0.85 * self.op)))
            return
        # pie, left of the big score
        r = PIE_R * self.es
        cx = self.ui_w - MARGIN - score_w - 20.0 - r
        cy = 24.0 + SCORE_H * self.es / 2.0
        d = 2.0 * r
        if frac >= 1.0:
            out.append(Sprite(cx * k, cy * k, d * k, d * k, "disc",
                              (1, 1, 1, 0.55 * self.op)))
        else:
            idx = min(int(frac * PIE_STEPS), PIE_STEPS - 1)
            if idx > 0:
                out.append(Sprite(cx * k, cy * k, d * k, d * k,
                                  f"pie_{idx:02d}",
                                  (1, 1, 1, 0.55 * self.op)))
        out.append(Sprite(cx * k, cy * k, d * k, d * k, "pie_ring",
                          (1, 1, 1, 0.8 * self.op)))

    def _combo(self, out: list[Sprite], t: float) -> None:
        s, d = self.s, self.data
        if not s.show_combo:
            return
        k = self.k
        h = COMBO_H * self.es
        x_left = MARGIN
        y_bottom = UI_HEIGHT - 28.0
        combo, age = d.combo_at(t)

        brk = d.last_break_at(t)
        if brk is not None:
            b_age = t - brk.time_ms
            if 0.0 <= b_age < COMBO_BREAK_FADE_MS:
                # the lost value: red, fading + shrinking + drifting upward
                # (the visible reset — the drift keeps it from stacking on
                # the new count when a hit lands right after the break)
                p = b_age / COMBO_BREAK_FADE_MS
                bh = h * (1.0 - 0.15 * p)
                alpha = 0.85 * (1.0 - p) * self.op
                self._combo_text(out, f"{brk.prev_combo}", x_left,
                                 y_bottom - h * 0.55 * p, bh,
                                 (0.95, 0.25, 0.30), alpha)
        if combo > 0:
            pop = combo_pop_scale(age)
            self._combo_text(out, f"{combo}", x_left, y_bottom, h * pop,
                             (1, 1, 1), 0.95 * self.op)

    def _combo_text(self, out: list[Sprite], num: str, x_left: float,
                    y_bottom: float, h: float, color, alpha: float) -> None:
        """Combo number + smaller 'x' suffix, baseline-anchored bottom-left
        (pop scaling grows the number upward, the anchor stays put)."""
        w = self._run(out, num, x_left, y_bottom - h, h, color, alpha,
                      mono=True)
        xh = h * COMBO_X_FRAC
        self._run(out, "x", x_left + w + 0.08 * h, y_bottom - xh, xh,
                  color, alpha * 0.85)

    def _hit_error(self, out: list[Sprite], t: float) -> None:
        s, d = self.s, self.data
        if not s.show_hit_error_meter:
            return
        k = self.k
        es = self.es
        cx = self.ui_w / 2.0
        cy = UI_HEIGHT - ERR_Y_FROM_BOTTOM
        half_w = ERR_HALF_W * es
        band_h = ERR_BAND_H * es
        meh = self.hw.meh
        # window bands: widths from the map's REAL OD windows, meh = full bar
        for win, color, a in ((meh, BAND_50, 0.55), (self.hw.ok, BAND_100, 0.6),
                              (self.hw.great, BAND_300, 0.7)):
            bw = 2.0 * half_w * (win / meh)
            out.append(Sprite(cx * k, cy * k, bw * k, band_h * k,
                              None, (*color, a * self.op)))
        # centre line
        out.append(Sprite(cx * k, cy * k, 3.0 * es * k, 26.0 * es * k,
                          None, (1, 1, 1, 0.9 * self.op)))
        # per-hit ticks at their signed delta, fading over the window
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
            # moving-average arrow above the bar
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

    def _key_overlay(self, out: list[Sprite], t: float) -> None:
        s = self.s
        if not s.show_key_overlay:
            return
        k = self.k
        es = self.es
        # K1/K2 are the resting pair; M1/M2 rows join when the replay
        # really presses a mouse-only button. Lazer replays record taps as
        # M1/M2 with the K bits never set — in that case the dead K rows
        # drop away and only the live mouse channels show.
        used = self.keys.used
        channels = [0, 1] + [ch for ch in (2, 3) if used[ch]]
        if not (used[0] or used[1]) and (used[2] or used[3]):
            channels = [ch for ch in (2, 3) if used[ch]]
        sq = KEY_SQ * es
        gap = KEY_GAP * es
        total_h = len(channels) * sq + (len(channels) - 1) * gap
        cx = self.ui_w - KEY_MARGIN - sq / 2.0
        y0 = UI_HEIGHT / 2.0 - total_h / 2.0
        held, counts = self.keys.state_at(t)
        for row, ch in enumerate(channels):
            cy = y0 + row * (sq + gap) + sq / 2.0
            pressed = bool(held & (1 << ch))
            size = sq * (0.90 if pressed else 1.0)
            color = KEY_COLORS[ch] if pressed else KEY_IDLE
            alpha = (0.95 if pressed else 0.55) * self.op
            out.append(Sprite(cx * k, cy * k, size * k, size * k,
                              "key_square", (*color, alpha)))
            label = str(counts[ch]) if counts[ch] > 0 else KEY_LABELS[ch]
            lh = (20.0 if counts[ch] > 0 else 17.0) * es
            lw = self._run_width(label, lh, mono=True)
            while lw > sq * 0.8 and lh > 8.0:      # 4+ digit counts shrink
                lh *= 0.85
                lw = self._run_width(label, lh, mono=True)
            self._run(out, label, cx - lw / 2.0, cy - lh / 2.0, lh,
                      (1, 1, 1), (0.95 if pressed else 0.8) * self.op,
                      mono=True)

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
