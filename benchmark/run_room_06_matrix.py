"""Run the complete Isaac-free room_06 algorithm/condition matrix."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from benchmark.room06_algorithms import EmosRoom06Algorithm, TeamWeaverRoom06Algorithm
from benchmark.scenarios.room_06 import Room06ScenarioAdapter


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = ROOT / "benchmark/results/room_06_logic_matrix.v1.json"
DEFAULT_CSV = ROOT / "benchmark/results/room_06_logic_matrix.v1.csv"


def run_matrix() -> list[dict[str, object]]:
    adapter = Room06ScenarioAdapter()
    rows: list[dict[str, object]] = []
    for algorithm_type in (EmosRoom06Algorithm, TeamWeaverRoom06Algorithm):
        for tier in range(1, 5):
            for condition in ("C0", "C1", "C2", "C3"):
                result = adapter.run_generic(
                    tier=tier, condition_id=condition,
                    algorithm=algorithm_type(), seed=1,
                )
                starts = [event for event in result["events"]
                          if event.get("kind") == "cooperative_service_started"]
                completes = [event for event in result["events"]
                             if event.get("kind") == "cooperative_service_completed"]
                rows.append({
                    "algorithm": algorithm_type.algorithm_id,
                    "tier": tier,
                    "condition_id": condition,
                    "success": bool(result["success"]),
                    "failure_reason": result["failure_reason"],
                    "makespan_s": result["makespan_s"],
                    "hard_deadline_s": result["hard_deadline_s"],
                    "deadline_met": result["deadline_met"],
                    "task_completion_rate": result["task_completion_rate"],
                    "resource_wait_s": result["resource_wait_ticks"] * 0.02,
                    "resource_contention_requests": result["resource_contention_requests"],
                    "communication_delay_s": result["communication_delay_ticks"] * 0.02,
                    "communication_loss_bits": result["communication_loss_bits"],
                    "lost_robots": result["lost_robots"],
                    "cooperative_start_count": len(starts),
                    "cooperative_complete_count": len(completes),
                    "cooperative_robot_ids": starts[0].get("robot_ids", []) if starts else [],
                    "checkpoint_labels": [item["label"] for item in result["checkpoints"]],
                    "checkpoint_replay_equivalent": result["replay_equivalent"],
                    "paper_score_eligible": result["paper_score_eligible"],
                })
    return rows


def write_results(rows: list[dict[str, object]], json_path: Path = DEFAULT_JSON,
                  csv_path: Path = DEFAULT_CSV) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "heterocoord.room_06.logic_matrix.v1",
        "scenario": "room_06",
        "clock_dt_s": 0.02,
        "row_count": len(rows),
        "success_count": sum(bool(row["success"]) for row in rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False)
                             if isinstance(value, list) else value
                             for key, value in row.items()})


if __name__ == "__main__":
    result_rows = run_matrix()
    write_results(result_rows)
    print(json.dumps({"rows": len(result_rows),
                      "success": sum(bool(row["success"]) for row in result_rows)},
                     ensure_ascii=False))
