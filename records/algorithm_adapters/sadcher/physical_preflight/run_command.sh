#!/usr/bin/env bash
set -euo pipefail

# PREPARED_ONLY: do not run automatically. This command is expected to stop
# after native_decision_from_report because SADCHER returns ur5_1 -> idle.
cd /home/airs/hetrocoord
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 \
/home/airs/miniconda3/envs/env_isaaclab/bin/python \
  tools/algorithm_adapters/isaac_pick_place.py \
  --algorithm sadcher \
  --algorithm-python /home/airs/.cache/heterocoord/envs/sadcher/bin/python \
  --source-root /tmp/heterocoord-algorithms-20261028/sadcher \
  --task /home/airs/hetrocoord/records/algorithm_adapters/sadcher/physical_preflight/task_host_aligned.json \
  --config /home/airs/hetrocoord/records/algorithm_adapters/sadcher/physical_preflight/adapter_config.json \
  --output /home/airs/hetrocoord/records/algorithm_adapters/sadcher/physical_preflight/future_isaac_run
