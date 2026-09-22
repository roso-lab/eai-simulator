#!/usr/bin/env bash
# Restaurant algorithm-episode A/B batch runner.
#
# usage: bash demo/benchmark/goal_restaurant_ab.sh <output-base> <instances-per-algo> [algo ...]
# algos: teamweaver emos (default: both)
# Each run is one formal algorithm episode (~2 min on CPU) with inline
# archive verification; runs execute sequentially per the Isaac
# single-worker discipline on this machine.
set -euo pipefail
cd "$(dirname "$0")/../../"

BASE="${1:?output base required}"; shift
N="${1:?instances per algorithm required}"; shift
ALGOS=("$@")
[ ${#ALGOS[@]} -eq 0 ] && ALGOS=(teamweaver emos)

PY="${PYTHON_ISAAC:-$HOME/miniconda3/envs/env_isaaclab/bin/python}"
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs
export EAI_USD_ROOT="${EAI_USD_ROOT:-/home/pupil/eai-simulator/usd}"
export EAI_CONTROLLER_ROOT="${EAI_CONTROLLER_ROOT:-/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller}"
PRODUCTS="${RESTAURANT_PRODUCTS:-tmp/restaurant_benchmark/products-candidate-v7}"
SOURCE="${EAI_SOURCE_USD:-/home/pupil/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda}"

mkdir -p "$BASE"
for algo in "${ALGOS[@]}"; do
  for i in $(seq 1 "$N"); do
    out="$BASE/${algo}-run${i}"
    echo "== [$(date +%H:%M:%S)] $algo #$i -> $out"
    start=$(date +%s)
    "$PY" -m demo.benchmark.goal_restaurant_algorithm_episode \
          --output-root "$out" --device cpu --algorithm "$algo" \
          --products-root "$PRODUCTS" --source-usd "$SOURCE" > "$out.log" 2>&1 \
      || echo "   run failed (rc=$?), see $out.log"
    echo "   wall: $(( $(date +%s) - start )) s"
  done
done

"$PY" - "$BASE" <<'PY'
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
rows = []
for run in sorted(p for p in base.iterdir() if p.is_dir()):
    try:
        report = json.loads(
            (run / "restaurant_algorithm_episode_report.json").read_text())
        result = report.get("goal_episode_result") or {}
        allocation = None
        team = None
        for row in report.get("participant_trace") or []:
            if row.get("event") == "allocation_final":
                allocation = row["assignment"]
                team = row["team"]
        process = json.loads((run / "process.json").read_text())
        verification = json.loads((run / "archive_verification.json").read_text())
        rows.append({
            "run": run.name,
            "algorithm": report.get("algorithm"),
            "episode_id": report.get("episode_id"),
            "validated": report.get("business_episode_execution_validated"),
            "error": result.get("error"),
            "receipts": result.get("skill_receipt_count"),
            "allocation": allocation,
            "team": team,
            "wall_s": round(process.get("duration_s", -1), 1),
            "archive_verified": verification.get("validated"),
        })
    except Exception as exc:
        rows.append({"run": run.name, "read_error": str(exc)[:120]})
summary = {
    "schema": "eai.restaurant.algorithm_ab_batch", "version": 1,
    "base": str(base), "runs": rows,
}
for row in rows:
    print(json.dumps(row, ensure_ascii=False, sort_keys=True))
(base / "batch_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8")
ok = sum(1 for row in rows if row.get("validated") and row.get("archive_verified"))
print(f"batch: {ok}/{len(rows)} runs validated and archive-verified")
PY
