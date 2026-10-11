"""Seek-safe slider visuals on the documented source-model clock.

FollowCircle/DefaultFollowCircle/ArgonFollowCircle/LegacyFollowCircle and
DrawableSlider are the authority. These are model animations, not native
runtime traces. No raw samples or reconciled outcomes drive these channels.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, replace


def clamp(value):
    return max(0.0, min(1.0, value))


def ease(value, easing):
    value = clamp(value)
    if easing == "out_quint":
        return 1 - (1 - value) ** 5
    if easing == "out":
        return value * (2 - value)
    if easing == "in":
        return value * value
    return value


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    initial: float
    target: float
    easing: str = "linear"

    def at(self, time):
        progress = 1 if self.end <= self.start else (time - self.start) / (self.end - self.start)
        return self.initial + (self.target - self.initial) * ease(progress, self.easing)


@dataclass(frozen=True)
class Channel:
    base: float
    segments: tuple[Segment, ...] = ()

    def at(self, time):
        for segment in reversed(self.segments):
            if segment.start <= time:
                return segment.at(time)
        return self.base

    def advance(self, time):
        # Framework completed transforms leave their final property value.
        # Keep only the last begun transform and future queued transforms.
        begun = [s for s in self.segments if s.start <= time]
        future = tuple(s for s in self.segments if s.start > time)
        if not begun:
            return self
        last = begun[-1]
        if time >= last.end:
            return Channel(last.target, future)
        return Channel(self.base, (last,) + future)

    def to(self, anchor, target, duration=0, easing="linear", now=None):
        initial = self.at(anchor)
        # New transforms cancel following transforms on THIS property only.
        earlier = tuple(s for s in self.segments if s.start < anchor)
        segment = Segment(anchor, anchor + max(0, duration), initial, target, easing)
        channel = Channel(self.base, earlier + (segment,))
        return channel.advance(now) if now is not None else channel


@dataclass(frozen=True)
class FollowState:
    scale: float
    alpha: float


def ball_variant(skin):
    """Resolve the loaded ball component independently of the follow asset.

    SkinElements.has includes supplied blank textures and resolved sliderb0
    frames. Companion-only assets do not select LegacySliderBall.
    """
    return "argon" if skin is None else "legacy" if skin.has("sliderb") else "default"


@dataclass(frozen=True)
class BallAnimation:
    """Intrinsic child opacity; draw_slider_layers applies parent alpha once.

    A known result uses its notification prefix and hit-state transform
    anchor. Missing results have an explicitly approximate nominal visual
    retirement, without producing or modifying any model callbacks. A known
    result with an unknown anchor cannot support a source-defined fade.
    """
    start: float
    end: float
    parent: object = None
    autoplay: bool = False

    @property
    def provenance(self):
        if self.parent is None:
            return "approximate_debug_no_replay" if self.autoplay else "approximate_nominal_retirement"
        anchor = self.parent.transform_time_ms
        return ("unsupported_parent_anchor" if anchor is None or not math.isfinite(anchor)
                else "source_model")

    def at(self, time, variant, active_alpha=1.0):
        if variant not in ("argon", "default", "legacy"):
            raise ValueError(f"Unknown ball variant: {variant}")
        if time < self.start:
            return 0.0
        parent = self.parent
        if parent is not None:
            anchor = parent.transform_time_ms
            if time < parent.time_ms or anchor is None or not math.isfinite(anchor):
                return active_alpha
        else:
            anchor = self.end  # Visual-only fallback; never a source callback.
        if variant == "legacy":
            return active_alpha if time < anchor else 0.0
        duration, easing = (50.0, "out_quint") if variant == "argon" else (112.5, "out")
        return active_alpha * (1 - ease((time - anchor) / duration, easing))


class FollowAnimation:
    """Compile bounded channel snapshots once; queries never change history.

    Snapshots are selected by notification time, so a late, backdated anchor
    cannot invent an animation before its callback. Channel start values use
    active predecessors at the anchor; completed predecessors retain their
    final value, as framework transform retirement requires.
    """

    def __init__(self, timeline, start, end, variant, autoplay=False):
        if variant not in ("default", "argon", "legacy"):
            raise ValueError(f"Unknown follow variant: {variant}")
        self.variant = variant
        scale, alpha = Channel(1), Channel(0)
        snapshots = []
        if autoplay:
            # Explicit debug perfect-play fallback; not a producer timeline.
            commands = [(start, 0, start, "press"), (end, 1, end, "end")]
        else:
            commands = []
            events = timeline.model_events_at(math.inf) if timeline is not None else ()
            for event in sorted(events, key=lambda e: (e.time_ms, e.sequence)):
                anchor = event.transform_time_ms
                if anchor is None or not math.isfinite(anchor):
                    continue
                action = None
                if event.follow_callback is True:
                    if event.kind == "tracking_published_on":
                        action = "press"
                    elif event.kind == "tracking_published_off":
                        action = "release"
                elif event.kind == "part_judged":
                    if event.hit is False:
                        action = "break"
                    elif event.hit is True:
                        action = "end" if event.part_kind == "tail" else "tick"
                if action is not None:
                    commands.append((event.time_ms, event.sequence, anchor, action))
        latest = end
        for now, sequence, anchor, action in commands:
            scale, alpha = scale.advance(now), alpha.advance(now)
            legacy = variant == "legacy"
            if action == "press":
                if legacy:
                    remaining = max(0, end - now)
                    scale = scale.to(anchor, 1, now=now).to(anchor, 2, min(180, remaining), "out", now)
                    alpha = alpha.to(anchor, 0, now=now).to(anchor, 1, min(60, remaining), now=now)
                else:
                    if abs(alpha.at(now)) < 1e-7:
                        scale = scale.to(anchor, 1, now=now)
                    scale = scale.to(anchor, 2.4, 300, "out_quint", now)
                    alpha = alpha.to(anchor, 1, 300, "out_quint", now)
            elif action == "release" and not legacy:
                scale = scale.to(anchor, 2.88, 150, "out_quint", now)
                alpha = alpha.to(anchor, 0, 150, "out_quint", now)
            elif action == "end":
                scale = scale.to(anchor, 1.6 if legacy else 1, 200 if legacy else 300,
                                 "out" if legacy else "out_quint", now)
                alpha = alpha.to(anchor, 0, 200 if legacy else 150,
                                 "in" if legacy else "out_quint", now)
            elif action == "tick":
                if legacy and scale.at(now) >= 2:
                    scale = scale.to(anchor, 2.2, now=now).to(anchor, 2, 200, now=now)
                elif not legacy and scale.at(now) >= 0.98 * 2.4:
                    scale = scale.to(anchor, 1.08 * 2.4, 40, "out_quint", now)
                    scale = scale.to(anchor + 40, 2.4, 200, "out_quint", now)
            elif action == "break" and legacy:
                scale = scale.to(anchor, 4, 100, now=now)
                alpha = alpha.to(anchor, 0, 100, now=now)
            snapshots.append((now, sequence, scale, alpha))
            latest = max(latest, now, *(s.end for s in scale.segments + alpha.segments))
        self.snapshots = tuple(snapshots)
        self.times = tuple(row[0] for row in snapshots)
        self.finish = latest

    def at(self, time):
        index = bisect.bisect_right(self.times, time) - 1
        if index < 0:
            return FollowState(1, 0)
        _, _, scale, alpha = self.snapshots[index]
        return FollowState(scale.at(time), clamp(alpha.at(time)))


class SliderVisualState:
    """Cached model authority plus conservative unsupported-input fallbacks."""

    def __init__(self, timeline, start, end, variant, autoplay=False):
        self.start, self.end, self.autoplay = start, end, autoplay
        self.events = tuple(sorted(timeline.model_events_at(math.inf),
                                   key=lambda e: (e.time_ms, e.sequence))) if timeline else ()
        self.head = next((e for e in self.events if e.kind == "head_judged"), None)
        self.parent = next((e for e in self.events if e.kind == "slider_judged"), None)
        self.parts = {(e.part_kind, e.result_time_ms): e for e in self.events
                      if e.kind == "part_judged" and e.result_time_ms is not None}
        self.freezes = {e.part_index: e.time_ms for e in self.events if e.kind == "repeat_freeze"}
        self.tail = next((e for e in self.events if e.kind == "part_judged" and e.part_kind == "tail"), None)
        self.follow = FollowAnimation(timeline, start, end, variant, autoplay)
        self.ball = BallAnimation(start, end, self.parent, autoplay)
        self.retire = max(end + 240, self.follow.finish,
                          (self.parent.time_ms + 240) if self.parent else end)

    def head_hit_at(self, time):
        return (time >= self.start if self.autoplay else
                bool(self.head and self.head.hit and time >= self.head.time_ms))

    def parent_alpha(self, time):
        if self.parent is not None:
            if time < self.parent.time_ms or self.parent.transform_time_ms is None:
                return 1.0
            anchor = self.parent.transform_time_ms
        else:
            # Explicit conservative nominal retirement for absent parent data.
            # This does not fabricate a completion notification/follow end.
            anchor = self.end
        return clamp(1 - (time - anchor) / 240)

    def body_alpha(self, time, preempt, fade_in, snaking_out):
        appearance = clamp((time - (self.start - preempt)) / fade_in) if fade_in > 0 else 1.0
        opacity = appearance * self.parent_alpha(time)
        parent = self.parent
        if (parent is not None and parent.hit and time >= parent.time_ms
                and parent.transform_time_ms is not None and snaking_out
                and self.head_hit_at(parent.time_ms)):
            opacity *= clamp(1 - (time - parent.transform_time_ms) / 40)
        return opacity

    def part_at(self, kind, nominal, time):
        event = self.parts.get((kind, nominal))
        return event if event is not None and event.time_ms <= time else None


def repeat_fade_in(time, start, preempt, span, repeat_index, fade_in, snaking_in):
    """ApplyRepeatFadeIn for circle (TimeFadeIn) or arrow (150ms).

    repeat_index is source zero-based: first repeat is 0; final tail is
    span-count minus one. Later end circles use a two-span preempt.
    """
    appear = start - preempt if repeat_index == 0 else start + (repeat_index - 1) * span
    if repeat_index == 0 and snaking_in:
        appear += preempt / 3
    duration = min(span, fade_in) if repeat_index > 0 else fade_in
    return clamp((time - appear) / duration) if duration > 0 else float(time >= appear)


def repeat_alpha(time, start, preempt, span, repeat_index, snaking_in, event=None,
                 fade_in=150):
    opacity = repeat_fade_in(time, start, preempt, span, repeat_index, fade_in, snaking_in)
    if event is not None and event.transform_time_ms is not None:
        duration = min(300, span)
        progress = (time - event.transform_time_ms) / duration if duration > 0 else 1
        opacity *= 1 - ease(progress, "out" if event.hit else "linear")
    return opacity


def draw_slider_layers(renderer, beneath, follow, above, alpha=1.0):
    """Separate draw calls prevent normal/additive batch reordering the ball.

    Shared scene calls both actual backends; their global batching is unchanged.
    """
    def opacity(sprites):
        if alpha >= 1:
            return sprites
        return [replace(s, color=(*s.color[:3], s.color[3] * alpha)) for s in sprites]
    if alpha <= 0:
        return
    if follow:
        for sprites in (beneath, follow, above):
            if sprites:
                renderer.draw(opacity(sprites))
    elif beneath or above:
        renderer.draw(opacity(beneath + above))
