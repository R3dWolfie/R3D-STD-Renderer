"""osu!lazer's BreakOverlay, ported onto the std renderer's GL sprite HUD.

This is the std-engine sibling of the catch reference implementation
(osu-catch/osu_catch_renderer/break_overlay.py, commit d8ccb60) — same
lazer semantics, this engine's drawing primitives (gl.Sprite quads +
upload_texture bakes instead of catch's CPU/PIL pastes).

Source of truth — ppy/osu master (read 2026-07-23, keep in sync):
  osu.Game/Screens/Play/BreakOverlay.cs        fade/slide timings, progress
                                               bar semantics, layout Y=±15
  osu.Game/Screens/Play/BreakTracker.cs        which breaks count (HasEffect),
                                               Period = (Start, End - FADE)
  osu.Game/Screens/Play/Break/BreakInfo.cs     "CURRENT PROGRESS" + info lines
  osu.Game/Screens/Play/Break/BreakInfoLine.cs label Yellow/value YellowLight,
                                               2px split margins, acc format
  osu.Game/Screens/Play/Break/RemainingTimeCounter.cs  ceil(ms/1000) seconds
  osu.Game/Screens/Play/Break/BreakArrows.cs   chevron pair geometry/offsets
  osu.Game/Screens/Play/Break/GlowIcon.cs      sharp icon + BlueLighter glow
  osu.Game/Screens/Play/Break/BlurredIcon.cs   blur-only + additive + a=0.7
  osu.Game/Beatmaps/Timing/BreakPeriod.cs      MIN_BREAK_DURATION = 650

The exact lazer timeline (BreakOverlay.updateDisplay, absolute from b.Start;
the tracker's Period trims BREAK_FADE_DURATION=325ms off the END, so with
D = period duration = break duration - 325):
  t'=0..325   fadeContainer.FadeIn(325) [linear]; arrows slide in (OutQuint,
              325ms); counter X -50->0 / info X +50->0 (OutQuint, 325ms);
              progress-bar CONTAINER width 0 -> 0.3 rel (OutQuint, 325ms)
  t'=0..D+325 counter counts (D+325 = full break duration) -> 0, linear;
              display = ceil(count/1000)
  every frame bar width DampContinuously(current, target, halfTime=40ms);
              target = max(0, (Period.End - now - 325) / D)  [reaches 0
              already 325ms BEFORE the fade-out starts]
  t'=D        fadeContainer.FadeOut(325); arrows slide back out (OutQuint,
              325ms); bar container width snaps to 0 — gone at t'=D+325,
              exactly the break's end.

ARROWS — deliberately NOT blinking: lazer master's BreakArrows only slide
in/out and hold (Show/Hide MoveToX, Easing.OutQuint); the "one bright one
dim" pair per side is the sharp GlowIcon (60px chevron, sigma-10
BlueLighter glow) in front of the big BlurredIcon (130px, sigma-20,
blur-only, additive, alpha 0.7). Cursor parallax has no analogue in a
fixed render and is dropped — same call as the catch reference.

VALUES — live, not snapshotted: BreakOverlay.LoadComplete BindTo()s the
ScoreProcessor's Accuracy/Rank bindables. The caller samples this engine's
own live HUD values each frame (HudData raw event accuracy + grade_at with
this ruleset's miss-caps-S-at-A rule) and hands them in; the display
percentage goes through the engine's own acc_display_value so the overlay
can never disagree with the accuracy counter next to it (stable rounds,
lazer floors — lazer replays get lazer's FormatAccuracy floor exactly).

TYPOGRAPHY — lazer draws Torus (info) + Venera numerals (counter). This
engine's stacks stand in exactly like the rest of its HUD: the bundled OFL
Nunito (ARGON_FONT_PATH, textures.py's Torus stand-in) for the text lines,
the Argon 7-segment counter cells (aseg_*) for the countdown digits — lit
cells only, no wireframe backing (that's a score-counter decoration, not
break art; catch made the same call with its Argon glyph cells).
DELIBERATE on skinned renders too: stable has no break-overlay equivalent
and the owner wants THIS lazer look on both paths (the catch decision), so
the overlay is identical lazer styling regardless of skin.

Z-ORDER: lazer's BreakOverlay is a LATER overlay-component child than
HUDOverlay (Player.createOverlayComponents), so StdHud draws it as a
SEPARATE sprite batch after the HUD batch — above every HUD sprite,
including the HUD's own additive pass. It stays under the scene's
fade-to-black / results / fail layers (scene.py draws those after
hud.draw), matching lazer's Player container order. Absent in merge/versus
renders (merge.py never builds a StdHud — no lazer analogue, the catch
decision).

Coordinates are lazer's 768-tall UI space scaled by lk = screen_h/768; the
arrows' X offsets are RELATIVE TO WIDTH (GlowIcon RelativePositionAxes =
Axes.X), matching upstream. All times are MAP-time ms (lazer runs these
transforms on the rate-adjusted FrameStableClock, which is the map
timeline — DT/HT inherently correct). Bakes and texture uploads happen
only when the map has an effective break, so no-break renders are
untouched (byte-identical, proven with framemd5).
"""
from __future__ import annotations

import math
from bisect import bisect_right

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .gl import Sprite
from .hud import LAZER_UI_HEIGHT, acc_display_value
from .textures import ARGON_FONT_PATH, ARGON_GLYPH_CAP_SCALE

# --- lazer constants (files cited in the module docstring) -------------------
MIN_BREAK_DURATION = 650.0        # BreakPeriod.MIN_BREAK_DURATION (HasEffect)
BREAK_FADE_MS = MIN_BREAK_DURATION / 2.0   # BreakOverlay.BREAK_FADE_DURATION
REMAINING_MAX_W = 0.3             # remaining_time_container_max_size
VERTICAL_MARGIN = 15.0            # BreakOverlay.vertical_margin (lazer px)
BAR_H = 8.0                       # remainingTimeBox Height
DAMP_HALF_MS = 40.0               # Interpolation.DampContinuously halfTime
SLIDE_X = 50.0                    # counter/info MoveToX slide distance

GLOW_ICON_SIZE = 60.0             # BreakArrows glow_icon_*
GLOW_ICON_SIGMA = 10.0
GLOW_ICON_FINAL = 0.22            # X offsets, RELATIVE TO WIDTH
GLOW_ICON_OFFSCREEN = 0.6
BLUR_ICON_SIZE = 130.0            # BreakArrows blurred_icon_*
BLUR_ICON_SIGMA = 20.0
BLUR_ICON_FINAL = 0.38
BLUR_ICON_OFFSCREEN = 0.7
BLUR_ICON_ALPHA = 0.7

BLUE_LIGHTER = (0xDD, 0xFF, 0xFF)   # OsuColour.BlueLighter (glow colour)
YELLOW = (0xFF, 0xCC, 0x22)         # OsuColour.Yellow (info labels)
YELLOW_LIGHT = (0xFF, 0xDD, 0x55)   # OsuColour.YellowLight (info values)
SHADOW_GRAY = (51, 51, 51)          # OsuColour.Gray(0.2f)
SHADOW_ALPHA = 0.8                  # .Opacity(0.8f)
SHADOW_RADIUS = 260.0               # EdgeEffect shadow radius (lazer px)
SHADOW_CORE_W, SHADOW_CORE_H = 80.0, 4.0   # the CircularContainer core

COUNTER_SIZE = 33.0               # RemainingTimeCounter OsuFont.Numeric 33
TITLE_SIZE = 15.0                 # "CURRENT PROGRESS" bold 15
LINE_SIZE = 17.0                  # BreakInfoLine label/value size
LINE_MARGIN = 2.0                 # BreakInfoLine margin each side of centre
FLOW_SPACING = 5.0                # BreakInfo FillFlow Spacing(5)

# BeatmapsStrings rank display text (osu-resources Localisation/Web) — the
# GradeDisplay renders ScoreRank.GetLocalisableDescription(): X="SS",
# XH="Silver SS", SH="Silver S". lazer's HD/FL AdjustRank turns X/S silver.
_HD, _FL = 1 << 3, 1 << 10

_CIRCLE_BAKE = 64                 # bake resolution of the bar cap circle


def _out_quint(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1.0 - (1.0 - u) ** 5


def grade_display(grade: str, mods: int) -> str:
    """The break overlay's Grade line text: the engine's own live grade
    (HudData.grade_at — ScoreProcessor.RankFromScore + the OsuScoreProcessor
    miss-caps-S/X-at-A override, single source for cutoffs) mapped to
    lazer's rank display strings, with the HD/FL silver adjustment lazer
    applies."""
    if mods & (_HD | _FL):
        if grade == "SS":
            return "Silver SS"
        if grade == "S":
            return "Silver S"
    return grade


class LazerBreakOverlay:
    """Stateful per-render overlay: bakes the static art once (only when
    the map has an effective break), then emits sprites during effective
    breaks (duration >= MIN_BREAK_DURATION). Frames must arrive in
    monotonic map-time order (they do — same contract as the HUD's rolling
    counters; the fail-freeze path repeats the death time, dt=0); the
    damped bar width is replayed statefully like lazer's always-running
    Update()."""

    def __init__(self, spr, bank, w: int, h: int, breaks, mods: int = 0):
        self.spr = spr
        self.bank = bank
        self.w, self.h = int(w), int(h)
        self.mods = int(mods or 0)
        self.lk = self.h / LAZER_UI_HEIGHT
        # BreakTracker.Breaks: only HasEffect breaks, Period end trimmed by
        # BREAK_FADE_DURATION. We keep (start, D) with D = period duration;
        # the overlay is on screen over [start, start + D + 325].
        self.periods = sorted(
            (float(s), float(e - s) - BREAK_FADE_MS)
            for s, e in (breaks or ())
            if (e - s) >= MIN_BREAK_DURATION)
        self._starts = [p[0] for p in self.periods]
        # DampContinuously state (remainingTimeBox.Width, RELATIVE 0..1)
        self._bar_w = 0.0
        self._last_t: float | None = None
        self._text_cache: dict = {}
        self._font_cache: dict = {}
        if not self.periods:
            return                     # no effective breaks -> never draws
        # static art, uploaded once (keys namespaced brkov_*)
        spr.upload_texture("brkov_shadow", self._bake_shadow())
        glow = self._bake_glow_icon()
        spr.upload_texture("brkov_glow_r", glow)
        spr.upload_texture("brkov_glow_l", glow[:, ::-1].copy())
        blur = self._bake_blurred_icon()
        spr.upload_texture("brkov_blur_r", blur)
        spr.upload_texture("brkov_blur_l", blur[:, ::-1].copy())
        # remainingTimeBox is a fully-rounded white Circle: cap circle baked
        # once, drawn as two half-cap sprites + a solid strip (see _bar)
        spr.upload_texture("brkov_circle", self._bake_circle())
        self._glow_canvas = glow.shape[1]      # square canvases (icon+pad)
        self._blur_canvas = blur.shape[1]

    # --- bakes ---------------------------------------------------------------

    def _bake_shadow(self) -> np.ndarray:
        """The fadeContainer's first child: an invisible 80x4 pill whose
        EdgeEffect SHADOW (radius 260, gray(0.2) @ 0.8) is the big soft dark
        blob behind the centre block. Approximated as a quadratic falloff
        over the radius from the pill edge (o!f edge-effect profile) —
        identical math to the catch reference."""
        lk = self.lk
        R = SHADOW_RADIUS * lk
        cw, ch = SHADOW_CORE_W * lk, SHADOW_CORE_H * lk
        W = int(math.ceil(cw + 2 * R))
        H = int(math.ceil(ch + 2 * R))
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = ch / 2.0                      # pill corner radius
        qx = np.abs(xx - W / 2.0) - (cw / 2.0 - r)
        qy = np.abs(yy - H / 2.0) - (ch / 2.0 - r)
        d = (np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
             + np.minimum(np.maximum(qx, qy), 0.0) - r)
        fall = np.clip(1.0 - d / R, 0.0, 1.0) ** 2
        rgba = np.zeros((H, W, 4), np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = SHADOW_GRAY
        rgba[..., 3] = np.round(fall * SHADOW_ALPHA * 255.0).astype(np.uint8)
        return rgba

    def _chevron_mask(self, size_px: int) -> Image.Image:
        """FontAwesome Solid.ChevronRight silhouette: a bold '>' polyline
        (glyph aspect ~0.63 in a square SpriteIcon cell), round caps/joint."""
        s = size_px
        m = Image.new("L", (s, s), 0)
        d = ImageDraw.Draw(m)
        w = max(2, int(round(s * 0.17)))
        pts = [(0.36 * s, 0.14 * s), (0.67 * s, 0.50 * s),
               (0.36 * s, 0.86 * s)]
        d.line(pts, fill=255, width=w, joint="curve")
        for px, py in (pts[0], pts[2]):
            d.ellipse([px - w / 2, py - w / 2, px + w / 2, py + w / 2],
                      fill=255)
        return m

    def _bake_glow_icon(self) -> np.ndarray:
        """GlowIcon: sharp white chevron over its BlueLighter gaussian glow
        (GlowingDrawable: blurred silhouette tinted GlowColour, original on
        top). Right-pointing; the left pair mirrors the bake."""
        lk = self.lk
        s = max(4, int(round(GLOW_ICON_SIZE * lk)))
        sigma = GLOW_ICON_SIGMA * lk
        pad = int(math.ceil(3 * sigma)) + 1
        cv = Image.new("L", (s + 2 * pad, s + 2 * pad), 0)
        cv.paste(self._chevron_mask(s), (pad, pad))
        glow_a = cv.filter(ImageFilter.GaussianBlur(sigma))
        glow = Image.new("RGBA", cv.size, BLUE_LIGHTER + (0,))
        glow.putalpha(glow_a)
        sharp = Image.new("RGBA", cv.size, (255, 255, 255, 0))
        sharp.putalpha(cv)
        return np.asarray(Image.alpha_composite(glow, sharp))

    def _bake_blurred_icon(self) -> np.ndarray:
        """BlurredIcon: blur-only (DrawOriginal=false), additive, alpha 0.7.
        Baked as RGBA with rgb=BlueLighter, a=blur*0.7 — the additive sprite
        pass blends dst + rgb*a (gl.py SRC_ALPHA/ONE), which reproduces the
        catch reference's premultiplied additive field exactly."""
        lk = self.lk
        s = max(4, int(round(BLUR_ICON_SIZE * lk)))
        sigma = BLUR_ICON_SIGMA * lk
        pad = int(math.ceil(3 * sigma)) + 1
        cv = Image.new("L", (s + 2 * pad, s + 2 * pad), 0)
        cv.paste(self._chevron_mask(s), (pad, pad))
        a = np.asarray(cv.filter(ImageFilter.GaussianBlur(sigma)),
                       np.float32) * BLUR_ICON_ALPHA
        rgba = np.zeros(cv.size[::-1] + (4,), np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2] = BLUE_LIGHTER
        rgba[..., 3] = np.round(a).astype(np.uint8)
        return rgba

    def _bake_circle(self) -> np.ndarray:
        """A white antialiased circle (supersampled), the bar's round caps."""
        ss = 4
        s = _CIRCLE_BAKE * ss
        im = Image.new("L", (s, s), 0)
        ImageDraw.Draw(im).ellipse([0, 0, s - 1, s - 1], fill=255)
        im = im.resize((_CIRCLE_BAKE, _CIRCLE_BAKE), Image.LANCZOS)
        rgba = np.full((_CIRCLE_BAKE, _CIRCLE_BAKE, 4), 255, np.uint8)
        rgba[..., 3] = np.asarray(im)
        return rgba

    # --- text (bundled Nunito, the engine's Torus stand-in) ------------------

    def _font(self, size_l: float, weight: int) -> ImageFont.FreeTypeFont:
        key = (size_l, weight)
        f = self._font_cache.get(key)
        if f is None:
            px = max(6, int(round(size_l * ARGON_GLYPH_CAP_SCALE * self.lk)))
            f = ImageFont.truetype(ARGON_FONT_PATH, px)
            try:
                f.set_variation_by_axes([weight])
            except Exception:  # noqa: BLE001 — non-variable build
                pass
            self._font_cache[key] = f
        return f

    def _text(self, text: str, size_l: float, weight: int,
              color) -> tuple[str, int, int]:
        """A text run baked to an uploaded texture, colour baked in (sprite
        colour stays white; alpha rides the sprite). Canvas height = the
        font's full line box (ascent+descent) so sprites centre like o!f's
        line anchoring. Returns (texture_key, w_px, h_px)."""
        key = (text, size_l, weight, color)
        hit = self._text_cache.get(key)
        if hit is None:
            f = self._font(size_l, weight)
            x0, _, x1, _ = f.getbbox(text)
            try:
                asc, desc = f.getmetrics()
            except AttributeError:
                asc, desc = f.getbbox("Ag")[3], 0
            im = Image.new("RGBA", (max(1, x1 - x0), max(1, asc + desc)),
                           color + (0,))
            ImageDraw.Draw(im).text((-x0, 0), text, font=f,
                                    fill=color + (255,))
            tkey = f"brkov_txt_{len(self._text_cache)}"
            self.spr.upload_texture(tkey, np.asarray(im))
            hit = (tkey, im.width, im.height)
            self._text_cache[key] = hit
        return hit

    # --- per-frame -----------------------------------------------------------

    def _bar(self, out: list, cx: float, cy: float, bw: int, bh: int,
             alpha: float) -> None:
        """remainingTimeBox: a white fully-rounded Circle, h = min(8, w) —
        two half-cap sprites of the baked circle + a solid centre strip
        (solid quad, texture_key None). At w <= h it IS a circle."""
        col = (1.0, 1.0, 1.0, alpha)
        if bw <= bh:
            out.append(Sprite(cx, cy, float(bw), float(bh),
                              "brkov_circle", col))
            return
        half = _CIRCLE_BAKE / 2.0
        # left cap: left half of the circle texture (uv 0..0.5)
        out.append(Sprite(cx - bw / 2.0 + bh / 4.0, cy, bh / 2.0, float(bh),
                          "brkov_circle", col,
                          uv_scale=(0.5, 1.0)))
        out.append(Sprite(cx + bw / 2.0 - bh / 4.0, cy, bh / 2.0, float(bh),
                          "brkov_circle", col,
                          uv_off=(0.5, 0.0), uv_scale=(0.5, 1.0)))
        out.append(Sprite(cx, cy, float(bw - bh), float(bh), None, col))

    def draw(self, t_ms: float, accuracy: float,
             grade: str, lazer_display: bool = False) -> None:
        """Draw the overlay for map time t_ms as its own sprite batches,
        AFTER the HUD batch (lazer's overlay z-order). Called every frame
        (the bar damp runs continuously, like lazer's Update); cheap no-op
        outside break windows — no GL calls at all, so no-break frames are
        byte-identical. `accuracy` (0..1 raw ratio) and `grade` are the
        engine's LIVE HUD values at t (HudData — bound like lazer's
        bindables, not snapshotted).

        Two batches: [shadow, bar, counter, info, ADDITIVE blurred icons]
        then [glow icons] — the sharp GlowIcons must alpha-composite OVER
        the already-added blur field (lazer child order: BlurredIcon behind
        GlowIcon), and gl.py's single-batch two-phase order would draw the
        additive pass last."""
        if not self.periods:
            return
        t = float(t_ms)
        dt = 16.7 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t

        # active period: overlay lives over [start, start + D + FADE]
        idx = bisect_right(self._starts, t) - 1
        cur = None
        if idx >= 0:
            s0, D = self.periods[idx]
            if t <= s0 + D + BREAK_FADE_MS:
                cur = (s0, D)

        # remainingTimeBox.Width — DampContinuously toward
        # max(0, (Period.End - now - FADE) / D), EVERY frame, in/out of breaks
        if cur is None:
            target = 0.0
        else:
            s0, D = cur
            target = max(0.0, (s0 + D - t - BREAK_FADE_MS) / D) if D > 0 else 0.0
        self._bar_w = target + (self._bar_w - target) * (0.5 ** (dt / DAMP_HALF_MS))

        if cur is None:
            return
        s0, D = cur
        tp = t - s0                       # time since break start
        # fadeContainer alpha: linear FadeIn/FadeOut over BREAK_FADE_MS
        if tp >= D:
            alpha = max(0.0, 1.0 - (tp - D) / BREAK_FADE_MS)
        else:
            alpha = min(1.0, tp / BREAK_FADE_MS)
        if alpha <= 0.004:
            return

        out: list[Sprite] = []
        lk = self.lk
        cx, cy = self.w / 2.0, self.h / 2.0
        p_in = _out_quint(tp / BREAK_FADE_MS)

        # 1) shadow blob (first fadeContainer child)
        out.append(Sprite(cx, cy,
                          float(math.ceil(SHADOW_CORE_W * lk
                                          + 2 * SHADOW_RADIUS * lk)),
                          float(math.ceil(SHADOW_CORE_H * lk
                                          + 2 * SHADOW_RADIUS * lk)),
                          "brkov_shadow", (1.0, 1.0, 1.0, alpha)))

        # 2) progress bar: container width 0 -> 0.3 (OutQuint, 325ms), snap
        #    to 0 at t'=D; pill width rides the damped fraction
        wc = REMAINING_MAX_W * p_in if tp < D else 0.0
        bw = int(round(wc * self.w * max(0.0, min(1.0, self._bar_w))))
        if bw >= 2:
            bh = max(1, int(round(min(BAR_H * lk, bw))))
            self._bar(out, cx, cy, bw, bh, alpha)

        # 3) remaining-time counter: ceil(count/1000); count runs linearly
        #    from the FULL break duration to 0 at the break's end. Digits =
        #    the engine's Argon 7-segment counter cells (aseg_*), lit only.
        count = max(0.0, (D + BREAK_FADE_MS) - tp)
        text = str(int(math.ceil(count / 1000.0)))
        dh = COUNTER_SIZE * lk
        cw = self.bank.argon_seg_advance * dh
        dx = -SLIDE_X * lk * (1.0 - p_in)          # MoveToX(-50 -> 0)
        run_w = cw * len(text)
        left = cx + dx - run_w / 2.0
        dcy = cy - VERTICAL_MARGIN * lk - dh / 2.0
        for i, ch in enumerate(text):
            out.append(Sprite(left + (i + 0.5) * cw, dcy, cw, dh,
                              f"aseg_{ch}", (1.0, 1.0, 1.0, alpha)))

        # 4) BreakInfo (slides +50 -> 0): title, then Accuracy / Grade lines
        #    split 2px either side of centre; values LIVE like lazer's
        #    bindables (constant mid-break in practice). The display
        #    percentage uses the engine's own acc_display_value so the
        #    overlay always shows the digits the accuracy counter shows
        #    (stable rounds; lazer replays get FormatAccuracy's floor).
        dxi = SLIDE_X * lk * (1.0 - p_in)
        cxi = cx + dxi
        y0 = cy + VERTICAL_MARGIN * lk
        tk, tw, th = self._text("CURRENT PROGRESS", TITLE_SIZE, 700,
                                (255, 255, 255))
        out.append(Sprite(cxi, y0 + TITLE_SIZE * lk / 2.0,
                          float(tw), float(th), tk, (1.0, 1.0, 1.0, alpha)))
        acc = max(0.0, min(1.0, float(accuracy)))
        acc_txt = f"{acc_display_value(acc, lazer_display):.2f}%"
        rows = [("Accuracy", acc_txt),
                ("Grade", grade_display(grade, self.mods))]
        ly = y0 + (TITLE_SIZE + FLOW_SPACING) * lk
        for label, value in rows:
            mid = ly + LINE_SIZE * lk / 2.0
            lkey, lw, lh = self._text(label, LINE_SIZE, 400, YELLOW)
            vkey, vw, vh = self._text(value, LINE_SIZE, 700, YELLOW_LIGHT)
            out.append(Sprite(cxi - LINE_MARGIN * lk - lw / 2.0, mid,
                              float(lw), float(lh), lkey,
                              (1.0, 1.0, 1.0, alpha)))
            out.append(Sprite(cxi + LINE_MARGIN * lk + vw / 2.0, mid,
                              float(vw), float(vh), vkey,
                              (1.0, 1.0, 1.0, alpha)))
            ly += LINE_SIZE * lk

        # 5) arrows, topmost: slide in over the fade (OutQuint), hold, slide
        #    back out from t'=D. X offsets are fractions of the WIDTH.
        if tp >= D:
            po = _out_quint((tp - D) / BREAK_FADE_MS)
            g_off = GLOW_ICON_FINAL + (GLOW_ICON_OFFSCREEN - GLOW_ICON_FINAL) * po
            b_off = BLUR_ICON_FINAL + (BLUR_ICON_OFFSCREEN - BLUR_ICON_FINAL) * po
        else:
            g_off = GLOW_ICON_OFFSCREEN + (GLOW_ICON_FINAL - GLOW_ICON_OFFSCREEN) * p_in
            b_off = BLUR_ICON_OFFSCREEN + (BLUR_ICON_FINAL - BLUR_ICON_OFFSCREEN) * p_in
        # origins CentreRight/CentreLeft: the offset is the icon's inner
        # LAYOUT edge (AutoSize box = the 60/130px icon; the glow/blur
        # overhang is draw-only, like o!f's inflated draw quad) -> shift
        # each sprite centre outward by half the LAYOUT size, not the
        # padded canvas
        g_half = GLOW_ICON_SIZE * lk / 2.0
        b_half = BLUR_ICON_SIZE * lk / 2.0
        bs = float(self._blur_canvas)
        gs = float(self._glow_canvas)
        col = (1.0, 1.0, 1.0, alpha)
        out.append(Sprite(cx - b_off * self.w - b_half, cy, bs, bs,
                          "brkov_blur_r", col, additive=True))
        out.append(Sprite(cx + b_off * self.w + b_half, cy, bs, bs,
                          "brkov_blur_l", col, additive=True))
        top = [Sprite(cx - g_off * self.w - g_half, cy, gs, gs,
                      "brkov_glow_r", col),
               Sprite(cx + g_off * self.w + g_half, cy, gs, gs,
                      "brkov_glow_l", col)]
        self.spr.draw(out)
        self.spr.draw(top)
