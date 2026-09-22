"""Algorithm-participant restaurant episodes over a dual-Carter executor.

The witness episode (``demo.benchmark.goal_restaurant_episode``) proves the
business closed loop with one fixed serial participant.  This module runs the
same physical substrate with a *measured allocator* as the participant: the
real TeamWeaver pipeline selects the team and assigns each service mission to
an agent, and the episode measures those decisions against native execution.

v1 scope, frozen deliberately: the executor covers the two Carter anchors
(both carry episode-grade native drivers), tasks execute serially through the
shared FormalGoalEpisodeRunner contract, and the algorithm-visible pool is
therefore the two Carters - mirroring the City discipline of declaring pools
whose every selection is executable.  Completion-time benefit from concurrent
driving and pools beyond the Carters are executor v2 work.  The independent
on-disk archive verifier for this variant is also pending; runs keep
``execution_validated=false`` and ``scoreable=false`` and their evidence is
in-run validated only.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from typing import Any, Mapping
import uuid

from .goal_driven_contract import (
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
    validate_goal_plan,
)
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner
from .goal_restaurant_episode import (
    NAVIGATION_WINDOW_S,
    NativeCarterEpisodeNavigator,
    OBSERVATION_WINDOW_S,
    RELAY_WINDOW_S,
    ROOT,
    _A,
    _B,
    _E,
    _action,
    _write_json,
    evaluate_restaurant_business,
)
from .provenance import canonical_sha256, file_sha256
from .runtime import route_waypoints
from demo.restaurant_benchmark import config as restaurant_config
from demo.restaurant_benchmark import runtime_probe


ROBOT_BINDINGS = {
    "restaurant_carter_1": {"native_agent_id": "carter_1", "spawn_region_id": "restaurant_staging",
                            "spawn_xy_m": list(_A), "mobility": "ground"},
    "restaurant_carter_2": {"native_agent_id": "carter_2", "spawn_region_id": "restaurant_staging",
                            "spawn_xy_m": list(restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_2"][:2]),
                            "mobility": "ground"},
    # The shared goal contract requires every task to expose heterogeneous
    # aerial and ground capabilities, so iris_1 carries the aerial
    # declaration and spawns at its public anchor.  The narrowed v5 business
    # semantics have no aerial-dispatchable mission (staging admits ground
    # access only, so an aerial relay plan cannot even validate), so iris_1
    # is a declarable candidate that no legal v1 plan dispatches; forcing an
    # aerial observation mission remains the open heterogeneity review.  Its
    # declared region is the public overview airspace, the only
    # aerial-access region in the shared contract.
    "restaurant_iris_1": {"native_agent_id": "iris_1", "spawn_region_id": "restaurant_overview",
                          "spawn_xy_m": list(restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["iris_1"][:2]),
                          "mobility": "aerial"},
}
_C = tuple(ROBOT_BINDINGS["restaurant_carter_2"]["spawn_xy_m"])
_IRIS = tuple(ROBOT_BINDINGS["restaurant_iris_1"]["spawn_xy_m"])
REGION_BINDINGS = {
    "restaurant_staging": {"center_xy_m": list(_A)},
    "kitchen_service": {"center_xy_m": list(_E)},
    "dining_service": {"center_xy_m": list(_B)},
}
ALGORITHM_POOL = tuple(sorted(ROBOT_BINDINGS))
ALGORITHMS = ("teamweaver",)
# Effective Carter speed measured on the witness legs (~0.25 m/s over planned
# distance) with a 12 s per-leg margin.  Windows are per-leg ceilings and the
# v1 executor is serial, so an allocation whose summed windows exceed the
# 240 s simulation budget is plan-infeasible - a real measured cost of the
# allocator's assignment choice, not an infrastructure failure.
EFFECTIVE_SPEED_M_S = 0.25
NAVIGATION_MARGIN_S = 12.0

# Agent-scoped leg matrix.  Both anchors are "restaurant_staging" members; a
# leg is identified by (native agent, from-region, to-region) and its geometry
# is planned once at prepare time and frozen in the request.
AGENT_PHYSICAL_LEGS = {
    "carter_1_staging_to_kitchen": {"agent": "carter_1", "from_region_id": "restaurant_staging",
                                    "to_region_id": "kitchen_service", "start_xy_m": _A, "goal_xy_m": _E},
    "carter_1_kitchen_to_staging": {"agent": "carter_1", "from_region_id": "kitchen_service",
                                    "to_region_id": "restaurant_staging", "start_xy_m": _E, "goal_xy_m": _A},
    "carter_1_staging_to_dining": {"agent": "carter_1", "from_region_id": "restaurant_staging",
                                   "to_region_id": "dining_service", "start_xy_m": _A, "goal_xy_m": _B},
    "carter_1_dining_to_staging": {"agent": "carter_1", "from_region_id": "dining_service",
                                   "to_region_id": "restaurant_staging", "start_xy_m": _B, "goal_xy_m": _A},
    "carter_2_staging_to_kitchen": {"agent": "carter_2", "from_region_id": "restaurant_staging",
                                    "to_region_id": "kitchen_service", "start_xy_m": _C, "goal_xy_m": _E},
    "carter_2_kitchen_to_staging": {"agent": "carter_2", "from_region_id": "kitchen_service",
                                    "to_region_id": "restaurant_staging", "start_xy_m": _E, "goal_xy_m": _C},
    "carter_2_staging_to_dining": {"agent": "carter_2", "from_region_id": "restaurant_staging",
                                   "to_region_id": "dining_service", "start_xy_m": _C, "goal_xy_m": _B},
    "carter_2_dining_to_staging": {"agent": "carter_2", "from_region_id": "dining_service",
                                   "to_region_id": "restaurant_staging", "start_xy_m": _B, "goal_xy_m": _C},
}


def _leg_route(products_root: Path, leg_id: str) -> dict[str, Any]:
    from EAI.scenario_spec.geometry import FactoryMap
    from EAI.scenario_spec.oracle import InflatedGrid

    definition = AGENT_PHYSICAL_LEGS[leg_id]
    occupancy = FactoryMap(products_root / "restaurant_benchmark_map.yaml")
    grid = InflatedGrid(occupancy, 0.40)
    cells = grid.path(definition["start_xy_m"], definition["goal_xy_m"])
    if cells is None:
        raise RuntimeError(f"Restaurant algorithm leg is not passable: {leg_id}")
    waypoints = route_waypoints(grid, cells, definition["goal_xy_m"], preserve_goal=True)
    points = [definition["start_xy_m"], *waypoints]
    length = sum(math.dist(a, b) for a, b in zip(points[:-1], points[1:]))
    window = max(NAVIGATION_WINDOW_S,
                 math.ceil(length / EFFECTIVE_SPEED_M_S + NAVIGATION_MARGIN_S))
    return {
        "route_id": leg_id,
        "agent": definition["agent"],
        "from_region_id": definition["from_region_id"],
        "to_region_id": definition["to_region_id"],
        "start_xy_m": list(definition["start_xy_m"]),
        "goal_xy_m": list(definition["goal_xy_m"]),
        "map_sha256": occupancy.sha256,
        "route_cells": [list(cell) for cell in cells],
        "waypoints_xy_m": [list(point) for point in waypoints],
        "planned_length_m": length,
        "navigation_window_s": float(window),
    }


def _leg_routes(products_root: Path) -> dict[str, dict[str, Any]]:
    return {leg_id: _leg_route(products_root, leg_id)
            for leg_id in sorted(AGENT_PHYSICAL_LEGS)}


def _leg_for(native_agent: str, from_region: str, to_region: str,
             routes: Mapping[str, Mapping[str, Any]]) -> tuple[str, Mapping[str, Any]]:
    matches = [(leg_id, route) for leg_id, route in routes.items()
               if route["agent"] == native_agent
               and route["from_region_id"] == from_region
               and route["to_region_id"] == to_region]
    if len(matches) != 1:
        raise ValueError(
            f"no unique {native_agent} leg {from_region}->{to_region}: "
            f"{[leg_id for leg_id, _ in matches]}")
    return matches[0]


def _algorithm_selection() -> dict[str, Any]:
    robots = []
    for public_id in ALGORITHM_POOL:
        binding = ROBOT_BINDINGS[public_id]
        spawn = list(restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M[binding["native_agent_id"]])
        robots.append({
            "type": "iris" if binding["mobility"] == "aerial" else "carter",
            "controller": {"mode": "default",
                           "cfg": "PEGASUS_IRIS_POSITION_CFG" if binding["mobility"] == "aerial"
                           else "CARTER_DIFF_CFG"},
            "attachments": [],
            "spawn_pose": {"position": spawn, "rotation": [1.0, 0.0, 0.0, 0.0]},
        })
    return {
        "version": 1,
        "task_name": "restaurant_algorithm_episode",
        "scene_key": restaurant_config.BENCHMARK_SCENE_KEY,
        "robots": robots,
    }


def _algorithm_public_task(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Restrict the shared pool to the executor-covered Carters (v1).

    The shared bundle's eight-anchor pool stays the authoring declaration;
    algorithm episodes declare the v1 executor coverage by filtering the
    pool, mirroring the City discipline that every selectable team is
    executable.  The derived task keeps the shared schema exactly.
    """
    task = deepcopy(dict(bundle["public_task"]))
    pool = [row for row in task["candidate_robot_pool"] if row["id"] in ALGORITHM_POOL]
    if len(pool) != len(ALGORITHM_POOL):
        raise ValueError("shared restaurant pool lost the Carter declarations")
    task["candidate_robot_pool"] = pool
    world = deepcopy(dict(task["public_world"]))
    spawns = world.get("robot_spawn_positions")
    if (not isinstance(spawns, Mapping)
            or not set(ALGORITHM_POOL) <= set(spawns)):
        raise ValueError("shared spawn declarations lost an algorithm-pool anchor")
    world["robot_spawn_positions"] = {key: deepcopy(spawns[key]) for key in ALGORITHM_POOL}
    task["public_world"] = world
    task["budgets"] = {**deepcopy(dict(task["budgets"])),
                       "max_active_agents": len(ALGORITHM_POOL)}
    return task


# -- mission catalogue -------------------------------------------------------

MISSIONS = (
    {"mission_id": "kitchen_inspection", "region_id": "kitchen_service"},
    {"mission_id": "dining_inspection", "region_id": "dining_service"},
)


def _public_route_id(task_spec: Mapping[str, Any], from_region: str, to_region: str) -> str:
    """Resolve a region transition onto the task's public route graph."""
    routes = task_spec["public_world"]["routes"]
    matches = [row["id"] for row in routes
               if row["from_region_id"] == from_region and row["to_region_id"] == to_region]
    if len(matches) != 1:
        raise ValueError(
            f"public route graph has no unique {from_region}->{to_region}: {matches}")
    return matches[0]


def _expand_mission_plan(task_spec: Mapping[str, Any], assignment: Mapping[str, str],
                         relay_agent: str, routes: Mapping[str, Mapping[str, Any]],
                         plan_id: str) -> dict[str, Any]:
    """Expand mission->agent allocation into the structural GoalSession plan.

    Each inspection mission expands to arrive/observe/return bound to one
    agent (region continuity is per agent); the relay closes the episode from
    staging by the allocation's relay agent.  Plan arguments reference the
    task's public route graph; the backend resolves the assigned agent's
    physical leg for the same transition at execution time, with windows from
    the frozen per-leg table.
    """
    rows: list[dict[str, Any]] = []
    clock = 0.0

    def append(task_id: str, skill: str, arguments: Mapping[str, Any],
               prerequisites: list[str], duration: float, agent: str) -> None:
        nonlocal clock
        rows.append({
            "task_id": task_id, "skill": skill, "arguments": deepcopy(dict(arguments)),
            "prerequisites": list(prerequisites), "required_capabilities": [skill],
            "assigned_agents": [agent], "evidence_refs": [],
            "execution": {"start_s": float(clock), "duration_s": float(duration)},
        })
        clock += duration

    agent_last_return: dict[str, str] = {}
    for mission in MISSIONS:
        region = mission["region_id"]
        public_agent = assignment[mission["mission_id"]]
        binding = ROBOT_BINDINGS[public_agent]
        if binding["mobility"] != "ground":
            raise ValueError(
                f"{mission['mission_id']} requires ground access; {public_agent} is aerial")
        native = binding["native_agent_id"]
        arrive_leg, arrive_route = _leg_for(native, "restaurant_staging", region, routes)
        return_leg, return_route = _leg_for(native, region, "restaurant_staging", routes)
        mid = mission["mission_id"]
        arrive_prerequisites: list[str] = []
        if public_agent in agent_last_return:
            # Position causality: the agent's previous mission must return it
            # to staging before this mission can depart staging again.
            arrive_prerequisites.append(agent_last_return[public_agent])
        append(f"restaurant_{mid}_arrive", "ground_navigation",
               {"region_id": region,
                "route_ids": [_public_route_id(task_spec, "restaurant_staging", region)]},
               arrive_prerequisites,
               float(arrive_route["navigation_window_s"]), public_agent)
        append(f"restaurant_{mid}_observe", "observe",
               {"region_id": region, "subject_ref": region,
                "observation_mode": "service_ground_readiness"},
               [f"restaurant_{mid}_arrive"], OBSERVATION_WINDOW_S, public_agent)
        append(f"restaurant_{mid}_return", "ground_navigation",
               {"region_id": "restaurant_staging",
                "route_ids": [_public_route_id(task_spec, region, "restaurant_staging")]},
               [f"restaurant_{mid}_observe"],
               float(return_route["navigation_window_s"]), public_agent)
        agent_last_return[public_agent] = f"restaurant_{mid}_return"
    relay_public = assignment["readiness_relay"]
    if relay_public not in ROBOT_BINDINGS:
        raise ValueError("relay agent is outside the algorithm pool")
    if ROBOT_BINDINGS[relay_public]["mobility"] != "ground":
        # staging admits ground access only, so an aerial relay cannot form
        # a valid plan under the shared contract; refuse loudly so the
        # allocator's decision surfaces as a measured plan failure.
        raise ValueError(
            f"aerial relay by {relay_public} cannot validate: staging is ground-access-only")
    append("restaurant_readiness_relay", "relay",
           {"message_kind": "restaurant_readiness_receipt",
            "region_id": "restaurant_staging"},
           ["restaurant_kitchen_inspection_observe",
            "restaurant_dining_inspection_observe",
            "restaurant_kitchen_inspection_return",
            "restaurant_dining_inspection_return"],
           RELAY_WINDOW_S, relay_public)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": plan_id, "assigned_team": sorted(set(assignment.values())),
            "tasks": rows}
    validate_goal_plan(plan, task_spec=task_spec)
    return plan


class RestaurantTeamWeaverParticipant:
    """Real TeamWeaver allocator deciding the restaurant episode's team.

    The measured decisions are team selection, the mission assignment and the
    relay agent; the structural expansion (arrive/observe/return per mission)
    is executor vocabulary.  Positions are the public spawn anchors and region
    centers, so the allocator's distance cost operates on real geometry.
    """

    def __init__(self, task_spec: Mapping[str, Any], routes: Mapping[str, Mapping[str, Any]]):
        self._task_spec = deepcopy(dict(task_spec))
        self._routes = deepcopy(dict(routes))
        self._plan: dict[str, Any] | None = None
        self._opened = False
        self._next = 0
        self._terminal: dict[str, str] = {}
        self.trace: list[dict[str, Any]] = []

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "restaurant-native-teamweaver", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def _allocate(self) -> dict[str, str]:
        from .goal_external_adapters import _ensure_algorithm_roots
        _ensure_algorithm_roots()
        from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
        from TeamWeaver.eai_adapter.semantic_decomposer import SemanticDecompositionResult
        from TeamWeaver.eai_adapter.capability_ontology import CapabilityRequirement
        from TeamWeaver.eai_adapter.task_models import (
            RobotSnapshot, SemanticTask, SymbolicWorldState, TargetSnapshot, TaskType,
        )

        axis = {"ground_navigation": "navigation", "observe": "sensing",
                "relay": "communication"}
        pool_rows = self._task_spec["candidate_robot_pool"]
        region_center = {"kitchen_inspection": _E, "dining_inspection": _B,
                         "readiness_relay": _A}
        # The relay must be published from restaurant_staging, whose public
        # access modes admit ground navigation only.  Encoding that public
        # contract fact into the mission requirements is interface
        # translation, not a decision for the allocator: it still chooses
        # which ground-capable agent publishes.
        requirement_axes = {
            "kitchen_inspection": ("navigation", "sensing"),
            "dining_inspection": ("navigation", "sensing"),
            "readiness_relay": ("communication", "navigation"),
        }
        raw_requirements = {
            "kitchen_inspection": {"ground_navigation", "observe"},
            "dining_inspection": {"ground_navigation", "observe"},
            "readiness_relay": {"relay", "ground_navigation"},
        }

        def robot_snapshot(row: Mapping[str, Any]) -> RobotSnapshot:
            values = {"navigation": 0.0, "sensing": 0.0, "manipulation": 0.0,
                      "payload": 0.0, "communication": 0.0}
            for cap in row["public_capabilities"]:
                key = axis.get(cap, cap)
                values[key] = max(values.get(key, 0.0), 1.0)
            position = tuple(ROBOT_BINDINGS[row["id"]]["spawn_xy_m"])
            return RobotSnapshot(
                name=row["id"], position=position, base_capabilities=values,
                reliability={name: 1.0 for name in values}, equipment=frozenset(),
                busy=False, current_task=None, current_load=0, safe=True)

        targets = tuple(
            TargetSnapshot(ref=mission, position=(*region_center[mission], 0.0),
                           available=True, kind="region",
                           compatible_task_types=(TaskType.NAVIGATE,))
            for mission in (*requirement_axes,))

        catalog = []

        class _Decomposer:
            def decompose(self, instruction, world_state, **_kwargs):
                rows = []
                for mission, axes in requirement_axes.items():
                    rows.append(SemanticTask(
                        task_id=mission, task_type=TaskType.NAVIGATE,
                        description="restaurant_mission", target_ref=mission,
                        priority=1,
                        requirements={key: CapabilityRequirement(1.0, 1.0, True)
                                      for key in axes},
                        prerequisites=(), can_parallel=True,
                        estimated_duration_s=45.0, preferred_agent=None))
                return SemanticDecompositionResult(tasks=tuple(rows), source="catalog",
                                                   attempts=1)

        catalog = list(_Decomposer().decompose(None, None).tasks)
        allocator = DynamicMIQPAllocator(prefer_miqp=False)
        assignment: dict[str, str] = {}
        remaining = set(requirement_axes)
        team: set[str] = set()
        for round_index in range(len(requirement_axes)):
            if not remaining:
                break
            world = SymbolicWorldState(
                hazard_id=0, hazard_position=(0.0, 0.0, 0.0), hazard_active=False,
                targets=targets, robots=tuple(robot_snapshot(row) for row in pool_rows),
                extinguisher_available=False, extinguisher_carrier=None,
                extinguisher_delivered=False, rescue_channel_open=True,
                completed_task_ids=(), failed_task_ids=(), recent_feedback=(),
                observations={})
            batch = tuple(task for task in catalog if task.task_id in remaining)
            result = allocator.allocate(world, batch)
            round_map = {row.task_id: row.robot_name for row in result.assignments
                         if row.task_id in remaining}
            self.trace.append({"event": "allocation_round", "round": round_index,
                               "solver": getattr(result, "solver", "miqp"),
                               "assignment": dict(round_map),
                               "remaining": sorted(remaining)})
            if not round_map:
                break
            assignment.update(round_map)
            team.update(round_map.values())
            remaining -= set(round_map)
        if remaining:
            raise RuntimeError(
                f"TeamWeaver left restaurant missions unassigned: {sorted(remaining)}")
        public_caps = {row["id"]: set(row["public_capabilities"]) for row in pool_rows}
        for mission, agent in assignment.items():
            if not raw_requirements[mission] <= public_caps[agent]:
                raise ValueError(f"{agent} cannot cover {mission}")
        return assignment

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.trace.extend(deepcopy(events))
        now = max((float(row["time_s"]) for row in events), default=0.0)
        if not self._opened:
            if not any(row["type"] == "episode_started" for row in events):
                return []
            self._opened = True
            assignment = self._allocate()
            self.trace.append({"event": "allocation_final", "assignment": dict(assignment),
                               "team": sorted(set(assignment.values()))})
            self._plan = _expand_mission_plan(
                self._task_spec, assignment,
                relay_agent=assignment["readiness_relay"],
                routes=self._routes,
                plan_id="restaurant_teamweaver_missions_v1")
            self.trace.append({"event": "plan_submitted",
                               "task_ids": [row["task_id"] for row in self._plan["tasks"]],
                               "assigned_team": self._plan["assigned_team"]})
            return [
                _action("restaurant_teamweaver_select", "select_team",
                        {"agent_ids": list(self._plan["assigned_team"])}, now),
                _action("restaurant_teamweaver_plan", "submit_plan",
                        {"plan": deepcopy(self._plan)}, now),
                _action("restaurant_teamweaver_dispatch_0", "dispatch_skill",
                        {"task_id": self._plan["tasks"][0]["task_id"],
                         "evidence_refs": []}, now),
            ]
        for row in events:
            if row["type"] != "action_result":
                continue
            payload = row["payload"]
            if payload.get("status") in {"succeeded", "failed", "cancelled"}:
                self._terminal[str(payload["action_id"])] = str(payload["status"])
        previous = f"restaurant_teamweaver_dispatch_{max(self._next - 1, 0)}"
        if self._next == 0:
            self._next = 1
        if previous not in self._terminal:
            return []
        if (self._terminal[previous] != "succeeded"
                or self._next >= len(self._plan["tasks"])):
            return []
        index = self._next
        self._next += 1
        return [_action(f"restaurant_teamweaver_dispatch_{index}", "dispatch_skill",
                        {"task_id": self._plan["tasks"][index]["task_id"],
                         "evidence_refs": []}, now)]

    def close(self) -> None:
        return None


from demo.restaurant_benchmark.service_backend import RestaurantReadinessBackend


class SharedClockCarterNavigator(NativeCarterEpisodeNavigator):
    """Tie the witness navigator's episode clock to the shared sim clock.

    The witness navigator counts only its own control steps, which is exact
    for a single-agent episode.  With several agents stepping the same
    simulation, the episode clock must be the global simulation time since
    episode start; ``drive`` and ``wait_until`` both loop on this property,
    so the override alone keeps windows, deadlines and holds consistent.
    """

    def __init__(self, *args: Any, episode_step_origin: int, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._episode_step_origin = int(episode_step_origin)

    @property
    def episode_time_s(self) -> float:
        # current_time_step_index counts PHYSICS substeps (0.005 s), not
        # control steps: multiplying by the control step would run the
        # shared clock decimation-times too fast.  The integer index is
        # exact and shared by every agent, unlike the witness navigator's
        # own-step counter.
        index = int(self.session.base_env.sim.current_time_step_index)
        return (index - self._episode_step_origin) * float(
            self.session.base_env.sim.get_physics_dt())

    def wait_until(self, target_time_s: float) -> dict[str, Any]:
        # Same contract as the witness implementation, with a tolerance that
        # absorbs the accumulated float error of the shared simulation clock
        # (still far below one 0.02 s control step).
        if target_time_s + 1e-9 < self.episode_time_s:
            raise RuntimeError("native Restaurant execution exceeded its GoalSession window")
        start = self._stamp(self.pose())
        while self.episode_time_s + self.control_dt_s <= target_time_s + 1e-9:
            self.step_once()
        if not math.isclose(self.episode_time_s, target_time_s, abs_tol=1e-6):
            raise RuntimeError("GoalSession window is not aligned to the native control step")
        return {"kind": "native_window_hold", "start": start,
                "end": self._stamp(self.pose())}


# -- backend -----------------------------------------------------------------

class RestaurantMultiAgentGoalBackend:
    """Translate GoalSession skills onto the assigned agent's native driver."""

    BACKEND_ID = "isaac.restaurant.goal_algorithm_episode.v1"

    def __init__(self, navigators: Mapping[str, Any], routes: Mapping[str, Mapping[str, Any]],
                 public_routes: Mapping[str, Mapping[str, Any]], *, episode_id: str):
        self.navigators = dict(navigators)
        self.routes = deepcopy(dict(routes))
        self.public_routes = {str(row["id"]): deepcopy(dict(row))
                              for row in public_routes}
        self.episode_id = episode_id
        self.readiness = RestaurantReadinessBackend(episode_id=episode_id)
        self.robot_regions = {public: ROBOT_BINDINGS[public]["spawn_region_id"]
                              for public in ROBOT_BINDINGS}
        self.evidence: list[dict[str, Any]] = []
        self.business_evaluation: dict[str, Any] | None = None

    def _pending(self, task: Mapping[str, Any], trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        ledger = trusted_report["session"]
        row = ledger["tasks"].get(task["task_id"])
        if (ledger.get("episode_id") != self.episode_id or row is None
                or row["status"] != "pending" or row["task"] != task
                or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native task does not match the pending GoalSession dispatch")
        return row

    def _record(self, row: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        body = deepcopy(dict(row))
        ref = "sha256:" + canonical_sha256(body)
        result = {**body, "evidence_ref": ref}
        self.evidence.append(result)
        return result, ref

    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        if episode_id != self.episode_id:
            raise ValueError("algorithm backend episode_id mismatch")
        pending = self._pending(task, trusted_report)
        public_agent = task["assigned_agents"][0]
        binding = ROBOT_BINDINGS[public_agent]
        native_agent = binding["native_agent_id"]
        navigator = self.navigators[native_agent]
        execution = task["execution"]
        end_s = float(execution["start_s"] + execution["duration_s"])
        if not math.isclose(navigator.episode_time_s,
                            float(trusted_report["session"]["current_time_s"]), abs_tol=1e-6):
            raise ValueError("native algorithm clock diverged from GoalSession")
        skill = task["skill"]
        arguments = task["arguments"]
        if skill == "ground_navigation":
            route_ids = arguments.get("route_ids")
            if not isinstance(route_ids, list) or len(route_ids) != 1:
                raise ValueError("algorithm navigation requires exactly one route")
            from_region = self.robot_regions[public_agent]
            to_region = str(arguments.get("region_id"))
            leg_id, route = _leg_for(native_agent, from_region, to_region, self.routes)
            public_route = self.public_routes.get(str(route_ids[0]))
            if (public_route is None
                    or public_route["from_region_id"] != from_region
                    or public_route["to_region_id"] != to_region):
                raise ValueError(
                    f"algorithm plan route {route_ids[0]} does not cover "
                    f"{from_region}->{to_region}")
            native = navigator.drive(leg_id, deadline_s=end_s)
            self.robot_regions[public_agent] = to_region
            native["task_id"] = task["task_id"]
            native["episode_id"] = episode_id
            native["producer_id"] = public_agent
            native["native_agent_id"] = native_agent
            native["public_route_id"] = str(route_ids[0])
            native["window_hold"] = navigator.wait_until(end_s)
            _saved, evidence_ref = self._record(native)
        elif skill == "observe":
            region = str(arguments.get("region_id"))
            if self.robot_regions[public_agent] != region:
                raise ValueError("algorithm observation agent has not arrived in its region")
            scene_observation = navigator.observe_region(region)
            stamp = scene_observation["stamp"]
            observation_id = f"{episode_id}:{region}:{stamp['simulation_step']}"
            digest = "sha256:" + canonical_sha256({
                "episode_id": episode_id, "region_id": region,
                "producer_id": public_agent, "scene_observation": scene_observation,
            })
            service_row = self.readiness.record_observation(
                region_id=region, observation_id=observation_id,
                simulation_step=stamp["simulation_step"], time_s=stamp["time_s"],
                producer_id=public_agent, evidence_digest=digest)
            native = {"kind": "observation", "episode_id": episode_id,
                      "task_id": task["task_id"], "region_id": region,
                      "producer_id": public_agent, "native_agent_id": native_agent,
                      "stamp": stamp, "scene_observation": scene_observation,
                      "service_record": service_row}
            native["window_hold"] = navigator.wait_until(end_s)
            _saved, evidence_ref = self._record(native)
        elif skill == "relay":
            if (self.robot_regions[public_agent] != "restaurant_staging"
                    or arguments.get("region_id") != "restaurant_staging"):
                raise ValueError("algorithm readiness relay must run from staging")
            snapshot = self.readiness.snapshot()
            refs = sorted(row["observation_id"] for row in snapshot["observations"].values())
            publication_stamp = navigator.step_once()
            publication = self.readiness.publish_relay(
                message_id=f"{episode_id}:readiness-relay",
                simulation_step=publication_stamp["simulation_step"],
                time_s=publication_stamp["time_s"], observation_refs=refs)
            receipt_stamp = navigator.step_once()
            business_receipt = self.readiness.commit_readiness_receipt(
                receipt_id=f"{episode_id}:readiness-receipt",
                simulation_step=receipt_stamp["simulation_step"],
                time_s=receipt_stamp["time_s"], observation_refs=refs)
            native = {"kind": "relay_and_receipt", "episode_id": episode_id,
                      "task_id": task["task_id"], "producer_id": public_agent,
                      "native_agent_id": native_agent,
                      "publication_stamp": publication_stamp,
                      "receipt_stamp": receipt_stamp,
                      "relay_publication": publication,
                      "business_receipt": business_receipt}
            native["window_hold"] = navigator.wait_until(end_s)
            _saved, evidence_ref = self._record(native)
        else:
            raise ValueError(f"unsupported algorithm Restaurant skill: {skill}")
        receipt = {
            "backend_id": self.BACKEND_ID,
            "receipt_ref": f"{episode_id}/{task['task_id']}",
            "task_id": task["task_id"], "action_id": pending["dispatch_action_id"],
            "status": "succeeded", "completed_at_s": end_s,
            "episode_id": episode_id, "evidence_refs": [evidence_ref],
        }
        return {"status": "succeeded", "completed_at_s": end_s, "receipt": receipt,
                "runtime_state": {"robot_regions": deepcopy(self.robot_regions),
                                  "object_states": {}}}

    def cancel(self, task: Mapping[str, Any], *, episode_id: str,
               trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "cancelled",
                "receipt": {"backend_id": self.BACKEND_ID, "status": "cancelled",
                            "task_id": task["task_id"], "episode_id": episode_id},
                "runtime_state": {"robot_regions": deepcopy(self.robot_regions),
                                  "object_states": {}}}


# -- request contract --------------------------------------------------------

def prepare_episode(output_root: str | Path, *, products_root: str | Path,
                    source_usd: str | Path, device: str, algorithm: str) -> dict[str, Any]:
    if algorithm not in ALGORITHMS:
        raise ValueError(f"unknown algorithm participant: {algorithm}")
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    products = Path(products_root).expanduser().resolve()
    source = Path(source_usd).expanduser().resolve()
    from tools.assets.build_restaurant_products import validate_products

    product_validation = validate_products(products, source_usd=source)
    metadata_path = products / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    bundle = build_goal_task_package("restaurant")
    public_task = _algorithm_public_task(bundle)
    run_id = uuid.uuid4().hex
    request = {
        "schema": "eai.restaurant.formal_goal_algorithm_episode_request", "version": 1,
        "run_id": run_id, "episode_id": f"restaurant-algo:{run_id}",
        "algorithm": algorithm,
        "scene_key": restaurant_config.BENCHMARK_SCENE_KEY,
        "products_root": str(products), "source_usd": str(source),
        "source_sha256": file_sha256(source),
        "metadata_sha256": file_sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": product_validation,
        "selection": _algorithm_selection(),
        "requested_device": device,
        "robot_bindings": deepcopy(ROBOT_BINDINGS),
        "region_bindings": deepcopy(REGION_BINDINGS),
        "physical_routes": _leg_routes(products),
        "public_task": public_task,
        "public_task_sha256": canonical_sha256(public_task),
        "participant_api_sha256": canonical_sha256(
            build_goal_participant_api(public_task)),
        "source_files": _source_files(),
        "executor": {"kind": "serial_ground_executor_v1",
                     "concurrent_driving": False,
                     "pool_note": "executor coverage v1: the two episode-grade Carters "
                                  "carry drivers; iris_1 holds the aerial declaration "
                                  "and its anchor but has no dispatchable v1 mission"},
        "release_gates": {"navigation_ready": False, "execution_validated": False,
                          "scoreable": False},
        "archive_verifier": "pending_independent_disk_verifier",
    }
    _write_json(root / "request.json", request)
    return request


def _source_files() -> dict[str, str]:
    files = [
        Path(__file__).resolve(),
        ROOT / "demo/benchmark/goal_restaurant_episode.py",
        ROOT / "demo/benchmark/goal_external_adapters.py",
        ROOT / "demo/benchmark/goal_episode_runner.py",
        ROOT / "demo/benchmark/goal_driven_contract.py",
        ROOT / "demo/benchmark/goal_driven_tasks.py",
        ROOT / "demo/restaurant_benchmark/runtime_probe.py",
        ROOT / "demo/restaurant_benchmark/navigation_probe.py",
        ROOT / "demo/restaurant_benchmark/contact_probe.py",
        ROOT / "demo/restaurant_benchmark/service_backend.py",
        ROOT / "source/EAI/EAI/scenario_spec/geometry.py",
        ROOT / "source/EAI/EAI/scenario_spec/oracle.py",
    ]
    return {str(path): file_sha256(path) for path in files}


def _validate_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise ValueError("algorithm request must be an object")
    run_id = request.get("run_id")
    try:
        parsed = uuid.UUID(hex=run_id) if isinstance(run_id, str) else None
    except ValueError:
        parsed = None
    bundle = build_goal_task_package("restaurant")
    public_task = _algorithm_public_task(bundle)
    if (request.get("schema") != "eai.restaurant.formal_goal_algorithm_episode_request"
            or request.get("version") != 1
            or parsed is None or parsed.version != 4 or parsed.hex != run_id
            or request.get("episode_id") != f"restaurant-algo:{run_id}"
            or request.get("algorithm") not in ALGORITHMS
            or request.get("scene_key") != restaurant_config.BENCHMARK_SCENE_KEY
            or request.get("selection") != _algorithm_selection()
            or request.get("robot_bindings") != ROBOT_BINDINGS
            or request.get("region_bindings") != REGION_BINDINGS
            or request.get("requested_device") not in {"cpu", "cuda:0"}
            or request.get("source_sha256") != restaurant_config.SOURCE_SHA256
            or request.get("product_validation", {}).get("validated") is not True
            or not isinstance(request.get("physical_routes"), Mapping)
            or set(request["physical_routes"]) != set(AGENT_PHYSICAL_LEGS)
            or request.get("public_task") != public_task
            or request.get("public_task_sha256") != canonical_sha256(public_task)
            or request.get("participant_api_sha256")
            != canonical_sha256(build_goal_participant_api(public_task))
            or request.get("source_files") != _source_files()
            or request.get("executor") != {
                "kind": "serial_ground_executor_v1", "concurrent_driving": False,
                "pool_note": "executor coverage v1: the two episode-grade Carters "
                             "carry drivers; iris_1 holds the aerial declaration "
                             "and its anchor but has no dispatchable v1 mission"}
            or request.get("release_gates") != {
                "navigation_ready": False, "execution_validated": False,
                "scoreable": False}
            or request.get("archive_verifier") != "pending_independent_disk_verifier"):
        raise ValueError("invalid Restaurant algorithm episode request")
    return request


def _load_request(root: Path) -> dict[str, Any]:
    request = _validate_request(
        json.loads((root / "request.json").read_text(encoding="utf-8")))
    products = Path(request["products_root"]).resolve()
    if (file_sha256(Path(request["source_usd"]).resolve()) != request["source_sha256"]
            or file_sha256(products / "bench_meta.json") != request["metadata_sha256"]
            or _leg_routes(products) != request["physical_routes"]):
        raise ValueError("Restaurant algorithm episode inputs changed after preparation")
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )
    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant algorithm episode bundle changed after preparation")
    return request


# -- worker ------------------------------------------------------------------

def run_worker(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    public_task = _algorithm_public_task(build_goal_task_package("restaurant"))
    participant_api = build_goal_participant_api(public_task)
    if canonical_sha256(participant_api) != request["participant_api_sha256"]:
        raise ValueError("participant API drifted from the frozen request")
    if canonical_sha256(public_task) != request["public_task_sha256"]:
        raise ValueError("derived public task drifted from the frozen request")
    report = {
        "schema": "eai.restaurant.formal_goal_algorithm_episode_report", "version": 1,
        "run_id": request["run_id"], "episode_id": request["episode_id"],
        "algorithm": request["algorithm"],
        "status": "starting", "business_episode_execution_validated": False,
        "execution_validated": False, "scoreable": False,
    }
    report_path = root / "restaurant_algorithm_episode_report.json"
    _write_json(report_path, report)
    from simulator import SimulatorLaunchConfig, open_simulator_session

    def configure(env_cfg: Any) -> None:
        env_cfg.sim.enable_scene_query_support = True
        env_cfg.episode_length_s = 3600.0

    launch = SimulatorLaunchConfig(
        env=request["selection"]["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=request["selection"], num_envs=1,
        device=request["requested_device"], seed=17, headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=configure)
    try:
        with open_simulator_session(launch) as session:
            import torch

            expected_agents = sorted(
                binding["native_agent_id"] for binding in ROBOT_BINDINGS.values())
            if sorted(session.possible_agents) != expected_agents:
                raise RuntimeError(
                    f"algorithm session agents {session.possible_agents} != {expected_agents}")
            iris_anchor = restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["iris_1"]
            hover = torch.tensor([list(iris_anchor)], device=session.device,
                                 dtype=torch.float32).expand(session.num_envs, 3)
            from demo.restaurant_benchmark import navigation_probe
            for _ in range(navigation_probe.SETTLE_STEPS):
                commands = {agent: torch.zeros((session.num_envs, 3), device=session.device)
                            for agent in session.possible_agents if agent != "iris_1"}
                commands["iris_1"] = hover.clone()
                session.env.step(commands)
            products_root = Path(request["products_root"])
            routes = request["physical_routes"]
            # Creation order matters for the shared contact-processing toggle:
            # close in reverse so each navigator restores what it observed.
            # Only the ground anchors get drivers; iris_1 has no dispatchable
            # v1 mission and holds its anchor through the episode.
            navigators = {}
            try:
                episode_step_origin = int(session.base_env.sim.current_time_step_index)
                for public_id in ALGORITHM_POOL:
                    binding = ROBOT_BINDINGS[public_id]
                    if binding["mobility"] != "ground":
                        continue
                    navigators[binding["native_agent_id"]] = SharedClockCarterNavigator(
                        session, binding["native_agent_id"], products_root, routes,
                        request["region_bindings"],
                        episode_step_origin=episode_step_origin)
                backend = RestaurantMultiAgentGoalBackend(
                    navigators, routes,
                    request["public_task"]["public_world"]["routes"],
                    episode_id=request["episode_id"])
                participant = RestaurantTeamWeaverParticipant(
                    request["public_task"], routes)
                evaluator = lambda episode_id, trusted: evaluate_restaurant_business(
                    backend, episode_id, trusted)
                episode_root = root / "goal_episode"
                result = FormalGoalEpisodeRunner(
                    request["public_task"], participant_api, participant,
                    backend, output_root=episode_root, episode_id=request["episode_id"],
                    evaluator=evaluator, max_turns=48,
                ).run(initial_state={
                    "robot_regions": {public: ROBOT_BINDINGS[public]["spawn_region_id"]
                                      for public in ALGORITHM_POOL},
                    "object_states": {}})
            finally:
                for native in reversed(list(navigators)):
                    navigators[native].close()
            _write_json(episode_root / "native_evidence_log.json", {
                "schema": "eai.restaurant.formal_goal_native_evidence", "version": 1,
                "episode_id": request["episode_id"], "records": backend.evidence})
            _write_json(episode_root / "readiness_service_snapshot.json", {
                "schema": "eai.restaurant.formal_goal_readiness_snapshot", "version": 1,
                "episode_id": request["episode_id"],
                "service": backend.readiness.snapshot(),
                "evaluation": backend.business_evaluation})
            _write_json(episode_root / "goal_episode_source.json", {
                "schema": "eai.restaurant.formal_goal_episode_source", "version": 1,
                "episode_id": request["episode_id"],
                "request_sha256": file_sha256(root / "request.json"),
                "source_files": request["source_files"]})
            passed = (result["error"] is None
                      and result["skill_receipt_count"] == len(participant._plan["tasks"])
                      and result["business_outcomes_validated"] is True
                      and backend.business_evaluation is not None)
            report.update(
                status="completed" if passed else "failed",
                actual_device=str(session.device),
                goal_episode_result=result,
                participant_trace=participant.trace,
                native_evidence_log_sha256=file_sha256(
                    episode_root / "native_evidence_log.json"),
                readiness_service_snapshot_sha256=file_sha256(
                    episode_root / "readiness_service_snapshot.json"),
                business_episode_execution_validated=passed,
                execution_validated=False, scoreable=False)
            _write_json(report_path, report)
    except BaseException as exc:
        report.update(status="failed", error=repr(exc), traceback=traceback.format_exc(),
                      business_episode_execution_validated=False,
                      execution_validated=False, scoreable=False)
        _write_json(report_path, report)
        raise
    if report["business_episode_execution_validated"] is not True:
        raise RuntimeError("Restaurant algorithm business episode did not validate")
    return report


def execute_episode(output_root: str | Path, *, timeout_s: float = 1800.0) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    _load_request(root)
    lock_path = ROOT / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable,
               "-m", "demo.benchmark.goal_restaurant_algorithm_episode",
               "--worker", "--output-root", str(root)]
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Restaurant native probe is running") from exc
        environment = {**os.environ,
                       "EAI_RESTAURANT_BENCHMARK_ROOT": json.loads(
                           (root / "request.json").read_text(encoding="utf-8"))["products_root"]}
        started = time.monotonic()
        with (root / "isaac.log").open("x", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=ROOT, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       text=True, timeout=timeout_s, check=False)
        duration = time.monotonic() - started
    report = json.loads(
        (root / "restaurant_algorithm_episode_report.json").read_text(encoding="utf-8"))
    process = {"returncode": completed.returncode, "duration_s": duration,
               "report_sha256": file_sha256(
                   root / "restaurant_algorithm_episode_report.json"),
               "isaac_log_sha256": file_sha256(root / "isaac.log"),
               "business_episode_execution_validated":
                   report.get("business_episode_execution_validated") is True}
    _write_json(root / "process.json", process)
    if completed.returncode != 0 or not process["business_episode_execution_validated"]:
        raise RuntimeError("Restaurant algorithm episode worker failed")
    return process


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="teamweaver")
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        report = run_worker(args.output_root)
    else:
        if args.products_root is None or args.source_usd is None:
            parser.error("--products-root and --source-usd are required")
        prepare_episode(args.output_root, products_root=args.products_root,
                        source_usd=args.source_usd, device=args.device,
                        algorithm=args.algorithm)
        execute_episode(args.output_root, timeout_s=args.timeout_s)
        report = json.loads(
            (args.output_root / "restaurant_algorithm_episode_report.json").read_text(
                encoding="utf-8"))
    print(json.dumps(
        {
            "episode_id": report.get("episode_id"),
            "status": report.get("status"),
            "business_episode_execution_validated":
                report.get("business_episode_execution_validated", False),
            "algorithm": report.get("algorithm") or args.algorithm,
            "execution_validated": False,
            "scoreable": False,
        },
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
