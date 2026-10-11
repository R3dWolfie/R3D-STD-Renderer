"""Truthful raw event authority; unsupported source notifications stay unknown.

The existing score/part/tracking decisions are intentionally retained. These
tests distinguish them from drawable callbacks rather than blessing nominal
part times as exact lazer judgement times.
"""
from __future__ import annotations

import dataclasses

from osu_std_renderer.replay.replay import KEY_K1, KEY_K2
from osu_std_renderer.ruleset import (
    JudgmentKind, ObjectVerdict, SliderVisualEvent, SliderVisualTimeline,
    StdRuleset,
)
from tests.test_ruleset import (
    _ball_frames, _frames, _lazer_stats_meta, _map, _meta, _multi_slider,
    _perfect_slider_frames,
)


def _slider(spans=1, length=250):
    bm = _map(f"256,192,1000,2,0,L|506:192,{spans},{length}\n")
    return bm, bm.hit_objects[0]


def _run(bm, frames, **kwargs):
    result = StdRuleset(bm, frames, **kwargs).run()
    verdict = result.verdict_for(bm.hit_objects[0])
    return verdict, verdict.slider_visuals


def _events(timeline, kind):
    return [event for event in timeline.events if event.kind == kind]


def _has_gap(timeline, kind, reason=None, index=None):
    return any(gap.kind == kind and (reason is None or gap.reason == reason)
               and (index is None or gap.part_index == index)
               for gap in timeline.unavailable)


def test_head_click_notification_is_raw_and_distinct_from_head_display_time():
    bm, slider = _slider()
    frames = _frames([(970, 256, 192, KEY_K1)]
                     + _ball_frames(slider, 1000, slider.get_end_time()))
    verdict, timeline = _run(bm, frames)
    head, = _events(timeline, "head_judged")
    assert head.time_ms == head.transform_time_ms == 970
    assert head.hit is True and head.part_index == 0 and head.part_kind == "head"
    assert head.provenance == "click_routing"
    assert verdict.parts[0].time == 1000
    assert timeline.status == "partial"
    assert _has_gap(timeline, "slider_judged")
    assert _has_gap(timeline, "tracking", "pre_start_tracking_not_simulated")


def test_automatic_head_miss_does_not_fabricate_a_notification_at_deadline():
    bm, slider = _slider()
    frames = _frames([(900, 0, 0, KEY_K1)]
                     + _ball_frames(slider, 1010, slider.get_end_time()))
    verdict, timeline = _run(bm, frames)
    assert not verdict.parts[0].hit and verdict.deadline == 1129.5
    assert not _events(timeline, "head_judged")
    assert _has_gap(timeline, "head_judged",
                    "head_window_notification_not_simulated", 0)


def test_lazer_forced_head_miss_records_the_routing_press_not_parent_judgement():
    bm = _map("100,100,1000,2,0,L|120:100,1,20\n"
              "300,300,1100,1,0,0:0:0:0:\n")
    frames = _frames([(1050, 300, 300, KEY_K1)])
    verdict, timeline = _run(bm, frames, lazer=True)
    head, = _events(timeline, "head_judged")
    assert not head.hit and head.time_ms == head.transform_time_ms == 1050
    assert verdict.deadline == 1050
    assert not _events(timeline, "slider_judged")
    # Under stable notelock this click is blocked: there is no forced event.
    stable, stable_timeline = _run(bm, frames, lazer=False)
    assert stable.deadline == 1129.5
    assert not _events(stable_timeline, "head_judged")


def test_tracking_gain_loss_regain_are_observed_edges_without_synthetic_end_off():
    bm, slider = _slider()
    entries = []
    for t in range(990, 2251, 10):
        x, y = slider.position_at(t)
        keys = KEY_K1 if (1000 <= t < 1200 or t >= 1300) else 0
        entries.append((t, x, y, keys))
    verdict, timeline = _run(bm, _frames(entries))
    edges = [event for event in timeline.events if event.kind.startswith("tracking_")]
    assert [(event.time_ms, event.kind) for event in edges] == [
        (1000, "tracking_on"), (1200, "tracking_off"), (1300, "tracking_on")]
    assert all(event.provenance == "tracking_sample"
               and event.transform_time_ms is None for event in edges)
    assert verdict.tracking[-1][1] == timeline.tracking_sample_end_ms == 2250
    assert _has_gap(timeline, "tracking", "post_end_tracking_not_simulated")
    assert _has_gap(timeline, "tracking", "parent_judged_guard_not_simulated")


def test_tail_sample_time_is_not_a_tail_notification_or_end_animation_anchor():
    bm, slider = _slider()
    for keys in (0, KEY_K1):
        frames = _frames(_ball_frames(slider, 1000, 2250, keys=keys))
        verdict, timeline = _run(bm, frames)
        tail, = [event for event in _events(timeline, "part_sampled")
                 if event.part_kind == "tail"]
        assert tail.time_ms == 2214 and tail.hit == bool(keys)
        assert verdict.parts[tail.part_index].time == 2250
        assert tail.transform_time_ms is None and tail.provenance == "part_sample"
        assert not _events(timeline, "part_judged")
        assert _has_gap(timeline, "part_judged",
                        "tail_notification_and_anchor_not_simulated", tail.part_index)


def test_short_slider_late_head_keeps_post_end_and_delayed_parts_unavailable():
    bm, slider = _slider(length=10)  # end=1050, window closes at 1129.5
    frames = _frames([(900, 0, 0, KEY_K2),
                      (1000, 256, 192, KEY_K2),
                      (1120, 256, 192, KEY_K1 | KEY_K2)])
    verdict, timeline = _run(bm, frames, lazer=True)
    head, = _events(timeline, "head_judged")
    tail, = _events(timeline, "part_sampled")
    assert head.time_ms == 1120 > verdict.end_time == 1050
    assert tail.time_ms == 1025 and tail.hit
    assert timeline.tracking_sample_end_ms == 1050
    assert not [event for event in timeline.events
                if event.kind.startswith("tracking_") and event.time_ms > 1050]
    assert _has_gap(timeline, "part_judged", "head_pending_at_sample", tail.part_index)
    assert not _events(timeline, "part_judged")
    assert not _events(timeline, "slider_judged")
    assert _has_gap(timeline, "tracking", "post_end_tracking_not_simulated")


def test_short_slider_head_miss_does_not_extend_tracking_to_its_window_close():
    bm, slider = _slider(length=10)
    verdict, timeline = _run(bm, _frames([(900, 0, 0, KEY_K1),
                                        (1000, 256, 192, KEY_K1)]))
    assert verdict.deadline > verdict.end_time
    assert all(event.time_ms <= verdict.end_time for event in timeline.events)
    assert _has_gap(timeline, "head_judged")
    assert _has_gap(timeline, "part_judged", "head_pending_at_sample", 1)


def test_late_head_with_passed_tick_does_not_invent_catch_up_success():
    bm, slider = _slider(length=20)
    bm.timings.tick_rate = 10
    slider.set_timing(bm.timings, 14)
    frames = _frames([(1100, 256, 192, KEY_K1)])
    verdict, timeline = _run(bm, frames, lazer=True)
    head, = _events(timeline, "head_judged")
    pending = [event for event in _events(timeline, "part_sampled")
               if event.time_ms < head.time_ms]
    assert any(event.part_kind == "tick" for event in pending)
    assert all(event.hit is False for event in pending)
    assert all(_has_gap(timeline, "part_judged", "head_pending_at_sample",
                        event.part_index) for event in pending)
    assert not _events(timeline, "part_judged")


def test_tail_which_samples_before_final_repeat_is_not_promoted_to_judgement():
    bm, slider = _slider(spans=3, length=5)  # 25ms spans
    frames = _frames(_ball_frames(slider, 1000, slider.get_end_time(), step=1))
    verdict, timeline = _run(bm, frames, lazer=True)
    samples = _events(timeline, "part_sampled")
    tail, = [event for event in samples if event.part_kind == "tail"]
    last_repeat = max(event.time_ms for event in samples if event.part_kind == "repeat")
    assert tail.time_ms < last_repeat
    assert _has_gap(timeline, "part_judged",
                    "tail_notification_and_anchor_not_simulated", tail.part_index)
    assert not _events(timeline, "part_judged")


def test_single_and_multiple_spans_keep_raw_part_indices_and_repeat_identity():
    for spans in (1, 2, 3):
        bm, slider = _slider(spans=spans, length=120)
        verdict, timeline = _run(bm, _perfect_slider_frames(bm))
        samples = _events(timeline, "part_sampled")
        assert sum(event.part_kind == "repeat" for event in samples) == spans - 1
        assert [event.part_index for event in samples] == list(range(1, len(verdict.parts)))
        assert all(event.part_kind == verdict.parts[event.part_index].kind
                   and event.hit == verdict.parts[event.part_index].hit
                   for event in samples)
        assert all(_has_gap(timeline, "part_judged", index=event.part_index)
                   for event in samples)


def test_equal_time_order_is_deterministic_raw_order_with_source_order_gap():
    bm, slider = _slider()
    frames = _frames([(1000, 256, 192, KEY_K1), (1400, 336, 192, 0),
                      (1500, 356, 192, KEY_K1)]
                     + _ball_frames(slider, 1510, 2250))
    verdict, timeline = _run(bm, frames)
    assert [event.kind for event in timeline.events_at(1000)] == [
        "head_judged", "tracking_on"]
    at_tick = [event for event in timeline.events if event.time_ms == 1500]
    assert [event.kind for event in at_tick] == ["tracking_on", "part_sampled"]
    assert [event.sequence for event in timeline.events] == list(range(len(timeline.events)))
    assert _has_gap(timeline, "callback_order", "drawable_callback_order_not_simulated")
    assert _run(bm, frames)[1] == timeline


def test_sub_millisecond_key_pulse_is_not_claimed_observed_or_bounded_by_one_ms():
    bm, slider = _slider()
    frames = _frames([(1000, 256, 192, KEY_K1), (1100, 276, 192, 0),
                      (1200.25, 296, 192, KEY_K1), (1200.75, 296, 192, 0)])
    verdict, timeline = _run(bm, frames, lazer=True)
    edges = [event for event in timeline.events if event.kind.startswith("tracking_")]
    assert [(event.time_ms, event.kind) for event in edges] == [
        (1000, "tracking_on"), (1100, "tracking_off")]
    assert timeline.sampling_step_ms == 1
    assert _has_gap(timeline, "tracking", "between_sample_edges_not_observed")


def test_lazer_part_reconciliation_changes_results_without_mutating_raw_timeline():
    bm = _multi_slider(8)
    frames = _perfect_slider_frames(bm)
    meta = _lazer_stats_meta(8, large_tick_hit=14, large_tick_miss=2,
                            slider_tail_hit=5, max_large_tick=16,
                            max_slider_tail=8, max_combo=24)
    raw = StdRuleset(bm, frames, meta, reconcile=False).run()
    final = StdRuleset(bm, frames, meta).run()
    discrepancies = []
    for slider in bm.hit_objects:
        raw_v, final_v = raw.verdict_for(slider), final.verdict_for(slider)
        assert raw_v.slider_visuals == final_v.slider_visuals
        for event in _events(final_v.slider_visuals, "part_sampled"):
            assert event.hit == raw_v.parts[event.part_index].hit
            discrepancies.append(event.hit != final_v.parts[event.part_index].hit)
    assert any(discrepancies)
    assert final.final_max_combo == 24


def test_stable_reconciliation_cannot_create_a_raw_phantom_head_event():
    bm, slider = _slider()
    frames = _frames([(3000, 0, 0, 0)])
    raw_v, raw_timeline = _run(bm, frames)
    final_v, final_timeline = _run(bm, frames, meta=_meta(1, 0, 0, 0))
    assert raw_v.kind is JudgmentKind.MISS
    assert final_v.kind is JudgmentKind.HIT300 and final_v.parts[0].hit
    assert final_v.hit_time == 1000  # existing scoring reconciliation, unchanged
    assert final_timeline == raw_timeline
    assert not _events(final_timeline, "head_judged")


def test_count_and_max_combo_relocation_leave_captured_head_truth_untouched():
    bm = _multi_slider(12)
    ruleset = StdRuleset(bm, _perfect_slider_frames(bm), lazer=True)
    sims = ruleset.simulate()
    captured = tuple(s.slider_visuals for s in sims)
    assert ruleset.reconcile_to_counts(sims, (10, 0, 0, 2)) == 2
    assert ruleset._reconcile_max_combo(sims, 20)
    assert tuple(s.slider_visuals for s in sims) == captured
    assert any(not s.head_hit for s in sims)
    assert all(_events(timeline, "head_judged")[0].hit for timeline in captured)


def test_replay_engine_and_classic_provenance_follow_existing_selection():
    bm, slider = _slider()
    frames = _perfect_slider_frames(bm)
    base = _meta(1, 0, 0, 0)
    stable_meta = dataclasses.replace(base, game_version=20191107)
    lazer_meta = dataclasses.replace(base, game_version=30_000_017)
    classic_meta = dataclasses.replace(lazer_meta, has_classic_mod=True)
    for meta, lazer, classic in ((stable_meta, False, False),
                                 (lazer_meta, True, False),
                                 (classic_meta, False, True)):
        verdict, timeline = _run(bm, frames, meta=meta)
        assert timeline.lazer == lazer and timeline.classic == classic
        assert timeline.status == "partial"
        assert _has_gap(timeline, "tracking", "tracking_action_restriction_not_simulated")


def test_no_input_is_unavailable_and_no_producer_remains_none():
    bm, slider = _slider()
    verdict, timeline = _run(bm, [])
    assert verdict.kind is JudgmentKind.MISS
    assert timeline.status == "unavailable" and not timeline.events
    assert timeline.tracking_sample_end_ms is None and timeline.sampling_step_ms is None
    assert {gap.reason for gap in timeline.unavailable} == {"no_replay_frames"}
    assert {gap.kind for gap in timeline.unavailable} == {
        "head_judged", "part_judged", "slider_judged", "tracking", "callback_order"}
    # Hand-made/legacy verdicts and no-judgements scene paths cannot imply
    # source-perfect input. --no-replay doesn't run this producer at all.
    legacy = ObjectVerdict("slider", JudgmentKind.HIT300, 1000, 2250,
                           (256, 192), (506, 192))
    assert legacy.slider_visuals is None
    circle_bm = _map("256,192,1000,1,0,0:0:0:0:\n")
    assert _run(circle_bm, _frames([(1000, 256, 192, KEY_K1)]))[1] is None


def test_seek_prefix_uses_notification_time_and_never_changes_immutable_history():
    event = SliderVisualEvent(1200, 0, "head_judged", "click_routing",
                              hit=True, transform_time_ms=1100)
    timeline = SliderVisualTimeline("partial", False, False, events=(event,))
    assert not timeline.events_at(1150)  # anchor alone doesn't notify a result
    assert timeline.events_at(1200) == (event,)
    assert not timeline.events_at(0)
    assert timeline.events_at(2000) == (event,)
    for obj, attr, value in ((event, "hit", False),
                             (timeline, "events", ())):
        try:
            setattr(obj, attr, value)
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError("visual observations must remain immutable")


def test_repeated_runs_and_out_of_order_queries_produce_identical_snapshots():
    bm, slider = _slider(spans=3, length=120)
    frames = _perfect_slider_frames(bm)
    ruleset = StdRuleset(bm, frames)
    timeline = ruleset.run().verdict_for(slider).slider_visuals
    times = [1600, 900, 2800, 1000, 1600]
    prefixes = [timeline.events_at(t) for t in times]
    again = ruleset.run().verdict_for(slider).slider_visuals
    assert again == timeline
    assert [again.events_at(t) for t in times] == prefixes
    assert prefixes[0] == prefixes[-1]
