#!/usr/bin/env bash
# Prepare a persistent Linux/macOS runtime without changing an existing .venv.
set -euo pipefail

dashboard_repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dashboard_env="$dashboard_repo/.dashboard-venv"

if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'uv is required to prepare the dashboard Python environment.' >&2
    exit 1
fi
if [[ ! -x "$dashboard_env/bin/python" ]]; then
    uv venv --python 3.12 "$dashboard_env"
fi
uv pip install --link-mode copy --python "$dashboard_env/bin/python" \
    -r "$dashboard_repo/tools/local-render-requirements.txt"
exec "$dashboard_env/bin/python" "$dashboard_repo/tools/local_render_web.py" "$@"
