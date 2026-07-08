"""Stacking — RENDER_PLAN.md §2.7 (app/beatmap/stackleniency.go), which is
danser's port of ppy's OsuBeatmapProcessor (MIT; attribution: ppy/osu
osu.Game.Rulesets.Osu/Beatmaps/OsuBeatmapProcessor.cs).

stackDistance = 3.0 px. stackThreshold = floor(Preempt * stackLeniency).
version >= 6 → the "new" backwards-scan algorithm; older → the simple
forward scan. Spinners always keep stack 0. Results are stored per
threshold in each object's stack_index_map and applied as a pixel offset
by HitObject.modify_position (§2.5: stackIndex * CircleRadius / 10).
"""
from __future__ import annotations

import math

from .difficulty import Difficulty
from .objects.base import HitObject
from .objects.slider import Slider
from .objects.spinner import Spinner

STACK_DISTANCE = 3.0


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def process_stacking(hit_objects: list[HitObject], version: int,
                     diff: Difficulty, stack_leniency: float) -> None:
    """§2.7 processStacking (stackleniency.go:23). Mutates stack_index_map."""
    stack_threshold = math.floor(diff.preempt * stack_leniency)

    stacks = [0] * len(hit_objects)
    if stack_leniency > 0:
        if version >= 6:
            _apply_new_stacking(hit_objects, stacks, stack_threshold)
        else:
            _apply_old_stacking(hit_objects, stacks, stack_threshold)

    for obj, stack in zip(hit_objects, stacks):
        obj.stack_index_map[stack_threshold] = 0 if isinstance(obj, Spinner) else stack


def _end_position(obj: HitObject) -> tuple[float, float]:
    """Slider tail (raw path end), else the object position."""
    if isinstance(obj, Slider):
        return obj.multi_curve.position_at(1.0)
    return obj.start_pos_raw


def _apply_new_stacking(objs: list[HitObject], stacks: list[int],
                        stack_threshold: int) -> None:
    """§2.7 applyNewStacking (:43) — ppy's OsuBeatmapProcessor backwards scan:
    start-start and sliderend-start proximity within the threshold, with the
    second pass that redistributes slider-tail stacks."""
    start_index, end_index = 0, len(objs) - 1
    extended_start_index = start_index

    for i in range(end_index, start_index, -1):
        n = i
        obj_i_idx = i
        obj_i = objs[i]
        if stacks[i] != 0 or isinstance(obj_i, Spinner):
            continue

        if isinstance(obj_i, Slider):
            while n - 1 >= start_index:
                n -= 1
                obj_n = objs[n]
                if isinstance(obj_n, Spinner):
                    continue
                if obj_i.get_start_time() - obj_n.get_start_time() > stack_threshold:
                    break
                if _dist(_end_position(obj_n), obj_i.start_pos_raw) < STACK_DISTANCE:
                    stacks[n] = stacks[obj_i_idx] + 1
                    obj_i_idx, obj_i = n, obj_n
        else:  # circle
            while n - 1 >= 0:
                n -= 1
                obj_n = objs[n]
                if isinstance(obj_n, Spinner):
                    continue
                if obj_i.get_start_time() - obj_n.get_end_time() > stack_threshold:
                    break
                if n < extended_start_index:
                    stacks[n] = 0
                    extended_start_index = n
                if isinstance(obj_n, Slider) and \
                        _dist(_end_position(obj_n), obj_i.start_pos_raw) < STACK_DISTANCE:
                    offset = stacks[obj_i_idx] - stacks[n] + 1
                    for j in range(n + 1, i + 1):
                        if _dist(_end_position(obj_n), objs[j].start_pos_raw) < STACK_DISTANCE:
                            stacks[j] -= offset
                    break
                if _dist(obj_n.start_pos_raw, obj_i.start_pos_raw) < STACK_DISTANCE:
                    stacks[n] = stacks[obj_i_idx] + 1
                    obj_i_idx, obj_i = n, obj_n


def _apply_old_stacking(objs: list[HitObject], stacks: list[int],
                        stack_threshold: int) -> None:
    """§2.7 applyOldStacking (:148) — the pre-v6 simple forward scan."""
    for i in range(len(objs)):
        curr = objs[i]
        if stacks[i] != 0 and not isinstance(curr, Slider):
            continue
        start_time = curr.get_end_time()
        slider_stack = 0
        for j in range(i + 1, len(objs)):
            if objs[j].get_start_time() - stack_threshold > start_time:
                break
            pos2 = _end_position(curr)
            if _dist(objs[j].start_pos_raw, curr.start_pos_raw) < STACK_DISTANCE:
                stacks[i] += 1
                start_time = objs[j].get_end_time()
            elif _dist(objs[j].start_pos_raw, pos2) < STACK_DISTANCE:
                slider_stack += 1
                stacks[j] -= slider_stack
                start_time = objs[j].get_end_time()
