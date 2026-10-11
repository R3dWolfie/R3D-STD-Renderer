# Slider visual-event authority

`ObjectVerdict.slider_visuals` is the single public immutable slider snapshot,
frozen **before** replay-header reconciliation. It now combines the original
raw R3D observations with a chronological, source-derived drawable model.
Neither stream is an observed osu!lazer runtime trace. Score, combo,
`PartOutcome`, tracking intervals, hitsounds and scene consumers retain their
existing behaviour. The scene does not consume the new events yet.

## Schema and provenance

The frozen models remain exported from `osu_std_renderer.ruleset`.

| Field | Meaning |
| --- | --- |
| Event `time_ms` | Actual observation/notification time **within its named producer**, in map milliseconds. Model notifications need not occur at nominal part times. |
| `sequence` | Contiguous per-slider order. Events sort by `(time_ms, sequence)`; raw diagnostics precede model events at ties, preserving each stream's relative order. Cross-stream tie order is not a drawable callback claim. |
| `provenance` | Raw: `click_routing`, `tracking_sample`, `part_sample`. Chronological surrogate: `source_model`. No value denotes native runtime observations. |
| `hit` | Producer-local head/part truth, independent of reconciliation. Tracking edges have `None`. |
| `part_index`, `part_kind` | Existing raw part-list identity; head is zero. The lenient raw tail can precede a repeat in this list. Model eligibility uses nominal tail end without changing this identity. |
| `channel`, `input_frame_index` | Routed head's logical channel (1 = M1/K1, 2 = M2/K2) and decoded press frame. Forced miss has no successful channel. Automatic model expiry has no press frame. |
| `result_time_ms` | Modelled `Result.TimeAbsolute`: notification clamped to object end plus its maximum judgement offset. |
| `transform_time_ms` | Separately justified animation anchor. Successful tail uses the parent's fallback end anchor. **`None` means unavailable**, not “substitute notification time”. |
| `follow_callback` | Published tracking edge was forwarded to FollowCircle (`True`) or ignored by the parent-Judged guard (`False`). `None` for other events. |
| `judgement` | Model parent result: classic aggregate `300`, `100`, `50`, `miss`, or lazer `ignore_hit`, `ignore_miss`. This is independent of the accuracy-scored head and does not replace score results. |
| Gap `kind`, `reason`, `part_index` | Unknown event/constraint with a stable reason code and optional raw part identity. No guessed notification or hit is attached. |
| Gap `provenance` | `raw_simulation` scopes retained sampler limitations; `source_model` scopes chronological-model limitations. `None` remains accepted for legacy/no-input records. |
| Timeline `status` | `partial` or `unavailable` (zero input); never exact native parity. |
| `lazer`, `classic` | Existing ruleset and Classic selection, unchanged. |
| `events`, `unavailable` | Frozen tuples of frozen records. |
| `tracking_sample_end_ms`, `sampling_step_ms` | Original sampler boundary and nominal 1ms lattice, supplemented by endpoints/parts. Not model-clock metadata or a source timing-error bound. |
| `clock_model` | `replay_boundaries_v1` when the chronological path ran; otherwise `None`. |
| `model_evaluations`, `model_input_frames`, `model_end_ms` | Number of model updates, decoded frames visited in this slider's lifetime slice, and last evaluation time. These expose bounds and truncation, not native cadence. |

`events_at(t)` returns an inclusive combined prefix. `raw_events_at(t)` and
`model_events_at(t)` return the corresponding stream. Queries never mutate
history and support arbitrary forward/backward/repeated seeks. Global sequence
numbers can have gaps inside a filtered stream. Header reconciliation cannot
change events, gaps, anchors, or ordering.

`slider_visuals=None` means no producer supplied a timeline (non-slider or
hand-made/legacy verdict). An empty prefix merely precedes events. With zero
frames the existing scoring misses remain, and the timeline is `unavailable`
with no events or model clock. The no-replay scene fallback is unchanged.

## Raw observations remain diagnostics

`_route_click()` records successful heads and lazer forced misses at the routed
press time, now also retaining channel/frame identity. These are facts of
R3D's click routing, which has existing notelock/mod/input approximations.

`_evaluate_slider()` still samples tracking on its original lattice and records
`part_sampled`, not drawable notifications. It closes stored tracking intervals
at nominal end without fabricating a release edge. Original part times and
outcomes remain untouched. Raw head miss notification is still absent when
window closure runs at infinity; the chronological model separately produces
finite automatic expiry.

## Explicit chronological clock and order

`SliderReplayInput` indexes decoded frames and logical channels once per
simulation. Each slider visits a bisected lifetime slice, not the entire replay.
It evaluates replay frames (including equal-time frames in file order), spawn,
start, nested nominal times, tail eligibility at end minus 36ms, the successful
head-window boundary, and nominal end. It also evaluates finite IEEE-754
right-limit points for strict expiry and two flush updates after end/expiry or
routed head notification. These right-limit updates are a **defined surrogate**;
they do not predict the spacing of native drawable frames.

Cursor positions at inserted boundaries use the existing replay interpolation.
Held channels use the frame at or before that time; an exact decoded frame uses
its own coordinates/actions, retaining equal-time transitions. No crossings
between these evaluations are inferred. All times are map milliseconds, using
the already converted beatmap/replay input contract; no wall-clock or encoder
frame schedule is introduced.

Each evaluation uses this order:

1. A routed input head judgement, if this is its decoded press frame. A successful
   head performs late catch-up, then updates the input manager's tracking.
2. Parent `DrawableSlider.Update` publishes the **previous manager state**. Its
   FollowCircle guard checks whether the parent has already been judged.
3. The input manager updates tracking, using previous actions, head channel,
   entry/keep radii and `!AllJudged || time <= end`.
4. Eligible tick container, repeat container, then tail container notifications.
5. Head-container automatic expiry, only strictly after the successful window.
   Pending nested containers unblock on the next evaluation in this case.
6. Parent post-children result check: tail judged **and** nominal end reached.

This models source subtree order on the documented clock. Native scheduling
of asynchronous input, bindables and drawable updates is unavailable; the
`native_drawable_clock_unobserved` gap always remains. Raw diagnostics at a
tie are outside this model callback order.

Tracking can begin before start and continue after end while results are
pending. Entry radius is the hitcircle radius; keep radius is 2.4 times it.
The head action restriction and strict `timeToAcceptAnyKeyAfter` test use the
previous update's pressed channels. Model `tracking_on/off` are **internal
manager** edges; they have no FollowCircle anchor. Model
`tracking_published_on/off` describe parent publication. Only publications with
`follow_callback=True` carry `max(time, start)` and may drive press/release.
After parent judgement, publication can still be recorded with
`follow_callback=False` and no transform anchor.

## Nested results and animation anchors

On a successful late head, source post-processing compares the **current**
cursor with each already-passed nested path position using expanded radius.
If all checks pass, all passed parts hit; otherwise all passed parts miss.
This emits nested notifications before updating tracking, distinct from the
ordinary update order. If the current cursor is outside the expanded current
ball area, catch-up does not run and ordinary eligibility still applies.

Ordinary ticks/repeats wait for the head and nonnegative time offset. The tail
can hit from end minus 36ms, even on a sub-36ms slider, but waits for prior
ticks/repeats. A non-tracking tail misses only at or after nominal end. The
source container queues advance once per part rather than rescanning every
part on every replay update.

Head's maximum positive result offset is 400ms. Tick/repeat/tail and parent use
empty hit windows, so delayed `result_time_ms` clamps to nominal end. Tick,
repeat and missed-tail FollowCircle anchors use that clamped result time;
successful tail completion uses parent `HitStateUpdateTime`, which falls back
to slider end while the parent is unjudged. Parent notification can occur later
than end; its result/transform anchor is end. A successful `repeat_freeze`
records the **actual model notification**, separately from the clamped repeat
fade anchor. Its `transform_time_ms` is intentionally absent: future arrow
positioning must freeze at `time_ms`, not at the fade anchor.

The successful model `head_judged` is also the data gate for future body
progress. A reconciled head hit, raw tick sample, or accuracy-scored lazer head
result is not a parent completion notification.

At replay EOF, an unfinished parent is left unresolved. No held-input
continuation or positive nested notification is invented beyond EOF. Only
mandatory AllJudged tracking cleanup after an already established parent may
run at flush points; its publication cannot forward a FollowCircle callback.

## Scoped gaps and remaining uncertainty

Retained raw gaps describe limitations of **raw sampling**, not absence of a
now-modelled notification: `head_window_notification_not_simulated`,
`parent_notification_not_simulated`, `parent_judged_guard_not_simulated`,
`post_end_tracking_not_simulated`, `pre_start_tracking_not_simulated`,
`tracking_action_restriction_not_simulated`, `between_sample_edges_not_observed`,
`drawable_callback_order_not_simulated`, `head_pending_at_sample`,
`nested_notification_not_simulated`, `tail_notification_and_anchor_not_simulated`,
and `part_sample_time_unavailable`. Every retained raw gap has
`provenance="raw_simulation"` when the model runs.

| Model reason | Meaning |
| --- | --- |
| `native_drawable_clock_unobserved` | Runtime cadence/input-to-drawable order cannot be recovered; surrogate publication latency is not native timing. Always present. |
| `interpolated_crossings_between_model_updates_unobserved` | Entry/keep crossings between evaluations are not established. Always present. |
| `head_routing_is_raw_r3d` | The source model conditions on existing R3D head routing, not a port of every lazer input/notelock/mod rule. Always present. |
| `source_geometry_not_verified` | Positions, stacking and nested generation use the existing R3D input contract; equality with converted lazer geometry is unverified. Always present. |
| `stable_client_semantics_not_verified` | Stable/Classic retain their scoring choice but use the inspected lazer follow model; stable-client parity is not established. |
| `pre_replay_input_unavailable` | Replay begins after model spawn; earlier input is unavailable. |
| `source_slider_duration_differs` | Existing stable and lazer durations differ; model uses the current input contract's duration/parts and cannot claim converted-lazer geometry parity. |
| `source_nested_parts_missing` | Existing slider supplies no tail from which to establish parent completion. |
| `invalid_slider_duration` | No valid positive finite duration to evaluate. |
| `replay_ended_before_notification` | Head or particular part remains unjudged at EOF; no callback time/hit is guessed. |
| `parent_conditions_unestablished` | Tail/end requirements did not establish parent judgement. |

Zero input retains the legacy unscoped `no_replay_frames` gaps. Geometry,
stacking and replay interpolation are the existing R3D input contract, not a
new osu!lazer curve/conversion port. Even when durations coincide, native
geometry/input equality has not been proven.

A future consumer must select `source_model` events and consider model-scoped
gaps. Use forwarded published tracking edges for press/release, `part_judged`
for tick/break/tail, `repeat_freeze.time_ms` for arrow freeze, model head hit for
body progression, and `slider_judged` for parent completion. Never silently
substitute reconciled truth, `part_sampled`, or nominal timestamps for absent
notifications/anchors. These data support a separate deterministic visual
implementation on this clock; exact native animation parity remains unproven.

## Source authority and validation

Inspected local `ppy/osu` master: `8a942790906a37930a0f55c5d27e498159835885`.

- `osu.Game.Rulesets.Osu/Objects/Drawables/SliderInputManager.cs`: current-cursor
  late catch-up, action restrictions and post-end tracking.
- `DrawableSliderHead.cs`, `DrawableHitCircle.cs`, `DrawableSlider.cs`,
  `DrawableSliderTick.cs`, `DrawableSliderRepeat.cs`, `DrawableSliderTail.cs`
  in that drawable directory: routing, strict expiry, child order, tail/parent
  conditions and repeat freeze.
- `osu.Game.Rulesets.Osu/Skinning/FollowCircle.cs`, `SnakingSliderBody.cs`:
  guarded tracking publications, anchors and head-hit body gate.
- `osu.Game/Rulesets/Objects/Drawables/DrawableHitObject.cs`,
  `osu.Game/Rulesets/Objects/HitObjectLifetimeEntry.cs`,
  `osu.Game/Rulesets/Judgements/JudgementResult.cs`,
  `osu.Game/Rulesets/Objects/HitObject.cs`: nested AllJudged, notification versus
  clamped absolute result time, maximum offset and post-children update.
- `osu.Game/Rulesets/Objects/SliderEventGenerator.cs`, Osu `Slider.cs`,
  `SliderTick.cs`, `SliderEndCircle.cs`, `HitCircle.cs`, `OsuHitWindows.cs`:
  nested generation and empty/head hit windows.
- Osu visual scenes `TestSceneSliderLateHitJudgement`,
  `TestSceneSliderEarlyHitJudgement`, `TestSceneSliderInput`,
  `TestSceneSliderFollowCircleInput`, `TestSceneSliderSnaking`: short sliders,
  channel restrictions, overlap catch-up, follow lifecycle and snaking.

Focused assertions live in `tests/test_slider_chronological.py`; original raw
contract assertions remain in `tests/test_slider_visual_events.py`. Run the
producer-only dense benchmark with
`python -m tests.benchmark_slider_chronological`. It measures one O(F) shared
index, lifetime slices and advancing nested queues. A slider with K relevant
frames and P parts costs O(log F + (K + P) log(K + P)) scheduling and O(K + P)
state evaluation; merged event sorting adds O(E log E). Existing raw scoring
sampling is outside this benchmark and remains unchanged.
