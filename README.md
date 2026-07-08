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

## Status: SCAFFOLD

Parsing is real and verified end-to-end (beatmap → curves → positions →
replay); the draw/record phases are structured stubs. See the table below.

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
├── ruleset/ruleset.py           §5.5 — STUB (hit windows/notelock/slider follow
│                                semantics documented; reconcile-to-counts plan)
├── skin/
│   ├── skin_ini.py              §3.2 — IMPLEMENTED (full field table + quirks)
│   └── skin.py                  §3.1/3.3 — file resolution IMPLEMENTED
│                                (SKIN→FALLBACK→LOCAL, @2x, frames, samples);
│                                GPU atlas + procedural Argon = later phase
├── render/
│   ├── context.py / gl.py       headless-EGL sprite batch (from catch) — IMPLEMENTED
│   ├── playfield.py             §5.4 exact camera — IMPLEMENTED
│   └── slider_body.py           STUB — the Phase-0 distance-field spike (NEXT)
├── record/
│   ├── pipeline.py              §5.2 fixed-timestep loop — IMPLEMENTED
│   ├── encode.py                §5.6 ffmpeg pipe (from mania v2) — IMPLEMENTED
│   └── audio.py                 NO-BASS offline mixer — IMPLEMENTED
├── settings.py                  §4 surface ↔ R3D preset keys (mapping table)
├── cli.py                       adapter-contract CLI (catch_renderer.py shape);
│                                --parse-only works today, rendering errors out
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

1. **Phase-0 spike — slider body** (`render/slider_body.py`): distance-field
   depth-trick tube in moderngl; a Bezier snake + an overlapping aspire case.
   The only real unknown; technique documented in the stub.
2. Golden harness: ~10 danser-rendered reference replays + frame diff.
3. Phase 1 MVP: object lifecycle, judging sim (reconcile-to-counts), cursor
   +trail, HUD reuse from mania v2, `std_renderer.py` adapter + mode-0
   routing behind `_std_renderer_available()` with danser fallback.

## License

AGPL-3.0 — Copyright (C) 2026 Cool Adults. `curves/sliderpath.py` ports
MIT-licensed geometry from ppy/osu-framework + ppy/osu (attribution in the
file header). `docs/RENDER_PLAN.md` documents the GPL-3.0 reference
renderer's architecture; this codebase ports formulas/semantics, not code.
