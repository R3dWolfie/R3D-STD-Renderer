"""Slider — RENDER_PLAN.md §2.5 (objects/slider.go).

Wire format (comma-split, extraIndex 10):
    x,y,time,type,hitSound,curveType|x:y|…,repeatCount,pixelLength,
    edgeSounds(a|b|…),edgeSets(s:a|s:a|…),extras

Velocity/tick math ported exactly (slider.go:373/:433):

    velocity        = 100 * SliderMult / TPoint.GetBeatLength()      # px/ms, SV-scaled
    scoringDistance = velocity * TPoint.GetBaseBeatLength()          # = 100*Mult/ratio
    tickDistance    = scoringDistance / TickRate                     # v8+: scales with SV
                      (version < 8: multiplied back by GetRatio() —
                       old maps' tick distance does NOT scale with SV)
    spanDuration    = pixelLength / velocity
    EndTime         = StartTime + RepeatCount * spanDuration
    partLen         = (EndTime - StartTime) / RepeatCount

The stable scorePath (slider.go:433) walks the flattened polyline forward /
backward per span into time-parameterised segments with
progress(ms) = distance / velocity; ticks are emitted every tickDistance
(capped 32768/repeat, suppressed within 10 ms-worth of path of the span
end — the plan's `velocity(px/s) * 0.01`), plus reverse markers per span
end and the final "last tick". PositionAt(time) binary-searches scorePath
and lerps.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from ...curves import StdSliderPath
from .base import HitObject, HitSound
from .timing import TimingPoint, Timings

# §2.5 constants (slider.go)
MAX_PATH_LENGTH = 100_000_000
MAX_REPEATS = 10_000
MAX_TICKS_PER_SPAN = 32_768


@dataclass(frozen=True)
class ScorePathSegment:
    """One stable scorePath line: linear in both space and time."""
    time1: float
    time2: float
    p1: tuple[float, float]
    p2: tuple[float, float]


@dataclass(frozen=True)
class TickPoint:
    """A scoring point on the slider (tick / repeat / last tick)."""
    time: float
    pos: tuple[float, float]
    kind: str          # "tick" | "reverse" | "last"
    span: int
    is_reverse: bool = False


@dataclass
class Slider(HitObject):
    pixel_length: float = 0.0
    repeat_count: int = 1
    multi_curve: StdSliderPath | None = None
    tpoint: TimingPoint = field(default_factory=TimingPoint)
    part_len: float = 0.0            # ms per span
    velocity: float = 0.0            # px/ms
    tick_distance: float = 0.0       # px between ticks
    score_path: list[ScorePathSegment] = field(default_factory=list)
    _score_times: list[float] = field(default_factory=list)  # bisect index
    tick_points: list[TickPoint] = field(default_factory=list)
    tick_reverse: list[TickPoint] = field(default_factory=list)
    score_points: list[TickPoint] = field(default_factory=list)
    edge_sounds: list[int] = field(default_factory=list)      # per-edge bitmasks
    edge_sets: list[tuple[int, int]] = field(default_factory=list)  # (set, add)
    # lazer values (§2.5 calculateFollowPointsLazer — always computed)
    end_time_lazer: float = 0.0
    span_duration_lazer: float = 0.0

    # --- §2.5 NewSlider (slider.go:105) --------------------------------------
    @classmethod
    def parse(cls, data: list[str]) -> "Slider | None":
        if len(data) < 8:
            return None
        s = cls()
        s.common_parse(data, 10)

        try:
            s.pixel_length = float(data[7])
            s.repeat_count = int(float(data[6]))
        except ValueError:
            return None
        # reject if pixelLength*RepeatCount > maxPathLength*10; clamp both
        if s.pixel_length * s.repeat_count > MAX_PATH_LENGTH * 10:
            return None
        s.pixel_length = min(s.pixel_length, float(MAX_PATH_LENGTH))
        s.repeat_count = max(1, min(s.repeat_count, MAX_REPEATS))

        x0, y0 = s.start_pos_raw
        try:
            s.multi_curve = StdSliderPath(data[5], x0, y0, s.pixel_length or None)
        except (ValueError, IndexError):
            return None
        if not s.multi_curve.path:
            return None  # nil curve → invalid slider (parseCurve)
        if s.pixel_length == 0:
            s.pixel_length = s.multi_curve.distance  # GetLength()

        # per-edge hitsounds: data[8] "|"-split bitmasks
        if len(data) > 8 and data[8]:
            for tok in data[8].split("|"):
                try:
                    s.edge_sounds.append(int(tok))
                except ValueError:
                    s.edge_sounds.append(0)
        # per-edge sample sets: data[9] "|"-split "set:add"
        if len(data) > 9 and data[9]:
            for tok in data[9].split("|"):
                bits = tok.split(":")
                try:
                    s.edge_sets.append((int(bits[0]), int(bits[1]) if len(bits) > 1 else 0))
                except (ValueError, IndexError):
                    s.edge_sets.append((0, 0))
        return s

    # --- §2.5 SetTiming (slider.go:360) ---------------------------------------
    def set_timing(self, timings: Timings, version: int,
                   diff_calc_only: bool = False) -> None:
        self.timings = timings
        self.tpoint = timings.get_point_at(self.start_time)
        self._calculate_follow_points_lazer(version)
        if diff_calc_only:
            return
        self._calculate_follow_points_stable(version)

    def _calculate_follow_points_lazer(self, version: int) -> None:
        """§2.5 lazer math (slider.go:373). Kept for future lazer-fidelity
        work; stable values drive this renderer."""
        beat_length = self.tpoint.get_beat_length()
        self.velocity = (100 * self.timings.slider_mult / beat_length
                         if beat_length > 0 else 0.0)
        scoring_distance = self.velocity * self.tpoint.get_base_beat_length()
        td = scoring_distance / self.timings.tick_rate if self.timings.tick_rate else 0.0
        if version < 8:
            td *= self.tpoint.get_ratio()  # legacy: SV does not scale tick distance
        self.tick_distance = td
        curve_length = self.multi_curve.distance
        if self.velocity > 0:
            self.end_time_lazer = self.start_time + self.repeat_count * curve_length / self.velocity
            self.span_duration_lazer = (self.end_time_lazer - self.start_time) / self.repeat_count
        else:
            self.end_time_lazer = self.start_time
            self.span_duration_lazer = 0.0

    def _calculate_follow_points_stable(self, version: int) -> None:
        """§2.5 stable walk (slider.go:433): scorePath + ticks + reverses."""
        if self.velocity <= 0 or self.pixel_length <= 0:
            self.end_time = self.start_time
            self.part_len = 0.0
            return

        span_duration = self.pixel_length / self.velocity          # ms
        self.end_time = self.start_time + self.repeat_count * span_duration
        self.part_len = (self.end_time - self.start_time) / self.repeat_count
        self.end_pos_raw = self.multi_curve.position_at(
            1.0 if self.repeat_count % 2 == 1 else 0.0)

        # scorePath: walk the flattened polyline forward/backward per span,
        # progress(ms) = distance / velocity.
        path = self.multi_curve.path
        cum = self.multi_curve.cum
        self.score_path = []
        for span in range(self.repeat_count):
            span_start = self.start_time + span * span_duration
            reverse = span % 2 == 1
            n = len(path)
            for i in range(n - 1):
                if reverse:
                    a_i, b_i = n - 1 - i, n - 2 - i
                    d1 = self.multi_curve.distance - cum[a_i]
                    d2 = self.multi_curve.distance - cum[b_i]
                else:
                    a_i, b_i = i, i + 1
                    d1, d2 = cum[a_i], cum[b_i]
                self.score_path.append(ScorePathSegment(
                    time1=span_start + d1 / self.velocity,
                    time2=span_start + d2 / self.velocity,
                    p1=path[a_i], p2=path[b_i]))
        self._score_times = [seg.time1 for seg in self.score_path]

        # ticks: every tickDistance, capped per span, suppressed within
        # 10 ms-worth of path of the span tail (velocity px/ms * 10 ms).
        self.tick_points = []
        self.tick_reverse = []
        self.score_points = []
        min_dist_from_end = self.velocity * 10.0
        if self.tick_distance > 0:
            for span in range(self.repeat_count):
                span_start = self.start_time + span * span_duration
                reverse = span % 2 == 1
                d = self.tick_distance
                emitted = 0
                ticks: list[TickPoint] = []
                while d < self.pixel_length - min_dist_from_end and emitted < MAX_TICKS_PER_SPAN:
                    progress = d / self.pixel_length
                    time_progress = 1.0 - progress if reverse else progress
                    ticks.append(TickPoint(
                        time=span_start + time_progress * span_duration,
                        pos=self.multi_curve.position_at(progress),
                        kind="tick", span=span))
                    d += self.tick_distance
                    emitted += 1
                ticks.sort(key=lambda t: t.time)
                self.tick_points.extend(ticks)
                self.score_points.extend(ticks)
                # reverse marker at every span end except the final one
                end_progress = 0.0 if reverse else 1.0
                marker = TickPoint(
                    time=span_start + span_duration,
                    pos=self.multi_curve.position_at(end_progress),
                    kind="reverse" if span < self.repeat_count - 1 else "last",
                    span=span, is_reverse=span < self.repeat_count - 1)
                if marker.is_reverse:
                    self.tick_reverse.append(marker)
                self.score_points.append(marker)

    # --- §2.5 PositionAt: binary-search scorePath, lerp ------------------------
    def position_at(self, time: float) -> tuple[float, float]:
        """RAW path position at absolute map time (no flips/stacking).
        Callers apply modify_position() for the presented position."""
        if not self.score_path:
            return self.start_pos_raw
        if time <= self.score_path[0].time1:
            return self.score_path[0].p1
        if time >= self.score_path[-1].time2:
            return self.score_path[-1].p2
        i = bisect.bisect_right(self._score_times, time) - 1
        seg = self.score_path[max(0, i)]
        span = seg.time2 - seg.time1
        if span <= 0:
            return seg.p2
        w = (time - seg.time1) / span
        return (seg.p1[0] + (seg.p2[0] - seg.p1[0]) * w,
                seg.p1[1] + (seg.p2[1] - seg.p1[1]) * w)

    def position_at_progress(self, progress: float) -> tuple[float, float]:
        """RAW path position at 0..1 of ONE span (path-space, not time)."""
        return self.multi_curve.position_at(progress)

    def get_stacked_position_at(self, time: float, diff) -> tuple[float, float]:
        return self.modify_position(self.position_at(time), diff)

    # --- §2.5 IsRetarded (slider.go:645) ---------------------------------------
    def is_invalid(self) -> bool:
        """Empty path or zero length in time (the reference's IsRetarded)."""
        return (not self.multi_curve.path) or self.end_time <= self.start_time

    # SetDifficulty (slider.go:561) builds snake gliders, head DummyCircle,
    # per-repeat reverse-arrow sprites, tick sprites and the GENERATED body
    # (sliderrenderer.NewBody). The body technique lives in
    # render/slider_body.py (Phase-0 spike; currently a stub).
