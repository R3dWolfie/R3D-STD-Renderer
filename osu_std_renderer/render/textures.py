"""Procedural "Argon-ish" default textures — the taiko `argon/` playbook
(PIL/numpy-baked, NO ppy asset files) applied to std.

Everything the Phase-1 scene draws is baked here at init:

  disc          filled hit-circle disc, near-white with a subtle radial
                lift toward the rim so combo-colour tinting reads with a
                little depth (tint = multiply, gl.py's u_color)
  ring          the hitcircleoverlay stand-in: a white border ring whose
                thickness fraction (0.128 of the radius) matches the
                slider-body border (slider_body.BASE_BORDER_PORTION x2
                visual family) so circles and slider borders read as one
                skin
  approach      thinner white ring for the approach circle (tinted combo)
  glow          radial-falloff dot (cursor glow / trail)
  digit_0..9    combo-number glyphs, DejaVu Sans Bold (system font; the
                repo bundles no fonts — same decision the taiko engine
                took before Torus landed), baked white for tinting
  miss_x        AA diagonal cross for the miss judgment popup (tinted red
                by the scene — the ruleset phase's judgment sprites)
  dot           small filled AA disc — the sliderscorepoint (slider tick)
                and followpoint procedural stand-in (white, untinted)
  arrow         right-pointing solid arrow — the reversearrow stand-in
                (white; the scene rotates it along the path tangent)

HUD PHASE additions (render/hud.py consumes these; §4.6 elements, drawn
in the §5.3 virtual-1080p UI space):

  glyph_<ch>    the HUD text set (mania text.py's PIL pattern, but baked
                ONCE per glyph instead of per-string): digits + the
                punctuation/letters the HUD composes (".%x,KMSABCDUR").
                All share one vertical extent so runs baseline-align;
                digit advance is uniform (mono) so the score roll and UR
                readout never jitter.
  key_square    rounded-square key-overlay cell (border + faint fill)
  vignette      edge-weighted red-pulse mask (combo-break feedback; the
                mania full-frame wash, reshaped to an edge vignette)
  pie_00..N-1   progress-pie fill masks (§4.6 Score.ProgressBar "Pie"),
                quantized fractions; a full pie reuses `disc`
  pie_ring      thin outline ring around the progress pie
  tri_down      small downward triangle (hit-error moving-average arrow)

All textures are white/greyscale so the sprite tint does the colouring —
the §3.2 default ComboColors rotate per combo set at draw time.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DISC_SIZE = 256
RING_SIZE = 256
APPROACH_SIZE = 512          # drawn up to 4x scale (§2.5) — bake bigger
GLOW_SIZE = 128
DIGIT_HEIGHT = 128

RING_THICKNESS = 0.128       # of radius; matches lazer BORDER_PORTION
APPROACH_THICKNESS = 0.09
_AA_PX = 1.5                 # texture-space anti-alias band

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
)


def _dist_grid(size: int) -> np.ndarray:
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    return np.hypot(xx - c, yy - c)


def bake_disc(size: int = DISC_SIZE, inner_shade: float = 0.88) -> np.ndarray:
    """Filled disc, AA edge; radial shade lifts rim to full white so a
    combo tint keeps a bright edge (flat tints look like paper cutouts)."""
    d = _dist_grid(size)
    radius = size / 2.0 - 2.0
    alpha = np.clip((radius - d) / _AA_PX, 0.0, 1.0)
    shade = inner_shade + (1.0 - inner_shade) * np.clip(d / radius, 0.0, 1.0)
    rgba = np.empty((size, size, 4), dtype=np.uint8)
    grey = np.round(shade * 255.0).astype(np.uint8)
    rgba[..., 0] = grey
    rgba[..., 1] = grey
    rgba[..., 2] = grey
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_ring(size: int = RING_SIZE,
              thickness_frac: float = RING_THICKNESS) -> np.ndarray:
    """White annulus (border inward from the texture edge), AA both edges."""
    d = _dist_grid(size)
    radius = size / 2.0 - 2.0
    thickness = max(radius * thickness_frac, 2.0)
    outer = np.clip((radius - d) / _AA_PX, 0.0, 1.0)
    inner = np.clip((d - (radius - thickness)) / _AA_PX, 0.0, 1.0)
    alpha = outer * inner
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_glow(size: int = GLOW_SIZE, power: float = 2.2) -> np.ndarray:
    """Soft radial falloff dot for the cursor glow / trail (additive)."""
    d = _dist_grid(size)
    radius = size / 2.0 - 1.0
    alpha = np.clip(1.0 - d / radius, 0.0, 1.0) ** power
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_miss_x(size: int = 128, thickness: float = 0.16,
                arm: float = 0.42) -> np.ndarray:
    """White AA diagonal cross (the miss popup, tinted red at draw time).
    thickness/arm are fractions of the texture size."""
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    u, v = xx - c, yy - c
    inv_sqrt2 = 1.0 / math.sqrt(2.0)
    d1 = np.abs(u - v) * inv_sqrt2          # distance to the / diagonal
    d2 = np.abs(u + v) * inv_sqrt2          # distance to the \ diagonal
    half_t = size * thickness / 2.0
    bar1 = np.clip((half_t - d1) / _AA_PX, 0.0, 1.0)
    bar2 = np.clip((half_t - d2) / _AA_PX, 0.0, 1.0)
    reach = np.clip((size * arm - np.hypot(u, v)) / _AA_PX, 0.0, 1.0)
    alpha = np.maximum(bar1, bar2) * reach
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_dot(size: int = 64) -> np.ndarray:
    """Small filled white disc, AA edge (slider tick / followpoint dot)."""
    d = _dist_grid(size)
    radius = size / 2.0 - 2.0
    alpha = np.clip((radius - d) / _AA_PX, 0.0, 1.0)
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_arrow(size: int = 256) -> np.ndarray:
    """White right-pointing arrow (shaft + head), supersampled AA — the
    reversearrow fallback, sized to sit inside the end circle when drawn
    at the circle diameter."""
    s4 = size * 4
    pts = [(0.16, 0.40), (0.50, 0.40), (0.50, 0.24), (0.84, 0.50),
           (0.50, 0.76), (0.50, 0.60), (0.16, 0.60)]
    img = Image.new("RGBA", (s4, s4), (0, 0, 0, 0))
    ImageDraw.Draw(img).polygon([(x * s4, y * s4) for x, y in pts],
                                fill=(255, 255, 255, 255))
    img = img.resize((size, size), Image.LANCZOS)   # cheap supersampled AA
    return np.asarray(img, dtype=np.uint8).copy()


def _load_font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for cand in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, px)
        except OSError:
            continue
    return ImageFont.load_default()  # bitmap fallback; blurry but functional


def bake_glyphs(chars: str, height: int = DIGIT_HEIGHT) -> dict[str, np.ndarray]:
    """Each char as a white RGBA glyph, all sharing one vertical extent
    (union of the glyph bboxes) so runs baseline-align when centred."""
    font = _load_font(int(height * 0.95))
    boxes = {}
    for ch in chars:
        try:
            boxes[ch] = font.getbbox(ch)
        except AttributeError:  # ancient PIL bitmap font
            w, h = font.getsize(ch)  # type: ignore[attr-defined]
            boxes[ch] = (0, 0, w, h)
    top = min(b[1] for b in boxes.values())
    bottom = max(b[3] for b in boxes.values())
    pad = 4
    out: dict[str, np.ndarray] = {}
    for ch in chars:
        x0, _, x1, _ = boxes[ch]
        w = (x1 - x0) + 2 * pad
        h = (bottom - top) + 2 * pad
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((pad - x0, pad - top), ch, font=font,
                                 fill=(255, 255, 255, 255))
        out[ch] = np.asarray(img, dtype=np.uint8).copy()
    return out


def bake_digits(height: int = DIGIT_HEIGHT) -> dict[str, np.ndarray]:
    """0-9 as white RGBA glyphs (the combo-number set; bake_glyphs with the
    original digit-only vertical extent)."""
    return bake_glyphs("0123456789", height)


# --- HUD textures (render/hud.py) ------------------------------------------------

# full uppercase set: HUD labels (ACCURACY/COMBO), key names, grades,
# SPIN!/CLEAR!/RPM, UR, time readouts ("-0:00")
HUD_CHARSET = "0123456789.%x,:-!ABCDEFGHIJKLMNOPQRSTUVWXYZ"
PIE_STEPS = 48          # quantized progress-pie fill masks
PIE_SIZE = 96
KEY_SQUARE_SIZE = 128
VIGNETTE_SIZE = 256


def bake_key_square(size: int = KEY_SQUARE_SIZE, radius_frac: float = 0.18,
                    border_frac: float = 0.07,
                    fill_alpha: float = 0.28) -> np.ndarray:
    """Rounded square: solid AA border + faint interior fill. White — the
    HUD tints it (idle grey / pressed key colour)."""
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    half = size / 2.0 - 2.0
    r = radius_frac * size
    # signed distance to a rounded square (box SDF)
    qx = np.abs(xx - c) - (half - r)
    qy = np.abs(yy - c) - (half - r)
    d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) \
        + np.minimum(np.maximum(qx, qy), 0.0) - r
    border = border_frac * size
    inside = np.clip(-d / _AA_PX, 0.0, 1.0)
    in_border = inside * np.clip((d + border) / _AA_PX, 0.0, 1.0)
    alpha = np.maximum(in_border, inside * fill_alpha)
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_vignette(size: int = VIGNETTE_SIZE, inner: float = 0.52,
                  outer: float = 1.0, power: float = 1.6) -> np.ndarray:
    """Edge-weighted mask: transparent centre, alpha ramping toward the
    frame edges/corners (distance normalized by the half-diagonal). Drawn
    full-screen with a red tint for the combo-break pulse."""
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    d = np.hypot(xx - c, yy - c) / (c * math.sqrt(2.0))
    alpha = np.clip((d - inner) / (outer - inner), 0.0, 1.0) ** power
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_pie(fraction: float, size: int = PIE_SIZE) -> np.ndarray:
    """Pie-fill mask: disc sector from 12 o'clock, clockwise, covering
    `fraction` of the turn. Radial edge AA'd; the angular edge is hard
    (quantized steps at PIE_STEPS are well under a frame's worth of
    progress drift at any sane fps)."""
    c = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float64)
    dx, dy = xx - c, yy - c
    radius = size / 2.0 - 2.0
    disc = np.clip((radius - np.hypot(dx, dy)) / _AA_PX, 0.0, 1.0)
    # angle from the top (12 o'clock), clockwise, in turns [0, 1)
    phi = (np.arctan2(dx, -dy) / (2.0 * math.pi)) % 1.0
    alpha = disc * (phi <= max(fraction, 1e-9))
    rgba = np.full((size, size, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


# --- Argon HUD bakes (skinless default — ppy/osu Argon* components) -----------------

def bake_pill(width: int = 256, height: int = 64) -> np.ndarray:
    """Fully-rounded white bar (radius = height/2). Generic rounded-rect
    stand-in for the Argon boxes/bars/indicators (BoxElement with
    CornerRadius 0.5, ArgonSongProgressBar's RoundedBar, the ArgonKeyCounter
    input indicator). Scaled non-uniformly at draw time — the cap
    distortion is invisible at HUD bar sizes."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    r = height / 2.0 - 1.0
    qx = np.abs(xx - (width - 1) / 2.0) - (width / 2.0 - 1.0 - r)
    qy = np.abs(yy - (height - 1) / 2.0) - (height / 2.0 - 1.0 - r)
    d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) \
        + np.minimum(np.maximum(qx, qy), 0.0) - r
    alpha = np.clip(-d / _AA_PX, 0.0, 1.0)
    rgba = np.full((height, width, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


def bake_wireframe_cell(width: int = 132, height: int = 240,
                        seg_frac: float = 0.14,
                        gap_frac: float = 0.045) -> np.ndarray:
    """One 7-segment "wireframe" digit cell — the segmented '8' the Argon
    counters draw behind their digits (ArgonCounterTextComponent's
    `argon-counter-wireframes` texture, WireframeOpacity 0.25). Procedural
    stand-in, white; the HUD tints/fades it."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    t = seg_frac * width                   # segment thickness
    gap = gap_frac * height
    m = t * 0.75                           # cell inset
    x0, x1 = m, width - m
    y0, ym, y1 = m, height / 2.0, height - m

    def hseg(cy: float) -> np.ndarray:
        """Horizontal segment at row cy (hexagonal-ish rounded bar)."""
        qx = np.abs(xx - width / 2.0) - (x1 - x0 - 2 * t - 2 * gap) / 2.0
        qy = np.abs(yy - cy) - t / 2.0
        d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) \
            + np.minimum(np.maximum(qx, qy), 0.0) - t * 0.18
        return np.clip(-d / _AA_PX, 0.0, 1.0)

    def vseg(cx: float, cy: float, half: float) -> np.ndarray:
        qx = np.abs(xx - cx) - t / 2.0
        qy = np.abs(yy - cy) - (half - t / 2.0 - gap)
        d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) \
            + np.minimum(np.maximum(qx, qy), 0.0) - t * 0.18
        return np.clip(-d / _AA_PX, 0.0, 1.0)

    half_v = (ym - y0) / 2.0
    alpha = hseg(y0 + t / 2.0)
    alpha = np.maximum(alpha, hseg(ym))
    alpha = np.maximum(alpha, hseg(y1 - t / 2.0))
    for cx in (x0 + t / 2.0, x1 - t / 2.0):
        alpha = np.maximum(alpha, vseg(cx, (y0 + ym) / 2.0, half_v))
        alpha = np.maximum(alpha, vseg(cx, (ym + y1) / 2.0, half_v))
    rgba = np.full((height, width, 4), 255, dtype=np.uint8)
    rgba[..., 3] = np.round(alpha * 255.0).astype(np.uint8)
    return rgba


WEDGE_W = 380.0                 # ArgonWedgePiece size in the default layout
WEDGE_H = 72.0
WEDGE_SHEAR = 0.8               # osu!framework Shear = (0.8, 0): x' = x - 0.8y
WEDGE_R = 10.0                  # CornerRadius (pre-shear space)
WEDGE_COLOR = (0x66, 0xCC, 0xFF)   # AccentColour #66CCFF


def bake_wedge(scale: float = 1.0) -> np.ndarray:
    """ArgonWedgePiece: a rounded rect sheared by (0.8, 0) with a vertical
    gradient of #66CCFF from alpha 0 (top) to 0.25 (bottom). Baked in the
    SHEARED frame (canvas width = W + 0.8·H) so the sprite draws axis-
    aligned; masking (corner radius) applies pre-shear, as upstream."""
    w = int(round((WEDGE_W + WEDGE_SHEAR * WEDGE_H) * scale))
    h = int(round(WEDGE_H * scale))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64) / scale
    # unshear: canvas x = u - 0.8·y + 0.8·H  (u = pre-shear x)
    u = xx + WEDGE_SHEAR * yy - WEDGE_SHEAR * WEDGE_H
    r = WEDGE_R
    qx = np.abs(u - WEDGE_W / 2.0) - (WEDGE_W / 2.0 - r)
    qy = np.abs(yy - WEDGE_H / 2.0) - (WEDGE_H / 2.0 - r)
    d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) \
        + np.minimum(np.maximum(qx, qy), 0.0) - r
    shape = np.clip(-d / (_AA_PX / scale), 0.0, 1.0)
    grad = 0.25 * np.clip(yy / WEDGE_H, 0.0, 1.0)
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = WEDGE_COLOR[0]
    rgba[..., 1] = WEDGE_COLOR[1]
    rgba[..., 2] = WEDGE_COLOR[2]
    rgba[..., 3] = np.round(shape * grad * 255.0).astype(np.uint8)
    return rgba


# --- legacy-default HUD bakes (custom-skin fallback — classic osu! look) ------------
#
# Under a CUSTOM skin, missing elements fall back to the CLASSIC default
# skin's look (lazer's legacy-skin fallback chain), NEVER to Argon. These
# are procedural approximations of the classic assets (house pattern, no
# ppy asset files): the classic score/combo font (white digits with a dark
# outline), the classic scorebar (dark frame + warm green fill + round ki
# marker — LegacyHealthDisplay's old-style pieces), and the classic input
# overlay (translucent strip + light rounded key — LegacyKeyCounter[Display]).

LEGACY_FONT_HEIGHT = 45.0       # classic score-*.png logical height (768-space)
LEGACY_BAR_BG_SIZE = (695.0, 44.0)   # classic scorebar-bg logical size
LEGACY_BAR_FILL_SIZE = (672.0, 16.0)
LEGACY_KI_SIZE = 24.0
LEGACY_IO_KEY_SIZE = 43.0       # classic inputoverlay-key logical size
LEGACY_IO_BG_SIZE = (200.0, 50.0)


def bake_legacy_font(chars: str = "0123456789.,%x",
                     height: int = 128) -> dict[str, np.ndarray]:
    """Classic score-font stand-in: bold white glyphs with a dark outline
    (the default skin score-0..9/dot/comma/percent/x look). Used whenever
    a custom skin lacks its ScorePrefix/ComboPrefix set (or single chars
    of it — stable falls back per-file to the default skin)."""
    font = _load_font(int(height * 0.9))
    boxes = {}
    for ch in chars:
        try:
            boxes[ch] = font.getbbox(ch)
        except AttributeError:
            w, h = font.getsize(ch)  # type: ignore[attr-defined]
            boxes[ch] = (0, 0, w, h)
    top = min(b[1] for b in boxes.values())
    bottom = max(b[3] for b in boxes.values())
    pad = 8
    outline = max(2, height // 24)
    out: dict[str, np.ndarray] = {}
    for ch in chars:
        x0, _, x1, _ = boxes[ch]
        w = (x1 - x0) + 2 * pad
        h = (bottom - top) + 2 * pad
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        drw = ImageDraw.Draw(img)
        try:
            drw.text((pad - x0, pad - top), ch, font=font,
                     fill=(255, 255, 255, 255),
                     stroke_width=outline, stroke_fill=(40, 40, 48, 255))
        except TypeError:      # ancient PIL without stroke support
            drw.text((pad - x0, pad - top), ch, font=font,
                     fill=(255, 255, 255, 255))
        out[ch] = np.asarray(img, dtype=np.uint8).copy()
    return out


def _rounded_rect_alpha(w: int, h: int, radius: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    qx = np.abs(xx - (w - 1) / 2.0) - (w / 2.0 - 1.0 - radius)
    qy = np.abs(yy - (h - 1) / 2.0) - (h / 2.0 - 1.0 - radius)
    d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0)) \
        + np.minimum(np.maximum(qx, qy), 0.0) - radius
    return d


def bake_legacy_scorebar_bg() -> np.ndarray:
    """Classic scorebar-bg stand-in: a dark translucent panel with a light
    rim around the fill groove (groove at the LegacyOldStyleFill offset
    (3,10)·1.6 so the classic fill lines up)."""
    w, h = int(LEGACY_BAR_BG_SIZE[0]), int(LEGACY_BAR_BG_SIZE[1])
    d = _rounded_rect_alpha(w, h, 10.0)
    panel = np.clip(-d / _AA_PX, 0.0, 1.0)
    rim = panel * np.clip((d + 2.5) / _AA_PX, 0.0, 1.0)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    base = panel * 0.55
    rgba[..., 0] = np.round(24 * panel + 200 * rim).astype(np.uint8)
    rgba[..., 1] = np.round(26 * panel + 205 * rim).astype(np.uint8)
    rgba[..., 2] = np.round(34 * panel + 215 * rim).astype(np.uint8)
    rgba[..., 3] = np.round(np.maximum(base, rim * 0.9) * 255).astype(np.uint8)
    return rgba


def bake_legacy_scorebar_colour() -> np.ndarray:
    """Classic scorebar-colour stand-in: the warm green-yellow fill bar."""
    w, h = int(LEGACY_BAR_FILL_SIZE[0]), int(LEGACY_BAR_FILL_SIZE[1])
    yy = np.mgrid[0:h, 0:w][0].astype(np.float64) / max(h - 1, 1)
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    top = np.array([214, 242, 120], dtype=np.float64)
    bot = np.array([158, 208, 60], dtype=np.float64)
    for c in range(3):
        rgba[..., c] = np.round(top[c] + (bot[c] - top[c]) * yy).astype(np.uint8)
    rgba[..., 3] = 255
    return rgba


def bake_legacy_ki(tint: tuple[int, int, int] = (255, 255, 255)) -> np.ndarray:
    """Classic ki marker stand-in: filled disc + ring, optionally tinted
    (kidanger → amber, kidanger2 → red)."""
    size = 96
    d = _dist_grid(size)
    radius = size / 2.0 - 2.0
    disc = np.clip((radius - d) / _AA_PX, 0.0, 1.0)
    ring = disc * np.clip((d - radius * 0.72) / _AA_PX, 0.0, 1.0)
    rgba = np.empty((size, size, 4), dtype=np.uint8)
    for c in range(3):
        inner = tint[c] * 0.92
        rgba[..., c] = np.round(inner + (255 - inner) * ring).astype(np.uint8)
    rgba[..., 3] = np.round(disc * 255.0).astype(np.uint8)
    return rgba


def bake_legacy_io_key() -> np.ndarray:
    """Classic inputoverlay-key stand-in: light rounded square with a soft
    border (tinted yellow/pink by the display while pressed)."""
    size = 128
    d = _rounded_rect_alpha(size, size, size * 0.22)
    inside = np.clip(-d / _AA_PX, 0.0, 1.0)
    border = inside * np.clip((d + size * 0.055) / _AA_PX, 0.0, 1.0)
    rgba = np.empty((size, size, 4), dtype=np.uint8)
    grey = 226 - 46 * border
    for c in range(3):
        rgba[..., c] = np.round(grey).astype(np.uint8)
    rgba[..., 3] = np.round(inside * 255.0).astype(np.uint8)
    return rgba


def bake_legacy_io_bg() -> np.ndarray:
    """Classic inputoverlay-background stand-in: translucent dark strip
    (drawn rotated 90° at the right screen edge, as stable does)."""
    w, h = int(LEGACY_IO_BG_SIZE[0]), int(LEGACY_IO_BG_SIZE[1])
    d = _rounded_rect_alpha(w, h, 12.0)
    inside = np.clip(-d / _AA_PX, 0.0, 1.0)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = 18
    rgba[..., 1] = 18
    rgba[..., 2] = 24
    rgba[..., 3] = np.round(inside * 0.55 * 255.0).astype(np.uint8)
    return rgba


def bake_tri_down(size: int = 64) -> np.ndarray:
    """Downward-pointing AA triangle (hit-error moving-average arrow)."""
    img = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    ImageDraw.Draw(img).polygon(
        [(4, 4), (size * 4 - 4, 4), (size * 2, size * 4 - 4)],
        fill=(255, 255, 255, 255))
    img = img.resize((size, size), Image.LANCZOS)   # cheap supersampled AA
    return np.asarray(img, dtype=np.uint8).copy()


class TextureBank:
    """Bakes the procedural set and uploads it into a SpriteRenderer.

    Texture keys: disc, ring, approach, glow, digit_0..digit_9, plus the
    HUD set (glyph_<ch>, key_square, vignette, pie_00.., pie_ring,
    tri_down). digit_aspect / glyph_aspect map char → width/height for
    layout math; glyph_mono_advance is the uniform digit advance (aspect
    units) the score/UR readouts use so rolling numbers don't jitter.
    """

    def __init__(self, renderer) -> None:
        renderer.upload_texture("disc", bake_disc())
        renderer.upload_texture("ring", bake_ring())
        renderer.upload_texture("approach",
                                bake_ring(APPROACH_SIZE, APPROACH_THICKNESS))
        renderer.upload_texture("glow", bake_glow())
        renderer.upload_texture("miss_x", bake_miss_x())
        renderer.upload_texture("dot", bake_dot())
        renderer.upload_texture("arrow", bake_arrow())
        self.digit_aspect: dict[str, float] = {}
        for ch, rgba in bake_digits().items():
            renderer.upload_texture(f"digit_{ch}", rgba)
            self.digit_aspect[ch] = rgba.shape[1] / rgba.shape[0]

        # --- HUD set --------------------------------------------------------
        self.glyph_aspect: dict[str, float] = {}
        for ch, rgba in bake_glyphs(HUD_CHARSET).items():
            renderer.upload_texture(f"glyph_{ch}", rgba)
            self.glyph_aspect[ch] = rgba.shape[1] / rgba.shape[0]
        self.glyph_mono_advance = max(
            self.glyph_aspect[ch] for ch in "0123456789")
        renderer.upload_texture("key_square", bake_key_square())
        renderer.upload_texture("vignette", bake_vignette())
        renderer.upload_texture("pie_ring", bake_ring(PIE_SIZE, 0.10))
        renderer.upload_texture("tri_down", bake_tri_down())
        for i in range(PIE_STEPS):
            renderer.upload_texture(f"pie_{i:02d}", bake_pie(i / PIE_STEPS))

        # --- Argon HUD set (skinless default) --------------------------------
        renderer.upload_texture("pill", bake_pill())
        renderer.upload_texture("argon_wireframe", bake_wireframe_cell())
        renderer.upload_texture("argon_wedge", bake_wedge())
        wf = bake_wireframe_cell()
        self.wireframe_aspect = wf.shape[1] / wf.shape[0]

        # --- legacy-default HUD set (custom-skin fallback, classic look) -----
        self.legacy_aspect: dict[str, float] = {}
        for ch, rgba in bake_legacy_font().items():
            key = {"%": "percent", ".": "dot", ",": "comma"}.get(ch, ch)
            renderer.upload_texture(f"lg_{key}", rgba)
            self.legacy_aspect[ch] = rgba.shape[1] / rgba.shape[0]
        renderer.upload_texture("lg_scorebar_bg", bake_legacy_scorebar_bg())
        renderer.upload_texture("lg_scorebar_colour",
                                bake_legacy_scorebar_colour())
        renderer.upload_texture("lg_ki", bake_legacy_ki())
        renderer.upload_texture("lg_kidanger",
                                bake_legacy_ki((255, 196, 70)))
        renderer.upload_texture("lg_kidanger2",
                                bake_legacy_ki((255, 80, 80)))
        renderer.upload_texture("lg_io_key", bake_legacy_io_key())
        renderer.upload_texture("lg_io_bg", bake_legacy_io_bg())
