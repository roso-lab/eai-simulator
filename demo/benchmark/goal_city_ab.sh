#!/usr/bin/env bash
# City goal-episode A/B matrix batch runner.
#
# usage: bash demo/benchmark/goal_city_ab.sh <output-base> <instances-per-algo> [--pool v1|v2] [algo ...]
# algos: witness teamweaver teamweaver_llm emos emos_llm (default: all but *_llm)
# Each run is one formal city episode (~140-160 s on CPU); runs execute
# sequentially per the Isaac single-worker discipline on this machine.
set -euo pipefail
cd "$(dirname "$0")/../../"

BASE="${1:?output base required}"; shift
N="${1:?instances per algorithm required}"; shift
POOL="v1"
while [ "${1:-}" = "--pool" ]; do POOL="$2"; shift 2; done
ALGOS=("$@")
[ ${#ALGOS[@]} -eq 0 ] && ALGOS=(witness teamweaver emos)

PY=/home/pupil/miniconda3/envs/env_isaaclab/bin/python
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs

mkdir -p "$BASE"
for algo in "${ALGOS[@]}"; do
  for i in $(seq 1 "$N"); do
    out="$BASE/${algo}-run${i}"
    echo "== [$(date +%H:%M:%S)] $algo #$i -> $out"
    start=$(date +%s)
    "$PY" -m demo.benchmark.goal_city_episode --output-root "$out" \
          --device cpu --pool "$POOL" --algorithm "$algo" > "$out.log" 2>&1 \
      || echo "   run failed (rc=$?), see $out.log"
    echo "   wall: $(( $(date +%s) - start )) s"
  done
done

"$PY" - "$BASE" <<'PY'
import json, sys, time
from pathlib import Path
base = Path(sys.argv[1])
rows = []
for run in sorted(p for p in base.iterdir() if p.is_dir()):
    try:
        result = json.loads((run / "goal_episode" / "result.json").read_text())
        business = json.loads((run / "goal_episode" / "business_evidence.json").read_text())
        probe = json.loads((run / "service_probe_report.json").read_text())
        ge = (probe.get("trace") or {}).get("goal_episode", {})
        rows.append({
            "run": run.name, "episode_id": business.get("episode_id"),
            "participant": ge.get("participant"),
            "error": result.get("error"),
            "outcomes": {k: v["status"] for k, v in sorted((business.get("outcomes") or {}).items())},
            "validated_count": sum(1 for v in (business.get("outcomes") or {}).values()
                                   if v["status"] == "validated"),
            "probe_status": probe.get("status"),
            "sim_time_s": probe.get("episode_time_s"),
        })
    except Exception as exc:
        rows.append({"run": run.name, "error_reading": str(exc)[:120]})
summary = {"schema": "eai.benchmark.goal_city_ab_summary", "version": 1,
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": rows}
(base / "ab_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(f"{'run':22s} {'participant':26s} {'4/4':4s} error")
for row in rows:
    print(f"{row['run']:22s} {str(row.get('participant')):26s} "
          f"{row.get('validated_count', '?')}/4  {str(row.get('error'))[:60]}")
print(f"\nsummary -> {base / 'ab_summary.json'}")
PY
