# Legacy cursor providers and composition

The extracted skin cursor retains native PNG RGB and alpha with a white
default tint. `cursor_rainbow` is the existing explicit tint override;
the procedural red glow is not applied to legitimate skin cursor assets.
No brightness/saturation adjustment or colour-profile conversion is added.

`SkinElements` resolves `cursor` and `cursortrail` independently through
SKIN → FALLBACK → LOCAL. It locks `cursormiddle` to the provider that
supplied `cursor`, following the LegacyCursor provider hierarchy. A missing
middle from that provider stays missing even if another provider offers one.
An intentionally blank texture still counts as supplied and remains blank.
Other skin resources and their fallback chains are unchanged.

`StdScene.long_trail` is selected from that same-provider middle, with
the existing `force_long_trail` override retained:

| Selected cursor provider has a middle | Explicit override | Trail |
| --- | --- | --- |
| No | OFF | Disjoint, normal/inherited blending |
| Yes (including blank) | OFF | Connected, additive blending |
| Either | ON | Connected/additive, deliberate R3D override |

The source authority is osu!lazer's `LegacyCursorTrail.load()` and
`LegacyCursor.load()`, inspected at
`8a942790906a37930a0f55c5d27e498159835885`. Stock mode uses the cursor's
provider, not the trail provider or global middle lookup. Trail blend uses
`SRC_ALPHA, ONE_MINUS_SRC_ALPHA` when disjoint and `SRC_ALPHA, ONE` when
connected, in R3D's existing sprite backends.

GPU draw order is trail → body → middle. GL and Metal ordinarily batch
normal sprites before additive sprites inside each draw call. The scene's
`_draw_cursor_sprites()` therefore submits consecutive homogeneous blend
groups for skin cursors. This retains painter order through both backends
without changing their generic batching or other additive effects. The
body/middle remain in their original normal sprite order. Normal play,
merge/versus and frozen fail cursors share this boundary; Bloom/No Scope
wrapping occurs before it, and backend post transforms still apply once.
Argon and procedural cursors retain their original single batch.

Native texture aspect, `@2x` logical size, cursor/trail scale, CursorCentre
placement, rainbow, merge accents/fade and the existing motion/fade
calculations are retained. Disjoint drops still use the absolute 16.67 ms
grid and 150 ms life. Connected points still use the established R3D
distance resampling/window/cap and `.85 * strength` sprite opacity.
This fixes provider/blend/order conformance; it does not claim native
connected trail timing, density, near-cursor avoidance or animation parity.
Existing cursor rotation/expansion and trail-origin behavior are not
expanded by this patch; those may need separate native-frame investigation.

`tests/test_cursor_parity.py` checks source combinations, blank assets,
native dimensions/fade and seeks, normal/additive pixel composites on dark
and bright backgrounds, both real GL backend configurations, BM/NS wraps,
Metal command order/post transforms, full scenes, merge and fail paths.
The native Metal framebuffer comparison is explicitly skipped off macOS;
Linux command serialization is separate evidence, not Metal GPU proof.
Cursor queries are stateless; the full scene retains its monotonic-clock
contract. Backward full-scene seeks are tested by recreating the scene,
not by changing that contract.

Native-resolution before/after PNGs and source/texel traces are in
`/home/theaussie/std-test/legacy-cursor-parity-20261012/`. The owner replay
and original assets are preserved. The byte-RGB texture alpha test measures
R3D's current pipeline; it is not proof about lazer's colour management.
No matched native lazer reference was supplied. Final visual acceptance
requires the owner's matched-timestamp comparison against osu!lazer.
