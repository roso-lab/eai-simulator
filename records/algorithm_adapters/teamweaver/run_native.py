"""Reproducible real-solver decision, TaskRunner and failure-scope receipt."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from algorithm.system.coordination.native_runtime import sha256_file, verify_source
from algorithm.system.coordination.TeamWeaver.native_adapter import (
    EXPECTED_COMMIT, NativeScopeError, NativeSolverError, NativeTeamWeaverAdapter,
)
from task.contracts import EaiTaskPackage, RobotState, TaskRunRequest
from task.runner import TaskRunner


def make_package() -> EaiTaskPackage:
    robots = (
        RobotState(RobotCapability("mobile", frozenset({"navigation"})),
                   (-1.0, 0.0, 0.0), {"yaw_rad": 0.0, "teamweaver_position": [-1.0, 0.0, 0.0]}),
        RobotState(RobotCapability("sensor", frozenset({"navigation", "sensing"})),
                   (1.0, 0.0, 0.0), {"yaw_rad": 0.0, "teamweaver_position": [1.0, 0.0, 0.0]}),
    )
    tasks = (
        CoordinationTask("move_mobile", "Move to the inspection area", frozenset({"navigation"}),
                         target_ref="inspection_area",
                         metadata={"native_task_type": "Navigate", "target_xy": [0.0, 0.0]}),
        CoordinationTask("move_sensor", "Move a sensing-capable robot to the inspection area",
                         frozenset({"navigation", "sensing"}), target_ref="inspection_area",
                         metadata={"native_task_type": "Navigate", "target_xy": [0.0, 0.0]}),
    )
    return EaiTaskPackage("teamweaver-native-smoke", "protocol_fixture_no_physics",
                          TaskGraph(tasks, "explicit supplied decomposition"), robots,
                          metadata={
                              "instruction": "Move the mobile robot and sensing robot to the inspection area.",
                              "teamweaver_world_graph": {
                                  "coordinate_frame": "habitat_y_up",
                                  "nodes": [
                                      {"name": "kitchen", "type": "Room", "properties": {"translation": [0, 0, 0]}},
                                      {"name": "counter", "type": "Furniture", "properties": {"translation": [1, 0, 1]}},
                                      {"name": "inspection_area", "type": "Object", "properties": {"translation": [0, 0, 0], "states": {}}},
                                      {"name": "agent_0", "type": "SpotRobot", "properties": {"translation": [-1, 0, 0], "rotation": [0, 0, 0, 1]}},
                                      {"name": "agent_1", "type": "SpotRobot", "properties": {"translation": [1, 0, 0], "rotation": [0, 0, 0, 1]}},
                                  ],
                                  "edges": [
                                      {"from": "counter", "to": "kitchen", "label": "inside", "opposite_label": "contains"},
                                      {"from": "inspection_area", "to": "counter", "label": "inside", "opposite_label": "contains"},
                                      {"from": "agent_0", "to": "kitchen", "label": "inside", "opposite_label": "contains"},
                                      {"from": "agent_1", "to": "kitchen", "label": "inside", "opposite_label": "contains"},
                                  ],
                              },
                          })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root")
    parser.add_argument("--python-executable")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    adapter = NativeTeamWeaverAdapter(args.source_root, python_executable=args.python_executable)
    before = verify_source(adapter.source_root, EXPECTED_COMMIT)
    task = make_package()
    decision = adapter(task, task.robots)
    result = TaskRunner().run(TaskRunRequest(task), adapter)
    # Native exact-one-per-robot equality forces two robots onto this one task.
    coalition_task = replace(task, graph=TaskGraph(task.graph.tasks[:1], "supplied coalition probe"))
    try:
        adapter(coalition_task, coalition_task.robots)
    except NativeScopeError as exc:
        coalition = {"status": "scope_error", "reason": str(exc), "native_result": exc.native_result}
    else:
        raise AssertionError("expected an explicit coalition scope error")
    infeasible_task = replace(task, robots=task.robots[:1])
    try:
        adapter(infeasible_task, infeasible_task.robots)
    except NativeSolverError as exc:
        infeasible = {"status": "native_solver_error", "reason": str(exc), "native_result": exc.native_result}
    else:
        raise AssertionError("expected native infeasibility without a fallback")
    probe = adapter.probe_full_semantic()
    after = verify_source(adapter.source_root, EXPECTED_COMMIT)
    assert before == after
    checked_files = (
        "TeamWeaver/planner/HRCS/class_def/RTA_task.py",
        "TeamWeaver/planner/HRCS/class_def/task_utility_normalizer.py",
        "TeamWeaver/planner/HRCS/params_module/scenario_params_task.py",
        "TeamWeaver/planner/HRCS/params_module/opt_params_task.py",
        "TeamWeaver/planner/HRCS/plan_module/miqp_solver_wrapper.py",
        "TeamWeaver/planner/HRCS/connector/phase_manager.py",
        "TeamWeaver/planner/miqp_prompts.py",
        "TeamWeaver/planner/llm_planner_comparison/llm_planner_miqp.py",
    )
    receipt = {
        "schema": "heterocoord.teamweaver.native_receipt.v1",
        "source_before": before.to_dict(), "source_after": after.to_dict(),
        "source_unchanged": before == after,
        "source_file_sha256": {p: sha256_file(adapter.source_root / p) for p in checked_files},
        "interpreter": adapter.python_executable,
        "decision": {"condition": decision.condition.to_dict(),
                     "assignments": [a.__dict__ for a in decision.assignments],
                     "unassigned_tasks": decision.unassigned_tasks,
                     "provenance": decision.provenance},
        "taskrunner_roundtrip": result.to_dict(),
        "coalition": coalition, "infeasible": infeasible, "full_semantic_probe": probe,
        "physical_execution": "not_run", "physical_success": "N/A", "scoreable": False,
    }
    serialized = json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
        print(f"Receipt: {args.output.resolve()}")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
