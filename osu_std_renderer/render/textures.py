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


def bake_digits(height: int = DIGIT_HEIGHT) -> dict[str, np.ndarray]:
    """0-9 as white RGBA glyphs, all sharing one vertical extent (union of
    the glyph bboxes) so centring them on the circle aligns baselines."""
    font = _load_font(int(height * 0.95))
    digits = "0123456789"
    boxes = {}
    for ch in digits:
        try:
            boxes[ch] = font.getbbox(ch)
        except AttributeError:  # ancient PIL bitmap font
            w, h = font.getsize(ch)  # type: ignore[attr-defined]
            boxes[ch] = (0, 0, w, h)
    top = min(b[1] for b in boxes.values())
    bottom = max(b[3] for b in boxes.values())
    pad = 4
    out: dict[str, np.ndarray] = {}
    for ch in digits:
        x0, _, x1, _ = boxes[ch]
        w = (x1 - x0) + 2 * pad
        h = (bottom - top) + 2 * pad
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(img).text((pad - x0, pad - top), ch, font=font,
                                 fill=(255, 255, 255, 255))
        out[ch] = np.asarray(img, dtype=np.uint8).copy()
    return out


class TextureBank:
    """Bakes the procedural set and uploads it into a SpriteRenderer.

    Texture keys: disc, ring, approach, glow, digit_0..digit_9.
    digit_aspect maps '0'..'9' → width/height for layout math.
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
