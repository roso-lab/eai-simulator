#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT/source/EAI${PYTHONPATH:+:$PYTHONPATH}" \
  python -m EAI.hmrs_env.env_diy.update_assets "$@"
