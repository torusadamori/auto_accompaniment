#!/usr/bin/env bash

# Shared helpers. Entry-point scripts set strict mode before sourcing this file.
UNOQ_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
UNOQ_CONFIG_FILE="${UNOQ_CONFIG_FILE:-$UNOQ_REPO_ROOT/config/unoq.env}"

unoq_die() {
    printf 'UNOQ: FAIL: %s\n' "$*" >&2
    return 1
}

unoq_load_config() {
    if [[ ! -f "$UNOQ_CONFIG_FILE" ]]; then
        unoq_die "configuration not found: $UNOQ_CONFIG_FILE"
        return
    fi
    # The tracked file is shell syntax by design. Override it by setting
    # UNOQ_CONFIG_FILE to another trusted file.
    # shellcheck source=../../config/unoq.env
    source "$UNOQ_CONFIG_FILE"
    : "${UNOQ_PYTHON:?UNOQ_PYTHON is required}"
    : "${UNOQ_VENV_DIR:?UNOQ_VENV_DIR is required}"
    : "${UNOQ_REQUIREMENTS:?UNOQ_REQUIREMENTS is required}"
    : "${UNOQ_RECEIVER_MODULE:?UNOQ_RECEIVER_MODULE is required}"
    : "${UNOQ_APP_DIR:?UNOQ_APP_DIR is required}"
    : "${UNOQ_APP_SOURCE_DIR:?UNOQ_APP_SOURCE_DIR is required}"
    : "${UNOQ_DEPLOY_FILES:?UNOQ_DEPLOY_FILES is required}"
    : "${UNOQ_RELAY_SOCKET:?UNOQ_RELAY_SOCKET is required}"
    : "${UNOQ_MIDI_PORT_PATTERN:?UNOQ_MIDI_PORT_PATTERN is required}"
    : "${UNOQ_MIDI_RETRY_SECONDS:?UNOQ_MIDI_RETRY_SECONDS is required}"
    UNOQ_VENV_PYTHON="$UNOQ_VENV_DIR/bin/python"
    UNOQ_REQUIREMENTS_PATH="$UNOQ_REPO_ROOT/$UNOQ_REQUIREMENTS"
    UNOQ_APP_SOURCE_PATH="$UNOQ_REPO_ROOT/$UNOQ_APP_SOURCE_DIR"
    UNOQ_LOG_DIR="$UNOQ_REPO_ROOT/logs/unoq"
    [[ "${UNOQ_WAIT_SECONDS:-120}" =~ ^[0-9]+$ ]] || \
        unoq_die "UNOQ_WAIT_SECONDS must be a nonnegative integer"
}

unoq_require_venv() {
    [[ -f "$UNOQ_VENV_DIR/pyvenv.cfg" && -x "$UNOQ_VENV_PYTHON" ]] || {
        unoq_die "venv is missing or incomplete: $UNOQ_VENV_DIR (run ./scripts/unoq/setup.sh)"
        return
    }
}
