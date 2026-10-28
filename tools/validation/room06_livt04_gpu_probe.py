#!/usr/bin/env python3
"""Fail-closed GPU probe driver for ``room_06 × liv-t-04``.

The default path is a full-task probe.  It validates a content-addressed input,
reruns the room-specific static preflight, and verifies the fixed package before
Isaac Lab is imported.  A separate, explicit scene-load diagnostic can exercise
only the hash-bound room_06 scene and CUDA identity.  That diagnostic path has
no task-runtime or terminal-material inputs and cannot emit task evidence.

The repository currently has no production liv-t-04 skill executor.  A future
executor must be supplied as a hash-bound Python hook.  Its observations are
still checked by the existing observer bridge; commands, lock grants, process
exit, and provider claims never become task completion.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from functools import partial
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import traceback
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
for _package in ("EAI", "EAI_assets", "EAI_hmrs"):
    _source = ROOT / "source" / _package
    if str(_source) not in sys.path:
        sys.path.insert(0, str(_source))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.room06_fixed_package_v1 import (  # noqa: E402
    Room06FixedPackageError,
    verify_room06_fixed_package,
)
from benchmark.room06_runtime_preflight_v1 import (  # noqa: E402
    FORMAL_GATES,
    audit_room06_runtime_preflight,
)


INPUT_SCHEMA = "heterocoord.room06-livt04-gpu-probe-input.v1"
FULL_REPORT_SCHEMA = "heterocoord.room06-livt04-gpu-probe-report.v1"
SCENE_REPORT_SCHEMA = "heterocoord.room06-scene-load-diagnostic.v1"
HOOK_CAPTURE_SCHEMA = "heterocoord.room06-runtime-hook-capture.v1"
EXPECTED_SCENE_SHA256 = (
    "e67affcbd6d67941feb8bc7728d6be4cd74ad63d414a8b65db771337b76e300a"
)
FULL_MODE = "full_task"
SCENE_MODE = "scene_load_diagnostic_only"
_HEX = frozenset("0123456789abcdef")
_CUDA_RE = re.compile(r"^cuda:(0|[1-9][0-9]*)$")
_GPU_UUID_RE = re.compile(
    r"^(?:GPU-)?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_INPUT_FIELDS = {
    "schema",
    "input_digest",
    "mode",
    "scene_ref",
    "requested_device",
    "expected_gpu_uuid",
    "headless",
    "seed",
    "diagnostic_steps",
    "package_ref",
    "preflight_inputs",
    "runtime_hook_ref",
    "formal_gates",
}
_FILE_REF_FIELDS = {"path", "sha256"}
_PACKAGE_REF_FIELDS = {"path", "sha256", "package_digest"}
_DIRECTORY_REF_FIELDS = {"path", "sha256"}
_PREFLIGHT_FIELDS = {
    "semantic_root",
    "scene_usd",
    "robot_roster",
    "binding",
    "task_setup",
}
_HOOK_REF_FIELDS = {"path", "sha256", "factory"}
_CAPTURE_FIELDS = {
    "schema",
    "assignment",
    "observations",
    "terminal_inputs",
    "collector_closed",
}
_ASSIGNMENT_FIELDS = {"tick", "assigned_robot_ids", "decision_digest"}
_TERMINAL_INPUT_FIELDS = {
    "episode",
    "d1",
    "d4_first_event_observation",
    "d6_measurement",
}


class Room06GpuProbeError(RuntimeError):
    """Raised when probe input, runtime identity, or observations fail closed."""

    def __init__(
        self,
        message: str,
        *,
        device_identity: Mapping[str, str] | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.device_identity = dict(device_identity) if device_identity else None
        self.details = deepcopy(dict(details or {}))


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Room06GpuProbeError("value is not finite canonical JSON") from exc


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06GpuProbeError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in _HEX for character in value):
        raise Room06GpuProbeError(f"{name} must be a lowercase SHA-256")
    return value


def _exact(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise Room06GpuProbeError(f"{name} schema mismatch")
    return value


def _sha256_file(path: Path, name: str) -> str:
    if not path.is_file() or path.is_symlink():
        raise Room06GpuProbeError(f"{name} is not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise Room06GpuProbeError(f"cannot hash {name}: {path}") from exc
    return digest.hexdigest()


def directory_digest(path: str | Path) -> str:
    """Hash a directory without following links or accepting special files."""

    declared = Path(path).expanduser()
    if declared.is_symlink():
        raise Room06GpuProbeError(f"directory input is a symlink: {declared}")
    root = declared.resolve()
    if not root.is_dir():
        raise Room06GpuProbeError(f"directory input is unavailable: {root}")
    rows: list[dict[str, str]] = []
    for item in sorted(root.rglob("*"), key=lambda candidate: candidate.as_posix()):
        if item.is_symlink():
            raise Room06GpuProbeError(f"directory input contains a symlink: {item}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise Room06GpuProbeError(f"directory input contains a special file: {item}")
        rows.append(
            {
                "path": item.relative_to(root).as_posix(),
                "sha256": _sha256_file(item, "directory member"),
            }
        )
    if not rows:
        raise Room06GpuProbeError(f"directory input is empty: {root}")
    return canonical_digest(rows)


def _resolve_file_ref(value: Any, name: str) -> Path:
    ref = _exact(value, _FILE_REF_FIELDS, name)
    path = Path(_text(ref.get("path"), f"{name}.path")).expanduser().resolve()
    expected = _digest(ref.get("sha256"), f"{name}.sha256")
    actual = _sha256_file(path, name)
    if actual != expected:
        raise Room06GpuProbeError(
            f"{name} digest mismatch: expected {expected}, observed {actual}"
        )
    return path


def _resolve_directory_ref(value: Any, name: str) -> Path:
    ref = _exact(value, _DIRECTORY_REF_FIELDS, name)
    declared = Path(_text(ref.get("path"), f"{name}.path")).expanduser()
    if declared.is_symlink():
        raise Room06GpuProbeError(f"{name} path is a symlink: {declared}")
    path = declared.resolve()
    expected = _digest(ref.get("sha256"), f"{name}.sha256")
    actual = directory_digest(path)
    if actual != expected:
        raise Room06GpuProbeError(
            f"{name} digest mismatch: expected {expected}, observed {actual}"
        )
    return path


def _semantic_revision_from_artifact_root(root: Path) -> Path:
    """Resolve the project-selected revision inside one fully hashed artifact."""

    project_path = root / "project.json"
    if not project_path.is_file() or project_path.is_symlink():
        raise Room06GpuProbeError(
            f"semantic artifact project.json is not a regular file: {project_path}"
        )
    try:
        project = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Room06GpuProbeError(
            f"cannot read semantic artifact project.json: {project_path}"
        ) from exc
    if not isinstance(project, Mapping):
        raise Room06GpuProbeError("semantic artifact project.json must be an object")
    current = _text(project.get("current_revision"), "project.current_revision")
    relative = Path(current)
    if relative.is_absolute():
        raise Room06GpuProbeError("project.current_revision must be relative")
    if relative == Path(".") or ".." in relative.parts:
        raise Room06GpuProbeError(
            "project.current_revision must select a child revision without '..'"
        )
    candidate = root.joinpath(relative)
    if candidate.is_symlink() or not candidate.is_dir():
        raise Room06GpuProbeError(
            "project.current_revision is not a regular revision directory"
        )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise Room06GpuProbeError(
            "project.current_revision escapes the semantic artifact root"
        ) from exc
    return resolved


def probe_input_digest(value: Mapping[str, Any]) -> str:
    body = dict(value)
    body.pop("input_digest", None)
    return canonical_digest(body)


def _validate_common_input(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) not in (
        _INPUT_FIELDS, _INPUT_FIELDS | {"runtime_source_ref"},
    ):
        raise Room06GpuProbeError("probe input schema mismatch")
    if value.get("schema") != INPUT_SCHEMA:
        raise Room06GpuProbeError("probe input schema is unsupported")
    declared = _digest(value.get("input_digest"), "input_digest")
    actual = probe_input_digest(value)
    if declared != actual:
        raise Room06GpuProbeError("input_digest does not bind probe input")
    if value.get("formal_gates") != FORMAL_GATES:
        raise Room06GpuProbeError("probe input formal gates must remain false")
    requested = _text(value.get("requested_device"), "requested_device").lower()
    if not _CUDA_RE.fullmatch(requested):
        raise Room06GpuProbeError("requested_device must be an explicit CUDA device")
    expected_uuid = _text(value.get("expected_gpu_uuid"), "expected_gpu_uuid")
    if not expected_uuid.startswith("GPU-"):
        raise Room06GpuProbeError("expected_gpu_uuid must be an NVIDIA GPU UUID")
    if type(value.get("headless")) is not bool:
        raise Room06GpuProbeError("headless must be boolean")
    seed = value.get("seed")
    if type(seed) is not int or seed < 0:
        raise Room06GpuProbeError("seed must be a non-negative integer")
    steps = value.get("diagnostic_steps")
    if type(steps) is not int or not 1 <= steps <= 100:
        raise Room06GpuProbeError("diagnostic_steps must be in [1, 100]")
    mode = value.get("mode")
    if mode not in {FULL_MODE, SCENE_MODE}:
        raise Room06GpuProbeError("probe mode is unsupported")
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def load_probe_input(
    path: str | Path,
    *,
    expected_file_sha256: str,
    scene_load_diagnostic_only: bool,
) -> dict[str, Any]:
    """Read and validate the frozen probe input before any Isaac import."""

    source = Path(path).expanduser().resolve()
    expected_file = _digest(expected_file_sha256, "input file SHA-256")
    actual_file = _sha256_file(source, "probe input")
    if actual_file != expected_file:
        raise Room06GpuProbeError(
            f"probe input file digest mismatch: expected {expected_file}, observed {actual_file}"
        )
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Room06GpuProbeError(f"cannot read probe input: {source}") from exc
    value = _validate_common_input(raw)
    expected_mode = SCENE_MODE if scene_load_diagnostic_only else FULL_MODE
    if value["mode"] != expected_mode:
        raise Room06GpuProbeError(
            f"probe input mode {value['mode']!r} does not match requested mode {expected_mode!r}"
        )
    if expected_mode == SCENE_MODE:
        if any(value.get(field) is not None for field in ("package_ref", "preflight_inputs", "runtime_hook_ref", "runtime_source_ref")):
            raise Room06GpuProbeError(
                "scene-load diagnostic input cannot contain task/package/runtime-hook fields"
            )
    else:
        if any(value[field] is None for field in ("package_ref", "preflight_inputs")):
            raise Room06GpuProbeError("full-task input requires package and preflight inputs")
    return value


def _verify_scene_ref(value: Any) -> Path:
    scene = _resolve_file_ref(value, "scene_ref")
    declared = value["sha256"]
    if declared != EXPECTED_SCENE_SHA256:
        raise Room06GpuProbeError(
            "scene_ref does not bind the project-published room_06 USD digest"
        )
    return scene


def normalize_device_identity(
    *,
    requested_device: str,
    actual_device: str,
    expected_gpu_uuid: str,
    actual_gpu_uuid: str,
) -> dict[str, str]:
    requested = _text(requested_device, "requested_device").lower()
    reported = _text(actual_device, "actual_device").lower()
    actual = "cuda:0" if reported == "cuda" else reported
    if not _CUDA_RE.fullmatch(requested):
        raise Room06GpuProbeError("requested_device must be an explicit CUDA device")
    if not _CUDA_RE.fullmatch(actual):
        raise Room06GpuProbeError(
            f"actual physics device is not CUDA: {actual_device}"
        )
    if requested != actual:
        raise Room06GpuProbeError(
            f"CUDA device mismatch: requested {requested}, observed {actual}"
        )
    expected_uuid = _text(expected_gpu_uuid, "expected_gpu_uuid")
    observed_uuid = _text(actual_gpu_uuid, "actual_gpu_uuid")
    if expected_uuid != observed_uuid:
        raise Room06GpuProbeError(
            f"GPU UUID mismatch: expected {expected_uuid}, observed {observed_uuid}"
        )
    return {
        "requested_device": requested,
        "actual_device": actual,
        "actual_device_reported": reported,
        "expected_gpu_uuid": expected_uuid,
        "actual_gpu_uuid": observed_uuid,
    }


def _normalize_cuda_uuid(value: Any) -> str:
    """Normalize PyTorch string or ``_CUuuid`` values to NVIDIA's GPU form."""

    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise Room06GpuProbeError(
                "CUDA device properties expose a non-ASCII GPU UUID"
            ) from exc
    elif not isinstance(value, str):
        value = str(value)
    match = _GPU_UUID_RE.fullmatch(value.strip())
    if match is None:
        raise Room06GpuProbeError(
            "CUDA device properties do not expose a canonical GPU UUID"
        )
    return "GPU-" + match.group(1).lower()


def _query_device_identity(request: Mapping[str, Any]) -> dict[str, str]:
    try:
        from isaacsim.core.simulation_manager import SimulationManager
        import torch

        actual_device = str(SimulationManager.get_physics_sim_device()).lower()
        normalized_actual = "cuda:0" if actual_device == "cuda" else actual_device
        match = _CUDA_RE.fullmatch(normalized_actual)
        if match is None:
            raise Room06GpuProbeError(
                f"SimulationManager reported a non-CUDA physics device: {actual_device}"
            )
        index = int(match.group(1))
        if not torch.cuda.is_available():
            raise Room06GpuProbeError("torch reports CUDA unavailable after AppLauncher")
        properties = torch.cuda.get_device_properties(index)
        actual_uuid = _normalize_cuda_uuid(getattr(properties, "uuid", None))
    except Room06GpuProbeError:
        raise
    except Exception as exc:
        raise Room06GpuProbeError(f"cannot query actual CUDA device identity: {exc}") from exc
    return normalize_device_identity(
        requested_device=request["requested_device"],
        actual_device=actual_device,
        expected_gpu_uuid=request["expected_gpu_uuid"],
        actual_gpu_uuid=actual_uuid,
    )


def reserve_output_directory(path: str | Path) -> Path:
    output = Path(path).expanduser().resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise Room06GpuProbeError(f"output directory already exists: {output}") from exc
    return output


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    canonical_json_bytes(value)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except FileExistsError as exc:
        raise Room06GpuProbeError(f"output already exists: {path}") from exc


def build_diagnostic_report(
    *,
    mode: str,
    phase: str,
    reason: str,
    request: Mapping[str, Any] | None = None,
    device_identity: Mapping[str, str] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    schema = SCENE_REPORT_SCHEMA if mode == SCENE_MODE else FULL_REPORT_SCHEMA
    report = {
        "schema": schema,
        "mode": mode,
        "status": "diagnostic_failure",
        "phase": _text(phase, "phase"),
        "reason": _text(reason, "reason"),
        "input_digest": request.get("input_digest") if request else None,
        "device_identity": dict(device_identity) if device_identity else None,
        "details": deepcopy(dict(details or {})),
        "formal_gates": dict(FORMAL_GATES),
    }
    canonical_json_bytes(report)
    return report


def build_scene_success_report(
    *,
    request: Mapping[str, Any],
    device_identity: Mapping[str, str],
    scene_readback: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the deliberately task-free scene diagnostic success envelope."""

    if request.get("mode") != SCENE_MODE:
        raise Room06GpuProbeError("scene success report requires diagnostic-only mode")
    report = {
        "schema": SCENE_REPORT_SCHEMA,
        "mode": SCENE_MODE,
        "status": "diagnostic_only",
        "input_digest": request["input_digest"],
        "device_identity": deepcopy(dict(device_identity)),
        "scene_readback": deepcopy(dict(scene_readback)),
        "limitations": [
            "no task objects or skills were run",
            "no observer events or held_object snapshots were collected",
            "no terminal material or receipt was produced",
        ],
        "formal_gates": dict(FORMAL_GATES),
    }
    canonical_json_bytes(report)
    return report


def _load_json_ref(value: Any, name: str) -> tuple[Path, dict[str, Any]]:
    path = _resolve_file_ref(value, name)
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Room06GpuProbeError(f"cannot read {name}: {path}") from exc
    if not isinstance(result, Mapping):
        raise Room06GpuProbeError(f"{name} must contain a JSON object")
    return path, dict(result)


def _verify_hook_ref(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    ref = _exact(value, _HOOK_REF_FIELDS, "runtime_hook_ref")
    path = _resolve_file_ref(
        {"path": ref["path"], "sha256": ref["sha256"]},
        "runtime_hook_ref",
    )
    factory = _text(ref.get("factory"), "runtime_hook_ref.factory")
    if not factory.isidentifier():
        raise Room06GpuProbeError("runtime_hook_ref.factory must be a Python identifier")
    return {"path": str(path), "sha256": ref["sha256"], "factory": factory}


def _verify_package_ref(value: Any) -> tuple[Path, dict[str, Any]]:
    ref = _exact(value, _PACKAGE_REF_FIELDS, "package_ref")
    path = _resolve_file_ref(
        {"path": ref["path"], "sha256": ref["sha256"]}, "package_ref"
    )
    expected_digest = _digest(ref.get("package_digest"), "package_ref.package_digest")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Room06GpuProbeError(f"cannot read fixed package: {path}") from exc
    try:
        package = verify_room06_fixed_package(raw)
    except (Room06FixedPackageError, TypeError, ValueError) as exc:
        raise Room06GpuProbeError(f"fixed package verification failed: {exc}") from exc
    if package.get("package_digest") != expected_digest:
        raise Room06GpuProbeError("fixed package digest disagrees with package_ref")
    return path, package


def _fresh_preflight(value: Any) -> tuple[dict[str, Path], dict[str, Any]]:
    inputs = _exact(value, _PREFLIGHT_FIELDS, "preflight_inputs")
    semantic_artifact_root = _resolve_directory_ref(
        inputs["semantic_root"], "semantic_root"
    )
    semantic_revision = _semantic_revision_from_artifact_root(
        semantic_artifact_root
    )
    scene_usd = _resolve_file_ref(inputs["scene_usd"], "preflight scene_usd")
    roster, _ = _load_json_ref(inputs["robot_roster"], "robot_roster")
    binding, _ = _load_json_ref(inputs["binding"], "preflight binding")
    task_setup, _ = _load_json_ref(inputs["task_setup"], "task_setup")
    report = audit_room06_runtime_preflight(
        semantic_root=semantic_revision,
        scene_usd=scene_usd,
        robot_roster=roster,
        binding=binding,
        task_setup=task_setup,
    )
    if report.get("formal_gates") != FORMAL_GATES:
        raise Room06GpuProbeError("fresh preflight formal gates must remain false")
    if report.get("preflight_passed") is not True:
        failures = report.get("failures")
        reason = "; ".join(str(item) for item in failures) if isinstance(failures, list) else "unknown failure"
        raise Room06GpuProbeError(
            f"fresh room06 preflight blocked: {reason}",
            details={"preflight_report": report},
        )
    return {
        "semantic_root": semantic_artifact_root,
        "semantic_revision": semantic_revision,
        "scene_usd": scene_usd,
        "robot_roster": roster,
        "binding": binding,
        "task_setup": task_setup,
    }, report


def prepare_scene_diagnostic(request: Mapping[str, Any]) -> dict[str, Any]:
    if request.get("mode") != SCENE_MODE:
        raise Room06GpuProbeError("scene diagnostic preparation requires scene mode")
    scene_path = _verify_scene_ref(request["scene_ref"])
    return {"request": deepcopy(dict(request)), "scene_path": scene_path}


def prepare_full_task(request: Mapping[str, Any]) -> dict[str, Any]:
    """Verify every full-task input and rerun preflight before AppLauncher."""

    if request.get("mode") != FULL_MODE:
        raise Room06GpuProbeError("full-task preparation requires full mode")
    for field in ("runtime_hook_ref", "runtime_source_ref"):
        if request.get(field) is None:
            raise Room06GpuProbeError(f"full-task input requires {field} before simulator launch")
        _verify_hook_ref(request[field])
    scene_path = _verify_scene_ref(request["scene_ref"])
    paths, fresh = _fresh_preflight(request["preflight_inputs"])
    if paths["scene_usd"] != scene_path:
        raise Room06GpuProbeError("scene_ref and preflight scene_usd paths disagree")
    _package_path, package = _verify_package_ref(request["package_ref"])
    if package.get("preflight") != fresh:
        raise Room06GpuProbeError("fixed package preflight disagrees with fresh preflight")
    if package.get("readiness", {}).get("formal_gates") != FORMAL_GATES:
        raise Room06GpuProbeError("fixed package formal gates must remain false")
    hook_ref = _verify_hook_ref(request.get("runtime_hook_ref"))
    source_ref = _verify_hook_ref(request.get("runtime_source_ref"))
    return {
        "request": deepcopy(dict(request)),
        "scene_path": scene_path,
        "paths": paths,
        "preflight": fresh,
        "package": package,
        "runtime_hook_ref": hook_ref,
        "runtime_source_ref": source_ref,
    }


def _selected_roster_rows(prepared: Mapping[str, Any]) -> list[dict[str, Any]]:
    roster_path = prepared["paths"]["robot_roster"]
    try:
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Room06GpuProbeError("cannot reload frozen robot roster") from exc
    selected = prepared["package"]["plan"]["selected_robot_ids"]
    rows = roster.get("robots") if isinstance(roster, Mapping) else None
    if not isinstance(rows, list):
        raise Room06GpuProbeError("frozen robot roster is malformed")
    by_id = {
        row.get("instance_id"): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("instance_id"), str)
    }
    if set(selected) - set(by_id):
        raise Room06GpuProbeError("selected instances disappeared from frozen roster")
    return [deepcopy(dict(by_id[instance_id])) for instance_id in selected]


def build_frozen_selection(prepared: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Build the Isaac selection strictly from the frozen roster and deployment."""

    selected = prepared["package"]["plan"]["selected_robot_ids"]
    deployments = {
        row["instance_id"]: row
        for row in prepared["package"]["scene_deployment"]["robots"]
    }
    rows = _selected_roster_rows(prepared)
    counts: dict[str, int] = {}
    agents: dict[str, str] = {}
    robots: list[dict[str, Any]] = []
    for instance_id, row in zip(selected, rows, strict=True):
        platform = _text(row.get("platform_type"), f"{instance_id}.platform_type").lower()
        capability = row.get("capability_binding")
        source = capability.get("source_input") if isinstance(capability, Mapping) else None
        if not isinstance(source, Mapping):
            raise Room06GpuProbeError(f"{instance_id} capability source is missing")
        controller = _text(source.get("motion_controller"), f"{instance_id}.motion_controller")
        manipulator = source.get("external_manipulator")
        attachments: list[dict[str, Any]] = []
        if manipulator is not None:
            manipulator = _text(manipulator, f"{instance_id}.external_manipulator").lower()
            manipulator_controller = _text(
                source.get("manipulator_controller"),
                f"{instance_id}.manipulator_controller",
            )
            attachments.append(
                {
                    "type": manipulator,
                    "controller": {"mode": "default", "cfg": manipulator_controller},
                }
            )
        deployment = deployments.get(instance_id)
        if not isinstance(deployment, Mapping):
            raise Room06GpuProbeError(f"deployment is missing selected instance {instance_id}")
        counts[platform] = counts.get(platform, 0) + 1
        agents[instance_id] = f"{platform}_{counts[platform]}"
        robots.append(
            {
                "type": platform,
                "controller": {"mode": "default", "cfg": controller},
                "visual": {},
                "attachments": attachments,
                "spawn_pose": {
                    "position": list(deployment["position_m"]),
                    "rotation": list(deployment["orientation_wxyz"]),
                },
            }
        )
    return {
        "version": 1,
        "scene_key": "room_06",
        "task_name": "room06-livt04-gpu-probe",
        "robots": robots,
    }, agents


def normalize_runtime_hook_capture(
    value: Any,
    *,
    selected_robot_ids: Sequence[str],
) -> dict[str, Any]:
    """Validate observer-owned hook output without inferring any success."""

    if value is None:
        raise Room06GpuProbeError("room06 runtime hook provider is unavailable")
    capture = _exact(value, _CAPTURE_FIELDS, "runtime hook capture")
    if capture.get("schema") != HOOK_CAPTURE_SCHEMA:
        raise Room06GpuProbeError("runtime hook capture schema is unsupported")
    if capture.get("collector_closed") is not True:
        raise Room06GpuProbeError("runtime hook collector did not close explicitly")
    assignment = _exact(capture.get("assignment"), _ASSIGNMENT_FIELDS, "assignment")
    if type(assignment.get("tick")) is not int or assignment["tick"] < 0:
        raise Room06GpuProbeError("assignment.tick must be a non-negative integer")
    assigned = assignment.get("assigned_robot_ids")
    if isinstance(assigned, (str, bytes)) or not isinstance(assigned, Sequence):
        raise Room06GpuProbeError("assignment.assigned_robot_ids must be a sequence")
    assigned_ids = [_text(item, "assigned_robot_id") for item in assigned]
    if not assigned_ids or len(set(assigned_ids)) != len(assigned_ids):
        raise Room06GpuProbeError("assignment robot IDs must be unique and non-empty")
    if not set(assigned_ids).issubset(set(selected_robot_ids)):
        raise Room06GpuProbeError("assignment contains an instance outside the frozen roster")
    _digest(assignment.get("decision_digest"), "assignment.decision_digest")
    observations = capture.get("observations")
    if not isinstance(observations, list) or not observations:
        raise Room06GpuProbeError("runtime hooks emitted no observer records")

    from benchmark.room06_observation_bridge_v1 import normalize_skill_event

    normalized_records: list[dict[str, Any]] = []
    event_count = 0
    snapshot_count = 0
    for index, record in enumerate(observations):
        if not isinstance(record, Mapping):
            raise Room06GpuProbeError(f"observations[{index}] must be an object")
        kind = record.get("kind")
        if kind == "skill_event":
            _exact(record, {"kind", "event"}, f"observations[{index}]")
            try:
                event = normalize_skill_event(record["event"])
            except (TypeError, ValueError) as exc:
                raise Room06GpuProbeError(f"invalid observer skill event: {exc}") from exc
            normalized_records.append({"kind": kind, "event": event})
            event_count += 1
        elif kind == "held_object_snapshot":
            _exact(record, {"kind", "tick", "held_objects"}, f"observations[{index}]")
            tick = record.get("tick")
            if type(tick) is not int or tick < 0:
                raise Room06GpuProbeError("held_object snapshot tick must be non-negative")
            held = record.get("held_objects")
            if not isinstance(held, Mapping) or set(held) != set(selected_robot_ids):
                raise Room06GpuProbeError(
                    "held_object snapshot must exactly cover the frozen runtime roster"
                )
            normalized_held: dict[str, str | None] = {}
            for robot_id in selected_robot_ids:
                object_id = held[robot_id]
                if object_id is not None:
                    object_id = _text(object_id, f"held_objects[{robot_id!r}]")
                normalized_held[robot_id] = object_id
            normalized_records.append(
                {"kind": kind, "tick": tick, "held_objects": normalized_held}
            )
            snapshot_count += 1
        elif kind == "horizon_reached":
            _exact(record, {"kind", "tick", "source"}, f"observations[{index}]")
            if record.get("tick") != 15_000 or record.get("source") != "observer":
                raise Room06GpuProbeError(
                    "horizon record must be observer-owned at tick 15000"
                )
            normalized_records.append(
                {"kind": kind, "tick": 15_000, "source": "observer"}
            )
        else:
            raise Room06GpuProbeError(
                f"unsupported runtime observer record kind: {kind!r}"
            )
    if event_count == 0:
        raise Room06GpuProbeError("runtime hooks emitted no completed skill-session events")
    if snapshot_count == 0:
        raise Room06GpuProbeError("runtime hooks emitted no held_object snapshots")
    terminal = _exact(
        capture.get("terminal_inputs"),
        _TERMINAL_INPUT_FIELDS,
        "terminal_inputs",
    )
    normalized = {
        "schema": HOOK_CAPTURE_SCHEMA,
        "assignment": {
            "tick": assignment["tick"],
            "assigned_robot_ids": list(assigned_ids),
            "decision_digest": assignment["decision_digest"],
        },
        "observations": normalized_records,
        "terminal_inputs": deepcopy(dict(terminal)),
        "collector_closed": True,
    }
    canonical_json_bytes(normalized)
    return normalized


def compose_terminal_evidence(
    prepared: Mapping[str, Any],
    capture_value: Any,
) -> dict[str, Any]:
    """Route only normalized observer facts through runtime and terminal seams."""

    from benchmark.room06_observation_bridge_v1 import Room06ObservationBridge
    from benchmark.room06_task_runtime_v1 import Room06TaskRuntime
    from benchmark.room06_terminal_material_v1 import assemble_room06_terminal_payload

    package = prepared["package"]
    selected = package["plan"]["selected_robot_ids"]
    capture = normalize_runtime_hook_capture(
        capture_value, selected_robot_ids=selected
    )
    binding_digest = package["plan"]["room06_runtime_binding"][
        "runtime_binding_digest"
    ]
    runtime = Room06TaskRuntime(
        task_setup=package["task_setup"],
        task_object_bindings=package["task_object_bindings"],
        preflight_report=prepared["preflight"],
        runtime_binding_digest=binding_digest,
    )
    bridge = Room06ObservationBridge(
        runtime,
        robot_ids=selected,
        runtime_binding_digest=binding_digest,
    )
    assignment = capture["assignment"]
    bridge.observe_assignment(
        tick=assignment["tick"],
        assigned_robot_ids=assignment["assigned_robot_ids"],
        decision_digest=assignment["decision_digest"],
    )
    for record in capture["observations"]:
        if record["kind"] == "skill_event":
            bridge.observe_skill_event(record["event"])
        elif record["kind"] == "held_object_snapshot":
            bridge.sample_holds(
                tick=record["tick"], held_objects=record["held_objects"]
            )
        else:
            bridge.finalize_at_horizon()
    try:
        bridge_material = bridge.evidence_material()
    except (TypeError, ValueError) as exc:
        raise Room06GpuProbeError(
            "runtime hooks closed before observer-derived terminal state"
        ) from exc
    terminal = capture["terminal_inputs"]
    try:
        payload = assemble_room06_terminal_payload(
            runtime_evidence=bridge_material["runtime_evidence"],
            episode=terminal["episode"],
            d1=terminal["d1"],
            d4_first_event_observation=terminal["d4_first_event_observation"],
            d6_measurement=terminal["d6_measurement"],
            obligation_binding=package["plan"]["oracle_obligation_binding"],
        )
    except (TypeError, ValueError) as exc:
        raise Room06GpuProbeError(f"terminal material assembly failed: {exc}") from exc
    if payload.get("formal_gates") != FORMAL_GATES:
        raise Room06GpuProbeError("terminal material formal gates must remain false")
    return {
        "bridge_material": bridge_material,
        "terminal_payload": payload,
        "runtime_state": payload["runtime_state"],
        "observation_counts": {
            "completed_skill_events": len(bridge_material["observed_skill_events"]),
            "held_object_snapshots": sum(
                row["kind"] == "held_object_snapshot"
                for row in capture["observations"]
            ),
        },
    }


def _load_runtime_hook(ref: Mapping[str, Any]) -> Callable[..., Any]:
    # Recheck immediately before executing plugin code, not only at preparation.
    _verify_hook_ref(ref)
    path = Path(ref["path"])
    name = f"_room06_probe_hook_{ref['sha256']}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Room06GpuProbeError(f"cannot import runtime hook: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise Room06GpuProbeError(f"runtime hook import failed: {exc}") from exc
    factory = getattr(module, ref["factory"], None)
    if not callable(factory):
        raise Room06GpuProbeError(
            f"runtime hook factory is not callable: {ref['factory']}"
        )
    return factory


def _scene_cfg_path(session: Any) -> Path:
    try:
        return Path(session.env_cfg.scene.world.spawn.usd_path).resolve()
    except Exception as exc:
        raise Room06GpuProbeError("loaded environment exposes no room_06 USD path") from exc


def _finite_scene_readback(session: Any, *, steps: int, scene_path: Path) -> dict[str, Any]:
    loaded_path = _scene_cfg_path(session)
    if loaded_path != scene_path:
        raise Room06GpuProbeError(
            f"loaded room_06 USD differs from frozen scene: {loaded_path}"
        )
    if _sha256_file(loaded_path, "loaded room_06 USD") != EXPECTED_SCENE_SHA256:
        raise Room06GpuProbeError("loaded room_06 USD bytes changed after launch")
    try:
        import omni.usd
        from isaacsim.core.simulation_manager import SimulationManager

        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath("/World/Room06") if stage is not None else None
        if prim is None or not prim.IsValid():
            raise Room06GpuProbeError("loaded stage has no valid /World/Room06 prim")
        for _index in range(steps):
            session.base_env.sim.step(render=False)
        physics_dt = float(session.base_env.sim.get_physics_dt())
        simulation_time = float(SimulationManager.get_simulation_time())
    except Room06GpuProbeError:
        raise
    except Exception as exc:
        raise Room06GpuProbeError(f"scene step/readback failed: {exc}") from exc
    if not math.isfinite(physics_dt) or physics_dt <= 0:
        raise Room06GpuProbeError("scene physics_dt is not finite and positive")
    if not math.isfinite(simulation_time) or simulation_time < 0:
        raise Room06GpuProbeError("scene simulation time is invalid")
    return {
        "scene_usd_path": str(loaded_path),
        "scene_usd_sha256": EXPECTED_SCENE_SHA256,
        "room06_prim_path": "/World/Room06",
        "finite_steps": steps,
        "physics_dt": physics_dt,
        "simulation_time": simulation_time,
    }


def _session_config(
    request: Mapping[str, Any],
    *,
    selection: Mapping[str, Any],
    runtime_source_factory: Callable[..., Any] | None = None,
    env_cfg_hook: Callable[[Any], None] | None = None,
) -> Any:
    from simulator import SimulatorLaunchConfig

    return SimulatorLaunchConfig(
        env="room06-livt04-gpu-probe",
        device=request["requested_device"],
        seed=request["seed"],
        headless=request["headless"],
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        resolved_env_name="EAI-Interactive-v0",
        selection_data=deepcopy(dict(selection)),
        room06_runtime_hook_source_factory=runtime_source_factory,
        env_cfg_hook=env_cfg_hook,
        app_launcher_args={
            "headless": request["headless"],
            "enable_cameras": True,
            "device": request["requested_device"],
        },
    )


def _room06_task_object_env_cfg_hook(package: Mapping[str, Any]) -> Callable[[Any], None]:
    """Attach the case-owned logical objects before Isaac creates the scene."""

    def configure(env_cfg: Any) -> None:
        from EAI_assets.scene.room06_task_objects import (
            build_room06_task_object_configs,
            build_room06_task_object_plan,
        )

        audit = package["preflight"]["semantic_input_audit"]
        manifest_path = Path(audit["manifest_path"]).resolve()
        semantic_points_path = manifest_path.parent / "semantic-points.json"
        points_payload = json.loads(semantic_points_path.read_text(encoding="utf-8"))
        points = {
            row["label"]: row["position_xyz"]
            for row in points_payload.get("points", [])
            if isinstance(row, Mapping) and isinstance(row.get("label"), str)
        }
        plan = build_room06_task_object_plan(package["task_setup"], points)
        configs = build_room06_task_object_configs(plan)
        scene_cls = type(env_cfg.scene)
        annotations = getattr(scene_cls, "__annotations__", {})
        for entity_id, config in configs.items():
            # RigidObjectCfg uses an environment regex; the observer later
            # resolves env_0 to the exact logical USD binding.
            config.prim_path = config.prim_path.replace(
                "/World/envs/env_0", "{ENV_REGEX_NS}"
            )
            annotations[entity_id] = type(config)
            setattr(env_cfg.scene, entity_id, config)

    return configure


def run_scene_diagnostic(
    prepared: Mapping[str, Any],
    *,
    report_writer: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Load/step/read room_06 only; this function has no task evidence path."""

    from simulator import open_simulator_session

    request = prepared["request"]
    selection = {
        "version": 1,
        "scene_key": "room_06",
        "task_name": "room06-scene-load-diagnostic-only",
        "robots": [],
    }
    with open_simulator_session(_session_config(request, selection=selection)) as session:
        identity = _query_device_identity(request)
        try:
            readback = _finite_scene_readback(
                session,
                steps=request["diagnostic_steps"],
                scene_path=prepared["scene_path"],
            )
        except Room06GpuProbeError as exc:
            raise Room06GpuProbeError(
                str(exc), device_identity=identity, details=exc.details
            ) from exc
        report = build_scene_success_report(
            request=request,
            device_identity=identity,
            scene_readback=readback,
        )
        if report_writer is not None:
            # Isaac's native close can terminate the process while leaving this
            # context.  Seal the report while the live readback is still owned.
            report_writer(report)
    return report


def run_full_task(
    prepared: Mapping[str, Any],
    *,
    capture_writer: Callable[[Mapping[str, Any]], None] | None = None,
    report_writer: Callable[[Mapping[str, Any]], None] | None = None,
    terminal_job: Any | None = None,
    evaluator_result: Mapping[str, Any] | None = None,
    parameters: Mapping[str, Any] | None = None,
    orchestration_result: Any | None = None,
) -> dict[str, Any]:
    handoff_values = (terminal_job, evaluator_result, parameters)
    if any(value is not None for value in handoff_values) and not all(
        value is not None for value in handoff_values
    ):
        raise Room06GpuProbeError(
            "terminal_job, evaluator_result and parameters must be supplied together"
        )
    if orchestration_result is not None and not all(
        value is not None for value in handoff_values
    ):
        raise Room06GpuProbeError(
            "orchestration_result requires terminal_job, evaluator_result and parameters"
        )
    request = prepared["request"]
    for field in ("runtime_hook_ref", "runtime_source_ref"):
        if prepared.get(field) is None:
            raise Room06GpuProbeError(f"full-task input requires {field} before simulator launch")
    # Imports and callable checks must complete before any simulator process.
    hook = _load_runtime_hook(prepared["runtime_hook_ref"])
    source_provider = _load_runtime_hook(prepared["runtime_source_ref"])
    from simulator import open_simulator_session

    selection, instance_agent_map = build_frozen_selection(prepared)
    source_factory = partial(
        source_provider,
        package=deepcopy(prepared["package"]),
        instance_agent_map=dict(instance_agent_map),
    )
    with open_simulator_session(_session_config(
        request, selection=selection, runtime_source_factory=source_factory,
        env_cfg_hook=_room06_task_object_env_cfg_hook(prepared["package"]),
    )) as session:
        identity = _query_device_identity(request)
        readback: dict[str, Any] | None = None
        try:
            readback = _finite_scene_readback(
                session,
                steps=request["diagnostic_steps"],
                scene_path=prepared["scene_path"],
            )
            capture = hook(
                session=session,
                package=deepcopy(prepared["package"]),
                instance_agent_map=dict(instance_agent_map),
            )
            if capture_writer is not None and isinstance(capture, Mapping):
                capture_writer(capture)
            evidence = compose_terminal_evidence(prepared, capture)
        except Room06GpuProbeError as exc:
            raise Room06GpuProbeError(
                str(exc),
                device_identity=identity,
                details={
                    **exc.details,
                    "scene_readback": readback,
                    "instance_agent_map": instance_agent_map,
                },
            ) from exc
        except Exception as exc:
            raise Room06GpuProbeError(
                f"runtime hook execution failed: {exc}",
                device_identity=identity,
                details={
                    "scene_readback": readback,
                    "instance_agent_map": instance_agent_map,
                },
            ) from exc
        report = {
            "schema": FULL_REPORT_SCHEMA,
            "mode": FULL_MODE,
            "status": "terminal_observation_captured",
            "input_digest": request["input_digest"],
            "package_digest": prepared["package"]["package_digest"],
            "device_identity": identity,
            "scene_readback": readback,
            "selected_robot_ids": list(
                prepared["package"]["plan"]["selected_robot_ids"]
            ),
            "instance_agent_map": instance_agent_map,
            **evidence,
            "formal_gates": dict(FORMAL_GATES),
        }
        if terminal_job is not None:
            from benchmark.room06_gpu_receipt_seam_v1 import build_terminal_payload

            try:
                report["map_ready_terminal_payload"] = build_terminal_payload(
                    gpu_report=report,
                    job=terminal_job,
                    evaluator_result=evaluator_result,
                    parameters=parameters,
                    orchestration_result=orchestration_result,
                )
            except (TypeError, ValueError) as exc:
                raise Room06GpuProbeError(
                    f"map-ready terminal handoff failed: {exc}"
                ) from exc
        canonical_json_bytes(report)
        if report_writer is not None:
            report_writer(report)
    return report


def execute(
    *,
    input_path: Path,
    input_sha256: str,
    output_dir: Path,
    scene_load_diagnostic_only: bool,
) -> int:
    """Run one non-overwriting attempt and preserve any diagnostic failure."""

    output = reserve_output_directory(output_dir)
    request: dict[str, Any] | None = None
    mode = SCENE_MODE if scene_load_diagnostic_only else FULL_MODE
    phase = "input_validation"
    try:
        request = load_probe_input(
            input_path,
            expected_file_sha256=input_sha256,
            scene_load_diagnostic_only=scene_load_diagnostic_only,
        )
        phase = "pre_simulation"
        prepared = (
            prepare_scene_diagnostic(request)
            if scene_load_diagnostic_only
            else prepare_full_task(request)
        )
        phase = "simulation"
        report = (
            run_scene_diagnostic(
                prepared,
                report_writer=lambda value: _write_new_json(
                    output / "report.json", value
                ),
            )
            if scene_load_diagnostic_only
            else run_full_task(
                prepared,
                capture_writer=lambda value: _write_new_json(
                    output / "runtime-hook-capture.json", value
                ),
                report_writer=lambda value: _write_new_json(
                    output / "report.json", value
                ),
            )
        )
        if not (output / "report.json").is_file():
            raise Room06GpuProbeError(
                "simulator returned without sealing the in-context report"
            )
        return 0
    except Exception as exc:
        error_details = {
            "exception_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        if isinstance(exc, Room06GpuProbeError):
            error_details.update(exc.details)
        failure = build_diagnostic_report(
            mode=mode,
            phase=phase,
            reason=str(exc) or type(exc).__name__,
            request=request,
            device_identity=(
                exc.device_identity
                if isinstance(exc, Room06GpuProbeError)
                else None
            ),
            details=error_details,
        )
        _write_new_json(output / "diagnostic.json", failure)
        print(f"BLOCKED: {failure['reason']}", file=sys.stderr, flush=True)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--scene-load-diagnostic-only",
        action="store_true",
        help="load and step only the frozen room_06 scene; cannot emit task evidence",
    )
    args = parser.parse_args(argv)
    try:
        return execute(
            input_path=args.input,
            input_sha256=args.input_sha256,
            output_dir=args.output_dir,
            scene_load_diagnostic_only=args.scene_load_diagnostic_only,
        )
    except Room06GpuProbeError as exc:
        parser.error(str(exc))
    raise AssertionError("unreachable")


__all__ = [
    "EXPECTED_SCENE_SHA256",
    "FORMAL_GATES",
    "FULL_MODE",
    "FULL_REPORT_SCHEMA",
    "HOOK_CAPTURE_SCHEMA",
    "INPUT_SCHEMA",
    "Room06GpuProbeError",
    "SCENE_MODE",
    "SCENE_REPORT_SCHEMA",
    "build_diagnostic_report",
    "build_frozen_selection",
    "build_scene_success_report",
    "canonical_digest",
    "compose_terminal_evidence",
    "directory_digest",
    "execute",
    "load_probe_input",
    "normalize_device_identity",
    "normalize_runtime_hook_capture",
    "prepare_full_task",
    "prepare_scene_diagnostic",
    "probe_input_digest",
    "reserve_output_directory",
]


if __name__ == "__main__":
    raise SystemExit(main())
