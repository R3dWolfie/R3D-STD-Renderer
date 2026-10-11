"""Source-derived follow transforms, model authority and legacy assets."""
from __future__ import annotations

import dataclasses
import math
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from osu_std_renderer.render.slider_visuals import (
    FollowAnimation, SliderVisualState, repeat_alpha, repeat_fade_in,
)
from osu_std_renderer.render.scene import snake_range
from osu_std_renderer.render.skin_elements import SkinElements
from osu_std_renderer.ruleset.slider_visual import SliderVisualEvent, SliderVisualTimeline
from osu_std_renderer.skin.skin import Skin
from tests.test_skin_elements import FakeRenderer


def event(time, kind, sequence=0, anchor=None, **kwargs):
    return SliderVisualEvent(time, sequence, kind, "source_model",
                             transform_time_ms=time if anchor is None else anchor, **kwargs)


def timeline(*events):
    return SliderVisualTimeline("partial", True, False, tuple(events),
                                clock_model="replay_boundaries_v1")


def press(time=1000, sequence=0, anchor=None):
    return event(time, "tracking_published_on", sequence, anchor, follow_callback=True)


def release(time, sequence=0):
    return event(time, "tracking_published_off", sequence, follow_callback=True)


def part(time, kind="tick", hit=True, sequence=0, anchor=None, index=1):
    return event(time, "part_judged", sequence, anchor, part_kind=kind,
                 part_index=index, hit=hit, result_time_ms=time)


def close(actual, expected):
    assert abs(actual - expected) < 1e-9, (actual, expected)


def test_modern_press_scale_alpha_out_quint_and_variant_identity():
    for variant in ("default", "argon"):
        animation = FollowAnimation(timeline(press()), 1000, 2000, variant)
        assert animation.at(999).alpha == 0
        close(animation.at(1150).alpha, 31 / 32)
        close(animation.at(1150).scale, 1 + 1.4 * 31 / 32)
        assert animation.at(1300).scale == 2.4
        assert animation.at(1300).alpha == 1


def test_early_press_uses_clamped_anchor_and_not_notification_time():
    animation = FollowAnimation(timeline(press(900, anchor=1000)), 1000, 2000, "default")
    assert animation.at(950).alpha == animation.at(1000).alpha == 0
    close(animation.at(1150).alpha, 31 / 32)


def test_release_continues_visible_then_reacquires_from_current_channels():
    animation = FollowAnimation(timeline(press(), release(1400), press(1450)),
                                 1000, 2200, "default")
    released = 1 - (1 - (1 - 50 / 150) ** 5)
    close(animation.at(1450).alpha, released)
    assert 2.4 < animation.at(1450).scale < 2.88
    assert animation.at(1500).alpha > released
    assert animation.at(1750).scale == 2.4 and animation.at(1750).alpha == 1
    finished = FollowAnimation(timeline(press(), release(1400)), 1000, 2000, "argon")
    close(finished.at(1475).alpha, 1 / 32)
    assert finished.at(1550).alpha == 0 and finished.at(1550).scale == 2.88


def test_reacquisition_from_invisible_resets_modern_scale():
    animation = FollowAnimation(timeline(press(), release(1400), press(1700)),
                                 1000, 2400, "default")
    assert animation.at(1699).alpha == 0
    assert animation.at(1700).scale == 1 and animation.at(1700).alpha == 0


def test_modern_tick_pulse_and_release_cancels_queued_return_only_on_scale():
    pulse = FollowAnimation(timeline(press(), part(1400)), 1000, 2200, "argon")
    close(pulse.at(1440).scale, 2.4 * 1.08)
    close(pulse.at(1540).scale, 2.4 + (2.4 * .08) / 32)
    assert pulse.at(1640).scale == 2.4 and pulse.at(1640).alpha == 1
    cut = FollowAnimation(timeline(press(), part(1400), release(1420)), 1000, 2200, "default")
    assert cut.at(1570).scale == 2.88 and cut.at(1570).alpha == 0
    assert cut.at(1800).scale == 2.88  # no stale queued pulse return


def test_tick_before_growth_threshold_and_modern_break_are_no_ops():
    base = FollowAnimation(timeline(press()), 1000, 2200, "default")
    changed = FollowAnimation(timeline(press(), part(1050), part(1100, hit=False)),
                               1000, 2200, "default")
    for time in (1060, 1150, 1400):
        assert base.at(time) == changed.at(time)


def test_tail_hit_runs_end_at_parent_anchor_and_miss_does_not_end_modern():
    ended = FollowAnimation(timeline(press(), part(1964, "tail", anchor=2000)),
                             1000, 2000, "default")
    assert ended.at(1980).alpha == 1
    close(ended.at(2075).alpha, 1 / 32)
    assert ended.at(2150).alpha == 0
    assert ended.at(2300).scale == 1
    missed = FollowAnimation(timeline(press(), part(2000, "tail", hit=False)),
                              1000, 2000, "default")
    assert missed.at(2150).alpha == 1


def test_legacy_native_scale_linear_alpha_and_remaining_time():
    animation = FollowAnimation(timeline(press()), 1000, 2000, "legacy")
    close(animation.at(1090).scale, 1.75)  # Easing.Out = OutQuad
    close(animation.at(1030).alpha, .5)
    assert animation.at(1180).scale == 2 and animation.at(1060).alpha == 1
    short = FollowAnimation(timeline(press()), 1000, 1050, "legacy")
    close(short.at(1025).scale, 1.75)
    close(short.at(1025).alpha, .5)
    assert short.at(1050).scale == 2 and short.at(1050).alpha == 1
    late = FollowAnimation(timeline(press(1120)), 1000, 1050, "legacy")
    assert late.at(1120).scale == 2 and late.at(1120).alpha == 1


def test_legacy_release_noop_tick_immediate_pulse_and_linear_return():
    animation = FollowAnimation(timeline(press(), release(1300), part(1400)),
                                 1000, 2200, "legacy")
    assert animation.at(1350).alpha == 1 and animation.at(1350).scale == 2
    close(animation.at(1400).scale, 2.2)
    close(animation.at(1500).scale, 2.1)
    assert animation.at(1600).scale == 2


def test_legacy_end_out_quad_scale_in_quad_alpha_and_break_linear():
    animation = FollowAnimation(timeline(press(), part(1964, "tail", anchor=2000)),
                                 1000, 2000, "legacy")
    close(animation.at(2100).scale, 1.7)
    close(animation.at(2100).alpha, .75)
    assert animation.at(2200).alpha == 0
    broken = FollowAnimation(timeline(press(), part(1400, hit=False)), 1000, 2000, "legacy")
    close(broken.at(1450).scale, 3)
    close(broken.at(1450).alpha, .5)
    assert broken.at(1500).scale == 4 and broken.at(1500).alpha == 0


def test_equal_notification_time_respects_sequence_not_input_tuple_order():
    pressed_last = FollowAnimation(timeline(press(sequence=2), part(1000, hit=False, sequence=1)),
                                   1000, 2000, "legacy")
    broken_last = FollowAnimation(timeline(press(sequence=1), part(1000, hit=False, sequence=2)),
                                  1000, 2000, "legacy")
    assert pressed_last.at(1100).alpha == 1
    assert broken_last.at(1100).alpha == 0


def test_late_notification_backdated_anchor_never_changes_prior_queries():
    animation = FollowAnimation(timeline(press(), part(2300, "tail", anchor=2000)),
                                 1000, 2000, "legacy")
    assert animation.at(2299).alpha == 1
    assert animation.at(2300).alpha == 0
    assert animation.at(1500).alpha == 1
    assert animation.at(2299).alpha == 1


def test_only_forwarded_publications_and_nested_callbacks_drive_follow():
    raw = dataclasses.replace(press(), provenance="tracking_sample")
    manager = event(1000, "tracking_on")
    ignored = dataclasses.replace(press(), follow_callback=False)
    parent = event(2000, "slider_judged", hit=True)
    unavailable = dataclasses.replace(press(), transform_time_ms=None)
    for variant in ("default", "argon", "legacy"):
        animation = FollowAnimation(timeline(raw, manager, ignored, parent, unavailable),
                                     1000, 2000, variant)
        assert all(animation.at(t).alpha == 0 for t in (1100, 1700, 2100))


def test_arbitrary_non_encoder_timestamps_and_backward_queries_are_stable():
    animation = FollowAnimation(timeline(press(1000.25), release(1400.75), press(1470.125)),
                                 1000, 2200, "default")
    times = (1600, 1100, 1399, 2000, 1600, 1401, 1100)
    first = [animation.at(t) for t in times]
    assert first[0] == first[4] and first[1] == first[-1]
    assert first == [animation.at(t) for t in times]
    assert all(len(row[2].segments) <= 2 and len(row[3].segments) <= 1
               for row in animation.snapshots)


def test_parent_body_components_at_anchor_and_time_aware_head_gate():
    head = event(1100, "head_judged", hit=True)
    parent = event(2050, "slider_judged", anchor=2000, hit=True)
    state = SliderVisualState(timeline(head, parent), 1000, 2000, "default")
    assert not state.head_hit_at(1099) and state.head_hit_at(1100)
    assert state.parent_alpha(2049) == 1
    close(state.parent_alpha(2120), .5)
    assert state.body_alpha(2050, 600, 400, True) == 0  # 40ms already elapsed
    close(state.body_alpha(2120, 600, 400, False), .5)
    early = SliderVisualState(timeline(event(1000, "head_judged", hit=True),
                                       event(2000, "slider_judged", hit=True)),
                               1000, 2000, "argon")
    close(early.body_alpha(2020, 600, 400, True), (1 - 20 / 240) * .5)
    missed = SliderVisualState(timeline(event(1129.5, "head_judged", hit=False),
                                        event(2000, "slider_judged", hit=True)),
                                1000, 2000, "default")
    close(missed.body_alpha(2120, 600, 400, True), .5)
    unknown_at_parent = SliderVisualState(timeline(event(2050, "head_judged", hit=True),
                                                  event(2000, "slider_judged", hit=True)),
                                         1000, 2000, "default")
    # Never retroactively add the parent's conditional transform using a
    # head notification that was unavailable when it completed.
    close(unknown_at_parent.body_alpha(2120, 600, 400, True), .5)


def test_unknown_notifications_keep_conservative_body_and_no_follow_end():
    state = SliderVisualState(timeline(press()), 1000, 2000, "legacy")
    assert not state.head_hit_at(1500)
    assert state.follow.at(2100).alpha == 1  # no invented release or tail end
    close(state.parent_alpha(2120), .5)  # explicitly nominal retirement fallback
    assert state.parent_alpha(2240) == 0
    empty = SliderVisualState(None, 1000, 2000, "default")
    assert empty.follow.at(1500).alpha == 0 and not empty.head_hit_at(1500)
    auto = SliderVisualState(None, 1000, 2000, "default", autoplay=True)
    assert auto.follow.at(1500).alpha == 1 and auto.head_hit_at(1500)
    assert not auto.events  # debug fallback does not manufacture model records


def test_snake_range_head_miss_hit_late_and_all_span_parities():
    for spans in (1, 2, 3, 4):
        end = 1000 + spans * 500
        assert snake_range(end - 250, 1000, 500, spans, 600, head_hit=False) == (0, 1)
        expected = (.5, 1) if spans % 2 else (0, .5)
        assert snake_range(end - 250, 1000, 500, spans, 600, head_hit=True) == expected
        assert snake_range(end - 250, 1000, 500, spans, 600, snaking_out=False) == (0, 1)
    assert snake_range(500, 1000, 500, 1, 600, head_hit=False) == (0, .5)
    assert snake_range(500, 1000, 500, 1, 600, snaking_in=False, head_hit=False) == (0, 1)


def test_repeat_zero_entrance_delay_later_preempt_and_separate_fade_times():
    assert repeat_fade_in(599, 1000, 600, 500, 0, 150, True) == 0
    close(repeat_fade_in(675, 1000, 600, 500, 0, 150, True), .5)
    close(repeat_fade_in(800, 1000, 600, 500, 0, 400, True), .5)
    close(repeat_fade_in(475, 1000, 600, 500, 0, 150, False), .5)
    assert repeat_fade_in(999, 1000, 600, 500, 1, 150, True) == 0
    close(repeat_fade_in(1075, 1000, 600, 500, 1, 150, True), .5)
    close(repeat_fade_in(1010, 1000, 600, 20, 1, 150, True), .5)
    assert repeat_fade_in(1000, 1000, 600, 500, 1, 0, True) == 1
    assert repeat_fade_in(999, 1000, 600, 500, 1, 0, True) == 0


def test_repeat_result_fade_uses_clamped_anchor_after_actual_notification():
    hit = part(1550, "repeat", anchor=1500)
    miss = part(1550, "repeat", False, anchor=1500)
    close(repeat_alpha(1650, 1000, 600, 500, 0, True, hit), .25)
    close(repeat_alpha(1650, 1000, 600, 500, 0, True, miss), .5)
    close(repeat_alpha(1550, 1000, 600, 20, 0, True, hit), 0)


def test_legacy_follow_dash_names_native_2x_cropping_blank_and_frame_cycling():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "skin.ini").write_text("[General]\nAnimationFramerate: 10\n")
        Image.new("RGBA", (512, 256), (255, 0, 0, 0)).save(root / "sliderfollowcircle-0@2x.png")
        Image.new("RGBA", (500, 200), (0, 255, 0, 255)).save(root / "sliderfollowcircle-1.png")
        Image.new("RGBA", (12, 12), (255, 255, 255, 255)).save(root / "sliderfollowcircle0.png")
        uploaded = FakeRenderer()
        elements = SkinElements(Skin(skin_dir=root), uploaded)
        assert elements.has("sliderfollowcircle") and "sliderfollowcircle" in elements.empty
        assert elements.frame_counts["sliderfollowcircle"] == 2
        assert elements.frame_size("sliderfollowcircle", 0) == (256, 128)
        assert elements.frame_size("sliderfollowcircle", 100) == (384, 200)
        assert elements.frame_key("sliderfollowcircle", 0) == "sk_sliderfollowcircle"
        assert elements.frame_key("sliderfollowcircle", 100) == "sk_sliderfollowcircle_f1"
        assert elements.frame_key("sliderfollowcircle", 200) == "sk_sliderfollowcircle"
        assert uploaded.uploaded["sk_sliderfollowcircle_f1"].shape == (200, 384, 4)
        assert uploaded.uploaded["sk_sliderfollowcircle"].shape == (256, 512, 4)


def test_undashed_follow_frames_are_not_lazer_animation_names_and_blank_single_is_asset():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        Image.new("RGBA", (256, 256), (255, 255, 255, 255)).save(root / "sliderfollowcircle0.png")
        assert not SkinElements(Skin(skin_dir=root), FakeRenderer()).has("sliderfollowcircle")
        Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(root / "sliderfollowcircle.png")
        elements = SkinElements(Skin(skin_dir=root), FakeRenderer())
        assert elements.has("sliderfollowcircle") and "sliderfollowcircle" in elements.empty


def test_follow_2x_max_crop_and_fallback_provider_frame_rate():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        current, fallback = root / "current", root / "fallback"
        current.mkdir()
        fallback.mkdir()
        (current / "skin.ini").write_text("[General]\nAnimationFramerate: 10\n")
        (fallback / "skin.ini").write_text("[General]\nAnimationFramerate: 20\n")
        Image.new("RGBA", (1024, 1024), (255, 0, 0, 255)).save(fallback / "sliderfollowcircle-0@2x.png")
        Image.new("RGBA", (128, 128), (0, 255, 0, 255)).save(fallback / "sliderfollowcircle-1.png")
        uploaded = FakeRenderer()
        elements = SkinElements(Skin(skin_dir=current, fallback_dir=fallback), uploaded)
        assert elements.frame_size("sliderfollowcircle", 0) == (384, 384)
        assert uploaded.uploaded["sk_sliderfollowcircle"].shape == (768, 768, 4)
        assert elements.frame_key("sliderfollowcircle", 50) == "sk_sliderfollowcircle_f1"
        assert elements.frame_key("sliderfollowcircle", 100) == "sk_sliderfollowcircle"


def test_same_time_tick_tail_break_press_collisions_and_independent_alpha():
    events = (press(), part(1400, "repeat", sequence=1),
              part(1400, "tail", sequence=2), part(1400, hit=False, sequence=3),
              press(1400, sequence=4))
    animation = FollowAnimation(timeline(*events), 1000, 2000, "legacy")
    # The last press resets both channels and cancels end/break fades.
    assert animation.at(1400).scale == 1 and animation.at(1400).alpha == 0
    assert animation.at(1580).scale == 2 and animation.at(1580).alpha == 1
    modern = FollowAnimation(timeline(press(), release(1400), part(1410)),
                              1000, 2000, "argon")
    # A tick replaces scale, not release alpha; opacity cannot revive.
    assert modern.at(1550).alpha == 0
