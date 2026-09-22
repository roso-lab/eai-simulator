"""Route-level native admission for the remaining Restaurant pool anchors.

go2_1 and lite3_1 follow A*-planned ground routes with foot-contact health
gates; iris_1 and iris_2 fly planned aerial polylines with altitude-floor,
route-deviation and tilt gates.  Passing route evidence is what promotes an
anchor into ``BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS``; it never flips
``navigation_ready``, ``execution_validated`` or ``scoreable`` by itself.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import uuid

from . import config as scene_config
from . import contact_probe, runtime_probe
from .candidate_admission import _CANDIDATES, _controller_source, _is_aerial


ROOT = Path(__file__).resolve().parents[2]
# Service-region centers stay bound to the values the Carter routes froze;
# importing them keeps one authority for region geometry.
from .navigation_probe import ROUTES as _CARTER_ROUTES  # noqa: E402

KITCHEN_SERVICE_XY_M = tuple(_CARTER_ROUTES["A-E"]["goal_xy_m"])
DINING_SERVICE_XY_M = tuple(_CARTER_ROUTES["A-B"]["goal_xy_m"])
# First frozen native observation anchor for the authoring-level
# restaurant_overview region: the midpoint of both service-region centers at
# the deployment-profile flight height.
OVERVIEW_ANCHOR_XYZ_M = (
    round((KITCHEN_SERVICE_XY_M[0] + DINING_SERVICE_XY_M[0]) / 2, 3),
    round((KITCHEN_SERVICE_XY_M[1] + DINING_SERVICE_XY_M[1]) / 2, 3),
    3.0,
)

PLANNING_RADIUS_M = 0.40
SPEED_M_S = 0.45
TURN_GAIN = 1.8
# Measured on go2_1 dining v1: commanded speeds at or below ~0.15 m/s produce
# no policy motion (the RSL locomotion policy has a response deadband), which
# stalled the tracker whenever the distance taper floored.  Holding the
# commanded speed at or above ~0.25 m/s keeps every leg responsive; waypoints
# switch on tolerance crossing, so no slow-down near them is required.
MINIMUM_COMMAND_DISTANCE_SCALE = 0.55
INTERMEDIATE_TOLERANCE_M = 0.15
FINAL_TOLERANCE_M = 0.25
STALL_WINDOW_STEPS = 300
MAX_REPLANS = 2
# The lite3 dining route plans to ~48 m; with turning and waypoint taper the
# effective speed is well below the commanded 0.45 m/s, so the ceiling must
# cover roughly 11k control steps plus margin.
MAX_STEPS = 12000
SETTLE_STEPS = 120
MINIMUM_STANCE_FEET_STEP_FRACTION = 0.95
MINIMUM_STANCE_FEET = 2
MINIMUM_MEDIAN_SUPPORT_RATIO = 0.60
MAXIMUM_MEDIAN_SUPPORT_RATIO = 1.40
MAXIMUM_ROUTE_DEVIATION_M = 0.40
MAXIMUM_Z_EXCURSION_M = 0.05
MAXIMUM_TILT_RAD = 0.15
CONTACT_SNAPSHOT_STRIDE = 25
GROUND_ROUTE_SCOPE = "single_legged_candidate_commanded_route_and_foot_contact_only"

AERIAL_FLIGHT_SPEED_M_S = 0.20
AERIAL_SETTLE_STEPS = 240
AERIAL_FINAL_TOLERANCE_M = 0.30
AERIAL_STALL_WINDOW_STEPS = 600
# iris_2 plans a ~41 m detour to the overview anchor; at the proven 0.2 m/s
# carrot speed that is ~10.3k control steps before settle and convergence.
AERIAL_MAXIMUM_STEPS = 14000
MINIMUM_FLIGHT_ALTITUDE_M = 1.0
AERIAL_MAXIMUM_ROUTE_DEVIATION_M = 0.50
AERIAL_MAXIMUM_TILT_RAD = 0.30
AERIAL_SNAPSHOT_STRIDE = 100
AERIAL_ROUTE_SCOPE = "single_aerial_candidate_commanded_route_and_hover_health_only"

ROUTE_PROFILES = {
    "go2_1-dining": {
        "candidate_id": "restaurant_go2_1",
        "goal_xy_m": list(DINING_SERVICE_XY_M),
    },
    # Both legged anchors route to the dining service region: the inflated
    # grid forces the enclosed kitchen to be entered from its staging-side
    # doorway, making the direct-bearing kitchen route (56.5 m from lite3)
    # longer than the dining detour (47.8 m).  Per-anchor native routes need
    # one validated service-region destination, not region diversity.
    "lite3_1-dining": {
        "candidate_id": "restaurant_lite3_1",
        "goal_xy_m": list(DINING_SERVICE_XY_M),
    },
    "iris_1-overview": {
        "candidate_id": "restaurant_iris_1",
        "goal_xyz_m": list(OVERVIEW_ANCHOR_XYZ_M),
    },
    "iris_2-overview": {
        "candidate_id": "restaurant_iris_2",
        "goal_xyz_m": list(OVERVIEW_ANCHOR_XYZ_M),
    },
}


def _profile(route_id: str) -> dict:
    try:
        return ROUTE_PROFILES[route_id]
    except KeyError as exc:
        raise ValueError(f"unknown Restaurant route: {route_id}") from exc


def _spec(route_id: str) -> dict:
    return _CANDIDATES[_profile(route_id)["candidate_id"]]


def _aerial_route(route_id: str) -> bool:
    return _is_aerial(_spec(route_id))


def _probe_scope(route_id: str) -> str:
    if _aerial_route(route_id):
        return AERIAL_ROUTE_SCOPE
    return GROUND_ROUTE_SCOPE


def _source_files(route_id: str) -> dict[str, str]:
    paths = {
        **runtime_probe._probe_source_files(),
        str(Path(contact_probe.__file__).resolve()): runtime_probe._sha256(
            Path(contact_probe.__file__).resolve()
        ),
        str(Path(__file__).resolve()): runtime_probe._sha256(Path(__file__).resolve()),
        str(Path(runtime_probe.__file__).resolve().parent / "candidate_admission.py"): (
            runtime_probe._sha256(
                Path(runtime_probe.__file__).resolve().parent / "candidate_admission.py"
            )
        ),
        str(ROOT / "demo/benchmark/runtime.py"): runtime_probe._sha256(
            ROOT / "demo/benchmark/runtime.py"
        ),
        str(ROOT / "demo/benchmark/control_step_evidence.py"): runtime_probe._sha256(
            ROOT / "demo/benchmark/control_step_evidence.py"
        ),
        str(ROOT / "source/EAI/EAI/scenario_spec/geometry.py"): runtime_probe._sha256(
            ROOT / "source/EAI/EAI/scenario_spec/geometry.py"
        ),
        str(ROOT / "source/EAI/EAI/scenario_spec/oracle.py"): runtime_probe._sha256(
            ROOT / "source/EAI/EAI/scenario_spec/oracle.py"
        ),
    }
    controller = _controller_source(_spec(route_id))
    paths[str(controller)] = runtime_probe._sha256(controller)
    return dict(sorted(paths.items()))


def _thresholds(route_id: str) -> dict:
    if _aerial_route(route_id):
        return {
            "planning_radius_m": PLANNING_RADIUS_M,
            "flight_speed_m_s": AERIAL_FLIGHT_SPEED_M_S,
            "settle_steps": AERIAL_SETTLE_STEPS,
            "final_tolerance_m": AERIAL_FINAL_TOLERANCE_M,
            "stall_window_steps": AERIAL_STALL_WINDOW_STEPS,
            "max_steps": AERIAL_MAXIMUM_STEPS,
            "minimum_flight_altitude_m": MINIMUM_FLIGHT_ALTITUDE_M,
            "maximum_route_deviation_m": AERIAL_MAXIMUM_ROUTE_DEVIATION_M,
            "maximum_tilt_rad": AERIAL_MAXIMUM_TILT_RAD,
            "snapshot_stride": AERIAL_SNAPSHOT_STRIDE,
        }
    return {
        "planning_radius_m": PLANNING_RADIUS_M,
        "speed_m_s": SPEED_M_S,
        "turn_gain": TURN_GAIN,
        "minimum_command_distance_scale": MINIMUM_COMMAND_DISTANCE_SCALE,
        "intermediate_tolerance_m": INTERMEDIATE_TOLERANCE_M,
        "final_tolerance_m": FINAL_TOLERANCE_M,
        "stall_window_steps": STALL_WINDOW_STEPS,
        "max_replans": MAX_REPLANS,
        "max_steps": MAX_STEPS,
        "settle_steps": SETTLE_STEPS,
        "minimum_stance_feet": MINIMUM_STANCE_FEET,
        "minimum_stance_feet_step_fraction": MINIMUM_STANCE_FEET_STEP_FRACTION,
        "median_support_ratio": [
            MINIMUM_MEDIAN_SUPPORT_RATIO,
            MAXIMUM_MEDIAN_SUPPORT_RATIO,
        ],
        "maximum_route_deviation_m": MAXIMUM_ROUTE_DEVIATION_M,
        "maximum_z_excursion_m": MAXIMUM_Z_EXCURSION_M,
        "maximum_tilt_rad": MAXIMUM_TILT_RAD,
        "contact_snapshot_stride": CONTACT_SNAPSHOT_STRIDE,
    }


def _selection(route_id: str) -> dict:
    spec = _spec(route_id)
    spawn = scene_config.BENCHMARK_ROBOT_SPAWN_XYZ_M[spec["spawn_key"]]
    return {
        "version": 1,
        "task_name": f"restaurant_route_admission_{route_id.replace('-', '_')}",
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "robots": [
            {
                "type": spec["robot_type"],
                "controller": {
                    "mode": "default",
                    "cfg": spec["controller_cfg"],
                },
                "attachments": [],
                "spawn_pose": {
                    "position": list(spawn),
                    "rotation": [1.0, 0.0, 0.0, 0.0],
                },
            }
        ],
    }


def _plan_route(products: Path, route_id: str) -> dict:
    from EAI.scenario_spec.geometry import FactoryMap
    from EAI.scenario_spec.oracle import InflatedGrid
    from demo.benchmark.runtime import route_waypoints

    spec = _spec(route_id)
    profile = _profile(route_id)
    start_xy = scene_config.BENCHMARK_ROBOT_SPAWN_XYZ_M[spec["spawn_key"]][:2]
    goal_xy = tuple(
        profile["goal_xyz_m"][:2] if "goal_xyz_m" in profile else profile["goal_xy_m"]
    )
    occupancy = FactoryMap(products / "restaurant_benchmark_map.yaml")
    grid = InflatedGrid(occupancy, PLANNING_RADIUS_M)
    cells = grid.path(start_xy, goal_xy)
    if cells is None:
        raise RuntimeError(f"Restaurant route {route_id} is not passable")
    waypoints = route_waypoints(grid, cells, goal_xy, preserve_goal=True)
    points = [start_xy, *waypoints]
    length = sum(math.dist(a, b) for a, b in zip(points[:-1], points[1:]))
    return {
        "route_id": route_id,
        "start_xy_m": list(start_xy),
        "goal_xy_m": list(goal_xy),
        "goal_xyz_m": list(profile.get("goal_xyz_m", [*goal_xy, 3.0])),
        "map_sha256": occupancy.sha256,
        "route_cells": [list(cell) for cell in cells],
        "waypoints_xy_m": [list(point) for point in waypoints],
        "planned_length_m": length,
    }


def prepare_probe(
    output_root: str | Path,
    *,
    route_id: str,
    products_root: str | Path,
    source_usd: str | Path,
    device: str,
) -> dict:
    if route_id not in ROUTE_PROFILES:
        raise ValueError(f"unknown Restaurant route: {route_id}")
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    products = Path(products_root).expanduser().resolve()
    source = Path(source_usd).expanduser().resolve()

    from tools.assets.build_restaurant_products import validate_products

    validation = validate_products(products, source_usd=source)
    metadata_path = products / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    request = {
        "schema": "eai.restaurant.route_admission_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "route_id": route_id,
        "candidate_id": _profile(route_id)["candidate_id"],
        "candidate_spec": _spec(route_id),
        "products_root": str(products),
        "source_usd": str(source),
        "source_sha256": runtime_probe._sha256(source),
        "metadata_sha256": runtime_probe._sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": validation,
        "source_files": _source_files(route_id),
        "selection": _selection(route_id),
        "route": _plan_route(products, route_id),
        "requested_device": device,
        "thresholds": _thresholds(route_id),
        "release_gates": metadata["release_gates"],
        "execution_validated": False,
        "scoreable": False,
    }
    runtime_probe._write(output / "request.json", request)
    return request


def _validate_request_contract(request: dict) -> None:
    route_id = request.get("route_id")
    if (
        request.get("schema") != "eai.restaurant.route_admission_request"
        or request.get("version") != 1
        or not isinstance(request.get("run_id"), str)
        or re.fullmatch(r"[a-f0-9]{32}", request["run_id"]) is None
        or route_id not in ROUTE_PROFILES
        or request.get("scene_key") != scene_config.BENCHMARK_SCENE_KEY
        or request.get("candidate_id") != _profile(route_id)["candidate_id"]
        or request.get("candidate_spec") != _spec(route_id)
        or request.get("selection") != _selection(route_id)
        or request.get("requested_device") not in {"cpu", "cuda:0"}
        or request.get("thresholds") != _thresholds(route_id)
        or request.get("source_sha256") != scene_config.SOURCE_SHA256
        or request.get("product_validation", {}).get("validated") is not True
        or request.get("source_files") != _source_files(route_id)
        or request.get("release_gates")
        != {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        }
        or request.get("execution_validated") is not False
        or request.get("scoreable") is not False
    ):
        raise ValueError("invalid Restaurant route admission request")
    route = request.get("route", {})
    profile = _profile(route_id)
    profile_goal = (
        profile["goal_xyz_m"][:2] if "goal_xyz_m" in profile else profile["goal_xy_m"]
    )
    if (
        route.get("map_sha256") is None
        or not route.get("route_cells")
        or not route.get("waypoints_xy_m")
        or not isinstance(route.get("planned_length_m"), (int, float))
        or route.get("route_id") != route_id
        or route.get("goal_xy_m") != list(profile_goal)
    ):
        raise ValueError("invalid Restaurant route admission request route payload")


def _load_request(root: Path) -> dict:
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    _validate_request_contract(request)
    products = Path(request["products_root"]).resolve()
    source = Path(request["source_usd"]).resolve()
    if runtime_probe._sha256(source) != request["source_sha256"]:
        raise ValueError("Restaurant source changed after route preparation")
    metadata_path = products / "bench_meta.json"
    if runtime_probe._sha256(metadata_path) != request["metadata_sha256"]:
        raise ValueError("Restaurant metadata changed after route preparation")
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )

    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant bundle changed after route preparation")
    return request


def _tilt(root_state) -> float:
    _w, x, y, _z = (float(value) for value in root_state[3:7])
    cosine = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
    return math.acos(cosine)


def _finite_root_state(state) -> bool:
    return all(math.isfinite(float(value)) for value in state)


def _point_segment_distance(point, start, end) -> float:
    px, py = point
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)
    t = ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (sx + t * dx), py - (sy + t * dy))


def _route_deviation(point, polyline) -> float:
    return min(
        _point_segment_distance(point, a, b) for a, b in zip(polyline[:-1], polyline[1:])
    )


def _polyline_point(polyline, arc_s: float):
    """Return the point at arc length ``arc_s`` along the polyline."""
    remaining = arc_s
    for a, b in zip(polyline[:-1], polyline[1:]):
        segment = math.dist(a, b)
        if remaining <= segment:
            if segment == 0:
                return a
            ratio = remaining / segment
            return (
                a[0] + (b[0] - a[0]) * ratio,
                a[1] + (b[1] - a[1]) * ratio,
            )
        remaining -= segment
    return polyline[-1]


def run_worker(output_root: str | Path) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    route_id = request["route_id"]
    spec = _spec(route_id)
    aerial = _aerial_route(route_id)
    report = {
        "schema": "eai.restaurant.route_admission_report",
        "version": 1,
        "run_id": request["run_id"],
        "scene_key": request["scene_key"],
        "route_id": route_id,
        "candidate_id": request["candidate_id"],
        "scope": _probe_scope(route_id),
        "request_sha256": runtime_probe._canonical_sha256(request),
        "source_files": request["source_files"],
        "status": "starting",
        "identity_validated": False,
        "route_navigation_validated": False,
        "navigation_ready": False,
        "business_episode_execution_validated": False,
        "scoreable": False,
    }
    report_path = root / "route_admission_report.json"
    runtime_probe._write(report_path, report)

    from simulator import SimulatorLaunchConfig, open_simulator_session

    def configure(env_cfg) -> None:
        env_cfg.sim.enable_scene_query_support = True
        env_cfg.episode_length_s = 3600.0

    launch = SimulatorLaunchConfig(
        env=request["selection"]["task_name"],
        resolved_env_name="EAI-Interactive-v0",
        selection_data=request["selection"],
        num_envs=1,
        device=request["requested_device"],
        seed=17,
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        env_cfg_hook=configure,
    )
    settings = None
    previous_disable_processing = None
    try:
        with open_simulator_session(launch) as session:
            import torch

            agent = session.possible_agents[0]
            articulation = session.base_env.scene.articulations[agent]
            body_names = list(articulation.body_names)
            support_bodies = tuple(spec["support_bodies"])
            identity_bodies = tuple(spec.get("identity_bodies", ()))
            link_paths = [str(path) for path in articulation.root_physx_view.link_paths[0]]
            agent_prefix = f"/World/envs/env_0/{agent}"
            identity_ok = (
                agent == spec["runtime_agent"]
                and len(body_names) == len(link_paths)
                and len(set(link_paths)) == len(link_paths)
                and all(body_names.count(name) == 1 for name in support_bodies)
                and all(body_names.count(name) == 1 for name in identity_bodies)
                and all(
                    path == agent_prefix or path.startswith(agent_prefix + "/")
                    for path in link_paths
                )
            )
            if not identity_ok:
                raise RuntimeError("Restaurant route candidate identity mismatch")

            views = {}
            identities = {}
            if not aerial:
                import carb
                from isaacsim.core.simulation_manager import SimulationManager

                settings = carb.settings.get_settings()
                previous_disable_processing = settings.get_as_bool(
                    "/physics/disableContactProcessing"
                )
                settings.set_bool("/physics/disableContactProcessing", False)
                physics_view = SimulationManager.get_physics_sim_view()
                body_paths = {
                    name: link_paths[body_names.index(name)] for name in support_bodies
                }
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
                    if identity != {
                        "sensor_paths": [body_path],
                        "filter_paths": [contact_probe.GROUND_COLLISION_PATH],
                        "max_contact_data_count": contact_probe.CONTACT_BUFFER_CAPACITY,
                    }:
                        raise RuntimeError(f"contact view identity mismatch for {name}")
                    views[name] = view
                    identities[name] = identity

            route = request["route"]
            thresholds = request["thresholds"]
            control_dt = float(session.base_env.step_dt)
            physics_dt = float(session.base_env.sim.get_physics_dt())
            mass_kg = float(
                articulation.root_physx_view.get_masses()[0].detach().cpu().sum().item()
            )
            from demo.benchmark.control_step_evidence import ControlStepEvidence
            from demo.benchmark.runtime import navigation_state, route_waypoints

            recorder = ControlStepEvidence(control_dt)
            command = torch.zeros((session.num_envs, 3), device=session.device)
            events = []
            trajectory = []
            snapshots = []
            contact_steps = {name: 0 for name in support_bodies}
            stance_adequate_steps = 0
            support_ratios = []
            buffer_reached = False
            max_deviation = 0.0
            max_z_excursion = 0.0
            max_tilt = 0.0
            minimum_altitude = float("inf")
            failure = None
            arrival_step = None

            if aerial:
                spawn_anchor = request["selection"]["robots"][0]["spawn_pose"][
                    "position"
                ]
                hover = torch.tensor(
                    [spawn_anchor], device=session.device, dtype=torch.float32
                ).expand(session.num_envs, 3)
                for _ in range(thresholds["settle_steps"]):
                    session.env.step({agent: hover.clone()})
                reference = articulation.data.root_state_w[0].detach().cpu().tolist()
                if not _finite_root_state(reference):
                    raise RuntimeError("non-finite aerial state after settle")

                polyline = [
                    (float(x), float(y))
                    for x, y in [route["start_xy_m"], *route["waypoints_xy_m"]]
                ]
                total_length = sum(
                    math.dist(a, b) for a, b in zip(polyline[:-1], polyline[1:])
                )
                goal_xyz = route["goal_xyz_m"]
                arc_s = 0.0
                best_goal_distance = math.inf
                last_progress_step = 0
                advance_per_step = thresholds["flight_speed_m_s"] * control_dt
                for step in range(1, thresholds["max_steps"] + 1):
                    if arc_s < total_length:
                        arc_s = min(total_length, arc_s + advance_per_step)
                    point = _polyline_point(polyline, arc_s)
                    command[0, 0] = point[0]
                    command[0, 1] = point[1]
                    command[0, 2] = goal_xyz[2]
                    submitted = {agent: command[0].detach().cpu().tolist()}
                    session.env.step({agent: command})
                    root_state = (
                        articulation.data.root_state_w[0].detach().cpu().tolist()
                    )
                    recorder.record(step, submitted, {agent: root_state})
                    if not _finite_root_state(root_state):
                        failure = "non_finite_root_state"
                        break
                    xy = (float(root_state[0]), float(root_state[1]))
                    altitude = float(root_state[2])
                    tilt = _tilt(root_state)
                    deviation = _route_deviation(xy, polyline)
                    minimum_altitude = min(minimum_altitude, altitude)
                    max_deviation = max(max_deviation, deviation)
                    max_tilt = max(max_tilt, tilt)
                    goal_distance = math.dist(root_state[:3], goal_xyz)
                    trajectory.append(
                        {
                            "step": step,
                            "simulation_time_s": float(
                                session.base_env.sim.current_time
                            ),
                            "carrot_arc_s_m": arc_s,
                            "command": submitted[agent],
                            "root_state_w": [float(value) for value in root_state],
                            "route_deviation_m": deviation,
                            "altitude_m": altitude,
                            "tilt_rad": tilt,
                            "distance_to_goal_m": goal_distance,
                        }
                    )
                    if step % thresholds["snapshot_stride"] == 0:
                        snapshots.append(
                            {
                                "step": step,
                                "altitude_m": altitude,
                                "route_deviation_m": deviation,
                                "tilt_rad": tilt,
                                "distance_to_goal_m": goal_distance,
                            }
                        )
                    if goal_distance < best_goal_distance - 0.005:
                        best_goal_distance = goal_distance
                        last_progress_step = step
                    if arc_s >= total_length and (
                        step - last_progress_step > thresholds["stall_window_steps"]
                    ):
                        failure = "aerial_convergence_stall"
                        break
                    if goal_distance <= thresholds["final_tolerance_m"]:
                        arrival_step = step
                        break
            else:
                from EAI.scenario_spec.geometry import FactoryMap
                from EAI.scenario_spec.oracle import InflatedGrid

                for _ in range(thresholds["settle_steps"]):
                    session.env.step({agent: command})
                reference = articulation.data.root_state_w[0].detach().cpu().tolist()
                if not _finite_root_state(reference):
                    raise RuntimeError("non-finite legged state after settle")
                weight_N = mass_kg * 9.81
                initial_polyline = [
                    (float(x), float(y))
                    for x, y in [route["start_xy_m"], *route["waypoints_xy_m"]]
                ]
                active_waypoints = [tuple(p) for p in route["waypoints_xy_m"]]
                waypoint_index = 0
                replans = 0
                best_distance = math.inf
                best_heading = math.inf
                last_progress_step = 0
                occupancy = FactoryMap(
                    Path(request["products_root"]) / "restaurant_benchmark_map.yaml"
                )
                grid = InflatedGrid(occupancy, PLANNING_RADIUS_M)

                def replan(current_xy, step):
                    nonlocal active_waypoints, waypoint_index, replans
                    cells = grid.path(current_xy, route["goal_xy_m"])
                    if cells is None:
                        return False
                    active_waypoints = [
                        tuple(p)
                        for p in route_waypoints(
                            grid, cells, route["goal_xy_m"], preserve_goal=True
                        )
                    ]
                    waypoint_index = 0
                    replans += 1
                    events.append(
                        {
                            "event": "route_replanned",
                            "step": step,
                            "replan_index": replans,
                            "start_xy_m": list(current_xy),
                            "route_cells": [list(cell) for cell in cells],
                            "waypoints_xy_m": [list(p) for p in active_waypoints],
                        }
                    )
                    return True

                from demo.benchmark.runtime import velocity_policy_command

                for step in range(1, thresholds["max_steps"] + 1):
                    x, y, _z, yaw = navigation_state(articulation)
                    while waypoint_index < len(active_waypoints):
                        tolerance = (
                            thresholds["final_tolerance_m"]
                            if waypoint_index == len(active_waypoints) - 1
                            else thresholds["intermediate_tolerance_m"]
                        )
                        if math.dist((x, y), active_waypoints[waypoint_index]) > tolerance:
                            break
                        events.append(
                            {
                                "event": "waypoint_reached",
                                "step": step - 1,
                                "waypoint_index": waypoint_index,
                                "position_xy_m": [x, y],
                            }
                        )
                        waypoint_index += 1
                        best_distance = math.inf
                        best_heading = math.inf
                        last_progress_step = step - 1
                    if waypoint_index >= len(active_waypoints):
                        if math.dist((x, y), route["goal_xy_m"]) <= thresholds[
                            "final_tolerance_m"
                        ]:
                            arrival_step = step - 1
                            break
                        failure = "waypoint_sequence_ended_outside_goal_tolerance"
                        break

                    target = active_waypoints[waypoint_index]
                    dx, dy = target[0] - x, target[1] - y
                    distance = math.hypot(dx, dy)
                    desired = math.atan2(dy, dx)
                    heading_error = (
                        desired - yaw + math.pi
                    ) % (2 * math.pi) - math.pi
                    linear, strafe, angular = velocity_policy_command(
                        distance=distance,
                        heading_error=heading_error,
                        speed=thresholds["speed_m_s"],
                        k_turn=thresholds["turn_gain"],
                        minimum_distance_scale=thresholds[
                            "minimum_command_distance_scale"
                        ],
                    )
                    command.zero_()
                    command[0, 0] = linear
                    command[0, 1] = strafe
                    command[0, 2] = angular
                    submitted = {agent: command[0].detach().cpu().tolist()}
                    session.env.step({agent: command})
                    root_state = (
                        articulation.data.root_state_w[0].detach().cpu().tolist()
                    )
                    recorder.record(step, submitted, {agent: root_state})
                    if not _finite_root_state(root_state):
                        failure = "non_finite_root_state"
                        break
                    post_xy = (float(root_state[0]), float(root_state[1]))
                    post_distance = math.dist(post_xy, target)
                    deviation = _route_deviation(post_xy, initial_polyline)
                    z_excursion = abs(float(root_state[2]) - float(reference[2]))
                    tilt = _tilt(root_state)
                    max_deviation = max(max_deviation, deviation)
                    max_z_excursion = max(max_z_excursion, z_excursion)
                    max_tilt = max(max_tilt, tilt)

                    feet = {}
                    total_ground_z = 0.0
                    stance_feet = 0
                    for name, view in views.items():
                        sample = contact_probe._sample_contact_view(
                            view, physics_dt=physics_dt
                        )
                        feet[name] = sample
                        force_z = sample["ground_force_N"][2]
                        total_ground_z += force_z
                        if force_z > contact_probe.CONTACT_FORCE_THRESHOLD_N:
                            contact_steps[name] += 1
                            stance_feet += 1
                        buffer_reached = (
                            buffer_reached or sample["buffer_capacity_reached"]
                        )
                    support_ratio = total_ground_z / weight_N
                    support_ratios.append(support_ratio)
                    if stance_feet >= thresholds["minimum_stance_feet"]:
                        stance_adequate_steps += 1
                    if (
                        step == 1
                        or step % thresholds["contact_snapshot_stride"] == 0
                        or stance_feet < thresholds["minimum_stance_feet"]
                    ):
                        snapshots.append(
                            {
                                "step": step,
                                "stance_feet": stance_feet,
                                "weight_support_ratio": support_ratio,
                                "feet": feet,
                            }
                        )
                    trajectory.append(
                        {
                            "step": step,
                            "simulation_time_s": float(
                                session.base_env.sim.current_time
                            ),
                            "waypoint_index": waypoint_index,
                            "target_xy_m": list(target),
                            "command": submitted[agent],
                            "root_state_w": [float(value) for value in root_state],
                            "distance_to_waypoint_m": post_distance,
                            "route_deviation_m": deviation,
                        }
                    )
                    if post_distance < best_distance - 0.005:
                        best_distance = post_distance
                        last_progress_step = step
                    post_yaw = navigation_state(articulation)[3]
                    post_heading = abs(
                        (math.atan2(target[1] - post_xy[1], target[0] - post_xy[0])
                         - post_yaw + math.pi)
                        % (2 * math.pi) - math.pi
                    )
                    if post_heading < best_heading - 0.01:
                        best_heading = post_heading
                        last_progress_step = step
                    if step - last_progress_step > thresholds["stall_window_steps"]:
                        if replans < thresholds["max_replans"] and replan(post_xy, step):
                            best_distance = math.inf
                            best_heading = math.inf
                            last_progress_step = step
                        else:
                            failure = "navigation_deadlock"
                            break
                    if math.dist(post_xy, route["goal_xy_m"]) <= thresholds[
                        "final_tolerance_m"
                    ]:
                        arrival_step = step
                        break

            steps_taken = len(trajectory)
            control_evidence = recorder.report()
            final_state = trajectory[-1]["root_state_w"] if trajectory else reference
            if aerial:
                final_distance = math.dist(final_state[:3], route["goal_xyz_m"])
            else:
                # Ground goals live on the floor; comparing against the
                # aerial z=3.0 default would swallow the horizontal arrival.
                final_distance = math.dist(final_state[:2], route["goal_xy_m"])
            common_ok = (
                identity_ok
                and failure is None
                and arrival_step is not None
                and final_distance <= thresholds["final_tolerance_m"]
                and control_evidence["validated"] is True
                and control_evidence["sample_count"] == steps_taken
            )
            if aerial:
                health_ok = (
                    minimum_altitude >= thresholds["minimum_flight_altitude_m"]
                    and max_deviation <= thresholds["maximum_route_deviation_m"]
                    and max_tilt <= thresholds["maximum_tilt_rad"]
                )
                gate_fields = {
                    "minimum_observed_altitude_m": (
                        minimum_altitude if math.isfinite(minimum_altitude) else None
                    ),
                    "contact_health_validated": None,
                    "aerial_health_validated": health_ok,
                }
            else:
                sorted_support = sorted(support_ratios)
                median_support = (
                    sorted_support[len(sorted_support) // 2] if sorted_support else 0.0
                )
                stance_fraction = (
                    stance_adequate_steps / steps_taken if steps_taken else 0.0
                )
                contact_ok = (
                    steps_taken > 0
                    and not buffer_reached
                    and stance_fraction
                    >= thresholds["minimum_stance_feet_step_fraction"]
                    and thresholds["median_support_ratio"][0]
                    <= median_support
                    <= thresholds["median_support_ratio"][1]
                )
                health_ok = (
                    contact_ok
                    and max_deviation <= thresholds["maximum_route_deviation_m"]
                    and max_z_excursion <= thresholds["maximum_z_excursion_m"]
                    and max_tilt <= thresholds["maximum_tilt_rad"]
                )
                gate_fields = {
                    "foot_contact_sample_ratios": {
                        name: (contact_steps[name] / steps_taken if steps_taken else 0.0)
                        for name in support_bodies
                    },
                    "stance_feet_step_fraction": stance_fraction,
                    "median_weight_support_ratio": median_support,
                    "contact_buffer_capacity_reached": buffer_reached,
                    "contact_health_validated": contact_ok,
                    "aerial_health_validated": None,
                    "maximum_z_excursion_m": max_z_excursion,
                }
            report.update(
                status="diagnostic_completed",
                actual_device=str(session.device),
                physics_dt_s=physics_dt,
                control_step_dt_s=control_dt,
                route=route,
                mass_kg=mass_kg,
                identity_validated=identity_ok,
                steps_taken=steps_taken,
                arrival_step=arrival_step,
                final_distance_to_goal_m=final_distance,
                events=events,
                trajectory=trajectory,
                control_step_evidence=control_evidence,
                health_snapshots=snapshots,
                maximum_route_deviation_m=max_deviation,
                maximum_tilt_rad=max_tilt,
                failure=failure,
                route_navigation_validated=common_ok and health_ok,
                navigation_ready=False,
                business_episode_execution_validated=False,
                scoreable=False,
                **gate_fields,
            )
            runtime_probe._write(report_path, report)
    except BaseException as exc:
        report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        runtime_probe._write(report_path, report)
        raise
    finally:
        if settings is not None and previous_disable_processing is not None:
            settings.set_bool(
                "/physics/disableContactProcessing", previous_disable_processing
            )
    if not report["route_navigation_validated"]:
        raise RuntimeError(f"Restaurant route {route_id} did not pass")
    return report


def execute_probe(output_root: str | Path, *, timeout_s: float) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    lock_path = ROOT / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "demo.restaurant_benchmark.route_admission",
        "--worker",
        "--output-root",
        str(root),
    ]
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another Restaurant native probe is running") from exc
        environment = {
            **os.environ,
            "EAI_RESTAURANT_BENCHMARK_ROOT": request["products_root"],
        }
        started = time.monotonic()
        with (root / "isaac.log").open("x", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Restaurant route probe failed with exit code "
                f"{completed.returncode}; see {root / 'isaac.log'}"
            )
    report = json.loads(
        (root / "route_admission_report.json").read_text(encoding="utf-8")
    )
    if (
        report.get("status") != "diagnostic_completed"
        or report.get("scope") != _probe_scope(request["route_id"])
        or report.get("request_sha256") != runtime_probe._canonical_sha256(request)
        or report.get("source_files") != _source_files(request["route_id"])
        or report.get("navigation_ready") is not False
        or report.get("business_episode_execution_validated") is not False
        or report.get("scoreable") is not False
        or not isinstance(report.get("route_navigation_validated"), bool)
    ):
        raise RuntimeError("Restaurant route worker produced an invalid report")
    report["wall_time_s"] = time.monotonic() - started
    runtime_probe._write(root / "route_admission_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--route", choices=tuple(ROUTE_PROFILES))
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        report = run_worker(args.output_root)
    else:
        if args.route is None or args.products_root is None or args.source_usd is None:
            parser.error("--route, --products-root and --source-usd are required")
        prepare_probe(
            args.output_root,
            route_id=args.route,
            products_root=args.products_root,
            source_usd=args.source_usd,
            device=args.device,
        )
        report = execute_probe(args.output_root, timeout_s=args.timeout_s)
    print(
        json.dumps(
            {
                "route_id": report.get("route_id"),
                "status": report.get("status"),
                "route_navigation_validated": report.get("route_navigation_validated"),
                "steps_taken": report.get("steps_taken"),
                "failure": report.get("failure"),
                "navigation_ready": False,
                "execution_validated": False,
                "scoreable": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
