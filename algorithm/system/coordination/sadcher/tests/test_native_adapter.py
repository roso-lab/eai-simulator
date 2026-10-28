"""Executed native tests; requires explicit upstream checkout and real weights.

HETEROCOORD_SADCHER_TEST_ROOT defaults to the adapter cache root. No fake modules,
mock scheduler, or random checkpoint is used as positive algorithm evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch

from algorithm.common_coordination.contract import (
    ContractError, CoordinationTask, ExecutionFeedback, RobotCapability, TaskGraph,
)
from algorithm.system.coordination.sadcher.native_adapter import (
    DEFAULT_CHECKPOINT, LEGACY_CHECKPOINT, DEFAULT_SOURCE_ROOT, CheckpointCompatibilityError,
    NativeCoalitionScopeError, NativeSadcherAdapter, audit_checkpoint,
)
from task.contracts import EaiTaskPackage, OrchestrationState, RobotState, TaskRunRequest
from task.runner import TaskRunner


ROOT = Path(os.getenv("HETEROCOORD_SADCHER_TEST_ROOT", str(DEFAULT_SOURCE_ROOT))).resolve()
COMPATIBLE = "imitation_learning/checkpoints/hyperparam_2_8t3r3s/best_checkpoint.pt"
SKILLS = ("terrain", "button", "sensor")


@pytest.fixture(scope="module", autouse=True)
def dependencies():
    if not (ROOT / COMPATIBLE).is_file():
        pytest.skip("native SADCHER source/checkpoint missing; native execution NOT verified")
    torch.set_num_threads(1)


def package():
    nodes = tuple(
        CoordinationTask(
            f"t{i}", f"native fixture task {i}", frozenset([skill]),
            prerequisites=("t0",) if i == 2 else (),
            metadata={"duration_s": 5 + i, "position": (10 + i * 5, 10, 0)},
        ) for i, skill in enumerate(SKILLS)
    )
    return EaiTaskPackage(
        "sadcher-native-fixture", "native-2d-simulation",
        TaskGraph(nodes, "explicit deterministic test fixture"),
        tuple(RobotState(RobotCapability(f"r{i}", frozenset([skill])), (0, 0, 0)) for i, skill in enumerate(SKILLS)),
        metadata={"sadcher": {"start_position": (0, 0, 0), "end_position": (30, 30, 0)}},
    )


def adapter():
    return NativeSadcherAdapter(ROOT, checkpoint_path=COMPATIBLE)


def independent_simulation():
    # Independently assembled original problem; does not call adapter translation.
    from simulation_environment.simulator_2D import Simulation
    locations = np.array([[0, 0], [10, 10], [15, 10], [20, 10], [30, 30]], dtype=float)
    return Simulation({
        "task_locations": locations, "T_e": np.array([0, 5, 6, 7, 0], dtype=float),
        "T_t": np.linalg.norm(locations[:, None, :] - locations[None, :, :], axis=-1),
        "Q": np.eye(3, dtype=bool),
        "R": np.concatenate([np.zeros((1, 3)), np.eye(3), np.zeros((1, 3))]).astype(bool),
        "precedence_constraints": [(1, 3)],
    }, scheduler_name="sadcher", use_idle=True)


def new_native_scheduler(sim):
    from schedulers.sadcher import SadcherScheduler
    native = SadcherScheduler(False, str(ROOT / COMPATIBLE), sim.duration_normalization, sim.location_normalization)
    audit_checkpoint(native, ROOT / COMPATIBLE)
    return native


def test_direct_native_equals_adapter_including_filters_idle_and_features():
    a, task = adapter(), package()
    counts = Counter()
    def profile(frame, event, arg):
        if event == "call" and str(ROOT) in frame.f_code.co_filename:
            counts[frame.f_code.co_name] += 1
    old_profile = sys.getprofile()
    sys.setprofile(profile)
    try:
        decision = a(task, task.robots)
    finally:
        sys.setprofile(old_profile)
    assert counts["calculate_robot_assignment"] == 1
    assert counts["solve"] == 1
    assert counts["filter_redundant_assignments"] == 1
    assert counts["filter_overassignments"] == 1
    sim = independent_simulation()
    native = new_native_scheduler(sim)
    rewards, schedule = native.calculate_robot_assignment(sim)
    assert schedule.robot_assignments == a.last_schedule.robot_assignments
    assert torch.equal(rewards, a.last_rewards)
    left = native.extract_task_robot_features(sim)
    right = a.last_scheduler.extract_task_robot_features(a.last_simulation)
    assert all(torch.equal(x, y) for x, y in zip(left, right))
    assert decision.provenance["idle_robot_ids"] == ["r2"]
    assert decision.provenance["native_assignments"] == {"0": 1, "1": 2, "2": 4}
    assert decision.provenance["task_adjacency"] == [[0, 0, 1], [0, 0, 0], [0, 0, 0]]
    assert [item.task_id for item in decision.assignments] == ["t0", "t1"]
    assert decision.provenance["checkpoint_audit"]["expected_keys"] == 116


def test_requested_legacy_checkpoint_is_rejected_before_inference():
    a, task = NativeSadcherAdapter(ROOT, checkpoint_path=LEGACY_CHECKPOINT), package()
    with pytest.raises(CheckpointCompatibilityError) as exc:
        a(task, task.robots)
    assert len(exc.value.audit["missing_keys"]) == 16
    assert len(exc.value.audit["unexpected_keys"]) == 84
    assert len(exc.value.audit["shape_mismatches"]) == 86
    assert a.last_schedule is None and a.last_scheduler is None


def test_default_checkpoint_is_complete_and_matches_explicit_official_benchmark(monkeypatch):
    monkeypatch.delenv("HETEROCOORD_SADCHER_CHECKPOINT", raising=False)
    task = package()
    default, explicit = NativeSadcherAdapter(ROOT), adapter()
    left, right = default(task, task.robots), explicit(task, task.robots)
    assert default.checkpoint_selection == "upstream_benchmark_default"
    assert left.provenance["checkpoint_audit"]["strict_complete"]
    assert left.provenance["checkpoint_audit"]["expected_keys"] == 116
    assert left.assignments == right.assignments
    assert torch.equal(default.last_rewards, explicit.last_rewards)
    monkeypatch.setenv("HETEROCOORD_SADCHER_CHECKPOINT", LEGACY_CHECKPOINT)
    env_adapter = NativeSadcherAdapter(ROOT)
    assert env_adapter.checkpoint_selection == "environment"
    with pytest.raises(CheckpointCompatibilityError):
        env_adapter(task, task.robots)
    assert NativeSadcherAdapter(ROOT, checkpoint_path=COMPATIBLE).checkpoint_selection == "explicit"


@pytest.mark.parametrize("bad", ["inactive", "skills", "far", "prerequisite"])
def test_invalid_progress_ownership_rejected(bad):
    task = package()
    node = replace(task.graph.tasks[0], metadata={**task.graph.tasks[0].metadata,
                   "status": "IN_PROGRESS", "remaining_duration_s": 2})
    robot = replace(task.robots[0], position=node.metadata["position"], metadata={"current_task_id": "t0"})
    if bad == "inactive":
        robot = replace(robot, capability=replace(robot.capability, active=False))
    elif bad == "skills":
        robot = replace(robot, capability=replace(robot.capability, capabilities=frozenset({"button"})))
    elif bad == "far":
        robot = replace(robot, position=(0, 0, 0))
    else:
        node = replace(node, prerequisites=("t1",))
    task = replace(task, graph=replace(task.graph, tasks=(node, *task.graph.tasks[1:])))
    with pytest.raises(ContractError):
        adapter().build_simulation(task, (robot, *task.robots[1:]))


@pytest.mark.parametrize("remaining", [1, float("inf"), float("nan"), -1, True])
def test_done_remaining_cannot_conceal_invalid_observation(remaining):
    task = package()
    node = replace(task.graph.tasks[0], metadata={**task.graph.tasks[0].metadata,
                   "status": "DONE", "remaining_duration_s": remaining})
    task = replace(task, graph=replace(task.graph, tasks=(node, *task.graph.tasks[1:])))
    with pytest.raises(ContractError):
        adapter().build_simulation(task, task.robots)


@pytest.mark.parametrize("bad", ["unknown_robot", "bad_position", "bool_position", "float32_overflow"])
def test_unrepresentable_runtime_roster_and_features(bad):
    task = package()
    robot = task.robots[0]
    if bad == "unknown_robot":
        robot = replace(robot, capability=replace(robot.capability, robot_id="unknown"))
    else:
        # RobotState is also a boundary: use valid numeric construction for
        # overflow and call the adapter's coordinate parser for malformed data.
        if bad in ("bad_position", "bool_position"):
            from algorithm.system.coordination.sadcher.native_adapter import _position
            with pytest.raises(ContractError):
                _position(("bad", 0) if bad == "bad_position" else (True, 0), "robot")
            return
        robot = replace(robot, position=(1e100, 0, 0))
    with pytest.raises(ContractError):
        adapter().build_simulation(task, (robot, *task.robots[1:]))


def test_actual_native_premature_exit_is_retained_without_completion():
    from algorithm.system.coordination.sadcher.native_adapter import NativePrematureExitError
    task = package()
    node = replace(task.graph.tasks[0], metadata={**task.graph.tasks[0].metadata,
                   "status": "IN_PROGRESS", "remaining_duration_s": 2})
    robot = replace(task.robots[0], position=node.metadata["position"], metadata={"current_task_id": "t0"})
    task = replace(task, graph=TaskGraph((node,), "native premature exit fixture"),
                   robots=(robot, *task.robots[1:]))
    a = adapter()
    with pytest.raises(NativePrematureExitError) as exc:
        a(task, task.robots)
    assert a.last_simulation.tasks[1].status == "IN_PROGRESS"
    assert exc.value.decision.provenance["end_robot_ids"] == ["r1", "r2"]
    assert not exc.value.decision.assignments


@pytest.mark.parametrize("bad", ["unknown", "duplicate", "overlap", "missing", "no_feedback", "failed_feedback", "wrong_robot", "duplicate_feedback", "unknown_feedback"])
def test_replan_rejects_inconsistent_completion(bad):
    from algorithm.common_coordination.contract import FailureCode
    task, a = package(), adapter()
    decision = a(task, task.robots)
    feedback = (ExecutionFeedback("t0", "r0", "done"),)
    state = OrchestrationState(1, ("t0",), (), (), ("t1", "t2"), feedback, decision)
    if bad == "unknown": state = replace(state, completed_task_ids=("unknown",))
    elif bad == "duplicate": state = replace(state, completed_task_ids=("t0", "t0"))
    elif bad == "overlap": state = replace(state, pending_task_ids=("t0", "t1", "t2"))
    elif bad == "missing": state = replace(state, pending_task_ids=("t2",))
    elif bad == "no_feedback": state = replace(state, feedback=())
    elif bad == "failed_feedback": state = replace(state, feedback=(replace(feedback[0], failure_code=FailureCode.MILESTONE_UNVERIFIED),))
    elif bad == "wrong_robot": state = replace(state, feedback=(replace(feedback[0], robot_id="r1"),))
    elif bad == "duplicate_feedback": state = replace(state, feedback=feedback * 2)
    else: state = replace(state, feedback=(replace(feedback[0], task_id="unknown"),))
    with pytest.raises(ContractError):
        a.replan(task, task.robots, state)


def test_replan_clears_completed_remaining_duration_and_checks_live_pending():
    a, task = adapter(), package()
    decision = a(task, task.robots)
    nodes = tuple(replace(n, metadata={**n.metadata, "remaining_duration_s": n.metadata["duration_s"]}) for n in task.graph.tasks)
    task = replace(task, graph=replace(task.graph, tasks=nodes))
    state = OrchestrationState(1, ("t0", "t1"), (), (), ("t2",),
                              (ExecutionFeedback("t0", "r0", "done"), ExecutionFeedback("t1", "r1", "done")), decision)
    # This deliberately retains start positions after completion; the native
    # matcher emits a coalition. Inspect translated state, not dispatch success.
    with pytest.raises(NativeCoalitionScopeError):
        a.replan(task, task.robots, state)
    assert [t.remaining_duration for t in a.last_simulation.tasks[1:3]] == [0, 0]
    live = {n.task_id: {"status": "DONE", "remaining_duration_s": 0} for n in nodes}
    task = replace(task, metadata={**task.metadata, "runtime_observation": {"objects": {"native_tasks": live}}})
    with pytest.raises(ContractError, match="pending dependency feedback conflicts"):
        a.replan(task, task.robots, state)


def test_checkpoint_audit_observes_without_reloading_or_changing_outputs():
    a, task = adapter(), package()
    sim = a.build_simulation(task, task.robots)
    from schedulers.sadcher import SadcherScheduler
    scheduler = SadcherScheduler(False, str(ROOT / COMPATIBLE), sim.duration_normalization, sim.location_normalization)
    before = {k: v.clone() for k, v in scheduler.trained_model.state_dict().items()}
    rewards_before, schedule_before = scheduler.calculate_robot_assignment(sim)
    calls = []
    def profile(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "load_state_dict":
            calls.append(frame.f_code.co_filename)
    prior = sys.getprofile()
    sys.setprofile(profile)
    try:
        audit = audit_checkpoint(scheduler, ROOT / COMPATIBLE)
    finally:
        sys.setprofile(prior)
    rewards_after, schedule_after = scheduler.calculate_robot_assignment(sim)
    assert not calls and audit["audit_mutates_model"] is False
    assert all(torch.equal(before[k], v) for k, v in scheduler.trained_model.state_dict().items())
    assert torch.equal(rewards_before, rewards_after)
    assert schedule_before.robot_assignments == schedule_after.robot_assignments


def test_checkpoint_audit_rejects_loaded_drift_without_repair():
    a, task = adapter(), package()
    scheduler = a.create_scheduler(a.build_simulation(task, task.robots))
    with torch.no_grad():
        scheduler.trained_model.robot_embedding.weight[0, 0] += 1
    changed = scheduler.trained_model.robot_embedding.weight.detach().clone()
    with pytest.raises(RuntimeError, match="native-loaded state differs"):
        audit_checkpoint(scheduler, ROOT / COMPATIBLE)
    assert torch.equal(scheduler.trained_model.robot_embedding.weight, changed)


def test_live_observation_progress_matches_actual_native_state():
    a, task = adapter(), package()
    a.build_simulation(task, task.robots)
    sim = independent_simulation()
    scheduler = new_native_scheduler(sim)
    rewards, schedule = scheduler.calculate_robot_assignment(sim)
    sim.find_highest_non_idle_reward(rewards)
    sim.assign_tasks_to_robots(schedule)
    for _ in range(17):
        sim.step()
    assert sim.tasks[1].status == "IN_PROGRESS"
    names = ["__start__", "t0", "t1", "t2", "__idle__", "__end__"]
    robots = tuple(replace(state, position=(*robot.location.tolist(), 0.), metadata={
        "current_task_id": names[robot.current_task.task_id] if robot.current_task else None,
    }) for state, robot in zip(task.robots, sim.robots))
    live = {names[t.task_id]: {"status": t.status, "remaining_duration_s": t.remaining_duration} for t in sim.tasks}
    bound = replace(task, robots=robots, metadata={**task.metadata, "runtime_observation": {"objects": {"native_tasks": live}}})
    adapted = a.build_simulation(bound, robots)
    assert adapted.tasks[1].status == sim.tasks[1].status
    assert adapted.tasks[1].remaining_duration == sim.tasks[1].remaining_duration
    for x, y in zip(scheduler.extract_task_robot_features(sim), scheduler.extract_task_robot_features(adapted)):
        assert torch.equal(x, y)
    a(bound, robots)
    expected_rewards, expected_schedule = scheduler.calculate_robot_assignment(sim)
    assert torch.equal(a.last_rewards, expected_rewards)
    assert a.last_schedule.robot_assignments == expected_schedule.robot_assignments
    state = OrchestrationState(1, ("t0",), (), (), ("t1", "t2"), (), None)
    with pytest.raises(ContractError, match="feedback conflicts"):
        a.replan(bound, robots, state)


def test_done_coincident_task_does_not_require_unused_neural_distance_normalization():
    a, task = adapter(), package()
    node = replace(task.graph.tasks[0], metadata={**task.graph.tasks[0].metadata, "status": "DONE"})
    robot = replace(task.robots[0], position=tuple(node.metadata["position"]))
    task = replace(task, graph=TaskGraph((node,), "terminal coincident fixture"), robots=(robot,))
    decision = a(task, task.robots)
    assert decision.provenance["end_robot_ids"] == ["r0"]
    assert not decision.assignments


@pytest.mark.parametrize("corruption", ["missing", "unexpected", "shape", "nonfinite"])
def test_incomplete_or_corrupted_real_checkpoint_fails_closed(tmp_path, corruption):
    original = torch.load(ROOT / COMPATIBLE, weights_only=True, map_location="cpu")
    if corruption == "missing":
        original.pop("robot_embedding.weight")
    elif corruption == "unexpected":
        original["unsupported_layer"] = original["robot_embedding.weight"]
    elif corruption == "shape":
        original["robot_embedding.weight"] = original["robot_embedding.weight"][:1]
    else:
        original["robot_embedding.weight"][0, 0] = float("nan")
    checkpoint = tmp_path / "invalid.pt"
    torch.save(original, checkpoint)
    a, task = NativeSadcherAdapter(ROOT, checkpoint_path=checkpoint), package()
    with pytest.raises(CheckpointCompatibilityError):
        a(task, task.robots)
    assert a.last_schedule is None


def test_explicit_status_remaining_workload_and_inactive_robot():
    a, task = adapter(), package()
    first = replace(task.graph.tasks[0], metadata={**task.graph.tasks[0].metadata, "status": "IN_PROGRESS", "remaining_duration_s": 2})
    task = replace(task, graph=replace(task.graph, tasks=(first, *task.graph.tasks[1:])))
    robots = (replace(task.robots[0], position=first.metadata["position"], metadata={"current_task_id": "t0"}), replace(task.robots[1], capability=replace(task.robots[1].capability, active=False)), task.robots[2])
    sim = a.build_simulation(task, robots)
    assert not sim.robots[0].available and sim.robots[0].remaining_workload == 2
    assert sim.robots[0].current_task is sim.tasks[1]
    assert sim.tasks[1].assigned and sim.tasks[1].status == "IN_PROGRESS"
    assert not sim.robots[1].available and not sim.tasks[3].ready
    d = a(task, robots)
    assert "0" not in d.provenance["native_assignments"]
    assert "1" not in d.provenance["native_assignments"]


@pytest.mark.parametrize("bad", ["missing_duration", "unsupported_skill", "missing_position", "nonplanar", "missing_sentinels", "busy_without_remaining"])
def test_unrepresentable_inputs_rejected(bad):
    a, task = adapter(), package()
    node = task.graph.tasks[0]
    metadata = dict(node.metadata)
    if bad == "missing_duration": metadata.pop("duration_s")
    if bad == "missing_position": metadata.pop("position")
    if bad == "nonplanar": metadata["position"] = (10, 10, 2)
    if bad == "busy_without_remaining": metadata["status"] = "IN_PROGRESS"
    if bad == "unsupported_skill": node = replace(node, required_capabilities=frozenset({"fourth_skill"}))
    task = replace(task, graph=replace(task.graph, tasks=(replace(node, metadata=metadata), *task.graph.tasks[1:])))
    if bad == "missing_sentinels": task = replace(task, metadata={})
    with pytest.raises(ContractError): a.build_simulation(task, task.robots)


def test_full_coalition_is_retained_and_current_runner_scope_is_explicit():
    a, task = adapter(), package()
    node = replace(task.graph.tasks[0], required_capabilities=frozenset({"terrain", "button"}))
    task = replace(task, graph=TaskGraph((node,), "coalition fixture"))
    with pytest.raises(NativeCoalitionScopeError) as exc:
        a(task, task.robots)
    decision = exc.value.decision
    assert decision.provenance["coalitions"] == {"t0": ["r0", "r1"]}
    assert len(decision.assignments) == 2
    assert not decision.provenance["task_runner_compatible"]
    assert decision.provenance["scope_rejection"] == {
        "status": "N/A",
        "reason": "benchmark_contract_cannot_represent_native_coalition",
    }
    assert exc.value.na_reason == "benchmark_contract_cannot_represent_native_coalition"
    with pytest.raises(ValueError, match="assigns a task more than once"):
        TaskRunner().run(TaskRunRequest(task), lambda *_: decision)


def test_completed_tasks_preserve_end_sentinel_output():
    a, task = adapter(), package()
    nodes = tuple(replace(node, metadata={**node.metadata, "status": "DONE"}) for node in task.graph.tasks)
    task = replace(task, graph=replace(task.graph, tasks=nodes))
    decision = a(task, task.robots)
    assert not decision.assignments and not decision.unassigned_tasks
    assert decision.provenance["end_robot_ids"] == ["r0", "r1", "r2"]


def test_current_idle_sentinel_retains_native_availability():
    a, task = adapter(), package()
    robots = tuple(replace(robot, metadata={"current_task_id": "__idle__"}) for robot in task.robots)
    sim = a.build_simulation(task, robots)
    assert all(robot.current_task is sim.tasks[sim.idle_task_id] for robot in sim.robots)
    assert all(robot.available and robot.remaining_workload == 0 for robot in sim.robots)
    assert not sim.tasks[sim.idle_task_id].assigned


def test_replan_does_not_invent_failure_recovery_semantics():
    task = package()
    with pytest.raises(ContractError, match="no native SADCHER status"):
        adapter().replan(task, task.robots, OrchestrationState(
            round_index=1, completed_task_ids=(), failed_task_ids=("t0",),
            blocked_task_ids=("t2",), pending_task_ids=("t1",), feedback=(), last_decision=None,
        ))


def test_legacy_entrypoint_has_no_fallback():
    from algorithm.system.coordination.sadcher import assign, condition_record
    with pytest.raises(ContractError, match="legacy SADCHER AssignmentRequest adapter is disabled"):
        assign(object())
    assert condition_record().execution_scope == "not_executable_legacy_contract_incomplete"


def test_requested_ur5_pickplace_input_preserves_native_idle():
    robot = RobotState(RobotCapability("ur5_1", frozenset({"manipulate"})), (0, 0, 0))
    node = CoordinationTask("pick_place", "Pick and place", frozenset({"manipulate"}), metadata={"position": (0.45, 0.05, 0), "duration_s": 30})
    task = EaiTaskPackage("sadcher-ur5-physical-input-smoke", "parent-owned-Isaac", TaskGraph((node,), "explicit user input"), (robot,), metadata={"sadcher": {"start_position": (0, 0, 0), "end_position": (0, 0, 0)}})
    a = NativeSadcherAdapter(ROOT, checkpoint_path=COMPATIBLE, skill_names=("manipulate", "inspect", "transport"))
    run = TaskRunner().run(TaskRunRequest(task), a)
    assert not run.decision.assignments
    assert run.decision.unassigned_tasks == ("pick_place",)
    assert run.decision.provenance["idle_robot_ids"] == ["ur5_1"]
    from simulation_environment.simulator_2D import Simulation
    locations = np.array([[0., 0.], [.45, .05], [0., 0.]])
    sim = Simulation({
        "task_locations": locations, "T_e": np.array([0., 30., 0.]),
        "T_t": np.linalg.norm(locations[:, None, :] - locations[None, :, :], axis=-1),
        "Q": np.array([[True, False, False]]),
        "R": np.array([[False, False, False], [True, False, False], [False, False, False]]),
        "precedence_constraints": [],
    }, scheduler_name="sadcher", use_idle=True)
    direct_rewards, direct = new_native_scheduler(sim).calculate_robot_assignment(sim)
    assert direct.robot_assignments == a.last_schedule.robot_assignments == {0: 2}
    assert torch.equal(direct_rewards, a.last_rewards)
    output = os.getenv("HETEROCOORD_SADCHER_EVIDENCE_DIR")
    if output:
        Path(output).mkdir(parents=True, exist_ok=True)
        (Path(output) / "ur5_pickplace_native_decision.json").write_text(json.dumps({
            "input": {"robot_id": "ur5_1", "robot_capabilities": ["manipulate"], "required_capabilities": ["manipulate"], "skill_names": list(a.skill_names), "robot_position": [0, 0, 0], "task_position": [.45, .05, 0], "duration_s": 30, "start_position": [0, 0, 0], "end_position": [0, 0, 0]},
            "decision_provenance": dict(run.decision.provenance),
            "predicted_rewards_including_sentinels": a.last_rewards.tolist(),
            "independent_native_parity": True,
            "run": run.to_dict(), "physical_smoke_status": "not_dispatched_native_selected_idle",
        }, indent=2) + "\n")


def test_full_original_simulation_completes_tasks_and_exit_without_timeout():
    # Initialize imports, then run the native simulation loop independently.
    adapter().build_simulation(package(), package().robots)
    sim = independent_simulation()
    scheduler = new_native_scheduler(sim)
    trace = []
    for _ in range(100):
        if sim.sim_done:
            break
        rewards, schedule = scheduler.calculate_robot_assignment(sim)
        trace.append({"timestep": sim.timestep, "assignments": schedule.robot_assignments})
        sim.find_highest_non_idle_reward(rewards)
        sim.assign_tasks_to_robots(schedule)
        sim.step_until_next_decision_point()
    assert sim.sim_done
    assert all(t.status == "DONE" for t in sim.tasks)
    assert sim.all_robots_at_exit_location(threshold=0.01)
    assert sim.makespan < sim.worst_case_makespan
    output = os.getenv("HETEROCOORD_SADCHER_EVIDENCE_DIR")
    if output:
        target = Path(output)
        target.mkdir(parents=True, exist_ok=True)
        (target / "original_simulation_rollout.json").write_text(json.dumps({
            "scope": "original_SADCHER_2D_simulation_only", "physical_success": "N/A",
            "decision_trace": trace, "makespan": sim.makespan,
            "worst_case_makespan": float(sim.worst_case_makespan),
            "task_statuses_including_sentinels": [t.status for t in sim.tasks],
            "all_robots_at_exit": bool(sim.all_robots_at_exit_location(threshold=0.01)),
            "robot_schedules": sim.robot_schedules,
        }, indent=2) + "\n")


class Native2DExecutor:
    """Only the upstream 2D simulator supplies these bounded fixture outcomes."""
    def __init__(self, a):
        self.adapter = a
        self.applied = False

    def execute(self, assignment, task, *, scene):
        sim = self.adapter.last_simulation
        if not self.applied:
            sim.find_highest_non_idle_reward(self.adapter.last_rewards)
            sim.assign_tasks_to_robots(self.adapter.last_schedule)
            self.applied = True
        index = int(task.task_id[1:]) + 1
        for _ in range(200):
            if sim.tasks[index].status == "DONE":
                break
            sim.step()
        assert sim.tasks[index].status == "DONE"
        return ExecutionFeedback(
            task.task_id, assignment.robot_id, "native_2d_task_done", timestamp_s=sim.timestep,
            world_changes={"evidence_scope": "upstream_2d_simulation_only", "native_status": sim.tasks[index].status},
        )


def test_taskrunner_feedback_reentry_matches_direct_native_and_records_evidence():
    a, task = adapter(), package()
    before = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*.py")}
    run = TaskRunner(executor=Native2DExecutor(a)).run(TaskRunRequest(task), a)
    assert len(run.feedback) == 2
    assert all(x.outcome == "native_2d_task_done" for x in run.feedback)
    assert run.verdict["status"] == "N/A"
    assert a.last_simulation.tasks[3].ready
    first_sim = a.last_simulation
    first_schedule = a.last_schedule.robot_assignments.copy()
    direct = independent_simulation()
    scheduler = new_native_scheduler(direct)
    rewards, schedule = scheduler.calculate_robot_assignment(direct)
    direct.find_highest_non_idle_reward(rewards)
    direct.assign_tasks_to_robots(schedule)
    for _ in range(first_sim.timestep): direct.step()
    assert [t.status for t in direct.tasks] == [t.status for t in first_sim.tasks]
    assert all(np.array_equal(r.location, q.location) for r, q in zip(direct.robots, first_sim.robots))
    # Re-enter via actual current status and coordinates after TaskRunner feedback.
    nodes = tuple(replace(node, metadata={**node.metadata, "status": native.status, "remaining_duration_s": native.remaining_duration}) for node, native in zip(task.graph.tasks, first_sim.tasks[1:-2]))
    robots = tuple(replace(state, position=(*native.location.tolist(), 0.0), metadata={"current_task_id": None}) for state, native in zip(task.robots, first_sim.robots))
    next_task = replace(task, graph=replace(task.graph, tasks=nodes), robots=robots)
    next_decision = a.replan(task, robots, OrchestrationState(
        round_index=1, completed_task_ids=("t0", "t1"), failed_task_ids=(),
        blocked_task_ids=(), pending_task_ids=("t2",), feedback=run.feedback, last_decision=run.decision,
    ))
    _, direct_next = scheduler.calculate_robot_assignment(direct)
    assert a.last_schedule.robot_assignments == direct_next.robot_assignments
    assert {x.task_id for x in next_decision.assignments} == {"t2"}
    second_run = TaskRunner(executor=Native2DExecutor(a)).run(TaskRunRequest(next_task), a)
    assert len(second_run.feedback) == 1
    assert second_run.feedback[0].task_id == "t2"
    after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.rglob("*.py")}
    assert before == after
    output = os.getenv("HETEROCOORD_SADCHER_EVIDENCE_DIR")
    if output:
        target = Path(output)
        target.mkdir(parents=True, exist_ok=True)
        evidence = {
            "source_reference": run.decision.condition.source_reference,
            "source_root": str(ROOT), "source_python_sha256": before,
            "source_python_unchanged": True, "checkpoint_audit": a.last_checkpoint_audit,
            "evidence_scope": {"contract": "passed", "native": "executed_exact_parity", "simulation": "upstream_2d_fixture", "EAI_PhysX": "not_executed", "physical_success": "N/A"},
            "independent_native_first_schedule": first_schedule,
            "independent_native_feedback_reentry": direct_next.robot_assignments,
            "first_run": run.to_dict(), "second_run": second_run.to_dict(),
        }
        (target / "native_parity_and_feedback.json").write_text(json.dumps(evidence, indent=2) + "\n")
