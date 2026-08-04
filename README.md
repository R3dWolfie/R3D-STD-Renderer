# R3D STD Renderer (`osu-std`)

In-house **osu!standard** (mode 0) replay → MP4 renderer — Python + headless-EGL moderngl, judgments simulated from the `.osr` and reconciled to its counts.

## Part of the R3D Renderer

This is the **std engine** of the [R3D Renderer](https://renderer.r3dwolfie.com) — a self-hosted osu! replay-to-video service (Discord bot + website, package `mania_ordr`) that dispatches each render to a per-mode engine repo. Alongside the mania (v2), taiko, and catch engines, this repo handles **mode 0 (osu!standard)** — the in-house replacement for danser-go on std.

The core invokes this engine as a **fresh subprocess per render** (`python -m osu_std_renderer …`); there is no long-running service, so an edit-in-place deploys on the next render with no restart. `StdRenderSettings.from_preset(dict)` consumes the bot's preset JSON wholesale — the shape the service adapter calls with.

## What it renders / fidelity

End-to-end std renders: 3-pass `.osu` parse and combo numbering, difficulty/timing math (float32-quirk-exact ports), stacking (old + v6 algorithms), slider paths (bit-exact `PathApproximator` port), and a judgment simulation ported from ppy/osu (stable `LegacyHitPolicy` and lazer `StartTimeOrderedHitPolicy`, auto-selected by the `.osr` game version) whose counts are **reconciled to the replay's authoritative totals**, with the pre-reconcile sim-vs-real delta printed as an honesty metric.

On top of gameplay: distance-field slider bodies (with snaking and `SliderMerge` union), spinners, hitsounds mixed offline (no BASS), fail/death handling, and the full R3D website settings surface — background dim/blur/parallax/triangles/video/flash-to-beat, bloom, seizure card, lead-in, nightcore beat overlay, cursor trail/rainbow/ripples, and HUD extras (live rosu-pp counter, hit counter, aim-error scatter, strain graph, warning arrows). Results screens: a ported **lazer** ranking screen (default) or the in-house **r3d** card.

**Fidelity notes:**
- HUD/skin: per-element hybrid — the skin's **legacy** component where the skin ships it, procedural **Argon** otherwise (`--legacy-defaults` forces the all-legacy look). Argon-style default textures and combo digits are **procedurally baked in-repo**.
- No osu! game assets are bundled (osu!'s skins/audio/art are CC BY-NC and excluded); only original or procedural art ships. The one bundled font is Nunito (SIL OFL, with `OFL.txt`), used as a free stand-in for lazer's non-commercial Torus.
- Star rating and pp come from `rosu-pp-py`; `--pp` / `--sr` pin the displayed values to osu!'s official figures while the live pp curve keeps its rosu shape.
- Scores are displayed as lazer-standardised ScoreV3 (raw ScoreV1 stable headers are re-derived from the sim). Accuracy uses lazer's tick/tail-inclusive value for lazer replays, the header value for stable.

## Usage

Invoked as a module (the adapter contract mirrors the catch renderer):

```bash
python -m osu_std_renderer REPLAY.osr BEATMAP_DIR -o out.mp4 \
    [--resolution 1920x1080] [--fps 60] [--encoder auto] [--skin DIR] …
```

Positionals: `osr` (replay `.osr`, omit with `--no-replay`) and `beatmap` (a directory holding the `.osu` + audio + background, or a direct `.osu` path — when a dir, the `.osu` whose md5 matches the replay is selected).

Common flags (all verified in `cli.py`; boolean flags take a `--no-` counterpart):

| Flag | Default | Purpose |
|------|---------|---------|
| `-o, --output` | — | output `.mp4` path |
| `--resolution WxH` | `1920x1080` | frame size |
| `--fps N` | `60` | frame rate |
| `--encoder` | `auto` | `auto \| h264_nvenc \| h264_vaapi \| libx264` |
| `--skin DIR` | — | extracted skin dir (real gameplay/HUD textures, per-element fallback) |
| `--default-skin DIR` | — | fallback skin dir |
| `--results` / `--results-style` | on / `lazer` | outro screen; `lazer` or `r3d` |
| `--leaderboard` / `--leaderboard-source` | on / `r3d` | results leaderboard flank cards; `r3d` (local render DB) or `osu` (needs `--leaderboard-json`) |
| `--pp N` / `--sr N` | — | pin displayed official pp / star rating |
| `--pp-counter`, `--strain-graph`, `--aim-error-meter`, `--hit-counter` | off | HUD extras |
| `--storyboard`, `--video`, `--bloom`, `--bg-blur`, `--bg-parallax`, `--flash-to-beat`, `--seizure-warning`, `--logo` | off | background / effects |
| `--legacy-defaults`, `--renderer-default-font-and-ranks` | off | HUD component family overrides |
| `--no-replay` | — | perfect-play debug render (no cursor/HUD/judgments/hitsounds) |

Debug helpers:

```bash
# parse map + replay + skin, print a summary, exit
python -m osu_std_renderer REPLAY.osr BEATMAP_DIR --parse-only

# render a clip (seconds into the map)
python -m osu_std_renderer REPLAY.osr BEATMAP_DIR -o clip.mp4 --start 35 --max-seconds 45

# write single PNG frames at map times (ms) instead of encoding
python -m osu_std_renderer REPLAY.osr BEATMAP_DIR --dump-frames "12000,34000"
```

Standalone tools:

```bash
python -m osu_std_renderer.tools.parse_summary MAP.osu [REPLAY.osr]   # end-to-end parse verification
python -m osu_std_renderer.tools.plot_map MAP.osu out.png             # CPU debug plot
python -m osu_std_renderer.merge …                                    # showdown!mrgd: N replays on one shared field (verify args)
```

Run the tests: `python -m tests.run_all`.

## Requirements

- **Python 3.12** (stack is version-locked; verify if targeting another version)
- **moderngl** ≥ 5.10 — standalone **headless EGL** context (no X server / GLFW)
- **numpy**
- **Pillow** (PIL)
- **osrparse** — replay decode
- **rosu-pp-py** — star rating / pp (renders fail-soft, hiding the pp counter, if absent)
- **ffmpeg** on `PATH` — raw-RGB frames are piped to a single ffmpeg process for encode + audio mux; encoder auto-detects `h264_nvenc → h264_vaapi → libx264`

There is **no `requirements.txt` / `pyproject.toml`** in the repo (verify — deps are installed from the locked stack above; the old README used `pip install numpy pillow osrparse moderngl`). Audio is mixed offline via numpy + ffmpeg — **no BASS**.

## Layout

```
osu_std_renderer/
├── __main__.py         module entry → cli.main()
├── cli.py              argparse CLI + the full render orchestration (the adapter contract)
├── settings.py         StdRenderSettings + from_preset() (R3D preset keys → flags)
├── merge.py            showdown!mrgd: N std replays on one shared field
├── timewarp.py         WU/WD (Wind Up/Down) rate-ramp wall↔map time warp
├── beatmap/            .osu parse, difficulty, timing, stacking, breaks, storyboard
│   └── objects/        circles / sliders / spinners / timing points
├── curves/             sliderpath.py (bit-exact ppy geometry port) + legacy_random.py
├── replay/             .osr decode, frame interpolation, relax synthesis, fail detection
├── ruleset/            judgment sim (ruleset.py) + HP drain (health.py)
├── skin/               skin.ini parsing + file resolution (skin→fallback→local)
├── render/             GL context, playfield, scene, slider bodies, HUD, results,
│                       background/video, effects/bloom, pp, leaderboard, spinners…
├── record/             pipeline (fixed-timestep loop), encode (ffmpeg pipe), audio, hitsounds
├── tools/              parse_summary.py, plot_map.py (CPU debug)
├── assets/             bundled fonts (Nunito, OFL) + default nightcore drums
└── argon_assets/       procedural Argon HUD asset support
docs/
├── RENDER_PLAN.md      behavioral spec — architectural teardown of the reference renderer
└── APPROACH.md         strategy
tests/                  ~45 pytest modules (run: python -m tests.run_all)
```

The prod branch is `std-leaderboard-watermark`.

## License

**AGPL-3.0** — Copyright (C) 2026 Cool Adults (full text in `LICENSE`, summary in `COPYRIGHT`).

This renderer re-implements osu! gameplay/timing/judgment/scoring/HUD behaviour by **porting logic** (not code) from the MIT-licensed [ppy/osu](https://github.com/ppy/osu) and [ppy/osu-framework](https://github.com/ppy/osu-framework); ports are cited per-module in docstrings. `curves/sliderpath.py`, `curves/legacy_random.py`, and much of `ruleset/`, `render/hud.py`, `render/lazer_results.py`, and `ruleset/health.py` carry specific ppy port attributions in their headers. Star rating / pp use `rosu-pp-py` (MIT, MaxOhn). `docs/RENDER_PLAN.md` documents the GPL-3.0 danser-go reference architecture. No ppy game assets are bundled. Nunito font: SIL OFL 1.1 (see `assets/fonts/OFL.txt`).

osu! is a rhythm game by peppy / ppy Pty Ltd. This is an independent, unofficial renderer, not affiliated with or endorsed by ppy.
