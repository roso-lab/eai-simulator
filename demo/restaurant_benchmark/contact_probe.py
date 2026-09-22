"""Measure Carter wheel contact and stability on the Restaurant candidate."""

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

from demo.benchmark.vertical_traversal import decode_foot_contact_buffer

from . import config as scene_config
from . import runtime_probe


ROOT = Path(__file__).resolve().parents[2]
WHEEL_NAMES = (
    "wheel_left",
    "wheel_right",
    "caster_wheel_left",
    "caster_wheel_right",
)
GROUND_COLLISION_PATH = "/World/RestaurantBenchmark/BenchCollision/zz_ground"
CONTACT_FORCE_THRESHOLD_N = 0.1
MINIMUM_CONTACT_SAMPLE_RATIO = 0.80
MINIMUM_WEIGHT_SUPPORT_RATIO = 0.70
MAXIMUM_WEIGHT_SUPPORT_RATIO = 1.30
MAXIMUM_XY_EXCURSION_M = 0.05
MAXIMUM_Z_EXCURSION_M = 0.02
MAXIMUM_TILT_RAD = 0.10
CONTACT_BUFFER_CAPACITY = 64
PROBE_SCOPE = (
    "single_carter_primary_spawn_static_collision_ground_contact_"
    "and_zero_command_stability_only"
)


def _expected_thresholds() -> dict:
    return {
        "contact_force_N": CONTACT_FORCE_THRESHOLD_N,
        "minimum_contact_sample_ratio": MINIMUM_CONTACT_SAMPLE_RATIO,
        "weight_support_ratio": [
            MINIMUM_WEIGHT_SUPPORT_RATIO,
            MAXIMUM_WEIGHT_SUPPORT_RATIO,
        ],
        "maximum_xy_excursion_m": MAXIMUM_XY_EXCURSION_M,
        "maximum_z_excursion_m": MAXIMUM_Z_EXCURSION_M,
        "maximum_tilt_rad": MAXIMUM_TILT_RAD,
        "contact_buffer_capacity": CONTACT_BUFFER_CAPACITY,
    }


def _source_files() -> dict[str, str]:
    paths = {
        **runtime_probe._probe_source_files(),
        str(Path(__file__).resolve()): runtime_probe._sha256(Path(__file__).resolve()),
    }
    return dict(sorted(paths.items()))


def prepare_probe(
    output_root: str | Path,
    *,
    products_root: str | Path,
    source_usd: str | Path,
    device: str,
    settle_steps: int,
    sample_steps: int,
) -> dict:
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
    if not 1 <= settle_steps <= 1200 or not 2 <= sample_steps <= 1200:
        raise ValueError("settle steps must be in [1,1200] and samples in [2,1200]")
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    products = Path(products_root).expanduser().resolve()
    source = Path(source_usd).expanduser().resolve()

    from tools.assets.build_restaurant_products import validate_products

    validation = validate_products(products, source_usd=source)
    metadata_path = products / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    request = {
        "schema": "eai.restaurant.native_contact_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "products_root": str(products),
        "source_usd": str(source),
        "source_sha256": runtime_probe._sha256(source),
        "metadata_sha256": runtime_probe._sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": validation,
        "selection": runtime_probe._selection(),
        "requested_device": device,
        "settle_steps": settle_steps,
        "sample_steps": sample_steps,
        "source_files": _source_files(),
        "thresholds": _expected_thresholds(),
        "release_gates": metadata["release_gates"],
        "scoreable": False,
    }
    runtime_probe._write(output / "request.json", request)
    return request


def _validate_request_contract(request: dict) -> None:
    run_id = request.get("run_id")
    try:
        parsed_run_id = uuid.UUID(hex=run_id) if isinstance(run_id, str) else None
    except ValueError:
        parsed_run_id = None
    settle_steps = request.get("settle_steps")
    sample_steps = request.get("sample_steps")
    if (
        request.get("schema") != "eai.restaurant.native_contact_request"
        or request.get("version") != 1
        or parsed_run_id is None
        or parsed_run_id.version != 4
        or parsed_run_id.hex != run_id
        or request.get("scene_key") != scene_config.BENCHMARK_SCENE_KEY
        or request.get("selection") != runtime_probe._selection()
        or request.get("requested_device") not in {"cpu", "cuda:0"}
        or request.get("source_sha256") != scene_config.SOURCE_SHA256
        or request.get("product_validation", {}).get("validated") is not True
        or request.get("source_files") != _source_files()
        or not isinstance(settle_steps, int)
        or isinstance(settle_steps, bool)
        or not 1 <= settle_steps <= 1200
        or not isinstance(sample_steps, int)
        or isinstance(sample_steps, bool)
        or not 2 <= sample_steps <= 1200
        or request.get("thresholds") != _expected_thresholds()
        or request.get("scoreable") is not False
        or request.get("release_gates")
        != {"navigation_ready": False, "execution_validated": False, "scoreable": False}
    ):
        raise ValueError("invalid Restaurant native contact request")


def _load_request(root: Path) -> dict:
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    _validate_request_contract(request)
    products = Path(request["products_root"]).resolve()
    source = Path(request["source_usd"]).resolve()
    if runtime_probe._sha256(source) != request["source_sha256"]:
        raise ValueError("Restaurant source changed after contact preparation")
    metadata_path = products / "bench_meta.json"
    if runtime_probe._sha256(metadata_path) != request["metadata_sha256"]:
        raise ValueError("Restaurant metadata changed after contact preparation")
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )

    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant bundle changed after contact preparation")
    return request


def _vector(value) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach().cpu().reshape(-1).tolist()
    return [float(item) for item in value]


def _to_host(value):
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return value


def _finite_vector(value, *, label: str) -> list[float]:
    result = _vector(value)
    if len(result) != 3 or not all(math.isfinite(item) for item in result):
        raise RuntimeError(f"{label} must contain three finite values")
    return result


def _decode_contact_data(raw) -> dict:
    decoded = decode_foot_contact_buffer(
        raw,
        collision_paths=[GROUND_COLLISION_PATH],
        capacity=CONTACT_BUFFER_CAPACITY,
    )
    if decoded.get("status") != "observed":
        raise RuntimeError(
            f"invalid Carter wheel contact buffer: {decoded.get('reason', 'unknown')}"
        )
    counts, starts = raw[4], raw[5]
    count = counts[0][0]
    start = starts[0][0] if count else None
    return {
        "contact_count": count,
        "contact_start": start,
        "buffer_capacity": CONTACT_BUFFER_CAPACITY,
        "buffer_capacity_reached": decoded["buffer_saturated"],
        "contacts": decoded["contacts"],
    }


def _sample_contact_view(view, *, physics_dt: float) -> dict:
    raw = tuple(_to_host(value) for value in view.get_contact_data(dt=physics_dt))
    decoded = _decode_contact_data(raw)
    matrix = view.get_contact_force_matrix(dt=physics_dt)
    filtered_force = _finite_vector(
        matrix.detach().cpu().reshape(-1, 3).sum(dim=0), label="filtered force"
    )
    net = view.get_net_contact_forces(dt=physics_dt)
    net_force = _finite_vector(
        net.detach().cpu().reshape(-1, 3).sum(dim=0), label="net contact force"
    )
    return {
        **decoded,
        "ground_force_N": filtered_force,
        "net_contact_force_N": net_force,
    }


def run_worker(output_root: str | Path) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    report = {
        "schema": "eai.restaurant.native_contact_probe",
        "version": 1,
        "run_id": request["run_id"],
        "scene_key": request["scene_key"],
        "scope": PROBE_SCOPE,
        "request_sha256": runtime_probe._canonical_sha256(request),
        "source_files": request["source_files"],
        "status": "starting",
        "static_collision_probe_validated": False,
        "wheel_contact_validated": False,
        "spawn_contact_physics_validated": False,
        "physics_scene_validated": False,
        "navigation_validated": False,
        "business_episode_execution_validated": False,
        "scoreable": False,
    }
    report_path = root / "contact_probe_report.json"
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
            import omni.physx
            import torch
            from isaacsim.core.simulation_manager import SimulationManager

            agent = session.possible_agents[0]
            articulation = session.base_env.scene.articulations[agent]
            body_names = list(articulation.body_names)
            missing = [name for name in WHEEL_NAMES if name not in body_names]
            if missing:
                raise RuntimeError(f"Carter wheel bodies are missing: {missing}")
            link_paths = articulation.root_physx_view.link_paths
            if len(link_paths) != 1:
                raise RuntimeError("contact probe requires one Carter articulation")
            body_paths = {
                name: str(link_paths[0][body_names.index(name)]) for name in WHEEL_NAMES
            }

            settings = carb.settings.get_settings()
            previous_disable_processing = settings.get_as_bool(
                "/physics/disableContactProcessing"
            )
            settings.set_bool("/physics/disableContactProcessing", False)
            physics_view = SimulationManager.get_physics_sim_view()
            views = {}
            contact_view_identities = {}
            for name, body_path in body_paths.items():
                view = physics_view.create_rigid_contact_view(
                    body_path,
                    filter_patterns=[GROUND_COLLISION_PATH],
                    max_contact_data_count=CONTACT_BUFFER_CAPACITY,
                )
                filters = list(view.filter_paths)
                if len(filters) == 1 and isinstance(filters[0], (list, tuple)):
                    filters = list(filters[0])
                if (
                    view.sensor_count != 1
                    or list(view.sensor_paths) != [body_path]
                    or view.filter_count != 1
                    or filters != [GROUND_COLLISION_PATH]
                    or view.max_contact_data_count != CONTACT_BUFFER_CAPACITY
                ):
                    raise RuntimeError(f"contact view identity mismatch for {name}")
                views[name] = view
                contact_view_identities[name] = {
                    "sensor_paths": list(view.sensor_paths),
                    "filter_paths": filters,
                    "max_contact_data_count": view.max_contact_data_count,
                }

            def step_zero() -> None:
                session.env.step(
                    {agent: torch.zeros((session.num_envs, 3), device=session.device)}
                )

            for _ in range(request["settle_steps"]):
                step_zero()
            reference = articulation.data.root_state_w[0].detach().cpu().tolist()
            physics_dt = float(session.base_env.sim.get_physics_dt())
            step_dt = float(session.base_env.step_dt)
            masses = articulation.root_physx_view.get_masses()[0].detach().cpu()
            weight_N = float(masses.sum().item()) * 9.81
            samples = []
            max_xy = 0.0
            max_z = 0.0
            max_tilt = 0.0
            contact_steps = {name: 0 for name in WHEEL_NAMES}
            support_ratios = []
            buffer_reached = False
            for index in range(1, request["sample_steps"] + 1):
                step_zero()
                state = articulation.data.root_state_w[0].detach().cpu().tolist()
                xy = math.dist(state[:2], reference[:2])
                z = abs(float(state[2]) - float(reference[2]))
                w, x, y, _z = (float(item) for item in state[3:7])
                vertical_cosine = max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y)))
                tilt = math.acos(vertical_cosine)
                max_xy = max(max_xy, xy)
                max_z = max(max_z, z)
                max_tilt = max(max_tilt, tilt)
                wheels = {}
                total_ground_z = 0.0
                for name, view in views.items():
                    wheel = _sample_contact_view(view, physics_dt=physics_dt)
                    wheels[name] = wheel
                    total_ground_z += wheel["ground_force_N"][2]
                    if wheel["ground_force_N"][2] > CONTACT_FORCE_THRESHOLD_N:
                        contact_steps[name] += 1
                    buffer_reached = buffer_reached or wheel["buffer_capacity_reached"]
                support_ratio = total_ground_z / weight_N
                support_ratios.append(support_ratio)
                samples.append(
                    {
                        "control_step": index,
                        "simulation_time_s": float(session.base_env.sim.current_time),
                        "root_state_w": [float(item) for item in state],
                        "xy_excursion_m": xy,
                        "z_excursion_m": z,
                        "tilt_rad": tilt,
                        "weight_support_ratio": support_ratio,
                        "wheels": wheels,
                    }
                )

            query = omni.physx.get_physx_scene_query_interface()
            support_hit = runtime_probe._hit_record(
                query.raycast_closest(
                    runtime_probe.SUPPORT_SAMPLE_XYZ_M, (0.0, 0.0, -1.0), 2.0
                )
            )
            obstacle_hit = runtime_probe._hit_record(
                query.raycast_closest(
                    runtime_probe.HORIZONTAL_RAY_ORIGIN_M,
                    runtime_probe.HORIZONTAL_RAY_DIRECTION,
                    runtime_probe.HORIZONTAL_RAY_DISTANCE_M,
                )
            )
            static_ok = (
                support_hit.get("hit") is True
                and support_hit.get("collision") == GROUND_COLLISION_PATH
                and abs(support_hit.get("position_m", [0.0, 0.0, 99.0])[2]) <= 0.02
                and support_hit.get("normal", [0.0, 0.0, 0.0])[2] >= 0.99
                and obstacle_hit.get("hit") is True
                and "/World/RestaurantBenchmark/BenchCollision/ground_obstacles/"
                in obstacle_hit.get("collision", "")
            )
            ratios = {
                name: contact_steps[name] / request["sample_steps"] for name in WHEEL_NAMES
            }
            sorted_support = sorted(support_ratios)
            median_support = sorted_support[len(sorted_support) // 2]
            stability_ok = (
                max_xy <= MAXIMUM_XY_EXCURSION_M
                and max_z <= MAXIMUM_Z_EXCURSION_M
                and max_tilt <= MAXIMUM_TILT_RAD
            )
            contact_ok = (
                not buffer_reached
                and all(value >= MINIMUM_CONTACT_SAMPLE_RATIO for value in ratios.values())
                and MINIMUM_WEIGHT_SUPPORT_RATIO
                <= median_support
                <= MAXIMUM_WEIGHT_SUPPORT_RATIO
            )
            report.update(
                status="diagnostic_completed",
                actual_device=str(session.device),
                physics_dt_s=physics_dt,
                control_step_dt_s=step_dt,
                body_names=body_names,
                wheel_body_paths=body_paths,
                contact_view_identities=contact_view_identities,
                ground_collision_filter=GROUND_COLLISION_PATH,
                mass_kg=float(masses.sum().item()),
                weight_N=weight_N,
                samples=samples,
                maximum_xy_excursion_m=max_xy,
                maximum_z_excursion_m=max_z,
                maximum_tilt_rad=max_tilt,
                contact_sample_ratios=ratios,
                median_weight_support_ratio=median_support,
                contact_buffer_capacity_reached=buffer_reached,
                stability_validated=stability_ok,
                support_raycast={**support_hit, "passed": static_ok},
                horizontal_obstacle_raycast={**obstacle_hit, "passed": static_ok},
                static_collision_probe_validated=static_ok,
                wheel_contact_validated=contact_ok,
                spawn_contact_physics_validated=(
                    static_ok and contact_ok and stability_ok
                ),
                physics_scene_validated=False,
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
    if not report["spawn_contact_physics_validated"]:
        raise RuntimeError("Restaurant contact and stability checks did not pass")
    return report


def execute_probe(output_root: str | Path, *, timeout_s: float) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    lock_path = ROOT / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "demo.restaurant_benchmark.contact_probe",
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
                f"Restaurant contact probe failed with exit code {completed.returncode}; "
                f"see {root / 'isaac.log'}"
            )
    report = json.loads((root / "contact_probe_report.json").read_text(encoding="utf-8"))
    if (
        report.get("status") != "diagnostic_completed"
        or report.get("scope") != PROBE_SCOPE
        or report.get("spawn_contact_physics_validated") is not True
        or report.get("physics_scene_validated") is not False
        or report.get("request_sha256") != runtime_probe._canonical_sha256(request)
        or report.get("source_files") != _source_files()
    ):
        raise RuntimeError("Restaurant contact worker did not produce a passing report")
    report["wall_time_s"] = time.monotonic() - started
    runtime_probe._write(root / "contact_probe_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--settle-steps", type=int, default=120)
    parser.add_argument("--sample-steps", type=int, default=240)
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        report = run_worker(args.output_root)
    else:
        if args.products_root is None or args.source_usd is None:
            parser.error("--products-root and --source-usd are required")
        prepare_probe(
            args.output_root,
            products_root=args.products_root,
            source_usd=args.source_usd,
            device=args.device,
            settle_steps=args.settle_steps,
            sample_steps=args.sample_steps,
        )
        report = execute_probe(args.output_root, timeout_s=args.timeout_s)
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
