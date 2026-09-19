"""Robot capability dossiers for benchmark composition and audit.

The dossier separates local declarations, asset-derived facts and runtime
validation. Payload fields are not manufacturer ratings; benchmark capacity is
supplied by an explicit policy and physical capacity is unknown until calibrated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

ROBOT_MORPHOLOGY = {
    "carter": ("wheeled", "differential", "ground"),
    "simple_car": ("wheeled", "planar_root_velocity", "ground"),
    "pepper": ("wheeled", "virtual_holonomic_joints", "ground"),
    "scout": ("wheeled", "differential", "ground"),
    "mushr_v2": ("wheeled", "ackermann", "ground"),
    "coco": ("wheeled", "ackermann", "ground"),
    "go2": ("quadruped", "legged", "ground"),
    "b2": ("quadruped", "legged", "ground"),
    "m20": ("wheel_legged", "legged_plus_wheels", "ground"),
    "lite3": ("quadruped", "legged", "ground"),
    "g1": ("biped", "legged", "ground"),
    "cf2x": ("aerial", "multirotor_thrust_torque", "aerial"),
    "iris": ("aerial", "multirotor_rotor", "aerial"),
    "pegasus": ("aerial", "multirotor_rotor", "aerial"),
}

CONTROLLER_META = {
    "carter": ("traditional", "twist_xy_yaw", ["vx", "vy", "wz"]),
    "simple_car": ("traditional", "planar_root_velocity", ["vx", "vy", "wz"]),
    "pepper": ("traditional", "virtual_joint_target", ["vx", "vy", "wz"]),
    "scout": ("traditional", "twist_xy_yaw", ["vx", "vy", "wz"]),
    "mushr_v2": ("traditional", "ackermann_twist", ["vx", "vy", "wz"]),
    "coco": ("traditional", "ackermann_twist", ["vx", "vy", "wz"]),
    "go2": ("rsl_rl", "velocity_command", ["vx", "vy", "wz"]),
    "b2": ("rsl_rl", "velocity_command", ["vx", "vy", "wz"]),
    "m20": ("rsl_rl", "velocity_command", ["vx", "vy", "wz"]),
    "lite3": ("rsl_rl", "velocity_command", ["vx", "vy", "wz"]),
    "g1": ("skrl", "policy_action", ["vx", "vy", "wz"]),
    "cf2x": ("skrl", "thrust_torque_action", ["thrust", "roll", "pitch", "yaw"]),
    "iris": ("pegasus", "position_or_rotor", ["x", "y", "z"]),
    "pegasus": ("pegasus", "position_or_rotor", ["x", "y", "z"]),
}

# Static USD observations are evidence for geometry only, never flight
# envelopes. Values are populated only for assets inspected in this workspace.
AERIAL_ASSET_OBSERVATIONS = {
    "cf2x": {"aabb_m": [0.109084, 0.076417, 0.027176], "declared_mass_kg": 0.0282},
    "iris": {"aabb_m": [0.521347, 0.477714, 0.113668], "declared_mass_kg": None},
    "pegasus": {"aabb_m": [0.300734, 0.377184, 0.097417], "declared_mass_kg": 1.504},
}


MOUNT_POSITIONS = {
    "ur5": {"go2": (0.0, 0.0, 0.056), "b2": (0.0, 0.0, 0.085),
            "m20": (0.0, 0.0, 0.0852), "scout": (0.0, 0.0, 0.1162),
            "lite3": (0.0, 0.0, 0.073)},
    "z1": {"carter": (0.0, 0.0, 0.414), "go2": (0.0, 0.0, 0.056),
           "b2": (0.0, 0.0, 0.085), "m20": (0.0, 0.0, 0.0852),
           "scout": (0.0, 0.0, 0.1162), "lite3": (0.0, 0.0, 0.073)},
    "kinova_gen3": {"go2": (0.0, 0.0, 0.056), "b2": (0.0, 0.0, 0.085),
                    "m20": (0.0, 0.0, 0.0852), "scout": (0.0, 0.0, 0.1162),
                    "lite3": (0.0, 0.0, 0.073)},
}

MOUNT_EFFECTS = {
    "ur5": {"go2": 1.0, "b2": 1.0, "m20": 1.0, "scout": 1.0, "lite3": 1.0},
    "z1": {"carter": 1.0, "go2": 1.0, "b2": 1.0, "m20": 1.0, "scout": 1.0, "lite3": 1.0},
    "kinova_gen3": {"go2": 1.0, "b2": 1.0, "m20": 1.0, "scout": 1.0, "lite3": 1.0},
}
LOCAL_SOURCES = {
    "catalog": "source/EAI/EAI/hmrs_env/env_diy/catalog.py",
    "builder": "source/EAI_hmrs/EAI_hmrs/env_builder.py",
    "profiles": "source/EAI/EAI/scenario_spec/schema.py",
    "controllers": "source/EAI_hmrs/EAI_hmrs/env_builder.py",
    "mount_ur5": "source/EAI_assets/EAI_assets/robots/ur5_mount.py",
    "mount_z1": "source/EAI_assets/EAI_assets/robots/z1_mount.py",
}


def _claim(value: Any, *, unit: str | None = None, status: str = "declared",
           confidence: float = 0.5, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    if value is None:
        return {"value": None, "unit": unit, "status": "unknown", "confidence": 0.0,
                "evidence_refs": []}
    return {"value": value, "unit": unit, "status": status,
            "confidence": confidence, "evidence_refs": evidence_refs or []}


def _sensor_mounts(robot_type: str) -> list[str]:
    if robot_type in {"cf2x", "iris", "pegasus"}:
        return ["built_in_camera", "built_in_lidar", "base_navigation_sensors"]
    if robot_type == "simple_car":
        return ["octagon_aio_c01", "mid360"]
    if robot_type == "mushr_v2":
        return ["realsense_d455", "lidar", "octagon_aio_c01"]
    if robot_type in {"carter", "go2", "b2", "m20", "scout", "coco", "lite3"}:
        return ["orsus", "realsense_d455", "lidar", "octagon_aio_c01"]
    if robot_type == "pepper":
        return ["realsense_d455"]
    return []


def _notes(robot_type: str) -> list[str]:
    notes: list[str] = []
    if robot_type == "b2":
        notes += ["separate B2 morphology/controller/asset; do not treat as Go2",
                  "asset path can select canonical or source USD depending on local availability"]
    if robot_type == "go2":
        notes.append("quadruped legged base; payload effects require a declared experiment policy")
    if robot_type == "m20":
        notes.append("wheel-legged base with hip/knee and wheel joint groups")
    if robot_type in {"cf2x", "iris", "pegasus"}:
        notes += ["benchmark aerial_scan has goal-position adapters for cf2x, iris and pegasus; explicit scan heading is limited to paired Iris/Pegasus position controllers",
                  "aerial size, wind tolerance and outdoor suitability are unverified locally"]
    if robot_type == "simple_car":
        notes.append("current ROS cmd_vel bridge does not preserve lateral velocity")
    if robot_type == "pepper":
        notes.append("holonomic behavior is implemented through virtual joint targets")
    if robot_type == "g1":
        notes.append("current builder asset is the configured G1 Nucleus asset, not a claimed Inspire variant")
    return notes


def robot_dossier(robot_type: str, *, attachments: list[str] | None = None,
                  physics_context: str = "benchmark") -> dict[str, Any]:
    """Build one JSON-ready dossier from the connected simulator catalog."""
    from EAI.hmrs_env.env_diy import catalog
    from EAI.scenario_spec.schema import ROBOT_PROFILES
    from tools.scenario_generation.manufacturer_specs import product_references

    if robot_type not in ROBOT_MORPHOLOGY or robot_type not in ROBOT_PROFILES:
        raise ValueError(f"unknown connected robot type {robot_type!r}")
    if physics_context not in {"benchmark", "simulator"}:
        raise ValueError("physics_context must be benchmark or simulator")
    selected = list(catalog.validate_attachment_types(robot_type, attachments or []))
    attachment_catalog = catalog.attachment_catalog()
    supported = [entry.name for entry in (attachment_catalog | catalog.tool_catalog()).values()
                 if entry.supports(robot_type) and not entry.visual_only]
    manipulator = [key for key in ("ur5", "ur5_bare", "z1", "kinova_gen3") if key in selected]
    mount_models = {key: "ur5" if key == "ur5_bare" else key for key in manipulator}
    simulator_mount_factors = {key: MOUNT_EFFECTS[model].get(robot_type)
                              for key, model in mount_models.items() if robot_type in MOUNT_EFFECTS[model]}
    mount_factors = {key: 1.0 if physics_context == "benchmark" else factor
                     for key, factor in simulator_mount_factors.items()}
    morphology, locomotion, domain = ROBOT_MORPHOLOGY[robot_type]
    controller_family, command_semantics, command_labels = CONTROLLER_META[robot_type]
    from tools.scenario_generation.arm_deployment_evidence import deployment_evidence_for_configuration
    manipulator_evidence = {
        key: deployment_evidence_for_configuration(robot_type, key)
        for key in manipulator
    }
    aerial = domain == "aerial"
    aerial_obs = AERIAL_ASSET_OBSERVATIONS.get(robot_type)
    evidence_id = "ev-local-catalog"
    geometry_claims = {
        "length_m": _claim(None, unit="m"), "width_m": _claim(None, unit="m"),
        "height_m": _claim(None, unit="m"),
    }
    if aerial_obs:
        geometry_claims["aabb_m"] = _claim(aerial_obs["aabb_m"], unit="m", status="measured",
                                              confidence=0.8, evidence_refs=["ev-static-usd"])
    evidence = [{"evidence_id": evidence_id, "tier": "E1",
                 "kind": "catalog_and_asset_config", "result": "pass",
                 "source_refs": list(LOCAL_SOURCES),
                 "limitations": ["static declaration; no Isaac spawn or physical load validation"]}]
    if aerial_obs:
        evidence.append({"evidence_id": "ev-static-usd", "tier": "E3",
                         "kind": "usd_static_bbox_and_massapi", "result": "pass",
                         "source_refs": ["local_asset_observation"],
                         "limitations": ["default-pose AABB; not rotor sweep, mounted payload, wind or safe transit envelope"]})
    config_observation = None
    sources = [{"source_id": key, "type": "repository", "locator": value,
                "revision": "working-tree", "immutable": False,
                "sha256": hashlib.sha256((REPO_ROOT / value).read_bytes()).hexdigest()}
               for key, value in LOCAL_SOURCES.items()]
    from tools.scenario_generation.robot_static_config import ROBOT_CONFIG_SOURCES, inspect_robot_config
    if robot_type in ROBOT_CONFIG_SOURCES:
        config_observation = inspect_robot_config(robot_type, repo_root=REPO_ROOT)
        sources.append({"source_id": "actuator_config", "type": "repository",
                        "locator": ROBOT_CONFIG_SOURCES[robot_type],
                        "sha256": config_observation["source"]["sha256"]})
        evidence.append({"evidence_id": "ev-actuator-config", "tier": "E1",
                         "kind": "source_literal_extracted", "result": "pass",
                         "source_refs": ["actuator_config"],
                         "limitations": config_observation["evidence"]["limitations"]})
    if aerial_obs:
        sources.append({"source_id": "local_asset_observation", "type": "archived_observation",
                        "scope": "local alternative asset; default builder equivalence unverified"
                                 if robot_type == "cf2x" else "local asset; runtime equivalence unverified",
                        "sha256": None})
    return {
        "schema_id": "eai.robot_dossier",
        "schema_version": "1.0",
        "dossier_id": f"{robot_type}@catalog",
        "robot_key": robot_type,
        "identity": {"display_name": catalog.robot_label(robot_type),
                     "manufacturer": None, "model": None, "revision": None},
        "morphology": {
            "family": morphology, "locomotion": locomotion, "domain": domain,
            "planning_radius_m": _claim(ROBOT_PROFILES[robot_type]["radius_m"], unit="m",
                                         status="derived", confidence=0.6, evidence_refs=[evidence_id]),
            "spawn_z_m": _claim(ROBOT_PROFILES[robot_type]["spawn_z_m"], unit="m",
                                 status="declared", confidence=0.8, evidence_refs=[evidence_id]),
            "dimensions": geometry_claims,
        },
        "physics": {
            "mass_kg": _claim(None, unit="kg"),
            "center_of_mass_m": _claim(None, unit="m"),
            "inertia_kg_m2": _claim(None, unit="kg m^2"),
            "support_geometry_status": "unknown",
            "link_field_status": "unknown",
            "aggregate_com_inertia_status": "unknown",
            "installation_geometry_status": "declared" if manipulator else "not_applicable",
            "physical_capacity_status": "unknown",
            "provenance_note": "底盘质量、重心和惯量必须由对应型号资产或实测数据提供；目录注册不填充默认值。",
        },
        "controller": {"default_cfg": catalog.default_controller_cfg(robot_type),
                       "family": controller_family, "command_semantics": command_semantics,
                       "command_labels": command_labels, "status": "catalog_declared",
                       "actuator_groups": _claim(
                           config_observation["actuators"] if config_observation else None,
                           status="declared" if config_observation else "unknown",
                           evidence_refs=["ev-actuator-config"] if config_observation else [],
                       ),
                       "evidence_refs": [evidence_id]},
        "capabilities": {
            "navigation": _claim(True, status="declared", confidence=0.8, evidence_refs=[evidence_id]),
            "aerial_scan": _claim(aerial, status="declared", confidence=0.8, evidence_refs=[evidence_id]),
            "manipulation": _claim(bool(manipulator), status="declared", confidence=0.8, evidence_refs=[evidence_id]),
            "joint_transport_participant": _claim(bool(manipulator) and not aerial,
                                                   status="declared", confidence=0.6, evidence_refs=[evidence_id]),
        },
        "supported_attachments": supported,
        "selected_attachments": selected,
        "sensing": {
            "mounts": _sensor_mounts(robot_type),
            "capabilities": {
                "camera": _claim(robot_type in catalog.BUILTIN_CAMERA_ROBOTS or
                                   bool({"realsense_d455", "orsus", "octagon_aio_c01"} & set(selected)),
                                   status="declared", confidence=0.6, evidence_refs=[evidence_id]),
                "lidar": _claim(aerial or robot_type == "simple_car" or
                                  bool({"lidar", "orsus", "octagon_aio_c01", "mid360"} & set(selected)),
                                  status="declared", confidence=0.6, evidence_refs=[evidence_id]),
            },
        },
        "manipulation": {
            "physics_context": physics_context,
            "mode": "kinematic_declared" if manipulator else "none",
            "attachments": [{"type": key, "mount_mass_scale": mount_factors.get(key),
                             "mount_position_m": MOUNT_POSITIONS[mount_models[key]].get(robot_type),
                             "manufacturer_references": product_references(mount_models[key]),
                             "gripper_present_in_asset": key == "z1",
                             "gripper_present_in_assembly": key != "ur5_bare",
                             "default_gripper": ("robotiq_2f85" if key == "ur5" else None if key == "ur5_bare"
                                     else "z1_gripper" if key == "z1" else None),
                             "physical_grasp_status": "unknown",
                             "deployment_evidence": manipulator_evidence[key]} for key in manipulator],
        },
        "sensor_mounts": _sensor_mounts(robot_type),
        "payload": {
            "physics_context": physics_context,
            "capacity_kg": _claim(None, unit="kg"),
            "manufacturer_references": product_references(robot_type),
            "policy_required": True,
            "mount_mass_scale": {key: _claim(value, status="declared", confidence=0.9,
                                               evidence_refs=[evidence_id])
                                 for key, value in mount_factors.items()},
            "simulator_tuning_mass_scale": simulator_mount_factors,
            "physical_capacity_status": "unknown",
            "benchmark_mount_mass_scale": 1.0,
            "benchmark_mount_inertia_scale": 1.0,
            "benchmark_physics_contract": "asset_authored_unscaled_v1",
        },
        "environment_suitability": {
            scene: {"status": "conditional", "score": None, "required_evidence_tier": "E4",
                    "reasons": [reason]}
            for scene, reason in {
                "factory": "verify route, clearance and attachment geometry",
                "warehouse_nvd": "verify route, asset availability and payload policy",
                "modern_house_nvd": "verify indoor clearance and sensor path",
            }.items()
        },
        "evidence": evidence,
        "sources": sources,
        "validation_status": {
            "overall": "registered", "runtime_validated": False,
            "per_gate": {"catalog": "pass", "source_integrity": "unknown", "asset_resolution": "unknown",
                          "structural": "unknown", "geometry": "unknown", "spawn": "unknown",
                          "execution": "unknown", "manipulation_physical": "unknown"},
        },
        "uncertainty": [
            "manufacturer references require exact variant matching and do not establish simulator capacity",
            "physical grasp, contact, IK and disturbance tolerance are not certified",
            "sensor availability does not prove sensor observations are consumed by a task",
        ],
        "notes": _notes(robot_type),
    }


def dossier_snapshot_hash(dossier: dict[str, Any]) -> str:
    """Hash canonical dossier JSON for experiment provenance."""
    payload = json.dumps(dossier, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def all_dossiers_snapshot_hash(dossiers: dict[str, dict[str, Any]] | None = None) -> str:
    data = dossiers if dossiers is not None else all_robot_dossiers()
    return dossier_snapshot_hash(data)
def validate_dossier(dossier: dict[str, Any]) -> list[str]:
    """Return validation errors; unknown Claims are required to fail closed."""
    errors: list[str] = []
    if dossier.get("schema_id") != "eai.robot_dossier":
        errors.append("schema_id must be eai.robot_dossier")
    if dossier.get("schema_version") != "1.0":
        errors.append("unsupported schema_version")
    from EAI.hmrs_env.env_diy.catalog import robot_keys as catalog_robot_keys
    if dossier.get("robot_key") not in set(catalog_robot_keys()):
        errors.append(f"robot_key is not a connected catalog robot: {dossier.get('robot_key')!r}")
    evidence_items = dossier.get("evidence", [])
    if any(isinstance(item, dict) and item.get("tier") == "E7" for item in evidence_items):
        errors.append("E7 robot evidence requires a configuration-specific physical importer; "
                      "standalone calibration cannot certify a robot configuration")
    raw_ids = [item.get("evidence_id") for item in evidence_items if isinstance(item, dict)]
    if any(not isinstance(item, str) or not item for item in raw_ids):
        errors.append("evidence_id must be a non-empty string")
    if len(raw_ids) != len(set(raw_ids)):
        errors.append("evidence_id values must be unique")
    evidence_ids = set(raw_ids)
    tier_order = {f"E{i}": i for i in range(8)}
    status_min_tier = {"registered": 1, "asset_available": 2, "structurally_validated": 3,
                       "placement_validated": 4, "spawn_validated": 5,
                       "navigation_execution_validated": 6, "kinematic_execution_validated": 6,
                       "physical_validated": 7, "blocked": 0}
    overall = dossier.get("validation_status", {}).get("overall")
    max_tier = max((tier_order.get(item.get("tier"), -1) for item in evidence_items
                    if isinstance(item, dict) and item.get("result") == "pass"), default=-1)
    if overall in status_min_tier and status_min_tier[overall] > max_tier:
        errors.append(f"validation_status.overall {overall!r} exceeds passing evidence tier E{max_tier}")

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if {"value", "status", "confidence", "evidence_refs"} <= value.keys():
                status = value.get("status")
                confidence = value.get("confidence")
                if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                    errors.append(f"{path}: confidence must be in [0,1]")
                if status == "unknown" and (value.get("value") is not None or confidence != 0 or value.get("evidence_refs")):
                    errors.append(f"{path}: unknown Claim must have null value, zero confidence and no evidence")
                for ref in value.get("evidence_refs", []):
                    if ref not in evidence_ids:
                        errors.append(f"{path}: unknown evidence reference {ref!r}")
            for key, child in value.items():
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(dossier, "$" )
    return errors
def all_robot_dossiers() -> dict[str, dict[str, Any]]:
    from EAI.hmrs_env.env_diy import catalog
    return {robot_type: robot_dossier(robot_type) for robot_type in catalog.robot_keys()}


def sanitized_robot_dossiers() -> dict[str, dict[str, Any]]:
    """Return logical dossier claims safe to expose to a candidate provider."""
    import copy
    dossiers = copy.deepcopy(all_robot_dossiers())
    for dossier in dossiers.values():
        dossier.pop("sources", None)
        for evidence in dossier.get("evidence", []):
            evidence.pop("source_refs", None)
            evidence["artifact"] = {"logical_ref": "local_catalog_snapshot"}
    return dossiers


__all__ = ["MOUNT_EFFECTS", "MOUNT_POSITIONS", "ROBOT_MORPHOLOGY", "all_dossiers_snapshot_hash", "all_robot_dossiers", "dossier_snapshot_hash", "robot_dossier", "sanitized_robot_dossiers", "validate_dossier"]
