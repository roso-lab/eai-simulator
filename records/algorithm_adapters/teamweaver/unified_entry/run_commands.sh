#!/usr/bin/env bash
set -eo pipefail

ROOT=/home/airs/hetrocoord
SOURCE=/home/airs/.cache/heterocoord/upstream/teamweaver

# The shell supplies Dongli values; the key is never printed or passed as an
# argument. The full semantic command is expected to exit 1 while the frozen
# upstream planner has the recorded syntax error.
cd "$ROOT"
source "$HOME/.bashrc"
set -u
python3 -m tools.algorithm_adapters.run \
  --algorithm teamweaver \
  --task records/algorithm_adapters/teamweaver/unified_entry/teamweaver_semantic_task.json \
  --config records/algorithm_adapters/teamweaver/unified_entry/teamweaver_full_semantic_config.json \
  --source-root "$SOURCE" \
  --report records/algorithm_adapters/teamweaver/unified_entry/full_semantic_report.json || test "$?" -eq 1

python3 -m tools.algorithm_adapters.run \
  --algorithm teamweaver \
  --task records/algorithm_adapters/teamweaver/unified_entry/teamweaver_semantic_task.json \
  --config records/algorithm_adapters/teamweaver/unified_entry/teamweaver_allocator_config.json \
  --source-root "$SOURCE" \
  --report records/algorithm_adapters/teamweaver/unified_entry/allocator_report.json
