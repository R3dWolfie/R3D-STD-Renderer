# Modern horizontal hit-error meter

Both Legacy and Argon use the current R3D Mania horizontal HEM. The source
is `osu_mania_renderer_v2/gpu/hit_error_meter.py` and
`gpu/hit_error_painter.py`, from Mania commit
`78ff3b8aaa01798a6f72cfdeeea658434dc2512a`. No old vertical Argon painter is selected.

The port keeps Mania's 1280×720 reference surface, height scaling, 244×22
natural size and eight reference pixels of bottom padding. The bar itself is
200×2 at local `(22,6)`; its axis centre is component X+122 reference pixels.
The component is horizontally centred using its complete scaled width,
including both icon slots and gaps: `component_x = (render_width - 244*scale)/2`.
The zero marker therefore sits at exactly half the viewport width, with origin
`(518,690)` at 720p and `(777,1035)` at 1080p. This owner-requested correction
replaces Mania's inherited origin, which put the zero marker 22 reference
pixels to the right. Local geometry, vertical placement and UR remain unchanged.
`hud_scale` does not retune this geometry. The shapes inherit HUD opacity.

Three Standard `OsuHitWindows` bands project Great/300 to blue `#66ccff`,
Ok/100 to green `#88b300`, and Meh/50 to yellow `#ffcc22`. Meh sets the signed
visible range; early offsets go left, late right. Yellow alone has a continuous
six-pixel tip fade. Source colours/constants, rounded four-pixel marker
strokes, eight-pixel blue centre/four-pixel darker centre, outlined two-pixel
chevron, icon contain-fit and painter order are preserved.

## Timing evidence

`hit_error_timeline.py` only reads the existing authoritative simulation and
replay press edges. It does not run a second simulation or alter results.

- Circles require finite, coherent verdict time/delta, a real press within
  the existing hit radius and the pre-reconciliation quality equal to the
  absolute timing offset. The simulator freezes raw circle misses as infinite
  quality, so repaired/header-derived perfect hits are rejected even if another
  press shares the synthetic timestamp.
- Slider heads use immutable `click_routing`/`head_judged` observations with
  `hit=True`, corroborated against the real press and channel. Offset is raw
  click time minus slider start. Colour uses the timing window, independently
  of repaired parts or downgraded aggregate judgements.
- Missing, invalid, unavailable or non-click observations are omitted. Ticks,
  repeats, tails, spinners and misses never invent timing markers. One press
  is consumed at most once; simultaneous channels keep replay press order.
- Older slider verdicts without the raw head timeline, or older circle
  verdicts without raw quality, cannot establish this evidence and are omitted.
  Erased raw circle observations cannot be recovered from reconciled misses;
  this presentation port does not reconstruct routing or use header totals.

The immutable timeline stores prefix EMA and interrupted transitions once.
Every frame bisects absolute beatmap milliseconds, selects the newest 50 live
samples, and evaluates the current transition without mutating history.
Marker entrance is 100 ms OutQuint, followed by 5000 ms linear alpha fade and
InQuint collapse; lifetime is 5100 ms. EMA is `old*.9 + offset*.1` from zero.
At each hit an interrupted 800 ms OutQuint chevron transition starts at the
position reached at that exact hit time. The EMA remains after markers expire.

## Backend and allocation behavior

`hit_error_painter.py` prepares one shared command sequence for GL and Metal.
GL uses Mania's unchanged analytic GLSL. Metal uses the equivalent segment
SDF/continuous fade and an 80-byte, five-float4 uniform layout, including
Standard's bottom-up render-target convention. Both preserve normal bands,
outer centre, additive ticks, normal inner centre/icons/chevron order.

The programs/pipelines, GL quad and the two original icon textures are cached
once per renderer. Frames only update uniforms or queue records; no masks or
textures are generated per frame. The two 100×100 original PNGs and their
source hashes are in `assets/hud/hit-error-meter/provenance.json`. Runtime
contain-fit uses 16×14 slots, preserving the original aspect ratio.

HUD submission splits the former meter slot while keeping every unrelated
normal sprite in its original order, followed by every unrelated additive
sprite in its original order. Foreground Legacy health art remains later in
the normal pass; BreakOverlay/fail/results ordering remains intact.

Native GL pixels are validated on AMD. Linux exercises the actual Metal
command/pipeline adapter against a recording core, including blend modes,
shader source, uniforms, queue order and icon dimensions. Two native Metal
framebuffer tests run on macOS and skip on Linux. This is not a claim of
native Metal pixel parity until those tests execute on a Mac:

```sh
python -m pytest -q tests/test_modern_hit_error_meter.py -k native_metal
```

## Visibility, UR and rollback

Existing `show_hit_error_meter` / `--hit-error-meter` and
`--no-hit-error-meter` select visibility. No dashboard control or setting was
added. The base bands/icons appear before the first click, like Mania; ticks
and chevron require actual observations.

UR retains `HudData.ur_at`, its existing input inclusion, prefix sums,
calculation, font, size, text, opacity, pre-game gate and independent
`show_unstable_rate` / CLI flag. Disabling HEM no longer disables UR.

The exact Mania placement overlapped the previous UR text. On 17 October 2026,
the owner approved moving **only UR above the enabled meter**. Its nominal
bottom is six reference pixels above the capsule/AA envelope. With HEM off,
the original Legacy/Argon UR anchors remain. No score, accuracy, grade, key,
mod, progress, HP, slider, spinner or cursor position changed.

The original horizontal/vertical implementations remain byte-equivalent in
`StdHud._previous_hit_error()` and `_argon_hit_error()` for comparison/rollback.
They are not called by the default path. To compare in a QA-only process,
replace the HUD's `_hit_error` callback with `_previous_hit_error` and suppress
the new independent UR callback to avoid duplicate UR. Do not wire the old
method into the modern painter's frame slot or draw both meters.
