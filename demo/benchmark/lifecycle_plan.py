"""Author-side lifecycle instance checks for maintained benchmark scenes.

The result is a private scenario artifact.  It proves that an authored
preloaded reserve has a collision-free initial placement and an admitted ground
route to its join rendezvous under the selected occupancy map.  It does not
prove Isaac activation, controller convergence, manipulation, or a physical
episode.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping

from EAI.scenario_spec.geometry import validate_connectivity, validate_geometry, static_blocked_grid
from EAI.scenario_spec.oracle import InflatedGrid
from EAI.scenario_spec.schema import ROBOT_PROFILES
from demo.benchmark.runtime import planning_radius, tracking_reserve_for


def _finite_pose(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("reserve parking pose must be XYZ")
    pose = [float(item) for item in value]
    if not all(math.isfinite(item) for item in pose):
        raise ValueError("reserve parking pose must be finite")
    return pose


def _lifecycle_sets(scenario: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    robots = scenario.get("robots")
    if not isinstance(robots, list) or not robots:
        raise ValueError("lifecycle plan requires a nonempty robot list")
    ids = {robot.get("id") for robot in robots}
    if len(ids) != len(robots) or None in ids:
        raise ValueError("lifecycle plan requires unique robot ids")
    lifecycle = scenario.get("runtime_lifecycle") or {}
    initial = set(lifecycle.get("initial_agent_ids") or ids)
    reserves = set(lifecycle.get("preloaded_reserve_agent_ids") or ())
    if initial & reserves or initial | reserves != ids:
        raise ValueError("lifecycle inventory must partition configured robots")
    return initial, reserves


def _node_target(scenario: Mapping[str, Any], node_id: str) -> tuple[float, float]:
    nodes = {node.get("id"): node for node in scenario.get("phase_graph", {}).get("nodes", ())}
    node = nodes.get(node_id)
    if node is None:
        raise ValueError(f"join target node is unknown: {node_id}")
    target_id = node.get("target_ref")
    targets = {target.get("id"): target for target in scenario["composition"]["targets"]}
    target = targets.get(target_id)
    if target is None:
        raise ValueError(f"join target has no declared target: {target_id}")
    return float(target["position"][0]), float(target["position"][1])


def _replace_reserve_pose(scenario: Mapping[str, Any], reserve_id: str,
                          parking_pose: list[float]) -> dict[str, Any]:
    candidate = deepcopy(dict(scenario))
    robots = candidate.get("robots") or []
    reserve = next((robot for robot in robots if robot.get("id") == reserve_id), None)
    if reserve is None:
        raise ValueError(f"reserve robot is unknown: {reserve_id}")
    position = reserve.setdefault("spawn_pose", {}).get("position")
    if not isinstance(position, list) or len(position) != 3:
        raise ValueError(f"reserve robot has no authored spawn pose: {reserve_id}")
    reserve["spawn_pose"]["position"] = list(parking_pose)
    return candidate


def validate_lifecycle_plan(
    scenario: Mapping[str, Any],
    occupancy: Any,
    *,
    reserve_agent_id: str,
    parking_pose: list[float] | tuple[float, float, float],
    join_node_id: str,
    join_time_s: float = 30.0,
    exit_agent_id: str | None = None,
    exit_time_s: float | None = None,
    replacement_agent_id: str | None = None,
    capability_change_agent_id: str | None = None,
    capability_change_time_s: float | None = None,
) -> dict[str, Any]:
    """Validate one private reserve/join/exit design against a map.

    Lifecycle events are deliberately represented as a plan, rather than
    inserted into the public task.  The returned route is an author-side
    witness and must not be sent to the participant.
    """
    if not isinstance(join_time_s, (int, float)) or isinstance(join_time_s, bool):
        raise ValueError("join_time_s must be numeric")
    join_time_s = float(join_time_s)
    if not math.isfinite(join_time_s) or join_time_s <= 0:
        raise ValueError("join_time_s must be finite and positive")
    initial, reserves = _lifecycle_sets(scenario)
    if reserve_agent_id not in reserves:
        raise ValueError("join reserve must be declared in preloaded_reserve_agent_ids")
    if exit_agent_id == reserve_agent_id:
        raise ValueError("reserve cannot exit before its join")
    if replacement_agent_id == reserve_agent_id:
        # The reserve is active after the join and may be the replacement.
        pass

    # Validate the event schedule before loading or scanning the map. This
    # keeps malformed author input deterministic and cheap to diagnose.
    if exit_agent_id is not None:
        if exit_agent_id not in initial or replacement_agent_id not in initial | {reserve_agent_id}:
            raise ValueError("exit and replacement must name an authored active member")
        if not isinstance(exit_time_s, (int, float)) or isinstance(exit_time_s, bool):
            raise ValueError("exit_time_s is required for member_exit")
        exit_time_s = float(exit_time_s)
        if not math.isfinite(exit_time_s) or exit_time_s <= join_time_s:
            raise ValueError("member_exit must occur after member_join")
        if replacement_agent_id == exit_agent_id:
            raise ValueError("member_exit replacement must differ from exited member")
    if capability_change_agent_id is not None:
        if capability_change_agent_id not in initial | {reserve_agent_id}:
            raise ValueError("capability change names an unknown authored member")
        if not isinstance(capability_change_time_s, (int, float)) or isinstance(capability_change_time_s, bool):
            raise ValueError("capability_change_time_s is required for capability_change")
        capability_change_time_s = float(capability_change_time_s)
        if not math.isfinite(capability_change_time_s) or capability_change_time_s <= join_time_s:
            raise ValueError("capability_change must occur after member_join")

    candidate = _replace_reserve_pose(scenario, reserve_agent_id, _finite_pose(parking_pose))
    reserve = next(robot for robot in candidate["robots"] if robot["id"] == reserve_agent_id)
    profile = ROBOT_PROFILES.get(reserve.get("type"))
    if profile is None:
        raise ValueError(f"unknown reserve robot type: {reserve.get('type')}")
    expected_z = float(profile["spawn_z_m"])
    if not math.isclose(float(parking_pose[2]), expected_z, abs_tol=1e-6):
        raise ValueError("reserve parking z must match the robot profile spawn height")

    placement = validate_geometry(candidate, occupancy)
    connectivity = validate_connectivity(candidate, occupancy)
    radius = planning_radius(reserve)
    blocked = static_blocked_grid(
        occupancy, candidate,
        ground_tracking_reserve_m=tracking_reserve_for(candidate["scene_key"]),
    )
    grid = InflatedGrid(occupancy, radius, blocked)
    start = tuple(float(value) for value in parking_pose[:2])
    goal = _node_target(candidate, join_node_id)
    route = grid.path(start, goal)
    if route is None:
        raise ValueError(f"reserve has no admitted route to join target: {join_node_id}")

    events = [{"kind": "member_join", "agent": reserve_agent_id, "time_s": join_time_s}]
    if exit_agent_id is not None:
        events.append({"kind": "member_exit", "agent": exit_agent_id,
                       "replacement_agent": replacement_agent_id, "time_s": exit_time_s})
    if capability_change_agent_id is not None:
        events.append({"kind": "capability_change", "agent": capability_change_agent_id,
                       "time_s": capability_change_time_s,
                       "allowed_field": "navigation_interface"})
    events.sort(key=lambda event: event["time_s"])
    if len({event["time_s"] for event in events}) != len(events):
        raise ValueError("lifecycle events must have distinct times")
    return {
        "schema": "eai.benchmark.private_lifecycle_plan",
        "version": 1,
        "scene_key": candidate["scene_key"],
        "reserve_agent_id": reserve_agent_id,
        "parking_pose": list(_finite_pose(parking_pose)),
        "join_node_id": join_node_id,
        "events": events,
        "map_sha256": occupancy.sha256,
        "placement": placement,
        "connectivity": connectivity,
        "route": {
            "validated": True,
            "waypoint_count": len(route),
            "start_xy": list(start),
            "goal_xy": list(goal),
        },
        "physical_execution_validated": False,
        "visibility": "author_evaluator_only",
    }


_MAINTAINED_SCENARIOS = {
    "factory": Path("demo/factory_benchmark/scenarios/coverage_01.json"),
    "warehouse_nvd": Path("demo/warehouse_benchmark/scenarios/business_logistics_01.json"),
    "modern_house_nvd": Path("demo/modern_house_benchmark/scenarios/home_recovery_01.json"),
}

_MAINTAINED_RESERVE_PLANS = {
    "factory": {
        "reserve_type": "simple_car", "parking_pose": [-3.45, -8.6, 0.34],
        "join_node_id": "dock_arrive", "exit_agent_id": "simple_car_3",
        "exit_time_s": 80.0, "capability_change_agent_id": "simple_car_2",
        "capability_change_time_s": 120.0,
    },
    "warehouse_nvd": {
        "reserve_type": "carter", "parking_pose": [-8.425, -9.975, 0.2],
        "join_node_id": "rack_overview_scan", "exit_agent_id": "carter_1",
        "exit_time_s": 80.0, "capability_change_agent_id": "m20_1",
        "capability_change_time_s": 120.0,
    },
    "modern_house_nvd": {
        "reserve_type": "carter", "parking_pose": [19.075, 5.025, 0.2],
        "join_node_id": "room_entry_scan", "exit_agent_id": "carter_1",
        "exit_time_s": 80.0, "capability_change_agent_id": "m20_1",
        "capability_change_time_s": 120.0,
    },
}


def build_maintained_lifecycle_plan(scene_key: str, *, occupancy=None) -> dict[str, Any]:
    """Compile the current private lifecycle instance for one maintained scene.

    This function is intentionally strict and reads the checked-in scenario
    source. It is useful in release QA and never changes the source scenario or
    the public task packet.
    """
    try:
        scenario_path = _MAINTAINED_SCENARIOS[scene_key]
        spec = _MAINTAINED_RESERVE_PLANS[scene_key]
    except KeyError as exc:
        raise ValueError(f"unsupported maintained lifecycle scene: {scene_key}") from exc
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    reserve_candidates = [
        robot for robot in scenario["robots"] if robot.get("type") == spec["reserve_type"]
    ]
    if not reserve_candidates:
        raise ValueError(f"maintained scene has no {spec['reserve_type']} reserve template")
    reserve = deepcopy(reserve_candidates[0])
    reserve["id"] = f"{spec['reserve_type']}_reserve_1"
    scenario["robots"].append(reserve)
    scenario["runtime_lifecycle"] = {
        "initial_agent_ids": [robot["id"] for robot in scenario["robots"][:-1]],
        "preloaded_reserve_agent_ids": [reserve["id"]],
    }
    if occupancy is None:
        from EAI.scenario_spec.geometry import provider_map
        occupancy = provider_map(scene_key=scene_key)
    return validate_lifecycle_plan(
        scenario, occupancy, reserve_agent_id=reserve["id"],
        parking_pose=spec["parking_pose"], join_node_id=spec["join_node_id"],
        exit_agent_id=spec["exit_agent_id"], exit_time_s=spec["exit_time_s"],
        replacement_agent_id=reserve["id"],
        capability_change_agent_id=spec["capability_change_agent_id"],
        capability_change_time_s=spec["capability_change_time_s"],
    )


__all__ = ["build_maintained_lifecycle_plan", "validate_lifecycle_plan"]
