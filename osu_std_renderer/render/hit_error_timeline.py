"""Read-only Standard timing observations and precomputed Mania HEM transitions.

No scoring/header reconstruction. Slider heads prefer the immutable routed
click snapshot, before aggregate/reconciliation changes. Circle verdicts must
be corroborated by a real replay press; synthetic perfect/header hits do not
create meter samples. UR deliberately retains HudData's independent inputs.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from math import isfinite, isclose

from ..ruleset import JudgmentKind, press_edges
from .hit_error_meter import (MARKER_LIFETIME_MS, MARKER_MAX_CONCURRENT,
    hit_error_chevron_position, next_hit_error_chevron_transition, next_hit_error_ema)


@dataclass(frozen=True)
class TimingObservation:
    time_ms: float
    offset_ms: float
    judgment: str
    object_kind: str


@dataclass(frozen=True)
class HitErrorEvent:
    offset_ms: float
    judgment: str
    age_ms: float


@dataclass(frozen=True)
class HitErrorFrame:
    hit_error_windows: tuple[float, float, float]
    hit_error_events: tuple[HitErrorEvent, ...]
    hit_error_ema_ms: float | None
    hit_error_chevron_position: float | None


def timing_observations(sim, frames, windows, radius):
    """Select real clicks without routing/judging the replay a second time."""
    presses = press_edges(frames)
    times = [p.time_ms for p in presses]
    candidates = []
    for order, verdict in enumerate(sim.verdicts.values()):
        if verdict.obj_kind not in ('circle', 'slider'):
            continue
        channel = None
        if verdict.obj_kind == 'slider':
            timeline = getattr(verdict, 'slider_visuals', None)
            # An unavailable/missed raw head cannot be replaced by repaired
            # parts or a synthesized on-time head from the replay totals.
            if timeline is None:
                continue
            heads = [e for e in timeline.events if e.kind == 'head_judged'
                     and e.provenance == 'click_routing' and e.hit is True]
            if not heads:
                continue
            event = min(heads, key=lambda e: (e.time_ms, e.sequence))
            time = event.time_ms
            if not all(isinstance(x, (int, float)) and isfinite(x)
                       for x in (time, verdict.start_time)):
                continue
            delta = time - verdict.start_time
            channel = event.channel
        else:
            if verdict.kind == JudgmentKind.MISS:
                continue
            time, delta = verdict.hit_time, verdict.delta
            # Circle quality is frozen before reconciliation as abs(raw
            # delta), or infinity for a raw miss. This excludes repaired
            # perfect hits even if an unrelated real press shares the time.
            quality = getattr(verdict, 'quality', None)
            if not (isinstance(quality, (int, float)) and isfinite(quality)
                    and isinstance(delta, (int, float)) and isfinite(delta)
                    and isclose(quality, abs(delta), abs_tol=1e-6)):
                continue
        if not all(isinstance(x, (int, float)) and isfinite(x)
                   for x in (time, delta, verdict.start_time)):
            continue
        if not isclose(time-verdict.start_time, delta, abs_tol=1e-6):
            continue
        kind = windows.result_for(delta)
        if kind is None:
            continue
        matches = []
        for i in range(bisect_left(times, time), bisect_right(times, time)):
            p = presses[i]
            if channel is not None and channel != p.channel:
                continue
            if (p.x-verdict.pos[0])**2 + (p.y-verdict.pos[1])**2 <= radius**2:
                matches.append(i)
        if matches:
            candidates.append((time, tuple(matches), order, TimingObservation(
                time, delta, kind.value, verdict.obj_kind)))
    # One press can only route to one object. Raw slider head observations
    # take precedence over a repaired circle sharing that press.
    candidates.sort(key=lambda c: (c[0], c[3].object_kind != 'slider', c[2]))
    used = set()
    observations = []
    for _, matches, _, observation in candidates:
        press = next((i for i in matches if i not in used), None)
        if press is not None:
            observations.append((press, observation))
            used.add(press)
    # Reservation priority must not reorder actual simultaneous channels:
    # press_edges visits channel1 before channel2 on the same replay frame.
    return tuple(observation for _, observation in sorted(
        observations, key=lambda pair: (pair[1].time_ms, pair[0])))


class HitErrorTimeline:
    """Immutable prefix folds; every query uses absolute beatmap milliseconds."""
    def __init__(self, observations, windows):
        self.observations = tuple(sorted(observations, key=lambda p: p.time_ms))
        self.windows = tuple(windows)
        self.times = tuple(p.time_ms for p in self.observations)
        averages, transitions = [], []
        average, previous = 0.0, None
        for event in self.observations:
            average = next_hit_error_ema(average, event.offset_ms)
            previous = next_hit_error_chevron_transition(
                previous, event.time_ms, average, self.windows[-1])
            averages.append(average)
            transitions.append(previous)
        self.averages = tuple(averages)
        self.transitions = tuple(transitions)

    def at(self, time_ms):
        hi = bisect_right(self.times, time_ms)
        lo = max(bisect_right(self.times, time_ms-MARKER_LIFETIME_MS),
                 hi-MARKER_MAX_CONCURRENT)
        events = tuple(HitErrorEvent(p.offset_ms, p.judgment, time_ms-p.time_ms)
                       for p in self.observations[lo:hi])
        return HitErrorFrame(self.windows, events,
            self.averages[hi-1] if hi else None,
            hit_error_chevron_position(self.transitions[hi-1], time_ms) if hi else None)
