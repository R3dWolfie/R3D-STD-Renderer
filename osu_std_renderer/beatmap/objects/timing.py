"""Timing points — RENDER_PLAN.md §2.6 (app/beatmap/objects/timing.go).

Red (uninherited) lines carry beatLength = ms/beat. Green (inherited) lines
carry a NEGATIVE value whose magnitude encodes the SV multiplier:
GetRatio() = clamp(-beatLength, 10, 1000) / 100  →  SV clamped to 0.1–10×.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field


@dataclass
class TimingPoint:
    """§2.6 TimingPoint struct (timing.go:9)."""
    time: float = 0.0
    beat_length_base: float = 60000.0 / 60.0   # inherited red-line ms/beat
    beat_length: float = 60000.0 / 60.0        # raw value; negative = green-line SV
    sample_set: int = 0
    sample_index: int = 1
    sample_volume: float = 1.0
    signature: int = 4
    inherited: bool = False                    # True = green line
    kiai: bool = False
    omit_first_bar_line: bool = False

    def get_ratio(self) -> float:
        """§2.6: beatLength>=0 or NaN → 1.0, else clamp(-bl,10,1000)/100."""
        if self.beat_length >= 0 or math.isnan(self.beat_length):
            return 1.0
        return max(10.0, min(1000.0, -self.beat_length)) / 100.0

    def get_base_beat_length(self) -> float:
        return self.beat_length_base

    def get_beat_length(self) -> float:
        """§2.6: SV-scaled effective beat length = beatLengthBase * GetRatio()."""
        return self.beat_length_base * self.get_ratio()


@dataclass
class Timings:
    """§2.6 Timings container (timing.go:55).

    Starts with a default 60 BPM point (like NewBeatMap) so GetPointAt never
    fails on a map parsed without [TimingPoints] (rejected later anyway).
    """
    slider_mult: float = 1.4      # [Difficulty] SliderMultiplier
    tick_rate: float = 1.0        # [Difficulty] SliderTickRate
    base_set: int = 1             # [General] SampleSet
    points: list[TimingPoint] = field(default_factory=list)          # all
    original_points: list[TimingPoint] = field(default_factory=list)  # red only
    _default: TimingPoint = field(default_factory=TimingPoint)

    def has_points(self) -> bool:
        return len(self.points) > 0

    def add_point(self, time: float, beat_length: float, sample_set: int,
                  sample_index: int, sample_volume: float, signature: int,
                  inherited: bool, kiai: bool,
                  omit_first_bar_line: bool = False) -> None:
        self.points.append(TimingPoint(
            time=time,
            beat_length_base=beat_length if not inherited and beat_length >= 0 else self._default.beat_length_base,
            beat_length=beat_length,
            sample_set=sample_set,
            sample_index=sample_index,
            sample_volume=sample_volume,
            signature=signature,
            inherited=inherited,
            kiai=kiai,
            omit_first_bar_line=omit_first_bar_line,
        ))

    def finalize_points(self) -> None:
        """§2.6 FinalizePoints (timing.go:104): stable-sort by time; green
        lines copy beatLengthBase from the previous point; reds also feed
        originalPoints."""
        self.points.sort(key=lambda p: p.time)  # Python sort is stable
        self.original_points = []
        last_base = self._default.beat_length_base
        for p in self.points:
            if p.inherited or p.beat_length < 0:
                p.beat_length_base = last_base
            else:
                p.beat_length_base = p.beat_length
                last_base = p.beat_length
                self.original_points.append(p)

    def get_point_at(self, time: float) -> TimingPoint:
        """§2.6 GetPointAt: binary search, last point with p.time <= time."""
        if not self.points:
            return self._default
        idx = bisect.bisect_right([p.time for p in self.points], time) - 1
        if idx < 0:
            return self.points[0]
        return self.points[idx]

    def get_original_point_at(self, time: float) -> TimingPoint:
        if not self.original_points:
            return self._default
        idx = bisect.bisect_right([p.time for p in self.original_points], time) - 1
        if idx < 0:
            return self.original_points[0]
        return self.original_points[idx]

    def get_scoring_distance(self) -> float:
        """§2.6: 100 * SliderMult / TickRate."""
        return (100 * self.slider_mult) / self.tick_rate

    def get_velocity(self, point: TimingPoint) -> float:
        """§2.6 GetVelocity: osu!px per SECOND at `point`.

        = ScoringDistance*TickRate (=100*SliderMult) * 1000 / effective beat len.
        """
        scoring_distance = self.get_scoring_distance() * self.tick_rate
        beat_length = point.get_beat_length()
        if beat_length >= 0:
            return scoring_distance * 1000 / beat_length
        return scoring_distance

    def get_tick_distance(self, point: TimingPoint) -> float:
        """§2.6 GetTickDistance = ScoringDistance / point.GetRatio()."""
        return self.get_scoring_distance() / point.get_ratio()
