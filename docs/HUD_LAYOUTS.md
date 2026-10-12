# Standard HUD layout and asset providers

The offline renderer selects one immutable 768-high HUD layout in
`render/hud_layout.py`, once when `StdHud` is constructed:

| Input | Layout |
| --- | --- |
| No active skin, no `legacy_defaults` | Argon |
| Explicit extracted `--skin`, including an empty/partial folder | Legacy |
| `legacy_defaults`, including skinless | Legacy |
| `--default-skin` alone | Argon; it is a fallback, not an active skin |

The loader retains its existing fail-soft behavior for nonexistent skin
paths: an explicit path selects Legacy and missing resources use fallback
assets. This does not add support for modern lazer skin serialization.

This supersedes the July 2026 hybrid **geometry** policy. Asset completeness
no longer selects different component anchors. Legacy score, accuracy,
combo, HP, keys and progress use their Legacy components together. Missing
textures use the existing classic procedural `lg_*` family; resolved skin
or fallback textures keep their provider, prefix, logical `@2x` size and
animation selection. A loaded transparent image remains transparent.
The skin loader, replay data and gameplay rendering are unchanged.

`renderer_default_font_and_ranks` overrides appearance only. Under Legacy,
procedural numeric glyphs occupy the same measured skin-font slots; score
and accuracy keep their right anchors, scale and layout bounds. Rank text
is procedural. It takes precedence over skin numeric/rank appearance,
including with `legacy_defaults`; it does not change layout. The existing
`legacy_defaults` synth sound-family behavior is unchanged.

## Native geometry crosswalk

Authority: local osu!lazer commit `8a942790906a37930a0f55c5d27e498159835885`,
checked through live `osu.lazer` MCP and local source. All native source
classes below are MIT licensed. No new upstream graphical assets were
imported.

| Element | Source and constraint |
| --- | --- |
| Argon score | `osu.Game/Skinning/ArgonSkin.cs:169–200`, `Screens/Play/HUD/ArgonScoreCounter.cs`: top-left container, right origin at x=250, y=50, minimum six wireframe cells |
| Argon accuracy | `ArgonSkin.cs:195–207`, `Screens/Play/HUD/ArgonAccuracyCounter.cs`: TopRight at (-20,20), half-size fraction, full-size percent |
| Argon keys | `ArgonSkin.cs:228–242`, `Screens/Play/ArgonKeyCounterDisplay.cs`: BottomRight, inset 60, bottom offset 66, horizontal cells |
| Argon progress | `Screens/Play/HUD/ArgonSongProgress.cs` / `ArgonSongProgressBar.cs`, `ArgonSkin.cs:228–242`: BottomCentre, 90% width, bottom padding 10 |
| Legacy score | `osu.Game/Skinning/LegacyScoreCounter.cs`: TopRight, scale .96, horizontal margin 10 |
| Legacy accuracy | `LegacyAccuracyCounter.cs`, `LegacySkin.cs:428–443`: TopRight, scale .576, horizontal margin 17, top = actual score bottom + 9 |
| Legacy progress | `LegacySongProgress.cs`, `LegacySkin.cs:436–443`: 33-unit pie, right edge 18 units left of accuracy, centre aligned with accuracy |
| Legacy keys | `osu.Game.Rulesets.Osu/Skinning/Legacy/OsuLegacySkinTransformer.cs:62–72`, `LegacyKeyCounterDisplay.cs`, `LegacyKeyCounter.cs`: CentreRight anchor, TopRight origin, top = 384-64*HUD scale; background begins at container top, key flow begins 7 units lower; 46-unit cells and 1.8 spacing |
| Legacy HP | `LegacySkin.cs:462–465`, `LegacyHealthDisplay.cs`: native texture dimensions and fill/marker placement; frontmost normal HUD art, including deliberate oversized full-screen scorebars |
| Font metrics | `LegacySpriteText.cs`: fixed digit cell from '5', prefix and negative overlap spacing; use actual glyph rectangles to derive score/accuracy bounds |
| Secondary flow | `Screens/Play/HUDOverlay.cs:259–324`: source derives flow offsets from occupied native bounds, excluding oversized Legacy health artwork |

Existing health damping, breaks, combo animations, key edge counts and
hit-error/UR behavior remain in their original components. Skinless
Argon uses side hit-error bars; Legacy uses the existing bottom meter.
BreakOverlay remains a separate later batch, above HUD opacity.

### Temporary Legacy placement calibration (12 October 2026)

The owner review pass adds presentation offsets to the immutable `LEGACY`
layout: score X −26, accuracy X +14 / Y −4, in 768-high logical HUD units.
The stock source margins above remain explicit in `hud.py`; effective project
insets are score 36, accuracy 3, and a score-to-accuracy gap of 5. Score still
starts at Y=0. Glyph dimensions, prefix, overlap, UVs and aspect remain unchanged
for normal fonts. Accuracy follows the actual measured score bottom, and the
pie/grade/secondary collision flow consumes the resulting bounds.

These are provisional owner-feedback calibration values, **not stock lazer
geometry or an implementation of `MainHUDComponents.json`**. They apply to
every Legacy profile, including other custom skins and skinless
`--legacy-defaults`; Argon has zero offsets. This shared-profile consequence
requires owner visual signoff. Set the two layout offsets back to zero to
restore stock placement without changing providers or the CLI/API contract.
No new setting or skin-specific detection was added.

At HUD scale 1, score moves −24.375 px horizontally at 1280×720 and −36.5625 px
at 1920×1080. Accuracy moves (+13.125, −3.75) and (+19.6875, −5.625) px,
respectively. Source-geometry tests retain the stock 10/17/9 expectations with
zero calibration; paired sprite tests separately check the displacement,
unchanged glyph properties, viewport constraints and independent Argon layout.

After the owner's first screenshot review, revision 2 moves score another 8
logical units left and accuracy 2 down relative to that first calibration:
−7.5 / −11.25 output pixels for score at 720p / 1080p, and +1.875 / +2.8125
pixels vertically for accuracy. Score Y and accuracy X remain unchanged.

The subsequent 100.00% review moves accuracy another 7 logical units right
(6.5625 / 9.84375 output pixels at 720p / 1080p), retaining its Y and size.
In the real replay at 10,000 ms, the leading 1 previously crossed the rounded
left edge of the skin's foreground `scorebar-bg`; this revision clears that
edge while keeping the complete glyph run in-frame. Grade and progress pie
follow the measured accuracy bounds with their existing gaps. This remains
presentation calibration for the shared Legacy profile, not a glyph clipping
bug or saved-layout support; health artwork and its overlay order are intact.

The owner fixture's health provider remains Legacy: `scorebar-bg@2x.png`
(1366×786 logical), 120 `scorebar-colour` frames (388×78 logical), and an
explicitly blank 1×1 `scorebar-marker.png`. Large background art is drawn once
at (0,0), height-scaled with native aspect and clipped by the viewport; its
visible artwork extends well beyond the top-left HP bar. Horizontal UV crop
preserves the fill's transparent padding. A blank marker selects new-style
fill positioning but draws no visible marker or procedural replacement.

This is consistent with the audited Legacy provider, native-size geometry,
fill crop and overlay order. It does **not** match the skin's saved customised
`ArgonHealthDisplay`, which uses relative width, a custom position, anchor/origin
and bar-height settings. Saved HUD deserialisation remains deferred; Argon's
provider is not selected by partially reading these JSON entries. Legacy HP
uses R3D's existing stateless damping/animation approximation, so this audit
does not assert frame-exact lazer animation parity or universal equivalence
of fallback-provider selection.

Fresh baseline, real replay scenes, 720p/1080p component traces and pixel
comparisons for this pass are in
`/home/theaussie/std-test/hud-calibration-20261012/`; the follow-up screenshot
review is in `/home/theaussie/std-test/hud-calibration-20261012-revision2/`.
The subsequent marked grade-slot review is in
`/home/theaussie/std-test/hud-calibration-20261012-grade/`.
The full-accuracy edge review is in
`/home/theaussie/std-test/hud-calibration-20261012-accuracy100/`.
The pending Argon visual
fidelity overhaul remains separate, with `theaussie.main` as its technical
reference and R3D's Nunito/procedural counter fonts retained initially.

## R3D extras and overflow adaptations

Live grade is an R3D extra; neither native MainHUDComponents container has
this grade slot. In Legacy it is centred vertically on measured accuracy,
with its right edge 12 logical units left of the reserved 33-unit progress
pie (which itself clears accuracy by 18 units). This follows the owner's
marked screenshot location. The pie remains reserved with progress OFF,
so toggling progress does not move grade. Measured accuracy also anchors
grade and secondary flow when score/accuracy sprites are hidden. Rank art
still fits a maximum 48x32 slot; procedural rank text shares that slot.
For pathological fonts that leave insufficient space beside accuracy, grade
uses an in-frame slot below the readouts at the left margin. Argon's existing
grade slot below accuracy is unchanged. None of this consumes saved HUD JSON.

Legacy mod pills flow below native readout/pie bounds, without including
grade in that vertical stack; showing grade no longer pushes mods down.
Argon retains its existing flow below accuracy and grade. Hit counts follow
the complete wrapped mod stack. These extras move inward when their
vertical interval crosses the profile's key display, preserving a 12-unit
gap. PP retains R3D's existing top-left placement, clearing the Argon score
wedge and health bar. Watermark text clears the Argon progress info row and
key display. Aim error and strain are retained R3D overlays; the strain
graph intentionally sits beneath other HUD content. Oversized skin health
art remains a native foreground layer, not a general collision obstacle.

Legacy digits align by actual drawable extents, including tall punctuation
and nonuniform digit images. Negative or unusually large positive overlap
cannot reverse the run. Pathological font sizes fit within the viewport
without truncating digits (score height capped at 180*HUD scale, accuracy
height at 108*HUD scale; width reserves the pie). Argon scores with more than
six cells fit their original anchored area rather than losing leading
digits beyond the left edge. These overflow protections are R3D adaptations,
not claims of native lazer pixel identity.

Progress and grade have independent flags. CLI defaults remain grade ON,
progress OFF. Legacy progress can remain visible when score is disabled;
its native score/accuracy dependency is measured but not drawn. Intentional
blank assets can still occupy native layout rectangles without visible ink.

## Verification

Run `python -m pytest -q tests/test_hud.py tests/test_hud_layout.py
tests/test_hud_extras.py`, followed by `python -m pytest -q` from the repo
root. The dedicated layout tests cover active/fallback providers, partial
and empty skins, defaults and glyph overrides, transparent resources,
animated assets, prefix/@2x/overlap cases, eight-digit scores, independent
grade/progress toggles, two/four key counts, held/released input, stateless
seek, output dimensions and HUD scales. Actual EGL framebuffer checks run
at 720p. A Metal command test executes the real serializer against the same
HUD batch; native Metal pixels require macOS.

`tests/capture_hud_layout.py` captures native-resolution EGL PNGs, resource
providers, anchors/origins, visibility flags, sprite draw order and bounds.
Use `PYTHONPATH` to run it against a read-only `git archive` baseline:

```sh
PYTHONPATH=. "$PYTHON" tests/capture_hud_layout.py \
  --output "$HOME/std-test/hud-parity" --label after --repo "$REPO"
```

Before and after labels write different files. Full-scene CLI dumps and
the actual replay videos complement these HUD-isolated captures. Owner
visual review remains the acceptance gate.
