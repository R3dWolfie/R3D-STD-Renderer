# Local standard render dashboard

The dashboard is adapted from the mania renderer's `tools/local_render_web.py`.
It uses Python's standard library for the server and the standard renderer's
existing Python dependencies for replay parsing and rendering. No frontend
build or additional server package is needed.

From this checkout:

```bash
./tools/start-local-render-web.sh
```

The launcher uses the installed `uv` tool to create a persistent, ignored
`.dashboard-venv/` with Python 3.12 and the verified dependencies listed in
`tools/local-render-requirements.txt`. Later launches check those dependencies
and reuse the same environment. It leaves the existing `.venv/` untouched.
All startup options below can also be passed to the launcher.

After the first setup, `python tools/local_render_web.py` automatically uses
the prepared environment if the current Python lacks renderer dependencies.
This keeps fixture parsing and rendering in the same environment. If neither
the current Python nor a local environment is usable, startup stops with the
launcher command instead of displaying every replay as invalid. `--help` works
without renderer dependencies. The startup banner shows the Python executable.

Open `http://127.0.0.1:7377` on this computer or the printed LAN URL on a phone
or tablet. The default binding is `0.0.0.0:7377`; use `--host 127.0.0.1` for
desktop-only access. The dashboard has no authentication and is for a trusted
local network. Ctrl+C stops the server and cancels any active render.

The default source folder is `<this-repo>/test-render`, resolved independently
of the shell's working directory. It discovers `.osr`, `.osu`, and extracted
skins containing `skin.ini` recursively. The current fixtures include
`test-render/test.osr`, `test-render/beatmap/`, and `test-render/skin/`.
Only osu!standard (mode 0) replays and maps can render. The newest valid replay
is selected initially, and an exact replay MD5 match selects its difficulty.
The selected `.osu` file is passed directly to `python -m osu_std_renderer`,
so manual difficulty overrides are respected. Select a skin, then click
**Render**. One render runs at a time.

Resolution, FPS, and encoder choices match the original bench. **Advanced**
offers skin hitsounds, legacy defaults, renderer fonts/ranks, a hit counter,
song progress and optional start/stop map times in seconds. **Show hit counter**
starts unchecked; checking it passes the existing `--hit-counter` renderer flag.
**Show song progress (Legacy pie / Argon progress)** also starts unchecked.
Checking it passes `--show-progress`, showing the existing circular progress
beside accuracy in a Legacy skin, or the corresponding Argon progress component
when Argon is selected. Unchecked or omitted sends no progress flag, retaining
the renderer's default OFF. This control does not load saved skin layout JSON.
For a short clip, enter start `35` and stop `40`; leave them blank for the
normal render. Stop is an absolute map time,
passed as `--max-seconds`, rather than a duration. The `auto` encoder uses
the standard renderer's startup probes: it tries NVIDIA, then VAAPI when a
render device is available, then software. An encoder must successfully
encode a test frame before it is selected. Explicit encoder choices remain
explicit; use `libx264` to select software encoding directly. Encoder failures
include ffmpeg's diagnostic in the render log.

Progress and logs update live. **Cancel** stops the renderer and its ffmpeg
process group. Finished videos play inline with seeking; recent renders can
be played or deleted. Browser preferences use a separate standard-renderer
storage key. **Rescan fixtures** discovers added files and refreshes the
renderer branch and commit shown above the controls.

Outputs go to the ignored `<this-repo>/local-web-renders/` directory, separate
from source fixtures. Ten recent outputs are retained by default; automatic
pruning only deletes older dashboard-generated filenames. Explicit deletion
asks for confirmation and may remove any finished MP4 in the output folder.

Startup options can override the fixture roots, extracted skin roots,
output folder, port, and retained history:

```bash
python tools/local_render_web.py --root /path/to/std-fixtures \
  --skin-root /path/to/skins --output-dir /tmp/std-renders --history 5 --port 7380
```

Use `--renderer-repo /path/to/another/std-checkout` to execute another standard
checkout. It must contain `osu_std_renderer/cli.py` and `osu_std_renderer/__main__.py`;
the tool prefers its executable `.venv/bin/python`, otherwise the current
Python interpreter. The subprocess working directory is that checkout.
The default fixture roots and output folder still belong to this dashboard's
repo. Omitting the option executes this repo with the current interpreter.

Run the dashboard tests with:

```bash
python -m pytest -q tests/test_local_render_web.py
```
