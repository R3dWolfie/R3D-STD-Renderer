"""Bounded producer-only benchmark; excludes the unchanged scoring sampler.

    python -m tests.benchmark_slider_chronological

Each fixture has 1ms input frames, 3-span sliders and 4x tick rate. Times are
medians, not performance assertions. The raw head facts are fixture inputs;
this measures shared indexing and chronological production, not click routing.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
import time

from osu_std_renderer.replay.replay import KEY_K1, StdFrame
from osu_std_renderer.ruleset import OsuHitWindows
from osu_std_renderer.ruleset.slider_chronological import (
    SliderReplayInput, chronological_slider,
)
from osu_std_renderer.ruleset.slider_visual import (
    SliderVisualEvent, SliderVisualTimeline,
)
from tests.test_ruleset import _map


def fixture(count):
    bm = _map("".join(f"256,192,{1000 + i * 2000},2,0,L|376:192,3,120\n"
                      for i in range(count)))
    bm.timings.tick_rate = 4
    sliders = bm.hit_objects
    for slider in sliders:
        slider.set_timing(bm.timings, 14)
    frames = []
    for t in range(0, count * 2000 + 1001):
        i = (t - 1000) // 2000
        if 0 <= i < count and t <= sliders[i].get_end_time():
            x, y = sliders[i].position_at(t)
            keys = KEY_K1
        else:
            x, y, keys = 0, 0, 0
        frames.append(StdFrame(t, x, y, keys))
    raw = []
    for slider in sliders:
        start = slider.get_start_time()
        head = SliderVisualEvent(start, 0, "head_judged", "click_routing",
                                 hit=True, part_index=0, part_kind="head",
                                 transform_time_ms=start, channel=1,
                                 input_frame_index=int(start))
        raw.append(SliderVisualTimeline("partial", True, False, (head,)))
    return bm, frames, raw


def benchmark(count, repeats):
    bm, frames, raw = fixture(count)
    index_times, producer_times = [], []
    digest = None
    for _ in range(repeats):
        started = time.perf_counter()
        inputs = SliderReplayInput(frames)
        indexed = time.perf_counter()
        timelines = [chronological_slider(snapshot, inputs, slider, bm.diff,
                                         bm.diff.get_radius(),
                                         OsuHitWindows(bm.diff.od).meh,
                                         snapshot.events[0])
                     for snapshot, slider in zip(raw, bm.hit_objects)]
        ended = time.perf_counter()
        index_times.append((indexed - started) * 1000)
        producer_times.append((ended - indexed) * 1000)
        payload = [[dataclasses.asdict(event)
                    for event in timeline.model_events_at(math.inf)]
                   for timeline in timelines]
        current = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        assert digest is None or digest == current, "Non-deterministic event history"
        digest = current
    return dict(sliders=count, frames=len(frames), step_ms=1, spans=3, tick_rate=4,
                index_ms=round(statistics.median(index_times), 3),
                producer_ms=round(statistics.median(producer_times), 3),
                relevant_frame_visits=sum(t.model_input_frames for t in timelines),
                evaluations=sum(t.model_evaluations for t in timelines),
                model_events=sum(len(t.model_events_at(math.inf)) for t in timelines),
                event_sha256=digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sliders", nargs="+", type=int, default=[25, 50, 100])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1 or any(n < 1 for n in args.sliders):
        parser.error("sliders and repeats must be positive")
    print(json.dumps([benchmark(n, args.repeats) for n in args.sliders], indent=2))


if __name__ == "__main__":
    main()
