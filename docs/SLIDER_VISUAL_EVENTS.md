# Slider visual-event authority

`ObjectVerdict.slider_visuals` is the single public location for an immutable
`SliderVisualTimeline`. It contains **raw producer observations before header
reconciliation**, not an exact osu!lazer drawable callback history. Existing
score, combo, `PartOutcome`, `tracking`, hitsound, and rendering consumers still
use their existing data; this contract is not consumed by the scene yet.

## Schema and provenance

The frozen models are exported from `osu_std_renderer.ruleset`:

| Model / field | Meaning |
| --- | --- |
| `SliderVisualEvent.time_ms` | Notification/observation time in **map milliseconds** in the producer named by `provenance`. |
| `sequence` | Deterministic per-slider order; events are sorted by `(time_ms, sequence)`. Ties preserve raw routing/sampling order, not an asserted lazer callback order. |
| `kind` | `head_judged`, `tracking_on`, `tracking_off`, `part_sampled`; the schema also reserves `part_judged` and `slider_judged`, which this producer never fabricates. |
| `provenance` | `click_routing`, `tracking_sample`, or `part_sample`. No value claims observed replay-engine callbacks. |
| `hit` | Copied raw truth for heads/part samples; `None` for tracking edges. Header reconciliation cannot change it. |
| `part_index`, `part_kind` | Raw part-list identity (head is index zero; remaining indices follow the evaluator's ordered judge points). A lenient tail sample can precede a final repeat, so tail is not necessarily last. |
| `transform_time_ms` | Separate animation anchor when established. **`None` means unavailable**, not “use `time_ms`”. Routed head events have the same click/forced-miss anchor as their notification; sampled tracking/parts have no asserted source anchor. |
| `SliderVisualGap` | A source event/constraint that is unavailable: `kind`, stable reason code, and optional raw `part_index`. No invented time or judgement is attached. |
| `SliderVisualTimeline.status` | `partial`: raw observations exist but source semantics are incomplete. `unavailable`: no input frames. Neither status permits claiming full animation parity. |
| `lazer`, `classic` | Existing ruleset selection and Classic-mod detection, copied without changing those choices. |
| `events`, `unavailable` | Frozen tuples of frozen records. |
| `tracking_sample_end_ms` | Last boundary of the existing nominal-end tracking sampler; it is not a parent judgement time. |
| `sampling_step_ms` | Nominal existing 1ms lattice, supplemented with slider endpoints and part times. This is **not** a bound on source timing error. |

`timeline.events_at(time_ms)` returns an inclusive event prefix and does not
mutate the timeline. Querying times backwards or repeating a query returns the
same prefix. Each simulation constructs its own snapshot; no render-frame
history is needed.

`slider_visuals=None` means no producer supplied a slider timeline (non-slider
or a hand-made/legacy verdict). An empty prefix is different: it may just precede
the first raw event. With zero frames, the ruleset retains its existing slider
miss results and publishes an explicitly `unavailable` timeline with no
events. The CLI's `--no-replay`/no-judgements scene fallback is unchanged and
does not acquire a fabricated perfect-play timeline.

## What the producer knows

`_route_click()` records successful head clicks and lazer's forced misses of
earlier heads at the actual routed press time. These are authoritative facts
**of R3D's raw simulation**, not proof that the replay engine judged identically.
The simulator still has its existing notelock/mod/input approximations.

`_evaluate_slider()` records tracking state changes at the existing lattice
samples, before evaluating parts at that sample. Part evaluations are recorded
as `part_sampled`, including raw hit/miss and the actual sample time. They are
not renamed to judgement notifications. The final tuple is frozen before
`reconcile_to_counts()`, `reconcile_slider_parts()`, `_reconcile_max_combo()`,
and `_verdict()` can relabel results or invent scoring head hit times.

The existing sampler only visits nominal slider lifetime. Closing a stored
tracking interval at slider end is not an observed release: no `tracking_off`
is emitted merely to close that interval. A routed head click after a short
slider's end is still recorded; no tracking or nested callbacks are invented
to fill the intervening or post-end time.

## Unavailable semantics and reason codes

| Scope / reason | Missing source semantics |
| --- | --- |
| All scopes: `no_replay_frames` | No replay input exists from which to produce visual observations. |
| Head: `head_window_notification_not_simulated` | `_close_windows()` runs before presses and once at infinity. The scoring deadline marks expiry, not a reliably observed finite drawable notification. No event at infinity or guessed deadline notification is emitted. |
| Parent: `parent_notification_not_simulated` | `DrawableSlider.CheckForResult()` waits for the tail judgement and nominal end. The scored lazer head event is not this parent boundary. |
| Tracking: `parent_judged_guard_not_simulated` | Follow-circle tracking callbacks ignore changes after the parent is `Judged`; that boundary is unavailable. |
| Tracking: `post_end_tracking_not_simulated` | Source input tracking can continue while judgements remain pending beyond nominal end. Existing samples end at nominal end. |
| Tracking: `pre_start_tracking_not_simulated` | Source drawable tracking/press anchors can occur before nominal start; existing samples begin at start. |
| Tracking: `tracking_action_restriction_not_simulated` | Source `timeToAcceptAnyKeyAfter`/initial head action handling differs from the existing any-held-key model, including Classic cases. |
| Tracking: `between_sample_edges_not_observed` | Sub-millisecond key pulses and multiple state crossings between lattice samples can be missed. Actual engine frame scheduling is not recorded, so no universal timing-error bound is asserted. |
| Ordering: `drawable_callback_order_not_simulated` | Late-head catch-up can notify nested judgements before updating tracking, unlike ordinary sampled tracking-before-parts order. Raw tie ordering is deterministic but cannot stand in for these callbacks. |
| Part: `head_pending_at_sample` | A raw sample occurs before the known head click/forced miss or while the head window is still pending. Source nested judgements wait for the head. Catch-up outcomes and timing are not inferred. |
| Part: `nested_notification_not_simulated` | The ordinary tick/repeat sample is not a source notification, even when its nominal time has passed. |
| Part: `tail_notification_and_anchor_not_simulated` | Source tail eligibility, waiting for earlier tick/repeat judgements, and early-hit-versus-at-end-miss behaviour are absent. Tail-hit transforms use the parent anchor, not a uniform 36ms correction. |
| Part: `part_sample_time_unavailable` | If a numeric straggler is resolved by the existing fallback without a timed sample, no timed observation is fabricated. |

Every nested raw part has a corresponding `part_judged` gap. The head-pending
reason is a known constraint, not a claim that the source callback eventually
ran at the click, deadline, or end. Other gaps remain relevant even on a perfect
raw run. Full follow-circle lifecycle, repeat-arrow freeze, and parent-end fades
must wait for a producer implementing the missing semantics; consumers must
not silently fall back from a gap or absent anchor to nominal part times or
reconciled truth.

## Source inspected for these constraints

Local `ppy/osu` master at `8a942790906a37930a0f55c5d27e498159835885`:

- `FollowCircle.cs`: parent `Judged` guard, tracking anchors, tick/repeat/tail
  transforms, and separate parent-versus-nested anchors.
- `SliderInputManager.cs`: action restrictions, `PostProcessHeadJudgement()`,
  `TryJudgeNestedObject()`, and tracking while `!AllJudged` beyond end.
- `DrawableSlider.cs`: `CheckForResult()` parent boundary and the
  `HeadCircle.IsHit` body-progress gate.
- `DrawableHitObject.cs`: `ApplyResult()` assigns `Result.RawTime` at notification;
  `HitStateUpdateTime` uses `Result.TimeAbsolute`.
- `JudgementResult.cs`: `TimeAbsolute` clamps `RawTime` to the object's end plus
  `MaximumJudgementOffset`. Future producers must model this distinction, not
  equate every notification and transform anchor.
- `DrawableSliderRepeat.cs`: hit-state freezes the arrow before further snaking
  position updates. A raw repeat sample is insufficient to schedule that freeze.
- `SnakingSliderBody.cs`: body progress and final-span snaking lifecycle.

The focused next simulation pass must supply chronological head expiry,
initial-action tracking and post-end pending input, late-head catch-up, delayed
nested/tail notifications, parent judgement, drawable tie order, and clamped
transform anchors. It must keep raw visual authority independent of header
reconciliation. Animation changes remain a subsequent pass.
