# Replacing danser for osu!standard — Architecture & Strategy

**Date:** 2026-07-08 · **Status:** investigation complete, read-only — no code changed
**Scope:** R3D Renderer (Discord bot + renderer.r3dwolfie.com, monetized supporter tier). std is the last mode rendered by third-party software (danser-go). mania/taiko/catch are already in-house.

---

## TL;DR + Recommendation

**Recommendation: Option A — build `osu-std` as the 4th mode in the existing in-house framework (Python 3.12 + moderngl headless-EGL + ffmpeg), following the exact playbook that shipped catch and taiko. Keep danser as the mode-0 fallback during the transition (the routing pattern for that already exists), and fix the one real license item (BASS) with a one-time €125–€950 license if any paid render keeps flowing through danser meanwhile.**

- **Effort: ~3–5 person-weeks to a shippable MVP** (default-skin std renders behind a beta flag), **~9–14 person-weeks cumulative to visual parity** with what danser delivers today (stable skins, mods, HUD, results screen — *excluding* storyboards), **+3–6 pw** if you want storyboard support instead of keeping a danser fallback for those renders.
- **The legal premise needs correcting (verified, sources in §2):** danser-go is plain **GPL-3.0 with no non-commercial clause**. GPL copyleft triggers on *distribution of the program*, not on running it server-side; the MP4 output is not covered. **Running danser inside a for-profit SaaS is permitted by its license.** The genuine license item inside danser is the **BASS audio library** (proprietary, free for non-commercial use only). The dominant *actual* legal risk of this business — unlicensed music inside every rendered MP4 — is identical no matter whose renderer draws the frames.
- **So why still build our own?** Because it's the right move for reasons *beyond* the (mostly imagined) GPL problem: full control of the paid product's core, no BASS gray area at all, kills danser's operational pain (X-server dependency, 240 s scoreboard-fetch stalls, toolbox/glibc/LD_LIBRARY_PATH dances, no clean AMD path), enables std in Versus/Showdown/presets/Argon-coherent branding, and removes the only third-party binary from the revenue path. The team has shipped three mode renderers in ~5 weeks of wall-clock; std is the hardest mode, but roughly half of it already exists in the codebase — including a **bit-exact port of osu!'s slider path geometry** that is already in production for catch.
- **Option B (build on osu!lazer) is rejected**: lazer *code* is MIT (commercial-safe), but the game *assets* (osu-resources: default skins incl. Argon textures, hitsounds) are **CC-BY-NC 4.0 — explicitly non-commercial**. A lazer-based renderer in a paid service would be a *worse* legal position than danser, plus there is no supported headless-record path. Lazer's value is as a **reference implementation to port logic from under MIT** — which the team already does (`sliderpath.py`).

---

## 1. Licensing reality (verified 2026-07-08, all claims sourced)

### 1.1 danser-go: GPL-3.0, and GPL is not the problem

- LICENSE is stock **GPL-3.0** ("Majority of danser-go project, unless stated otherwise… is released under the following license"). No non-commercial clause anywhere in LICENSE, README, or releases; no statement from Wieku restricting services. — https://raw.githubusercontent.com/Wieku/danser-go/master/LICENSE · https://github.com/Wieku/danser-go
- **GPL-3.0 permits commercial use** ("The right to sell copies is part of the definition of free software" — GNU FAQ). **Copyleft triggers on conveying the program.** Running danser server-side and distributing only MP4s is not conveyance; even privately *modified* danser builds carry no source-release duty ("the organization is just making the copies for itself"). Network-service source obligations are **AGPL**, and danser is not AGPL. **Program output is not covered** by the program's copyright unless it embeds the program itself — video frames don't. — https://www.gnu.org/licenses/gpl-faq.html (#DoesTheGPLAllowMoney, #InternalDistribution, #UnreleasedMods, #WhatCaseIsOutputGPL)
- **Precedent:** o!rdr (ordr.issou.best) runs danser server-side at scale, publicly, with donation perks (4K unlock), no special arrangement documented or needed. — https://ordr.issou.best/donate · https://github.com/MasterIO02/ordr-client
- **The real catch — BASS.** danser's own CREDITS.md: *"This software relies heavily on BASS audio library, which is a commercial product. As it's free for non-commercial use, you have to buy appropriate licenses if you plan to sell this software or its derivatives."* `libbass.so` / `libbass_fx.so` / `libbassmix.so` are sitting in our install at `/home/red/.local/share/danser-go/`. un4seen's terms are written around "your product," so a supporter-funded service using BASS internally is at minimum a gray area. Fix = one-time license: **Shareware €125** (product priced ≤ €40) / Single Commercial €950 — not a rewrite. — https://github.com/Wieku/danser-go/blob/master/CREDITS.md · https://www.un4seen.com/bass.html
- danser's bundled gameplay assets are **not ppy's** — they're Haskorion's "Redd Glass HD," used with permission granted *to danser*. Worth a courtesy ask if we keep danser fallback long-term.

### 1.2 osu!lazer: MIT code, NON-COMMERCIAL assets

- **ppy/osu and ppy/osu-framework code: MIT.** Free to use, port, and sell. README carve-outs: "osu!"/"ppy" **branding is trademark-protected**, and *"game resources are covered by a separate licence."* — https://github.com/ppy/osu · https://github.com/ppy/osu-framework
- **ppy/osu-resources (default skins incl. Argon/Triangles/Classic textures, hitsounds): CC-BY-NC 4.0** — "for NonCommercial purposes only." Shipping or rendering ppy's actual asset files in a paid service is squarely commercial use of NC content. Contact for other uses: contact@ppy.sh. — https://github.com/ppy/osu-resources
- **Implication for us:** porting lazer *logic and look* (algorithms, layouts, procedurally redrawn Argon-style art — what taiko/catch/mania already do) rides on MIT and original artwork. Copying osu-resources *files* does not. The existing "match lazer, port skinning generically, redraw Argon procedurally" directive is exactly the legally-sound version of lazer fidelity.

### 1.3 The ppy/IP exposure that doesn't depend on the renderer

- **Music is the elephant.** Nearly every MP4 we serve embeds commercially unlicensed songs; osu!'s own music licensing does not extend to third-party monetized redistribution. This risk is *identical* for danser, lazer, or our renderers. Mitigations: the DMCA machinery we already have (`renders.dmca_muted/dmca_notice` columns exist), takedown responsiveness, and o!rdr-style framing of payments as supporter perks rather than "buying a video." — https://osu.ppy.sh/legal/en/Music_licensing · https://osu.ppy.sh/legal/en/Terms
- Beatmap backgrounds/storyboards and third-party user skins carry the same UGC uncertainty; ppy's ToS grants no third-party commercial license to beatmap content.
- **Trademark:** keep "osu!" out of product branding; descriptive use ("renders osu! replays") is normal nominative use.
- **Bottom line: "we can't use danser for a for-profit product" is not what the licenses say.** The decision to replace danser should be made — and is justified — on control, operations, product coherence, and eliminating the BASS gray area, not on GPL fear. Meanwhile two cheap actions de-risk everything: a BASS license (if danser stays in any paid path) and an email to contact@ppy.sh about asset/trademark posture.

---

## 2. The foundation: what we already have (this is the strongest card)

All three in-house renderers share one stack: **Python 3.12 · moderngl ≥ 5.10 (standalone headless EGL context — no X server) · numpy · Pillow · osrparse · raw-RGB pipe into ffmpeg**. No game engine. One venv serves catch+taiko; mania v2 has its own. Everything runs as `render(...)`-signature adapters behind one worker.

### 2.1 mania v2 — the flagship (~12,000 lines)
`/home/foof/r3drender/OsuManiaRenderer_v2/osu_mania_renderer_v2/`

- `render.py` — orchestrator with a deliberate split: `build_render_plan` / `build_frame_state` compute gameplay semantics independently of the draw path (two draw paths already share them). This is the architecture a std mode plugs into.
- `gpu/` — `context.py` (HeadlessGl), `atlas.py` (sprite atlas, texture arrays), `renderer.py` (~1,500 lines: playfield geometry from skin.ini or lazer defaults, notes, receptors, **full HUD**: score/combo/acc/HP bar/PP/hit-error popups/UR histogram/progress bar/grade/mode pills/banner/watermark/results overlay/**flashlight post-pass**), `shaders.py`, `text.py`, `readback.py` (PBO-accelerated readback — the Jun-23 perf work, ~17% faster).
- `skin_ini.py` (23 KB) — real skin.ini parser: quirks, case-insensitivity, `//` comments, multiple `[Mania]` blocks, per-column overrides, spec-default fallbacks. **Directly extensible to the std keys.**
- `judgments.py` — includes `reconcile_to_counts`: the sim's live numbers are *reconciled to the replay's authoritative final counts*, so the simulator doesn't have to be perfect. This trick is load-bearing for std feasibility (see §5 risks).
- `hitsounds.py` (build hitsound track), `mods.py` (rate mods incl. the Jul-04 HT fix), `pp.py`, `encode.py` (FfmpegPipe, encoder probing, `loudnorm` single-pass), `video_bg.py` (background video), `wiki_renderer.py` + `wiki_elements/` (the "wiki-driven" skin-faithful draw path), results screen, fail overlay.

### 2.2 catch (~3,700 lines) — quietly, half of a std renderer
`/home/foof/r3drender/osu-catch/osu_catch_renderer/`

- **`sliderpath.py` — a bit-exact port of osu!'s slider geometry** (its own docstring: *"Ported from: ppy/osu-framework PathApproximator.cs, CircularArcProperties.cs; ppy/osu SliderPath.cs"*): Bezier subdivision, Catmull, perfect-circle arcs, linear, expected-distance length capping, `position_at(progress)`. **In production; pixel-validated.** This is the scariest single piece of std math, already done. (MIT source → legal to port; add attribution in the repo.)
- **`beatmap.py` parses std-format .osu files** — catch maps *are* std maps: circle/slider/spinner type bits, red/green timing points, SV multipliers, `SliderMultiplier`/`SliderTickRate`, and `_slider_objects()` generating **head / ticks / repeats / tail in time order per span** — this is the std slider event timeline, verbatim. Plus `legacy_random.py` (stable's RNG, ported).
- `replay.py` — osrparse frame decode, garbage-first-frame handling, **binary-search + linear interpolation of cursor position over time** (`catcher_x_at` — std needs the same in 2D), fail detection from the life-bar graph, grade calc, accuracy semantics.
- `models.py` — **`ar_to_preempt_ms`** (AR→preempt already implemented), CS scaling helpers.
- `scene.py` — the sim→per-frame-draw-list pattern: geometric judging, combo/score/HP running sim, reconciled to replay counts.
- `gl.py` — the minimal moderngl sprite batch (EGL standalone context, painter's-order quads, RGB24 readback). Same file in taiko.

### 2.3 taiko (~4,600 lines) — the "procedural Argon" playbook
`/home/foof/r3drender/osu-taiko/osu_taiko_renderer/`

- `argon/` — lazer's Argon look **redrawn procedurally** (PIL/numpy-baked textures, additive compositor, geometry, glyph fonts) — zero ppy assets, which per §1.2 is exactly the commercially-safe way to deliver "lazer-look" defaults. Same approach exists for mania (v2 Argon branch) and catch (argon counter/health).
- `scene.py` — greedy hit-matching inside OD windows → GREAT/OK/MISS with combo/score/acc/HP; HD fade handling; **skin-element-presence fallback** (use the user skin's `taiko-*` sprites when present, else Argon) — the same graceful skin/default blend std needs.
- Historical note in `gl.py`: catch/taiko were consciously built "quick and self-contained" pending a unified **"VRender" wiki-driven branch** — i.e., a generic skin-spec-driven renderer was already the stated direction. std is the natural forcing function to hoist the shared pieces.

### 2.4 The service pipeline std must slot into
`/home/foof/r3drender/mania_ordr/mania_ordr/`

- Entry: Discord bot (`bot.py`/`app.py`) and the web render panel (`embed_server.py` + `templates/`, SSE progress via `web_render.py`) → `RenderJob`.
- Queue: `nas_queue.py` writes a JSON spec (`type: "r3d_render"`, `r3d_config` with replay path, beatmap md5, skin archive, mode 0–3, resolution/fps, full preset dict, watermark, supporter flag) to the NAS `job-queue/pending/`; GPU workers (systemd `job-worker-pool-b@1/2` on FoofPC, "r3d_render ONLY") claim and run `cli/r3d_render.py` (821 lines).
- **Mode routing in `cli/r3d_render.py` (~line 519) — the pattern to copy:**
  - mode 3 → mania v2 · mode 2 → `_build_catch_renderer()` *if available, else danser* · mode 1 → taiko (no danser fallback; danser panics on taiko) · **mode 0 → danser. std is the last danser user.**
- Adapters `catch_renderer.py` / `taiko_renderer.py` wrap the standalone CLIs with an identical async `render(...)` contract (progress regex, RenderError). **A `std_renderer.py` adapter is a ~150-line copy of `catch_renderer.py`.**
- Post-steps shared by all modes: loudnorm to −14 LUFS, watermark (danser path needs a second ffmpeg pass; in-house renderers bake it in-loop), thumbnail, local-scratch→NAS publish (CIFS crash-loop lesson), gallery/DB insert, supporter/free tier enforcement (720p30 + forced watermark + expensive-features-off for free; 1080p60/4K/120fps paywalled).

### 2.5 What danser currently provides for std — the surface to replicate
`danser_renderer.py` (612 lines) + `/home/red/.local/share/danser-go/settings/default.json`:

- Invocation: per-render settings JSON inherited from `default.json` (`OsuSongsDir` → flat md5 beatmap cache; `OsuSkinsDir`/`CurrentSkin` → resolver-extracted .osk, Night05 fallback), then `danser-cli -settings r-<id> -replay <osr> -skip -record -out <id>` under Xorg `:99` (or xvfb fallback) with `LD_LIBRARY_PATH` pointed at danser's bundled ffmpeg; NVENC p2/p5 / vaapi-wrapper / libx264 tier logic; 240 s stall watchdog (danser's un-disableable top-50 leaderboard fetch from osu.ppy.sh stalls renders).
- **Feature surface actually exposed to users via `_apply_preset_to_danser_cfg` (the parity checklist):** score/grade/combo/HP/hit-error meter + UR/aim-error meter/PP counter/key overlay/scoreboard (+avatars)/mods/hit counter/strain graph/boundaries/results screen (+duration)/warning arrows/hit lighting · bg dim (intro/game/breaks)/blur/parallax/**storyboards**/**bg video**/triangles/flash-to-beat · seizure warning/bloom/logo · lead-in/skip-intro/fade-out · cursor (draw, skin cursor, scale, trail + long-trail, rainbow, ripples) · approach circles/combo numbers/follow points/slider snaking in+out/slider merge · volumes/offset/nightcore samples/beatmap-samples toggle · combo-color source (skin vs beatmap).
- Not exposed but rendered by default: sliders (ball/follow/ticks/repeats), spinners, stacking (`StackEnabled`), break overlays, HD/HR/DT/FL visuals, skinned judgments/numbers/hit-lighting.
- Ops note: **free tier already forces `load_storyboard=False`, `load_video=False`, bloom/triangles off, skip-intro on, no results screen** — so an MVP without storyboards degrades *supporter* renders only, and only when maps have storyboards.

Fleet reality: ~842 completed renders logged, avg ~71 s each; danser benchmark 5-min replay ≈ 28 s (Xorg+NVENC, 2070S) vs 95 s (xvfb). Pools: A=2070S (FoofPC), B=1070 (new render box), C=AMD (RedPC) — danser's encoder path on C is a vaapi shell-wrapper hack; a moderngl+system-ffmpeg renderer makes all three pools uniform.

---

## 3. Options

### Option A — 4th in-house mode renderer (RECOMMENDED)
New `osu-std` package in the catch/taiko mold + `std_renderer.py` adapter + one routing line in `r3d_render.py` (mode 0 → custom when available, danser fallback — mechanism already exists for catch).

**Already in hand (from §2):** slider path math (bit-exact), slider event timeline (head/ticks/repeats/tail), std .osu parsing, timing/SV machinery, AR→preempt, CS scaling, replay frame decode + interpolation, fail detection, reconcile-to-counts judging strategy, headless GL sprite batch, atlas/text/PBO pipeline, full HUD, skin.ini parser, .osk resolution + per-mode skin selection, hitsounds, mods/rate handling, bg video, loudnorm/encode, watermark, results screen, procedural-Argon playbook, tier enforcement, queue/adapter plumbing.

**Genuinely new work:**
1. **Slider body rendering** — the one hard graphics problem: union of circles swept along the path with border + interior gradient. Standard solution (used by danser and lazer alike): render path-quad geometry with fragment distance + depth-test max-union into an offscreen target, then shade by distance; snaking = draw sub-range of path. New shader work in moderngl; well-documented technique; the path *positions* are already exact.
2. 2D playfield object lifecycle: approach circles, fade/preempt, stacking algorithm (well-specified, ~100 lines, port from lazer/stable), combo colors + numbers, hit-lighting, judgment popups at object position, follow points, repeat arrows, slider ball + follow circle, ticks.
3. Spinners: sprites + RPM from cursor angle deltas + bonus display (fidelity can start low; spinners are seconds per map).
4. Cursor layer: 2D interpolation (extend catch's 1D), cursor/trail (+optional smoke), trail shader.
5. std judging sim: OD hit windows, in-order note matching (approximate notelock), slider follow-state → tick/end/sliderbreak, all *reconciled to the replay's real counts* so sim imperfection never changes the final numbers.
6. std skin surface: hitcircle/overlay/approachcircle/default-N/slider*/reversearrow/spinner-*/followpoint/hit0-300 (+ animation frames, @2x) — extend `skin_ini.py` + atlas; Argon-procedural default via the taiko playbook.
7. Punt list for MVP: storyboards (keep danser fallback for `load_storyboard` supporter renders, or drop toggle temporarily), live scoreboard (needs osu API; we already hold tokens — later), aim-error meter, strain graph.

**Trade-offs:** most engineering effort of all options; a visual-parity long tail (weird old maps, aspire sliders, skin quirks) that danser spent years sanding; Python per-frame cost at 1080p60 (mitigations proven in mania: texture arrays, PBO, instancing; NVENC does the encode). In exchange: no third-party binary in the paid path, no BASS, no X server, uniform pools, std joins Versus/Showdown/presets/Argon branding, and every future feature request is ours to implement.

### Option B — build on osu!lazer (REJECTED as foundation, KEEP as reference)
- Legal: code MIT ✓, but **osu-resources assets CC-BY-NC ✗** — a paid service rendering ppy's default skins/hitsounds is precisely the violation we're trying to avoid; you'd have to strip and replace all assets anyway, forfeiting the "perfect accuracy for free" appeal.
- Technical: no supported headless-record API; you'd screen-capture a .NET game under Xvfb — heavier and *more* fragile than danser (which purpose-built offline rendering), a moving target tracking lazer releases, and C#/.NET ops foreign to this codebase.
- Correct use: **the reference implementation.** Keep porting algorithms/layouts from MIT code with attribution — sliderpath.py proves the workflow. Diff our frames against lazer/danser output in tests.

### Option C — keep danser, fix licensing (legitimate; rejected as end-state, adopted as transition)
- Per §1, buy a BASS license (likely the €125 shareware tier given supporter pricing), keep o!rdr-style supporter framing, courtesy notes to Wieku/Haskorion → danser use becomes defensible indefinitely at near-zero cost.
- Rejected as the end state because the strategic reasons stand (control, ops pain, product coherence, third-party binary in the revenue core, danser's own project risk) — but **this is exactly the right bridge**: it removes all urgency, letting the std renderer ship on quality rather than legal panic.

---

## 4. Recommended roadmap (Option A, danser as fading fallback)

**Phase 0 — De-risk + scaffold (0.5–1 pw)**
Golden-test harness first: pick ~10 varied prod std replays, render via danser, store reference frames for pixel-diffing throughout development. **Spike the slider-body shader in moderngl (1–2 days)** — it's the only real unknown; everything else is known-shape work. Scaffold `/home/foof/r3drender/osu-std/osu_std_renderer/` from the catch package; keep catch's `beatmap.py` slider machinery but retain objects as circles/sliders/spinners instead of converting to fruit; std replay decode (x, y, keys); playfield transform (512×384 osu-pixels → letterboxed screen); circles + approach circles + cursor dot on screen → ffmpeg. *Milestone: an ugly but recognizable render.*

**Phase 1 — MVP (cumulative ~3–5 pw)**
Slider bodies (snaking in/out) + ball + follow circle + ticks + repeat arrows; stacking; combo colors/numbers; judging sim + reconcile-to-counts; judgments + hit lighting; spinners (basic); cursor trail; HUD by reuse from mania v2 (score/combo/acc/HP/PP/UR/hit-error/key overlay/progress/grade/results/watermark); bg dim + bg video (`video_bg.py`); HR/HD/DT/EZ/FL visuals (flashlight pass exists); hitsounds + loudnorm via existing encode path; `std_renderer.py` adapter + mode-0 routing behind `_std_renderer_available()` with danser fallback. *Milestone: default-skin (procedural Argon) renders shippable behind a supporter beta toggle.*

**Phase 2 — Skin parity (cumulative ~6–9 pw)**
Full std `skin.ini` surface + animated elements + @2x in `skin_ini.py`/atlas; lazer-default layout fallbacks (port LegacySkin defaults, as mania already does); test matrix from the `skin_catalog` + `user_skin_library` DB tables (render top-N most-used skins, eyeball + pixel-diff); lazer replay quirks (`is_lazer` column exists); spinner + break/warning-arrow polish. *Milestone: user-skin renders match danser on the catalog set.*

**Phase 3 — Flip the default (cumulative ~9–14 pw)**
Perf pass using the mania playbook (texture arrays, PBO, instanced sprites; target ≤2× danser wall-time at 1080p60 — free-tier 720p30 is easy either way); rollout across pools A/B/C (EGL headless kills the Xorg/xvfb requirement; AMD pool uses system ffmpeg vaapi natively — the wrapper hack dies); N-replay side-by-side QA vs danser; flip mode-0 default to in-house; danser demoted to explicit fallback (`load_storyboard` renders and emergencies). *Milestone: no danser in the default paid path.*

**Phase 4 — Optional/ongoing**
Storyboards (+3–6 pw for the sprite/move/scale/fade/rotate/loop subset covering most ranked maps) → then danser fully retired; live scoreboard via existing osu API tokens; aim-error meter; strain graph; the permanent long tail (aspire/2B/ancient-format maps) handled by bug-report-driven fixes, as with mania today.

**Calibration honesty:** catch took ~1.5–2 pw-equivalent, taiko ~3–4, mania v2 ~8–10 over ~5 weeks wall-clock total. std's feature surface ≈ taiko + catch combined, plus sliders. 9–14 pw to parity assumes the same person+Claude cadence and the reuse mapped in §2 actually landing. It will not match danser frame-for-frame on day one — parity is a target you converge on with golden diffs, and reconcile-to-counts guarantees the *numbers* are always right even while pixels converge.

## 5. Top risks

1. **Slider visual fidelity** (highest). Body union rendering, huge/overlapping/aspire sliders, snaking edge cases. *Mitigate:* depth-trick distance-field technique (industry-standard for osu sliders), exact path math already in prod, Phase-0 shader spike before commitment, golden-frame diffs vs danser.
2. **Skin parity long tail.** std skins are the most numerous and quirkiest (animation frames, overlap rules, weird @2x mixes). *Mitigate:* wiki-driven approach + lazer LegacySkin defaults (mania precedent), catalog-driven test matrix, ship Argon-default first (fully ours), expand skin support behind the beta flag.
3. **Performance (Python at 1080p60).** More live sprites than mania (trails, followpoints, sliders). *Mitigate:* proven optimizations (atlas/texture-array/PBO/instancing), NVENC encode, free tier is 720p30, `phase_timing` profiling discipline; accept ≤2× danser wall-time initially — render farm has headroom (avg load ~71 s/render).
4. **Sim inaccuracies visible in gameplay** (phantom sliderbreaks, wrong notelock on abuse maps). *Mitigate:* reconcile-to-counts (already the accepted pattern in three shipped modes), life-bar fail detection, accept cosmetic deviation on adversarial maps.
5. **Legal residue independent of renderer:** music/art inside MP4s from a paid service. *Mitigate:* keep DMCA machinery sharp, o!rdr-style supporter framing, never ship osu-resources assets, keep Argon *procedural*, trademark hygiene, send the two emails (§6). If danser remains in any paid path during transition: BASS license (€125 tier likely suffices).
6. **Scope creep.** Versus/Showdown/std-Wrapped integration temptations mid-build. *Mitigate:* the adapter contract makes std automatically compatible with existing compositing — defer all extras until Phase 3 flips.

## 6. Concrete next steps

1. **Decide:** Option A + danser-as-fallback transition (this doc's recommendation). No emergency: §1 removes the legal deadline.
2. **Two emails, one purchase (~1 hour + ≤€125):** contact@ppy.sh (asset/trademark posture for a supporter-funded render service); courtesy note to Wieku (+ Haskorion re: Redd Glass/Night05 default-skin use); BASS shareware license from un4seen if danser keeps serving paid std renders during transition.
3. **Phase-0 spike (1–2 days):** slider-body shader PoC in moderngl on a nasty slider (a Bezier snake + an overlapping aspire case). This single spike converts the biggest unknown into a known.
4. **Golden harness:** 10 danser-rendered reference replays + frame-diff script into the new package's `tests/`.
5. **Scaffold `osu-std`** from the catch package; hoist/copy `sliderpath.py`, `gl.py`, `legacy_random.py`, `fonts.py` per existing convention (or start the long-planned shared "VRender" core — the code comments already point there); add MIT attribution headers on lazer-ported files.
6. **Wire routing** mirroring catch: `std_renderer.py` adapter + `_std_renderer_available()` in `cli/r3d_render.py`, danser fallback preserved — so the very first MVP build can serve real traffic behind a flag.

*Key file references:* `mania_ordr/cli/r3d_render.py` (routing, ~line 519) · `mania_ordr/danser_renderer.py` (danser surface) · `mania_ordr/nas_queue.py` (job wire format) · `osu-catch/osu_catch_renderer/{sliderpath,beatmap,replay,scene,gl}.py` (std building blocks) · `osu-taiko/osu_taiko_renderer/argon/` (procedural Argon playbook) · `OsuManiaRenderer_v2/osu_mania_renderer_v2/{render,skin_ini,judgments}.py` + `gpu/` (engine + HUD + skin.ini + reconcile pattern). All paths under `/home/foof/r3drender/` on FoofPC.
