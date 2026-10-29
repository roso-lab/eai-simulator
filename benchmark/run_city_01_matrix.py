"""Generate reproducible Isaac-free city_01 algorithm matrix receipts.

The receipt is a diagnostic data product.  It intentionally labels the two
current algorithm columns as contract adapters; native EMOS/TeamWeaver runs
require their source-backed adapters and provenance gates separately.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmark.city_01_algorithms import EmosCity01Algorithm, TeamWeaverCity01Algorithm
from benchmark.hetero_diagnostic_runtime_v1 import DiagnosticRuntime
from benchmark.scenarios.city_01 import City01ScenarioAdapter


ALGORITHMS = {"emos": EmosCity01Algorithm, "teamweaver": TeamWeaverCity01Algorithm}
COMMUNICATIONS = ("C0", "C1", "C2", "C3")


def run_matrix(*, repeats: int = 1, output: Path | None = None) -> dict[str, Any]:
    adapter = City01ScenarioAdapter()
    rows: list[dict[str, Any]] = []
    for algorithm_id, algorithm_cls in ALGORITHMS.items():
        for tier in range(1, 5):
            conditions = ("nominal_no_battery",) if tier < 3 else ("battery_homogeneous", "battery_heterogeneous")
            for condition_id in conditions:
                for communication in COMMUNICATIONS:
                    for repeat in range(1, repeats + 1):
                        episode = adapter.build_episode(tier=tier, condition_id=condition_id, communication=communication)
                        # K=3 is a real repeat axis: keep paired cells aligned
                        # while changing only the registered runtime seed.
                        episode = replace(episode, seed=repeat - 1)
                        runtime = DiagnosticRuntime(
                            episode,
                            algorithm=algorithm_cls(),
                        )
                        checkpoint = runtime.checkpoint(metadata={"schema": runtime.schema_version, "tick": "0"})
                        result = runtime.run()
                        replay = runtime.replay(checkpoint, ticks=None)
                        metrics = result
                        rows.append({
                            "algorithm": algorithm_id,
                            "algorithm_scope": "deterministic_contract_adapter",
                            "model_provider": "dongli",
                            "model": "gpt-5.6-luna",
                            "reasoning_effort": "low",
                            "scene": "city_01",
                            "tier": tier,
                            "condition": condition_id,
                            "communication": communication,
                            "repeat": repeat,
                            "success": bool(metrics["success"]),
                            "failure_reason": metrics["failure_reason"],
                            "makespan_s": metrics["makespan_s"],
                            "coverage_fraction": metrics["coverage_fraction"],
                            "task_completion_rate": metrics["task_completion_rate"],
                            "recover_services": metrics["recover_services"],
                            "resource_contention_requests": metrics["resource_contention_requests"],
                            "resource_wait_ticks": metrics["resource_wait_ticks"],
                            "min_battery_remaining_m": metrics["min_battery_remaining_m"],
                            "communication_bits": metrics["communication_bits"],
                            "communication_delay_ticks": metrics["communication_delay_ticks"],
                            "communication_loss_bits": metrics["communication_loss_bits"],
                            "replay_equivalent": bool(replay["replay_equivalent"]),
                            "paper_score_eligible": False,
                        })
    receipt = {
        "schema": "city_01.logic_matrix.v1",
        "isaac_sim_started": False,
        "control_dt_s": 0.02,
        "repeats": repeats,
        "rows": rows,
        "notes": [
            "Model fields reserve the requested Dongli gpt-5.6-luna/low configuration.",
            "Current rows are deterministic contract-adapter diagnostics, not native LLM score columns.",
            "Communication is metered by the runtime; algorithm replanning does not yet consume delivered frames.",
            "paper_score_eligible remains false until paired evidence and source-backed native gates are complete.",
        ],
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        csv_path = output.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("benchmark/results/city_01_logic_matrix.v1.json"))
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    receipt = run_matrix(repeats=args.repeats, output=args.output)
    print(json.dumps({"rows": len(receipt["rows"]), "output": str(args.output.resolve()), "csv": str(args.output.with_suffix('.csv').resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
