"""Measure one Restaurant candidate's native spawn, support and stability.

Two admission profiles share this worker.  Ground candidates (wheeled or
legged) settle under zero commands and are gated on filtered ground contact,
weight support and post-settle excursion.  Aerial candidates station-keep at
their frozen spawn anchor under the position controller and are gated on
altitude floor, anchor deviation and post-settle excursion instead of ground
contact.

This is an author-side admission diagnostic.  A completed diagnostic may reject
the candidate; it never promotes the Restaurant suite or its scoreable gate.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import traceback
import uuid

from . import config as scene_config
from . import contact_probe, runtime_probe


ROOT = Path(__file__).resolve().parents[2]
SETTLE_STEPS = 120
AERIAL_SETTLE_STEPS = 240
SAMPLE_STEPS = 240
MINIMUM_CONTACT_SAMPLE_RATIO = 0.80
MINIMUM_WEIGHT_SUPPORT_RATIO = 0.70
MAXIMUM_WEIGHT_SUPPORT_RATIO = 1.30
MAXIMUM_XY_EXCURSION_M = 0.05
MAXIMUM_Z_EXCURSION_M = 0.02
MAXIMUM_TILT_RAD = 0.10
SPAWN_BINDING_TOLERANCE_M = 0.0001
MINIMUM_HOVER_ALTITUDE_M = 1.0
MAXIMUM_ANCHOR_DEVIATION_M = 0.30
PROBE_SCOPE = "candidate_spawn_support_and_zero_command_stability_only"
AERIAL_PROBE_SCOPE = "candidate_spawn_hover_and_station_stability_only"

_M20_WHEELS = ("fl_wheel", "fr_wheel", "hl_wheel", "hr_wheel")
# Articulation body names follow demo.benchmark.vertical_traversal.FOOT_CONTACT_BODIES:
# go2 uses lowercase "foot" with RL/RR hind-leg prefixes, lite3 uses uppercase
# FOOT with HL/HR prefixes.  The probe still verifies each name resolves to
# exactly one articulation body at runtime before trusting it.
_GO2_FEET = ("FL_foot", "FR_foot", "RL_foot", "RR_foot")
_LITE3_FEET = ("FL_FOOT", "FR_FOOT", "HL_FOOT", "HR_FOOT")
_CANDIDATES = {
    "restaurant_m20_1": {
        "spawn_key": "m20_1",
        "robot_type": "m20",
        "controller_cfg": "M20_ROUGH_RSL_CFG",
        "runtime_agent": "m20_1",
        "support_bodies": list(_M20_WHEELS),
    },
    "restaurant_m20_2": {
        "spawn_key": "m20_2",
        "robot_type": "m20",
        "controller_cfg": "M20_ROUGH_RSL_CFG",
        "runtime_agent": "m20_1",
        "support_bodies": list(_M20_WHEELS),
    },
    "restaurant_go2_1": {
        "spawn_key": "go2_1",
        "robot_type": "go2",
        "controller_cfg": "GO2_VELOCITY_RSL_CFG",
        "runtime_agent": "go2_1",
        "support_bodies": list(_GO2_FEET),
    },
    "restaurant_lite3_1": {
        "spawn_key": "lite3_1",
        "robot_type": "lite3",
        "controller_cfg": "LITE3_VELOCITY_RSL_CFG",
        "runtime_agent": "lite3_1",
        "support_bodies": list(_LITE3_FEET),
    },
    "restaurant_iris_1": {
        "spawn_key": "iris_1",
        "robot_type": "iris",
        "controller_cfg": "PEGASUS_IRIS_POSITION_CFG",
        "runtime_agent": "iris_1",
        "support_bodies": [],
        "identity_bodies": ["body"],
        "profile": "aerial_hover",
    },
    "restaurant_iris_2": {
        "spawn_key": "iris_2",
        "robot_type": "iris",
        "controller_cfg": "PEGASUS_IRIS_POSITION_CFG",
        "runtime_agent": "iris_1",
        "support_bodies": [],
        "identity_bodies": ["body"],
        "profile": "aerial_hover",
    },
}

_CONTROLLER_SOURCE_PATHS = {
    "M20_ROUGH_RSL_CFG": "rl/m20_rough_rsl/m20_rough_rsl.py",
    "GO2_VELOCITY_RSL_CFG": "rl/go2_rsl_rl/go2_rsl_rl.py",
    "LITE3_VELOCITY_RSL_CFG": "rl/lite3_rsl_rl/lite3_rsl_rl.py",
    "PEGASUS_IRIS_POSITION_CFG": (
        "traditional/pegasus_multirotor/pegasus_multirotor.py"
    ),
}


def _is_aerial(spec: dict) -> bool:
    return spec.get("profile") == "aerial_hover"


def _candidate(candidate_id: str) -> dict:
    try:
        return _CANDIDATES[candidate_id]
    except KeyError as exc:
        raise ValueError(f"unsupported Restaurant candidate: {candidate_id}") from exc


def _controller_source(spec: dict) -> Path:
    root = os.environ.get("EAI_CONTROLLER_ROOT")
    base = (
        Path(root).expanduser().resolve()
        if root
        else ROOT / "source/EAI_assets/EAI_assets/controller"
    )
    try:
        relative = _CONTROLLER_SOURCE_PATHS[spec["controller_cfg"]]
    except KeyError as exc:
        raise ValueError("candidate controller source mapping is incomplete") from exc
    path = base / relative
    if not path.is_file():
        raise RuntimeError(
            f"{spec['controller_cfg']} controller source is unavailable; "
            "set EAI_CONTROLLER_ROOT"
        )
    return path.resolve()


def _source_files(candidate_id: str) -> dict[str, str]:
    spec = _candidate(candidate_id)
    paths = {
        **runtime_probe._probe_source_files(),
        str(Path(__file__).resolve()): runtime_probe._sha256(Path(__file__).resolve()),
        str(Path(contact_probe.__file__).resolve()): runtime_probe._sha256(
            Path(contact_probe.__file__).resolve()
        ),
    }
    controller = _controller_source(spec)
    paths[str(controller)] = runtime_probe._sha256(controller)
    return dict(sorted(paths.items()))


def _thresholds(candidate_id: str) -> dict:
    if _is_aerial(_candidate(candidate_id)):
        # No post-settle Z-excursion gate: a height-tracking controller
        # legitimately moves vertically toward the commanded anchor, and that
        # transient is bounded by the anchor-deviation gate plus the altitude
        # floor.  Measured on iris_1 v1: monotone convergence toward the goal
        # was misread as drift by the ground-calibrated Z limit.
        return {
            "settle_steps": AERIAL_SETTLE_STEPS,
            "sample_steps": SAMPLE_STEPS,
            "minimum_hover_altitude_m": MINIMUM_HOVER_ALTITUDE_M,
            "maximum_anchor_deviation_m": MAXIMUM_ANCHOR_DEVIATION_M,
            "maximum_xy_excursion_m": MAXIMUM_XY_EXCURSION_M,
            "maximum_tilt_rad": MAXIMUM_TILT_RAD,
            "spawn_binding_tolerance_m": SPAWN_BINDING_TOLERANCE_M,
        }
    return {
        "settle_steps": SETTLE_STEPS,
        "sample_steps": SAMPLE_STEPS,
        "minimum_contact_sample_ratio": MINIMUM_CONTACT_SAMPLE_RATIO,
        "median_weight_support_ratio": [
            MINIMUM_WEIGHT_SUPPORT_RATIO,
            MAXIMUM_WEIGHT_SUPPORT_RATIO,
        ],
        "maximum_xy_excursion_m": MAXIMUM_XY_EXCURSION_M,
        "maximum_z_excursion_m": MAXIMUM_Z_EXCURSION_M,
        "maximum_tilt_rad": MAXIMUM_TILT_RAD,
        "spawn_binding_tolerance_m": SPAWN_BINDING_TOLERANCE_M,
    }


def _probe_scope(candidate_id: str) -> str:
    if _is_aerial(_candidate(candidate_id)):
        return AERIAL_PROBE_SCOPE
    return PROBE_SCOPE


def _selection(candidate_id: str) -> dict:
    spec = _candidate(candidate_id)
    spawn = scene_config.BENCHMARK_ROBOT_SPAWN_XYZ_M[spec["spawn_key"]]
    return {
        "version": 1,
        "task_name": f"restaurant_candidate_admission_{candidate_id}",
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


def prepare_probe(
    output_root: str | Path,
    *,
    candidate_id: str,
    products_root: str | Path,
    source_usd: str | Path,
    device: str,
) -> dict:
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
    spec = _candidate(candidate_id)
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    products = Path(products_root).expanduser().resolve()
    source = Path(source_usd).expanduser().resolve()

    from tools.assets.build_restaurant_products import validate_products

    validation = validate_products(products, source_usd=source)
    metadata_path = products / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    request = {
        "schema": "eai.restaurant.candidate_admission_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "candidate_id": candidate_id,
        "candidate_spec": spec,
        "products_root": str(products),
        "source_usd": str(source),
        "source_sha256": runtime_probe._sha256(source),
        "metadata_sha256": runtime_probe._sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": validation,
        "source_files": _source_files(candidate_id),
        "selection": _selection(candidate_id),
        "requested_device": device,
        "thresholds": _thresholds(candidate_id),
        "release_gates": metadata["release_gates"],
        "execution_validated": False,
        "scoreable": False,
    }
    runtime_probe._write(output / "request.json", request)
    return request


def _validate_request_contract(request: dict) -> None:
    candidate_id = request.get("candidate_id")
    if (
        request.get("schema") != "eai.restaurant.candidate_admission_request"
        or request.get("version") != 1
        or not isinstance(request.get("run_id"), str)
        or re.fullmatch(r"[a-f0-9]{32}", request["run_id"]) is None
        or candidate_id not in _CANDIDATES
        or request.get("scene_key") != scene_config.BENCHMARK_SCENE_KEY
        or request.get("candidate_spec") != _candidate(candidate_id)
        or request.get("selection") != _selection(candidate_id)
        or request.get("requested_device") not in {"cpu", "cuda:0"}
        or request.get("thresholds") != _thresholds(candidate_id)
        or request.get("source_sha256") != scene_config.SOURCE_SHA256
        or request.get("product_validation", {}).get("validated") is not True
        or request.get("source_files") != _source_files(candidate_id)
        or request.get("release_gates")
        != {
            "navigation_ready": False,
            "execution_validated": False,
            "scoreable": False,
        }
        or request.get("execution_validated") is not False
        or request.get("scoreable") is not False
    ):
        raise ValueError("invalid Restaurant candidate admission request")


def _load_request(root: Path) -> dict:
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    _validate_request_contract(request)
    products = Path(request["products_root"]).resolve()
    source = Path(request["source_usd"]).resolve()
    if runtime_probe._sha256(source) != request["source_sha256"]:
        raise ValueError("Restaurant source changed after candidate preparation")
    metadata_path = products / "bench_meta.json"
    if runtime_probe._sha256(metadata_path) != request["metadata_sha256"]:
        raise ValueError("Restaurant metadata changed after candidate preparation")
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )

    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant bundle changed after candidate preparation")
    return request


def _tilt(state: list[float]) -> float:
    _w, x, y, _z = (float(value) for value in state[3:7])
    cosine = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
    return math.acos(cosine)


def run_worker(output_root: str | Path) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    spec = _candidate(request["candidate_id"])
    aerial = _is_aerial(spec)
    candidate_id = request["candidate_id"]
    report = {
        "schema": "eai.restaurant.candidate_admission_report",
        "version": 1,
        "run_id": request["run_id"],
        "scene_key": request["scene_key"],
        "candidate_id": candidate_id,
        "scope": _probe_scope(candidate_id),
        "request_sha256": runtime_probe._canonical_sha256(request),
        "source_files": request["source_files"],
        "status": "starting",
        "identity_validated": False,
        "spawn_binding_validated": False,
        "support_contact_validated": False if not aerial else None,
        "altitude_validated": None if not aerial else False,
        "hover_anchor_validated": None if not aerial else False,
        "zero_command_stability_validated": False if not aerial else None,
        "station_stability_validated": None if not aerial else False,
        "candidate_admitted": False,
        "execution_validated": False,
        "scoreable": False,
    }
    report_path = root / "candidate_admission_report.json"
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
            robot = session.base_env.scene.articulations[agent]
            body_names = list(robot.body_names)
            link_paths = [str(path) for path in robot.root_physx_view.link_paths[0]]
            support_bodies = tuple(spec["support_bodies"])
            identity_bodies = tuple(spec.get("identity_bodies", ()))
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
                raise RuntimeError("Restaurant candidate native identity mismatch")
            body_paths = {
                name: link_paths[body_names.index(name)]
                for name in support_bodies + identity_bodies
            }

            views = {}
            identities = {}
            physics_view = None
            if not aerial:
                import carb
                from isaacsim.core.simulation_manager import SimulationManager

                settings = carb.settings.get_settings()
                previous_disable_processing = settings.get_as_bool(
                    "/physics/disableContactProcessing"
                )
                settings.set_bool("/physics/disableContactProcessing", False)
                physics_view = SimulationManager.get_physics_sim_view()
                for name in support_bodies:
                    body_path = body_paths[name]
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

            expected_spawn = request["selection"]["robots"][0]["spawn_pose"][
                "position"
            ]
            if aerial:
                # The Pegasus position controller treats an all-zero command as
                # "hold the current pose", which would tolerate arbitrary drift.
                # The aerial gate therefore commands the frozen spawn anchor as
                # the station-keeping goal on every step.
                hover_goal = torch.tensor(
                    [expected_spawn],
                    device=session.device,
                    dtype=torch.float32,
                ).expand(session.num_envs, 3)

                def step_hold() -> None:
                    session.env.step(
                        {agent: hover_goal.clone()}
                    )

                step_command = step_hold
            else:
                def step_zero() -> None:
                    session.env.step(
                        {agent: torch.zeros((session.num_envs, 3), device=session.device)}
                    )

                step_command = step_zero

            initial = robot.data.root_state_w[0].detach().cpu().tolist()
            spawn_error = math.dist(initial[:3], expected_spawn)
            spawn_ok = spawn_error <= SPAWN_BINDING_TOLERANCE_M
            for _ in range(_thresholds(candidate_id)["settle_steps"]):
                step_command()
            reference = robot.data.root_state_w[0].detach().cpu().tolist()
            physics_dt = float(session.base_env.sim.get_physics_dt())
            masses = robot.root_physx_view.get_masses()[0].detach().cpu()
            contact_steps = {name: 0 for name in support_bodies}
            support_ratios = []
            samples = []
            max_xy = max_z = max_tilt = 0.0
            max_anchor_deviation = 0.0
            minimum_altitude = float("inf")
            buffer_reached = False
            for control_step in range(1, SAMPLE_STEPS + 1):
                step_command()
                state = robot.data.root_state_w[0].detach().cpu().tolist()
                xy = math.dist(state[:2], reference[:2])
                z = abs(float(state[2]) - float(reference[2]))
                tilt = _tilt(state)
                max_xy = max(max_xy, xy)
                max_z = max(max_z, z)
                max_tilt = max(max_tilt, tilt)
                sample_record = {
                    "control_step": control_step,
                    "simulation_time_s": float(session.base_env.sim.current_time),
                    "root_state_w": [float(value) for value in state],
                    "xy_excursion_m": xy,
                    "z_excursion_m": z,
                    "tilt_rad": tilt,
                }
                if aerial:
                    altitude = float(state[2])
                    minimum_altitude = min(minimum_altitude, altitude)
                    anchor_deviation = math.dist(state[:3], expected_spawn)
                    max_anchor_deviation = max(max_anchor_deviation, anchor_deviation)
                    sample_record["altitude_m"] = altitude
                    sample_record["anchor_deviation_m"] = anchor_deviation
                else:
                    wheels = {}
                    ground_force_n = 0.0
                    for name, view in views.items():
                        sample = contact_probe._sample_contact_view(
                            view, physics_dt=physics_dt
                        )
                        force_z = sample["ground_force_N"][2]
                        contact_steps[name] += int(
                            force_z > contact_probe.CONTACT_FORCE_THRESHOLD_N
                        )
                        buffer_reached = (
                            buffer_reached or sample["buffer_capacity_reached"]
                        )
                        ground_force_n += force_z
                        wheels[name] = sample
                    weight_n = float(masses.sum().item()) * 9.81
                    support_ratio = ground_force_n / weight_n
                    support_ratios.append(support_ratio)
                    sample_record["weight_support_ratio"] = support_ratio
                    sample_record["support_bodies"] = wheels
                samples.append(sample_record)
            final = robot.data.root_state_w[0].detach().cpu().tolist()
            stability_ok = (
                max_xy <= MAXIMUM_XY_EXCURSION_M
                and (aerial or max_z <= MAXIMUM_Z_EXCURSION_M)
                and max_tilt <= MAXIMUM_TILT_RAD
            )
            blockers = []
            if not spawn_ok:
                blockers.append("spawn_binding_failed")
            if aerial:
                altitude_ok = minimum_altitude >= MINIMUM_HOVER_ALTITUDE_M
                anchor_ok = max_anchor_deviation <= MAXIMUM_ANCHOR_DEVIATION_M
                if not altitude_ok:
                    blockers.append("altitude_floor_failed")
                if not anchor_ok:
                    blockers.append("hover_anchor_deviation_failed")
                if not stability_ok:
                    blockers.append("station_stability_failed")
                admitted = identity_ok and spawn_ok and altitude_ok and anchor_ok and stability_ok
                gate_fields = {
                    "minimum_observed_altitude_m": minimum_altitude,
                    "maximum_anchor_deviation_m": max_anchor_deviation,
                    "altitude_validated": altitude_ok,
                    "hover_anchor_validated": anchor_ok,
                    "station_stability_validated": stability_ok,
                }
            else:
                ratios = {
                    name: count / SAMPLE_STEPS for name, count in contact_steps.items()
                }
                median_support = statistics.median(support_ratios)
                contact_ok = (
                    not buffer_reached
                    and all(
                        ratio >= MINIMUM_CONTACT_SAMPLE_RATIO
                        for ratio in ratios.values()
                    )
                    and MINIMUM_WEIGHT_SUPPORT_RATIO
                    <= median_support
                    <= MAXIMUM_WEIGHT_SUPPORT_RATIO
                )
                if not contact_ok:
                    blockers.append("support_contact_failed")
                if not stability_ok:
                    blockers.append("zero_command_stability_failed")
                admitted = identity_ok and spawn_ok and contact_ok and stability_ok
                gate_fields = {
                    "support_contact_sample_ratios": ratios,
                    "median_weight_support_ratio": median_support,
                    "contact_buffer_capacity_reached": buffer_reached,
                    "support_contact_validated": contact_ok,
                    "zero_command_stability_validated": stability_ok,
                }
            report.update(
                status="diagnostic_completed",
                actual_device=str(session.device),
                runtime_agent=agent,
                native_spawn_xyz_m=initial[:3],
                expected_spawn_xyz_m=expected_spawn,
                spawn_binding_error_m=spawn_error,
                settled_reference_xyz_m=reference[:3],
                final_xyz_m=final[:3],
                settle_displacement_m=math.dist(initial[:3], reference[:3]),
                sample_displacement_m=math.dist(reference[:3], final[:3]),
                body_names=body_names,
                link_paths=link_paths,
                support_body_paths=body_paths,
                contact_view_identities=identities,
                ground_collision_filter=(
                    None if aerial else contact_probe.GROUND_COLLISION_PATH
                ),
                physics_dt_s=physics_dt,
                control_step_dt_s=float(session.base_env.step_dt),
                mass_kg=float(masses.sum().item()),
                samples=samples,
                maximum_xy_excursion_m=max_xy,
                maximum_z_excursion_m=max_z,
                maximum_tilt_rad=max_tilt,
                identity_validated=identity_ok,
                spawn_binding_validated=spawn_ok,
                candidate_admitted=admitted,
                admission_blockers=blockers,
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
    return report


def execute_probe(output_root: str | Path, *, timeout_s: float) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    lock_path = ROOT / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "demo.restaurant_benchmark.candidate_admission",
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
                f"Restaurant candidate probe failed with exit code "
                f"{completed.returncode}; see {root / 'isaac.log'}"
            )
    report = json.loads(
        (root / "candidate_admission_report.json").read_text(encoding="utf-8")
    )
    if (
        report.get("status") != "diagnostic_completed"
        or report.get("scope") != _probe_scope(request["candidate_id"])
        or report.get("request_sha256") != runtime_probe._canonical_sha256(request)
        or report.get("source_files") != _source_files(request["candidate_id"])
        or report.get("execution_validated") is not False
        or report.get("scoreable") is not False
        or not isinstance(report.get("candidate_admitted"), bool)
    ):
        raise RuntimeError("Restaurant candidate worker produced an invalid report")
    report["wall_time_s"] = time.monotonic() - started
    runtime_probe._write(root / "candidate_admission_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--candidate", choices=tuple(_CANDIDATES))
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        report = run_worker(args.output_root)
    else:
        if args.candidate is None or args.products_root is None or args.source_usd is None:
            parser.error("--candidate, --products-root and --source-usd are required")
        prepare_probe(
            args.output_root,
            candidate_id=args.candidate,
            products_root=args.products_root,
            source_usd=args.source_usd,
            device=args.device,
        )
        report = execute_probe(args.output_root, timeout_s=args.timeout_s)
    print(
        json.dumps(
            {
                "candidate_id": report.get("candidate_id"),
                "status": report.get("status"),
                "candidate_admitted": report.get("candidate_admitted"),
                "admission_blockers": report.get("admission_blockers", []),
                "execution_validated": False,
                "scoreable": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
