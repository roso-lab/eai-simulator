"""CPU integration of the shared factory/CLI and native 2D feedback host.

Only test harness code supplies a simulator host. No shared entrypoint or
algorithm policy is changed. Set HETEROCOORD_SADCHER_EVIDENCE_DIR to persist.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import torch

from algorithm.common_coordination.contract import ExecutionFeedback
from algorithm.system.coordination.sadcher.native_adapter import NativeCoalitionScopeError
from algorithm.system.coordination.sadcher.tests.test_native_adapter import (
    ROOT, COMPATIBLE, package, independent_simulation, new_native_scheduler,
)
from benchmark.native_algorithms import build_native_adapter
from task.contracts import DependencyOutcome, EaiTaskPackage, ExecutionUpdate, OrchestrationLimits, TaskRunRequest
from task.observation import WorldSnapshot
from task.runner import TaskRunner


REPO = Path(__file__).resolve().parents[5]


@pytest.fixture(autouse=True)
def dependencies():
    if not (ROOT / COMPATIBLE).is_file():
        pytest.skip("native SADCHER assets absent; integration not verified")
    torch.set_num_threads(1)


@pytest.fixture
def output(tmp_path):
    configured = os.getenv("HETEROCOORD_SADCHER_EVIDENCE_DIR")
    target = Path(configured) / "unified_entry" if configured else tmp_path
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def task_json(task):
    return {
        "task_id": task.task_id, "scene_id": task.scene_id, "metadata": dict(task.metadata),
        "tasks": [{"task_id": t.task_id, "description": t.description,
                   "required_capabilities": sorted(t.required_capabilities),
                   "prerequisites": list(t.prerequisites), "metadata": dict(t.metadata)} for t in task.graph.tasks],
        "robots": [{"robot_id": r.capability.robot_id, "capabilities": sorted(r.capability.capabilities),
                    "active": r.capability.active, "position": r.position, "metadata": dict(r.metadata)} for r in task.robots],
    }


def case_input(case):
    data = task_json(package())
    config = {"checkpoint_path": COMPATIBLE}
    if case == "ur5_idle":
        data = {
            "task_id": "sadcher-ur5-physical-input-smoke", "scene_id": "parent-owned-Isaac",
            "metadata": {"sadcher": {"start_position": [0, 0, 0], "end_position": [0, 0, 0]}},
            "tasks": [{"task_id": "pick_place", "description": "Pick and place",
                       "required_capabilities": ["manipulate"],
                       "metadata": {"position": [.45, .05, 0], "duration_s": 30}}],
            "robots": [{"robot_id": "ur5_1", "capabilities": ["manipulate"], "position": [0, 0, 0]}],
        }
        config["skill_names"] = ["manipulate", "inspect", "transport"]
    if case == "coalition_rejected":
        data["tasks"] = data["tasks"][:1]
        data["tasks"][0]["required_capabilities"] = ["terrain", "button"]
    if case == "default_checkpoint_rejected":
        from algorithm.system.coordination.sadcher.native_adapter import LEGACY_CHECKPOINT
        config = {"checkpoint_path": LEGACY_CHECKPOINT}
    return data, config


@pytest.mark.parametrize("case", ["normal", "ur5_idle", "coalition_rejected", "default_checkpoint_rejected"])
def test_unified_factory_and_real_cli(case, output):
    data, config = case_input(case)
    task_path, config_path = output / f"{case}.task.json", output / f"{case}.config.json"
    report_path = output / f"{case}.cli.json"
    write_json(task_path, data)
    write_json(config_path, config)
    task = EaiTaskPackage.from_json(task_path)
    provider = build_native_adapter("sadcher", source_root=ROOT, **config)
    if case == "coalition_rejected":
        with pytest.raises(NativeCoalitionScopeError) as exc:
            TaskRunner().run(TaskRunRequest(task), provider)
        write_json(output / f"{case}.factory.json", {
            "status": "scope_rejected", "native_assignments": exc.value.native_assignments,
            "N/A_reason": exc.value.na_reason,
            "coalitions": exc.value.decision.provenance["coalitions"],
            "assignments": [a.__dict__ for a in exc.value.decision.assignments], "dispatch_count": 0,
        })
    elif case == "default_checkpoint_rejected":
        from algorithm.system.coordination.sadcher.native_adapter import CheckpointCompatibilityError
        with pytest.raises(CheckpointCompatibilityError) as exc:
            provider(task, task.robots)
        write_json(output / f"{case}.factory.json", {"status": "checkpoint_rejected", "audit": exc.value.audit})
    else:
        result = TaskRunner().run(TaskRunRequest(task), provider)
        assert result.verdict["status"] == "N/A"
        assert result.decision.provenance["source_integrity"]["commit"] == provider.spec["commit"]
        assert result.decision.provenance["native_adapter_call"] == 1
        assert len(result.decision.assignments) == (2 if case == "normal" else 0)
        if case == "ur5_idle":
            assert result.decision.provenance["idle_robot_ids"] == ["ur5_1"]
        write_json(output / f"{case}.factory.json", {"result": result.to_dict(), "provenance": result.decision.provenance,
                                                    "dispatch_count": 0, "physical_success": None})
    command = [sys.executable, "-m", "tools.algorithm_adapters.run", "--algorithm", "sadcher",
               "--source-root", str(ROOT), "--task", str(task_path), "--config", str(config_path), "--report", str(report_path)]
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"}
    proc = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    (output / f"{case}.cli.stdout.txt").write_text(proc.stdout)
    (output / f"{case}.cli.stderr.txt").write_text(proc.stderr)
    write_json(output / f"{case}.command.json", {"argv": command, "cwd": str(REPO), "returncode": proc.returncode,
                                               "environment_overrides": {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"}})
    report = json.loads(report_path.read_text())
    assert report["physical_success"] is None and not report["simulation_acceptance_passed"]
    if case.endswith("rejected"):
        assert proc.returncode == 1 and report["status"] == "not_passed"
        assert report["error_type"] == ("NativeCoalitionScopeError" if case == "coalition_rejected" else "CheckpointCompatibilityError")
        if case == "coalition_rejected":
            assert report["N/A_reason"] == "benchmark_contract_cannot_represent_native_coalition"
    else:
        assert proc.returncode == 0 and report["status"] == "native_decision_returned"
        assert report["result"]["assignments"] == [a.__dict__ for a in result.decision.assignments]
        assert report["provenance"]["native_assignments"] == result.decision.provenance["native_assignments"]


class PersistentNativeHost:
    """Synchronous batch executor with an uninterrupted upstream Simulation.

    Decisions are compared with the original scheduler on the same observed
    state. Batch completion is TaskRunner's cadence, not a claim of parity with
    upstream step_until_next_decision_point event timing.
    """
    def __init__(self, provider, task):
        self.provider, self.task = provider, task
        provider.build_simulation(task, task.robots)  # actual native imports
        self.sim = independent_simulation()  # independent initial data
        self.task_index = {node.task_id: i + 1 for i, node in enumerate(task.graph.tasks)}
        self.robot_index = {robot.capability.robot_id: i for i, robot in enumerate(task.robots)}
        self.names = ["__start__"] + [node.task_id for node in task.graph.tasks] + ["__idle__", "__end__"]
        self.parity_trace, self.batch_trace = [], []

    def observe(self, scene):
        robots = tuple(replace(state, position=(*native.location.tolist(), 0.0), metadata={
            "current_task_id": self.names[native.current_task.task_id] if native.current_task else None,
            "available": bool(native.available),
        }) for state, native in zip(self.task.robots, self.sim.robots))
        return WorldSnapshot(scene_id=self.task.scene_id, revision=self.sim.timestep, tick=self.sim.timestep, robots=robots,
                             objects={"native_tasks": {self.names[t.task_id]: {"status": t.status, "remaining_duration_s": t.remaining_duration} for t in self.sim.tasks}})

    def compare_and_apply(self):
        oracle_sim = copy.deepcopy(self.sim)
        oracle = new_native_scheduler(oracle_sim)
        expected_rewards, expected_schedule = oracle.calculate_robot_assignment(oracle_sim)
        assert expected_schedule.robot_assignments == self.provider.last_schedule.robot_assignments
        assert torch.equal(expected_rewards, self.provider.last_rewards)
        for left, right in zip(oracle.extract_task_robot_features(oracle_sim), self.provider.last_scheduler.extract_task_robot_features(self.provider.last_simulation)):
            assert torch.equal(left, right)
        self.parity_trace.append({"tick": self.sim.timestep, "native_assignments": expected_schedule.robot_assignments,
                                  "rewards_equal": True, "features_equal": True,
                                  "source_integrity_calls": self.provider.calls})
        self.sim.find_highest_non_idle_reward(self.provider.last_rewards)
        self.sim.assign_tasks_to_robots(self.provider.last_schedule)

    def execute_batch(self, assignments, tasks, *, scene):
        selected = {(self.task_index[a.task_id], self.robot_index[a.robot_id]) for a in assignments}
        native = {(t, r) for r, t in self.provider.last_schedule.robot_assignments.items() if 0 < t < self.sim.idle_task_id}
        assert selected == native  # no scheduler choice omitted/substituted
        start = self.sim.timestep
        self.compare_and_apply()
        completed = {}
        for _ in range(200):
            for a in assignments:
                if self.sim.tasks[self.task_index[a.task_id]].status == "DONE":
                    completed.setdefault(a.task_id, self.sim.timestep)
            if len(completed) == len(assignments):
                break
            self.sim.step()
        assert len(completed) == len(assignments)
        self.batch_trace.append({"start_tick": start, "end_tick": self.sim.timestep, "completed_at_tick": completed})
        return tuple(ExecutionUpdate(ExecutionFeedback(
            a.task_id, a.robot_id, "native_2d_task_done", timestamp_s=completed[a.task_id],
            world_changes={"evidence_scope": "upstream_2d_simulation_only", "native_status": "DONE"},
        ), DependencyOutcome.COMPLETED) for a in assignments)


def test_factory_run_orchestrated_persistent_native_feedback_loop(output):
    task = package()
    provider = build_native_adapter("sadcher", source_root=ROOT, checkpoint_path=COMPATIBLE)
    host = PersistentNativeHost(provider, task)
    result = TaskRunner(orchestration_executor=host, observation_provider=host.observe).run_orchestrated(
        TaskRunRequest(task), provider, replan=provider.replan, limits=OrchestrationLimits(max_rounds=8, max_dispatches=8),
    )
    assert result.termination_reason == "all_dependencies_completed"
    assert result.dispatch_count == 3 and result.rounds == 2
    assert result.task_states == {"t0": "completed", "t1": "completed", "t2": "completed"}
    assert result.verdict["status"] == "N/A"
    assert len(result.decisions) == 2 and all(d.provenance["source_integrity"] for d in result.decisions)
    assert all(host.sim.tasks[host.task_index[t]].status == "DONE" for t in result.task_states)
    # TaskRunner stops at work completion. Exit is explicit native environment
    # tail, not a fake EAI task or a fourth TaskRunner dispatch.
    work_complete_tick = host.sim.timestep
    done = replace(task, graph=replace(task.graph, tasks=tuple(replace(t, metadata={**t.metadata, "status": "DONE"}) for t in task.graph.tasks)))
    robots = host.observe(None).robots
    exit_decision = provider(done, robots)
    assert not exit_decision.assignments and len(exit_decision.provenance["end_robot_ids"]) == 3
    host.compare_and_apply()
    for _ in range(200):
        if host.sim.sim_done:
            break
        host.sim.step()
    assert host.sim.sim_done and host.sim.all_robots_at_exit_location(threshold=.01)
    write_json(output / "run_orchestrated_native_feedback.json", {
        "scope": "TaskRunner.run_orchestrated_with_persistent_upstream_2D_Simulation",
        "physical_success": None, "isaac_executed": False,
        "result": result.to_dict(), "per_decision_native_parity": host.parity_trace,
        "batch_trace": host.batch_trace, "work_completed_tick": work_complete_tick,
        "native_exit_tail": {"decision_provenance": exit_decision.provenance, "all_robots_at_exit": True,
                             "makespan": host.sim.makespan, "taskrunner_dispatch_count": 0},
        "cadence_limit": "batch executor waits for all dispatched tasks; per-state decisions match native, event-driven rollout trajectory is not claimed equal",
    })


def test_run_orchestrated_idle_remains_zero_dispatch_and_bounded(output):
    data, config = case_input("ur5_idle")
    path = output / "ur5_idle.task.json"
    write_json(path, data)
    task = EaiTaskPackage.from_json(path)
    provider = build_native_adapter("sadcher", source_root=ROOT, **config)
    class NeverDispatched:
        def execute_batch(self, *args, **kwargs):
            raise AssertionError("idle must not fabricate a physical dispatch")
    result = TaskRunner(orchestration_executor=NeverDispatched()).run_orchestrated(
        TaskRunRequest(task), provider, replan=provider.replan, limits=OrchestrationLimits(max_rounds=3, max_dispatches=3),
    )
    assert result.dispatch_count == 0 and not result.feedback
    assert result.termination_reason == "max_rounds_reached"
    assert result.task_states == {"pick_place": "pending"}
    assert all(d.provenance["idle_robot_ids"] == ["ur5_1"] for d in result.decisions)
    write_json(output / "ur5_idle.orchestrated.json", result.to_dict())


def test_run_orchestrated_coalition_rejected_before_executor(output):
    data, config = case_input("coalition_rejected")
    path = output / "coalition_rejected.task.json"
    write_json(path, data)
    task = EaiTaskPackage.from_json(path)
    provider = build_native_adapter("sadcher", source_root=ROOT, **config)
    with pytest.raises(NativeCoalitionScopeError) as exc:
        TaskRunner().run_orchestrated(TaskRunRequest(task), provider, replan=provider.replan)
    assert len(exc.value.decision.assignments) == 2
