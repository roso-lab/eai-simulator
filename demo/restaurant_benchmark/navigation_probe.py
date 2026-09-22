"""Run source-bound Carter navigation routes on the Restaurant candidate."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

from demo.benchmark.control_step_evidence import ControlStepEvidence
from demo.benchmark.runtime import navigation_state, route_waypoints, steering_command

from . import config as scene_config
from . import contact_probe
from . import runtime_probe


ROOT = Path(__file__).resolve().parents[2]
ROUTES = {
    "A-E": {"start_xy_m": (21.875, 20.875), "goal_xy_m": (24.225, 17.625)},
    "A-B": {"start_xy_m": (21.875, 20.875), "goal_xy_m": (14.225, 21.925)},
    # The second formal-service Carter starts in a separate dining-zone
    # staging area. Keeping this as an independently runnable route lets the
    # formal episode freeze its scheduling window from native time evidence.
    "C-B": {"start_xy_m": (10.675, 18.375), "goal_xy_m": (14.225, 21.925)},
}
PLANNING_RADIUS_M = 0.40
SPEED_M_S = 0.45
TURN_GAIN = 1.8
INTERMEDIATE_TOLERANCE_M = 0.15
FINAL_TOLERANCE_M = 0.25
STALL_WINDOW_STEPS = 300
MAX_REPLANS = 2
MAX_STEPS = 6000
SETTLE_STEPS = 120
MINIMUM_WHEEL_CONTACT_RATIO = 0.70
MINIMUM_MEDIAN_SUPPORT_RATIO = 0.60
MAXIMUM_MEDIAN_SUPPORT_RATIO = 1.40
MAXIMUM_ROUTE_DEVIATION_M = 0.40
MAXIMUM_Z_EXCURSION_M = 0.05
MAXIMUM_TILT_RAD = 0.15
CONTACT_SNAPSHOT_STRIDE = 25
PROBE_SCOPE = "single_carter_candidate_v7_commanded_route_and_wheel_contact_only"


def _controller_source() -> Path:
    root = os.environ.get("EAI_CONTROLLER_ROOT")
    base = (
        Path(root).expanduser().resolve()
        if root
        else ROOT / "source/EAI_assets/EAI_assets/controller"
    )
    path = base / "traditional/carter_diff/carter_diff.py"
    if not path.is_file():
        raise RuntimeError(
            "Carter controller source is unavailable; set EAI_CONTROLLER_ROOT"
        )
    return path.resolve()


def _source_files() -> dict[str, str]:
    paths = {
        **runtime_probe._probe_source_files(),
        str(Path(contact_probe.__file__).resolve()): runtime_probe._sha256(
            Path(contact_probe.__file__).resolve()
        ),
        str(Path(__file__).resolve()): runtime_probe._sha256(Path(__file__).resolve()),
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
    controller = _controller_source()
    paths[str(controller)] = runtime_probe._sha256(controller)
    return dict(sorted(paths.items()))


def _thresholds() -> dict:
    return {
        "planning_radius_m": PLANNING_RADIUS_M,
        "speed_m_s": SPEED_M_S,
        "turn_gain": TURN_GAIN,
        "intermediate_tolerance_m": INTERMEDIATE_TOLERANCE_M,
        "final_tolerance_m": FINAL_TOLERANCE_M,
        "stall_window_steps": STALL_WINDOW_STEPS,
        "max_replans": MAX_REPLANS,
        "max_steps": MAX_STEPS,
        "settle_steps": SETTLE_STEPS,
        "minimum_wheel_contact_ratio": MINIMUM_WHEEL_CONTACT_RATIO,
        "median_support_ratio": [
            MINIMUM_MEDIAN_SUPPORT_RATIO,
            MAXIMUM_MEDIAN_SUPPORT_RATIO,
        ],
        "maximum_route_deviation_m": MAXIMUM_ROUTE_DEVIATION_M,
        "maximum_z_excursion_m": MAXIMUM_Z_EXCURSION_M,
        "maximum_tilt_rad": MAXIMUM_TILT_RAD,
        "contact_snapshot_stride": CONTACT_SNAPSHOT_STRIDE,
    }


def _selection_for_route(route_id: str) -> dict:
    """Return the exact native selection bound to one frozen route start.

    The historical A-* diagnostics predate explicit spawn poses and keep their
    original selection contract.  Deployment-v2 routes must name their start
    pose so the native articulation begins where the public route says it
    begins; otherwise a geometrically valid path could be driven from the
    builder's legacy formation and produce misleading deviation evidence.
    """
    selection = runtime_probe._selection()
    if route_id == "C-B":
        start = ROUTES[route_id]["start_xy_m"]
        selection["robots"][0]["spawn_pose"] = {
            "position": [*start, scene_config.BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_2"][2]],
            "rotation": [1.0, 0.0, 0.0, 0.0],
        }
    return selection


def _plan_route(products: Path, route_id: str) -> dict:
    from EAI.scenario_spec.geometry import FactoryMap
    from EAI.scenario_spec.oracle import InflatedGrid

    route = ROUTES[route_id]
    occupancy = FactoryMap(products / "restaurant_benchmark_map.yaml")
    grid = InflatedGrid(occupancy, PLANNING_RADIUS_M)
    cells = grid.path(route["start_xy_m"], route["goal_xy_m"])
    if cells is None:
        raise RuntimeError(f"Restaurant route {route_id} is not passable")
    waypoints = route_waypoints(
        grid, cells, route["goal_xy_m"], preserve_goal=True
    )
    points = [route["start_xy_m"], *waypoints]
    length = sum(math.dist(a, b) for a, b in zip(points[:-1], points[1:]))
    return {
        "route_id": route_id,
        "start_xy_m": list(route["start_xy_m"]),
        "goal_xy_m": list(route["goal_xy_m"]),
        "map_sha256": occupancy.sha256,
        "route_cells": [list(cell) for cell in cells],
        "waypoints_xy_m": [list(point) for point in waypoints],
        "planned_length_m": length,
    }


def prepare_probe(
    output_root: str | Path,
    *,
    products_root: str | Path,
    source_usd: str | Path,
    route_id: str,
    device: str,
) -> dict:
    if route_id not in ROUTES:
        raise ValueError(f"unknown route {route_id}")
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
        "schema": "eai.restaurant.native_navigation_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "products_root": str(products),
        "source_usd": str(source),
        "source_sha256": runtime_probe._sha256(source),
        "metadata_sha256": runtime_probe._sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": validation,
        "selection": _selection_for_route(route_id),
        "requested_device": device,
        "route": _plan_route(products, route_id),
        "thresholds": _thresholds(),
        "source_files": _source_files(),
        "release_gates": metadata["release_gates"],
        "business_episode_execution_validated": False,
        "scoreable": False,
    }
    runtime_probe._write(output / "request.json", request)
    return request


def _validate_request_contract(request: dict) -> None:
    run_id = request.get("run_id")
    try:
        parsed = uuid.UUID(hex=run_id) if isinstance(run_id, str) else None
    except ValueError:
        parsed = None
    route = request.get("route")
    route_id = route.get("route_id") if isinstance(route, dict) else None
    expected_route = ROUTES.get(route_id)
    if (
        request.get("schema") != "eai.restaurant.native_navigation_request"
        or request.get("version") != 1
        or parsed is None
        or parsed.version != 4
        or parsed.hex != run_id
        or request.get("scene_key") != scene_config.BENCHMARK_SCENE_KEY
        or request.get("selection") != _selection_for_route(route_id)
        or request.get("requested_device") not in {"cpu", "cuda:0"}
        or request.get("source_sha256") != scene_config.SOURCE_SHA256
        or request.get("product_validation", {}).get("validated") is not True
        or request.get("source_files") != _source_files()
        or request.get("thresholds") != _thresholds()
        or expected_route is None
        or route.get("start_xy_m") != list(expected_route["start_xy_m"])
        or route.get("goal_xy_m") != list(expected_route["goal_xy_m"])
        or not isinstance(route.get("route_cells"), list)
        or not route["route_cells"]
        or not isinstance(route.get("waypoints_xy_m"), list)
        or not route["waypoints_xy_m"]
        or request.get("release_gates")
        != {"navigation_ready": False, "execution_validated": False, "scoreable": False}
        or request.get("business_episode_execution_validated") is not False
        or request.get("scoreable") is not False
    ):
        raise ValueError("invalid Restaurant native navigation request")


def _load_request(root: Path) -> dict:
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    _validate_request_contract(request)
    products = Path(request["products_root"]).resolve()
    source = Path(request["source_usd"]).resolve()
    if runtime_probe._sha256(source) != request["source_sha256"]:
        raise ValueError("Restaurant source changed after navigation preparation")
    metadata_path = products / "bench_meta.json"
    if runtime_probe._sha256(metadata_path) != request["metadata_sha256"]:
        raise ValueError("Restaurant metadata changed after navigation preparation")
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )

    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant bundle changed after navigation preparation")
    if _plan_route(products, request["route"]["route_id"]) != request["route"]:
        raise ValueError("Restaurant navigation route changed after preparation")
    return request


def _point_segment_distance(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    squared = dx * dx + dy * dy
    if squared == 0:
        return math.dist(point, start)
    fraction = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / squared
    fraction = max(0.0, min(1.0, fraction))
    closest = (start[0] + fraction * dx, start[1] + fraction * dy)
    return math.dist(point, closest)


def _route_deviation(point, polyline) -> float:
    return min(
        _point_segment_distance(point, start, end)
        for start, end in zip(polyline[:-1], polyline[1:])
    )


def _tilt(root_state) -> float:
    _w, x, y, _z = (float(value) for value in root_state[3:7])
    cosine = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
    return math.acos(cosine)


def _finite_root_state(state) -> bool:
    return len(state) == 13 and all(math.isfinite(float(value)) for value in state)


def run_worker(output_root: str | Path) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    report = {
        "schema": "eai.restaurant.native_navigation_probe",
        "version": 1,
        "run_id": request["run_id"],
        "scene_key": request["scene_key"],
        "scope": PROBE_SCOPE,
        "route_id": request["route"]["route_id"],
        "request_sha256": runtime_probe._canonical_sha256(request),
        "source_files": request["source_files"],
        "status": "starting",
        "route_navigation_validated": False,
        "navigation_validated": False,
        "navigation_ready": False,
        "business_episode_execution_validated": False,
        "scoreable": False,
    }
    report_path = root / "navigation_probe_report.json"
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
            import carb
            import torch
            from isaacsim.core.simulation_manager import SimulationManager

            agent = session.possible_agents[0]
            articulation = session.base_env.scene.articulations[agent]
            body_names = list(articulation.body_names)
            missing = [name for name in contact_probe.WHEEL_NAMES if name not in body_names]
            if missing:
                raise RuntimeError(f"Carter wheel bodies are missing: {missing}")
            link_paths = articulation.root_physx_view.link_paths
            if len(link_paths) != 1:
                raise RuntimeError("navigation probe requires one Carter articulation")
            body_paths = {
                name: str(link_paths[0][body_names.index(name)])
                for name in contact_probe.WHEEL_NAMES
            }

            settings = carb.settings.get_settings()
            previous_disable_processing = settings.get_as_bool(
                "/physics/disableContactProcessing"
            )
            settings.set_bool("/physics/disableContactProcessing", False)
            physics_view = SimulationManager.get_physics_sim_view()
            views = {}
            identities = {}
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
                views[name] = view
                identities[name] = identity

            command = torch.zeros((session.num_envs, 3), device=session.device)
            for _ in range(request["thresholds"]["settle_steps"]):
                session.env.step({agent: command})

            reference = articulation.data.root_state_w[0].detach().cpu().tolist()
            if not _finite_root_state(reference):
                raise RuntimeError("non-finite Carter state after settle")
            physics_dt = float(session.base_env.sim.get_physics_dt())
            control_dt = float(session.base_env.step_dt)
            mass_kg = float(
                articulation.root_physx_view.get_masses()[0].detach().cpu().sum().item()
            )
            weight_N = mass_kg * 9.81
            route = request["route"]
            initial_polyline = [route["start_xy_m"], *route["waypoints_xy_m"]]
            active_waypoints = [tuple(point) for point in route["waypoints_xy_m"]]
            waypoint_index = 0
            replans = 0
            events = []
            trajectory = []
            contact_snapshots = []
            contact_steps = {name: 0 for name in contact_probe.WHEEL_NAMES}
            support_ratios = []
            buffer_reached = False
            recorder = ControlStepEvidence(control_dt)
            max_deviation = 0.0
            max_z_excursion = 0.0
            max_tilt = 0.0
            best_distance = math.inf
            best_heading = math.inf
            last_progress_step = 0
            arrival_step = None
            failure = None

            from EAI.scenario_spec.geometry import FactoryMap
            from EAI.scenario_spec.oracle import InflatedGrid

            occupancy = FactoryMap(
                Path(request["products_root"]) / "restaurant_benchmark_map.yaml"
            )
            grid = InflatedGrid(occupancy, PLANNING_RADIUS_M)

            def replan(current_xy, step):
                nonlocal active_waypoints, waypoint_index, replans
                cells = grid.path(current_xy, route["goal_xy_m"])
                if cells is None:
                    return False
                active_waypoints = route_waypoints(
                    grid, cells, route["goal_xy_m"], preserve_goal=True
                )
                active_waypoints = [tuple(point) for point in active_waypoints]
                waypoint_index = 0
                replans += 1
                events.append(
                    {
                        "event": "route_replanned",
                        "step": step,
                        "replan_index": replans,
                        "start_xy_m": list(current_xy),
                        "route_cells": [list(cell) for cell in cells],
                        "waypoints_xy_m": [list(point) for point in active_waypoints],
                    }
                )
                return True

            for step in range(1, request["thresholds"]["max_steps"] + 1):
                x, y, _z, yaw = navigation_state(articulation)
                while waypoint_index < len(active_waypoints):
                    tolerance = (
                        FINAL_TOLERANCE_M
                        if waypoint_index == len(active_waypoints) - 1
                        else INTERMEDIATE_TOLERANCE_M
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
                    if math.dist((x, y), route["goal_xy_m"]) <= FINAL_TOLERANCE_M:
                        arrival_step = step - 1
                        break
                    failure = "waypoint_sequence_ended_outside_goal_tolerance"
                    break

                target = active_waypoints[waypoint_index]
                dx, dy = target[0] - x, target[1] - y
                distance = math.hypot(dx, dy)
                desired = math.atan2(dy, dx)
                heading_error = (desired - yaw + math.pi) % (2 * math.pi) - math.pi
                linear, angular = steering_command(
                    distance=distance,
                    heading_error=heading_error,
                    speed=SPEED_M_S,
                    k_turn=TURN_GAIN,
                )
                command.zero_()
                command[0, 0] = linear
                command[0, 2] = angular
                submitted = {agent: command[0].detach().cpu().tolist()}
                session.env.step({agent: command})
                root_state = articulation.data.root_state_w[0].detach().cpu().tolist()
                recorder.record(step, submitted, {agent: root_state})
                if not _finite_root_state(root_state):
                    failure = "non_finite_root_state"
                    break
                post_xy = (float(root_state[0]), float(root_state[1]))
                post_yaw = navigation_state(articulation)[3]
                post_distance = math.dist(post_xy, target)
                post_heading = abs(
                    (math.atan2(target[1] - post_xy[1], target[0] - post_xy[0])
                     - post_yaw + math.pi)
                    % (2 * math.pi)
                    - math.pi
                )
                deviation = _route_deviation(post_xy, initial_polyline)
                z_excursion = abs(float(root_state[2]) - float(reference[2]))
                tilt = _tilt(root_state)
                max_deviation = max(max_deviation, deviation)
                max_z_excursion = max(max_z_excursion, z_excursion)
                max_tilt = max(max_tilt, tilt)

                wheels = {}
                total_ground_z = 0.0
                for name, view in views.items():
                    wheel = contact_probe._sample_contact_view(
                        view, physics_dt=physics_dt
                    )
                    wheels[name] = wheel
                    total_ground_z += wheel["ground_force_N"][2]
                    if wheel["ground_force_N"][2] > contact_probe.CONTACT_FORCE_THRESHOLD_N:
                        contact_steps[name] += 1
                    buffer_reached = buffer_reached or wheel["buffer_capacity_reached"]
                support_ratio = total_ground_z / weight_N
                support_ratios.append(support_ratio)
                if (
                    step == 1
                    or step % CONTACT_SNAPSHOT_STRIDE == 0
                    or any(
                        wheel["ground_force_N"][2]
                        <= contact_probe.CONTACT_FORCE_THRESHOLD_N
                        for wheel in wheels.values()
                    )
                ):
                    contact_snapshots.append(
                        {
                            "step": step,
                            "weight_support_ratio": support_ratio,
                            "wheels": wheels,
                        }
                    )
                trajectory.append(
                    {
                        "step": step,
                        "simulation_time_s": float(session.base_env.sim.current_time),
                        "waypoint_index": waypoint_index,
                        "target_xy_m": list(target),
                        "command": submitted[agent],
                        "root_state_w": [float(value) for value in root_state],
                        "distance_to_waypoint_m": post_distance,
                        "heading_error_rad": post_heading,
                        "route_deviation_m": deviation,
                    }
                )

                if post_distance < best_distance - 0.005:
                    best_distance = post_distance
                    last_progress_step = step
                if post_heading < best_heading - 0.01:
                    best_heading = post_heading
                    last_progress_step = step
                if step - last_progress_step > STALL_WINDOW_STEPS:
                    if replans < MAX_REPLANS and replan(post_xy, step):
                        best_distance = math.inf
                        best_heading = math.inf
                        last_progress_step = step
                    else:
                        failure = "navigation_deadlock"
                        break
                if math.dist(post_xy, route["goal_xy_m"]) <= FINAL_TOLERANCE_M:
                    arrival_step = step
                    break

            steps_taken = len(trajectory)
            control_evidence = recorder.report()
            ratios = {
                name: contact_steps[name] / steps_taken if steps_taken else 0.0
                for name in contact_probe.WHEEL_NAMES
            }
            sorted_support = sorted(support_ratios)
            median_support = (
                sorted_support[len(sorted_support) // 2] if sorted_support else 0.0
            )
            contact_ok = (
                steps_taken > 0
                and not buffer_reached
                and all(value >= MINIMUM_WHEEL_CONTACT_RATIO for value in ratios.values())
                and MINIMUM_MEDIAN_SUPPORT_RATIO
                <= median_support
                <= MAXIMUM_MEDIAN_SUPPORT_RATIO
            )
            final_state = (
                trajectory[-1]["root_state_w"] if trajectory else reference
            )
            final_distance = math.dist(final_state[:2], route["goal_xy_m"])
            route_ok = (
                failure is None
                and arrival_step is not None
                and final_distance <= FINAL_TOLERANCE_M
                and control_evidence["validated"] is True
                and control_evidence["sample_count"] == steps_taken
                and contact_ok
                and max_deviation <= MAXIMUM_ROUTE_DEVIATION_M
                and max_z_excursion <= MAXIMUM_Z_EXCURSION_M
                and max_tilt <= MAXIMUM_TILT_RAD
            )
            report.update(
                status="diagnostic_completed",
                actual_device=str(session.device),
                physics_dt_s=physics_dt,
                control_step_dt_s=control_dt,
                route=route,
                thresholds=request["thresholds"],
                contact_view_identities=identities,
                mass_kg=mass_kg,
                weight_N=weight_N,
                steps_taken=steps_taken,
                arrival_step=arrival_step,
                final_distance_to_goal_m=final_distance,
                replans=replans,
                events=events,
                trajectory=trajectory,
                control_step_evidence=control_evidence,
                contact_snapshots=contact_snapshots,
                wheel_contact_sample_ratios=ratios,
                median_weight_support_ratio=median_support,
                contact_buffer_capacity_reached=buffer_reached,
                contact_health_validated=contact_ok,
                maximum_route_deviation_m=max_deviation,
                maximum_z_excursion_m=max_z_excursion,
                maximum_tilt_rad=max_tilt,
                failure=failure,
                route_navigation_validated=route_ok,
                navigation_validated=False,
                navigation_ready=False,
                business_episode_execution_validated=False,
                scoreable=False,
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
        raise RuntimeError(
            f"Restaurant route {request['route']['route_id']} did not pass"
        )
    return report


def execute_probe(output_root: str | Path, *, timeout_s: float) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    lock_path = ROOT / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "demo.restaurant_benchmark.navigation_probe",
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
                f"Restaurant navigation probe failed with exit code "
                f"{completed.returncode}; see {root / 'isaac.log'}"
            )
    report = json.loads((root / "navigation_probe_report.json").read_text())
    if (
        report.get("status") != "diagnostic_completed"
        or report.get("scope") != PROBE_SCOPE
        or report.get("route_navigation_validated") is not True
        or report.get("navigation_validated") is not False
        or report.get("navigation_ready") is not False
        or report.get("business_episode_execution_validated") is not False
        or report.get("scoreable") is not False
        or report.get("request_sha256") != runtime_probe._canonical_sha256(request)
        or report.get("source_files") != _source_files()
    ):
        raise RuntimeError("Restaurant navigation worker did not produce a passing report")
    report["wall_time_s"] = time.monotonic() - started
    runtime_probe._write(root / "navigation_probe_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--route", choices=tuple(ROUTES))
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        report = run_worker(args.output_root)
    else:
        if args.products_root is None or args.source_usd is None or args.route is None:
            parser.error("--products-root, --source-usd and --route are required")
        prepare_probe(
            args.output_root,
            products_root=args.products_root,
            source_usd=args.source_usd,
            route_id=args.route,
            device=args.device,
        )
        report = execute_probe(args.output_root, timeout_s=args.timeout_s)
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
