"""Original osu!lazer ModIcon PNG presentation, independent of font files.

Sources: ppy/osu ModIcon.cs, ModFlowDisplay.cs, ModDisplay.cs, OsuColour.cs
and Mod*.cs at 8a942790906a37930a0f55c5d27e498159835885. Asset provenance is
in assets/hud/mod-icons/inventory.json and THIRD_PARTY_NOTICES.txt.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .gl import Sprite

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets/hud/mod-icons"
GAMEPLAY_ICON_SIZE = 80.0 * 0.6
EXTENDED_ICON_WIDTH = (80.0 - 22.0 + 116.0) * 0.6
EXTENDED_TEXT_HEIGHT = 34.0 * 0.6

# Mod.Type / OsuIcon.OsuIconMapping, verified against compiled upstream mods.
# V2 is Standard's existing bitfield display alias for upstream SV2.
LAZER_MOD_ICONS = {
    'AC': ('mod-accuracy-challenge', 'DifficultyIncrease'),
    'AD': ('mod-approach-different', 'Fun'),
    'AL': ('mod-alternate', 'Conversion'),
    'AP': ('mod-autopilot', 'Automation'),
    'AS': ('mod-adaptive-speed', 'Fun'),
    'AT': ('mod-autoplay', 'Automation'),
    'BL': ('mod-blinds', 'DifficultyIncrease'),
    'BM': ('mod-bloom', 'Fun'),
    'BR': ('mod-barrel-roll', 'Fun'),
    'BU': ('mod-bubbles', 'Fun'),
    'CL': ('mod-classic', 'Conversion'),
    'CN': ('mod-cinema', 'Automation'),
    'DA': ('mod-difficulty-adjust', 'Conversion'),
    'DC': ('mod-daycore', 'DifficultyReduction'),
    'DF': ('mod-deflate', 'Fun'),
    'DP': ('mod-depth', 'Fun'),
    'DT': ('mod-double-time', 'DifficultyIncrease'),
    'EZ': ('mod-easy', 'DifficultyReduction'),
    'FL': ('mod-flashlight', 'DifficultyIncrease'),
    'FR': ('mod-freeze-frame', 'Fun'),
    'GR': ('mod-grow', 'Fun'),
    'HD': ('mod-hidden', 'DifficultyIncrease'),
    'HR': ('mod-hard-rock', 'DifficultyIncrease'),
    'HT': ('mod-half-time', 'DifficultyReduction'),
    'MG': ('mod-magnetised', 'Fun'),
    'MR': ('mod-mirror', 'Conversion'),
    'MU': ('mod-muted', 'Fun'),
    'NC': ('mod-nightcore', 'DifficultyIncrease'),
    'NF': ('mod-no-fail', 'DifficultyReduction'),
    'NS': ('mod-no-scope', 'Fun'),
    'PF': ('mod-perfect', 'DifficultyIncrease'),
    'RD': ('mod-random', 'Conversion'),
    'RP': ('mod-repel', 'Fun'),
    'RX': ('mod-relax', 'Automation'),
    'SD': ('mod-sudden-death', 'DifficultyIncrease'),
    'SG': ('mod-single-tap', 'Conversion'),
    'SI': ('mod-spin-in', 'Fun'),
    'SO': ('mod-spun-out', 'Automation'),
    'ST': ('mod-strict-tracking', 'DifficultyIncrease'),
    'SV2': ('mod-score-v2', 'System'),
    'SY': ('mod-synesthesia', 'Fun'),
    'TC': ('mod-traceable', 'DifficultyIncrease'),
    'TD': ('mod-touch-device', 'System'),
    'TP': ('mod-target-practice', 'Conversion'),
    'TR': ('mod-transform', 'Fun'),
    'V2': ('mod-score-v2', 'System'),
    'WD': ('mod-wind-down', 'Fun'),
    'WG': ('mod-wiggle', 'Fun'),
    'WU': ('mod-wind-up', 'Fun'),
}

# OsuColour.ForModType: all six source categories, not the old pill palette.
MOD_TYPE_HEX = {
    "DifficultyReduction": "b2ff66", "DifficultyIncrease": "ff6666",
    "Conversion": "8c66ff", "Automation": "66ccff",
    "Fun": "ff66ab", "System": "ffcc22",
}


def mod_colours(acronym):
    """Unselected sRGB background, linear-interpolated foreground, extender.

    Interpolation.ValueAt<Colour4>(0.1, Black, background, 0, 1) blends in
    linear space; Darken(2.8) divides the sRGB channels by 3.8. Confirmed
    against the actual compiled osu.Framework Colour4 implementation.
    """
    colour = MOD_TYPE_HEX[LAZER_MOD_ICONS[acronym][1]]
    background = tuple(int(colour[i:i+2], 16) / 255 for i in (0, 2, 4))
    def foreground_channel(value):
        linear = value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4
        value = linear * .1
        return 12.92*value if value <= .0031308 else 1.055*value**(1/2.4)-.055
    return (background, tuple(foreground_channel(c) for c in background),
            tuple(c/3.8 for c in background))


def rate_information(pill):
    """Only metadata already carried by ModPill; no fabricated settings."""
    label = pill.text.removeprefix(pill.acr).strip()
    if pill.acr in {"DT", "NC", "HT", "DC"} and label.endswith("×"):
        try:
            return f"{float(label[:-1]):.2f}x"
        except ValueError:
            pass
    return label


def fit_canvas(size, width, height):
    factor = min(width/size[0], height/size[1])
    return size[0]*factor, size[1]*factor


@lru_cache(maxsize=128)
def load_png(path):
    try:
        with Image.open(path) as image:
            return np.array(image.convert("RGBA"), dtype=np.uint8)
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("ModIcon PNG unreadable: %s", path)
        return None


class LazerModIcons:
    """One setup-time upload per original PNG, shared GL/Metal sprite keys."""

    def __init__(self, renderer, asset_dir=ASSET_DIR):
        self.size = {}
        names = {name for name, _ in LAZER_MOD_ICONS.values()}
        for name in sorted(names | {"mod-icon", "mod-icon-extender"}):
            rgba = load_png(Path(asset_dir) / f"{name}.png")
            if rgba is not None:
                # The source TextureStore uses linear sampling. The shared
                # upload option applies equally to the GL and Metal backends.
                renderer.upload_texture("lz_" + name, rgba, mipmaps=False)
                self.size[name] = (rgba.shape[1], rgba.shape[0])

    def has(self, acronym):
        # A missing individual PNG keeps its 48-unit slot and renders the
        # known acronym against the original backing, just like null Icon.
        return acronym in LAZER_MOD_ICONS

    def draw(self, out, acronym, x, top, scale, opacity, label, text, text_width, *, unit=1.0):
        """80×80 component at 0.6 gameplay scale, using existing HUD text."""
        h = GAMEPLAY_ICON_SIZE * scale
        background, foreground, extender = mod_colours(acronym)

        def sprite(name, cx, cy, width, height, colour):
            if name not in self.size:
                return
            width, height = fit_canvas(self.size[name], width, height)
            out.append(Sprite(cx*unit, cy*unit, width*unit, height*unit,
                              "lz_"+name, (*colour, opacity)))

        if label:
            # Extended content: X=58, W=116, H=80; label centre X=6.
            sprite("mod-icon-extender", x+116*.6*scale, top+h/2,
                   116*.6*scale, h, extender)
            th = EXTENDED_TEXT_HEIGHT * scale
            tw = text_width(label, th)
            # R3D's existing font has different advances from Torus. Keep the
            # source centre and maximum height, fitting wide runs into the
            # exposed extender so neither neighbouring icon hides a digit.
            available = 2*(122-80)*.6*scale
            if tw > available:
                th *= available/tw
                tw = text_width(label, th)
            text(out, label, x+122*.6*scale-tw/2, top+(h-th)/2,
                 th, background, opacity)
        sprite("mod-icon", x+h/2, top+h/2, h, h, background)
        name = LAZER_MOD_ICONS[acronym][0]
        if name in self.size:
            sprite(name, x+h/2, top+h/2, h*131/135, h*92/135, foreground)
        else:
            th = 22*.6*scale
            tw = text_width(acronym, th)
            text(out, acronym, x+h/2-tw/2, top+(h-th)/2, th, foreground, opacity)
