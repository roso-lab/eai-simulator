"""One native semantic planning batch with ideal logical navigation execution.

Uses the real LLM decomposition, original MIQP and action-generation chain.
The bounded host does not run Habitat/Isaac or claim persistent phase feedback.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path

from algorithm.common_coordination.contract import ExecutionFeedback
from benchmark.native_algorithms import build_native_adapter
from task.contracts import EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner


class LogicalNavigationExecutor:
    def __init__(self, task):
        self.package = task
        self.robots = {r.capability.robot_id: r for r in task.robots}
        self.completed = set()
        self.trace = []
        graph = task.metadata["teamweaver_world_graph"]
        if graph.get("coordinate_frame") != "habitat_y_up":
            raise ValueError("explicit habitat_y_up frame required")
        nodes = {n["name"]: n for n in graph["nodes"]}
        self.targets = {}
        for t in task.graph.tasks:
            if t.metadata.get("native_task_type") != "Navigate" or t.prerequisites:
                raise ValueError("this one-batch host supports independent Navigate tasks only")
            raw = nodes[t.target_ref]["properties"]["translation"]
            if len(raw) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in raw):
                raise ValueError("finite world target required")
            # Explicit Habitat y-up -> EAI z-up conversion, no learned policy.
            target = (float(raw[0]), float(raw[2]), float(raw[1]))
            declared = t.metadata.get("target_position", t.metadata.get("target_xy"))
            if declared is None or tuple(declared) != target[:len(declared)] or len(declared) not in (2, 3):
                raise ValueError("native world target and EAI task target disagree")
            self.targets[t.task_id] = target
        for robot in task.robots:
            expected = (robot.position[0], robot.position[2], robot.position[1])
            if tuple(robot.metadata.get("teamweaver_position", ())) != expected:
                raise ValueError("native and EAI robot coordinates disagree")

    def execute(self, assignment, task, *, scene):
        robot = self.robots[assignment.robot_id]
        canonical = next(t for t in self.package.graph.tasks if t.task_id == task.task_id)
        if canonical != task or task.task_id in self.completed:
            raise ValueError("unknown or repeated task execution")
        if not robot.capability.active or not task.required_capabilities <= robot.capability.capabilities:
            raise ValueError("ineligible robot")
        target = self.targets[task.task_id]
        after = replace(robot, position=target, metadata={**robot.metadata,
                        "teamweaver_position": [target[0], target[2], target[1]]})
        self.robots[assignment.robot_id] = after
        self.completed.add(task.task_id)
        self.trace.append({"task_id": task.task_id, "robot_id": assignment.robot_id,
                           "before": list(robot.position), "after": list(after.position),
                           "target": list(target), "execution": "ideal_logical_navigation"})
        return ExecutionFeedback(task.task_id, assignment.robot_id, "succeeded",
                                 timestamp_s=float(len(self.trace)),
                                 world_changes={"robot_position": list(after.position), "physical": False})


def run_once(task, *, timeout_s=240):
    executor = LogicalNavigationExecutor(task)
    adapter = build_native_adapter("teamweaver", scope="full_semantic", output_seam=True,
                                   source_compatibility=True, timeout_s=timeout_s)
    try:
        run = TaskRunner(executor=executor).run(TaskRunRequest(task), adapter)
    except Exception as exc:
        return {"status": "not_passed", "error_type": type(exc).__name__,
                "native_result": getattr(exc, "native_result", {}),
                "executions": executor.trace, "physical_execution": False,
                "logical_success": False}
    # Independent post-execution check against declared world targets.
    expected = {t.task_id for t in task.graph.tasks}
    valid = (executor.completed == expected and len(executor.trace) == len(expected)
             and len({t["robot_id"] for t in executor.trace}) == len(executor.trace)
             and all(tuple(t["after"]) == executor.targets[t["task_id"]] for t in executor.trace)
             and all(f.outcome == "succeeded" for f in run.feedback))
    return {"status": "logical_batch_completed" if valid else "not_passed",
            "logical_success": valid, "physical_execution": False, "scoreable": False,
            "persistent_semantic_feedback": False, "executions": executor.trace, "result": run.to_dict()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=240)
    args = parser.parse_args()
    if args.output.resolve() == args.task.resolve() or args.output.exists():
        parser.error("output must be a new file distinct from input")
    receipt = run_once(EaiTaskPackage.from_json(args.task), timeout_s=args.timeout_s)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"status": receipt["status"], "output": str(args.output)}))
    return 0 if receipt["logical_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
