"""Hit circle — RENDER_PLAN.md §2.5 (objects/circle.go).

NewCircle (:53): commonParse(data, 5), sample = int(data[4]), texture base
"hit". SetDifficulty (:143) builds fade/scale transforms from
Preempt/TimeFadeIn/Hit100/Hit50; the approach circle scales 4→1 over
[startTime-Preempt, startTime]. Those transforms belong to the draw phase
and are derived on the fly by the renderer in this port (see
render/playfield.py); the parse-side surface is complete here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import HitObject


@dataclass
class Circle(HitObject):
    sample: int = 0

    @classmethod
    def parse(cls, data: list[str]) -> "Circle":
        c = cls()
        c.common_parse(data, 5)
        c.sample = int(data[4]) if len(data) > 4 else 0
        return c

    # Draw-phase constants documented from circle.go for the render phase:
    #   approach circle: scale 4→1 over [startTime-Preempt, startTime]
    #   fade in over TimeFadeIn starting at startTime-Preempt
    #   on hit: explosion scale →1.4 (skin.ini Version>=2) / →1.8 (Version<2)
    #   combo number: quick 60 ms fade (v2+) / scales+fades with explosion (v1)
    APPROACH_START_SCALE = 4.0
