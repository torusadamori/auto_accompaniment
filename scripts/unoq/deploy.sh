#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
unoq_load_config
cd -- "$UNOQ_REPO_ROOT"

unoq_require_venv
args=(
    --app-dir "$UNOQ_APP_DIR"
    --source-dir "$UNOQ_APP_SOURCE_PATH"
    --socket "$UNOQ_RELAY_SOCKET"
)
read -r -a deploy_files <<< "$UNOQ_DEPLOY_FILES"
for relative in "${deploy_files[@]}"; do
    args+=(--file "$relative")
done
exec "$UNOQ_VENV_PYTHON" -B -m unoq.deploy "${args[@]}" "$@"
