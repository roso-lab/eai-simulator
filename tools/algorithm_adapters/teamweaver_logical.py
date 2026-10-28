"""Native TeamWeaver allocation with ideal logical navigation; no Isaac or LLM.

Run with ``python -m tools.algorithm_adapters.teamweaver_logical``.  Positions
are updated by this host, observed by TaskRunner, and checked independently of
the executor feedback. No physical success or benchmark score is claimed.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path

from algorithm.common_coordination.contract import (
    CoordinationTask, ExecutionFeedback, FailureCode, RobotCapability, TaskGraph,
)
from algorithm.system.coordination.TeamWeaver.native_adapter import NativeTeamWeaverAdapter
from task.contracts import (
    DependencyOutcome, EaiTaskPackage, ExecutionUpdate, OrchestrationLimits,
    RobotState, TaskRunRequest,
)
from task.observation import WorldSnapshot
from task.runner import TaskRunner


def make_package() -> EaiTaskPackage:
    """Two robots, two supplied DAG frontiers, common native p_goal per frontier."""
    robots = (
        RobotState(RobotCapability("mobile", frozenset({"navigation"})),
                   (-1., 0., 0.), {"yaw_rad": 0.}),
        RobotState(RobotCapability("sensor", frozenset({"navigation", "sensing"})),
                   (1., 0., 0.), {"yaw_rad": 0.}),
    )
    targets = {"inspection": [0., 0., 0.], "return": [2., 1., 0.]}
    tasks = []
    for phase, target in enumerate(targets):
        prerequisites = () if phase == 0 else ("phase0_mobile", "phase0_sensor")
        for robot in robots:
            tasks.append(CoordinationTask(
                f"phase{phase}_{robot.capability.robot_id}", f"Navigate to {target}",
                robot.capability.capabilities, prerequisites, target,
                {"native_task_type": "Navigate", "target_position": targets[target],
                 "target_xy": targets[target][:2]},
            ))
    return EaiTaskPackage("teamweaver-logical-dag", "ideal_logical_no_physics",
                          TaskGraph(tuple(tasks), "explicit supplied two-frontier DAG"),
                          robots, metadata={"logical_targets": targets})


def _coordinates(value, size):
    if (not isinstance(value, (tuple, list)) or len(value) != size
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in value)):
        raise ValueError("target/position requires finite coordinates")
    return tuple(float(v) for v in value)


def preflight(package):
    """Reject inconsistent target representations before invoking the solver."""
    targets = package.metadata.get("logical_targets", {})
    if not targets or package.metadata.get("completed_task_ids"):
        raise ValueError("logical host requires targets and a fresh mission")
    for robot in package.robots:
        _coordinates(robot.position, 3)
        if not robot.capability.active:
            raise ValueError("logical host requires active robots")
    resolved = {}
    for task in package.graph.tasks:
        if task.metadata.get("native_task_type") != "Navigate":
            raise ValueError("logical host only executes Navigate")
        if task.target_ref not in targets:
            raise ValueError("unknown logical target")
        target = _coordinates(targets[task.target_ref], 3)
        if "target_position" not in task.metadata and "target_xy" not in task.metadata:
            raise ValueError("missing native target")
        if "target_position" in task.metadata:
            if _coordinates(task.metadata["target_position"], 3) != target:
                raise ValueError("target_position conflicts with logical target")
        if "target_xy" in task.metadata:
            if _coordinates(task.metadata["target_xy"], 2) != target[:2]:
                raise ValueError("target_xy conflicts with logical target")
        if any(robot.position[2] != target[2] for robot in package.robots):
            raise ValueError("native Navigate is planar; target height must match robots")
        resolved[task.task_id] = target
    completed = set()
    while len(completed) < len(package.graph.tasks):
        ready = [t for t in package.graph.tasks if t.task_id not in completed
                 and set(t.prerequisites) <= completed]
        if not ready:
            raise ValueError("DAG has no ready frontier")
        if len({resolved[t.task_id][:2] for t in ready}) != 1:
            raise ValueError("native frontier shares p_goal; conflicting targets")
        completed.update(t.task_id for t in ready)
    return resolved


def verify_step(package, assignments, updates, before, after, completed):
    """Independent reject-only check of identities, DAG, positions and feedback."""
    targets = preflight(package)
    tasks = {t.task_id: t for t in package.graph.tasks}
    if len(assignments) != len(updates) or not assignments:
        raise ValueError("feedback cardinality mismatch")
    if len({a.task_id for a in assignments}) != len(assignments):
        raise ValueError("duplicate task")
    if len({a.robot_id for a in assignments}) != len(assignments):
        raise ValueError("duplicate robot slot")
    old = {r.capability.robot_id: r for r in before.robots}
    new = {r.capability.robot_id: r for r in after.robots}
    if (set(old) != set(new) or before.scene_id != after.scene_id
            or before.scene_id != package.scene_id
            or after.revision != before.revision + 1 or after.tick != before.tick + 1):
        raise ValueError("invalid step observation identity/revision")
    expected = {rid: r.position for rid, r in old.items()}
    for assignment, update in zip(assignments, updates, strict=True):
        if assignment.task_id not in tasks or assignment.robot_id not in old:
            raise ValueError("unknown assignment identity")
        task = tasks[assignment.task_id]
        robot = old[assignment.robot_id]
        if task.task_id in completed or not set(task.prerequisites) <= set(completed):
            raise ValueError("duplicate completion or unmet prerequisite")
        if not robot.capability.active or not task.required_capabilities <= robot.capability.capabilities:
            raise ValueError("ineligible robot")
        feedback = update.feedback
        if ((feedback.task_id, feedback.robot_id) != (assignment.task_id, assignment.robot_id)
                or feedback.outcome != "succeeded" or feedback.failure_code is not FailureCode.NONE
                or update.dependency_outcome is not DependencyOutcome.COMPLETED
                or feedback.timestamp_s != after.tick):
            raise ValueError("feedback does not match independently verified step")
        expected[assignment.robot_id] = targets[task.task_id]
    for rid in old:
        if (new[rid].position != expected[rid] or new[rid].capability != old[rid].capability
                or new[rid].metadata != old[rid].metadata):
            raise ValueError("position/state does not match executed Navigate")


class LogicalHost:
    def __init__(self, package):
        self.package = package
        self.targets = preflight(package)
        self.robots = tuple(package.robots)
        self.tick = 0
        self.completed = set()
        self.steps = []

    def observe(self, scene=None):
        return WorldSnapshot(scene_id=self.package.scene_id, revision=self.tick,
                             tick=self.tick, robots=self.robots)

    def execute_batch(self, assignments, tasks, *, scene):
        before = self.observe()
        # Validate all identities/dependencies before changing host state.
        if not assignments:
            raise ValueError("empty dispatch batch")
        if len({a.robot_id for a in assignments}) != len(assignments):
            raise ValueError("duplicate robot slot")
        if len({a.task_id for a in assignments}) != len(assignments):
            raise ValueError("duplicate task")
        canonical = {t.task_id: t for t in self.package.graph.tasks}
        robots = {r.capability.robot_id: r for r in self.robots}
        for a in assignments:
            if a.task_id not in canonical or a.robot_id not in robots or tasks[a.task_id] != canonical[a.task_id]:
                raise ValueError("unknown assignment identity/task contract")
            t = canonical[a.task_id]
            if a.task_id in self.completed or not set(t.prerequisites) <= self.completed:
                raise ValueError("duplicate completion or unmet prerequisite")
            if not t.required_capabilities <= robots[a.robot_id].capability.capabilities:
                raise ValueError("ineligible robot")
        for a in assignments:
            robots[a.robot_id] = replace(robots[a.robot_id], position=self.targets[a.task_id])
        self.robots = tuple(robots[r.capability.robot_id] for r in self.robots)
        self.tick += 1
        after = self.observe()
        updates = tuple(ExecutionUpdate(
            ExecutionFeedback(a.task_id, a.robot_id, "succeeded", FailureCode.NONE,
                              "Ideal logical Navigate; no physical execution", float(self.tick)),
            DependencyOutcome.COMPLETED,
        ) for a in assignments)
        verify_step(self.package, assignments, updates, before, after, self.completed)
        self.steps.append({"assignments": [a.__dict__ for a in assignments],
                           "before": before.to_dict(), "after": after.to_dict(),
                           "feedback": [{**u.feedback.__dict__, "failure_code": u.feedback.failure_code.value,
                                         "dependency_outcome": u.dependency_outcome.value} for u in updates],
                           "independently_checked": True})
        self.completed.update(a.task_id for a in assignments)
        return updates


def run_logical(adapter=None, package=None):
    package = package or make_package()
    host = LogicalHost(package)
    adapter = adapter or NativeTeamWeaverAdapter()
    if adapter.scope != "supplied_decomposition":
        raise ValueError("logical tool requires native supplied_decomposition scope")
    if not callable(getattr(adapter, "replan", None)):
        raise RuntimeError("native adapter.replan integration is required")
    result = TaskRunner(orchestration_executor=host, observation_provider=host.observe).run_orchestrated(
        TaskRunRequest(package), adapter, replan=adapter.replan,
        limits=OrchestrationLimits(max_rounds=12, max_dispatches=16),
    )
    complete = (result.termination_reason == "all_dependencies_completed"
                and set(host.completed) == {t.task_id for t in package.graph.tasks}
                and all(s == "completed" for s in result.task_states.values()))
    return {"schema": "heterocoord.teamweaver.logical.v1", "logical_success": complete,
            "execution_scope": "supplied_decomposition", "physical_execution": "not_run",
            "physical_success": "N/A", "scoreable": False,
            "steps": host.steps, "result": result.to_dict()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root")
    parser.add_argument("--python-executable")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    receipt = run_logical(NativeTeamWeaverAdapter(args.source_root, python_executable=args.python_executable))
    text = json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if receipt["logical_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
