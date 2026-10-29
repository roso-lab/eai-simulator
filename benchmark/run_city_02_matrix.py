"""Generate the reproducible Isaac-free city_02 diagnostic matrix."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmark.city_02_algorithms import EmosCity02Algorithm, TeamWeaverCity02Algorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.scenarios.city_02 import City02ScenarioAdapter


ALGORITHMS = {"emos": EmosCity02Algorithm, "teamweaver": TeamWeaverCity02Algorithm}
COMMUNICATIONS = ("C0", "C1", "C2", "C3")


def run_matrix(*, repeats: int = 1, output: Path | None = None) -> dict[str, Any]:
    adapter = City02ScenarioAdapter()
    rows: list[dict[str, Any]] = []
    for algorithm_id, algorithm_cls in ALGORITHMS.items():
        for tier in range(1, 5):
            for communication in COMMUNICATIONS:
                for repeat in range(1, repeats + 1):
                    episode = replace(adapter.build_episode(tier=tier, communication=communication, seed=repeat - 1), seed=repeat - 1)
                    runtime = DiagnosticRuntime(episode, algorithm=algorithm_cls())
                    checkpoint = runtime.checkpoint(metadata={"schema": runtime.schema_version, "tick": "0"})
                    result = runtime.run()
                    replay = runtime.replay(checkpoint)
                    diagnostic = adapter.diagnostic_metrics(result)
                    rows.append({
                        "algorithm": algorithm_id, "algorithm_scope": "deterministic_contract_adapter",
                        "model_provider": "dongli", "model": "gpt-5.6-luna", "reasoning_effort": "low",
                        "scene": "city_02", "tier": tier, "condition": communication,
                        "repeat": repeat, "success": bool(result["success"]), "failure_reason": result["failure_reason"],
                        "makespan_s": result["makespan_s"], "task_completion_rate": result["task_completion_rate"],
                        "patrol_ground_fraction": diagnostic["patrol_ground_fraction"],
                        "patrol_aerial_fraction": diagnostic["patrol_aerial_fraction"],
                        "xy_area_coverage_fraction": diagnostic["xy_area_coverage_fraction"],
                        "warning_sign_placed": diagnostic["object_place_success"]["warning_sign"],
                        "box_placed": diagnostic["object_place_success"]["box"],
                        "discoverer_hold_s": diagnostic["discoverer_hold_s"],
                        "resource_wait_ticks": result["resource_wait_ticks"],
                        "communication_delay_ticks": result["communication_delay_ticks"],
                        "communication_loss_bits": result["communication_loss_bits"],
                        "notification_submitted": result["notification_submitted"],
                        "notification_delivered": result["notification_delivered"],
                        "notification_pending": result["notification_pending"],
                        "invalid_capability_assignments": diagnostic["capability_invalid_assignments"],
                        "replay_equivalent": bool(replay["replay_equivalent"]),
                        "paper_score_eligible": False,
                    })
    receipt = {
        "schema": "city_02.logic_matrix.v1", "isaac_sim_started": False,
        "control_dt_s": 0.02, "repeats": repeats, "rows": rows,
        "notes": [
            "The matrix consumes the frozen semantic map and uses the logical 0.02 s clock.",
            "EMOS and TeamWeaver columns are deterministic contract adapters in goal mode; native model calls are not implied.",
            "Object placement is capability gated and validated by source/destination and same-robot continuation groups.",
            "paper_score_eligible remains false until the independent evidence gates are satisfied.",
        ],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        csv_path = output.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("benchmark/results/city_02_logic_matrix.v1.json"))
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    receipt = run_matrix(repeats=args.repeats, output=args.output)
    print(json.dumps({"rows": len(receipt["rows"]), "output": str(args.output.resolve()), "csv": str(args.output.with_suffix('.csv').resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
