"""Execution/environment fingerprint for the revised benchmark composition."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
import math
import os
import subprocess
import sys
from typing import Any, Mapping


COMPOSITION_SCHEMA_VERSION = "heterocoord.composition.v1"


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CompositionManifestV1:
    schema_version: str
    protocol_version: str
    scorebook_digest: str
    project_commit: str
    entry_mode: str
    requested_python: str
    actual_import_origins: Mapping[str, str]
    source_hashes: Mapping[str, str]
    asset_roots: Mapping[str, str | None]
    scene_digest: str
    task_digest: str
    robot_digest: str
    controller_digest: str
    topology_digest: str
    oracle_id: str
    skill_id: str
    replay_id: str
    evaluator_id: str
    step_owner: str
    sim_dt_s: float
    control_substeps: int
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != COMPOSITION_SCHEMA_VERSION:
            raise ValueError(f"unexpected composition schema: {self.schema_version}")
        for name in ("protocol_version", "scorebook_digest", "project_commit", "entry_mode", "requested_python", "step_owner", "oracle_id", "skill_id", "replay_id", "evaluator_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not math.isfinite(float(self.sim_dt_s)) or self.sim_dt_s <= 0:
            raise ValueError("sim_dt_s must be finite and positive")
        if type(self.control_substeps) is not int or self.control_substeps <= 0:
            raise ValueError("sim_dt_s and control_substeps must be positive")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def digest(self) -> str:
        return _digest(self.to_dict())


def current_git_commit(cwd: str) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def build_composition_manifest(*, cwd: str, protocol_version: str, scorebook_digest: str, entry_mode: str, actual_import_origins: Mapping[str, str] | None = None, source_hashes: Mapping[str, str] | None = None, asset_roots: Mapping[str, str | None] | None = None, scene_digest: str = "unresolved", task_digest: str = "unresolved", robot_digest: str = "unresolved", controller_digest: str = "unresolved", topology_digest: str = "unresolved", oracle_id: str = "unresolved", skill_id: str = "unresolved", replay_id: str = "unresolved", evaluator_id: str = "unresolved", step_owner: str = "unresolved", sim_dt_s: float = 0.02, control_substeps: int = 1, metadata: Mapping[str, Any] | None = None) -> CompositionManifestV1:
    """Build a manifest without importing Isaac/ROS or mutating the environment."""

    roots = dict(asset_roots or {})
    for name in ("EAI_USD_ROOT", "EAI_CONTROLLER_ROOT", "EAI_NVD_ASSET_ROOT"):
        roots.setdefault(name, os.environ.get(name))
    return CompositionManifestV1(
        schema_version=COMPOSITION_SCHEMA_VERSION,
        protocol_version=protocol_version,
        scorebook_digest=scorebook_digest,
        project_commit=current_git_commit(cwd),
        entry_mode=entry_mode,
        requested_python=sys.executable,
        actual_import_origins=dict(actual_import_origins or {}),
        source_hashes=dict(source_hashes or {}),
        asset_roots=roots,
        scene_digest=scene_digest,
        task_digest=task_digest,
        robot_digest=robot_digest,
        controller_digest=controller_digest,
        topology_digest=topology_digest,
        oracle_id=oracle_id,
        skill_id=skill_id,
        replay_id=replay_id,
        evaluator_id=evaluator_id,
        step_owner=step_owner,
        sim_dt_s=sim_dt_s,
        control_substeps=control_substeps,
        metadata=dict(metadata or {}),
    )


__all__ = ["COMPOSITION_SCHEMA_VERSION", "CompositionManifestV1", "build_composition_manifest", "current_git_commit"]
