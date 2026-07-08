# R3D STD Renderer (`osu-std`)

The in-house **osu!standard** replay renderer — the 4th R3D mode engine,
joining mania v2, taiko and catch. Replaces danser-go for mode 0.

**Stack (locked):** Python 3.12 · moderngl ≥ 5.10 (standalone **headless
EGL** — no X server) · numpy · Pillow · osrparse · raw-RGB frames piped to
ffmpeg. **No BASS** (audio is offline numpy/ffmpeg mixing), **no GLFW**,
**no Go.**

**Behavioral spec:** [`docs/RENDER_PLAN.md`](docs/RENDER_PLAN.md) — a
complete architectural teardown of the reference renderer. Modules cite the
plan section they port; formulas are ported exactly (including the float32
quirks). Strategy: [`docs/APPROACH.md`](docs/APPROACH.md) (Option A).

## Status: RENDERING — full website settings surface

End-to-end renders ship: parsing, judgment sim (reconciled to the .osr),
slider bodies (distance-field union), skins (per-element fallback),
hitsounds, the lazer-port HUD (Argon skinless / full LEGACY set under any
custom skin, classic lg_* bakes filling gaps), spinners, results screen —
and, as of the settings-surface phase, EVERY std setting the R3D site
exposes (mania_ordr/presets.py): video/blur/parallax/triangles/
flash-to-beat backgrounds, nightcore beat overlay, mod pills, live rosu-pp
counter, hit counter, aim-error scatter, strain graph, warning arrows,
seizure card + lead-in, real fade-out, bloom, the R3D logo splash, cursor
trail-scale/rainbow/ripples, snaking-out + slider merge, beatmap [Colours].
`StdRenderSettings.from_preset(dict)` consumes the bot preset JSON
wholesale (the future `std_renderer.py` adapter entry). Accepted + NO-OP,
documented: `load_storyboard` (danser fallback stays), `show_scoreboard`
(render/scoreboard.py holds the osu!API JSON hand-off stub).

```
osu_std_renderer/
├── beatmap/                     RENDER_PLAN §2 — IMPLEMENTED
│   ├── parser.py                §2.1–2.2  3-pass parse, combo numbering (spinner
│   │                                      forces new combo, ColorOffset skip bits)
│   ├── beatmap.py               §2.3      struct, ParsePoint, spawn queue,
│   │                                      memoised stacking
│   ├── difficulty.py            §2.4      DifficultyRate (float32 quirk),
│   │                                      radius *1.00041, preempt/fade, windows,
│   │                                      HR/EZ/DT/HT, ARReal/ODReal
│   ├── stacking.py              §2.7      v6+ new + old algorithms (ppy port)
│   ├── pause.py                 §2.8      breaks
│   └── objects/
│       ├── base.py              §2.5      commonParse, ModifyPosition (HR flip +
│       │                                  stack offset = idx*radius/10)
│       ├── circle.py / spinner.py §2.5
│       ├── slider.py            §2.5      velocity/tick math, scorePath walk,
│       │                                  per-edge hitsounds, PositionAt
│       └── timing.py            §2.6      red/green, GetRatio SV clamp 0.1–10,
│                                          FinalizePoints inheritance
├── curves/                      §2.9 — IMPLEMENTED
│   ├── sliderpath.py            VERBATIM copy of catch's bit-exact
│   │                            PathApproximator port (attribution kept)
│   └── __init__.py              StdSliderPath: true 2D head, no flips at parse
│                                (gaps vs plan documented in the docstring)
├── replay/replay.py             §5.5 — IMPLEMENTED (osrparse decode, garbage
│                                first frame, 2D interpolation; per-ms walk
│                                contract documented for the ruleset)
├── ruleset/ruleset.py           §5.5 — IMPLEMENTED (judgment sim: ported
│                                ppy/osu hit windows + notelock — stable
│                                LegacyHitPolicy AND lazer StartTimeOrdered,
│                                auto-picked by .osr game_version; slider
│                                follow-area tracking w/ the 3 distinct
│                                part-miss kinds; simplified spinners;
│                                reconcile-to-counts snaps to the replay)
├── skin/
│   ├── skin_ini.py              §3.2 — IMPLEMENTED (full field table + quirks)
│   └── skin.py                  §3.1/3.3 — file resolution IMPLEMENTED
│                                (SKIN→FALLBACK→LOCAL, @2x, frames, samples);
│                                GPU atlas + procedural Argon = later phase
├── render/
│   ├── context.py / gl.py       headless-EGL sprite batch (from catch) — IMPLEMENTED
│   ├── playfield.py             §5.4 exact camera — IMPLEMENTED
│   ├── slider_body.py           distance-field depth-trick bodies + snaking
│   │                            + build_merged (§4.9 SliderMerge union pass)
│   ├── scene.py                 object lifecycle + full frame draw (§5.3)
│   ├── hud.py                   lazer HUD port (Argon / legacy) + mod pills,
│   │                            pp/hit counters, aim scatter, strain graph
│   ├── background.py            §4.10 dim envelope + blur/parallax/flash
│   ├── video_bg.py              §4.10 LoadVideos (mania v2 port)
│   ├── effects.py               triangles/logo/seizure/fade/arrows/ripples
│   ├── bloom.py                 §4.10 bloom post-pass (bright+blur+add)
│   ├── pp.py                    rosu-pp gradual pp + strains (fail-soft)
│   ├── scoreboard.py            §4.6 ScoreBoard NO-OP + JSON loader stub
│   ├── markers.py / spinner.py  arrows/ticks/followpoints, spinners
│   ├── skin_elements.py         real-skin textures, per-element fallback
│   ├── textures.py              procedural Argon-ish + classic lg_* bakes
│   └── results.py               Red's results-screen outro
├── record/
│   ├── pipeline.py              §5.2 fixed-timestep loop — IMPLEMENTED
│   ├── encode.py                §5.6 ffmpeg pipe (from mania v2) — IMPLEMENTED
│   ├── hitsounds.py             §3.4 sample grid + nightcore beat overlay
│   └── audio.py                 NO-BASS offline mixer + pre-roll/fade ramps
├── settings.py                  §4 surface ↔ R3D preset keys — from_preset()
│                                consumes the bot preset JSON wholesale
├── cli.py                       adapter-contract CLI (catch_renderer.py shape)
└── tools/
    ├── parse_summary.py         real-map end-to-end verification
    └── plot_map.py              CPU debug plot (PIL): rings/paths/ticks/stacks
```

## Verify the scaffold

```bash
python3.12 -m venv .venv && .venv/bin/pip install numpy pillow osrparse moderngl
.venv/bin/python -m tests.run_all
.venv/bin/python -m osu_std_renderer.tools.parse_summary MAP.osu [REPLAY.osr]
.venv/bin/python -m osu_std_renderer.tools.plot_map MAP.osu out.png --zoom zoom.png
.venv/bin/python -m osu_std_renderer REPLAY.osr BEATMAP_DIR --parse-only
```

## Next steps (from APPROACH.md §4)

1. The `std_renderer.py` service adapter + mode-0 routing behind
   `_std_renderer_available()` with danser fallback —
   `StdRenderSettings.from_preset()` is the entry it calls.
2. Golden harness: ~10 danser-rendered reference replays + frame diff.
3. Deferred subsystems (accepted + no-op'd, documented in settings.py):
   storyboards (danser keeps SB maps), the live scoreboard (needs the
   bot's osu!API leaderboard hand-off — render/scoreboard.py).
4. Perf phase: texture-array atlas + PBO readback (the mania v2 gpu/
   playbook), FBO cache for static slider bodies, video texture
   streaming without per-frame mipmap rebuilds.

## License

AGPL-3.0 — Copyright (C) 2026 Cool Adults. `curves/sliderpath.py` ports
MIT-licensed geometry from ppy/osu-framework + ppy/osu (attribution in the
file header). `docs/RENDER_PLAN.md` documents the GPL-3.0 reference
renderer's architecture; this codebase ports formulas/semantics, not code.
