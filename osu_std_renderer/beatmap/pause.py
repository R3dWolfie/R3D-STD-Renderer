"""Breaks — RENDER_PLAN.md §2.8 (app/beatmap/pause.go).

[Events] line `2,start,end` (or `Break,start,end`):
NewPause(data): start = data[1], end = data[2].
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pause:
    start_time: float
    end_time: float

    @classmethod
    def parse(cls, data: list[str]) -> "Pause | None":
        try:
            return cls(start_time=float(data[1]), end_time=float(data[2]))
        except (ValueError, IndexError):
            return None

    def duration(self) -> float:
        return self.end_time - self.start_time
