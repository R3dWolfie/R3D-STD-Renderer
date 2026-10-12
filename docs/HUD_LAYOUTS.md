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

The owner-selected Legacy calibration uses **one shared right edge** for
score and accuracy: `layout.legacy_counter_right(width, scale)` returns
`width + (legacy_accuracy_offset.x - 17)*scale`, currently
`width - 3*scale` in the 768-high HUD space. Accuracy retains its approved
`legacy_accuracy_offset=(14,-4)`, `.576` text scale and vertical placement
at actual score bottom + `5*scale`. Score retains `.96` scale and Y=0.

This supersedes the earlier score X offset of −26 (effective inset 36).
Relative to that immediately preceding result, score moves right by
`33*scale` logical units: 30.9375 pixels at 1280×720 and 46.40625 pixels
at 1920×1080 with scale 1. Score/accuracy drawable right-edge separation
is now zero. Their shared output X is `output_width - 3*scale*height/768`:
1277.1875 at 720p and 1915.78125 at 1080p, scale 1.

This is **project calibration**, not stock lazer's separate score/accuracy
margins of 10/17, and not saved `MainHUDComponents.json` support. It applies
to every Legacy profile: complete, partial, empty and blank custom skins,
asset fallbacks, the existing font override and skinless `--legacy-defaults`.
Argon remains independent. There is no separate score X calibration to
silently drift from accuracy. Setting accuracy offsets to zero gives both
counters the native accuracy margin/gap; it does not restore the independent
stock score margin.

Standardised score formatting remains a **minimum of six digits**: `000000`,
`999999`, `1000000`, `1001273`, through eight digits and beyond. Normal fonts
expand left as a leading digit is added, retaining the rightmost glyph,
fixed-width `'5'` advance minus overlap, native `ScorePrefix` glyph sizes,
`@2x` normalization and UVs. Punctuation uses its own advance. The existing
one-second roll can keep the displayed score below one million briefly after
the target crosses it. These checks concern the settled/rolled value, not
new score calculation or an eight-digit Classic minimum.

Viewport width/height fitting remains for unusually large fonts or long
runs. That deliberate adaptation can reduce all score glyphs and change
accuracy's dependent vertical position when fit activates. Nonuniform
native glyph heights/overhang may also affect drawable bounds; extreme
positive overlap keeps the existing non-reversing zero-advance guard.
No fit activates for the owner's six, seven or eight digits at tested
720p/1080p and HUD scales .75/1/1.25. Grade's fixed font-derived pivot,
native pie and all other HUD positions remain unchanged in those cases.

The supplied 12 October screenshot references show different gameplay
moments and different artwork from the current extracted test skin. They
establish the requested right-alignment/digit-growth contract, not matched
frame or font pixel parity. Native-resolution before/after EGL captures
and glyph/visible-ink measurements are recorded in
`/home/theaussie/std-test/legacy-score-alignment-20261012/`. Million-score
captures there are explicitly labelled synthetic HUD fixtures; the owner's
real replay is preserved separately.

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
this grade slot. Legacy now uses a **fixed right-centre pivot**, measured from
the selected font and immutable profile, independently of the current score,
accuracy string or rank artwork. The reference is the owner's preferred
two-integer-digit `97.68%` placement, 12 logical units left of its reserved
33-unit pie (which itself clears accuracy by 18). The slot also clears the
widest legal percentage's pie with a one-unit minimum gap; wide/nonuniform
fonts can therefore move this reference slightly left. The owner's extracted
font gives a pivot of `(1238.333333, 73.64)` in 768-high HUD coordinates at
16:9 and scale 1, versus the previous width-dependent X of `1227.333333` at
100% and `1238.853333` at 97.68%. This is project presentation calibration,
not native lazer grade placement. Accuracy placement remains unchanged by
the subsequent shared-right-edge score fix.

The pivot stays fixed with progress OFF or hidden score/accuracy. The native
pie retains its existing current-string placement; grade no longer inherits
that motion. Rank art still fits a maximum 48x32 slot, retains aspect ratio,
and uses the same right-centre pivot as procedural text. Pathological fonts
that leave insufficient side space use a fixed left-margin slot below the
font's readout envelope. Argon's grade placement is unchanged. None of this
consumes saved HUD JSON.

Live lazer rank now uses raw scoring accuracy and the ordinary object Miss
count, with exact 1.0 required for SS, then .95/.90/.80/.70 cutoffs. An ordinary
Miss caps S/SS at A; missed ticks and tails lower accuracy without incrementing
that cap. Stable/Classic `grade_for()` callers retain object-count accuracy.
Rank is never derived from rolled/truncated/rounded counter glyphs.

`SimResult.accuracy_timeline` contains frozen samples from the **existing**
chronological scoring lattice for object, tick, repeat and tail results.
HUD accuracy and rank share those samples; BreakOverlay reads the same raw
accuracy/rank. Provenance is `derived-lattice` or `reconciled-lattice`, not a
claim of native callback timing. Popup-only/older inputs use the deterministic
`object-events-fallback` authority for lazer, without invented nested times.
The established last-popup final accuracy pin is preserved (`canonical-final-pin`
or `lattice-final-pin`) and wins from that popup onward. Consequently, a map
ending on a slider can still present its eventual/canonical final accuracy
before the tail's nominal time. Reconciled part positions and the sim's tail
timing/rolling-counter approximations remain limitations; this HUD fix does
not modify judged outcomes, score events or slider visual lifecycles.

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
