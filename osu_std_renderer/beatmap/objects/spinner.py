"""Spinner — RENDER_PLAN.md §2.5 (objects/spinner.go).

NewSpinner (:71): commonParse(data, 6), EndTime = data[5].
rpms = 0.00795 is the reference auto-spin constant (revolutions per ms the
autoplay spinner turns); the approach circle shrinks 1.9→0.1 over the spin.
Spinner RPM/bonus display comes from cursor angle deltas at judge time
(ruleset phase — see ruleset/ruleset.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from .base import HitObject

RPMS = 0.00795  # §2.5 auto-spin constant (revolutions per millisecond)


@dataclass
class Spinner(HitObject):
    @classmethod
    def parse(cls, data: list[str]) -> "Spinner":
        s = cls()
        s.common_parse(data, 6)
        if len(data) > 5:
            s.end_time = float(data[5].split(":")[0])
        # spinners sit at playfield centre regardless of parsed coords
        return s

    # Draw-phase constants (spinner.go):
    APPROACH_START_SCALE = 1.9
    APPROACH_END_SCALE = 0.1

    def duration(self) -> float:
        return self.end_time - self.start_time
