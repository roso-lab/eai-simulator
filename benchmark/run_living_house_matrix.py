"""Run the complete Isaac-free living_house algorithm/condition matrix."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from benchmark.living_house_algorithms import EmosLivingHouseAlgorithm, TeamWeaverLivingHouseAlgorithm
from benchmark.scenarios.living_house import LivingHouseScenarioAdapter


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = ROOT / "benchmark/results/living_house_logic_matrix.v1.json"
DEFAULT_CSV = ROOT / "benchmark/results/living_house_logic_matrix.v1.csv"


def run_matrix(seed: int = 1) -> list[dict[str, object]]:
    adapter = LivingHouseScenarioAdapter()
    rows: list[dict[str, object]] = []
    for algorithm_type in (EmosLivingHouseAlgorithm, TeamWeaverLivingHouseAlgorithm):
        for tier in range(1, 5):
            for condition in ("C0", "C1", "C2", "C3"):
                result = adapter.run_generic(tier=tier, condition_id=condition,
                                             algorithm=algorithm_type(), seed=seed)
                metrics = result["living_house_metrics"]
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
                    "door_open_count": metrics["door_open_count"],
                    "door_close_count": metrics["door_close_count"],
                    "door_open_to_close_s": metrics["door_open_to_close_s"],
                    "cooperative_sync_gap_s": metrics["cooperative_sync_gap_s"],
                    "handoff_count": metrics["handoff_count"],
                    "invalid_assignment_count": metrics["invalid_assignment_count"],
                    "communication_delay_s": result["communication_delay_ticks"] * 0.02,
                    "communication_loss_bits": result["communication_loss_bits"],
                    "lost_robots": result["lost_robots"],
                    "checkpoint_labels": [item["label"] for item in result["checkpoints"]],
                    "checkpoint_replay_equivalent": result["replay_equivalent"],
                    "paper_score_eligible": result["paper_score_eligible"],
                })
    return rows


def write_results(rows: list[dict[str, object]], json_path: Path = DEFAULT_JSON,
                  csv_path: Path = DEFAULT_CSV) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "heterocoord.living_house.logic_matrix.v1",
               "scenario": "living_house", "clock_dt_s": 0.02,
               "seed": 1, "row_count": len(rows),
               "success_count": sum(bool(row["success"]) for row in rows),
               "rows": rows}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False)
                             if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


if __name__ == "__main__":
    result_rows = run_matrix()
    write_results(result_rows)
    print(json.dumps({"rows": len(result_rows),
                      "success": sum(bool(row["success"]) for row in result_rows)},
                     ensure_ascii=False))
