"""Algorithm-participant restaurant episodes over a measured mixed executor.

The witness episode (``demo.benchmark.goal_restaurant_episode``) proves the
business closed loop with one fixed serial participant.  This module runs the
same physical substrate with a *measured allocator* as the participant: the
real TeamWeaver pipeline selects the team and assigns each service mission to
an agent, and the episode measures those decisions against native execution.

v1 scope, frozen deliberately: the two Carter anchors carry episode-grade
navigation/observation drivers and the hover-held Iris can publish the final
non-motion relay from its actual overview pose. Tasks execute serially through
the shared FormalGoalEpisodeRunner contract. Aerial service-zone inspection,
concurrent driving and pools beyond these three agents are executor v2 work.
The on-disk verifier independently re-derives the allocation/plan and checks
native arrivals, observations, relay pose/stamps, receipts and business
evidence. Runs still keep ``execution_validated=false`` and ``scoreable=false``
until a new native archive is accepted under the release process.
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
from typing import Any, Callable, Mapping, Optional
import uuid

from .goal_driven_contract import (
    GOAL_PLAN_VERSION,
    build_goal_participant_api,
    validate_goal_plan,
)
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner
from .goal_restaurant_episode import (
    EVALUATOR_REF,
    NAVIGATION_WINDOW_S,
    NativeCarterEpisodeNavigator,
    OBSERVATION_WINDOW_S,
    REGION_OBSERVATION_TOLERANCE_M,
    RELAY_WINDOW_S,
    ROOT,
    _A,
    _B,
    _E,
    _action,
    _finite_number,
    _verify_archived_contact,
    _write_json,
    evaluate_restaurant_business,
)
from .provenance import canonical_sha256, file_sha256
from .runtime import route_waypoints
from demo.restaurant_benchmark import config as restaurant_config
from demo.restaurant_benchmark import contact_probe, navigation_probe, runtime_probe


ROBOT_BINDINGS = {
    "restaurant_carter_1": {"native_agent_id": "carter_1", "spawn_region_id": "restaurant_staging",
                            "spawn_xy_m": list(_A), "mobility": "ground"},
    "restaurant_carter_2": {"native_agent_id": "carter_2", "spawn_region_id": "restaurant_staging",
                            "spawn_xy_m": list(restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_2"][:2]),
                            "mobility": "ground"},
    # Iris spawns and remains hover-held at its public overview anchor. The
    # service-zone inspections remain ground-only, while relay is a non-motion
    # event-bus skill and can execute from this current public region.
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
ALGORITHMS = ("teamweaver", "emos")
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
    """Restrict the shared pool to the v1 executable algorithm pool.

    The shared bundle's eight-anchor pool stays the authoring declaration;
    algorithm episodes declare measured executor coverage by filtering to the
    two Carter navigation/observation anchors plus the hover-held Iris relay
    member. Every remaining selection must be executable or fail closed. The
    derived task keeps the shared schema exactly, including staging's public
    aerial+ground access modes.
    """
    task = deepcopy(dict(bundle["public_task"]))
    pool = [row for row in task["candidate_robot_pool"] if row["id"] in ALGORITHM_POOL]
    if len(pool) != len(ALGORITHM_POOL):
        raise ValueError("shared restaurant pool lost an algorithm-pool declaration")
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
# Raw capability requirements per mission. Relay is a communication action;
# it does not inherit a mobility requirement from the producer's region.
MISSION_REQUIREMENTS = {
    "kitchen_inspection": ("ground_navigation", "observe"),
    "dining_inspection": ("ground_navigation", "observe"),
    "readiness_relay": ("relay",),
}
# Stable catalogue order drives both allocators. Never rebuild this from a
# set: PYTHONHASHSEED would otherwise reshuffle mission walk order and can
# assign relay before the two inspections, leaving an aerial member as the
# only remaining body for a ground-only mission.
MISSION_ORDER = tuple(MISSION_REQUIREMENTS)
# Physical inspection targets are region centers. Readiness relay is a
# non-motion event-bus action and has no invented travel target.
MISSION_TARGET_XY = {
    "kitchen_inspection": _E,
    "dining_inspection": _B,
}


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
    ground agent (region continuity is per agent). The relay closes the
    episode from the relay agent's current public region: staging for a
    returned Carter, overview for the hover-held Iris. Plan arguments
    reference the task's public route graph; the backend resolves the
    assigned agent's physical leg for the same transition at execution time,
    with windows from the frozen per-leg table.
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
    if relay_agent != relay_public:
        raise ValueError("relay agent does not match the mission assignment")
    # Every ground inspection returns to its ground spawn region. Iris never
    # moves during this executor version and relays from its hover-held public
    # overview region. No symbolic location or physical pose is fabricated.
    relay_region = ROBOT_BINDINGS[relay_public]["spawn_region_id"]
    append("restaurant_readiness_relay", "relay",
           {"message_kind": "restaurant_readiness_receipt",
            "region_id": relay_region},
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
    is executor vocabulary. Inspection positions use public spawn anchors and
    region centers. Relay is a virtual non-motion target, so it adds no false
    travel distance.
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
        region_center = dict(MISSION_TARGET_XY)
        # Both participants translate the same public capability requirements
        # in MISSION_ORDER so hash seed cannot reshuffle the catalogue.
        requirement_axes = {
            mission: tuple(sorted({axis.get(cap, cap)
                                   for cap in MISSION_REQUIREMENTS[mission]}))
            for mission in MISSION_ORDER
        }
        raw_requirements = {
            mission: set(MISSION_REQUIREMENTS[mission])
            for mission in MISSION_ORDER
        }
        task_types = {
            mission: (TaskType.ESTABLISH_RELAY
                      if mission == "readiness_relay" else TaskType.NAVIGATE)
            for mission in MISSION_ORDER
        }

        def robot_snapshot(row: Mapping[str, Any], *, busy: bool = False) -> RobotSnapshot:
            values = {"navigation": 0.0, "sensing": 0.0, "manipulation": 0.0,
                      "payload": 0.0, "communication": 0.0}
            for cap in row["public_capabilities"]:
                key = axis.get(cap, cap)
                values[key] = max(values.get(key, 0.0), 1.0)
            position = tuple(ROBOT_BINDINGS[row["id"]]["spawn_xy_m"])
            return RobotSnapshot(
                name=row["id"], position=position, base_capabilities=values,
                reliability={name: 1.0 for name in values}, equipment=frozenset(),
                busy=busy, current_task=None, current_load=0, safe=True)

        targets = tuple(
            TargetSnapshot(
                ref=mission,
                position=(None if mission == "readiness_relay"
                          else (*region_center[mission], 0.0)),
                available=True,
                kind="virtual" if mission == "readiness_relay" else "region",
                compatible_task_types=(task_types[mission],),
            )
            for mission in MISSION_ORDER)

        class _Decomposer:
            def decompose(self, instruction, world_state, **_kwargs):
                rows = []
                for mission in MISSION_ORDER:
                    axes = requirement_axes[mission]
                    rows.append(SemanticTask(
                        task_id=mission, task_type=task_types[mission],
                        description="restaurant_mission", target_ref=mission,
                        priority=1,
                        requirements={key: CapabilityRequirement(1.0, 1.0, True)
                                      for key in axes},
                        prerequisites=(), can_parallel=True,
                        estimated_duration_s=(RELAY_WINDOW_S
                                              if mission == "readiness_relay" else 45.0),
                        preferred_agent=None))
                return SemanticDecompositionResult(tasks=tuple(rows), source="catalog",
                                                   attempts=1)

        catalog = list(_Decomposer().decompose(None, None).tasks)
        allocator = DynamicMIQPAllocator(prefer_miqp=False)
        assignment: dict[str, str] = {}
        # Ordered remainder + busy agents across rounds: never rebuild the
        # open set from a hash-ordered set comprehension.
        remaining = list(MISSION_ORDER)
        used_agents: set[str] = set()
        for round_index in range(len(MISSION_ORDER)):
            if not remaining:
                break
            open_ids = set(remaining)
            world = SymbolicWorldState(
                hazard_id=0, hazard_position=(0.0, 0.0, 0.0), hazard_active=False,
                targets=targets,
                robots=tuple(
                    robot_snapshot(row, busy=(row["id"] in used_agents))
                    for row in pool_rows),
                extinguisher_available=False, extinguisher_carrier=None,
                extinguisher_delivered=False, rescue_channel_open=True,
                completed_task_ids=(), failed_task_ids=(), recent_feedback=(),
                observations={})
            batch = tuple(task for task in catalog if task.task_id in open_ids)
            result = allocator.allocate(world, batch)
            round_map = {row.task_id: row.robot_name for row in result.assignments
                         if row.task_id in open_ids}
            self.trace.append({"event": "allocation_round", "round": round_index,
                               "solver": getattr(result, "solver", "miqp"),
                               "assignment": dict(round_map),
                               "remaining": list(remaining)})
            if not round_map:
                break
            for mission, agent in round_map.items():
                if mission in assignment:
                    raise RuntimeError(f"TeamWeaver reassigned mission {mission}")
                assignment[mission] = agent
                used_agents.add(agent)
            remaining = [mission for mission in remaining if mission not in round_map]
        if remaining:
            raise RuntimeError(
                f"TeamWeaver left restaurant missions unassigned: {remaining}")
        if set(assignment) != set(MISSION_ORDER):
            raise RuntimeError(
                f"TeamWeaver assignment keys diverge from MISSION_ORDER: {assignment}")
        public_caps = {row["id"]: set(row["public_capabilities"]) for row in pool_rows}
        for mission, agent in assignment.items():
            if not raw_requirements[mission] <= public_caps[agent]:
                raise ValueError(f"{agent} cannot cover {mission}")
            binding = ROBOT_BINDINGS[agent]
            if mission != "readiness_relay" and binding["mobility"] != "ground":
                raise ValueError(f"{agent} cannot cover ground mission {mission}")
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


from demo.restaurant_benchmark.service_backend import (
    RestaurantReadinessBackend,
    evaluate_readiness_receipt,
)


_EMOS_POLICY_NAME = "restaurant-goal-native-deterministic"
_EMOS_ROUND_TIMEOUT_S = 240.0
_EMOS_DISCUSSION_LOG_DIR = ROOT / "tmp" / "emos_discussion_restaurant"

# Pose providers for EMOS. Focused / authoring allocation uses the frozen public
# spawn map; native episode wiring injects a live root-state callback. Never
# label ``public_spawn_anchor`` as a PhysX sample.
POSITION_SOURCE_PUBLIC_SPAWN_ANCHOR = "public_spawn_anchor"
POSITION_SOURCE_NATIVE_ROOT_STATE = "native_root_state"
POSITION_SOURCE_NOT_CONSUMED = "allocator_does_not_consume_positions"


def restaurant_public_spawn_xyz_m(public_id: str) -> tuple[float, float, float]:
    """Frozen public spawn for a pool member (xy from bindings, z from config).

    This is a disclosed selection / spawn-map anchor, not a live root-state
    sample. Iris uses the overview anchor; Carters use their staging spawns.
    """
    binding = ROBOT_BINDINGS[public_id]
    native = binding["native_agent_id"]
    spawn_xyz = restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M[native]
    xy = binding["spawn_xy_m"]
    return (float(xy[0]), float(xy[1]), float(spawn_xyz[2]))


def make_restaurant_public_spawn_pos_fn(
    agent_ids: Optional[Any] = None,
) -> Callable[[str], tuple[float, float, float]]:
    """Snapshot public spawn anchors for EMOS ``get_robot_pos_fn`` injection."""
    ids = tuple(agent_ids) if agent_ids is not None else ALGORITHM_POOL
    snapshot = {agent_id: restaurant_public_spawn_xyz_m(agent_id) for agent_id in ids}

    def _fn(robot_name: str) -> tuple[float, float, float]:
        if robot_name not in snapshot:
            raise KeyError(
                f"restaurant public spawn anchor unavailable for {robot_name!r}"
            )
        return snapshot[robot_name]

    return _fn


class RestaurantNativeRootStateProvider:
    """Read public robot positions from the settled native articulation state.

    The provider is deliberately strict: a formal EMOS allocation cannot fall
    back to public spawn anchors or an invented origin when a native body is
    missing or unhealthy.  ``metadata`` is archived with the participant
    trace so reviewers can bind the allocator input to a physics step.
    """

    def __init__(self, base_env: Any, public_ids: Any = ALGORITHM_POOL):
        self._base_env = base_env
        self._bindings = {
            public_id: ROBOT_BINDINGS[public_id]["native_agent_id"]
            for public_id in tuple(public_ids)
        }
        articulations = getattr(getattr(base_env, "scene", None), "articulations", None)
        if not isinstance(articulations, Mapping):
            raise RuntimeError("Restaurant native position provider has no articulation scene")
        missing = sorted(set(self._bindings.values()) - set(articulations))
        if missing:
            raise RuntimeError(
                f"Restaurant native position provider missing articulations: {missing}")
        self._samples: dict[str, dict[str, Any]] = {}

    def __call__(self, public_id: str) -> tuple[float, float, float]:
        try:
            native_id = self._bindings[public_id]
        except KeyError as exc:
            raise KeyError(f"unknown Restaurant public robot {public_id!r}") from exc
        articulation = self._base_env.scene.articulations[native_id]
        try:
            state = articulation.data.root_state_w[0].detach().cpu().tolist()
        except Exception as exc:
            raise RuntimeError(
                f"Restaurant native root state unavailable for {public_id} ({native_id})"
            ) from exc
        if (not isinstance(state, list) or len(state) < 13
                or not navigation_probe._finite_root_state(state)):
            raise RuntimeError(
                f"Restaurant native root state is invalid for {public_id} ({native_id})")
        step = getattr(getattr(self._base_env, "sim", None),
                       "current_time_step_index", None)
        if isinstance(step, bool) or not isinstance(step, int):
            raise RuntimeError("Restaurant native position provider has no integer physics step")
        self._samples[public_id] = {
            "native_agent_id": native_id,
            "physics_step": int(step),
            "root_state_w": [float(value) for value in state],
        }
        return tuple(float(value) for value in state[:3])

    def metadata(self) -> dict[str, Any]:
        if set(self._samples) != set(self._bindings):
            missing = sorted(set(self._bindings) - set(self._samples))
            raise RuntimeError(
                f"Restaurant native position provider has not sampled: {missing}")
        return {
            "position_source": POSITION_SOURCE_NATIVE_ROOT_STATE,
            "capture": "settled_articulation_root_state_w",
            "samples": deepcopy(self._samples),
        }


def _emos_restaurant_policy(request):
    """Deterministic capability-matching EMOS policy (restaurant missions).

    Walks scenario.subtasks in insertion order (must be MISSION_ORDER) and
    assigns each open mission to the first still-unused pool-order agent
    whose public capabilities cover the requirements. Unique agents plus
    inspection-before-relay catalogue order make Iris the only remaining
    relay-capable body after the two Carters take the ground inspections —
    without hard-coding Iris onto readiness_relay.
    """
    scenario = request.scenario
    order = list(request.robot_positions)
    profiles = scenario.robot_profiles
    assignments: dict[str, str] = {}
    used_agents: set[str] = set()
    for subtask_id, spec in scenario.subtasks.items():
        required = set(spec.match_keywords)
        for name in order:
            if name in used_agents:
                continue
            profile = profiles.get(name)
            capabilities = set(profile.capabilities) if profile is not None else set()
            if required <= capabilities:
                assignments[name] = subtask_id
                used_agents.add(name)
                break
        else:
            raise ValueError(
                f"EMOS restaurant policy found no eligible unused agent for {subtask_id}")
    if len(assignments) != len(scenario.subtasks):
        raise ValueError("EMOS restaurant policy left missions unassigned")
    return assignments


class RestaurantEmosParticipant:
    """Real EMOS discussion manager deciding the restaurant episode's team.

    The measured decisions - team selection, mission assignment and the
    relay agent - come from the real ``EMOSDiscussionManager`` discussion
    boundary over the public pool. Inspection targets use their real region
    centers; readiness relay has no travel target. The deterministic
    capability-matching policy runs through the manager's public
    ``set_assignment_policy`` boundary; the structural plan expansion is
    shared with the TeamWeaver participant, so the two algorithms are
    measured on identical executor vocabulary.

    Robot poses for EMOS scene / assignment requests are injected explicitly:
    a caller-supplied ``get_robot_pos_fn`` (native root-state) takes priority;
    otherwise the focused path uses labeled ``public_spawn_anchor`` snapshots.
    Neither path silently invents ``(0, 0, 0)``.
    """

    def __init__(
        self,
        task_spec: Mapping[str, Any],
        routes: Mapping[str, Mapping[str, Any]],
        *,
        get_robot_pos_fn: Optional[
            Callable[[str], tuple[float, float, float]]
        ] = None,
        position_source: Optional[str] = None,
    ):
        self._task_spec = deepcopy(dict(task_spec))
        self._routes = deepcopy(dict(routes))
        self._plan: dict[str, Any] | None = None
        self._opened = False
        self._next = 0
        self._terminal: dict[str, str] = {}
        self.trace: list[dict[str, Any]] = []
        if get_robot_pos_fn is not None:
            self._get_robot_pos_fn = get_robot_pos_fn
            self._position_source = (
                position_source or POSITION_SOURCE_NATIVE_ROOT_STATE
            )
        else:
            self._get_robot_pos_fn = None
            self._position_source = (
                position_source or POSITION_SOURCE_PUBLIC_SPAWN_ANCHOR
            )

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "restaurant-native-emos", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api
        self.trace.append({"event": "initialize", "planner": "EMOSDiscussionManager",
                           "policy": _EMOS_POLICY_NAME,
                           "position_source": self._position_source})

    def _resolve_position_provider(
        self, pool_rows: list[Mapping[str, Any]]
    ) -> tuple[Callable[[str], tuple[float, float, float]], str]:
        if self._get_robot_pos_fn is not None:
            return self._get_robot_pos_fn, self._position_source
        if self._position_source not in {
            POSITION_SOURCE_PUBLIC_SPAWN_ANCHOR,
            None,
            "",
        }:
            raise RuntimeError(
                f"restaurant EMOS position_source={self._position_source!r} "
                "requires an explicit get_robot_pos_fn; refusing invented zeros"
            )
        agent_ids = [row["id"] for row in pool_rows]
        return (
            make_restaurant_public_spawn_pos_fn(agent_ids),
            POSITION_SOURCE_PUBLIC_SPAWN_ANCHOR,
        )

    def _allocate(self) -> dict[str, str]:
        import time as _time
        from .goal_external_adapters import _ensure_algorithm_roots
        _ensure_algorithm_roots()
        from emos.engine import EMOSDiscussionManager
        from emos.types import EMOSRobotAgentSpec, EMOSScenarioConfig, PositionRule, SubtaskSpec

        pool_rows = self._task_spec["candidate_robot_pool"]
        pos_fn, position_source = self._resolve_position_provider(pool_rows)
        # One-shot unique assignment over MISSION_ORDER. Building open
        # subtasks from set(remaining) was PYTHONHASHSEED-sensitive and could
        # schedule relay before inspections, then leave Iris as the only body
        # for a ground-only mission (or re-use Carter across rounds).
        subtasks = {
            mission: SubtaskSpec(
                id=mission, name=mission, description=f"restaurant:{mission}",
                match_keywords=sorted(MISSION_REQUIREMENTS[mission]))
            for mission in MISSION_ORDER
        }

        def _position_rule(mission: str) -> PositionRule:
            # Relay has no travel target; do not invent a staging waypoint that
            # would fake distance cost or imply Iris must leave overview.
            if mission == "readiness_relay":
                return PositionRule(kind="none", xy=None)
            return PositionRule(kind="fixed", xy=MISSION_TARGET_XY[mission])

        scenario = EMOSScenarioConfig(
            scenario_id="restaurant_goal_native", scene_title="Restaurant",
            anchor_label="restaurant opening readiness",
            task_description=(
                "Coordinate the restaurant opening-readiness inspection: one "
                "member inspects the kitchen service area, one inspects the "
                "dining service area, and a relay-capable member publishes "
                "the consolidated readiness receipt from its current region."),
            subtasks=subtasks,
            position_rules={mission: _position_rule(mission) for mission in MISSION_ORDER},
            robot_profiles={},
            preferred_fallback={},
            yellow_subtask_id="", green_subtask_id="")
        specs = {row["id"]: EMOSRobotAgentSpec(
                     agent_name=row["id"], robot_type=row["mobility"],
                     capabilities=list(row["public_capabilities"]))
                 for row in pool_rows}
        capability_map = {row["id"]: set(row["public_capabilities"]) for row in pool_rows}
        manager = EMOSDiscussionManager.build_from_agent_specs(
            None, specs, scenario, use_env_resume=False,
            discussion_log_dir=str(_EMOS_DISCUSSION_LOG_DIR),
            get_robot_pos_fn=pos_fn,
            position_source=position_source,
            strict_robot_positions=True)
        manager.set_assignment_policy(_emos_restaurant_policy, name=_EMOS_POLICY_NAME)
        manager.trigger(1, (0.0, 0.0, 0.0))
        request = manager._assignment_request
        if request is None:
            raise RuntimeError("EMOS assignment request was not built")
        robot_positions = {
            name: tuple(request.robot_positions[name]) for name in request.robot_positions
        }
        for name, xyz in robot_positions.items():
            if xyz[:2] == (0.0, 0.0) and xyz[2] == 0.0:
                raise RuntimeError(
                    f"EMOS restaurant refused invented origin pose for {name}: {xyz}"
                )
        self.trace.append({
            "event": "emos_robot_positions",
            "position_source": position_source,
            "robot_positions": {
                name: [float(v) for v in xyz] for name, xyz in robot_positions.items()
            },
        })
        metadata = getattr(pos_fn, "metadata", None)
        if callable(metadata):
            self.trace[-1]["position_provenance"] = metadata()
        result = None
        deadline = _time.monotonic() + _EMOS_ROUND_TIMEOUT_S
        while result is None and _time.monotonic() < deadline:
            result = manager.poll()
            if result is None:
                _time.sleep(0.01)
        if result is None:
            raise RuntimeError("EMOS discussion did not produce an assignment in time")
        assignment = {row.subtask_colour: row.robot_name for row in result.values()}
        self.trace.append({"event": "emos_assignment", "round": 0,
                           "manager": "EMOSDiscussionManager",
                           "assignment": dict(assignment),
                           "remaining": [],
                           "position_source": position_source,
                           "discussion_count": manager.discussion_count,
                           "discussion_elapsed_s": manager.last_discussion_elapsed_s})
        if set(assignment) != set(MISSION_ORDER):
            raise RuntimeError(
                f"EMOS left restaurant missions unassigned or invented ids: {assignment}")
        if len(set(assignment.values())) != len(assignment):
            raise RuntimeError(
                f"EMOS reused an agent across restaurant missions: {assignment}")
        for mission, agent in assignment.items():
            if not set(MISSION_REQUIREMENTS[mission]) <= capability_map[agent]:
                raise RuntimeError(f"EMOS assigned {mission} to ineligible {agent}")
            binding = ROBOT_BINDINGS[agent]
            if mission != "readiness_relay" and binding["mobility"] != "ground":
                raise RuntimeError(f"EMOS assigned ground mission {mission} to {agent}")
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
                plan_id="restaurant_emos_missions_v1")
            self.trace.append({"event": "plan_submitted",
                               "task_ids": [row["task_id"] for row in self._plan["tasks"]],
                               "assigned_team": self._plan["assigned_team"]})
            return [
                _action("restaurant_emos_select", "select_team",
                        {"agent_ids": list(self._plan["assigned_team"])}, now),
                _action("restaurant_emos_plan", "submit_plan",
                        {"plan": deepcopy(self._plan)}, now),
                _action("restaurant_emos_dispatch_0", "dispatch_skill",
                        {"task_id": self._plan["tasks"][0]["task_id"],
                         "evidence_refs": []}, now),
            ]
        for row in events:
            if row["type"] != "action_result":
                continue
            payload = row["payload"]
            if payload.get("status") in {"succeeded", "failed", "cancelled"}:
                self._terminal[str(payload["action_id"])] = str(payload["status"])
        previous = f"restaurant_emos_dispatch_{max(self._next - 1, 0)}"
        if self._next == 0:
            self._next = 1
        if previous not in self._terminal:
            return []
        if (self._terminal[previous] != "succeeded"
                or self._next >= len(self._plan["tasks"])):
            return []
        index = self._next
        self._next += 1
        return [_action(f"restaurant_emos_dispatch_{index}", "dispatch_skill",
                        {"task_id": self._plan["tasks"][index]["task_id"],
                         "evidence_refs": []}, now)]

    def close(self) -> None:
        return None


class SharedIdleHolds:
    """Keep unengaged agents stationary against zero-command creep.

    Measured on the algorithm executor: a Carter commanded to exact zeros
    still creeps at ~0.0024 m/s (0.34 m over a 140 s idle stretch, past the
    route-start tolerance), and an uncommanded Pegasus iris loses its lift
    wrench and falls.  Idle management is executor infrastructure, never an
    algorithm decision: each idle ground agent is tethered to the pose where
    it became idle (re-captured when its own mission ends) with a small
    proportional correction, and the aerial member is commanded its frozen
    anchor as an explicit hover goal.
    """

    POSITION_GAIN = 2.0
    YAW_GAIN = 1.5
    MAX_LINEAR = 0.06
    MAX_ANGULAR = 0.30

    def __init__(self, session: Any, aerial_agent: str, aerial_goal_xyz):
        import torch

        self.session = session
        self._torch = torch
        self._aerial_agent = aerial_agent
        self._aerial_goal = torch.tensor(
            [list(aerial_goal_xyz)], device=session.device,
            dtype=torch.float32).expand(session.num_envs, 3)
        self._hold_xy: dict[str, tuple[float, float]] = {}
        self._hold_yaw: dict[str, float] = {}
        self._last_active: str | None = None

    def _yaw(self, quat) -> float:
        w, x, y, z = (float(value) for value in quat)
        return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))

    def commands(self, exclude: str | None) -> dict[str, Any]:
        torch = self._torch
        commands: dict[str, Any] = {}
        for agent in self.session.possible_agents:
            if agent == exclude:
                continue
            state = self.session.base_env.scene.articulations[agent].data.root_state_w[0]
            state = state.detach().cpu().tolist()
            xy = (float(state[0]), float(state[1]))
            yaw = self._yaw(state[3:7])
            if agent == self._aerial_agent:
                commands[agent] = self._aerial_goal.clone()
                continue
            if self._last_active == agent:
                # The agent just finished its own active stretch: re-tether
                # at the pose where its mission left it.
                self._hold_xy[agent] = xy
                self._hold_yaw[agent] = yaw
            hold_xy = self._hold_xy.setdefault(agent, xy)
            hold_yaw = self._hold_yaw.setdefault(agent, yaw)
            dx, dy = hold_xy[0] - xy[0], hold_xy[1] - xy[1]
            distance = math.hypot(dx, dy)
            heading_error = (math.atan2(dy, dx) - yaw + math.pi) % (2 * math.pi) - math.pi
            linear = max(-self.MAX_LINEAR, min(self.MAX_LINEAR,
                                               self.POSITION_GAIN * distance * math.cos(heading_error)))
            angular = max(-self.MAX_ANGULAR, min(self.MAX_ANGULAR,
                                                 self.YAW_GAIN * (
                                                     (hold_yaw - yaw + math.pi) % (2 * math.pi) - math.pi)))
            command = torch.zeros((self.session.num_envs, 3), device=self.session.device)
            command[0, 0] = linear
            command[0, 2] = angular
            commands[agent] = command
        self._last_active = exclude
        return commands


class SharedClockRelayDriver:
    """Advance a stationary relay on the shared simulator clock.

    Relay is an event-bus operation, so it needs no navigation driver. Each
    step still advances the real scene with all agents held in place and
    stamps the selected producer's measured root state.
    """

    def __init__(self, session: Any, agent: str, *, episode_step_origin: int,
                 idle_holds: SharedIdleHolds):
        self.session = session
        self.agent = agent
        self.articulation = session.base_env.scene.articulations[agent]
        self.control_dt_s = float(session.base_env.step_dt)
        self._episode_step_origin = int(episode_step_origin)
        self._idle_holds = idle_holds

    def close(self) -> None:
        return None

    @property
    def episode_time_s(self) -> float:
        index = int(self.session.base_env.sim.current_time_step_index)
        return (index - self._episode_step_origin) * float(
            self.session.base_env.sim.get_physics_dt())

    def pose(self) -> list[float]:
        return self.articulation.data.root_state_w[0].detach().cpu().tolist()

    def _stamp(self) -> dict[str, Any]:
        root = self.pose()
        if not navigation_probe._finite_root_state(root):
            raise RuntimeError("non-finite Iris root state in formal Restaurant relay")
        return {
            "simulation_step": int(self.session.base_env.sim.current_time_step_index),
            "time_s": self.episode_time_s,
            "root_state_w": [float(value) for value in root],
        }

    def step_once(self) -> dict[str, Any]:
        self.session.env.step(self._idle_holds.commands(exclude=None))
        return self._stamp()

    def wait_until(self, target_time_s: float) -> dict[str, Any]:
        if target_time_s + 1e-9 < self.episode_time_s:
            raise RuntimeError("native Restaurant execution exceeded its GoalSession window")
        start = self._stamp()
        while self.episode_time_s + self.control_dt_s <= target_time_s + 1e-9:
            self.step_once()
        if not math.isclose(self.episode_time_s, target_time_s, abs_tol=1e-6):
            raise RuntimeError("GoalSession window is not aligned to the native control step")
        return {"kind": "native_window_hold", "start": start, "end": self._stamp()}


class SharedClockCarterNavigator(NativeCarterEpisodeNavigator):
    """Tie the witness navigator's episode clock to the shared sim clock.

    The witness navigator counts only its own control steps, which is exact
    for a single-agent episode.  With several agents stepping the same
    simulation, the episode clock must be the global simulation time since
    episode start; ``drive`` and ``wait_until`` both loop on this property,
    so the override alone keeps windows, deadlines and holds consistent.
    """

    def __init__(self, *args: Any, episode_step_origin: int,
                 idle_holds: "SharedIdleHolds", public_producer_id: str, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.public_producer_id = str(public_producer_id)
        self._episode_step_origin = int(episode_step_origin)
        # Every step carries commands for ALL agents: agents missing from an
        # env.step dict are skipped by apply_action entirely, which lets idle
        # Carters creep and the uncommanded iris fall out of the air.  The
        # shared holds object tethers unengaged agents (see its docstring).
        self._idle_holds = idle_holds

    def observe_region(self, region_id: str) -> dict[str, Any]:
        # The witness navigator stamps its fixed public producer into the
        # observation payload; multi-agent evidence must carry the acting
        # agent's public id, so re-stamp after the native query.
        observation = super().observe_region(region_id)
        observation["producer_id"] = self.public_producer_id
        return observation

    def step_once(self, command: tuple[float, float] | None = None) -> dict[str, Any]:
        self._command.zero_()
        if command is not None:
            self._command[0, 0] = float(command[0])
            self._command[0, 2] = float(command[1])
        step_dict = self._idle_holds.commands(exclude=self.agent)
        step_dict[self.agent] = self._command
        self.session.env.step(step_dict)
        self._control_steps += 1
        root = self.pose()
        if not navigation_probe._finite_root_state(root):
            raise RuntimeError("non-finite Carter root state in formal Restaurant episode")
        return self._stamp(root)

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
        navigator = self.navigators.get(native_agent)
        if navigator is None:
            raise RuntimeError(
                f"native executor has no driver for {public_agent} ({native_agent})")
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
            relay_region = str(arguments.get("region_id"))
            if self.robot_regions[public_agent] != relay_region:
                raise ValueError("algorithm readiness relay region is not the producer's current region")
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
        "executor": {"kind": "serial_mixed_executor_v1",
                     "concurrent_driving": False,
                     "pool_note": "executor coverage v1: the two episode-grade Carters "
                                  "carry navigation/observation drivers; hover-held "
                                  "iris_1 can execute stationary relay only"},
        "release_gates": {"navigation_ready": False, "execution_validated": False,
                          "scoreable": False},
        "archive_verifier": "demo.benchmark.goal_restaurant_algorithm_episode"
                           ".verify_algorithm_episode_archive",
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
                "kind": "serial_mixed_executor_v1", "concurrent_driving": False,
                "pool_note": "executor coverage v1: the two episode-grade Carters "
                             "carry navigation/observation drivers; hover-held "
                             "iris_1 can execute stationary relay only"}
            or request.get("release_gates") != {
                "navigation_ready": False, "execution_validated": False,
                "scoreable": False}
            or request.get("archive_verifier")
            != "demo.benchmark.goal_restaurant_algorithm_episode"
               ".verify_algorithm_episode_archive"):
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
            for _ in range(navigation_probe.SETTLE_STEPS):
                commands = {agent: torch.zeros((session.num_envs, 3), device=session.device)
                            for agent in session.possible_agents if agent != "iris_1"}
                commands["iris_1"] = hover.clone()
                session.env.step(commands)
            products_root = Path(request["products_root"])
            routes = request["physical_routes"]
            # Creation order matters for the shared contact-processing toggle:
            # close in reverse so each navigator restores what it observed.
            # Carters get navigation/observation drivers. Iris gets a
            # stationary relay driver that advances the same physical clock
            # while SharedIdleHolds continues commanding its hover anchor.
            navigators = {}
            try:
                episode_step_origin = int(session.base_env.sim.current_time_step_index)
                iris_anchor = restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["iris_1"]
                idle_holds = SharedIdleHolds(session, "iris_1", iris_anchor)
                for public_id in ALGORITHM_POOL:
                    binding = ROBOT_BINDINGS[public_id]
                    if binding["mobility"] == "aerial":
                        navigators[binding["native_agent_id"]] = SharedClockRelayDriver(
                            session, binding["native_agent_id"],
                            episode_step_origin=episode_step_origin,
                            idle_holds=idle_holds)
                        continue
                    navigators[binding["native_agent_id"]] = SharedClockCarterNavigator(
                        session, binding["native_agent_id"], products_root, routes,
                        request["region_bindings"],
                        episode_step_origin=episode_step_origin,
                        idle_holds=idle_holds, public_producer_id=public_id)
                backend = RestaurantMultiAgentGoalBackend(
                    navigators, routes,
                    request["public_task"]["public_world"]["routes"],
                    episode_id=request["episode_id"])
                participant_class = (
                    RestaurantTeamWeaverParticipant
                    if request["algorithm"] == "teamweaver"
                    else RestaurantEmosParticipant)
                participant_kwargs = {}
                if participant_class is RestaurantEmosParticipant:
                    native_position_provider = RestaurantNativeRootStateProvider(
                        session.base_env, ALGORITHM_POOL)
                    participant_kwargs = {
                        "get_robot_pos_fn": native_position_provider,
                        "position_source": POSITION_SOURCE_NATIVE_ROOT_STATE,
                    }
                else:
                    native_position_provider = None
                participant = participant_class(
                    request["public_task"], routes, **participant_kwargs)
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
                "source_files": request["source_files"],
                "position_provenance": (
                    native_position_provider.metadata()
                    if native_position_provider is not None else {
                        "position_source": POSITION_SOURCE_NOT_CONSUMED,
                    })})
            passed = (result["error"] is None
                      and result["skill_receipt_count"] == len(participant._plan["tasks"])
                      and result["business_outcomes_validated"] is True
                      and backend.business_evaluation is not None)
            if not passed and os.environ.get("RESTAURANT_ALGO_DEBUG_POSES"):
                poses = {}
                for agent, art in session.base_env.scene.articulations.items():
                    st = art.data.root_state_w[0].detach().cpu().tolist()
                    poses[agent] = [round(float(v), 3) for v in st[:7]]
                report["debug_poses"] = poses
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
        if os.environ.get("RESTAURANT_ALGO_DEBUG_POSES"):
            try:
                poses = {}
                for agent, art in session.base_env.scene.articulations.items():
                    st = art.data.root_state_w[0].detach().cpu().tolist()
                    poses[agent] = [round(float(v), 3) for v in st[:7]]
                report["debug_poses"] = poses
            except Exception:
                report["debug_poses"] = "unavailable"
        _write_json(report_path, report)
        raise
    if report["business_episode_execution_validated"] is not True:
        raise RuntimeError("Restaurant algorithm business episode did not validate")
    return report


_ALGO_ARCHIVE_EPISODE_FILES = (
    "episode_manifest.json", "public_task.json", "participant_api.json",
    "goal_session_report.json", "skill_receipts.json", "business_evidence.json",
    "team_usage.json", "participant_trace.json", "hidden_evaluator_trace.json",
    "runtime_snapshots.json", "result.json", "native_evidence_log.json",
    "readiness_service_snapshot.json", "goal_episode_source.json",
)


def _validate_archived_position_provenance(
    provenance: Any, algorithm: str,
) -> Mapping[str, Any]:
    """Validate allocator pose provenance without upgrading unused samples."""
    if algorithm == "teamweaver":
        expected = {"position_source": POSITION_SOURCE_NOT_CONSUMED}
        if provenance != expected:
            raise ValueError("TeamWeaver archive falsely claims allocator position capture")
        return provenance
    if algorithm != "emos" or not isinstance(provenance, Mapping):
        raise ValueError("archived allocator position provenance is invalid")
    samples = provenance.get("samples")
    if (provenance.get("position_source") != POSITION_SOURCE_NATIVE_ROOT_STATE
            or provenance.get("capture") != "settled_articulation_root_state_w"
            or not isinstance(samples, Mapping)
            or set(samples) != set(ALGORITHM_POOL)):
        raise ValueError("archived EMOS native position provenance is incomplete")
    for public_id, binding in ROBOT_BINDINGS.items():
        sample = samples.get(public_id)
        root = sample.get("root_state_w") if isinstance(sample, Mapping) else None
        step = sample.get("physics_step") if isinstance(sample, Mapping) else None
        if (not isinstance(sample, Mapping)
                or sample.get("native_agent_id") != binding["native_agent_id"]
                or isinstance(step, bool) or not isinstance(step, int) or step < 0
                or not isinstance(root, list) or len(root) != 13
                or any(isinstance(value, bool) for value in root)
                or not navigation_probe._finite_root_state(root)):
            raise ValueError(
                f"archived EMOS native position sample is invalid: {public_id}")
    return provenance


def _verify_archived_agent_observation(row: Mapping[str, Any], request: Mapping[str, Any]) -> str:
    """Agent-aware adaptation of the witness scene-observation contract."""
    region = row.get("region_id")
    scene = row.get("scene_observation")
    stamp = row.get("stamp")
    producer = row.get("producer_id")
    if (region not in {"kitchen_service", "dining_service"}
            or not isinstance(scene, Mapping) or not isinstance(stamp, Mapping)
            or scene.get("schema") != "eai.restaurant.native_region_observation"
            or scene.get("version") != 1
            or scene.get("region_id") != region
            or scene.get("producer_id") != producer
            or scene.get("stamp") != stamp
            or scene.get("scene_queries_enabled") is not True
            or scene.get("query_kind")
            != "episode_timestamped_physx_ground_support_raycast"
            or scene.get("expected_collision_path") != contact_probe.GROUND_COLLISION_PATH):
        raise ValueError(f"native observation evidence failed its contract: {region}")
    center = request["region_bindings"][region]["center_xy_m"]
    observed_xy = stamp.get("root_state_w", ())[:2]
    if (scene.get("region_center_xy_m") != center
            or scene.get("observed_robot_xy_m") != observed_xy
            or len(observed_xy) != 2):
        raise ValueError(f"native observation position binding failed: {region}")
    expected_error = math.dist(
        [_finite_number(value, f"observed position {region}") for value in observed_xy],
        [_finite_number(value, f"region center {region}") for value in center])
    archived_error = _finite_number(scene.get("region_error_m"), f"region error {region}")
    tolerance = _finite_number(scene.get("region_tolerance_m"), f"region tolerance {region}")
    if (not math.isclose(archived_error, expected_error, abs_tol=1e-9)
            or tolerance != REGION_OBSERVATION_TOLERANCE_M
            or archived_error > tolerance):
        raise ValueError(f"native observation lies outside its region: {region}")
    return region


def _verify_archived_relay_pose(row: Mapping[str, Any], task: Mapping[str, Any]) -> str:
    """Bind a relay's producer and native stamps to its unchanged anchor region."""
    producer = row.get("producer_id")
    binding = ROBOT_BINDINGS.get(producer)
    region = (task.get("arguments") or {}).get("region_id")
    if (binding is None
            or task.get("skill") != "relay"
            or task.get("assigned_agents") != [producer]
            or row.get("native_agent_id") != binding["native_agent_id"]
            or region != binding["spawn_region_id"]):
        raise ValueError("native relay producer does not match its current public region")
    expected_xyz = restaurant_config.BENCHMARK_ROBOT_SPAWN_XYZ_M[
        binding["native_agent_id"]]
    dimensions = 3 if binding["mobility"] == "aerial" else 2
    for name in ("publication_stamp", "receipt_stamp"):
        stamp = row.get(name)
        root = stamp.get("root_state_w") if isinstance(stamp, Mapping) else None
        if (not isinstance(root, list)
                or not navigation_probe._finite_root_state(root)
                or math.dist(root[:dimensions], expected_xyz[:dimensions])
                > navigation_probe.FINAL_TOLERANCE_M):
            raise ValueError(f"native relay {name} is outside its bound producer region")
    return str(region)


def _require_non_bool_int(value: Any, label: str) -> int:
    """Reject bool/str/float for archive simulation steps."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a non-bool int, got {type(value).__name__}")
    return value


def _require_finite_numeric(value: Any, label: str) -> float:
    """Reject bool/str/non-finite values for archive simulation times."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a non-bool finite numeric, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{label} must be finite")
    return out


def _verify_archived_relay_service_binding(row: Mapping[str, Any],
                                          service: Mapping[str, Any]) -> None:
    """Cross-bind the native relay evidence row to the readiness service archive.

    Pose/root-state checks alone are insufficient: the native publication and
    receipt stamps must match the readiness service records field-for-field,
    including simulation step and time with strict Python types (non-bool int
    step, non-bool finite numeric time — strings do not coerce). Divergent or
    type-tampered step/time values must fail closed.
    """
    publications = service.get("relay_publications")
    receipt = service.get("receipt")
    native_publication = row.get("relay_publication")
    native_receipt = row.get("business_receipt")
    publication_stamp = row.get("publication_stamp")
    receipt_stamp = row.get("receipt_stamp")
    if (not isinstance(publications, list) or len(publications) != 1
            or not isinstance(receipt, Mapping)
            or not isinstance(native_publication, Mapping)
            or not isinstance(native_receipt, Mapping)
            or not isinstance(publication_stamp, Mapping)
            or not isinstance(receipt_stamp, Mapping)):
        raise ValueError("native relay is missing readiness service binding records")
    service_publication = publications[0]
    if native_publication != service_publication:
        raise ValueError("native relay_publication diverges from readiness service archive")
    if native_receipt != receipt:
        raise ValueError("native business_receipt diverges from readiness service archive")

    pub_native_step = _require_non_bool_int(
        native_publication.get("simulation_step"), "relay_publication.simulation_step")
    pub_stamp_step = _require_non_bool_int(
        publication_stamp.get("simulation_step"), "publication_stamp.simulation_step")
    pub_native_time = _require_finite_numeric(
        native_publication.get("time_s"), "relay_publication.time_s")
    pub_stamp_time = _require_finite_numeric(
        publication_stamp.get("time_s"), "publication_stamp.time_s")
    if pub_native_step != pub_stamp_step or pub_native_time != pub_stamp_time:
        raise ValueError("relay publication step/time diverges from native publication_stamp")

    rec_native_step = _require_non_bool_int(
        native_receipt.get("simulation_step"), "business_receipt.simulation_step")
    rec_stamp_step = _require_non_bool_int(
        receipt_stamp.get("simulation_step"), "receipt_stamp.simulation_step")
    rec_native_time = _require_finite_numeric(
        native_receipt.get("time_s"), "business_receipt.time_s")
    rec_stamp_time = _require_finite_numeric(
        receipt_stamp.get("time_s"), "receipt_stamp.time_s")
    if rec_native_step != rec_stamp_step or rec_native_time != rec_stamp_time:
        raise ValueError("readiness receipt step/time diverges from native receipt_stamp")
    if (pub_stamp_step >= rec_stamp_step or pub_stamp_time >= rec_stamp_time):
        raise ValueError("native relay stamps are not strictly ordered publication before receipt")


def verify_algorithm_episode_archive(output_root: str | Path) -> dict[str, Any]:
    """Independently recompute an algorithm episode's evidence chain from disk.

    Mirrors the witness verifier's discipline: no worker in-memory state is
    trusted.  The algorithm plan is re-derived from the archived participant
    allocation and the frozen request substrate, and every native record,
    service row, receipt and business outcome is recomputed or cross-checked.
    """
    base = Path(output_root).expanduser().resolve()
    episode_root = base / "goal_episode"
    missing = [name for name in _ALGO_ARCHIVE_EPISODE_FILES
               if not (episode_root / name).is_file()]
    missing += [name for name in ("request.json",
                                  "restaurant_algorithm_episode_report.json",
                                  "process.json", "isaac.log")
                if not (base / name).is_file()]
    if missing:
        raise ValueError(f"algorithm episode archive is incomplete: {sorted(missing)}")

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    request = _load_request(base)
    report = load(base / "restaurant_algorithm_episode_report.json")
    process = load(base / "process.json")
    bundle = build_goal_task_package("restaurant")
    public_task = _algorithm_public_task(bundle)
    participant_api = build_goal_participant_api(public_task)
    episode_id = request["episode_id"]
    routes = request["physical_routes"]

    if process.get("returncode") != 0:
        raise ValueError("archived algorithm worker did not exit cleanly")
    if (report.get("schema") != "eai.restaurant.formal_goal_algorithm_episode_report"
            or report.get("version") != 1
            or report.get("run_id") != request["run_id"]
            or report.get("episode_id") != episode_id
            or report.get("algorithm") != request["algorithm"]
            or report.get("status") != "completed"
            or report.get("business_episode_execution_validated") is not True
            or report.get("execution_validated") is not False
            or report.get("scoreable") is not False):
        raise ValueError("archived algorithm report violates its own release gates")
    if (report.get("native_evidence_log_sha256")
            != file_sha256(episode_root / "native_evidence_log.json")
            or report.get("readiness_service_snapshot_sha256")
            != file_sha256(episode_root / "readiness_service_snapshot.json")
            or process.get("report_sha256")
            != file_sha256(base / "restaurant_algorithm_episode_report.json")
            or process.get("isaac_log_sha256") != file_sha256(base / "isaac.log")):
        raise ValueError("archived algorithm hash chain does not match its files")
    source_row = load(episode_root / "goal_episode_source.json")
    provenance = _validate_archived_position_provenance(
        source_row.get("position_provenance"), request["algorithm"])
    if (source_row.get("schema") != "eai.restaurant.formal_goal_episode_source"
            or source_row.get("version") != 1
            or source_row.get("episode_id") != episode_id
            or source_row.get("source_files") != request["source_files"]
            or source_row.get("request_sha256") != file_sha256(base / "request.json")):
        raise ValueError("archived algorithm source binding is inconsistent")

    manifest = load(episode_root / "episode_manifest.json")
    archived_task = load(episode_root / "public_task.json")
    archived_api = load(episode_root / "participant_api.json")
    if (manifest.get("episode_id") != episode_id
            or manifest.get("schema") != "eai.benchmark.formal_goal_episode"
            or manifest.get("task_id") != public_task["task_id"]
            or manifest.get("scene_key") != public_task["mission"]["scene_key"]
            or manifest.get("public_task_sha256") != canonical_sha256(archived_task)
            or canonical_sha256(archived_task) != canonical_sha256(public_task)
            or manifest.get("participant_api_sha256") != canonical_sha256(archived_api)
            or canonical_sha256(archived_api) != canonical_sha256(participant_api)
            or manifest.get("execution_validated") is not False
            or manifest.get("scoreable") is not False):
        raise ValueError("archived algorithm manifest is inconsistent")
    for name in ("result.json", "business_evidence.json", "participant_trace.json",
                 "hidden_evaluator_trace.json", "goal_session_report.json",
                 "native_evidence_log.json", "readiness_service_snapshot.json"):
        if load(episode_root / name).get("episode_id") != episode_id:
            raise ValueError(f"archived {name} carries a foreign episode identity")

    # The allocator's decision record lives in the hash-chained report
    # participant trace; the runner's participant_trace.json only carries the
    # public event stream.
    allocations = [row for row in (report.get("participant_trace") or [])
                   if row.get("event") == "allocation_final"]
    if len(allocations) != 1:
        raise ValueError("archived report trace lacks exactly one final allocation")
    position_events = [row for row in (report.get("participant_trace") or [])
                       if row.get("event") == "emos_robot_positions"]
    if request["algorithm"] == "emos":
        if len(position_events) != 1:
            raise ValueError("archived EMOS trace lacks native position provenance")
        trace_provenance = position_events[0].get("position_provenance")
        if (trace_provenance != provenance
                or position_events[0].get("position_source")
                != POSITION_SOURCE_NATIVE_ROOT_STATE):
            raise ValueError("archived EMOS position provenance diverges from source")
        positions = position_events[0].get("robot_positions")
        if (not isinstance(positions, Mapping)
                or set(positions) != set(ALGORITHM_POOL)
                or any(positions[public_id]
                       != provenance["samples"][public_id]["root_state_w"][:3]
                       for public_id in ALGORITHM_POOL)):
            raise ValueError("archived EMOS allocator positions diverge from native samples")
    elif position_events:
        raise ValueError("archived TeamWeaver trace falsely claims position consumption")
    assignment = allocations[0]["assignment"]
    session = load(episode_root / "goal_session_report.json")["report"]
    plan_rows = session.get("plans") or []
    submitted = [row for row in plan_rows
                 if (row.get("plan") or {}).get("plan_id")
                 == f"restaurant_{request['algorithm']}_missions_v1"]
    if len(submitted) != 1:
        raise ValueError("archived session did not submit the algorithm plan")
    submitted_plan = submitted[0]["plan"]
    # The allocation must expand to exactly the submitted plan.
    expected_plan = _expand_mission_plan(
        public_task, assignment,
        relay_agent=assignment["readiness_relay"], routes=routes,
        plan_id=submitted_plan["plan_id"])
    if canonical_sha256(submitted_plan) != canonical_sha256(expected_plan):
        raise ValueError("archived plan does not match its allocation expansion")
    plan = expected_plan
    task_by_id = {row["task_id"]: row for row in plan["tasks"]}
    tasks = session.get("tasks") or {}
    if (set(tasks) != set(task_by_id)
            or any(row.get("status") != "succeeded" for row in tasks.values())):
        raise ValueError("archived session tasks did not all succeed")
    outcomes_rows = session.get("business_outcomes") or []
    required = {row["id"] for row in public_task["required_outcomes"]}
    if ({row.get("id") for row in outcomes_rows} != required
            or any(row.get("status") != "validated"
                   or row.get("evaluator_ref") != EVALUATOR_REF
                   for row in outcomes_rows)):
        raise ValueError("archived session business outcomes are not fully validated")

    receipts = load(episode_root / "skill_receipts.json")
    result = load(episode_root / "result.json")
    if (len(receipts) != len(plan["tasks"])
            or any(row.get("status") != "succeeded"
                   or row.get("episode_id") != episode_id for row in receipts)
            or result.get("error") is not None
            or result.get("skill_receipt_count") != len(plan["tasks"])
            or result.get("business_outcomes_validated") is not True
            or result.get("execution_validated") is not False
            or result.get("scoreable") is not False):
        raise ValueError("archived skill receipts or result contradict the episode")

    evidence_log = load(episode_root / "native_evidence_log.json")
    records = evidence_log.get("records")
    if (evidence_log.get("schema") != "eai.restaurant.formal_goal_native_evidence"
            or evidence_log.get("version") != 1
            or not isinstance(records, list) or len(records) != len(plan["tasks"])):
        raise ValueError("native evidence record count does not match the plan")
    evidence_refs: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("episode_id") != episode_id or "evidence_ref" not in record:
            raise ValueError("native evidence record is not episode-bound")
        body = {key: value for key, value in record.items() if key != "evidence_ref"}
        if record["evidence_ref"] != "sha256:" + canonical_sha256(body):
            raise ValueError("native evidence record does not match its evidence_ref")
        evidence_refs[record["evidence_ref"]] = record
    for row in receipts + outcomes_rows:
        for ref in row.get("evidence_refs", []):
            if ref not in evidence_refs:
                raise ValueError("receipt or outcome references missing native evidence")

    arrivals = [row for row in records if row.get("kind") == "arrival"]
    observations = [row for row in records if row.get("kind") == "observation"]
    relays = [row for row in records if row.get("kind") == "relay_and_receipt"]
    if len(relays) != 1:
        raise ValueError("native evidence lacks exactly one relay")
    leg_by_task = {}
    public_routes = {row["id"]: row for row in public_task["public_world"]["routes"]}
    for row in plan["tasks"]:
        if row["skill"] != "ground_navigation":
            continue
        public_route = public_routes[row["arguments"]["route_ids"][0]]
        producer = row["assigned_agents"][0]
        native = ROBOT_BINDINGS[producer]["native_agent_id"]
        leg_id, leg = _leg_for(native, public_route["from_region_id"],
                               str(row["arguments"]["region_id"]), routes)
        leg_by_task[row["task_id"]] = (leg_id, leg)
    seen_legs = []
    for row in arrivals:
        task = task_by_id.get(row.get("task_id"))
        if task is None or row.get("task_id") not in leg_by_task:
            raise ValueError("native arrival does not correspond to a navigation task")
        leg_id, leg = leg_by_task[row["task_id"]]
        producer = row.get("producer_id")
        binding = ROBOT_BINDINGS.get(producer)
        if (row.get("route_id") != leg_id
                or binding is None
                or leg["agent"] != binding["native_agent_id"]
                or row.get("region_id") != leg["to_region_id"]
                or task["assigned_agents"] != [producer]
                or int(row["arrival"]["simulation_step"])
                <= int(row["started"]["simulation_step"])):
            raise ValueError(f"native arrival failed its agent binding: {leg_id}")
        _verify_archived_contact(row)
        seen_legs.append(leg_id)
    if sorted(seen_legs) != sorted(leg_id for leg_id, _ in leg_by_task.values()):
        raise ValueError("native arrivals do not cover the plan's navigation legs")
    observation_by_region: dict[str, dict[str, Any]] = {}
    arrival_by_region: dict[str, dict[str, Any]] = {}
    for row in arrivals:
        arrival_by_region[row["region_id"]] = row
    for row in observations:
        task = task_by_id.get(row.get("task_id"))
        if task is None or task["skill"] != "observe":
            raise ValueError("native observation does not correspond to an observe task")
        region = _verify_archived_agent_observation(row, request)
        if (task["assigned_agents"] != [row.get("producer_id")]
                or region in observation_by_region):
            raise ValueError(f"native observation producer mismatch: {region}")
        observation_by_region[region] = row
    if set(observation_by_region) != {"kitchen_service", "dining_service"}:
        raise ValueError("native evidence lacks both service observations")
    relay_row = relays[0]
    relay_task = task_by_id.get(relay_row.get("task_id"))
    if (relay_task is None or relay_task["skill"] != "relay"
            or relay_task["assigned_agents"] != [relay_row.get("producer_id")]):
        raise ValueError("native relay does not match its plan task")
    _verify_archived_relay_pose(relay_row, relay_task)
    for region, observation in observation_by_region.items():
        arrival = arrival_by_region.get(region)
        if (arrival is None
                or arrival["producer_id"] != observation["producer_id"]
                or int(arrival["arrival"]["simulation_step"])
                >= int(observation["stamp"]["simulation_step"])):
            raise ValueError(f"native observation for {region} is not caused by its arrival")
    if (int(relay_row["publication_stamp"]["simulation_step"])
            >= int(relay_row["receipt_stamp"]["simulation_step"])
            or max(int(row["stamp"]["simulation_step"])
                   for row in observation_by_region.values())
            >= int(relay_row["publication_stamp"]["simulation_step"])):
        raise ValueError("native relay is not ordered after observations and before receipt")

    snapshot = load(episode_root / "readiness_service_snapshot.json")
    if (snapshot.get("schema") != "eai.restaurant.formal_goal_readiness_snapshot"
            or snapshot.get("version") != 1):
        raise ValueError("invalid readiness snapshot schema")
    service = snapshot.get("service") or {}
    _verify_archived_relay_service_binding(relay_row, service)
    archived_observations = {row["observation_id"]: row
                             for row in (service.get("observations") or {}).values()}
    for observation in observation_by_region.values():
        service_row = observation.get("service_record") or {}
        expected_digest = "sha256:" + canonical_sha256({
            "episode_id": episode_id,
            "region_id": observation["region_id"],
            "producer_id": observation["producer_id"],
            "scene_observation": observation["scene_observation"],
        })
        if (service_row.get("observation_id") not in archived_observations
                or service_row.get("simulation_step")
                != observation["stamp"]["simulation_step"]
                or service_row.get("region_id") != observation["region_id"]
                or service_row.get("producer_id") != observation["producer_id"]
                or service_row.get("evidence_digest") != expected_digest
                or archived_observations[service_row["observation_id"]] != service_row):
            raise ValueError("archived service observation diverges from native stamp")
    review = evaluate_readiness_receipt(
        episode_id=episode_id, receipt=service.get("receipt"),
        observations=archived_observations,
        relay_publications=service.get("relay_publications"))
    if review.get("validated") is not True:
        raise ValueError("archived readiness receipt did not survive independent review")

    business = load(episode_root / "business_evidence.json")
    usage = load(episode_root / "team_usage.json")
    if (set(business.get("outcomes") or {}) != required
            or any(row.get("status") != "validated"
                   or row.get("evaluator_ref") != EVALUATOR_REF
                   or any(ref not in evidence_refs for ref in row.get("evidence_refs", []))
                   for row in business["outcomes"].values())
            or business.get("execution_validated") is not False
            or business.get("scoreable") is not False
            or usage.get("selected_team") != plan["assigned_team"]
            or usage.get("action_count") != 2 + len(plan["tasks"])
            or usage.get("scoreable") is not False):
        raise ValueError("archived business or team-usage evidence is inconsistent")

    class _ReplayBackend:
        """Evaluate the archived business facts with the shared evaluator."""

        def __init__(self):
            self.evidence = records
            self.business_evaluation = None

        class _Readiness:
            def __init__(self, service_row):
                self._service = service_row

            def snapshot(self):
                return self._service

        @property
        def readiness(self):
            return _ReplayBackend._Readiness(service)

    replayed = evaluate_restaurant_business(_ReplayBackend(), episode_id, {
        "session": {"episode_id": episode_id}})
    if {key: row["status"] for key, row in replayed.items()} != {
            key: row["status"] for key, row in business["outcomes"].items()}:
        raise ValueError("independent business replay disagrees with the archive")
    return {
        "schema": "eai.restaurant.formal_goal_algorithm_archive_verification", "version": 1,
        "episode_id": episode_id, "algorithm": request["algorithm"],
        "validated": True,
        "record_count": len(records), "receipt_count": len(receipts),
        "outcomes": sorted(required),
        "allocation": dict(assignment),
        "assigned_team": plan["assigned_team"],
        "readiness_review": review,
        "execution_validated": False, "scoreable": False,
    }


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
    verification = verify_algorithm_episode_archive(root)
    _write_json(root / "archive_verification.json", verification)
    return {**process, "archive_verification": verification}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="teamweaver")
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument("--verify", action="store_true",
                         help="independently re-verify a finished episode archive")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.verify:
        verification = verify_algorithm_episode_archive(args.output_root)
        print(json.dumps({"episode_id": verification["episode_id"],
                          "algorithm": verification["algorithm"],
                          "validated": verification["validated"],
                          "execution_validated": False, "scoreable": False},
                         sort_keys=True))
        return 0
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
