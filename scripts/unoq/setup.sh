#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
unoq_load_config
cd -- "$UNOQ_REPO_ROOT"

printf 'UNOQ setup: repo=%s\nUNOQ setup: config=%s\n' "$UNOQ_REPO_ROOT" "$UNOQ_CONFIG_FILE"
command -v "$UNOQ_PYTHON" >/dev/null 2>&1 || unoq_die "Python command not found: $UNOQ_PYTHON"

if [[ -e "$UNOQ_VENV_DIR" && ! -f "$UNOQ_VENV_DIR/pyvenv.cfg" ]]; then
    unoq_die "venv path exists but is not a Python venv; preserved: $UNOQ_VENV_DIR"
fi
if [[ ! -f "$UNOQ_VENV_DIR/pyvenv.cfg" ]]; then
    printf 'UNOQ setup: creating venv %s\n' "$UNOQ_VENV_DIR"
    "$UNOQ_PYTHON" -m venv "$UNOQ_VENV_DIR" || unoq_die \
        "venv creation failed (install the distro python3-venv package, then retry)"
else
    printf 'UNOQ setup: venv already exists %s\n' "$UNOQ_VENV_DIR"
fi
unoq_require_venv
[[ -f "$UNOQ_REQUIREMENTS_PATH" ]] || unoq_die "requirements file missing: $UNOQ_REQUIREMENTS_PATH"
"$UNOQ_VENV_PYTHON" -m pip install --disable-pip-version-check -r "$UNOQ_REQUIREMENTS_PATH"

mkdir -p -- "$UNOQ_LOG_DIR"
printf 'UNOQ setup: dependency and runtime directories are ready\n'

if command -v bluetoothctl >/dev/null 2>&1; then
    printf 'UNOQ setup: Bluetooth/BlueZ command found\n'
    bluetoothctl show 2>/dev/null | grep -q 'Powered: yes' || \
        printf 'UNOQ setup: WARN: no powered default Bluetooth adapter detected\n' >&2
else
    printf 'UNOQ setup: WARN: bluetoothctl not found; check the UNO Q BlueZ installation\n' >&2
fi
if command -v arduino-app-cli >/dev/null 2>&1; then
    printf 'UNOQ setup: App CLI found: '
    arduino-app-cli version 2>/dev/null || arduino-app-cli --version 2>/dev/null || printf 'version unavailable\n'
else
    if [[ "${UNOQ_APP_START_MODE:-cli}" == cli ]]; then
        unoq_die "arduino-app-cli not found; update the official UNO Q/App Lab environment, or explicitly configure gui mode"
    fi
    printf 'UNOQ setup: WARN: arduino-app-cli not found; gui Run mode is configured\n' >&2
fi
if [[ -f "$UNOQ_APP_DIR/app.yaml" ]]; then
    printf 'UNOQ setup: App Lab target found: %s\n' "$UNOQ_APP_DIR"
else
    unoq_die "App Lab target is not initialized: $UNOQ_APP_DIR/app.yaml. Create/copy one proven Bridge app once, set UNOQ_APP_DIR, then rerun setup."
fi

printf 'UNOQ setup: PASS. Next: ./scripts/unoq/run.sh\n'
