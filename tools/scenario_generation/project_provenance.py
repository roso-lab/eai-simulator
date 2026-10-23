"""Minimal project-local metadata for an unavailable scenario backend.

Hashes here bind a launch request only.  They never authenticate a runtime
report and cannot upgrade historical evidence while the versioned scenario
backend is absent from the current project.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

if __package__ in (None, ""):
    import common
else:
    from . import common

REPO_ROOT = common.REPO_ROOT
PROVENANCE_SCHEMA = "heterocoord_project_execution_request_v1"
RUNTIME_DEFAULTS = {
    "steps": 0,
    "device": "cpu",
    "transport_mode": "none",
    "render_period_s": 0.0,
    "wall_timeout_s": 0.0,
    "archive_camera_frames": False,
    "inventory_label_id": None,
    "inventory_label_state": "visible",
}

_SOURCE_FILES = (
    "tools/scenario_generation/execute_episode.py",
    "tools/scenario_generation/batch_execute.py",
    "tools/scenario_generation/runtime_evidence.py",
    "tools/scenario_generation/project_provenance.py",
    "benchmark/runtime.py",
    "simulator.py",
)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def execution_code_provenance() -> dict[str, str]:
    """Hash only current project files; never resolve a legacy/external root."""

    result: dict[str, str] = {}
    for relative in _SOURCE_FILES:
        path = REPO_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"project execution source is missing: {relative}")
        result[relative] = file_sha256(path)
    return result


def runtime_options(**overrides: Any) -> dict[str, Any]:
    unknown = sorted(set(overrides) - set(RUNTIME_DEFAULTS))
    if unknown:
        raise ValueError(f"unknown runtime options: {unknown}")
    options = {**RUNTIME_DEFAULTS, **overrides}
    for name in ("render_period_s", "wall_timeout_s"):
        value = options[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    return options


def capture_request(scenario_path: str | Path, options: Mapping[str, Any]) -> dict[str, Any]:
    path = Path(scenario_path).resolve()
    if not path.is_file() or not path.is_relative_to(REPO_ROOT):
        raise ValueError("scenario input must be an existing file inside the current project")
    payload = {
        "schema": PROVENANCE_SCHEMA,
        "scenario_sha256": file_sha256(path),
        "code_provenance": execution_code_provenance(),
        "options": runtime_options(**dict(options)),
    }
    return {**payload, "sha256": canonical_sha256(payload),
            "captured_at": datetime.now(timezone.utc).isoformat()}


def validate_execution_provenance(
    attempt: Any, *, expected_sources: Any = None,
    expected_options: Any = None,
    check_runtime_files: bool = False,
) -> dict[str, Any]:
    del attempt, expected_sources, expected_options, check_runtime_files
    raise ValueError(
        "scenario execution provenance is unavailable: no current project "
        "scenario backend can authenticate or reuse an execution report"
    )


__all__ = [
    "PROVENANCE_SCHEMA", "RUNTIME_DEFAULTS", "canonical_sha256", "capture_request",
    "execution_code_provenance", "file_sha256",
    "runtime_options", "validate_execution_provenance",
]
