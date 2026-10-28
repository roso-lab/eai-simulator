"""I/O boundary for the unchanged upstream SADCHER scheduler.

Task metadata: ``duration_s`` and ``position`` are required; ``status`` defaults
to PENDING, and IN_PROGRESS requires ``remaining_duration_s``. Robot position
is required; metadata may contain ``current_task_id`` and ``available``.
Package metadata ``sadcher`` must supply ``start_position`` and ``end_position``.
Coordinates must share a plane, time is in seconds, native speed is 1 unit/s.
Exactly three named binary skills are supported; their order is explicit.

No registry changes, upstream patches, approximate matching, inferred durations,
or checkpoint substitution. Native coalitions remain multiple Assignment records
on NativeCoalitionScopeError.decision; no coalition subset is dispatched.
"""

from __future__ import annotations

import hashlib
import io
import importlib
import math
import os
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, ContractError, FailureCode
from task.contracts import AlgorithmDecision, EaiTaskPackage, OrchestrationState, RobotState


DEFAULT_SOURCE_ROOT = Path("/home/airs/.cache/heterocoord/upstream/sadcher")
LEGACY_CHECKPOINT = "imitation_learning/checkpoints/8t3r3s_models/model_0/best_checkpoint.pt"
# Same complete IL checkpoint selected by upstream benchmarking/benchmark_schedulers.py.
# Explicit caller/environment checkpoints are never replaced on load failure.
DEFAULT_CHECKPOINT = "imitation_learning/checkpoints/hyperparam_2_8t3r3s/best_checkpoint.pt"
SOURCE_REFERENCE = "jakbichler/Sadcher@a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae"


class CheckpointCompatibilityError(RuntimeError):
    def __init__(self, audit: dict[str, Any]):
        self.audit = audit
        super().__init__(
            "SADCHER checkpoint rejected: "
            f"{len(audit['missing_keys'])} missing, {len(audit['unexpected_keys'])} unexpected, "
            f"{len(audit['shape_mismatches'])} shape mismatches; no native inference executed"
        )


class NativeCoalitionScopeError(ContractError):
    """A lossless native decision that the current TaskRunner cannot dispatch."""

    NA_REASON = "benchmark_contract_cannot_represent_native_coalition"

    def __init__(self, decision: AlgorithmDecision):
        self.decision = decision
        self.native_assignments = decision.provenance["native_assignments"]
        self.na_reason = self.NA_REASON
        super().__init__("native SADCHER coalition cannot map to single-owner TaskRunner; full decision retained on exception.decision")


class NativePrematureExitError(ContractError):
    """Retain upstream exit output when real tasks are still in progress."""
    def __init__(self, decision):
        self.decision = decision
        super().__init__("native SADCHER selected exit before all real tasks completed; no completion inferred")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_checkpoint(scheduler: Any, checkpoint: Path) -> dict[str, Any]:
    """Audit externally after upstream construction, before any inference.

    Upstream's permissive loader is not changed. All expected state must exist,
    every shape must match, and every loaded tensor must equal the checkpoint.
    An incompatible construction can never reach calculate_robot_assignment.
    """
    import torch

    checkpoint_bytes = checkpoint.read_bytes()
    payload = torch.load(io.BytesIO(checkpoint_bytes), map_location="cpu", weights_only=True)
    state = payload.get("state_dict", payload).get("policy", payload)
    state = {k.removeprefix("scheduler_net."): v for k, v in state.items()}
    expected = scheduler.trained_model.state_dict()
    shared = set(expected) & set(state)
    audit = {
        "checkpoint": str(checkpoint), "sha256": hashlib.sha256(checkpoint_bytes).hexdigest(),
        "expected_keys": len(expected), "checkpoint_keys": len(state),
        "missing_keys": sorted(set(expected) - set(state)),
        "unexpected_keys": sorted(set(state) - set(expected)),
        "shape_mismatches": {
            k: {"expected": list(expected[k].shape), "checkpoint": list(state[k].shape)}
            for k in sorted(shared) if expected[k].shape != state[k].shape
        },
        "nonfinite_keys": sorted(k for k in state if not torch.isfinite(state[k]).all()),
    }
    if any(audit[k] for k in ("missing_keys", "unexpected_keys", "shape_mismatches", "nonfinite_keys")):
        raise CheckpointCompatibilityError(audit)
    # Observe native-loaded values, never repair them. A second load here could
    # conceal changed checkpoint bytes or an incorrectly loaded native model.
    mismatches = sorted(k for k, v in expected.items()
                        if v.dtype != state[k].dtype or not torch.equal(v.detach().cpu(), state[k]))
    if mismatches:
        raise RuntimeError(f"SADCHER native-loaded state differs from the complete checkpoint: {mismatches}")
    audit["strict_complete"] = True
    audit["audit_mutates_model"] = False
    return audit


def _native_imports(root: Path):
    if not (root / "schedulers/sadcher.py").is_file():
        raise FileNotFoundError(f"SADCHER source unavailable: {root}")
    # The upstream `models` directory is a namespace package. Keep its source
    # path installed for subsequent imports; never create substitute modules.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    prefixes = ("models", "schedulers", "helper_functions", "simulation_environment")
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] not in prefixes:
            continue
        origin = getattr(module, "__file__", None)
        paths = list(getattr(module, "__path__", ()))
        valid = Path(origin).resolve().is_relative_to(root) if origin else any(Path(p).resolve().is_relative_to(root) for p in paths)
        if not valid:
            raise RuntimeError(f"SADCHER module collision: {name}; use an isolated process")
    old_path = sys.path[:]
    try:
        scheduler = importlib.import_module("schedulers.sadcher").SadcherScheduler
        simulation = importlib.import_module("simulation_environment.simulator_2D").Simulation
    finally:
        sys.path[:] = old_path
    return scheduler, simulation


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ContractError(f"{label} must be finite numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label} must be finite numeric") from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ContractError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _position(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) not in (2, 3):
        raise ContractError(f"{label}: explicit 2D/3D position required")
    if any(isinstance(x, bool) for x in value):
        raise ContractError(f"{label}: finite numeric coordinates required")
    try:
        result = tuple(float(x) for x in value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{label}: finite numeric coordinates required") from exc
    if not all(math.isfinite(x) for x in result):
        raise ContractError(f"{label}: finite position required")
    return result if len(result) == 3 else (*result, 0.0)


class NativeSadcherAdapter:
    def __init__(
        self, source_root: str | Path | None = None, *,
        checkpoint_path: str | Path | None = None,
        skill_names: Sequence[str] = ("terrain", "button", "sensor"),
    ):
        self.source_root = Path(source_root or os.getenv("HETEROCOORD_SADCHER_ROOT") or DEFAULT_SOURCE_ROOT).expanduser().resolve()
        selected = checkpoint_path or os.getenv("HETEROCOORD_SADCHER_CHECKPOINT") or DEFAULT_CHECKPOINT
        self.checkpoint_path = Path(selected).expanduser()
        if not self.checkpoint_path.is_absolute():
            self.checkpoint_path = self.source_root / self.checkpoint_path
        self.checkpoint_path = self.checkpoint_path.resolve()
        self.skill_names = tuple(skill_names)
        if len(self.skill_names) != 3 or len(set(self.skill_names)) != 3 or any(not isinstance(x, str) or not x for x in self.skill_names):
            raise ContractError("SADCHER checkpoint supports exactly three distinct named binary skills")
        self.last_simulation = None
        self.last_scheduler = None
        self.last_checkpoint_audit = None
        self.last_schedule = None
        self.last_rewards = None
        self.checkpoint_selection = ("explicit" if checkpoint_path else "environment"
                                     if os.getenv("HETEROCOORD_SADCHER_CHECKPOINT") else "upstream_benchmark_default")

    def _skills(self, values):
        unknown = set(values) - set(self.skill_names)
        if unknown:
            raise ContractError(f"unsupported SADCHER skill dimensions: {sorted(unknown)}")
        return [name in values for name in self.skill_names]

    @staticmethod
    def _live_tasks(task: EaiTaskPackage):
        """Explicit SADCHER observation vocabulary, never inferred from text."""
        observation = task.metadata.get("runtime_observation", {})
        live = observation.get("objects", {}).get("native_tasks")
        if live is None:
            return None
        if not isinstance(live, dict):
            raise ContractError("runtime_observation.objects.native_tasks must be a mapping")
        ids = {node.task_id for node in task.graph.tasks}
        if not ids <= set(live) or set(live) - ids - {"__start__", "__idle__", "__end__"}:
            raise ContractError("native_tasks observation must cover the full real task roster")
        for task_id in ids:
            entry = live[task_id]
            if not isinstance(entry, dict) or not {"status", "remaining_duration_s"} <= set(entry):
                raise ContractError(f"{task_id}: native_tasks requires explicit status and remaining_duration_s")
        return live

    def build_simulation(self, task: EaiTaskPackage, robots: Sequence[RobotState]):
        """Create actual upstream Simulation/Task/Robot records, preserving DAG."""
        import numpy as np

        if not task.graph.valid or not robots:
            raise ContractError("SADCHER needs a valid graph and nonempty robot roster")
        ids = [robot.capability.robot_id for robot in robots]
        if len(ids) != len(set(ids)):
            raise ContractError("duplicate SADCHER robot IDs")
        if set(ids) != {r.capability.robot_id for r in task.robots}:
            raise ContractError("runtime robot IDs differ from task package")
        config = task.metadata.get("sadcher", {})
        nodes = task.graph.tasks
        live_tasks = self._live_tasks(task)
        metadata = {node.task_id: {**node.metadata, **({k: live_tasks[node.task_id][k] for k in ("status", "remaining_duration_s")} if live_tasks is not None else {})} for node in nodes}
        index = {node.task_id: i + 1 for i, node in enumerate(nodes)}
        if set(index) & {"__start__", "__idle__", "__end__"}:
            raise ContractError("task IDs collide with native SADCHER sentinel names")
        positions = [_position(config.get("start_position"), "start_position")]
        positions += [_position(node.metadata.get("position"), node.task_id) for node in nodes]
        positions += [_position(config.get("end_position"), "end_position")]
        robot_positions = [_position(robot.position, ids[i]) for i, robot in enumerate(robots)]
        if len({point[2] for point in positions + robot_positions}) != 1:
            raise ContractError("SADCHER supports a common 2D plane; nonplanar positions unsupported")
        xy = np.array([point[:2] for point in positions], dtype=float)
        if np.max(xy) <= 0:
            raise ContractError("upstream location normalization requires positive max task coordinate")
        all_done = all(metadata[node.task_id].get("status", "PENDING") == "DONE" for node in nodes)
        if not all_done and not any(np.linalg.norm(np.array(r[:2]) - p) > 0 for r in robot_positions for p in xy[1:-1]):
            raise ContractError("upstream relative-distance normalization is undefined for coincident positions")
        durations = [0.0] + [_number(node.metadata.get("duration_s"), f"{node.task_id}.duration_s", positive=True) for node in nodes] + [0.0]
        problem = {
            "task_locations": xy, "T_e": np.array(durations),
            "T_t": np.linalg.norm(xy[:, None, :] - xy[None, :, :], axis=-1),
            "Q": np.array([self._skills(robot.capability.capabilities) for robot in robots], dtype=bool),
            "R": np.array([[False] * 3] + [self._skills(node.required_capabilities) for node in nodes] + [[False] * 3], dtype=bool),
            "precedence_constraints": [(index[p], index[node.task_id]) for node in nodes for p in node.prerequisites],
        }
        _, simulation_type = _native_imports(self.source_root)
        sim = simulation_type(problem, scheduler_name="sadcher", use_idle=True)
        for node, native in zip(nodes, sim.tasks[1:-2]):
            fields = metadata[node.task_id]
            status = fields.get("status", "PENDING")
            if status not in ("PENDING", "IN_PROGRESS", "DONE"):
                raise ContractError(f"{node.task_id}: unsupported native status {status}")
            if status == "DONE" and "remaining_duration_s" in fields and _number(
                    fields["remaining_duration_s"], f"{node.task_id}.remaining_duration_s") != 0:
                raise ContractError(f"{node.task_id}: DONE requires zero remaining duration")
            native.status = status
            native.incomplete = status != "DONE"
            native.remaining_duration = 0.0 if status == "DONE" else _number(
                fields.get("remaining_duration_s", None if status == "IN_PROGRESS" else native.overall_duration),
                f"{node.task_id}.remaining_duration_s", positive=True,
            )
            if native.remaining_duration > native.overall_duration:
                raise ContractError(f"{node.task_id}: remaining duration exceeds overall duration")
        for state, native, position in zip(robots, sim.robots, robot_positions):
            if state.metadata.get("speed", 1.0) != 1.0:
                raise ContractError("SADCHER environment assumes speed=1 coordinate unit per second")
            native.location = np.array(position[:2])
            current = state.metadata.get("current_task_id")
            if current is not None:
                current_index = {**index, "__start__": 0, "__idle__": sim.idle_task_id, "__end__": sim.last_task_id}
                if current not in current_index:
                    raise ContractError(f"unknown current_task_id: {current}")
                native.current_task = sim.tasks[current_index[current]]
            native.check_task_status(sim.idle_task_id)
            if native.current_task is not None:
                native.current_task.assigned = native.current_task.task_id != sim.idle_task_id
            explicit_available = state.metadata.get("available", native.available)
            if not isinstance(explicit_available, bool) or (explicit_available and not native.available):
                raise ContractError("available must be boolean and cannot override an active current task")
            native.available = state.capability.active and explicit_available
        sim.update_task_status()
        for native in sim.tasks[1:-2]:
            if native.status != "DONE" and native.assigned and not native.ready:
                raise ContractError("owned task requires completed predecessors")
            if native.status == "IN_PROGRESS" and (not native.ready or not native.assigned):
                raise ContractError("IN_PROGRESS requires completed predecessors and explicit current_task_id robot ownership")
            owners = [i for i, robot in enumerate(sim.robots) if robot.current_task is native]
            if any(not robots[i].capability.active for i in owners):
                raise ContractError("inactive robot cannot own an unfinished native task")
            if native.status == "IN_PROGRESS" and not np.all(
                    np.any(np.array([sim.robots[i].capabilities for i in owners]), axis=0)
                    >= native.requirements):
                raise ContractError("IN_PROGRESS owner team does not cover required skills")
            if native.status == "IN_PROGRESS" and not sim.all_robots_at_task(native, threshold=0.01):
                raise ContractError("IN_PROGRESS requires capable owners at the native task location")
        # Validate representability without altering upstream normalization.
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            features = np.array([t.feature_vector(sim.location_normalization, sim.duration_normalization)
                                 for t in sim.tasks[1:-2]], dtype=np.float32)
            robot_features = np.array([r.feature_vector(sim.location_normalization, sim.duration_normalization)
                                       for r in sim.robots], dtype=np.float32)
        if not np.isfinite(features).all() or not np.isfinite(robot_features).all():
            raise ContractError("native normalized features are not finite in float32")
        return sim

    def create_scheduler(self, sim):
        self.last_checkpoint_audit = None
        if not self.checkpoint_path.is_file():
            raise FileNotFoundError(f"SADCHER checkpoint unavailable: {self.checkpoint_path}")
        scheduler_type, _ = _native_imports(self.source_root)
        checkpoint_before = _sha256(self.checkpoint_path)
        scheduler = scheduler_type(
            debugging=False, checkpoint_path=str(self.checkpoint_path),
            duration_normalization=sim.duration_normalization,
            location_normalization=sim.location_normalization,
        )
        self.last_checkpoint_audit = audit_checkpoint(scheduler, self.checkpoint_path)
        if self.last_checkpoint_audit["sha256"] != checkpoint_before:
            raise RuntimeError("SADCHER checkpoint changed during native loading; inference rejected")
        return scheduler

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        import numpy as np
        import torch

        robots = tuple(robots)
        self.last_checkpoint_audit = None
        self.last_simulation = self.last_scheduler = self.last_schedule = self.last_rewards = None
        sim = self.build_simulation(task, robots)
        scheduler = self.create_scheduler(sim)
        self.last_simulation, self.last_scheduler = sim, scheduler
        rewards, schedule = scheduler.calculate_robot_assignment(sim)
        self.last_schedule, self.last_rewards = schedule, rewards
        if not torch.isfinite(rewards).all():
            raise RuntimeError("native SADCHER returned nonfinite rewards")
        assignments = []
        coalitions = defaultdict(list)
        idle, exit_robots, start_robots = [], [], []
        raw = {}
        for robot_index, task_index in schedule.robot_assignments.items():
            if (isinstance(robot_index, bool) or isinstance(task_index, bool)
                    or not isinstance(robot_index, (int, np.integer))
                    or not 0 <= robot_index < len(robots)
                    or not isinstance(task_index, (int, np.integer))
                    or not 0 <= task_index < len(sim.tasks)):
                raise ContractError("native SADCHER returned an invalid robot/task index")
            raw[str(robot_index)] = int(task_index)
            if not sim.robots[robot_index].available:
                raise ContractError("native SADCHER selected an unavailable robot")
            robot_id = robots[robot_index].capability.robot_id
            if task_index == sim.idle_task_id:
                idle.append(robot_id)
            elif task_index == sim.last_task_id:
                exit_robots.append(robot_id)
            elif task_index == 0:
                start_robots.append(robot_id)
            else:
                native_task = sim.tasks[task_index]
                if not native_task.ready or native_task.status != "PENDING" or not native_task.incomplete:
                    raise ContractError("native SADCHER selected a non-ready/completed task")
                task_id = task.graph.tasks[task_index - 1].task_id
                assignments.append(Assignment(task_id, robot_id, source="native_sadcher"))
                coalitions[task_id].append(robot_id)
        condition = AlgorithmCondition(
            algorithm_id="SADCHER-native-to-EAI", source_reference=SOURCE_REFERENCE,
            implementation="unchanged_upstream_SadcherScheduler", execution_scope="native_assignment_only",
            planner="neural_rewards+CachedBipartiteMatcher+filter_redundant_assignments+filter_overassignments",
            checkpoint=str(self.checkpoint_path), model_or_api="official_SADCHER_IL_checkpoint",
            supported_primitives=("three_binary_skills", "DAG", "native_coalitions", "native_idle"),
            unsupported_dimensions=("nonplanar_geometry", "extra_skill_dimensions", "coalition_dispatch_in_current_TaskRunner", "physical_success_without_eai_evidence"),
        )
        decision = AlgorithmDecision(
            condition=condition, assignments=tuple(assignments),
            unassigned_tasks=tuple(node.task_id for node, native in zip(task.graph.tasks, sim.tasks[1:-2]) if native.status == "PENDING" and node.task_id not in coalitions),
            provenance={
                "source_root": str(self.source_root), "checkpoint_audit": self.last_checkpoint_audit,
                "checkpoint_selection": self.checkpoint_selection,
                "predicted_rewards_including_sentinels": rewards.detach().cpu().tolist(),
                "skill_order": list(self.skill_names), "native_assignments": raw,
                "robot_index": [robot.capability.robot_id for robot in robots],
                "task_index": ["__start__"] + [node.task_id for node in task.graph.tasks] + ["__idle__", "__end__"],
                "coalitions": dict(coalitions), "idle_robot_ids": idle,
                "end_robot_ids": exit_robots, "start_robot_ids": start_robots,
                "unselected_robot_ids": [robot.capability.robot_id for i, robot in enumerate(robots) if str(i) not in raw],
                "task_adjacency": sim.task_adjacency.tolist(),
                "task_runner_compatible": all(len(team) == 1 for team in coalitions.values()),
                "scope_rejection": (
                    {"status": "N/A", "reason": NativeCoalitionScopeError.NA_REASON}
                    if any(len(team) > 1 for team in coalitions.values()) else None
                ),
                "evidence": {"contract": "translated", "native": "executed_complete_checkpoint", "simulation": "not_executed", "physical": "N/A"},
                "normalization": {"duration": float(sim.duration_normalization), "location": float(sim.location_normalization)},
            },
        )
        if not decision.provenance["task_runner_compatible"]:
            raise NativeCoalitionScopeError(decision)
        if exit_robots and any(t.status != "DONE" for t in sim.tasks[1:-2]):
            raise NativePrematureExitError(decision)
        return decision

    def replan(self, task: EaiTaskPackage, robots: Sequence[RobotState], state: OrchestrationState) -> AlgorithmDecision:
        """Translate executor-owned dependency completion into native task status."""
        if state.failed_task_ids or state.blocked_task_ids:
            raise ContractError("terminal failure/blocked tasks have no native SADCHER status")
        ids = {node.task_id for node in task.graph.tasks}
        completed, pending = set(state.completed_task_ids), set(state.pending_task_ids)
        if (completed & pending or completed | pending != ids
                or len(completed) != len(state.completed_task_ids)
                or len(pending) != len(state.pending_task_ids)):
            raise ContractError("replan state must partition the declared task roster")
        live = self._live_tasks(task)
        if live is not None and any(live[task_id]["status"] != "DONE" for task_id in state.completed_task_ids):
            raise ContractError("completed dependency feedback conflicts with native_tasks observation")
        if live is not None and any(live[task_id]["status"] == "DONE" for task_id in pending):
            raise ContractError("pending dependency feedback conflicts with native_tasks observation")
        # Runner supplies cumulative feedback, but last_decision only covers the
        # latest batch. Validate that batch's owners without inventing history.
        robot_ids = {r.capability.robot_id for r in robots}
        latest = {a.task_id: a.robot_id for a in state.last_decision.assignments} if state.last_decision else {}
        seen = set()
        for item in state.feedback:
            if item.task_id not in ids or item.robot_id not in robot_ids or item.task_id in seen:
                raise ContractError("replan feedback has unknown or duplicate task/robot identity")
            seen.add(item.task_id)
            if item.task_id not in completed or item.failure_code is not FailureCode.NONE:
                raise ContractError("replan completion contradicts execution feedback")
            if item.task_id in latest and item.robot_id != latest[item.task_id]:
                raise ContractError("replan feedback robot differs from dispatched assignment")
        initially_done = {n.task_id for n in task.graph.tasks if n.metadata.get("status") == "DONE"}
        if completed - initially_done - seen:
            raise ContractError("replan completion requires executor feedback")
        nodes = tuple(replace(node, metadata={**node.metadata, "status": "DONE", "remaining_duration_s": 0.0}) if node.task_id in state.completed_task_ids else node for node in task.graph.tasks)
        return self(replace(task, graph=replace(task.graph, tasks=nodes)), robots)


__all__ = ["NativeSadcherAdapter", "NativeCoalitionScopeError", "NativePrematureExitError", "CheckpointCompatibilityError", "audit_checkpoint"]
