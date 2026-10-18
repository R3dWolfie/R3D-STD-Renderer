"""Source-derived notification constraints on the documented model clock.

Expectations follow SliderInputManager/DrawableSlider and their inspected test
scenes. No assertion calls these times native lazer runtime observations.
"""
from __future__ import annotations

import dataclasses
import math

from osu_std_renderer.replay.replay import KEY_K1, KEY_K2, KEY_M1, KEY_M2
from osu_std_renderer.ruleset import JudgmentKind, StdRuleset
from osu_std_renderer.ruleset.slider_chronological import (
    SliderReplayInput, chronological_slider,
)
from tests.test_ruleset import (
    _frames, _map, _meta, _multi_slider, _perfect_slider_frames,
    _lazer_stats_meta,
)


def _map_slider(length=120, spans=1, multiplier=1, tick_rate=1, curve="L|506:192"):
    bm = _map(f"256,192,1000,2,0,{curve},{spans},{length}\n")
    bm.timings.slider_mult = multiplier
    bm.timings.tick_rate = tick_rate
    slider = bm.hit_objects[0]
    slider.set_timing(bm.timings, 14)
    return bm, slider


def _follow(slider, keys_at=lambda t: KEY_K1, start=1000, step=10):
    end = slider.get_end_time()
    entries = [(t, *slider.position_at(t), keys_at(t))
               for t in range(start, math.ceil(end) + 101, step)]
    return _frames(entries)


def _run(bm, frames, **kwargs):
    result = StdRuleset(bm, frames, **kwargs).run()
    verdict = result.verdict_for(bm.hit_objects[0])
    return result, verdict, verdict.slider_visuals


def _model(timeline, kind=None):
    return [event for event in timeline.model_events_at(math.inf)
            if kind is None or event.kind == kind]


def _gaps(timeline, kind=None):
    return [gap for gap in timeline.unavailable
            if gap.provenance == "source_model" and (kind is None or gap.kind == kind)]


def test_early_on_time_late_head_preserve_press_channel_and_model_provenance():
    for time in (970, 1000, 1100):
        bm, slider = _map_slider()
        frames = _frames([(time, 256, 192, KEY_K2)]
                         + [(t, *slider.position_at(t), KEY_K2)
                            for t in range(max(1110, time + 10), 1701, 10)])
        result, verdict, timeline = _run(bm, frames, lazer=True)
        head, = _model(timeline, "head_judged")
        assert head.hit and head.channel == 2
        assert head.time_ms == head.result_time_ms == head.transform_time_ms == time
        assert frames[head.input_frame_index].time_ms == time
        raw_head, = [event for event in timeline.raw_events_at(math.inf)
                     if event.kind == "head_judged"]
        assert raw_head.time_ms == time and raw_head.channel == 2
        assert timeline.status == "partial" and timeline.clock_model == "replay_boundaries_v1"
        assert any(gap.reason == "native_drawable_clock_unobserved" for gap in _gaps(timeline))


def test_strict_expiry_is_finite_first_model_evaluation_after_success_window():
    bm, slider = _map_slider(length=10)
    frames = _frames([(1000, 0, 0, 0), (1129.5, 0, 0, 0), (1400, 0, 0, 0)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    head, = _model(timeline, "head_judged")
    expiry = math.nextafter(1129.5, math.inf)
    assert not head.hit and math.isfinite(head.time_ms) and head.time_ms == expiry
    assert not [event for event in _model(timeline, "head_judged") if event.time_ms <= 1129.5]
    assert head.channel is None and head.input_frame_index is None
    assert head.result_time_ms == expiry  # 400ms miss offset doesn't clamp this
    tail, = _model(timeline, "part_judged")
    # Ordinary child checks happen before head-container automatic expiry.
    assert tail.time_ms == math.nextafter(expiry, math.inf) > head.time_ms
    assert not tail.hit and tail.transform_time_ms == tail.result_time_ms == 1050
    parent, = _model(timeline, "slider_judged")
    assert parent.time_ms == tail.time_ms and parent.result_time_ms == 1050
    assert verdict.deadline == 1129.5  # pre-existing score/display anchor stays


def test_success_window_boundary_click_still_hits_without_a_model_miss():
    bm, slider = _map_slider(length=10)
    result, verdict, timeline = _run(bm, _frames([(1129.5, 256, 192, KEY_K1),
                                                (1400, 266, 192, KEY_K1)]), lazer=True)
    head, = _model(timeline, "head_judged")
    assert head.hit and head.time_ms == 1129.5


def test_short_slider_head_after_end_catch_up_and_parent_are_distinct():
    bm, slider = _map_slider(length=10)
    frames = _frames([(1120, 256, 192, KEY_K1), (1300, 266, 192, KEY_K1)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    head, = _model(timeline, "head_judged")
    tail, = _model(timeline, "part_judged")
    parent, = _model(timeline, "slider_judged")
    assert head.time_ms == tail.time_ms == parent.time_ms == 1120
    assert head.sequence < tail.sequence < parent.sequence
    assert head.hit and tail.hit and parent.hit
    assert tail.result_time_ms == tail.transform_time_ms == parent.transform_time_ms == 1050
    # The legacy scorer evaluates a missed tail before this click. Both facts
    # survive independently; the producer does not rewrite scoring to agree.
    assert not verdict.parts[tail.part_index].hit
    assert parent.judgement == "ignore_hit"


def test_forced_miss_uses_raw_lazer_routing_press_and_unblocks_nested_model():
    bm = _map("100,100,1000,2,0,L|120:100,1,20\n300,300,1100,1,0,0:0:0:0:\n")
    result, verdict, timeline = _run(bm, _frames([(1050, 300, 300, KEY_K1),
                                                (1400, 300, 300, 0)]), lazer=True)
    head, = _model(timeline, "head_judged")
    assert not head.hit and head.time_ms == 1050 and head.channel is None
    assert head.input_frame_index == 1 and verdict.deadline == 1050
    assert not [event for event in _model(timeline, "head_judged") if event.time_ms > 1050]


def test_opposite_preheld_channel_cannot_take_over_when_head_tap_releases():
    bm, slider = _map_slider()
    frames = _frames([(900, 0, 0, KEY_K1), (990, 256, 192, KEY_K1)]
                     + [(t, *slider.position_at(t), KEY_K1 | KEY_K2 if t < 1100 else KEY_K1)
                        for t in range(1000, 1701, 10)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    head, = _model(timeline, "head_judged")
    assert head.channel == 2
    assert [(event.time_ms, event.kind) for event in _model(timeline)
            if event.kind == "tracking_off" and event.time_ms <= 1500] == [(1100, "tracking_off")]
    tick, = [event for event in _model(timeline, "part_judged") if event.part_kind == "tick"]
    assert not tick.hit and verdict.parts[tick.part_index].hit  # raw any-key model differs


def test_both_simultaneously_pressed_then_one_released_can_keep_tracking():
    bm, slider = _map_slider()
    frames = _follow(slider, lambda t: KEY_K1 | KEY_K2 if t < 1100 else KEY_K2)
    result, verdict, timeline = _run(bm, frames, lazer=True)
    assert _model(timeline, "head_judged")[0].channel == 1
    assert all(event.hit for event in _model(timeline, "part_judged"))
    assert not [event for event in _model(timeline, "tracking_off") if event.time_ms <= 1500]


def test_valid_channel_switch_after_initial_other_key_was_not_preheld():
    bm, slider = _map_slider()
    frames = _follow(slider, lambda t: KEY_K1 if t < 1100
                     else KEY_K1 | KEY_K2 if t < 1200 else KEY_K2)
    result, verdict, timeline = _run(bm, frames, lazer=True)
    assert all(event.hit for event in _model(timeline, "part_judged"))
    assert not [event for event in _model(timeline, "tracking_off") if event.time_ms <= 1500]


def test_any_key_acceptance_uses_previous_actions_and_strict_after_time():
    bm, slider = _map_slider()
    frames = _frames([(900, 0, 0, KEY_K1), (990, 256, 192, KEY_K1),
                      (1000, 256, 192, KEY_K1 | KEY_K2),
                      (1100, 276, 192, 0), (1110, 278, 192, KEY_K1),
                      (1120, 280, 192, KEY_K1), (1700, 376, 192, KEY_K1)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    edges = [(event.time_ms, event.kind) for event in _model(timeline)
             if event.kind in ("tracking_on", "tracking_off")]
    assert (1100, "tracking_off") in edges
    assert (1110, "tracking_on") not in edges
    assert (1120, "tracking_on") in edges


def test_mouse_keyboard_equivalence_does_not_create_extra_head_presses():
    for first, second, channel in ((KEY_M1, KEY_K1, 1), (KEY_M2, KEY_K2, 2)):
        bm, slider = _map_slider()
        frames = _follow(slider, lambda t: first if t < 1100 else first | second if t < 1200 else second)
        result, verdict, timeline = _run(bm, frames, lazer=True)
        head, = _model(timeline, "head_judged")
        assert head.channel == channel and head.time_ms == 1000
        assert all(event.hit for event in _model(timeline, "part_judged"))


def test_pre_start_tracking_follow_publication_clamps_anchor_to_start():
    bm, slider = _map_slider()
    result, verdict, timeline = _run(bm, _frames([(900, 256, 192, KEY_K1),
                                                (1700, 376, 192, KEY_K1)]), lazer=True)
    head, = _model(timeline, "head_judged")
    press = _model(timeline, "tracking_published_on")[0]
    assert head.hit and press.time_ms == 900 < verdict.start_time
    assert press.follow_callback is True and press.transform_time_ms == 1000


def test_pending_post_end_tracking_reacquires_then_parent_blocks_follow_release():
    bm, slider = _map_slider(length=10)
    frames = _frames([(900, 0, 0, KEY_K2), (1000, 256, 192, KEY_K2),
                      (1055, 0, 0, 0), (1060, 0, 0, KEY_K2),
                      (1070, 266, 192, KEY_K2),
                      (1120, 256, 192, KEY_K1 | KEY_K2), (1300, 266, 192, KEY_K2)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    assert (1070, "tracking_on") in [(event.time_ms, event.kind) for event in _model(timeline)]
    parent, = _model(timeline, "slider_judged")
    assert parent.time_ms == 1120 > verdict.end_time
    publications = [event for event in _model(timeline) if event.kind.startswith("tracking_published_")]
    assert any(event.time_ms > verdict.end_time and event.follow_callback for event in publications)
    ignored = [event for event in publications if event.time_ms > parent.time_ms]
    assert ignored and all(event.follow_callback is False and event.transform_time_ms is None for event in ignored)
    assert not [event for event in publications if event.follow_callback and event.time_ms > parent.time_ms]


def test_ordinary_tracking_publication_precedes_child_update_and_part_notification():
    bm, slider = _map_slider()
    frames = _frames([(1000, 256, 192, KEY_K1), (1400, 336, 192, 0),
                      (1500, 356, 192, KEY_K1), (1700, 376, 192, KEY_K1)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    at_tick = [event for event in _model(timeline) if event.time_ms == 1500]
    assert [event.kind for event in at_tick] == [
        "tracking_published_off", "tracking_on", "part_judged"]
    assert at_tick[0].follow_callback and at_tick[-1].hit


def test_multiple_spans_notify_repeat_success_and_freeze_at_actual_boundary():
    for spans in (1, 2, 3, 4):
        bm, slider = _map_slider(spans=spans)
        result, verdict, timeline = _run(bm, _follow(slider), lazer=True)
        repeats = [event for event in _model(timeline, "part_judged") if event.part_kind == "repeat"]
        freezes = _model(timeline, "repeat_freeze")
        assert len(repeats) == len(freezes) == spans - 1
        assert all(event.hit for event in _model(timeline, "part_judged"))
        assert [(event.part_index, event.time_ms) for event in repeats] == [
            (event.part_index, event.time_ms) for event in freezes]
        assert all(event.transform_time_ms is None for event in freezes)
        assert _model(timeline, "slider_judged")[0].time_ms == slider.get_end_time()


def test_repeat_miss_has_no_successful_freeze():
    bm, slider = _map_slider(spans=2)
    frames = _follow(slider, lambda t: KEY_K1 if t < 1500 else 0)
    result, verdict, timeline = _run(bm, frames, lazer=True)
    repeat, = [event for event in _model(timeline, "part_judged") if event.part_kind == "repeat"]
    assert not repeat.hit and not _model(timeline, "repeat_freeze")


def test_early_tail_hit_and_delayed_miss_use_distinct_notifications_and_anchors():
    bm, slider = _map_slider(length=10)
    for held, time in ((True, 1014), (False, 1050)):
        frames = _frames([(1000, 256, 192, KEY_K1),
                          (1001, 256, 192, KEY_K1 if held else 0),
                          (1400, 266, 192, KEY_K1 if held else 0)])
        result, verdict, timeline = _run(bm, frames, lazer=True)
        tail, = _model(timeline, "part_judged")
        assert tail.hit == held and tail.time_ms == time
        assert tail.result_time_ms == time and tail.transform_time_ms == 1050
        assert _model(timeline, "slider_judged")[0].time_ms == 1050


def test_sub_36ms_tail_eligibility_does_not_use_legacy_half_duration_clamp():
    bm, slider = _map_slider(length=2)  # duration 10ms; tail eligible at 974
    result, verdict, timeline = _run(bm, _frames([(980, 256, 192, KEY_K1),
                                                (1400, 258, 192, KEY_K1)]), lazer=True)
    tail, = _model(timeline, "part_judged")
    assert tail.hit and tail.time_ms == 980 < verdict.start_time
    assert tail.result_time_ms == 980 and tail.transform_time_ms == 1010
    assert _model(timeline, "slider_judged")[0].time_ms == 1010


def test_tail_waits_for_last_repeat_even_when_leniency_boundary_is_earlier():
    for spans in (2, 3, 4):
        bm, slider = _map_slider(length=5, spans=spans)
        result, verdict, timeline = _run(bm, _follow(slider, step=1), lazer=True)
        parts = _model(timeline, "part_judged")
        tail, = [event for event in parts if event.part_kind == "tail"]
        last_repeat = max((event for event in parts if event.part_kind == "repeat"),
                          key=lambda event: event.time_ms)
        assert tail.time_ms >= last_repeat.time_ms
        assert tail.sequence > last_repeat.sequence
        assert tail.time_ms == max(slider.get_end_time() - 36, last_repeat.time_ms)


def test_late_head_catch_up_can_hit_while_raw_samples_miss_and_anchor_is_clamped():
    bm, slider = _map_slider(length=20, tick_rate=10)
    result, verdict, timeline = _run(bm, _frames([(1090, 256, 192, KEY_K1),
                                                (1300, 276, 192, KEY_K1)]), lazer=True)
    tick, = [event for event in _model(timeline, "part_judged") if event.part_kind == "tick"]
    assert tick.hit and tick.time_ms == 1090
    assert tick.result_time_ms == tick.transform_time_ms == 1050
    assert not verdict.parts[tick.part_index].hit
    at_head = [event.kind for event in _model(timeline) if event.time_ms == 1090]
    assert at_head[:4] == ["head_judged", "part_judged", "tracking_on", "tracking_published_on"]


def test_late_repeat_freezes_at_notification_not_its_earlier_fade_anchor():
    bm, slider = _map_slider(length=10, spans=2)
    result, verdict, timeline = _run(bm, _frames([(1090, 256, 192, KEY_K1),
                                                (1300, 256, 192, KEY_K1)]), lazer=True)
    repeat, = [event for event in _model(timeline, "part_judged") if event.part_kind == "repeat"]
    freeze, = _model(timeline, "repeat_freeze")
    assert repeat.hit and repeat.time_ms == freeze.time_ms == 1090
    assert repeat.transform_time_ms == 1050 < freeze.time_ms
    assert freeze.transform_time_ms is None


def test_expanded_catch_up_enables_tracking_outside_the_entry_radius():
    bm, slider = _map_slider(length=200, multiplier=5, tick_rate=10)
    result, verdict, timeline = _run(bm, _frames([(1090, 292, 192, KEY_K1),
                                                (1400, 456, 192, KEY_K1)]), lazer=True)
    assert slider.position_at(1090)[0] - 292 > bm.diff.get_radius()
    assert any(event.time_ms == 1090 for event in _model(timeline, "tracking_on"))
    tick = [event for event in _model(timeline, "part_judged") if event.time_ms == 1090]
    assert tick and all(event.hit for event in tick)
    assert not verdict.tracked_at(1090)


def test_outside_expanded_area_does_not_force_catch_up_success():
    bm, slider = _map_slider(length=500, multiplier=10, tick_rate=20)
    result, verdict, timeline = _run(bm, _frames([(1090, 256, 192, KEY_K1),
                                                (1400, 756, 192, KEY_K1)]), lazer=True)
    passed = [event for event in _model(timeline, "part_judged") if event.time_ms == 1090]
    assert passed and all(not event.hit for event in passed)
    assert not _model(timeline, "tracking_on") or _model(timeline, "tracking_on")[0].time_ms > 1090


def test_overlap_catch_up_checks_all_passed_path_positions_not_only_current_ball():
    bm, slider = _map_slider(length=420, multiplier=20, tick_rate=20,
                             curve="L|456:192|256:192|276:192")
    result, verdict, timeline = _run(bm, _frames([(1100, 256, 192, KEY_K1),
                                                (1400, 276, 192, KEY_K1)]), lazer=True)
    assert math.dist(slider.position_at(1100), (256, 192)) < bm.diff.get_radius()
    ticks = [event for event in _model(timeline, "part_judged") if event.part_kind == "tick"]
    assert len(ticks) == 3 and all(event.time_ms == 1100 and not event.hit for event in ticks)
    tail, = [event for event in _model(timeline, "part_judged") if event.part_kind == "tail"]
    assert tail.hit and tail.time_ms == 1100 and tail.sequence > ticks[-1].sequence


def test_model_parent_visual_result_is_separate_from_lazer_head_accuracy():
    bm, slider = _map_slider()
    frames = _frames([(900, 0, 0, KEY_K1)]
                     + [(t, *slider.position_at(t), KEY_K1) for t in range(1000, 1701, 10)])
    for lazer, judgement in ((True, "ignore_hit"), (False, "100")):
        result, verdict, timeline = _run(bm, frames, lazer=lazer)
        head, = _model(timeline, "head_judged")
        parent, = _model(timeline, "slider_judged")
        assert not head.hit and parent.hit and parent.judgement == judgement
        if lazer:
            assert verdict.kind is JudgmentKind.MISS
        else:
            assert any(gap.reason == "stable_client_semantics_not_verified" for gap in _gaps(timeline))


def test_equal_time_input_frames_keep_decoded_transitions_and_head_frame_identity():
    bm, slider = _map_slider(length=10)
    frames = _frames([(1000, 256, 192, KEY_K1), (1000, 256, 192, 0),
                      (1000, 256, 192, KEY_K2), (1400, 266, 192, KEY_K2)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    head, = _model(timeline, "head_judged")
    assert head.channel == 1 and head.input_frame_index == 1
    assert [(event.kind, event.time_ms) for event in _model(timeline)
            if event.kind in ("tracking_on", "tracking_off") and event.time_ms == 1000] == [
                ("tracking_on", 1000), ("tracking_off", 1000)]
    assert timeline.model_input_frames == 3  # tail is done before the final distant frame


def test_sub_ms_key_pulse_is_in_model_but_missing_from_preserved_raw_samples():
    bm, slider = _map_slider()
    frames = _frames([(1000, 256, 192, KEY_K1), (1100, 276, 192, 0),
                      (1200.25, 296, 192, KEY_K1), (1200.75, 296, 192, 0),
                      (1700, 376, 192, 0)])
    result, verdict, timeline = _run(bm, frames, lazer=True)
    assert (1200.25, "tracking_on") in [(event.time_ms, event.kind) for event in _model(timeline)]
    assert (1200.75, "tracking_off") in [(event.time_ms, event.kind) for event in _model(timeline)]
    assert not [event for event in timeline.raw_events_at(math.inf) if 1200 < event.time_ms < 1201]


def test_reconciliation_and_classic_selection_cannot_mutate_model_events():
    bm = _multi_slider(8)
    frames = _perfect_slider_frames(bm)
    meta = _lazer_stats_meta(8, large_tick_hit=14, large_tick_miss=2,
                            slider_tail_hit=5, max_large_tick=16,
                            max_slider_tail=8, max_combo=24)
    for classic in (False, True):
        selected = dataclasses.replace(meta, has_classic_mod=classic)
        raw = StdRuleset(bm, frames, selected, reconcile=False).run()
        final = StdRuleset(bm, frames, selected).run()
        for slider in bm.hit_objects:
            raw_timeline = raw.verdict_for(slider).slider_visuals
            final_timeline = final.verdict_for(slider).slider_visuals
            assert raw_timeline == final_timeline
            assert final_timeline.classic == classic and final_timeline.lazer != classic


def test_failed_or_truncated_input_keeps_future_notifications_unavailable():
    bm, slider = _map_slider(length=250)
    frames = _frames([(1000, 256, 192, KEY_K1), (1100, 276, 192, 0)])
    result, verdict, timeline = _run(bm, frames, meta=_meta(0, 0, 0, 0), reconcile=False)
    assert not _model(timeline, "part_judged") and not _model(timeline, "slider_judged")
    assert all(event.time_ms <= 1100 for event in _model(timeline))
    assert len([gap for gap in _gaps(timeline, "part_judged")
                if gap.reason == "replay_ended_before_notification"]) == len(verdict.parts) - 1
    assert any(gap.reason == "parent_conditions_unestablished" for gap in _gaps(timeline))
    missed = _run(bm, _frames([(1100, 0, 0, 0)]))[2]
    assert not _model(missed, "head_judged")
    assert any(gap.reason == "replay_ended_before_notification" for gap in _gaps(missed, "head_judged"))


def test_no_input_stays_unavailable_and_never_acquires_a_model_clock():
    bm, slider = _map_slider()
    result, verdict, timeline = _run(bm, [])
    assert timeline.status == "unavailable" and not timeline.events
    assert timeline.clock_model is None and timeline.model_evaluations == 0
    assert {gap.reason for gap in timeline.unavailable} == {"no_replay_frames"}


def test_arbitrary_seek_order_is_immutable_and_repeated_producers_are_identical():
    bm, slider = _map_slider(spans=3)
    frames = _follow(slider)
    ruleset = StdRuleset(bm, frames, lazer=True)
    timeline = ruleset.run().verdict_for(slider).slider_visuals
    times = (2200, 900, 3000, 1000, 2200)
    prefixes = [timeline.model_events_at(time) for time in times]
    assert prefixes[0] == prefixes[-1]
    assert ruleset.run().verdict_for(slider).slider_visuals == timeline
    assert [event.sequence for event in timeline.events] == list(range(len(timeline.events)))
    for time in times:
        assert timeline.events_at(time) == tuple(event for event in timeline.events if event.time_ms <= time)


def test_frame_index_is_shared_and_only_relevant_lifetimes_are_visited():
    bm, slider = _map_slider(length=10)
    frames = _frames([(t, 266, 192, 0) for t in range(10, 100001, 10)])
    inputs = SliderReplayInput(frames)
    assert len(inputs.times) == len(frames)
    result, verdict, timeline = _run(bm, frames, lazer=True)
    assert timeline.model_input_frames < 100  # not all 10,001 frames per slider
    assert timeline.model_evaluations < 120


def test_geometry_and_duration_assumptions_are_machine_readable():
    bm, slider = _map_slider()
    slider.end_time_lazer += 1
    timeline = _run(bm, _follow(slider), lazer=True)[2]
    reasons = {gap.reason for gap in _gaps(timeline)}
    assert "source_geometry_not_verified" in reasons
    assert "source_slider_duration_differs" in reasons
    assert all(event.provenance == "source_model" for event in _model(timeline))
    assert timeline.status == "partial"


def test_missing_tail_cannot_fabricate_parent_completion():
    bm, slider = _map_slider()
    frames = _follow(slider)
    original = _run(bm, frames, lazer=True)[2]
    raw = dataclasses.replace(original, events=original.raw_events_at(math.inf),
                              unavailable=())
    slider.score_points = [point for point in slider.score_points if point.kind != "last"]
    timeline = chronological_slider(raw, SliderReplayInput(frames), slider,
                                    bm.diff, bm.diff.get_radius(), 129.5, raw.events[0])
    assert not _model(timeline, "slider_judged")
    assert not [event for event in _model(timeline, "part_judged") if event.part_kind == "tail"]
    assert "source_nested_parts_missing" in {gap.reason for gap in _gaps(timeline)}


def test_invalid_duration_preserves_unknown_without_claiming_replay_eof():
    bm, slider = _map_slider()
    frames = _follow(slider)
    original = _run(bm, frames, lazer=True)[2]
    raw = dataclasses.replace(original, events=original.raw_events_at(math.inf),
                              unavailable=())
    slider.end_time = slider.start_time
    timeline = chronological_slider(raw, SliderReplayInput(frames), slider,
                                    bm.diff, bm.diff.get_radius(), 129.5, raw.events[0])
    assert not _model(timeline) and timeline.model_evaluations == 0
    reasons = {gap.reason for gap in _gaps(timeline)}
    assert "invalid_slider_duration" in reasons
    assert "replay_ended_before_notification" not in reasons
