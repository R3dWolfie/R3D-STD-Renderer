"""HUD container geometry, independent of font and texture providers.

Source: osu!lazer ArgonSkin/LegacySkin.MainHUDComponents and the standard
OsuLegacySkinTransformer (MIT). Coordinates use the 768-high HUD space.
The live grade and secondary stack are explicit R3D additions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

LEGACY_ACCURACY_MARGIN_X = 17.0  # upstream LegacyAccuracyCounter


@dataclass(frozen=True)
class HudLayout:
    name: str
    score_anchor: str
    keys_anchor: str
    keys_origin: str
    # Temporary project presentation calibration, separate from stock lazer
    # margins. Applies to every Legacy profile; saved skin JSON is not read.
    legacy_accuracy_offset: tuple[float, float] = (0.0, 0.0)

    @property
    def legacy(self) -> bool:
        return self.name == 'legacy'

    def legacy_counter_right(self, width: float, scale: float) -> float:
        """Owner-calibrated shared score/accuracy right edge in HUD units.

        Accuracy retains its stock margin and project offset. Score adopts
        that edge instead of a separate horizontal calibration; this is not
        stock lazer's independent score margin of 10.
        """
        return width + (self.legacy_accuracy_offset[0]
                        - LEGACY_ACCURACY_MARGIN_X) * scale


ARGON = HudLayout('argon', 'TopLeft', 'BottomRight', 'BottomRight')
LEGACY = HudLayout('legacy', 'TopRight', 'CentreRight', 'TopRight',
                   legacy_accuracy_offset=(14.0, -4.0))


def select_layout(settings, skin_elems) -> HudLayout:
    # An active extracted legacy provider counts even if it supplies no HUD
    # textures. --default-skin alone is a fallback, not an active skin.
    provider = getattr(skin_elems, 'skin', None)
    active_provider = (skin_elems is not None and
                       (provider is None or provider.skin_dir is not None))
    return LEGACY if (getattr(settings, 'legacy_defaults', False)
                      or getattr(settings, 'skin_dir', None) is not None
                      or active_provider) else ARGON


@dataclass(frozen=True)
class HudRect:
    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def sprites(cls, sprites, unit: float):
        # Include transparent sprites: an intentional blank still occupies
        # its native drawable's layout rectangle. No per-pixel ink guessing.
        if not sprites:
            return None
        rects = []
        for s in sprites:
            c, a = abs(math.cos(s.rotation)), abs(math.sin(s.rotation))
            w, h = c*s.w + a*s.h, a*s.w + c*s.h
            rects.append(cls((s.x-w/2)/unit, (s.y-h/2)/unit,
                             (s.x+w/2)/unit, (s.y+h/2)/unit))
        return cls(min(r.left for r in rects), min(r.top for r in rects),
                   max(r.right for r in rects), max(r.bottom for r in rects))

    def intersects(self, other) -> bool:
        return (self.left < other.right and self.right > other.left
                and self.top < other.bottom and self.bottom > other.top)
