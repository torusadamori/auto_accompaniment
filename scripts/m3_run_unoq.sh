#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
venv_python="${M3_VENV:-$HOME/blemidi}/bin/python"
if [[ ! -x "$venv_python" ]]; then
    printf 'M3: Python not found: %s\nUse the existing ~/blemidi venv, or set M3_VENV.\n' "$venv_python" >&2
    exit 1
fi
cd -- "$repo_root"
exec "$venv_python" -B -m m3.launcher "$@"
