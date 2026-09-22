"""Formal Restaurant GoalSession episode backed by one persistent Isaac scene.

The fixed participant chooses the admitted Carter and runs a source-bound
staging -> kitchen -> staging -> dining -> staging inspection loop.  Every
arrival, observation, relay publication and readiness receipt is captured
after a real ``env.step`` in the same native session and bound to one episode.
The run validates one witness business episode; it does not admit the rest of
the authoring pool or promote the Restaurant package to scoreable status.
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

from .control_step_evidence import ControlStepEvidence
from .goal_driven_contract import GOAL_PLAN_VERSION, validate_goal_plan
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner
from .provenance import canonical_sha256, file_sha256
from .runtime import navigation_state, route_waypoints, steering_command
from demo.restaurant_benchmark import config as restaurant_config
from demo.restaurant_benchmark import contact_probe, navigation_probe, runtime_probe
from demo.restaurant_benchmark.service_backend import (
    RestaurantReadinessBackend,
    evaluate_readiness_receipt,
)


ROOT = Path(__file__).resolve().parents[2]
ROBOT_ID = "restaurant_carter_1"
BACKEND_ID = "isaac.restaurant.goal_episode.v1"
EVALUATOR_REF = "demo.benchmark.goal_restaurant_episode.evaluate_restaurant_business"
PLAN_ID = "restaurant_native_witness_a_v1"
NAVIGATION_WINDOW_S = 45.0
OBSERVATION_WINDOW_S = 4.0
RELAY_WINDOW_S = 2.0
REGION_OBSERVATION_TOLERANCE_M = navigation_probe.FINAL_TOLERANCE_M
REGION_SUPPORT_SAMPLE_OFFSETS_M = (
    (-0.45, -0.45), (-0.45, 0.45), (0.45, -0.45), (0.45, 0.45),
)
REGION_SUPPORT_RAY_HEIGHT_M = 0.20
REGION_SUPPORT_RAY_DISTANCE_M = 0.30
MINIMUM_REGION_SUPPORT_SAMPLES = 2

_A = tuple(navigation_probe.ROUTES["A-E"]["start_xy_m"])
_E = tuple(navigation_probe.ROUTES["A-E"]["goal_xy_m"])
_B = tuple(navigation_probe.ROUTES["A-B"]["goal_xy_m"])
PHYSICAL_LEGS = {
    "restaurant_staging_to_kitchen": {
        "from_region_id": "restaurant_staging", "to_region_id": "kitchen_service",
        "start_xy_m": _A, "goal_xy_m": _E,
    },
    "restaurant_kitchen_to_staging": {
        "from_region_id": "kitchen_service", "to_region_id": "restaurant_staging",
        "start_xy_m": _E, "goal_xy_m": _A,
    },
    "restaurant_staging_to_dining": {
        "from_region_id": "restaurant_staging", "to_region_id": "dining_service",
        "start_xy_m": _A, "goal_xy_m": _B,
    },
    "restaurant_dining_to_staging": {
        "from_region_id": "dining_service", "to_region_id": "restaurant_staging",
        "start_xy_m": _B, "goal_xy_m": _A,
    },
}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _action(identifier: str, kind: str, payload: Mapping[str, Any], now: float) -> dict[str, Any]:
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": identifier, "submitted_at_s": float(now),
            "type": kind, "payload": deepcopy(dict(payload))}


def _task(task_id: str, skill: str, arguments: Mapping[str, Any], prerequisites: list[str],
          start_s: float, duration_s: float) -> dict[str, Any]:
    return {
        "task_id": task_id, "skill": skill, "arguments": deepcopy(dict(arguments)),
        "prerequisites": list(prerequisites), "required_capabilities": [skill],
        "assigned_agents": [ROBOT_ID], "evidence_refs": [],
        "execution": {"start_s": float(start_s), "duration_s": float(duration_s)},
    }


def build_restaurant_episode_plan(public_task: Mapping[str, Any]) -> dict[str, Any]:
    """Build the measured-route single-Carter witness within the 240 s budget."""
    rows: list[dict[str, Any]] = []
    clock = 0.0

    def append(task_id: str, skill: str, arguments: Mapping[str, Any],
               prerequisites: list[str], duration: float) -> None:
        nonlocal clock
        rows.append(_task(task_id, skill, arguments, prerequisites, clock, duration))
        clock += duration

    append("restaurant_native_arrive_kitchen", "ground_navigation",
           {"region_id": "kitchen_service",
            "route_ids": ["restaurant_staging_to_kitchen"]}, [], NAVIGATION_WINDOW_S)
    append("restaurant_native_observe_kitchen", "observe",
           {"region_id": "kitchen_service", "subject_ref": "kitchen_service",
            "observation_mode": "opening_readiness"},
           ["restaurant_native_arrive_kitchen"], OBSERVATION_WINDOW_S)
    append("restaurant_native_return_after_kitchen", "ground_navigation",
           {"region_id": "restaurant_staging",
            "route_ids": ["restaurant_kitchen_to_staging"]},
           ["restaurant_native_observe_kitchen"], NAVIGATION_WINDOW_S)
    append("restaurant_native_arrive_dining", "ground_navigation",
           {"region_id": "dining_service",
            "route_ids": ["restaurant_staging_to_dining"]},
           ["restaurant_native_return_after_kitchen"], NAVIGATION_WINDOW_S)
    append("restaurant_native_observe_dining", "observe",
           {"region_id": "dining_service", "subject_ref": "dining_service",
            "observation_mode": "opening_readiness"},
           ["restaurant_native_arrive_dining"], OBSERVATION_WINDOW_S)
    append("restaurant_native_return_after_dining", "ground_navigation",
           {"region_id": "restaurant_staging",
            "route_ids": ["restaurant_dining_to_staging"]},
           ["restaurant_native_observe_dining"], NAVIGATION_WINDOW_S)
    append("restaurant_native_relay_readiness", "relay",
           {"message_kind": "restaurant_readiness_receipt",
            "region_id": "restaurant_staging"},
           ["restaurant_native_return_after_dining",
            "restaurant_native_observe_kitchen",
            "restaurant_native_observe_dining"], RELAY_WINDOW_S)
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": PLAN_ID, "assigned_team": [ROBOT_ID], "tasks": rows}
    validate_goal_plan(plan, task_spec=public_task)
    return plan


class RestaurantWitnessParticipant:
    """Fixed participant that sees only public events and dispatches serially."""

    def __init__(self, plan: Mapping[str, Any]):
        self._plan = deepcopy(dict(plan))
        self._opened = False
        self._next = 0
        self._terminal: dict[str, str] = {}
        self.trace: list[dict[str, Any]] = []

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "restaurant-native-goal-witness", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.trace.extend(deepcopy(events))
        now = max((float(row["time_s"]) for row in events), default=0.0)
        if not self._opened:
            if not any(row["type"] == "episode_started" for row in events):
                return []
            self._opened = True
            return [
                _action("restaurant_witness_select", "select_team",
                        {"agent_ids": [ROBOT_ID]}, now),
                _action("restaurant_witness_plan", "submit_plan",
                        {"plan": deepcopy(self._plan)}, now),
                _action("restaurant_witness_dispatch_0", "dispatch_skill",
                        {"task_id": self._plan["tasks"][0]["task_id"],
                         "evidence_refs": []}, now),
            ]
        for row in events:
            if row["type"] != "action_result":
                continue
            payload = row["payload"]
            if payload.get("status") in {"succeeded", "failed", "cancelled"}:
                self._terminal[str(payload["action_id"])] = str(payload["status"])
        if self._next == 0:
            self._next = 1
        previous = f"restaurant_witness_dispatch_{self._next - 1}"
        if previous not in self._terminal:
            return []
        if self._terminal[previous] != "succeeded" or self._next >= len(self._plan["tasks"]):
            return []
        index = self._next
        self._next += 1
        return [_action(f"restaurant_witness_dispatch_{index}", "dispatch_skill",
                        {"task_id": self._plan["tasks"][index]["task_id"],
                         "evidence_refs": []}, now)]

    def close(self) -> None:
        return None


def _route_plan(products_root: Path, route_id: str) -> dict[str, Any]:
    from EAI.scenario_spec.geometry import FactoryMap
    from EAI.scenario_spec.oracle import InflatedGrid

    definition = PHYSICAL_LEGS[route_id]
    occupancy = FactoryMap(products_root / "restaurant_benchmark_map.yaml")
    grid = InflatedGrid(occupancy, navigation_probe.PLANNING_RADIUS_M)
    cells = grid.path(definition["start_xy_m"], definition["goal_xy_m"])
    if cells is None:
        raise RuntimeError(f"Restaurant formal route is not passable: {route_id}")
    waypoints = route_waypoints(grid, cells, definition["goal_xy_m"], preserve_goal=True)
    points = [definition["start_xy_m"], *waypoints]
    return {
        "route_id": route_id,
        "from_region_id": definition["from_region_id"],
        "to_region_id": definition["to_region_id"],
        "start_xy_m": list(definition["start_xy_m"]),
        "goal_xy_m": list(definition["goal_xy_m"]),
        "map_sha256": occupancy.sha256,
        "route_cells": [list(cell) for cell in cells],
        "waypoints_xy_m": [list(point) for point in waypoints],
        "planned_length_m": sum(math.dist(a, b) for a, b in zip(points[:-1], points[1:])),
    }


def _physical_routes(products_root: Path) -> dict[str, dict[str, Any]]:
    return {route_id: _route_plan(products_root, route_id)
            for route_id in sorted(PHYSICAL_LEGS)}


class NativeCarterEpisodeNavigator:
    """Drive all formal legs in one native session and retain post-step facts."""

    def __init__(self, session: Any, agent: str, products_root: Path,
                 routes: Mapping[str, Mapping[str, Any]],
                 region_bindings: Mapping[str, Mapping[str, Any]]):
        import carb
        import omni.physx
        import torch
        from isaacsim.core.simulation_manager import SimulationManager
        from EAI.scenario_spec.geometry import FactoryMap
        from EAI.scenario_spec.oracle import InflatedGrid

        self.session = session
        self.agent = agent
        self.articulation = session.base_env.scene.articulations[agent]
        self.control_dt_s = float(session.base_env.step_dt)
        self._command = torch.zeros((session.num_envs, 3), device=session.device)
        self._control_steps = 0
        self._routes = deepcopy(dict(routes))
        self._region_bindings = deepcopy(dict(region_bindings))
        self._grid = InflatedGrid(
            FactoryMap(products_root / "restaurant_benchmark_map.yaml"),
            navigation_probe.PLANNING_RADIUS_M)
        self._native_step_origin = int(session.base_env.sim.current_time_step_index)
        body_names = list(self.articulation.body_names)
        missing = [name for name in contact_probe.WHEEL_NAMES if name not in body_names]
        if missing:
            raise RuntimeError(f"Carter wheel bodies are missing: {missing}")
        link_paths = self.articulation.root_physx_view.link_paths
        if len(link_paths) != 1:
            raise RuntimeError("formal Restaurant episode requires one Carter articulation")
        body_paths = {
            name: str(link_paths[0][body_names.index(name)])
            for name in contact_probe.WHEEL_NAMES
        }
        self._settings = carb.settings.get_settings()
        self._previous_disable_contact_processing = self._settings.get_as_bool(
            "/physics/disableContactProcessing")
        self._settings.set_bool("/physics/disableContactProcessing", False)
        physics_view = SimulationManager.get_physics_sim_view()
        self._contact_views: dict[str, Any] = {}
        self.contact_view_identities: dict[str, dict[str, Any]] = {}
        for name, body_path in body_paths.items():
            view = physics_view.create_rigid_contact_view(
                body_path,
                filter_patterns=[contact_probe.GROUND_COLLISION_PATH],
                max_contact_data_count=contact_probe.CONTACT_BUFFER_CAPACITY,
            )
            filters = list(view.filter_paths)
            if len(filters) == 1 and isinstance(filters[0], (list, tuple)):
                filters = list(filters[0])
            identity = {
                "sensor_paths": list(view.sensor_paths),
                "filter_paths": filters,
                "max_contact_data_count": view.max_contact_data_count,
            }
            expected = {
                "sensor_paths": [body_path],
                "filter_paths": [contact_probe.GROUND_COLLISION_PATH],
                "max_contact_data_count": contact_probe.CONTACT_BUFFER_CAPACITY,
            }
            if identity != expected or view.sensor_count != 1 or view.filter_count != 1:
                raise RuntimeError(f"contact view identity mismatch for {name}")
            self._contact_views[name] = view
            self.contact_view_identities[name] = identity
        self._physics_dt_s = float(session.base_env.sim.get_physics_dt())
        mass_kg = float(
            self.articulation.root_physx_view.get_masses()[0].detach().cpu().sum().item())
        self._weight_N = mass_kg * 9.81
        self._scene_query = omni.physx.get_physx_scene_query_interface()

    def close(self) -> None:
        self._settings.set_bool(
            "/physics/disableContactProcessing",
            self._previous_disable_contact_processing,
        )

    def _sample_contacts(self) -> tuple[dict[str, Any], float, bool]:
        wheels: dict[str, Any] = {}
        total_ground_z = 0.0
        buffer_reached = False
        for name, view in self._contact_views.items():
            wheel = contact_probe._sample_contact_view(view, physics_dt=self._physics_dt_s)
            wheels[name] = wheel
            total_ground_z += wheel["ground_force_N"][2]
            buffer_reached = buffer_reached or wheel["buffer_capacity_reached"]
        return wheels, total_ground_z / self._weight_N, buffer_reached

    @property
    def episode_time_s(self) -> float:
        return self._control_steps * self.control_dt_s

    def pose(self) -> list[float]:
        return self.articulation.data.root_state_w[0].detach().cpu().tolist()

    def _stamp(self, root_state: list[float]) -> dict[str, Any]:
        return {
            "simulation_step": int(self.session.base_env.sim.current_time_step_index),
            "episode_control_step": self._control_steps,
            "time_s": self.episode_time_s,
            "root_state_w": [float(value) for value in root_state],
        }

    def step_once(self, command: tuple[float, float] | None = None) -> dict[str, Any]:
        self._command.zero_()
        if command is not None:
            self._command[0, 0] = float(command[0])
            self._command[0, 2] = float(command[1])
        self.session.env.step({self.agent: self._command})
        self._control_steps += 1
        root = self.pose()
        if not navigation_probe._finite_root_state(root):
            raise RuntimeError("non-finite Carter root state in formal Restaurant episode")
        return self._stamp(root)

    def wait_until(self, target_time_s: float) -> dict[str, Any]:
        if target_time_s + 1e-9 < self.episode_time_s:
            raise RuntimeError("native Restaurant execution exceeded its GoalSession window")
        start = self._stamp(self.pose())
        while self.episode_time_s + self.control_dt_s <= target_time_s + 1e-9:
            self.step_once()
        if not math.isclose(self.episode_time_s, target_time_s, abs_tol=1e-8):
            raise RuntimeError("GoalSession window is not aligned to the native control step")
        return {"kind": "native_window_hold", "start": start,
                "end": self._stamp(self.pose())}

    def observe_region(self, region_id: str) -> dict[str, Any]:
        binding = self._region_bindings.get(region_id)
        if not isinstance(binding, Mapping):
            raise ValueError(f"unknown Restaurant observation region: {region_id}")
        stamp = self.step_once()
        center = [float(value) for value in binding["center_xy_m"]]
        observed_xy = [float(value) for value in stamp["root_state_w"][:2]]
        region_error = math.dist(observed_xy, center)
        support_samples = []
        for dx, dy in REGION_SUPPORT_SAMPLE_OFFSETS_M:
            origin = (center[0] + dx, center[1] + dy, REGION_SUPPORT_RAY_HEIGHT_M)
            hit = runtime_probe._hit_record(self._scene_query.raycast_closest(
                origin, (0.0, 0.0, -1.0), REGION_SUPPORT_RAY_DISTANCE_M))
            passed = (
                hit.get("hit") is True
                and hit.get("collision") == contact_probe.GROUND_COLLISION_PATH
                and abs(float(hit.get("position_m", [0.0, 0.0, 99.0])[2])) <= 0.02
                and float(hit.get("normal", [0.0, 0.0, 0.0])[2]) >= 0.99
            )
            support_samples.append({
                "origin_m": [float(value) for value in origin],
                "direction": [0.0, 0.0, -1.0],
                "distance_m": REGION_SUPPORT_RAY_DISTANCE_M,
                "hit": hit,
                "passed": passed,
            })
        passed_count = sum(row["passed"] is True for row in support_samples)
        validated = (
            region_error <= REGION_OBSERVATION_TOLERANCE_M
            and passed_count >= MINIMUM_REGION_SUPPORT_SAMPLES
        )
        observation = {
            "schema": "eai.restaurant.native_region_observation",
            "version": 1,
            "region_id": region_id,
            "producer_id": ROBOT_ID,
            "query_kind": "episode_timestamped_physx_ground_support_raycast",
            "scene_queries_enabled": self.session.env_cfg.sim.enable_scene_query_support is True,
            "expected_collision_path": contact_probe.GROUND_COLLISION_PATH,
            "region_center_xy_m": center,
            "observed_robot_xy_m": observed_xy,
            "region_error_m": region_error,
            "region_tolerance_m": REGION_OBSERVATION_TOLERANCE_M,
            "support_samples": support_samples,
            "minimum_support_samples": MINIMUM_REGION_SUPPORT_SAMPLES,
            "passed_support_samples": passed_count,
            "stamp": stamp,
            "scene_observation_validated": validated,
        }
        if not validated:
            raise RuntimeError(f"Restaurant {region_id} native scene observation did not validate")
        return observation

    def drive(self, route_id: str, *, deadline_s: float) -> dict[str, Any]:
        route = deepcopy(self._routes[route_id])
        start_pose = self.pose()
        start_xy = (float(start_pose[0]), float(start_pose[1]))
        if math.dist(start_xy, route["start_xy_m"]) > navigation_probe.FINAL_TOLERANCE_M + 0.05:
            raise RuntimeError(f"Carter is not at the declared start of {route_id}")
        initial_polyline = [route["start_xy_m"], *route["waypoints_xy_m"]]
        active_waypoints = [tuple(point) for point in route["waypoints_xy_m"]]
        waypoint_index = 0
        replans = 0
        events: list[dict[str, Any]] = []
        recorder = ControlStepEvidence(self.control_dt_s)
        local_step = 0
        last_progress = 0
        best_distance = math.inf
        best_heading = math.inf
        max_deviation = 0.0
        max_z_excursion = 0.0
        max_tilt = 0.0
        contact_steps = {name: 0 for name in contact_probe.WHEEL_NAMES}
        support_ratios: list[float] = []
        contact_snapshots: list[dict[str, Any]] = []
        contact_buffer_reached = False
        arrival: dict[str, Any] | None = None
        started = self._stamp(start_pose)

        def replan(current_xy: tuple[float, float]) -> bool:
            nonlocal active_waypoints, waypoint_index, replans
            cells = self._grid.path(current_xy, route["goal_xy_m"])
            if cells is None:
                return False
            active_waypoints = [tuple(point) for point in route_waypoints(
                self._grid, cells, route["goal_xy_m"], preserve_goal=True)]
            waypoint_index = 0
            replans += 1
            events.append({"event": "route_replanned", "episode_control_step": self._control_steps,
                           "replan_index": replans, "start_xy_m": list(current_xy),
                           "route_cells": [list(cell) for cell in cells],
                           "waypoints_xy_m": [list(point) for point in active_waypoints]})
            return True

        while self.episode_time_s + self.control_dt_s <= deadline_s + 1e-9:
            x, y, _z, yaw = navigation_state(self.articulation)
            while waypoint_index < len(active_waypoints):
                tolerance = (navigation_probe.FINAL_TOLERANCE_M
                             if waypoint_index == len(active_waypoints) - 1
                             else navigation_probe.INTERMEDIATE_TOLERANCE_M)
                if math.dist((x, y), active_waypoints[waypoint_index]) > tolerance:
                    break
                events.append({"event": "waypoint_reached",
                               "episode_control_step": self._control_steps,
                               "waypoint_index": waypoint_index,
                               "position_xy_m": [x, y]})
                waypoint_index += 1
                best_distance = math.inf
                best_heading = math.inf
                last_progress = local_step
            if waypoint_index >= len(active_waypoints):
                if math.dist((x, y), route["goal_xy_m"]) <= navigation_probe.FINAL_TOLERANCE_M:
                    arrival = self._stamp(self.pose())
                    break
                raise RuntimeError(f"{route_id} ended outside its goal tolerance")
            target = active_waypoints[waypoint_index]
            distance = math.dist((x, y), target)
            desired = math.atan2(target[1] - y, target[0] - x)
            heading_error = (desired - yaw + math.pi) % (2 * math.pi) - math.pi
            linear, angular = steering_command(
                distance=distance, heading_error=heading_error,
                speed=navigation_probe.SPEED_M_S, k_turn=navigation_probe.TURN_GAIN)
            submitted = [float(linear), 0.0, float(angular)]
            stamp = self.step_once((linear, angular))
            local_step += 1
            recorder.record(local_step, {self.agent: submitted},
                            {self.agent: stamp["root_state_w"]})
            wheels, support_ratio, buffer_reached = self._sample_contacts()
            support_ratios.append(support_ratio)
            contact_buffer_reached = contact_buffer_reached or buffer_reached
            for name, wheel in wheels.items():
                if wheel["ground_force_N"][2] > contact_probe.CONTACT_FORCE_THRESHOLD_N:
                    contact_steps[name] += 1
            if (local_step == 1
                    or local_step % navigation_probe.CONTACT_SNAPSHOT_STRIDE == 0
                    or any(wheel["ground_force_N"][2]
                           <= contact_probe.CONTACT_FORCE_THRESHOLD_N
                           for wheel in wheels.values())):
                contact_snapshots.append({
                    "episode_control_step": self._control_steps,
                    "route_control_step": local_step,
                    "weight_support_ratio": support_ratio,
                    "wheels": wheels,
                })
            post_xy = tuple(float(value) for value in stamp["root_state_w"][:2])
            post_yaw = navigation_state(self.articulation)[3]
            post_distance = math.dist(post_xy, target)
            post_heading = abs((math.atan2(target[1] - post_xy[1], target[0] - post_xy[0])
                                - post_yaw + math.pi) % (2 * math.pi) - math.pi)
            max_deviation = max(max_deviation,
                                navigation_probe._route_deviation(post_xy, initial_polyline))
            max_z_excursion = max(max_z_excursion,
                                  abs(float(stamp["root_state_w"][2]) - float(start_pose[2])))
            max_tilt = max(max_tilt, navigation_probe._tilt(stamp["root_state_w"]))
            if post_distance + 1e-5 < best_distance or post_heading + 1e-4 < best_heading:
                best_distance = min(best_distance, post_distance)
                best_heading = min(best_heading, post_heading)
                last_progress = local_step
            if local_step - last_progress > navigation_probe.STALL_WINDOW_STEPS:
                if replans >= navigation_probe.MAX_REPLANS or not replan(post_xy):
                    raise RuntimeError(f"{route_id} stalled before arrival")
                last_progress = local_step
            if math.dist(post_xy, route["goal_xy_m"]) <= navigation_probe.FINAL_TOLERANCE_M:
                arrival = stamp
                break
        if arrival is None:
            raise RuntimeError(f"{route_id} did not arrive within its GoalSession window")
        control = recorder.report()
        ratios = {
            name: contact_steps[name] / local_step if local_step else 0.0
            for name in contact_probe.WHEEL_NAMES
        }
        sorted_support = sorted(support_ratios)
        median_support = sorted_support[len(sorted_support) // 2] if sorted_support else 0.0
        contact_ok = (
            local_step > 0
            and not contact_buffer_reached
            and all(value >= navigation_probe.MINIMUM_WHEEL_CONTACT_RATIO
                    for value in ratios.values())
            and navigation_probe.MINIMUM_MEDIAN_SUPPORT_RATIO
            <= median_support
            <= navigation_probe.MAXIMUM_MEDIAN_SUPPORT_RATIO
        )
        if (control["validated"] is not True
                or not contact_ok
                or max_deviation > navigation_probe.MAXIMUM_ROUTE_DEVIATION_M
                or max_z_excursion > navigation_probe.MAXIMUM_Z_EXCURSION_M
                or max_tilt > navigation_probe.MAXIMUM_TILT_RAD):
            raise RuntimeError(f"{route_id} native control evidence failed its route envelope")
        return {
            "kind": "arrival", "route_id": route_id,
            "from_region_id": route["from_region_id"],
            "region_id": route["to_region_id"], "producer_id": ROBOT_ID,
            "started": started, "arrival": arrival, "steps_taken": local_step,
            "replans": replans, "events": events,
            "maximum_route_deviation_m": max_deviation,
            "maximum_z_excursion_m": max_z_excursion,
            "maximum_tilt_rad": max_tilt,
            "control_step_evidence": control,
            "contact_view_identities": deepcopy(self.contact_view_identities),
            "contact_snapshots": contact_snapshots,
            "wheel_contact_sample_ratios": ratios,
            "median_weight_support_ratio": median_support,
            "contact_buffer_capacity_reached": contact_buffer_reached,
            "contact_health_validated": contact_ok,
        }


class RestaurantNativeGoalBackend:
    """Translate pending GoalSession skills into native Restaurant operations."""

    def __init__(self, navigator: Any, plan: Mapping[str, Any], *, episode_id: str):
        self.navigator = navigator
        self.plan = deepcopy(dict(plan))
        self.episode_id = episode_id
        self.readiness = RestaurantReadinessBackend(episode_id=episode_id)
        self.robot_regions = {ROBOT_ID: "restaurant_staging"}
        self.evidence: list[dict[str, Any]] = []
        self.business_evaluation: dict[str, Any] | None = None

    def _pending(self, task: Mapping[str, Any], trusted_report: Mapping[str, Any]) -> dict[str, Any]:
        ledger = trusted_report["session"]
        row = ledger["tasks"].get(task["task_id"])
        if (ledger.get("episode_id") != self.episode_id or row is None
                or row["status"] != "pending" or row["task"] != task
                or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native Restaurant task does not match the pending GoalSession dispatch")
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
            raise ValueError("Restaurant backend episode_id mismatch")
        pending = self._pending(task, trusted_report)
        execution = task["execution"]
        end_s = float(execution["start_s"] + execution["duration_s"])
        if not math.isclose(self.navigator.episode_time_s,
                            float(trusted_report["session"]["current_time_s"]), abs_tol=1e-8):
            raise ValueError("native Restaurant clock diverged from GoalSession")
        skill = task["skill"]
        arguments = task["arguments"]
        if skill == "ground_navigation":
            route_ids = arguments.get("route_ids")
            if not isinstance(route_ids, list) or len(route_ids) != 1:
                raise ValueError("formal Restaurant navigation requires exactly one route")
            route_id = route_ids[0]
            definition = PHYSICAL_LEGS.get(route_id)
            if (definition is None
                    or self.robot_regions[ROBOT_ID] != definition["from_region_id"]
                    or arguments.get("region_id") != definition["to_region_id"]):
                raise ValueError("formal Restaurant route does not match current native region")
            native = self.navigator.drive(route_id, deadline_s=end_s)
            self.robot_regions[ROBOT_ID] = definition["to_region_id"]
            native["task_id"] = task["task_id"]
            native["episode_id"] = episode_id
            native["window_hold"] = self.navigator.wait_until(end_s)
            _saved, evidence_ref = self._record(native)
        elif skill == "observe":
            region = str(arguments.get("region_id"))
            if self.robot_regions[ROBOT_ID] != region:
                raise ValueError("Restaurant observation robot has not arrived in its service region")
            scene_observation = self.navigator.observe_region(region)
            stamp = scene_observation["stamp"]
            observation_id = f"{episode_id}:{region}:{stamp['simulation_step']}"
            digest = "sha256:" + canonical_sha256({
                "episode_id": episode_id, "region_id": region,
                "producer_id": ROBOT_ID, "scene_observation": scene_observation,
            })
            service_row = self.readiness.record_observation(
                region_id=region, observation_id=observation_id,
                simulation_step=stamp["simulation_step"], time_s=stamp["time_s"],
                producer_id=ROBOT_ID, evidence_digest=digest)
            native = {"kind": "observation", "episode_id": episode_id,
                      "task_id": task["task_id"], "region_id": region,
                      "producer_id": ROBOT_ID, "stamp": stamp,
                      "scene_observation": scene_observation,
                      "service_record": service_row}
            native["window_hold"] = self.navigator.wait_until(end_s)
            _saved, evidence_ref = self._record(native)
        elif skill == "relay":
            if (self.robot_regions[ROBOT_ID] != "restaurant_staging"
                    or arguments.get("region_id") != "restaurant_staging"):
                raise ValueError("Restaurant readiness relay must run from staging")
            snapshot = self.readiness.snapshot()
            refs = sorted(row["observation_id"] for row in snapshot["observations"].values())
            publication_stamp = self.navigator.step_once()
            publication = self.readiness.publish_relay(
                message_id=f"{episode_id}:readiness-relay",
                simulation_step=publication_stamp["simulation_step"],
                time_s=publication_stamp["time_s"], observation_refs=refs)
            receipt_stamp = self.navigator.step_once()
            business_receipt = self.readiness.commit_readiness_receipt(
                receipt_id=f"{episode_id}:readiness-receipt",
                simulation_step=receipt_stamp["simulation_step"],
                time_s=receipt_stamp["time_s"], observation_refs=refs)
            native = {"kind": "relay_and_receipt", "episode_id": episode_id,
                      "task_id": task["task_id"], "producer_id": ROBOT_ID,
                      "publication_stamp": publication_stamp,
                      "receipt_stamp": receipt_stamp,
                      "relay_publication": publication,
                      "business_receipt": business_receipt}
            native["window_hold"] = self.navigator.wait_until(end_s)
            _saved, evidence_ref = self._record(native)
        else:
            raise ValueError(f"unsupported formal Restaurant skill: {skill}")
        receipt = {
            "backend_id": BACKEND_ID,
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
        row = trusted_report["session"]["tasks"][task["task_id"]]
        now = float(trusted_report["session"]["current_time_s"])
        ref = f"native:{episode_id}:cancel:{task['task_id']}"
        return {"status": "cancelled", "completed_at_s": now,
                "receipt": {"backend_id": BACKEND_ID, "receipt_ref": ref,
                            "task_id": task["task_id"],
                            "action_id": row["dispatch_action_id"],
                            "status": "cancelled", "completed_at_s": now,
                            "episode_id": episode_id, "evidence_refs": [ref]}}


def evaluate_restaurant_business(backend: RestaurantNativeGoalBackend, episode_id: str,
                                 trusted_report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Recompute the three business outcomes from archived native facts."""
    if trusted_report["session"].get("episode_id") != episode_id:
        raise ValueError("Restaurant evaluator episode_id mismatch")
    snapshot = backend.readiness.snapshot()
    receipt = snapshot.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ValueError("Restaurant business episode has no readiness receipt")
    observations = {row["observation_id"]: row for row in snapshot["observations"].values()}
    receipt_review = evaluate_readiness_receipt(
        episode_id=episode_id, receipt=receipt, observations=observations,
        relay_publications=snapshot["relay_publications"])
    outcomes: dict[str, Mapping[str, Any]] = {}
    for region, outcome_id in (("kitchen_service", "kitchen_service_inspected"),
                               ("dining_service", "dining_service_inspected")):
        arrivals = [row for row in backend.evidence
                    if row.get("kind") == "arrival" and row.get("region_id") == region]
        observed = [row for row in backend.evidence
                    if row.get("kind") == "observation" and row.get("region_id") == region]
        if (len(arrivals) != 1 or len(observed) != 1
                or arrivals[0].get("producer_id") != observed[0].get("producer_id")
                or arrivals[0].get("contact_health_validated") is not True
                or observed[0].get("scene_observation", {}).get(
                    "scene_observation_validated") is not True
                or arrivals[0]["arrival"]["simulation_step"]
                >= observed[0]["stamp"]["simulation_step"]):
            raise ValueError(f"Restaurant {region} lacks causal same-robot arrival and observation")
        outcomes[outcome_id] = {
            "status": "validated",
            "evidence_refs": [arrivals[0]["evidence_ref"], observed[0]["evidence_ref"]],
            "evaluator_ref": EVALUATOR_REF,
        }
    relay = [row for row in backend.evidence if row.get("kind") == "relay_and_receipt"]
    if len(relay) != 1 or receipt_review.get("validated") is not True:
        raise ValueError("Restaurant readiness relay evidence is incomplete")
    outcomes["readiness_receipt_committed"] = {
        "status": "validated", "evidence_refs": [relay[0]["evidence_ref"]],
        "evaluator_ref": EVALUATOR_REF,
    }
    backend.business_evaluation = {"receipt_review": receipt_review,
                                   "outcomes": deepcopy(outcomes)}
    return outcomes


def _source_files() -> dict[str, str]:
    paths = (
        Path(__file__).resolve(),
        ROOT / "demo/benchmark/goal_episode_runner.py",
        ROOT / "demo/benchmark/goal_driven_tasks.py",
        ROOT / "demo/benchmark/goal_driven_fixtures.py",
        ROOT / "demo/restaurant_benchmark/service_backend.py",
        ROOT / "demo/restaurant_benchmark/navigation_probe.py",
        Path(runtime_probe.__file__).resolve(),
        Path(restaurant_config.__file__).resolve(),
        ROOT / "simulator.py",
    )
    result = {str(path): file_sha256(path) for path in paths}
    controller = navigation_probe._controller_source()
    result[str(controller)] = file_sha256(controller)
    return dict(sorted(result.items()))


def prepare_episode(output_root: str | Path, *, products_root: str | Path,
                    source_usd: str | Path, device: str) -> dict[str, Any]:
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
    plan = build_restaurant_episode_plan(bundle["public_task"])
    run_id = uuid.uuid4().hex
    request = {
        "schema": "eai.restaurant.formal_goal_episode_request", "version": 1,
        "run_id": run_id, "episode_id": f"restaurant-goal:{run_id}",
        "scene_key": restaurant_config.BENCHMARK_SCENE_KEY,
        "products_root": str(products), "source_usd": str(source),
        "source_sha256": file_sha256(source),
        "metadata_sha256": file_sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": product_validation,
        "selection": runtime_probe._selection(), "requested_device": device,
        "robot_binding": {
            "public_robot_id": ROBOT_ID,
            "native_agent_id": "carter_1",
            "spawn_region_id": "restaurant_staging",
            "spawn_xy_m": list(_A),
        },
        "region_bindings": {
            "restaurant_staging": {"center_xy_m": list(_A)},
            "kitchen_service": {"center_xy_m": list(_E)},
            "dining_service": {"center_xy_m": list(_B)},
        },
        "physical_routes": _physical_routes(products),
        "public_task_sha256": canonical_sha256(bundle["public_task"]),
        "participant_api_sha256": canonical_sha256(bundle["participant_api"]),
        "plan_sha256": canonical_sha256(plan),
        "source_files": _source_files(),
        "release_gates": {"navigation_ready": False, "execution_validated": False,
                          "scoreable": False},
    }
    _write_json(root / "request.json", request)
    return request


def _request_shape_error(request: Any, bundle: Mapping[str, Any],
                         plan: Mapping[str, Any]) -> str | None:
    """Return the first contract violation in a formal episode request, if any."""
    if not isinstance(request, Mapping):
        return "request must be an object"
    run_id = request.get("run_id")
    try:
        parsed_run_id = uuid.UUID(hex=run_id) if isinstance(run_id, str) else None
    except ValueError:
        parsed_run_id = None
    routes = request.get("physical_routes")
    if (request.get("schema") != "eai.restaurant.formal_goal_episode_request"
            or request.get("version") != 1
            or parsed_run_id is None
            or parsed_run_id.version != 4
            or parsed_run_id.hex != run_id
            or request.get("episode_id") != f"restaurant-goal:{run_id}"
            or request.get("scene_key") != restaurant_config.BENCHMARK_SCENE_KEY
            or request.get("selection") != runtime_probe._selection()
            or request.get("robot_binding") != {
                "public_robot_id": ROBOT_ID, "native_agent_id": "carter_1",
                "spawn_region_id": "restaurant_staging", "spawn_xy_m": list(_A)}
            or request.get("region_bindings") != {
                "restaurant_staging": {"center_xy_m": list(_A)},
                "kitchen_service": {"center_xy_m": list(_E)},
                "dining_service": {"center_xy_m": list(_B)}}
            or request.get("requested_device") not in {"cpu", "cuda:0"}
            or request.get("source_sha256") != restaurant_config.SOURCE_SHA256
            or request.get("product_validation", {}).get("validated") is not True
            or not isinstance(routes, Mapping) or set(routes) != set(PHYSICAL_LEGS)
            or request.get("public_task_sha256") != canonical_sha256(bundle["public_task"])
            or request.get("participant_api_sha256") != canonical_sha256(bundle["participant_api"])
            or request.get("plan_sha256") != canonical_sha256(plan)
            or request.get("source_files") != _source_files()
            or request.get("release_gates") != {
                "navigation_ready": False, "execution_validated": False, "scoreable": False}):
        return "invalid Restaurant formal Goal episode request"
    return None


def _load_request(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    bundle = build_goal_task_package("restaurant")
    plan = build_restaurant_episode_plan(bundle["public_task"])
    shape_error = _request_shape_error(request, bundle, plan)
    if shape_error is not None:
        raise ValueError(shape_error)
    products = Path(request["products_root"]).resolve()
    source = Path(request["source_usd"]).resolve()
    if (file_sha256(source) != request["source_sha256"]
            or file_sha256(products / "bench_meta.json") != request["metadata_sha256"]
            or _physical_routes(products) != request["physical_routes"]):
        raise ValueError("Restaurant formal Goal episode inputs changed after preparation")
    from EAI_assets.restaurant_benchmark_products import resolve_restaurant_benchmark_products
    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant formal Goal episode bundle changed after preparation")
    return request, bundle, plan


_ARCHIVE_EPISODE_FILES = (
    "episode_manifest.json", "public_task.json", "participant_api.json",
    "goal_session_report.json", "skill_receipts.json", "business_evidence.json",
    "team_usage.json", "participant_trace.json", "hidden_evaluator_trace.json",
    "runtime_snapshots.json", "result.json", "native_evidence_log.json",
    "readiness_service_snapshot.json", "goal_episode_source.json",
)
_ROUTE_ORDER = (
    "restaurant_staging_to_kitchen", "restaurant_kitchen_to_staging",
    "restaurant_staging_to_dining", "restaurant_dining_to_staging",
)


def verify_episode_archive(output_root: str | Path) -> dict[str, Any]:
    """Independently recompute a finished episode's evidence chain from disk.

    This never trusts the worker's in-memory state: every hash, identity,
    causal ordering, and business conclusion is recomputed from the archived
    files alone so tampering with any layer surfaces as a hard error.
    """
    base = Path(output_root).expanduser().resolve()
    episode_root = base / "goal_episode"
    missing = [name for name in _ARCHIVE_EPISODE_FILES
               if not (episode_root / name).is_file()]
    missing += [name for name in ("request.json", "restaurant_goal_episode_report.json",
                                  "process.json", "isaac.log")
                if not (base / name).is_file()]
    if missing:
        raise ValueError(f"Restaurant episode archive is incomplete: {sorted(missing)}")

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    request = load(base / "request.json")
    report = load(base / "restaurant_goal_episode_report.json")
    process = load(base / "process.json")
    bundle = build_goal_task_package("restaurant")
    plan = build_restaurant_episode_plan(bundle["public_task"])
    shape_error = _request_shape_error(request, bundle, plan)
    if shape_error is not None:
        raise ValueError(shape_error)
    episode_id = request["episode_id"]

    if process.get("returncode") != 0:
        raise ValueError("archived episode worker did not exit cleanly")
    if (report.get("schema") != "eai.restaurant.formal_goal_episode_report"
            or report.get("version") != 1
            or report.get("run_id") != request["run_id"]
            or report.get("episode_id") != episode_id
            or report.get("status") != "completed"
            or report.get("business_episode_execution_validated") is not True
            or report.get("execution_validated") is not False
            or report.get("scoreable") is not False):
        raise ValueError("archived episode report violates its own release gates")
    source_row = load(episode_root / "goal_episode_source.json")
    if (source_row.get("schema") != "eai.restaurant.formal_goal_episode_source"
            or source_row.get("version") != 1
            or source_row.get("episode_id") != episode_id
            or source_row.get("source_files") != request["source_files"]
            or source_row.get("request_sha256") != file_sha256(base / "request.json")):
        raise ValueError("archived episode source binding is inconsistent")
    if (report.get("native_evidence_log_sha256")
            != file_sha256(episode_root / "native_evidence_log.json")
            or report.get("readiness_service_snapshot_sha256")
            != file_sha256(episode_root / "readiness_service_snapshot.json")
            or process.get("report_sha256")
            != file_sha256(base / "restaurant_goal_episode_report.json")
            or process.get("isaac_log_sha256") != file_sha256(base / "isaac.log")):
        raise ValueError("archived episode hash chain does not match its files")

    manifest = load(episode_root / "episode_manifest.json")
    public_task = load(episode_root / "public_task.json")
    participant_api = load(episode_root / "participant_api.json")
    if (manifest.get("episode_id") != episode_id
            or manifest.get("schema") != "eai.benchmark.formal_goal_episode"
            or manifest.get("task_id") != bundle["public_task"]["task_id"]
            or manifest.get("scene_key") != bundle["public_task"]["mission"]["scene_key"]
            or manifest.get("public_task_sha256") != canonical_sha256(public_task)
            or canonical_sha256(public_task) != canonical_sha256(bundle["public_task"])
            or manifest.get("participant_api_sha256") != canonical_sha256(participant_api)
            or canonical_sha256(participant_api) != canonical_sha256(bundle["participant_api"])
            or manifest.get("execution_validated") is not False
            or manifest.get("scoreable") is not False):
        raise ValueError("archived episode manifest is inconsistent")
    for name in ("result.json", "business_evidence.json", "participant_trace.json",
                 "hidden_evaluator_trace.json", "goal_session_report.json",
                 "native_evidence_log.json", "readiness_service_snapshot.json"):
        if load(episode_root / name).get("episode_id") != episode_id:
            raise ValueError(f"archived {name} carries a foreign episode identity")

    session = load(episode_root / "goal_session_report.json")["report"]
    plan_rows = session.get("plans") or []
    submitted = [row for row in plan_rows
                 if (row.get("plan") or {}).get("plan_id") == plan["plan_id"]]
    if (len(submitted) != 1
            or canonical_sha256(submitted[0]["plan"]) != request["plan_sha256"]):
        raise ValueError("archived session did not submit the frozen witness plan")
    tasks = session.get("tasks") or {}
    if (set(tasks) != {row["task_id"] for row in plan["tasks"]}
            or any(row.get("status") != "succeeded" for row in tasks.values())):
        raise ValueError("archived session tasks did not all succeed")
    outcomes_rows = session.get("business_outcomes") or []
    required = {row["id"] for row in bundle["public_task"]["required_outcomes"]}
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
    if (evidence_log.get("schema") != "eai.restaurant.formal_goal_native_evidence"
            or evidence_log.get("version") != 1):
        raise ValueError("invalid native evidence log schema")
    records = evidence_log.get("records")
    if not isinstance(records, list) or len(records) != len(plan["tasks"]):
        raise ValueError("native evidence record count does not match the witness plan")
    evidence_refs: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("episode_id") != episode_id or "evidence_ref" not in record:
            raise ValueError("native evidence record is not episode-bound")
        body = {key: value for key, value in record.items() if key != "evidence_ref"}
        if record["evidence_ref"] != "sha256:" + canonical_sha256(body):
            raise ValueError("native evidence record does not match its evidence_ref")
        evidence_refs[record["evidence_ref"]] = record
    for row in receipts:
        for ref in row.get("evidence_refs", []):
            if ref not in evidence_refs:
                raise ValueError("skill receipt references missing native evidence")
    for row in outcomes_rows:
        for ref in row.get("evidence_refs", []):
            if ref not in evidence_refs:
                raise ValueError("business outcome references missing native evidence")

    arrivals = [row for row in records if row.get("kind") == "arrival"]
    observations = [row for row in records if row.get("kind") == "observation"]
    relays = [row for row in records if row.get("kind") == "relay_and_receipt"]
    if (len(arrivals) != 4 or len(observations) != 2 or len(relays) != 1
            or [row.get("route_id") for row in arrivals] != list(_ROUTE_ORDER)):
        raise ValueError("native evidence does not follow the frozen route loop")
    region_of = {"restaurant_staging_to_kitchen": "kitchen_service",
                 "restaurant_kitchen_to_staging": "restaurant_staging",
                 "restaurant_staging_to_dining": "dining_service",
                 "restaurant_dining_to_staging": "restaurant_staging"}
    for row in arrivals:
        if (row.get("region_id") != region_of[row["route_id"]]
                or row.get("contact_health_validated") is not True
                or row.get("producer_id") != ROBOT_ID
                or int(row["arrival"]["simulation_step"])
                <= int(row["started"]["simulation_step"])):
            raise ValueError(f"native arrival evidence failed its contract: {row['route_id']}")
    observation_by_region: dict[str, dict[str, Any]] = {}
    arrival_by_region: dict[str, dict[str, Any]] = {}
    for row in arrivals:
        arrival_by_region[row["region_id"]] = row
    for row in observations:
        region = row.get("region_id")
        if (region not in {"kitchen_service", "dining_service"}
                or row.get("scene_observation", {}).get(
                    "scene_observation_validated") is not True
                or row.get("producer_id") != ROBOT_ID):
            raise ValueError(f"native observation evidence failed its contract: {region}")
        if region in observation_by_region:
            raise ValueError(f"duplicate native observation for {region}")
        observation_by_region[region] = row
    if set(observation_by_region) != {"kitchen_service", "dining_service"}:
        raise ValueError("native evidence lacks both service observations")
    relay_row = relays[0]
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
    archived_observations = {row["observation_id"]: row
                             for row in (service.get("observations") or {}).values()}
    for observation in observation_by_region.values():
        service_row = observation.get("service_record") or {}
        if (service_row.get("observation_id") not in archived_observations
                or service_row.get("simulation_step")
                != observation["stamp"]["simulation_step"]
                or service_row.get("time_s") != observation["stamp"]["time_s"]
                or service_row.get("region_id") != observation["region_id"]):
            raise ValueError("archived service observation diverges from native stamp")
    review = evaluate_readiness_receipt(
        episode_id=episode_id, receipt=service.get("receipt"),
        observations=archived_observations,
        relay_publications=service.get("relay_publications"))
    if review.get("validated") is not True:
        raise ValueError("archived readiness receipt did not survive independent review")
    archived_publication = (service.get("relay_publications") or [{}])[0]
    if (int(archived_publication.get("simulation_step", -1))
            != int(relay_row["publication_stamp"]["simulation_step"])
            or int(service["receipt"]["simulation_step"])
            != int(relay_row["receipt_stamp"]["simulation_step"])):
        raise ValueError("archived readiness chain diverges from native relay stamps")

    business = load(episode_root / "business_evidence.json")
    if (set(business.get("outcomes") or {}) != required
            or any(row.get("status") != "validated"
                   or row.get("evaluator_ref") != EVALUATOR_REF
                   or any(ref not in evidence_refs for ref in row.get("evidence_refs", []))
                   for row in business["outcomes"].values())
            or business.get("execution_validated") is not False
            or business.get("scoreable") is not False):
        raise ValueError("archived business evidence is inconsistent")
    usage = load(episode_root / "team_usage.json")
    if (usage.get("selected_team") != [ROBOT_ID]
            or usage.get("active_agents") != [ROBOT_ID]
            or usage.get("action_count") != 2 + len(plan["tasks"])
            or usage.get("scoreable") is not False):
        raise ValueError("archived team usage record is inconsistent")
    return {
        "schema": "eai.restaurant.formal_goal_archive_verification", "version": 1,
        "episode_id": episode_id, "validated": True,
        "record_count": len(records), "receipt_count": len(receipts),
        "outcomes": sorted(required), "routes": list(_ROUTE_ORDER),
        "readiness_review": review,
        "execution_validated": False, "scoreable": False,
    }


def run_worker(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    request, bundle, plan = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    report = {
        "schema": "eai.restaurant.formal_goal_episode_report", "version": 1,
        "run_id": request["run_id"], "episode_id": request["episode_id"],
        "status": "starting", "business_episode_execution_validated": False,
        "execution_validated": False, "scoreable": False,
    }
    report_path = root / "restaurant_goal_episode_report.json"
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

            agent = session.possible_agents[0]
            zero = torch.zeros((session.num_envs, 3), device=session.device)
            for _ in range(navigation_probe.SETTLE_STEPS):
                session.env.step({agent: zero})
            navigator = NativeCarterEpisodeNavigator(
                session, agent, Path(request["products_root"]), request["physical_routes"],
                request["region_bindings"])
            episode_root = root / "goal_episode"
            try:
                backend = RestaurantNativeGoalBackend(
                    navigator, plan, episode_id=request["episode_id"])
                participant = RestaurantWitnessParticipant(plan)
                evaluator = lambda episode_id, trusted: evaluate_restaurant_business(
                    backend, episode_id, trusted)
                result = FormalGoalEpisodeRunner(
                    bundle["public_task"], bundle["participant_api"], participant, backend,
                    output_root=episode_root, episode_id=request["episode_id"],
                    evaluator=evaluator, max_turns=32,
                ).run(initial_state={"robot_regions": {ROBOT_ID: "restaurant_staging"},
                                     "object_states": {}})
            finally:
                navigator.close()
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
                      and result["skill_receipt_count"] == len(plan["tasks"])
                      and result["business_outcomes_validated"] is True
                      and backend.business_evaluation is not None)
            report.update(
                status="completed" if passed else "failed",
                actual_device=str(session.device), control_dt_s=navigator.control_dt_s,
                episode_control_steps=navigator._control_steps,
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
        raise RuntimeError("Restaurant formal business episode did not validate")
    return report


def execute_episode(output_root: str | Path, *, timeout_s: float = 1800.0) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    _load_request(root)
    lock_path = ROOT / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "demo.benchmark.goal_restaurant_episode",
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
    report = json.loads((root / "restaurant_goal_episode_report.json").read_text(
        encoding="utf-8"))
    process = {"returncode": completed.returncode, "duration_s": duration,
               "report_sha256": file_sha256(root / "restaurant_goal_episode_report.json"),
               "isaac_log_sha256": file_sha256(root / "isaac.log"),
               "business_episode_execution_validated":
                   report.get("business_episode_execution_validated") is True}
    _write_json(root / "process.json", process)
    if completed.returncode != 0 or not process["business_episode_execution_validated"]:
        raise RuntimeError("Restaurant formal Goal episode worker failed")
    verification = verify_episode_archive(root)
    _write_json(root / "archive_verification.json", verification)
    return {**process, "archive_verification": verification}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--timeout-s", type=float, default=1800.0)
    parser.add_argument("--verify", action="store_true",
                        help="re-verify a finished episode archive without Isaac")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        run_worker(args.output_root)
        return 0
    if args.verify:
        if args.products_root is not None or args.source_usd is not None:
            parser.error("--verify does not accept --products-root or --source-usd")
        verification = verify_episode_archive(args.output_root)
        print(json.dumps(verification, ensure_ascii=False, sort_keys=True))
        return 0
    if args.products_root is None or args.source_usd is None:
        parser.error("--products-root and --source-usd are required")
    request = prepare_episode(args.output_root, products_root=args.products_root,
                              source_usd=args.source_usd, device=args.device)
    process = execute_episode(args.output_root, timeout_s=args.timeout_s)
    print(json.dumps({"episode_id": request["episode_id"],
                      **{key: value for key, value in process.items()
                         if key != "archive_verification"},
                      "archive_verified": process["archive_verification"]["validated"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
