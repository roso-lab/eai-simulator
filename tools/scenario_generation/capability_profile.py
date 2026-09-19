"""Configuration-level capability profiles for benchmark robot selection.

The profile is an evidence view, not a robot ranking.  Static dossier claims
populate hard constraints and identity; radar axes receive values only from
versioned measurements.  Missing measurements remain ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping


RADAR_AXES = (
    "scene_traversability",
    "motion_efficiency",
    "near_observation",
    "spatial_observation",
    "task_reliability",
    "information_service",
    "operating_duration",
    "operation_service",
)

_CONTROL_ADAPTERS = {
    "differential": "differential_twist",
    "planar_root_velocity": "planar_root_velocity",
    "virtual_holonomic_joints": "holonomic_joint_target",
    "ackermann": "ackermann_curvature",
    "legged": "legged_velocity_policy",
    "legged_plus_wheels": "wheel_legged_velocity_policy",
    "multirotor_thrust_torque": "multirotor_thrust_torque",
    "multirotor_rotor": "multirotor_position_or_rotor",
}


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def configuration_id(*, robot_key: str, controller_cfg: str,
                     locomotion_mode: str, sensors: Iterable[str],
                     attachments: Iterable[str], software_revision: str = "working-tree") -> str:
    identity = {
        "robot_key": robot_key,
        "controller_cfg": controller_cfg,
        "locomotion_mode": locomotion_mode,
        "sensors": sorted(set(sensors)),
        "attachments": sorted(set(attachments)),
        "software_revision": software_revision,
    }
    return f"{robot_key}@{_canonical_hash(identity)[:16]}"


def _unknown_axis(axis: str) -> dict[str, Any]:
    return {"axis": axis, "status": "unknown", "normalized": None,
            "measurements": [], "reason": "no configuration-specific measurement"}


def _validate_measurement(item: Mapping[str, Any]) -> dict[str, Any]:
    required = ("axis", "raw_value", "unit", "protocol_id", "sample_count", "evidence_id")
    missing = [key for key in required if key not in item]
    if missing:
        raise ValueError(f"capability measurement missing fields: {', '.join(missing)}")
    axis = item["axis"]
    if axis not in RADAR_AXES:
        raise ValueError(f"unsupported radar axis {axis!r}")
    raw_value = item["raw_value"]
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)) or not math.isfinite(float(raw_value)):
        raise ValueError(f"{axis}: raw_value must be finite and numeric")
    normalized = item.get("normalized")
    if isinstance(normalized, bool) or not isinstance(normalized, (int, float)) \
            or not math.isfinite(float(normalized)) or not 0 <= float(normalized) <= 1:
        raise ValueError(f"{axis}: normalized must be in [0, 1]")
    if not isinstance(item["unit"], str) or not item["unit"]:
        raise ValueError(f"{axis}: unit is required")
    if not isinstance(item["protocol_id"], str) or not item["protocol_id"]:
        raise ValueError(f"{axis}: protocol_id is required")
    count = item["sample_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"{axis}: sample_count must be a positive integer")
    if not isinstance(item["evidence_id"], str) or not item["evidence_id"]:
        raise ValueError(f"{axis}: evidence_id is required")
    evidence_tier = item.get("evidence_tier", "E0")
    if evidence_tier not in {f"E{i}" for i in range(8)}:
        raise ValueError(f"{axis}: unsupported evidence_tier")
    return {"axis": axis, "status": "measured", "raw_value": raw_value,
            "unit": item["unit"], "normalized": float(normalized),
            "protocol_id": item["protocol_id"], "sample_count": count,
            "evidence_id": item["evidence_id"],
            "evidence_tier": evidence_tier,
            "conditions": dict(item.get("conditions") or {})}


def profile_for_dossier(dossier: Mapping[str, Any], measurements: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    robot_key = dossier.get("robot_key")
    controller = dossier.get("controller") or {}
    morphology = dossier.get("morphology") or {}
    sensing = dossier.get("sensing") or {}
    attachments = list(dossier.get("selected_attachments") or ())
    sensors = list(dossier.get("sensor_mounts") or sensing.get("mounts") or ())
    controller_cfg = controller.get("default_cfg")
    locomotion_mode = morphology.get("locomotion")
    if not all(isinstance(value, str) and value for value in (robot_key, controller_cfg, locomotion_mode)):
        raise ValueError("dossier is missing configuration identity fields")
    identity = {
        "configuration_id": configuration_id(
            robot_key=robot_key, controller_cfg=controller_cfg,
            locomotion_mode=locomotion_mode, sensors=sensors,
            attachments=attachments),
        "robot_key": robot_key,
        "controller_cfg": controller_cfg,
        "controller_family": controller.get("family"),
        "locomotion_mode": locomotion_mode,
        "sensors": sorted(set(sensors)),
        "attachments": sorted(set(attachments)),
    }
    from tools.scenario_generation.arm_deployment_evidence import (
        PROFILE_MANIPULATORS,
        deployment_evidence_for_robot,
    )
    deployment_evidence = deployment_evidence_for_robot(robot_key)
    non_manipulator_attachments = [item for item in attachments if item not in PROFILE_MANIPULATORS]
    for group in ("env_diy_variants", "candidate_only"):
        for item in deployment_evidence[group]:
            candidate_attachments = [*non_manipulator_attachments, item["manipulator"]]
            item["configuration_identity"] = {
                "configuration_id": configuration_id(
                    robot_key=robot_key,
                    controller_cfg=controller_cfg,
                    locomotion_mode=locomotion_mode,
                    sensors=sensors,
                    attachments=candidate_attachments,
                ),
                "host_controller_cfg": controller_cfg,
                "attachments": sorted(set(candidate_attachments)),
                "selected_in_profile": item["manipulator"] in attachments,
            }
    by_axis = {axis: _unknown_axis(axis) for axis in RADAR_AXES}
    normalized_measurements = []
    protocol_checks = []
    for raw in measurements:
        if raw.get("kind") == "protocol_check":
            if (raw.get("configuration_id") != identity["configuration_id"]
                    or raw.get("status") != "passed" or raw.get("evidence_tier") != "E6"
                    or not isinstance(raw.get("scope"), Mapping) or not raw.get("protocol_id")
                    or not raw.get("evidence_id")):
                raise ValueError("protocol check does not match this configuration_id or evidence contract")
            protocol_checks.append(dict(raw))
            continue
        measurement = _validate_measurement(raw)
        bound_configuration = measurement["conditions"].get("configuration_id")
        if bound_configuration is not None and bound_configuration != identity["configuration_id"]:
            raise ValueError("measurement configuration_id differs from this profile")
        by_axis[measurement["axis"]]["status"] = "measured"
        by_axis[measurement["axis"]]["reason"] = None
        by_axis[measurement["axis"]]["measurements"].append(measurement)
        normalized_measurements.append(measurement)
    for axis, value in by_axis.items():
        values = [item["normalized"] for item in value["measurements"]]
        if values:
            value["normalized"] = sum(values) / len(values)
    evidence_tier_order = {f"E{i}": i for i in range(8)}
    passing_evidence_tier = max(
        (evidence_tier_order.get(item.get("tier"), -1)
         for item in dossier.get("evidence", ())
         if isinstance(item, Mapping) and item.get("result") == "pass"),
        default=-1,
    )
    measured_tiers = [evidence_tier_order[item["evidence_tier"]]
                      for item in [*normalized_measurements, *protocol_checks]]
    if measured_tiers:
        passing_evidence_tier = max(passing_evidence_tier, *measured_tiers)
    hard_constraints = {
        "domain": morphology.get("domain"),
        "controller_semantics": controller.get("command_semantics"),
        "supports_aerial_scan": (dossier.get("capabilities", {}).get("aerial_scan", {}).get("value") is True),
        "supports_navigation": (dossier.get("capabilities", {}).get("navigation", {}).get("value") is True),
        "sensor_capabilities": {
            key: claim.get("value") is True
            for key, claim in (sensing.get("capabilities") or {}).items()
            if isinstance(claim, Mapping)
        },
        "attachments": sorted(set(attachments)),
        "dynamic_manipulation_configurations": [
            item["configuration_identity"]["configuration_id"]
            for item in deployment_evidence["env_diy_variants"]
            if item["dynamic_manipulation_admission"]["status"] == "allowed"
            and item["mobile_configuration_e7"]["status"] == "passed"
        ],
        "passing_evidence_tier": f"E{passing_evidence_tier}" if passing_evidence_tier >= 0 else "E0",
    }
    return {
        "schema": "eai.robot_capability_profile",
        "version": 2,
        "identity": identity,
        "hard_constraints": hard_constraints,
        "deployment_evidence": deployment_evidence,
        "radar": [by_axis[axis] for axis in RADAR_AXES],
        "protocol_checks": protocol_checks,
        "protocol_check_count": len(protocol_checks),
        "measurement_count": len(normalized_measurements),
        "measurement_protocols": sorted({item["protocol_id"] for item in normalized_measurements}),
        "passing_evidence_tier": f"E{passing_evidence_tier}" if passing_evidence_tier >= 0 else "E0",
        "limitations": [
            "radar values are configuration-specific measurements, not manufacturer ratings",
            "unknown axes are not zero and cannot be used as measured capability",
            "radar values are not success probabilities and are not combined by polygon area",
            "single protocol checks retain scoped raw metrics and do not fill radar axes",
            "stationary mount evidence and component E7 evidence do not admit dynamic mobile manipulation",
            "payload, grasp force, collision margin, wind resistance and load dynamics remain unknown",
        ],
    }


def all_robot_capability_profiles(dossiers: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {key: profile_for_dossier(dossier) for key, dossier in dossiers.items()}


def configuration_coverage_matrix(dossiers: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the explicit control/sensor coverage table for each configuration."""
    profiles = all_robot_capability_profiles(dossiers)
    rows = []
    for robot_key in sorted(profiles):
        profile = profiles[robot_key]
        identity = profile["identity"]
        locomotion = identity["locomotion_mode"]
        adapter = _CONTROL_ADAPTERS.get(locomotion)
        if adapter is None:
            raise ValueError(f"{robot_key}: no control adapter for locomotion mode {locomotion!r}")
        rows.append({
            "robot_key": robot_key,
            "configuration_id": identity["configuration_id"],
            "controller_cfg": identity["controller_cfg"],
            "controller_semantics": profile["hard_constraints"]["controller_semantics"],
            "locomotion_mode": locomotion,
            "control_adapter": adapter,
            "domain": profile["hard_constraints"]["domain"],
            "sensors": list(identity["sensors"]),
            "attachments": list(identity["attachments"]),
            "radar_status": {axis["axis"]: axis["status"] for axis in profile["radar"]},
            "manipulator_deployment": {
                item["manipulator"]: item["mount_evidence"]["status"]
                for group in ("env_diy_variants", "candidate_only")
                for item in profile["deployment_evidence"][group]
            },
        })
    return rows


def validate_configuration_coverage(dossiers: Mapping[str, Mapping[str, Any]],
                                    expected_robot_keys: Iterable[str] | None = None) -> list[dict[str, Any]]:
    rows = configuration_coverage_matrix(dossiers)
    actual = {row["robot_key"] for row in rows}
    expected = set(expected_robot_keys) if expected_robot_keys is not None else actual
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ValueError(f"configuration coverage mismatch; missing={missing}, extra={extra}")
    if len(rows) != len(actual):
        raise ValueError("configuration coverage contains duplicate robot keys")
    return rows


__all__ = [
    "RADAR_AXES", "all_robot_capability_profiles", "configuration_coverage_matrix",
    "configuration_id", "profile_for_dossier", "validate_configuration_coverage",
]
