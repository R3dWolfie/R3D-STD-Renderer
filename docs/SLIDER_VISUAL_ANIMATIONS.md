# Slider visual animation consumers

Stage 3 consumes the immutable chronological timeline described in
[SLIDER_VISUAL_EVENTS.md](SLIDER_VISUAL_EVENTS.md). It changes rendering only.
The producer clock is a deterministic source-derived surrogate; it is not an
observed native osu!lazer callback trace.

## Authority and evaluation

`render/slider_visuals.py` compiles each slider once. Follow transforms consume
only forwarded `tracking_published_on/off` and nested `part_judged` callbacks
with an available transform anchor. Internal tracking edges, raw observations,
reconciled part outcomes and parent completion cannot substitute for these
callbacks. Snapshots sort by notification time and sequence. A late callback
with a backdated anchor affects queries only from its notification onwards.

Scale and opacity have independent immutable channels. New transforms cancel
following transforms on their property, preserving the predecessor's value.
Completed transforms retain their final value. A tick's queued return can be
cancelled without reviving opacity. Queries are pure and accept arbitrary
seek order. Compilation costs O(E log E), storage O(E), and queries O(log E)
with at most two scale segments and one opacity segment per snapshot. The
overall scene's existing spawn/retire loop still requires forward frame times.

| Callback | Default / Argon | Legacy |
| --- | --- | --- |
| Press | Invisible scale reset to 1; scale 2.4, alpha 1, 300 ms OutQuint | Reset scale 1 / alpha 0; scale 2 over min(180, remaining) ms OutQuad; alpha 1 over min(60, remaining) ms linear |
| Release | Scale 2.88 and alpha 0, 150 ms OutQuint | No transform |
| Successful tail | Scale 1 / 300 ms and alpha 0 / 150 ms, OutQuint | Scale 1.6 / 200 ms OutQuad; alpha 0 / 200 ms InQuad |
| Successful tick/repeat | At scale >= 2.352, scale 2.592 / 40 ms then 2.4 / 200 ms, OutQuint | At scale >= 2, immediate 2.2 then 2 / 200 ms linear |
| Missed part | No transform | Scale 4 and alpha 0 / 100 ms linear |

The renderer selects Argon without a skin, Legacy when a skin provides a
follow resource (including explicit transparent frames), and Default orange
when a custom skin lacks it. Modern follow diameter starts at twice the
hitcircle radius, independent of Argon's smaller ball. Argon retains additive
accent fill .3, border 4 in the 128 logical-pixel container, and a vertical
accent-to-accent/1.5 gradient. Default uses orange, fill .2 and border 5.

Legacy animation names are `sliderfollowcircle-0.png`, `-1.png`, etc., with
@2x resolution and a single `sliderfollowcircle.png` fallback. Undashed and
underscore frame names are not used by the inspected source call. All frames
retain their native logical dimensions, with per-axis centre cropping to
384 logical pixels and the source child scale .5. Cadence comes from the
resource provider's `AnimationFramerate`, or 1000/frame-count ms. Frame phase
starts at the slider's visual spawn. Fully transparent supplied assets do not
activate Default fallback.

## Body, endpoints and compositing

Both merged and individual bodies gate final-span retraction on a model head
hit whose notification has occurred. A missed or unknown head keeps the full
grown path. Source preempt/3 snake-in, span parity and settings flags remain.
Parent opacity fades over 240 ms at its published transform anchor. A known
successful parent also fades its body over 40 ms when the head has hit and
SnakingOut is enabled. These two opacity components multiply.

An unresolved/missing timeline never invents release, tail or parent events.
For conservative retirement only, absent parent data gets a nominal end +
240 ms fade. A supplied parent without an anchor is held until retirement;
it does not acquire a guessed anchor. Debug no-replay mode explicitly assumes
perfect play without writing fabricated model records.

## Ball child lifecycle

`BallAnimation` evaluates intrinsic ball opacity separately from the follow
channels. It uses `slider_judged.time_ms` to gate visibility of the result,
then evaluates the fade at `transform_time_ms`. A delayed result does not
change frames before its notification, even when the anchor precedes it.
Head success, parent hit/miss, snaking and merge settings do not gate this fade.

| Resolved ball | Child fade | Source |
| --- | --- | --- |
| No skin: Argon | 50 ms OutQuint | `ArgonSliderBall.cs:102` |
| Active skin without loaded `sliderb`: Default | 112.5 ms OutQuad | `DefaultSliderBall.cs:92` |
| Loaded `sliderb` or undashed `sliderb0` frames: Legacy | Instant | `LegacySliderBall.cs:141` |

The resource test is independent of `sliderfollowcircle`. Transparent loaded
ball assets still select Legacy and suppress the procedural fallback.
`sliderb-nd`/`sliderb-spec` alone do not select Legacy. Existing resource
priority, @2x sizing, frame phase, tint and detached opacity (.35) remain;
tracking and ball position freeze at nominal end during the brief child fade.
The parent alpha is multiplied **once**, in `draw_slider_layers`, rather than
being included in child opacity. Follow, tail, head and repeat lifecycles stay
separate and can remain visible after the Legacy ball has disappeared.

Missing parent data uses `approximate_nominal_retirement`, a visual-only fade
at nominal end with the selected child envelope. Debug no-replay uses
`approximate_debug_no_replay`. Neither creates events or changes gap records.
A supplied parent with an unavailable/non-finite anchor is labelled
`unsupported_parent_anchor` and receives no guessed child fade; existing
scene retirement remains conservative. Supporting that case requires the
producer to supply an independently justified `transform_time_ms`, already
present in the contract. It must not be inferred from reconciled outcomes.

This end-opacity correction retains existing entrance/scale/icon/rotation
approximations and does not add Legacy companion layers or change animation
cadence. It is source-conforming consumption of the chronological surrogate,
not proof of native callback or pixel parity.

## Endpoint components

Legacy final tails stay at their true final endpoint for every span parity.
Repeat circle pieces share their repeat's moving/frozen position beneath its
arrow. Argon has no visible tail/repeat circle. First circle/arrow entrance is
delayed by preempt/3 when SnakingIn is enabled. Later circles appear instantly
at their source two-span preempt; arrows retain a 150 ms fade, clamped to span
duration. Repeat hit/miss fades use the model part anchor, not reconciled flags.

Successful repeat pose freezes at its `repeat_freeze.time_ms`, using the
left-limit snaked geometry before that boundary's body update. Unhit/missed
repeats continue following the visible endpoint. Rotation uses the first
distinct inward polyline tangent. This is a deterministic approximation:
native elapsed-frame rotation smoothing and update-order pose are unavailable.

Separate beneath/follow/ball-and-head draw calls preserve additive follow
below the normal ball in GL and Metal. Their global batching algorithms are
unchanged. Existing scene object/fail transforms apply to every sprite pass.
Hidden fades legacy circle pieces with the head and hides judged repeats;
pending arrows and follow remain. Traceable hides tail/repeat circles and
preserves outline bodies. Freeze Frame retains its per-object preempt.

## Evidence and limits

Focused tests cover channel timing/cancellation, sequence ties, missing
notifications, late/short/head-miss cases, all span parities, assets and actual
GL pixels. Metal's checked-in draw method is executed with a command recorder;
the native pixel check runs only on macOS. Native Metal is untested on Linux.
The existing polyline implementation is retained: curved, overlapping, tiny
and degenerate path tests agree with its sampled endpoints and ball positions.
This does not establish converted-lazer geometry equality.

Remaining approximations include native callback cadence, rotation smoothing,
existing path sampling/conversion and score-clock differences, existing tick
appearance/active-span scheduling, procedural raster padding and colour space,
and a half native-pixel crop-centre rounding difference for odd oversized
legacy images. SliderMerge retains its existing shared colour/max-opacity
approximation across simultaneous sliders. Native pixel parity is unverified.

Generate bounded native-resolution PNGs and an importable synthetic map/replay:

```sh
python -m tests.capture_slider_stage3 --output /absolute/qa/folder --label after
```

The utility records 10 timestamps for Argon, a two-frame Legacy skin, and a
custom skin missing follow. Its `.osr` round-trips through the production
parser; both captures use identical decoded inputs. Run the same script with
`PYTHONPATH` pointing at an archive of the pre-change commit for `--label before`.
See the untracked Stage 3 report for exact commands, hashes and owner QA notes.

For ball-specific evidence, `tests/capture_slider_ball_lifecycle.py` adds a
distinct visible `legacy-ball` fixture while leaving the old follow-only
`legacy` fixture intact. It captures Argon, Default ball with Legacy follow,
Legacy ball, and four real replay objects on an absolute map-clock 60 fps grid,
including a delayed parent. Manifests record notification/anchor, ball
resources, sprite IDs and effective alpha. Pass `--repo` with the real input
checkout and separate `--label before`/`after`; run the script against an
archived baseline via `PYTHONPATH` for the before capture. Existing owner
inputs and Stage 3 artifacts are never overwritten.

## Source references

Inspected local `ppy/osu` commit `8a942790906a37930a0f55c5d27e498159835885`:

- `osu.Game.Rulesets.Osu/Skinning/FollowCircle.cs`,
  `Default/DefaultFollowCircle.cs`, `Argon/ArgonFollowCircle.cs`,
  `Legacy/LegacyFollowCircle.cs`: callback transforms and component visuals.
- `Legacy/OsuLegacySkinTransformer.cs`, `osu.Game/Skinning/LegacySkinExtensions.cs`:
  resource naming, cadence, native dimensions and max-size crop.
- `Argon/ArgonSliderBall.cs:102`, `Default/DefaultSliderBall.cs:92`,
  `Legacy/LegacySliderBall.cs:141`: independent ball child fade transforms.
  `osu.Game/Rulesets/Objects/Drawables/DrawableHitObject.cs:682`: nominal start
  and result/fallback-end hit-state anchors; `:483` rebuilds hit-state transforms.
- `Objects/Drawables/DrawableSliderBall.cs`, `DrawableSlider.cs`,
  `DrawableSliderTail.cs`, `DrawableSliderRepeat.cs`, `DrawableOsuHitObject.cs`:
  layers, body/parent fades, endpoints, freeze and ApplyRepeatFadeIn.
- `Skinning/SnakingSliderBody.cs`, `Objects/SliderEndCircle.cs`,
  `Mods/OsuModHidden.cs`, `Mods/OsuModTraceable.cs`,
  `osu.Game.Rulesets.Osu.Tests/TestSceneSliderSnaking.cs`: range, entrances,
  mod visibility and source repeat/snaking tests.
- `osu.Game/Rulesets/Objects/SliderPath.cs`: sampled curve distance cutting.

Framework transform behaviour and easing were inspected in primary upstream
[DefaultEasingFunction.cs](https://github.com/ppy/osu-framework/blob/master/osu.Framework/Graphics/Transforms/DefaultEasingFunction.cs),
[Transformable.cs](https://github.com/ppy/osu-framework/blob/master/osu.Framework/Graphics/Transforms/Transformable.cs)
and [TargetGroupingTransformTracker.cs](https://github.com/ppy/osu-framework/blob/master/osu.Framework/Graphics/Transforms/TargetGroupingTransformTracker.cs).
These upstream reads are not a pinned native runtime verification.
