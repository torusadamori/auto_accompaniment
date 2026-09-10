#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"
unoq_load_config
cd -- "$UNOQ_REPO_ROOT"

printf 'UNOQ update: current commit %s\n' "$(git rev-parse --verify HEAD)"
if [[ -n "$(git status --porcelain)" ]]; then
    unoq_die "working tree is dirty; commit or preserve changes first (no stash/reset performed)"
fi
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)" || \
    unoq_die "current branch has no upstream"
remote="${upstream%%/*}"
printf 'UNOQ update: fetching %s\n' "$remote"
git fetch "$remote"
git pull --ff-only
printf 'UNOQ update: PASS: current commit %s\n' "$(git rev-parse --verify HEAD)"
