"""Playfield camera — RENDER_PLAN.md §5.4 EXACT
(app/bmath/camera/camera.go).

    OsuWidth=512, OsuHeight=384
    baseScale = height/384;  if 512/384 > width/height { baseScale = width/512 }   // fit
    scl = baseScale * 0.8 * Playfield.Scale        // 0.8 = osu!'s playfield inset
    projection = Ortho(-w/2..w/2, h/2..-h/2)       // Y-down
    origin (-256,-192); position (ShiftX,ShiftY)*scl (OsuShift → +8px Y)

The ortho projection + origin translate is algebraically identical to the
direct pixel mapping implemented here:

    screen_x = screen_w/2 + (x - 256 + shift_x') * scl
    screen_y = screen_h/2 + (y - 192 + shift_y') * scl

where shift_y' includes the OsuShift +8 osu!px. GenRotated (the -cursors
mirror/mandala divides) is a later phase; single PV matrix for now.
"""
from __future__ import annotations

from dataclasses import dataclass

OSU_WIDTH = 512.0
OSU_HEIGHT = 384.0
PLAYFIELD_INSET = 0.8   # osu!'s playfield inset (§5.4)
OSU_SHIFT_Y = 8.0       # OsuShift → +8 osu!px Y


@dataclass
class PlayfieldCamera:
    screen_w: int
    screen_h: int
    scale: float = 1.0        # settings Playfield.Scale
    osu_shift: bool = False   # settings Playfield.OsuShift
    shift_x: float = 0.0      # settings Playfield.ShiftX (osu!px)
    shift_y: float = 0.0      # settings Playfield.ShiftY (osu!px)

    def __post_init__(self) -> None:
        base_scale = self.screen_h / OSU_HEIGHT
        if OSU_WIDTH / OSU_HEIGHT > self.screen_w / self.screen_h:
            base_scale = self.screen_w / OSU_WIDTH
        self.base_scale = base_scale
        self.scl = base_scale * PLAYFIELD_INSET * self.scale
        self._sx = self.shift_x
        self._sy = self.shift_y + (OSU_SHIFT_Y if self.osu_shift else 0.0)

    # --- osu!px (512×384, top-left origin, Y-down) → screen px ------------------
    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (self.screen_w / 2.0 + (x - OSU_WIDTH / 2.0 + self._sx) * self.scl,
                self.screen_h / 2.0 + (y - OSU_HEIGHT / 2.0 + self._sy) * self.scl)

    def to_osu(self, sx: float, sy: float) -> tuple[float, float]:
        return ((sx - self.screen_w / 2.0) / self.scl + OSU_WIDTH / 2.0 - self._sx,
                (sy - self.screen_h / 2.0) / self.scl + OSU_HEIGHT / 2.0 - self._sy)

    def len_to_screen(self, osu_len: float) -> float:
        """Scale a length (e.g. CircleRadius) to screen px."""
        return osu_len * self.scl
