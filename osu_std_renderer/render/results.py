"""Post-game results card — RED'S results screen, PORTED (not redesigned)
from the mania v2 renderer's `_draw_results_overlay`
(OsuManiaRenderer_v2/osu_mania_renderer_v2/gpu/renderer.py) so all four
in-house engines share ONE results identity:

  * the scene (HUD included) dims under a 0.7 black wash while the card
    fades in over RESULTS_FADE_IN_MS (mania's 400 ms opacity ramp);
  * a CENTRED VERTICAL STACK, top-to-bottom with fixed gaps (the mania
    `_line` cursor): grade (huge, colour-coded — here the skin's BIG
    ranking-* image when it ships one, the coloured letter otherwise),
    score (comma-grouped), accuracy, "Max combo Nx", the colour-coded
    judgment row, the UR/avg line, and the hit-offset histogram (25 bins,
    centre tick, bars coloured by timing window — mania's judgment bands
    swapped for the std 300/100/50 windows);
  * std additions ABOVE the grade (owner spec): the map line
    "Artist - Title [Diff]" and "played by <player> +MODS";
  * mania's PP line is SKIPPED — no PP model in this repo (honest gap,
    same rule as the gameplay HUD).

Text is baked ONCE at init through PIL (the mania `_cached_text`
pattern — the card's strings are final values, nothing rolls), sized in
1080-space and scaled by frame height. `renderer_default_font_and_ranks`
forces the procedural grade letter even under a skin with rank images.

The scene draws the card LAST (after the HUD — the mania draw order) from
`results_start_ms`; the CLI extends the render by results_screen_time
(behind show_results / --results, the §4.6 ShowResultsScreen keys).
"""
from __future__ import annotations

from PIL import Image, ImageDraw

from .gl import Sprite
from .hud import BAND_50, BAND_100, BAND_300, GRADE_COLORS
from .textures import _load_font

UI_H = 1080.0                 # the card's design space (mania sizes)
RESULTS_FADE_IN_MS = 400.0    # mania: opacity ramps over 400 ms
DIM_ALPHA = 0.7               # mania: scene dim under the card
CARD_TOP_FRAC = 0.10          # stack top (mania 0.12, raised for the
                              # two std header lines)
GRADE_PX = 220                # mania font sizes, 1080-space
SCORE_PX = 96
ACC_PX = 56
COMBO_PX = 40
JUDGE_PX = 36
UR_PX = 36
MAP_PX = 40
SUB_PX = 30
HIST_BINS = 25                # mania histogram shape
HIST_W_FRAC = 0.5
HIST_H_FRAC = 0.08

# judgment row colours (mania's colour-coded cells, std judgment set)
JUDGE_COLORS = {
    "300": tuple(int(c * 255) for c in BAND_300),
    "100": tuple(int(c * 255) for c in BAND_100),
    "50": tuple(int(c * 255) for c in BAND_50),
    "MISS": (240, 80, 80),
}

GRADE_BIG_ELEMENT = {         # grade → skin big ranking image (item-5 set)
    "SS": "ranking-X", "S": "ranking-S", "A": "ranking-A",
    "B": "ranking-B", "C": "ranking-C", "D": "ranking-D",
}

# osu! mod bitmask → display names (NC eats DT, PF eats SD)
_MOD_BITS = ((2, "EZ"), (1, "NF"), (256, "HT"), (8, "HD"), (16, "HR"),
             (16384, "PF"), (32, "SD"), (512, "NC"), (64, "DT"),
             (1024, "FL"), (128, "RX"), (8192, "AP"), (4096, "SO"),
             (4, "TD"), (536870912, "V2"))


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def mods_string(mods: int) -> str:
    """Comma-joined mod acronyms of an .osr mods bitmask; NC/PF absorb
    their DT/SD component bits. '' for nomod."""
    if mods & 512:
        mods &= ~64
    if mods & 16384:
        mods &= ~32
    return ",".join(name for bit, name in _MOD_BITS if mods & bit)


def histogram_bins(deltas, n_bins: int, rng_ms: float) -> list[int]:
    """The mania histogram binning: signed hit deltas clipped to
    ±rng_ms over n_bins cells (centre = on time)."""
    bins = [0] * n_bins
    if rng_ms <= 0:
        return bins
    for d in deltas:
        clipped = max(-rng_ms, min(rng_ms, d))
        idx = int((clipped + rng_ms) / (2 * rng_ms) * (n_bins - 1) + 0.5)
        bins[idx] += 1
    return bins


def _bake_text(text: str, px: int,
               color: tuple[int, int, int]):
    """One PIL-baked text line (the mania _cached_text pattern) →
    (rgba HxWx4, w, h). Empty/blank text bakes a 1×1 transparent stub."""
    import numpy as np
    font = _load_font(max(int(px * 0.95), 8))
    try:
        x0, y0, x1, y1 = font.getbbox(text)
    except AttributeError:                 # ancient PIL bitmap font
        w, h = font.getsize(text)          # type: ignore[attr-defined]
        x0, y0, x1, y1 = 0, 0, w, h
    pad = max(px // 16, 2)
    w = max(x1 - x0, 1) + 2 * pad
    h = max(y1 - y0, 1) + 2 * pad
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - x0, pad - y0), text, font=font,
                             fill=(*color, 255))
    return np.asarray(img, dtype=np.uint8).copy(), w, h


class ResultsScreen:
    """Bakes the card's lines at init, draws the stack per frame.

    counts = (c300, c100, c50, cmiss); acc_pct is the DISPLAY percentage
    (the .osr-derived round(acc·100, 2)); err_deltas are the signed hit
    deltas; meh_ms sizes the histogram range (the 50 window)."""

    def __init__(self, spr, skin_elems, *, counts, acc_pct: float,
                 score: int, max_combo: int, grade: str, ur: float,
                 avg_ms: float, err_deltas, meh_ms: float,
                 player: str = "", map_line: str = "", diff_name: str = "",
                 mods: int = 0, use_skin_ranks: bool = True):
        self.spr = spr
        self.w, self.h = float(spr.width), float(spr.height)
        self.k = self.h / UI_H
        k = self.k
        self.grade = grade

        # skin big rank image (item 5) unless the default-ranks toggle won
        self.grade_key = None
        self.grade_size = (0.0, 0.0)
        el = GRADE_BIG_ELEMENT.get(grade)
        if (use_skin_ranks and skin_elems is not None and el is not None
                and skin_elems.has(el) and el not in skin_elems.empty):
            gw, gh = skin_elems.size[el]
            m = (GRADE_PX * k) / max(gh, 1.0)
            self.grade_key = f"sk_{el}"
            self.grade_size = (gw * m, gh * m)

        # --- bake every line (final values — nothing rolls) ------------
        self._n = 0
        c300, c100, c50, cmiss = counts
        title = map_line if not diff_name else f"{map_line} [{diff_name}]"
        sub = f"played by {player}" if player else ""
        ms = mods_string(mods)
        if ms:
            sub = f"{sub} +{ms}" if sub else f"+{ms}"
        gc = GRADE_COLORS.get(grade, (0.8, 0.8, 0.85))
        grade_rgb = tuple(int(c * 255) for c in gc)

        # rows are (texture_key, w_px, h_px, gap_after_1080px)
        self._map_row = self._bake(title, MAP_PX, (235, 235, 245), 8)
        self._sub_row = self._bake(sub, SUB_PX, (200, 200, 220), 20)
        self._grade_letter = None
        if self.grade_key is None:
            self._grade_letter = self._bake(grade, GRADE_PX, grade_rgb, 18)
        self._score_row = self._bake(f"{score:,}", SCORE_PX,
                                     (255, 255, 255), 10)
        self._acc_row = self._bake(f"{acc_pct:.2f}%", ACC_PX,
                                   (235, 235, 245), 10)
        self._combo_row = self._bake(f"Max combo {max_combo}x", COMBO_PX,
                                     (200, 200, 220), 24)
        self._judge_cells = [
            self._bake(f"{lab}: {cnt}", JUDGE_PX, JUDGE_COLORS[lab], 0)
            for lab, cnt in (("300", c300), ("100", c100),
                             ("50", c50), ("MISS", cmiss))]
        self._ur_row = self._bake(f"UR {ur:.1f}   Avg {avg_ms:+.1f} ms",
                                  UR_PX, (180, 200, 220), 8)

        # histogram data (drawn as solid quads, mania style)
        self.hist_rng = max(float(meh_ms), 1.0)
        self.bins = histogram_bins(list(err_deltas), HIST_BINS,
                                   self.hist_rng)
        self.hist_peak = max(self.bins) if any(self.bins) else 0
        self._band = None      # filled by set_windows

    def set_windows(self, great_ms: float, ok_ms: float) -> None:
        """Timing windows for the histogram bar colours (std 300/100)."""
        self._band = (great_ms, ok_ms)

    def _bake(self, text: str, px: int, color, gap: int):
        if not text:
            return None
        rgba, w, h = _bake_text(text, int(px * self.k), color)
        key = f"res_{self._n}"
        self._n += 1
        self.spr.upload_texture(key, rgba)
        return (key, float(w), float(h), gap)

    # --- per-frame draw --------------------------------------------------

    def draw(self, age_ms: float) -> None:
        a = _clamp01(age_ms / RESULTS_FADE_IN_MS)
        if a <= 0.0:
            return
        out: list[Sprite] = []
        w, h, k = self.w, self.h, self.k
        cx = w / 2.0
        # scene dim (mania: 0.7 black over everything, HUD included)
        out.append(Sprite(cx, h / 2.0, w, h, None, (0, 0, 0, DIM_ALPHA * a)))

        y = h * CARD_TOP_FRAC        # stack cursor (top edge of next row)

        def line(row) -> None:
            nonlocal y
            if row is None:
                return
            key, rw, rh, gap = row
            out.append(Sprite(cx, y + rh / 2.0, rw, rh, key, (1, 1, 1, a)))
            y += rh + gap * k

        line(self._map_row)
        line(self._sub_row)
        if self.grade_key is not None:
            gw, gh = self.grade_size
            out.append(Sprite(cx, y + gh / 2.0, gw, gh, self.grade_key,
                              (1, 1, 1, a)))
            y += gh + 18 * k
        else:
            line(self._grade_letter)
        line(self._score_row)
        line(self._acc_row)
        line(self._combo_row)
        # judgment row — horizontal colour-coded cells (mania layout)
        cells = [c for c in self._judge_cells if c is not None]
        if cells:
            gap = 24.0 * k
            total = sum(c[1] for c in cells) + gap * (len(cells) - 1)
            row_h = max(c[2] for c in cells)
            x = cx - total / 2.0
            for key, rw, rh, _ in cells:
                out.append(Sprite(x + rw / 2.0, y + rh / 2.0, rw, rh, key,
                                  (1, 1, 1, a)))
                x += rw + gap
            y += row_h + 24 * k
        line(self._ur_row)
        # histogram (mania bars, std window colours)
        if self.hist_peak > 0:
            hist_w = w * HIST_W_FRAC
            hist_h = max(40.0 * k, h * HIST_H_FRAC)
            x0 = cx - hist_w / 2.0
            out.append(Sprite(cx, y + hist_h / 2.0, hist_w, hist_h, None,
                              (0.10, 0.10, 0.15, 0.6 * a)))
            cell_w = hist_w / HIST_BINS
            bar_w = max(2.0, cell_w - 2.0)
            for i, count in enumerate(self.bins):
                if count == 0:
                    continue
                centre = -self.hist_rng + ((i + 0.5)
                                           * (2 * self.hist_rng / HIST_BINS))
                absms = abs(centre)
                if self._band is not None and absms <= self._band[0]:
                    col = BAND_300
                elif self._band is not None and absms <= self._band[1]:
                    col = BAND_100
                else:
                    col = BAND_50
                bar_h = hist_h * count / self.hist_peak
                bx = x0 + i * cell_w + cell_w / 2.0
                # bars anchor at the strip base and grow UP (mania style)
                out.append(Sprite(bx, y + hist_h - bar_h / 2.0, bar_w,
                                  bar_h, None, (*col, a)))
            out.append(Sprite(cx, y + hist_h / 2.0, 2.0, hist_h, None,
                              (1, 1, 1, 0.6 * a)))
            y += hist_h + 16 * k
        self.spr.draw(out)
