"""Bind execution evidence to pre-launch inputs and pre-control runtime files."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
PROVENANCE_SCHEMA = "benchmark_execution_v1"
RUNTIME_DEFAULTS = {
    "steps": 0, "speed": 0.55, "k_turn": 1.5, "waypoint_tol": 0.35,
    "path_waypoint_tol": 0.55, "stall_window": 150, "max_replans": 12,
    "transport_mode": "none", "aerial_motion_mode": "controller",
    "device": "cpu", "gui": False,
    "camera_mode": "active", "follow": "", "wall_timeout_s": 0.0,
    "render_period_s": 0.0,
    "archive_camera_frames": False,
    "inventory_label_id": None, "inventory_label_state": "visible",
    "inventory_dispatch_mode": "none", "inventory_message_delay_s": 60.0,
    "inventory_dispatch_policy": "immediate", "inventory_dispatch_wait_s": 65.0,
}
_NAMED_SOURCES = {
    "executor_sha256": "tools/scenario_generation/execute_episode.py",
    "batch_executor_sha256": "tools/scenario_generation/batch_execute.py",
    "demo_runtime_sha256": "demo/benchmark/runtime.py",
    "demo_control_sha256": "demo/benchmark/control.py",
    "demo_scheduling_sha256": "demo/benchmark/scheduling.py",
}
_SOURCE_TREES = (
    "demo/benchmark", "demo/factory_benchmark", "demo/warehouse_benchmark",
    "demo/modern_house_benchmark", "source/EAI/EAI/scenario_spec",
    "source/EAI/EAI/controllers", "source/EAI/EAI/hmrs_ros",
    "source/EAI_assets/EAI_assets/robots", "source/EAI_assets/EAI_assets/sensor",
)
_SOURCE_FILES = (
    "simulator.py", "source/EAI_hmrs/EAI_hmrs/env_builder.py",
    "source/EAI_hmrs/EAI_hmrs/scenario_builder.py",
    "source/EAI_hmrs/EAI_hmrs/controller_loader.py",
    "source/EAI/EAI/hmrs_env/multi_robot_direct_env.py",
    "source/EAI/EAI/hmrs_env/env_diy/catalog.py",
    "tools/scenario_generation/common.py", "tools/scenario_generation/runtime_evidence.py",
    "tools/scenario_generation/benchmark_protocol.py",
    "tools/scenario_generation/benchmark_mass_audit.py",
    "tools/scenario_generation/capability_profile.py", "tools/assets/usd_closure.py",
    "source/EAI_assets/EAI_assets/asset_resolver.py",
    "source/EAI_assets/EAI_assets/scenario_assets.py",
)


def file_sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def execution_code_provenance():
    files = set(_SOURCE_FILES)
    for directory in _SOURCE_TREES:
        files.update(path.relative_to(REPO_ROOT).as_posix()
                     for path in (REPO_ROOT / directory).rglob("*.py"))
    return {
        **{key: file_sha256(REPO_ROOT / value) for key, value in _NAMED_SOURCES.items()},
        **{path: file_sha256(REPO_ROOT / path) for path in sorted(files)},
    }


def runtime_options(**overrides):
    unknown = overrides.keys() - RUNTIME_DEFAULTS.keys()
    if unknown:
        raise ValueError(f"unknown runtime options: {sorted(unknown)}")
    options = {**RUNTIME_DEFAULTS, **overrides}
    canonical_sha256(options)
    return options


def capture_request(scenario_path, options):
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "scenario_sha256": file_sha256(scenario_path),
        "code_provenance": execution_code_provenance(),
        "options": runtime_options(**options),
    }
    return {**payload, "sha256": canonical_sha256(payload),
            "captured_at": datetime.now(timezone.utc).isoformat()}


def _content_hash(snapshot):
    return canonical_sha256({key: value for key, value in snapshot.items()
                             if key not in {"sha256", "captured_at"}})


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("execution provenance requires timestamps")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("execution provenance timestamps must include a timezone")
    return timestamp


def capture_runtime(session, occupancy, plan, *, input_file_hashes=None):
    from EAI_assets.asset_resolver import (
        collect_controller_asset_paths, collect_usd_asset_paths, controller_root,
    )
    from tools.assets.usd_closure import collect_closure
    import yaml

    roots = collect_usd_asset_paths(session.env_cfg)
    files, _, edges = collect_closure(roots)
    files.update(Path(path).resolve() for path in collect_controller_asset_paths(session.env_cfg))
    for module in tuple(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if (path and Path(path).suffix == ".py"
                and (Path(path).is_relative_to(REPO_ROOT) or Path(path).is_relative_to(controller_root()))):
            files.add(Path(path).resolve())
    map_path = occupancy.path.resolve()
    map_config = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    files.update({map_path, (map_path.parent / map_config["image"]).resolve()})
    input_file_hashes = dict(input_file_hashes or {})
    files.update(Path(path).resolve() for path in input_file_hashes)
    missing = [{"owner": str(edge["owner"]), "asset": edge["authored"]}
               for edge in edges if edge["status"] == "missing"]
    versions = {"python": sys.version}
    if "cv2" in sys.modules:
        versions["opencv_runtime"] = sys.modules["cv2"].__version__
    for distribution in ("isaacsim", "isaaclab", "torch", "numpy"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "unknown"
    file_hashes = {str(path): file_sha256(path) for path in sorted(files)}
    for path, expected in input_file_hashes.items():
        if file_hashes.get(str(Path(path).resolve())) != expected:
            missing.append({"owner": "loaded_runtime_input", "asset": str(path),
                            "reason": "file_changed_between_load_and_capture"})
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "files": file_hashes,
        "input_file_hashes": input_file_hashes,
        "usd_roots": roots, "missing_dependencies": missing,
        "runtime_modules": sorted({edge["authored"] for edge in edges if edge["status"] == "runtime"}),
        "occupancy_sha256": occupancy.sha256, "plan_sha256": canonical_sha256(plan),
        "effective": {
            "device": str(session.device), "physics_dt_s": float(session.env_cfg.sim.dt),
            "control_dt_s": float(session.base_env.step_dt),
            "render_period_s": float(session.env_cfg.sim.dt) * session.env_cfg.sim.render_interval,
            "decimation": session.env_cfg.decimation, "versions": versions,
        },
        "complete": bool(roots) and not missing,
        "limitations": [
            "Captured before task control, after simulator startup and reset",
            "Source/configuration and local file identity do not certify physical capability",
            "Runtime MDL modules, driver binaries and GPU nondeterminism are not content-addressed",
        ],
    }
    return {**payload, "sha256": canonical_sha256(payload),
            "captured_at": datetime.now(timezone.utc).isoformat()}


def changed_runtime_files(snapshot):
    changed = []
    for path, digest in snapshot.get("files", {}).items():
        try:
            matches = file_sha256(path) == digest
        except OSError:
            matches = False
        if not matches:
            changed.append(path)
    return changed


def finish_provenance(request, runtime, *, scenario_path):
    violations = []
    if not Path(scenario_path).is_file() or request["scenario_sha256"] != file_sha256(scenario_path):
        violations.append("scenario_changed_during_execution")
    if request["code_provenance"] != execution_code_provenance():
        violations.append("source_changed_during_execution")
    if not runtime["complete"]:
        violations.append("runtime_dependencies_incomplete")
    violations.extend(f"runtime_file_changed:{path}" for path in changed_runtime_files(runtime))
    return {"schema": PROVENANCE_SCHEMA, "request": deepcopy(request), "runtime": deepcopy(runtime),
            "validated": not violations, "violations": violations}


def validate_execution_provenance(attempt, *, expected_sources=None, expected_options=None,
                                  check_runtime_files=False):
    report = attempt.get("report_body")
    config = attempt.get("config")
    if not isinstance(report, dict) or not isinstance(config, dict):
        raise ValueError("execution provenance requires a report and pre-launch config")
    provenance = report.get("execution_provenance")
    if (not isinstance(provenance, dict) or provenance.get("schema") != PROVENANCE_SCHEMA
            or provenance.get("validated") is not True or provenance.get("violations") != []):
        raise ValueError("execution provenance is missing, incomplete or changed during execution")
    request, runtime = provenance.get("request"), provenance.get("runtime")
    for snapshot in (request, runtime):
        if (not isinstance(snapshot, dict) or snapshot.get("schema") != PROVENANCE_SCHEMA
                or snapshot.get("sha256") != _content_hash(snapshot)):
            raise ValueError("execution provenance snapshot digest mismatch")
    if not (_timestamp(request.get("captured_at")) <= _timestamp(runtime.get("captured_at"))
            <= _timestamp(attempt.get("attempted_at"))):
        raise ValueError("execution provenance timestamps are out of order")
    if (config.get("request") != request or config.get("code_provenance") != request.get("code_provenance")
            or attempt.get("scenario_sha256") != request.get("scenario_sha256")):
        raise ValueError("execution provenance does not match pre-launch inputs")
    if (runtime.get("complete") is not True or not runtime.get("files") or not runtime.get("usd_roots")
            or runtime.get("missing_dependencies") != []):
        raise ValueError("execution provenance has incomplete runtime files")
    if not isinstance(runtime["files"], dict) or any(
        not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        for path, digest in runtime["files"].items()
    ):
        raise ValueError("execution provenance has malformed runtime file digests")
    if not isinstance(request.get("options"), dict) or set(request["options"]) != set(RUNTIME_DEFAULTS):
        raise ValueError("execution provenance has incomplete runtime options")
    sources = execution_code_provenance() if expected_sources is None else expected_sources
    if request.get("code_provenance") != sources:
        raise ValueError("execution provenance source version differs from expected sources")
    if expected_options is not None and request["options"] != runtime_options(**expected_options):
        raise ValueError("execution provenance runtime options differ from requested conditions")
    if config.get("transport_mode") != request["options"]["transport_mode"]:
        raise ValueError("execution provenance transport mode differs from batch config")
    expected_steps = request["options"]["steps"] or "auto"
    if (config.get("steps", expected_steps) != expected_steps
            or config.get("device", request["options"]["device"]) != request["options"]["device"]):
        raise ValueError("execution provenance requested steps or device differ from batch config")
    effective = runtime.get("effective", {})
    rendering = report.get("rendering", {})
    if not isinstance(effective, dict) or not isinstance(rendering, dict) or any(
        isinstance(effective.get(key), bool) or not isinstance(effective.get(key), (int, float))
        or not math.isfinite(effective[key]) or effective[key] <= 0
        for key in ("physics_dt_s", "control_dt_s", "render_period_s")
    ):
        raise ValueError("execution provenance requires finite positive effective timing")
    if (effective.get("control_dt_s") != report.get("control_dt_s")
            or effective.get("physics_dt_s") != rendering.get("physics_dt_s")
            or effective.get("render_period_s") != rendering.get("actual_period_s")):
        raise ValueError("execution provenance effective timing differs from report")
    if attempt.get("report_sha256") != canonical_sha256(report):
        raise ValueError("execution report digest mismatch")
    if check_runtime_files and changed_runtime_files(runtime):
        raise ValueError("execution provenance runtime files differ from current installation")
    return provenance
