"""Run bounded native physics checks against the local Restaurant candidate."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
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


ROOT = Path(__file__).resolve().parents[2]
SUPPORT_SAMPLE_XYZ_M = (19.875, 21.275, 1.5)
HORIZONTAL_RAY_ORIGIN_M = (3.275, 1.025, 0.5)
HORIZONTAL_RAY_DIRECTION = (1.0, 0.0, 0.0)
HORIZONTAL_RAY_DISTANCE_M = 1.2
STATIONARY_DISPLACEMENT_LIMIT_M = 0.05
STATIONARY_VERTICAL_DRIFT_LIMIT_M = 0.02


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _probe_source_files() -> dict[str, str]:
    paths = (
        Path(__file__).resolve(),
        Path(scene_config.__file__).resolve(),
        ROOT / "simulator.py",
        ROOT / "tools/assets/build_restaurant_products.py",
        ROOT / "source/EAI/EAI/hmrs_env/env_diy/catalog.py",
        ROOT / "source/EAI_assets/EAI_assets/asset_requirements.py",
        ROOT / "source/EAI_assets/EAI_assets/restaurant_benchmark_products.py",
        ROOT / "source/EAI_assets/EAI_assets/scene/restaurant_benchmark.py",
        ROOT / "source/EAI_hmrs/EAI_hmrs/env_builder.py",
    )
    return {str(path): _sha256(path) for path in paths}


def _selection() -> dict:
    return {
        "version": 1,
        "task_name": "restaurant_benchmark_native_probe",
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "robots": [
            {
                "type": "carter",
                "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"},
                "attachments": [],
            }
        ],
    }


def prepare_probe(
    output_root: str | Path,
    *,
    products_root: str | Path,
    source_usd: str | Path,
    device: str,
    settle_steps: int,
    observe_steps: int,
) -> dict:
    if device not in {"cpu", "cuda:0"}:
        raise ValueError("device must be cpu or cuda:0")
    if not 1 <= settle_steps <= 1200 or not 1 <= observe_steps <= 1200:
        raise ValueError("settle and observation steps must be in [1, 1200]")
    output = Path(output_root).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    products = Path(products_root).expanduser().resolve()
    source = Path(source_usd).expanduser().resolve()

    # Standalone OpenUSD validation belongs in the parent, before Kit starts.
    from tools.assets.build_restaurant_products import validate_products

    validation = validate_products(products, source_usd=source)
    metadata_path = products / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    request = {
        "schema": "eai.restaurant.native_runtime_request",
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "scene_key": scene_config.BENCHMARK_SCENE_KEY,
        "products_root": str(products),
        "source_usd": str(source),
        "source_sha256": _sha256(source),
        "metadata_sha256": _sha256(metadata_path),
        "bundle_manifest_sha256": metadata["bundle_manifest_sha256"],
        "product_validation": validation,
        "source_files": _probe_source_files(),
        "selection": _selection(),
        "requested_device": device,
        "settle_steps": settle_steps,
        "observe_steps": observe_steps,
        "release_gates": metadata["release_gates"],
        "scoreable": False,
    }
    _write(output / "request.json", request)
    return request


def _load_request(root: Path) -> dict:
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    if (
        request.get("schema") != "eai.restaurant.native_runtime_request"
        or request.get("version") != 1
        or not isinstance(request.get("run_id"), str)
        or re.fullmatch(r"[a-f0-9]{32}", request["run_id"]) is None
        or request.get("scene_key") != scene_config.BENCHMARK_SCENE_KEY
        or request.get("selection") != _selection()
        or request.get("requested_device") not in {"cpu", "cuda:0"}
        or not isinstance(request.get("settle_steps"), int)
        or not 1 <= request["settle_steps"] <= 1200
        or not isinstance(request.get("observe_steps"), int)
        or not 1 <= request["observe_steps"] <= 1200
        or request.get("source_sha256") != scene_config.SOURCE_SHA256
        or request.get("product_validation", {}).get("validated") is not True
        or request.get("source_files") != _probe_source_files()
        or request.get("scoreable") is not False
        or request.get("release_gates")
        != {"navigation_ready": False, "execution_validated": False, "scoreable": False}
    ):
        raise ValueError("invalid Restaurant native runtime request")
    products = Path(request["products_root"]).resolve()
    source = Path(request["source_usd"]).resolve()
    if _sha256(source) != request["source_sha256"]:
        raise ValueError("Restaurant source changed after probe preparation")
    metadata_path = products / "bench_meta.json"
    if _sha256(metadata_path) != request["metadata_sha256"]:
        raise ValueError("Restaurant candidate metadata changed after preparation")
    from EAI_assets.restaurant_benchmark_products import (
        resolve_restaurant_benchmark_products,
    )

    resolved = resolve_restaurant_benchmark_products(products)
    if resolved.bundle_manifest_sha256 != request["bundle_manifest_sha256"]:
        raise ValueError("Restaurant candidate bundle changed after preparation")
    return request


def _hit_record(hit) -> dict:
    if not isinstance(hit, dict):
        return {"hit": False, "raw_type": type(hit).__name__}
    row = {"hit": hit.get("hit") is True}
    for key in ("position", "normal"):
        value = hit.get(key)
        if value is not None:
            output_key = "position_m" if key == "position" else key
            row[output_key] = [float(item) for item in value]
    if hit.get("distance") is not None:
        row["distance_m"] = float(hit["distance"])
    collision = hit.get("collision")
    row["collision"] = collision if isinstance(collision, str) else str(collision)
    row["raw_proto_index"] = hit.get("protoIndex")
    return row


def run_worker(output_root: str | Path) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    os.environ["EAI_RESTAURANT_BENCHMARK_ROOT"] = request["products_root"]
    report = {
        "schema": "eai.restaurant.native_runtime_probe",
        "version": 1,
        "run_id": request["run_id"],
        "scene_key": request["scene_key"],
        "request_sha256": _canonical_sha256(request),
        "source_files": request["source_files"],
        "status": "starting",
        "static_collision_probe_validated": False,
        "physics_scene_validated": False,
        "wheel_contact_validated": False,
        "navigation_validated": False,
        "business_episode_execution_validated": False,
        "scoreable": False,
    }
    report_path = root / "runtime_probe_report.json"
    _write(report_path, report)

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
    try:
        with open_simulator_session(launch) as session:
            import omni.physx
            import omni.usd
            import torch
            from pxr import Usd, UsdPhysics

            stage = omni.usd.get_context().get_stage()
            scene = stage.GetPrimAtPath("/World/RestaurantBenchmark")
            if not scene or not scene.IsValid():
                raise RuntimeError("Restaurant benchmark scene prim is absent")
            collisions = [
                str(prim.GetPath())
                for prim in Usd.PrimRange(scene)
                if prim.HasAPI(UsdPhysics.CollisionAPI)
                and UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is True
            ]
            expected_ground = "/World/RestaurantBenchmark/BenchCollision/zz_ground"
            if expected_ground not in collisions:
                raise RuntimeError("Restaurant benchmark ground collider is absent")

            agent = session.possible_agents[0]
            articulation = session.base_env.scene.articulations[agent]
            spawn = articulation.data.root_pos_w[0].detach().cpu().tolist()

            def step_zero(count: int) -> None:
                for _ in range(count):
                    session.env.step(
                        {agent: torch.zeros((session.num_envs, 3), device=session.device)}
                    )

            step_zero(request["settle_steps"])
            stationary_start = articulation.data.root_pos_w[0].detach().cpu().tolist()
            step_zero(request["observe_steps"])
            stationary_end = articulation.data.root_pos_w[0].detach().cpu().tolist()
            displacement = math.dist(stationary_start, stationary_end)
            vertical_drift = float(stationary_end[2]) - float(stationary_start[2])

            query = omni.physx.get_physx_scene_query_interface()
            support_hit = _hit_record(
                query.raycast_closest(SUPPORT_SAMPLE_XYZ_M, (0.0, 0.0, -1.0), 2.0)
            )
            obstacle_hit = _hit_record(
                query.raycast_closest(
                    HORIZONTAL_RAY_ORIGIN_M,
                    HORIZONTAL_RAY_DIRECTION,
                    HORIZONTAL_RAY_DISTANCE_M,
                )
            )
            support_ok = (
                support_hit.get("hit") is True
                and support_hit.get("collision") == expected_ground
                and abs(support_hit.get("position_m", [0.0, 0.0, 99.0])[2]) <= 0.02
                and support_hit.get("normal", [0.0, 0.0, 0.0])[2] >= 0.99
            )
            obstacle_ok = (
                obstacle_hit.get("hit") is True
                and "/World/RestaurantBenchmark/BenchCollision/ground_obstacles/"
                in obstacle_hit.get("collision", "")
                and 0.8 <= obstacle_hit.get("distance_m", math.inf) <= 1.2
            )
            stationary_ok = (
                displacement <= STATIONARY_DISPLACEMENT_LIMIT_M
                and abs(vertical_drift) <= STATIONARY_VERTICAL_DRIFT_LIMIT_M
            )
            report.update(
                status="diagnostic_completed",
                actual_device=str(session.device),
                physics_steps=int(session.base_env.sim.current_time_step_index),
                scene_prim_path=str(scene.GetPath()),
                collision_prim_count=len(collisions),
                support_raycast={**support_hit, "passed": support_ok},
                horizontal_obstacle_raycast={**obstacle_hit, "passed": obstacle_ok},
                robot_stationary_observation={
                    "agent_id": agent,
                    "spawn_root_position_m": [float(item) for item in spawn],
                    "start_root_position_m": [float(item) for item in stationary_start],
                    "end_root_position_m": [float(item) for item in stationary_end],
                    "displacement_m": displacement,
                    "vertical_drift_m": vertical_drift,
                    "displacement_limit_m": STATIONARY_DISPLACEMENT_LIMIT_M,
                    "vertical_drift_limit_m": STATIONARY_VERTICAL_DRIFT_LIMIT_M,
                    "passed": stationary_ok,
                },
                static_collision_probe_validated=(
                    support_ok and obstacle_ok and stationary_ok
                ),
            )
            _write(report_path, report)
    except BaseException as exc:
        report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
        _write(report_path, report)
        raise
    if not report["static_collision_probe_validated"]:
        raise RuntimeError("Restaurant native static collision checks did not pass")
    return report


def execute_probe(output_root: str | Path, *, timeout_s: float = 900.0) -> dict:
    root = Path(output_root).expanduser().resolve()
    request = _load_request(root)
    lock_path = Path(__file__).resolve().parents[2] / "tmp/restaurant_benchmark/native.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "demo.restaurant_benchmark.runtime_probe",
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
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Restaurant native probe failed with exit code {completed.returncode}; "
                f"see {root / 'isaac.log'}"
            )
    report = json.loads((root / "runtime_probe_report.json").read_text(encoding="utf-8"))
    if (
        report.get("status") != "diagnostic_completed"
        or report.get("static_collision_probe_validated") is not True
        or report.get("request_sha256") != _canonical_sha256(request)
        or report.get("source_files") != _probe_source_files()
    ):
        raise RuntimeError("Restaurant native worker did not produce a passing terminal report")
    report["wall_time_s"] = time.monotonic() - started
    _write(root / "runtime_probe_report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--products-root", type=Path)
    parser.add_argument("--source-usd", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--settle-steps", type=int, default=120)
    parser.add_argument("--observe-steps", type=int, default=240)
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
            observe_steps=args.observe_steps,
        )
        report = execute_probe(args.output_root, timeout_s=args.timeout_s)
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
