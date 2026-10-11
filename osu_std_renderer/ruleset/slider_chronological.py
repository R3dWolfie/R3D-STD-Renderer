"""Source-derived slider notifications on an explicit offline model clock.

This path reads inputs and raw routed heads; it NEVER writes scoring results.
Native drawable cadence is absent from .osr. replay_boundaries_v1 evaluates
decoded frames (including equal-time frames in file order), nominal/eligibility
boundaries, and finite right-limit expiry/flush points. See the contract docs.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, replace

from ..replay.replay import KEY_K1, KEY_K2, KEY_M1, KEY_M2, cursor_at
from .slider_visual import SliderVisualEvent, SliderVisualGap, SliderVisualTimeline


def _channels(keys: int) -> int:
    return ((1 if keys & (KEY_M1 | KEY_K1) else 0)
            | (2 if keys & (KEY_M2 | KEY_K2) else 0))


class SliderReplayInput:
    """One O(F) index shared by all sliders, with O(log F) lifetime slicing."""

    def __init__(self, frames):
        self.frames = frames
        self.times = [frame.time_ms for frame in frames]
        self.channels = [_channels(frame.keys) for frame in frames]
        self.press_frame_indices = []
        previous = 0
        for i, channels in enumerate(self.channels):
            new = channels & ~previous
            for channel in (1, 2):
                if new & channel:
                    self.press_frame_indices.append(i)
            previous = channels

    def state_at(self, time_ms, frame_index=None):
        if frame_index is not None:
            frame = self.frames[frame_index]
            return frame.x, frame.y, self.channels[frame_index]
        i = bisect.bisect_right(self.times, time_ms) - 1
        x, y, _ = cursor_at(self.frames, time_ms)
        return x, y, self.channels[i] if i >= 0 else 0


@dataclass
class _Part:
    index: int
    kind: str
    nominal: float
    hit: bool | None = None


def chronological_slider(raw: SliderVisualTimeline, inputs: SliderReplayInput,
                         slider, diff, radius: float, head_window: float,
                         head_event: SliderVisualEvent | None,
                         ) -> SliderVisualTimeline:
    """Extend a frozen raw snapshot with separately labelled model events."""
    if raw.status == "unavailable":
        return raw
    return _Producer(raw, inputs, slider, diff, radius, head_window,
                     head_event).run()


class _Producer:
    def __init__(self, raw, inputs, slider, diff, radius, head_window, head):
        self.raw, self.inputs, self.slider, self.diff = raw, inputs, slider, diff
        self.radius, self.head_window, self.head = radius, head_window, head
        self.start, self.end = slider.get_start_time(), slider.get_end_time()
        self.deadline = self.start + head_window
        self.expiry = math.nextafter(self.deadline, math.inf)
        self.events = []
        self.gaps = []
        self.tracking = self.published = self.parent_judged = False
        self.head_judged = self.head_hit = False
        self.hit_channel = self.accept_any_after = None
        self.last_actions = 0
        self.evaluations = self.input_frames = 0
        self.last_time = None
        # Raw list identity differs from drawable nominal order for a lenient
        # tail sample. Retain identity, but use the actual tail end for source
        # eligibility/ordering. Never read PartOutcome or reconciled flags.
        legacy_last = max(self.start + (self.end - self.start) / 2,
                          self.end - 36)
        points = sorted(slider.score_points,
                        key=lambda p: legacy_last if p.kind == "last" else p.time)
        self.parts = [_Part(i + 1, {"tick": "tick", "reverse": "repeat",
                                   "last": "tail"}[point.kind],
                            self.end if point.kind == "last" else point.time)
                      for i, point in enumerate(points)]
        self.nested = sorted(self.parts, key=lambda p: (p.nominal, p.index))
        self.ticks = [part for part in self.nested if part.kind == "tick"]
        self.repeats = [part for part in self.nested if part.kind == "repeat"]
        self.tail = next((part for part in self.parts if part.kind == "tail"), None)
        self.tick_i = self.repeat_i = 0
        self.remaining = len(self.parts)

    def gap(self, kind, reason, index=None):
        self.gaps.append(SliderVisualGap(kind, reason, index, "source_model"))

    def emit(self, time, kind, **kwargs):
        self.events.append(SliderVisualEvent(
            time, len(self.events), kind, "source_model", **kwargs))

    def in_area(self, time, cursor, expanded):
        bx, by = self.slider.get_stacked_position_at(time, self.diff)
        rr = self.radius * (2.4 if expanded else 1)
        return (cursor[0] - bx) ** 2 + (cursor[1] - by) ** 2 <= rr * rr

    def update_tracking(self, time, channels, in_area):
        # SliderInputManager.updateTracking/isValidTrackingAction. In
        # particular, lastPressedActions is the PREVIOUS update's actions.
        if self.hit_channel is None:
            self.accept_any_after = None
        elif self.accept_any_after is None:
            other = 2 if self.hit_channel == 1 else 1
            if not self.last_actions & other:
                self.accept_any_after = time
        self.last_actions = channels
        if (self.hit_channel is not None and
                (self.accept_any_after is None or time <= self.accept_any_after)):
            valid_action = bool(channels & self.hit_channel)
        else:
            valid_action = bool(channels)
        all_judged = self.parent_judged and self.head_judged and self.remaining == 0
        tracking = ((not all_judged or time <= self.end)
                    and in_area and valid_action)
        if tracking != self.tracking:
            self.tracking = tracking
            self.emit(time, "tracking_on" if tracking else "tracking_off")

    def publish_tracking(self, time):
        # DrawableSlider.Update runs before its input-manager child Update.
        # FollowCircle only observes this bindable publication, with a parent
        # Judged guard, NOT every internal manager edge.
        if self.published != self.tracking:
            self.published = self.tracking
            forward = not self.parent_judged
            self.emit(time, "tracking_published_on" if self.published
                      else "tracking_published_off", follow_callback=forward,
                      transform_time_ms=max(time, self.start) if forward else None)

    def judge_part(self, time, part, hit):
        if part.hit is not None:
            return
        part.hit = hit
        self.remaining -= 1
        # SliderTick/SliderEndCircle use HitWindows.Empty: max offset = 0.
        # Tail-hit FollowCircle uses the still-unjudged parent's fallback end
        # anchor; a tail miss uses its own clamped result absolute time.
        result_time = min(time, part.nominal)
        anchor = self.end if part.kind == "tail" and hit else result_time
        self.emit(time, "part_judged", hit=hit, part_index=part.index,
                  part_kind=part.kind, result_time_ms=result_time,
                  transform_time_ms=anchor)
        if part.kind == "repeat" and hit:
            self.emit(time, "repeat_freeze", hit=True, part_index=part.index,
                      part_kind="repeat", result_time_ms=result_time)

    def judge_head(self, time, hit, cursor, channels, frame_index=None):
        self.head_judged, self.head_hit = True, hit
        self.hit_channel = self.head.channel if hit and self.head is not None else None
        result_time = min(time, self.start + 400)  # OsuHitWindows.MISS_WINDOW
        self.emit(time, "head_judged", hit=hit, part_index=0, part_kind="head",
                  channel=self.hit_channel, input_frame_index=frame_index,
                  result_time_ms=result_time, transform_time_ms=result_time)
        if not hit or not self.in_area(time, cursor, True):
            return
        # PostProcessHeadJudgement iterates nominally sorted nested objects,
        # tests the CURRENT cursor against each passed path position, then
        # force-hits/misses ALL passed objects. It is not an input-history
        # reconstruction and not the ordinary tracking-before-parts order.
        passed = [part for part in self.nested
                  if part.hit is None and part.nominal <= time]
        all_in_range = all(self.in_area(part.nominal, cursor, True) for part in passed)
        for part in passed:
            self.judge_part(time, part, all_in_range)
        self.update_tracking(time, channels,
                             all_in_range or self.in_area(time, cursor, False))

    def ordinary_parts(self, time):
        if not self.head_judged:
            return
        # DrawableSlider's actual child containers process ticks, repeats,
        # then tail (tail proxy only affects drawing). Advancing queues makes
        # this O(P) over a lifetime, not O(P * number-of-input-frames).
        while self.tick_i < len(self.ticks) and self.ticks[self.tick_i].nominal <= time:
            self.judge_part(time, self.ticks[self.tick_i], self.tracking)
            self.tick_i += 1
        while self.repeat_i < len(self.repeats) and self.repeats[self.repeat_i].nominal <= time:
            self.judge_part(time, self.repeats[self.repeat_i], self.tracking)
            self.repeat_i += 1
        if (self.tail is not None and self.tail.hit is None
                and time >= self.end - 36
                and all(part.hit is not None for part in self.repeats[-1:] + self.ticks[-1:])):
            if self.tracking or time >= self.end:
                self.judge_part(time, self.tail, self.tracking)

    def parent_result(self, time):
        if (self.parent_judged or self.tail is None or self.tail.hit is None
                or time < self.end):
            return
        # DrawableSlider.CheckForResult(false): parent scoring/visual result
        # is independent of the accuracy-scored head and header reconcile.
        hits = int(self.head_hit) + sum(part.hit is True for part in self.parts)
        total = 1 + len(self.parts)
        if self.raw.lazer:
            judgement = "ignore_hit" if hits else "ignore_miss"
        else:
            judgement = ("300" if hits == total else "miss" if hits == 0
                         else "100" if hits / total >= 0.5 else "50")
        self.parent_judged = True
        self.emit(time, "slider_judged", hit=hits > 0, judgement=judgement,
                  result_time_ms=self.end, transform_time_ms=self.end)

    def schedule(self):
        lower = self.start - self.diff.preempt
        if self.head is not None:
            lower = min(lower, self.head.time_ms)
        lower = max(lower, self.inputs.times[0])
        terminal = max(self.end, self.expiry,
                       self.head.time_ms if self.head is not None else self.start)
        points = {lower, self.start, self.end, self.end - 36, self.deadline}
        points.update(part.nominal for part in self.parts)
        # Right-limit evaluations explicitly define the model expiry and
        # flush schedule; they are not claims about a native GPU cadence.
        for boundary in (self.end, self.expiry,
                         self.head.time_ms if self.head is not None else self.expiry):
            points.add(boundary)
            for _ in range(2):
                boundary = math.nextafter(boundary, math.inf)
                points.add(boundary)
        upper = math.nextafter(math.nextafter(terminal, math.inf), math.inf)
        first = bisect.bisect_left(self.inputs.times, lower)
        last = bisect.bisect_right(self.inputs.times, upper)
        # Each decoded input frame is retained, including duplicate map times.
        by_time = {}
        for i in range(first, last):
            by_time.setdefault(self.inputs.times[i], []).append(i)
        points.update(by_time)
        for time in sorted(t for t in points if lower <= t <= upper):
            for index in by_time.get(time, (None,)):
                yield time, index

    def run(self):
        self.gap("callback_order", "native_drawable_clock_unobserved")
        self.gap("tracking", "interpolated_crossings_between_model_updates_unobserved")
        self.gap("head_judged", "head_routing_is_raw_r3d")
        self.gap("part_judged", "source_geometry_not_verified")
        if not self.raw.lazer:
            self.gap("callback_order", "stable_client_semantics_not_verified")
        if self.inputs.times[0] > self.start - self.diff.preempt:
            self.gap("tracking", "pre_replay_input_unavailable")
        if not math.isclose(self.slider.end_time_lazer, self.end, abs_tol=1e-7):
            self.gap("part_judged", "source_slider_duration_differs")
        if self.tail is None:
            self.gap("part_judged", "source_nested_parts_missing")
        valid_duration = self.end > self.start and math.isfinite(self.end)
        if not valid_duration:
            self.gap("part_judged", "invalid_slider_duration")
        else:
            for time, frame_index in self.schedule():
                # A truncated/failed replay supplies no further player input.
                # Only mandatory AllJudged cleanup can be proved after EOF.
                if time > self.inputs.times[-1] and not self.parent_judged:
                    break
                self.last_time = time
                self.evaluations += 1
                self.input_frames += frame_index is not None
                cursor_x, cursor_y, channels = self.inputs.state_at(time, frame_index)
                cursor = (cursor_x, cursor_y)
                if (not self.head_judged and self.head is not None
                        and time == self.head.time_ms
                        and (self.head.input_frame_index is None
                             or frame_index == self.head.input_frame_index)):
                    self.judge_head(time, bool(self.head.hit), cursor, channels, frame_index)
                self.publish_tracking(time)
                self.update_tracking(time, channels, self.in_area(time, cursor, self.tracking))
                self.ordinary_parts(time)
                # headContainer follows the tick/repeat/tail containers.
                # Automatic expiry unblocks children on the NEXT evaluation.
                if not self.head_judged and time > self.deadline:
                    self.judge_head(time, False, cursor, channels)
                self.parent_result(time)
        unresolved = ("replay_ended_before_notification" if valid_duration
                      else "invalid_slider_duration")
        if not self.head_judged:
            self.gap("head_judged", unresolved, 0)
        for part in self.parts:
            if part.hit is None:
                self.gap("part_judged", unresolved, part.index)
        if not self.parent_judged:
            self.gap("slider_judged", "parent_conditions_unestablished")
        # One authoritative merged timeline; raw observations keep their own
        # ordering and provenance. Source-model callback ordering is preserved
        # within its stream. Diagnostic raw events precede model ties.
        merged = sorted(self.raw.events + tuple(self.events),
                        key=lambda e: (e.time_ms, e.provenance == "source_model", e.sequence))
        events = tuple(replace(event, sequence=i) for i, event in enumerate(merged))
        raw_gaps = tuple(replace(gap, provenance="raw_simulation")
                         for gap in self.raw.unavailable)
        return replace(self.raw, events=events, unavailable=raw_gaps + tuple(self.gaps),
                       clock_model="replay_boundaries_v1",
                       model_evaluations=self.evaluations,
                       model_input_frames=self.input_frames, model_end_ms=self.last_time)
