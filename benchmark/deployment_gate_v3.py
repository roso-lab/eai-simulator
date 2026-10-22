"""Fail-closed deployment qualification gate for the v3 release path.

The gate is intentionally read-only.  It checks provenance and declared
qualification records; it does not infer a physical result from imports,
controller names, or a transport configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

from .composition_v3 import CompositionManifestV1
from .skill_qualification_v3 import SkillQualificationV3, SkillQualificationStatus, SkillTrack


@dataclass(frozen=True)
class DeploymentGateV3:
    status: str
    ready: bool
    reasons: tuple[str, ...]
    checks: Mapping[str, bool]
    transport_mode: str
    require_shared_transport: bool = True

    def __post_init__(self) -> None:
        if self.status not in {"ready", "not_ready", "invalid"}:
            raise ValueError("invalid deployment gate status")
        if self.ready != (self.status == "ready"):
            raise ValueError("deployment ready/status mismatch")
        if self.ready and self.reasons:
            raise ValueError("ready deployment cannot carry failure reasons")
        if self.transport_mode not in {"legacy", "shared_v3", "unknown"}:
            raise ValueError("unregistered transport mode")
        if type(self.require_shared_transport) is not bool:
            raise ValueError("require_shared_transport must be bool")
        if any(type(value) is not bool for value in self.checks.values()):
            raise ValueError("deployment checks must be boolean")
        if self.ready and ((self.require_shared_transport and self.transport_mode != "shared_v3") or not self.checks or not all(self.checks.values())):
            raise ValueError("ready deployment requires all checks and shared_v3")
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "checks", dict(self.checks))

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "ready": self.ready, "reasons": list(self.reasons), "checks": dict(self.checks), "transport_mode": self.transport_mode}


def assess_deployment_v3(
    manifest: CompositionManifestV1,
    qualifications: Sequence[SkillQualificationV3],
    *,
    transport_policy: Mapping[str, Any] | None = None,
    required_imports: Sequence[str] = ("EAI", "EAI_assets", "EAI_hmrs"),
    required_asset_roots: Sequence[str] = ("EAI_USD_ROOT", "EAI_CONTROLLER_ROOT", "EAI_NVD_ASSET_ROOT"),
    require_shared_transport: bool = True,
) -> DeploymentGateV3:
    """Assess whether a run may enter the physical/rankable release path."""

    checks: dict[str, bool] = {}
    reasons: list[str] = []
    if not isinstance(manifest, CompositionManifestV1):
        return DeploymentGateV3("invalid", False, ("composition_manifest_type_invalid",), {"manifest_type": False}, "unknown", require_shared_transport=require_shared_transport)
    def resolved(value: Any) -> bool:
        return value is not None and str(value).strip().lower() not in {"", "unknown", "unresolved", "none", "null"}

    checks["manifest_identity"] = resolved(manifest.project_commit) and resolved(manifest.protocol_version) and resolved(manifest.scorebook_digest)
    if not checks["manifest_identity"]:
        reasons.append("composition_identity_unresolved")
    checks["composition_ids"] = all(resolved(getattr(manifest, name)) for name in ("scene_digest", "task_digest", "robot_digest", "controller_digest", "topology_digest", "oracle_id", "skill_id", "replay_id", "evaluator_id", "step_owner"))
    if not checks["composition_ids"]:
        reasons.append("composition_ids_unresolved")
    checks["timing"] = math.isfinite(float(manifest.sim_dt_s)) and manifest.sim_dt_s > 0 and type(manifest.control_substeps) is int and manifest.control_substeps > 0
    if not checks["timing"]:
        reasons.append("composition_timing_invalid")
    checks["import_requirements_declared"] = bool(required_imports)
    checks["import_provenance"] = checks["import_requirements_declared"] and all(resolved(manifest.actual_import_origins.get(name)) for name in required_imports)
    if not checks["import_provenance"]:
        reasons.append("actual_import_origins_missing")
    checks["asset_requirements_declared"] = bool(required_asset_roots)
    checks["asset_roots"] = checks["asset_requirements_declared"] and all(resolved(manifest.asset_roots.get(name)) for name in required_asset_roots)
    if not checks["asset_roots"]:
        reasons.append("asset_roots_missing")
    mode = str((transport_policy or {}).get("mode", "legacy"))
    checks["transport_policy"] = mode in {"legacy", "shared_v3"} and (not require_shared_transport or mode == "shared_v3")
    if mode == "shared_v3":
        policy = transport_policy or {}
        checks["shared_transport_parameters"] = type(policy.get("bits_per_tick")) is int and policy["bits_per_tick"] > 0 and type(policy.get("max_queue_bits")) is int and policy["max_queue_bits"] > 0
    else:
        checks["shared_transport_parameters"] = True
    if not checks["transport_policy"]:
        reasons.append("shared_transport_required" if require_shared_transport and mode != "shared_v3" else "transport_mode_unregistered")
    elif not checks["shared_transport_parameters"]:
        reasons.append("shared_transport_parameters_invalid")
    primary = tuple(item for item in qualifications if item.track is SkillTrack.PRIMARY_PHYSICAL)
    def matches_composition(item: SkillQualificationV3) -> bool:
        metadata = dict(item.metadata)
        return (
            item.skill_id == manifest.skill_id
            and resolved(metadata.get("robot_digest"))
            and metadata.get("robot_digest") == manifest.robot_digest
            and resolved(metadata.get("controller_digest"))
            and metadata.get("controller_digest") == manifest.controller_digest
            and math.isfinite(float(item.physics_dt_s)) and item.physics_dt_s > 0
            and math.isclose(float(item.physics_dt_s), float(manifest.sim_dt_s), rel_tol=1e-9, abs_tol=1e-12)
        )
    checks["primary_physical_qualification"] = bool(primary) and all(item.status is SkillQualificationStatus.QUALIFIED and matches_composition(item) for item in primary)
    if not checks["primary_physical_qualification"]:
        reasons.append("primary_physical_skill_qualification_missing")
    ready = all(checks.values())
    return DeploymentGateV3("ready" if ready else "not_ready", ready, tuple(reasons), checks, mode, require_shared_transport=require_shared_transport)


__all__ = ["DeploymentGateV3", "assess_deployment_v3"]
