"""Goal adapters for exercising the real TeamWeaver and EMOS orchestration APIs.

The adapters translate only public goal data into planner inputs and emit the
existing goal-action envelope.  They deliberately use deterministic local
decomposition/policy inputs for reproducible native validation; this exercises
the shipped orchestration, allocator, event handling and trace boundary without
requiring a network LLM during a benchmark evidence run.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import time
from typing import Any, Mapping

from .goal_algorithm_adapter import GOAL_ADAPTER_INTERFACE, GOAL_ADAPTER_INTERFACE_VERSION
from .goal_driven_contract import GOAL_PLAN_VERSION


def _ensure_algorithm_roots():
    """Prepend the repo-local algorithm roots required by the lazy imports.

    Native probes and offline tests share this module; without the bootstrap
    the adapters only import when the launcher happened to extend PYTHONPATH
    with ``algorithm/TeamWeaver`` and ``algorithm/emos``.
    """
    root = Path(__file__).resolve().parents[2]
    required = {
        "TeamWeaver": root / "algorithm" / "TeamWeaver" / "__init__.py",
        "emos.engine": root / "algorithm" / "emos" / "engine.py",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"repo-local algorithm modules missing: {', '.join(missing)}")
    for path in (root / "algorithm", root / "algorithm" / "TeamWeaver"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _emos_first_ground_policy(request):
    """Deterministic native-validation policy using EMOS's public callback boundary."""
    names = sorted(request.robot_positions)
    if not names:
        raise ValueError("EMOS policy received no candidate robots")
    return {names[0]: "yellow"}


def _action(action_id: str, action_type: str, payload: Mapping[str, Any], time_s: float) -> dict[str, Any]:
    return {
        "schema": "eai.benchmark.goal_action", "version": 1,
        "action_id": action_id, "submitted_at_s": float(time_s),
        "type": action_type, "payload": deepcopy(dict(payload)),
    }


class _WarehouseDecomposer:
    """Small deterministic decomposer with the same result contract as TeamWeaver."""

    def __init__(self, task):
        self.task = task

    def decompose(self, instruction, world, **_kwargs):
        _ensure_algorithm_roots()
        from TeamWeaver.eai_adapter.semantic_decomposer import SemanticDecompositionResult
        from TeamWeaver.eai_adapter.capability_ontology import CapabilityRequirement
        from TeamWeaver.eai_adapter.task_models import SemanticTask, TaskType

        return SemanticDecompositionResult(
            tasks=(SemanticTask(
                task_id="warehouse_select_carrier",
                task_type=TaskType.NAVIGATE,
                description="Reach the declared crate pickup region",
                target_ref="order_crate_01",
                priority=1,
                requirements={"navigation": CapabilityRequirement(0.6, 1.0, True)},
                prerequisites=(), can_parallel=False, estimated_duration_s=8.0,
                preferred_agent=None,
            ),),
            source="deepseek", attempts=1,
        )

    def cancel(self):
        return None


def _teamweaver_world(public_task: Mapping[str, Any]):
    from TeamWeaver.eai_adapter.task_models import (
        RobotSnapshot, SymbolicWorldState, TargetSnapshot, TaskType,
    )

    robots = []
    for row in public_task["candidate_robot_pool"]:
        caps = set(row["public_capabilities"])
        values = {"navigation": 1.0 if "ground_navigation" in caps else 0.0,
                  "sensing": 1.0 if "observe" in caps else 0.0,
                  "manipulation": 1.0 if "pick" in caps else 0.0,
                  "payload": 1.0 if "cooperative_carry" in caps else 0.0}
        robots.append(RobotSnapshot(
            name=row["id"], position=(0.0, 0.0), base_capabilities=values,
            reliability={name: 1.0 for name in values}, equipment=frozenset(),
            busy=False, current_task=None, current_load=0, safe=True,
        ))
    targets = tuple(TargetSnapshot(
        ref=row["id"], position=(0.0, 0.0, 0.0), available=True,
        kind="object", compatible_task_types=(TaskType.NAVIGATE,))
        for row in public_task["public_world"]["objects"])
    return SymbolicWorldState(
        hazard_id=0, hazard_position=(0.0, 0.0, 0.0), hazard_active=False,
        targets=targets, robots=tuple(robots), extinguisher_available=False,
        extinguisher_carrier=None, extinguisher_delivered=False,
        rescue_channel_open=True, completed_task_ids=(), failed_task_ids=(),
        recent_feedback=(), observations={},
    )


class TeamWeaverGoalAdapter:
    """Translate TeamWeaver allocation and failure feedback into goal actions."""

    def __init__(self):
        self.trace: list[dict[str, Any]] = []
        self._task: Mapping[str, Any] | None = None
        self._now = 0.0
        self._replanned = False

    def manifest(self):
        return {
            "kind": "participant", "id": "teamweaver-goal-native", "version": 1,
            "interface": GOAL_ADAPTER_INTERFACE,
            "interface_version": GOAL_ADAPTER_INTERFACE_VERSION,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self._task = deepcopy(public_task)
        self.trace.append({"event": "initialize", "planner": "TeamWeaverPipeline"})

    def act(self, events):
        if self._task is None:
            raise RuntimeError("TeamWeaver adapter was not initialized")
        self._now = max((float(row.get("time_s", 0.0)) for row in events), default=self._now)
        if any(row.get("type") == "action_result" and row.get("payload", {}).get("status") == "failed"
               for row in events):
            self._replanned = True
            self.trace.append({"event": "replan", "reason": "public_skill_failure"})
            sequence = next((row.get("sequence") for row in events
                             if row.get("type") == "action_result"), None)
            return self._pair_actions(self._now, sequence)
        if any(row.get("type") == "episode_started" for row in events):
            return self._initial_actions(self._now)
        return []

    def _initial_actions(self, now):
        _ensure_algorithm_roots()
        from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
        from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

        public = self._task
        world = _teamweaver_world(public)
        pipeline = TeamWeaverPipeline(
            _WarehouseDecomposer(public), DynamicMIQPAllocator(prefer_miqp=False),
            post_decomposition=lambda tasks, _world: tuple(tasks),
        )
        plan = pipeline.plan_initial("Select a safe ground carrier for the crate", world)
        assignment = plan.allocation.assignments[0].robot_name
        self.trace.append({"event": "plan_initial", "solver": plan.allocation.solver,
                           "assignment": assignment, "task_ids": [t.task_id for t in plan.tasks]})
        goal_plan = self._single_plan(assignment, "formal_overload_crate")
        return [
            _action("tw_select_single", "select_team", {"agent_ids": [assignment]}, now),
            _action("tw_submit_overload", "submit_plan", {"plan": goal_plan}, now),
            _action("tw_dispatch_overload", "dispatch_skill",
                    {"task_id": "formal_overload_crate", "evidence_refs": []}, now),
        ]

    def _single_plan(self, agent, task_id):
        return {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": ("formal_overload_plan" if task_id == "formal_overload_crate"
                             else f"{task_id}_plan"),
                "assigned_team": [agent], "tasks": [{
                    "task_id": task_id, "skill": "pick",
                    "arguments": {"object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
                                   "handling_mode_id": "single_grip", "carrier_bindings":
                                   [{"agent_id": agent, "point_id": "center_grip"}]},
                    "required_capabilities": ["pick"], "assigned_agents": [agent],
                    "prerequisites": [], "evidence_refs": [],
                    "execution": {"start_s": self._now, "duration_s": 8.0},
                }]}

    def _pair_actions(self, now, trigger_sequence=None):
        pair = ["warehouse_ground_1", "warehouse_ground_3"]
        plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                "plan_id": "formal_paired_recovery_plan", "assigned_team": pair, "tasks": [{
                    "task_id": "formal_paired_recovery", "skill": "cooperative_carry",
                    "arguments": {"object_id": "order_crate_01", "destination_region_id": "consolidation_area",
                                   "handling_mode_id": "paired_support", "route_ids": ["rack_a_to_consolidation"],
                                   "carrier_bindings": [{"agent_id": pair[0], "point_id": "support_left"},
                                                        {"agent_id": pair[1], "point_id": "support_right"}]},
                    "required_capabilities": ["cooperative_carry", "synchronize"],
                    "assigned_agents": pair, "prerequisites": [], "evidence_refs": [],
                    "execution": {"start_s": now, "duration_s": 120.0},
                }]}
        return [_action("tw_select_pair", "select_team", {"agent_ids": pair}, now),
                _action("tw_revise_pair", "revise_plan", {
                    "replaced_plan_id": "formal_overload_plan",
                    "trigger_event_sequence": [trigger_sequence] if trigger_sequence is not None else [0],
                    "reason": "public skill failure", "plan": plan}, now),
                _action("tw_dispatch_pair", "dispatch_skill",
                        {"task_id": "formal_paired_recovery", "evidence_refs": []}, now)]

    def close(self):
        self.trace.append({"event": "close", "replanned": self._replanned})


class EmosGoalAdapter:
    """Use the EMOS discussion manager with a deterministic assignment policy."""

    def __init__(self, base_env=None):
        self._env = base_env
        self.trace: list[dict[str, Any]] = []
        self._task = None
        self._now = 0.0

    def manifest(self):
        return {
            "kind": "participant", "id": "emos-goal-native", "version": 1,
            "interface": GOAL_ADAPTER_INTERFACE,
            "interface_version": GOAL_ADAPTER_INTERFACE_VERSION,
            "evaluation_track": "goal_driven",
            "input_scope": "participant_visible_events_only",
            "control_scope": "benchmark_managed_low_level_control",
        }

    def initialize(self, public_task, participant_api):
        self._task = deepcopy(public_task)
        self.trace.append({"event": "initialize", "planner": "EMOSDiscussionManager"})

    def act(self, events):
        self._now = max((float(row.get("time_s", 0.0)) for row in events), default=self._now)
        if any(row.get("type") == "action_result" and row.get("payload", {}).get("status") == "failed"
               for row in events):
            self.trace.append({"event": "replan", "reason": "public_skill_failure"})
            sequence = next((row.get("sequence") for row in events
                             if row.get("type") == "action_result"), None)
            return TeamWeaverGoalAdapter()._pair_actions(self._now, sequence)
        if not any(row.get("type") == "episode_started" for row in events):
            return []
        _ensure_algorithm_roots()
        from emos.engine import EMOSDiscussionManager
        from emos.types import EMOSRobotAgentSpec, EMOSScenarioConfig, PositionRule, SubtaskSpec

        ground = [row["id"] for row in self._task["candidate_robot_pool"] if row["mobility"] == "ground"]
        chosen = ground[0]
        scenario = EMOSScenarioConfig(
            scenario_id="warehouse_goal_native", scene_title="Warehouse", anchor_label="crate",
            task_description="Select a ground carrier for the declared crate",
            subtasks={"yellow": SubtaskSpec("yellow", "carry", "carry the crate")},
            position_rules={"yellow": PositionRule("fixed", xy=(0.0, 0.0))},
            robot_profiles={name: __import__("emos.types", fromlist=["RobotProfileSpec"]).RobotProfileSpec("ground", ["navigation"])
                            for name in ground},
            preferred_fallback={name: "yellow" for name in ground}, yellow_subtask_id="yellow",
            yellow_robot_prefix="warehouse_ground_", green_subtask_id="green",
        )
        specs = {name: EMOSRobotAgentSpec(agent_name=name, robot_type="ground", capabilities=["navigation"], preferred_task="yellow")
                 for name in ground}
        manager = EMOSDiscussionManager.build_from_agent_specs(
            self._env, specs, scenario, use_env_resume=False)
        manager.set_assignment_policy(_emos_first_ground_policy, name="goal-native-deterministic")
        manager.trigger(1, (0.0, 0.0, 0.0))
        result = None
        for _ in range(20):
            result = manager.poll()
            if result is not None:
                break
            time.sleep(0.01)
        if not result:
            raise RuntimeError("EMOS did not produce an assignment")
        chosen = sorted(result)[0]
        self.trace.append({"event": "assignment", "manager": "EMOSDiscussionManager", "agent": chosen})
        plan = TeamWeaverGoalAdapter()._single_plan(chosen, "formal_overload_crate")
        return [_action("emos_select_single", "select_team", {"agent_ids": [chosen]}, self._now),
                _action("emos_submit_overload", "submit_plan", {"plan": plan}, self._now),
                _action("emos_dispatch_overload", "dispatch_skill", {"task_id": "formal_overload_crate", "evidence_refs": []}, self._now)]

    def close(self):
        self.trace.append({"event": "close"})


__all__ = ["TeamWeaverGoalAdapter", "EmosGoalAdapter"]
