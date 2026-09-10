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
    --address "$UNOQ_BLE_ADDRESS" \
    --service-uuid "$UNOQ_BLE_MIDI_SERVICE_UUID" \
    --characteristic-uuid "$UNOQ_BLE_MIDI_CHARACTERISTIC_UUID" \
    --ble-timeout "${UNOQ_BLE_PROBE_SECONDS:-10}" \
    --notify-probe-seconds "${UNOQ_BLE_NOTIFY_PROBE_SECONDS:-1}" \
    "$@"
