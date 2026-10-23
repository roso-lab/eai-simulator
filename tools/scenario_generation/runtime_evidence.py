"""Import runtime reports into versioned robot dossier evidence.

The importer is deliberately fail-closed. A successful process exit or a
non-empty physics trace is not enough to certify a capability: each accepted
record must carry the frozen scenario and dossier provenance.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime
from typing import Any, Mapping

RUNTIME_TIERS = {
    "spawn_validated": 5,
    "navigation_execution_validated": 6,
    "kinematic_execution_validated": 6,
    "physical_validated": 7,
}
_STATUS_BY_TIER = {5: "spawn_validated", 6: "kinematic_execution_validated", 7: "physical_validated"}
_TIER_BY_STATUS = {"registered": 1, "asset_available": 2, "structurally_validated": 3,
                   "placement_validated": 4, "spawn_validated": 5,
                   "navigation_execution_validated": 6, "kinematic_execution_validated": 6,
                   "physical_validated": 7, "blocked": 0}

def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("attempted_at must be an ISO-8601 string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("attempted_at must be valid ISO-8601") from exc
    return value


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def import_execution_attempt(attempt: Mapping[str, Any], *, scenario_sha256: str,
                              robot_dossier_snapshot_sha256: str, payload_policy_hash: str | None = None) -> dict[str, Any]:
    """Validate and normalize one batch_execute attempt as E6 evidence."""
    expected_scenario = _sha(scenario_sha256, "scenario_sha256")
    expected_dossier = _sha(robot_dossier_snapshot_sha256, "robot_dossier_snapshot_sha256")
    if not isinstance(attempt, Mapping):
        raise ValueError("execution attempt must be an object")
    actual_scenario = _sha(attempt.get("scenario_sha256"), "attempt.scenario_sha256")
    if actual_scenario != expected_scenario:
        raise ValueError("execution attempt scenario hash does not match frozen input")
    actual_dossier = _sha(attempt.get("robot_dossier_snapshot_sha256"),
                          "attempt.robot_dossier_snapshot_sha256")
    if actual_dossier != expected_dossier:
        raise ValueError("execution attempt dossier snapshot does not match frozen dossier")
    if attempt.get("status") != "completed" or not isinstance(attempt.get("outcome"), Mapping) \
            or attempt["outcome"].get("returncode") != 0:
        raise ValueError("execution attempt does not have a zero-return completed outcome")
    if payload_policy_hash is not None:
        expected_policy = _sha(payload_policy_hash, "payload_policy_hash")
        actual_policy = _sha(attempt.get("payload_policy_hash"), "attempt.payload_policy_hash")
        if actual_policy != expected_policy:
            raise ValueError("execution attempt payload policy does not match frozen policy")
    elif attempt.get("payload_policy_hash") is not None:
        raise ValueError("execution attempt has policy provenance but frozen policy is absent")
    report = attempt.get("report_body")
    validation = report.get("execution_validation") if isinstance(report, Mapping) else None
    if not isinstance(validation, Mapping) or validation.get("validated") is not True:
        raise ValueError("execution report is not a validated runtime result")
    tier = validation.get("tier")
    if tier not in {"navigation_execution_validated", "kinematic_execution_validated"}:
        raise ValueError("execution report must declare a supported E6 tier")
    from .project_provenance import validate_execution_provenance
    from EAI.scenario_spec.benchmark_physics import has_current_mass_audit

    provenance = validate_execution_provenance(attempt, check_runtime_files=True)
    if not has_current_mass_audit(report):
        raise ValueError("execution report lacks the current unscaled mass audit")
    attempted_at = _timestamp(attempt.get("attempted_at"))
    evidence_id = f"ev-runtime-{tier}-{actual_scenario[:16]}-{attempted_at.replace(':', '').replace('+', '')}"
    return {
        "evidence_id": evidence_id, "tier": "E6", "kind": "execution_attempt_import",
        "validation_status": tier, "result": "pass", "source_refs": [],
        "artifact": {"scenario_sha256": actual_scenario,
                     "robot_dossier_snapshot_sha256": actual_dossier,
                     "payload_policy_hash": payload_policy_hash,
                     "request_sha256": provenance["request"]["sha256"],
                     "runtime_sha256": provenance["runtime"]["sha256"],
                     "report_sha256": attempt["report_sha256"],
                     "runtime_options": provenance["request"]["options"],
                     "effective_conditions": provenance["runtime"]["effective"],
                     "attempted_at": attempted_at},
        "limitations": ["E6 navigation/kinematic evidence does not certify physical grasp, contact or payload dynamics"],
    }


def import_settle_report(report: Mapping[str, Any], *, scenario_sha256: str,
                         robot_dossier_snapshot_sha256: str) -> dict[str, Any]:
    """Validate a settle report as E5 evidence with exact provenance."""
    scenario = _sha(scenario_sha256, "scenario_sha256")
    dossier = _sha(robot_dossier_snapshot_sha256, "robot_dossier_snapshot_sha256")
    if not isinstance(report, Mapping) or report.get("status") != "spawn_validated":
        raise ValueError("settle report is not spawn_validated")
    if _sha(report.get("scenario_sha256"), "report.scenario_sha256") != scenario:
        raise ValueError("settle report scenario hash does not match frozen input")
    if _sha(report.get("robot_dossier_snapshot_sha256"), "report.robot_dossier_snapshot_sha256") != dossier:
        raise ValueError("settle report dossier snapshot does not match frozen dossier")
    entities = report.get("entities")
    if not isinstance(entities, Mapping) or not entities:
        raise ValueError("spawn_validated report must include entity drift measurements")
    drifts = []
    for name, item in entities.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"entity {name!r} drift record must be an object")
        drift = item.get("drift_m")
        if isinstance(drift, bool) or not isinstance(drift, (int, float)) or not math.isfinite(float(drift)) or drift < 0:
            raise ValueError(f"entity {name!r} drift_m must be finite and nonnegative")
        drifts.append(float(drift))
    return {"evidence_id": f"ev-spawn-{scenario[:16]}", "tier": "E5",
            "kind": "isaac_settle_import", "result": "pass", "source_refs": [],
            "artifact": {"scenario_sha256": scenario, "robot_dossier_snapshot_sha256": dossier,
                         "max_drift_m": max(drifts)},
            "limitations": ["settle does not validate navigation, manipulation or payload dynamics"]}


def import_physics_measurements(measurements: Mapping[str, Any], *, scenario_sha256: str,
                                robot_dossier_snapshot_sha256: str, expected_body_paths: list[str]) -> dict[str, Any]:
    """Accept finite post-solve measurements as an E5 observation artifact.

    This records coverage and finite values only. It cannot independently create
    E6/E7 capability evidence; a task verifier must establish the relevant event.
    """
    scenario = _sha(scenario_sha256, "scenario_sha256")
    dossier = _sha(robot_dossier_snapshot_sha256, "robot_dossier_snapshot_sha256")
    if not isinstance(measurements, Mapping) or not measurements:
        raise ValueError("physics measurements cannot be empty")
    if not expected_body_paths or any(not isinstance(path, str) or not path for path in expected_body_paths):
        raise ValueError("expected_body_paths must contain non-empty paths")
    body_keys = sorted(key for key in measurements if str(key).startswith("bodies/"))
    if not body_keys:
        raise ValueError("physics measurements must include bodies/* coverage")
    def finite(value: Any) -> bool:
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            return all(finite(item) for item in value)
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
    if not all(finite(value) for value in measurements.values()):
        raise ValueError("physics measurements contain non-finite values")
    return {"evidence_id": f"ev-physx-observation-{scenario[:16]}", "tier": "E5",
            "kind": "physx_measurement_import", "result": "pass", "source_refs": [],
            "artifact": {"scenario_sha256": scenario, "robot_dossier_snapshot_sha256": dossier,
                         "expected_body_paths": list(expected_body_paths), "measurement_keys": body_keys},
            "limitations": ["measurement coverage is not a task success or physical manipulation certificate"]}


def merge_runtime_evidence(dossier: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return a dossier copy with validated runtime evidence and monotonic gate status."""
    merged = copy.deepcopy(dict(dossier))
    current = {item.get("evidence_id") for item in merged.get("evidence", ()) if isinstance(item, Mapping)}
    evidence_id = evidence.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id or evidence_id in current:
        raise ValueError("runtime evidence id must be unique")
    tier = evidence.get("tier")
    if evidence.get("result") != "pass" or tier not in {"E5", "E6", "E7"}:
        raise ValueError("only passing E5-E7 runtime evidence can be merged")
    if tier == "E7":
        raise ValueError("E7 robot promotion requires a configuration-specific physical importer; "
                         "standalone component calibration and self-declared flags are insufficient")
    merged.setdefault("evidence", []).append(copy.deepcopy(dict(evidence)))
    tier_number = int(tier[1:])
    validation_status = evidence.get("validation_status")
    status = merged.setdefault("validation_status", {})
    status["runtime_validated"] = True
    if validation_status in {"spawn_validated", "physical_validated", "navigation_execution_validated", "kinematic_execution_validated"}:
        existing = status.get("overall", "registered")
        existing_tier = _TIER_BY_STATUS.get(existing, 1)
        if tier_number >= existing_tier:
            status["overall"] = validation_status
    return merged


__all__ = ["RUNTIME_TIERS", "import_execution_attempt", "import_physics_measurements", "import_settle_report", "merge_runtime_evidence"]
