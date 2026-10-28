#!/usr/bin/env bash
# Prepared only; NOT executed in this preflight. The user schedules Isaac runs.
# Re-runs the original provider once. A rejection stops before skill execution.
set -euo pipefail
case_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
output_dir="${1:?Supply a NEW absolute output directory for this scheduled run}"
[[ "$output_dir" = /* ]] || { echo 'Output directory must be absolute' >&2; exit 2; }
[[ ! -e "$output_dir" ]] || { echo 'Refusing to overwrite an existing run' >&2; exit 2; }
mkdir -p -- "$output_dir"
cd /home/airs/hetrocoord
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=/home/airs/hetrocoord
exec /home/airs/miniconda3/envs/env_isaaclab/bin/python -B \
  tools/algorithm_adapters/isaac_pick_place.py \
  --algorithm fmc_ata \
  --algorithm-python /home/airs/.cache/heterocoord/envs/fmc-ata/bin/python \
  --source-root /tmp/heterocoord-algorithms-20261028/fmc-ata \
  --task "$case_dir/task.json" \
  --config "$case_dir/adapter_config.json" \
  --output "$output_dir/host" >"$output_dir/host_process.log" 2>&1
