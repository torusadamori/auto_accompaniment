#!/usr/bin/env bash
set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
unoq_load_config || exit 1
cd -- "$UNOQ_REPO_ROOT" || exit 1

do_update="${UNOQ_UPDATE_ON_RUN:-0}"
dry_run=0
sync_existing=0
raw=0
while (($#)); do
    case "$1" in
        --update) do_update=1 ;;
        --dry-run) dry_run=1 ;;
        --sync-existing) sync_existing=1 ;;
        --raw) raw=1 ;;
        -h|--help)
            printf 'Usage: %s [--update] [--dry-run] [--sync-existing] [--raw]\n' "$0"
            exit 0
            ;;
        *) unoq_die "unknown argument: $1"; exit 2 ;;
    esac
    shift
done

mkdir -p -- "$UNOQ_LOG_DIR" || exit 1
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="$UNOQ_LOG_DIR/$stamp-$$.log"
latest_tmp="$UNOQ_LOG_DIR/.latest.$$"
ln -s -- "$(basename -- "$log_file")" "$latest_tmp" && mv -f -- "$latest_tmp" "$UNOQ_LOG_DIR/latest.log"
start_seconds="${SECONDS:-0}"

run_workflow() {
    printf 'UNOQ run: repo=%s\nUNOQ run: commit=%s\nUNOQ run: log=%s\n' \
        "$UNOQ_REPO_ROOT" "$(git rev-parse --short HEAD)" "$log_file"
    unoq_require_venv || return
    "$UNOQ_VENV_PYTHON" -c \
        'import sys, bleak; assert sys.version_info >= (3, 11); print("UNOQ run: Python/Bleak dependency check PASS")' || {
        unoq_die "Python 3.11+ and Bleak are required; run setup.sh"
        return
    }
    if [[ "$do_update" == 1 ]]; then
        if [[ "$dry_run" == 1 ]]; then
            printf 'UNOQ run: dry-run: would call update.sh\n'
        else
            "$UNOQ_REPO_ROOT/scripts/unoq/update.sh" || return
        fi
    fi
    deploy_args=(--stop-running)
    [[ "$dry_run" == 1 ]] && deploy_args+=(--dry-run)
    [[ "$sync_existing" == 1 ]] && deploy_args+=(--sync-existing)
    "$UNOQ_REPO_ROOT/scripts/unoq/deploy.sh" "${deploy_args[@]}" || return
    if [[ "$dry_run" == 1 ]]; then
        printf 'UNOQ run: dry-run: would ensure App, wait up to %ss, then start BLE receiver\n' \
            "${UNOQ_WAIT_SECONDS:-120}"
        return 0
    fi
    if [[ ( -e "$UNOQ_RELAY_SOCKET" || -L "$UNOQ_RELAY_SOCKET" ) && ! -S "$UNOQ_RELAY_SOCKET" ]]; then
        unoq_die "relay path is not a Unix socket and was preserved: $UNOQ_RELAY_SOCKET"
        return
    fi
    if [[ ! -S "$UNOQ_RELAY_SOCKET" ]]; then
        case "${UNOQ_APP_START_MODE:-cli}" in
            cli)
                command -v arduino-app-cli >/dev/null 2>&1 || {
                    unoq_die "arduino-app-cli is unavailable. Update the official UNO Q/App Lab environment, or set UNOQ_APP_START_MODE=gui and press App Lab Run once."
                    return
                }
                printf 'UNOQ run: starting App through official arduino-app-cli\n'
                arduino-app-cli app start "$UNOQ_APP_DIR" || return
                ;;
            gui)
                printf 'UNOQ run: App Labで対象AppのRunを1回押してください。\n'
                ;;
            *) unoq_die "UNOQ_APP_START_MODE must be cli or gui"; return ;;
        esac
    fi
    deadline=$((SECONDS + ${UNOQ_WAIT_SECONDS:-120}))
    while [[ ! -S "$UNOQ_RELAY_SOCKET" ]]; do
        if ((SECONDS >= deadline)); then
            unoq_die "relay socket did not appear within ${UNOQ_WAIT_SECONDS:-120}s: $UNOQ_RELAY_SOCKET"
            return
        fi
        sleep 1
    done
    [[ -r "$UNOQ_RELAY_SOCKET" && -w "$UNOQ_RELAY_SOCKET" ]] || {
        unoq_die "relay socket is not accessible by this user: $UNOQ_RELAY_SOCKET"
        return
    }
    receiver_args=(--address "$UNOQ_BLE_ADDRESS" --socket "$UNOQ_RELAY_SOCKET")
    [[ "$raw" == 1 ]] && receiver_args+=(--raw)
    export UNOQ_BLE_MIDI_SERVICE_UUID UNOQ_BLE_MIDI_CHARACTERISTIC_UUID
    printf 'UNOQ run: starting BLE receiver for %s (Ctrl+C to stop)\n' "$UNOQ_BLE_ADDRESS"
    "$UNOQ_VENV_PYTHON" -B -m "$UNOQ_RECEIVER_MODULE" "${receiver_args[@]}"
}

run_workflow 2>&1 | tee -a "$log_file"
result=${PIPESTATUS[0]}
elapsed=$((SECONDS - start_seconds))
if ((result == 0)); then
    summary="UNOQ run: PASS (${elapsed}s) log=$log_file"
else
    summary="UNOQ run: FAIL exit=$result (${elapsed}s) log=$log_file"
fi
printf '%s\n' "$summary" | tee -a "$log_file"
exit "$result"
