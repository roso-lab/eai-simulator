"""Command line entry point for the reusable diagnostic runtime.

``city_01`` is exposed through its frozen compatibility adapter.  Other scenes
can provide a small JSON episode spec and use the same runtime, checkpoints,
communication conditions and algorithm seam without starting Isaac Sim.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any

from .hetero_diagnostic_runtime_v1 import (
    DiagnosticRuntime, EpisodeSpec, GreedyGoalAlgorithm, ResourceSpec,
    RobotSpec, SemanticEdge, SemanticGraph, SemanticNode, TaskSpec,
)
from .scenarios.city_01 import City01ScenarioAdapter


def _episode_from_json(path: Path, communication: str | None = None) -> EpisodeSpec:
    doc = json.loads(path.read_text(encoding="utf-8"))
    graph = SemanticGraph(
        [SemanticNode(str(row["node_id"]), str(row["domain"]), tuple(row.get("position", (0, 0, 0))), tuple(row.get("semantics", ()))) for row in doc["nodes"]],
        [SemanticEdge(str(row["source"]), str(row["target"]), float(row["distance_m"]), bool(row.get("bidirectional", True))) for row in doc.get("edges", ())],
    )
    robots = tuple(RobotSpec(**row) for row in doc["robots"])
    tasks = tuple(TaskSpec(**row) for row in doc["tasks"])
    resources = tuple(ResourceSpec(**row) for row in doc.get("resources", ()))
    return EpisodeSpec(graph, robots, tasks, resources=resources,
                       horizon_ticks=int(doc.get("horizon_ticks", 10000)),
                       dt_s=float(doc.get("dt_s", 0.02)), seed=int(doc.get("seed", 0)),
                       communication_condition=communication or str(doc.get("communication_condition", "C0")))


def _algorithm(name: str) -> Any:
    if name == "greedy_goal_v1":
        return GreedyGoalAlgorithm()
    if ":" not in name:
        raise ValueError("algorithm must be greedy_goal_v1 or a local module:path")
    module_name, attribute = name.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    value = factory() if callable(factory) else factory
    for method in ("initialize", "decide", "snapshot", "restore"):
        if not callable(getattr(value, method, None)):
            raise TypeError(f"algorithm adapter missing {method}()")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="city_01")
    parser.add_argument("--tier", type=int, default=4)
    parser.add_argument("--condition", default=None)
    parser.add_argument("--communication", default=None, choices=("C0", "C1", "C2", "C3"))
    parser.add_argument("--engine", choices=("generic", "compatibility"), default="generic",
                        help="generic reusable runtime, or the frozen city compatibility runner")
    parser.add_argument("--spec", type=Path, help="generic EpisodeSpec JSON; omit for city_01")
    parser.add_argument("--algorithm", default="greedy_goal_v1",
                        help="greedy_goal_v1 or a local module:factory implementing AlgorithmAdapter")
    parser.add_argument("--checkpoint", type=Path, help="write a runtime checkpoint artifact")
    parser.add_argument("--checkpoint-ticks", type=int, default=0,
                        help="advance this many logical ticks before writing --checkpoint")
    parser.add_argument("--verify-replay", action="store_true",
                        help="replay from the written/in-memory checkpoint and report equivalence")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.spec is None:
        if args.scenario != "city_01":
            parser.error("--spec is required for scenarios other than city_01")
        adapter = City01ScenarioAdapter()
        if args.engine == "compatibility":
            result = adapter.run_compatibility(tier=args.tier, condition_id=args.condition)
        else:
            result = adapter.run_generic(tier=args.tier, condition_id=args.condition,
                                         communication=args.communication, algorithm=_algorithm(args.algorithm))
    else:
        episode = _episode_from_json(args.spec, args.communication)
        runtime = DiagnosticRuntime(episode, algorithm=_algorithm(args.algorithm))
        checkpoint_receipt = None
        if args.checkpoint:
            if args.checkpoint_ticks < 0:
                parser.error("--checkpoint-ticks must be nonnegative")
            if args.checkpoint_ticks:
                runtime.run(max_ticks=args.checkpoint_ticks)
            checkpoint_receipt = runtime.checkpoint(args.checkpoint, metadata={"schema": runtime.schema_version, "tick": str(runtime.clock_tick)})
        result = runtime.run()
        if checkpoint_receipt is not None:
            result["checkpoint"] = checkpoint_receipt
            if args.verify_replay:
                result["replay"] = runtime.replay(checkpoint_receipt)
        elif args.verify_replay:
            checkpoint_receipt = runtime.checkpoint()
            result["replay"] = runtime.replay(checkpoint_receipt)
        result["scenario"] = {"scenario_id": args.scenario, "spec": str(args.spec.resolve())}

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if bool(result.get("metrics", result).get("success", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
