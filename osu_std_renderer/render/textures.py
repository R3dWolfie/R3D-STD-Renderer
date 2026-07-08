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

HUD_CHARSET = "0123456789.%x,KMSABCDUR"
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
