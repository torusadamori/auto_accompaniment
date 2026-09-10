#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
unoq_load_config
cd -- "$UNOQ_REPO_ROOT"

unoq_require_venv
exec "$UNOQ_VENV_PYTHON" -B -m unoq.doctor \
    --venv "$UNOQ_VENV_DIR" \
    --app-dir "$UNOQ_APP_DIR" \
    --socket "$UNOQ_RELAY_SOCKET" \
    --port-pattern "$UNOQ_MIDI_PORT_PATTERN" \
    --midi-probe-seconds "${UNOQ_MIDI_PROBE_SECONDS:-1}" \
    "$@"
