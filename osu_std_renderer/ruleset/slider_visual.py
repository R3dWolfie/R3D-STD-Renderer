"""Immutable raw observations and source-modelled slider notifications.

This is a PARTIAL contract, not a lazer callback log. See
docs/SLIDER_VISUAL_EVENTS.md before consuming it for animation. In particular,
a ``part_sampled`` event is never a ``part_judged`` notification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SliderVisualEvent:
    """One producer observation in map milliseconds, before reconciliation.

    ``time_ms`` belongs to the producer identified by ``provenance``; it does
    not claim an observed lazer drawable callback. ``source_model`` denotes
    the documented chronological surrogate, not native runtime observations.
    ``transform_time_ms=None`` means the animation anchor is UNAVAILABLE,
    not an instruction to substitute time_ms. Part indices address the raw
    head/tick/repeat/tail list (head is zero).
    """
    time_ms: float
    sequence: int
    kind: Literal["head_judged", "tracking_on", "tracking_off",
                  "part_sampled", "part_judged", "slider_judged",
                  "tracking_published_on", "tracking_published_off",
                  "repeat_freeze"]
    provenance: Literal["click_routing", "tracking_sample", "part_sample",
                        "source_model"]
    hit: bool | None = None
    part_index: int | None = None
    part_kind: Literal["head", "tick", "repeat", "tail"] | None = None
    transform_time_ms: float | None = None
    channel: int | None = None
    input_frame_index: int | None = None
    result_time_ms: float | None = None
    follow_callback: bool | None = None
    judgement: str | None = None


@dataclass(frozen=True)
class SliderVisualGap:
    """A source event/constraint the producer cannot establish.

    No invented notification time, animation anchor, or hit is attached.
    ``reason`` is a stable machine-readable code, documented with the model.
    """
    kind: Literal["head_judged", "part_judged", "slider_judged",
                  "tracking", "callback_order"]
    reason: str
    part_index: int | None = None
    provenance: Literal["raw_simulation", "source_model"] | None = None


@dataclass(frozen=True)
class SliderVisualTimeline:
    """Stable per-slider snapshot; query prefixes without changing history.

    Produced timelines are ``partial`` (raw/model events plus scoped gaps)
    or ``unavailable`` (no input frames). Neither status authorises full
    follow-circle/repeat/parent-end animation parity. ``None`` on a verdict
    means no slider producer ran, which differs from an empty event prefix.
    The existing tracking lattice has a 1ms nominal step plus endpoints and
    part times; sampling_step_ms is NOT a bound on source timing error.
    """
    status: Literal["partial", "unavailable"]
    lazer: bool
    classic: bool
    events: tuple[SliderVisualEvent, ...] = ()
    unavailable: tuple[SliderVisualGap, ...] = ()
    tracking_sample_end_ms: float | None = None
    sampling_step_ms: float | None = None
    clock_model: Literal["replay_boundaries_v1"] | None = None
    model_evaluations: int = 0
    model_input_frames: int = 0
    model_end_ms: float | None = None

    def events_at(self, time_ms: float) -> tuple[SliderVisualEvent, ...]:
        """Inclusive prefix in producer order; safe for arbitrary seek order."""
        return tuple(event for event in self.events if event.time_ms <= time_ms)

    def raw_events_at(self, time_ms: float) -> tuple[SliderVisualEvent, ...]:
        """Raw routed/sampled observations only, in their original order."""
        return tuple(event for event in self.events_at(time_ms)
                     if event.provenance != "source_model")

    def model_events_at(self, time_ms: float) -> tuple[SliderVisualEvent, ...]:
        """Source-derived surrogate callbacks, never native runtime traces."""
        return tuple(event for event in self.events_at(time_ms)
                     if event.provenance == "source_model")
