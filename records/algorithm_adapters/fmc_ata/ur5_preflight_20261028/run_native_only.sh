#!/usr/bin/env bash
# CPU-only native decision through the shared integrity-checked factory/CLI.
set -euo pipefail
case_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${1:?Supply a NEW absolute output directory}"
[[ "$output_dir" = /* ]] || { echo 'Output directory must be absolute' >&2; exit 2; }
[[ ! -e "$output_dir" ]] || { echo 'Refusing to overwrite an existing run' >&2; exit 2; }
mkdir -p -- "$output_dir"
cd /home/airs/hetrocoord
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/home/airs/hetrocoord
exec /home/airs/.cache/heterocoord/envs/fmc-ata/bin/python -B \
  -m tools.algorithm_adapters.run \
  --algorithm fmc_ata \
  --source-root /tmp/heterocoord-algorithms-20261028/fmc-ata \
  --task "$case_dir/task.json" \
  --config "$case_dir/adapter_config.json" \
  --report "$output_dir/native_decision.json" >"$output_dir/native_process.log" 2>&1
