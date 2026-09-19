"""Build one auditable Factory smoke input for every connected EAI robot.

The output is a configuration matrix, not an execution result.  It keeps the
controller, sensor/tool, spawn condition, protocol primitive, and static
blockers together so a later Isaac run cannot silently substitute a different
configuration.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for package_root in (REPO_ROOT / "source" / "EAI", REPO_ROOT / "source" / "EAI_assets",
                     REPO_ROOT / "source" / "EAI_hmrs"):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

SCHEMA = "eai.robot_configuration_smoke_suite"
VERSION = 2
SCENE_KEY = "factory"
SCENE_RELATIVE_PATHS = (
    "scene/factory/factory.usd",
    "scene/factory/factory_map.yaml",
    "scene/factory/factory_map.png",
)

PROTOCOLS: dict[str, dict[str, Any]] = {
    "ground_wheeled_navigation_v1": {
        "version": 1,
        "primitive": "closed_loop_waypoint_navigation",
        "domain": "ground",
        "sensor_requirement": "navigation_state",
        "executor_support": "shared_factory_runtime",
    },
    "ackermann_navigation_v1": {
        "version": 1,
        "primitive": "curvature_limited_waypoint_navigation",
        "domain": "ground",
        "sensor_requirement": "navigation_state",
        "executor_support": "shared_factory_runtime",
    },
    "legged_navigation_v2": {
        "version": 2,
        "primitive": "velocity_policy_waypoint_navigation",
        "domain": "ground",
        "sensor_requirement": "navigation_state",
        "executor_support": "shared_factory_runtime",
    },
    "biped_navigation_v2": {
        "version": 2,
        "primitive": "policy_waypoint_navigation",
        "domain": "ground",
        "sensor_requirement": "navigation_state",
        "executor_support": "shared_factory_runtime",
    },
    "aerial_scan_v1": {
        "version": 1,
        "primitive": "position_hold_with_camera_capture",
        "domain": "aerial",
        "sensor_requirement": "built_in_camera",
        "executor_support": "paired_goal_position_with_live_camera_capture",
    },
}

_PROTOCOL_BY_LOCOMOTION = {
    "differential": "ground_wheeled_navigation_v1",
    "planar_root_velocity": "ground_wheeled_navigation_v1",
    "virtual_holonomic_joints": "ground_wheeled_navigation_v1",
    "ackermann": "ackermann_navigation_v1",
    "legged": "legged_navigation_v2",
    "legged_plus_wheels": "legged_navigation_v2",
    "multirotor_thrust_torque": "aerial_scan_v1",
    "multirotor_rotor": "aerial_scan_v1",
}

_SPAWN_BY_FAMILY = {
    "wheeled": (-7.95, -5.6),
    "quadruped": (-1.95, -8.6),
    "wheel_legged": (-1.95, -8.6),
    "biped": (-4.95, 0.4),
    "aerial": (0.0, 0.4),
}

_SPAWN_BY_PROTOCOL = {
    "ground_wheeled_navigation_v1": (-7.95, -5.6),
    "ackermann_navigation_v1": (-7.95, -5.6),
    "legged_navigation_v2": (-1.95, -8.6),
    "biped_navigation_v2": (-4.95, 0.4),
    "aerial_scan_v1": (0.0, 0.4),
}

_TARGET_BY_PROTOCOL = {
    "ground_wheeled_navigation_v1": ("ground_navigation_target", (-1.85, -5.6, 0.0)),
    "ackermann_navigation_v1": ("ackermann_navigation_target", (-1.95, 6.4, 0.0)),
    "legged_navigation_v2": ("legged_navigation_target", (-1.95, -7.0, 0.0)),
    "biped_navigation_v2": ("biped_navigation_target", (-3.5, 0.4, 0.0)),
    "aerial_scan_v1": ("inspection_site", (-7.95, -5.6, 0.0)),
}

_TARGET_BY_ROBOT = {
    "scout": ("scout_navigation_target", (-7.95, -2.6, 0.0)),
}

_AERIAL_POSITION_CONTROLLERS = {
    "cf2x": "QUADCOPTER_GOAL_SKRL_CFG",
    "iris": "PEGASUS_IRIS_POSITION_CFG",
    "pegasus": "PEGASUS_X4_POSITION_CFG",
}


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return sha256(path.read_bytes()).hexdigest()


def _load_controller_imports() -> dict[str, tuple[str, str]]:
    """Read the authoritative literal map without importing Isaac modules."""
    source = REPO_ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "env_builder.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == "CONTROLLER_CFG_IMPORTS" for target in targets):
            value = node.value
            loaded = ast.literal_eval(value)
            return {str(key): (str(item[0]), str(item[1])) for key, item in loaded.items()}
    raise ValueError("CONTROLLER_CFG_IMPORTS was not found in env_builder.py")


def _asset_root() -> Path:
    configured = os.environ.get("EAI_USD_ROOT")
    return Path(configured).expanduser().resolve() if configured else REPO_ROOT / "usd"


def _controller_root() -> Path:
    configured = os.environ.get("EAI_CONTROLLER_ROOT")
    return (Path(configured).expanduser().resolve() if configured else
            REPO_ROOT / "source" / "EAI_assets" / "EAI_assets" / "controller")


def _scene_status() -> dict[str, Any]:
    root = _asset_root()
    files = []
    for relative in SCENE_RELATIVE_PATHS:
        path = root / relative
        files.append({"relative_path": relative, "path": str(path),
                      "present": path.is_file(), "sha256": _sha256_file(path)})
    missing = [item["relative_path"] for item in files if not item["present"]]
    return {"status": "available" if not missing else "missing", "root": str(root),
            "files": files, "missing": missing}


def _sensor_tools(robot_key: str, domain: str) -> tuple[list[str], dict[str, Any]]:
    if domain == "aerial":
        return ["camera", "navigation_io"], {"mode": "built_in_camera", "frame_consumer": "aerial_scan_v1"}
    return ["navigation_io"], {"mode": "navigation_state_only", "frame_consumer": None}


def _scenario(robot_key: str, controller_cfg: str, protocol_id: str, family: str,
              spawn_z: float, attachments: list[str]) -> dict[str, Any]:
    target_id, target_position = _TARGET_BY_ROBOT.get(robot_key, _TARGET_BY_PROTOCOL[protocol_id])
    domain = "aerial" if protocol_id == "aerial_scan_v1" else "ground"
    skill = "aerial_scan" if domain == "aerial" else "navigate"
    seed = 1701 + list(_SPAWN_BY_FAMILY).index(family)
    return {
        "version": 2,
        "task_name": f"factory_configuration_smoke_{robot_key}",
        "scene_key": SCENE_KEY,
        "seed": seed,
        "robots": [{
            "id": f"{robot_key}_1",
            "type": robot_key,
            "controller": {"mode": "default", "cfg": controller_cfg},
            "attachments": [{"type": item, "controller": None} for item in attachments],
            "spawn_pose": {"position": [*_SPAWN_BY_PROTOCOL[protocol_id], spawn_z],
                            "rotation": [1.0, 0.0, 0.0, 0.0]},
        }],
        "composition": {"entities": [], "targets": [{"id": target_id,
                         "position": list(target_position), "radius_m": 0.5}]},
        "phase_graph": {"nodes": [{"id": "smoke_phase", "skill_key": skill,
                         "preferred_agent": f"{robot_key}_1", "agents": None,
                         "target_ref": target_id, "object_ref": None,
                         "prerequisites": [], "timeout_s": 180.0,
                         "duration_s": None,
                         "resources": []}]},
    }


def build_record(robot_key: str, *, scene: Mapping[str, Any] | None = None,
                 controller_imports: Mapping[str, tuple[str, str]] | None = None,
                 physics_gate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from EAI.hmrs_env.env_diy import catalog
    from tools.scenario_generation.capability_profile import profile_for_dossier
    from tools.scenario_generation.robot_dossier import robot_dossier, dossier_snapshot_hash

    base_dossier = robot_dossier(robot_key)
    morphology = base_dossier["morphology"]
    locomotion = morphology["locomotion"]
    domain = morphology["domain"]
    protocol_id = ("biped_navigation_v2" if morphology["family"] == "biped"
                   else _PROTOCOL_BY_LOCOMOTION[locomotion])
    protocol = PROTOCOLS[protocol_id]
    controller_cfg = base_dossier["controller"]["default_cfg"]
    imports = dict(controller_imports or _load_controller_imports())
    controller_entry = imports.get(controller_cfg)
    controller_blockers: list[str] = []
    controller_file = None
    controller_status = "declared"
    if controller_entry is None:
        controller_status = "missing_mapping"
        controller_blockers.append(f"controller_mapping_missing:{controller_cfg}")
    else:
        relative_path, attribute = controller_entry
        controller_file = _controller_root() / relative_path
        if not controller_file.is_file():
            controller_status = "missing_local_asset"
            controller_blockers.append(f"controller_asset_missing:{relative_path}")
        else:
            controller_status = "available"

    attachments, sensor = _sensor_tools(robot_key, domain)
    attachment_errors: list[str] = []
    try:
        normalized_attachments = list(catalog.validate_attachment_types(robot_key, attachments))
    except ValueError as exc:
        normalized_attachments = attachments
        attachment_errors.append(str(exc))

    dossier = (robot_dossier(robot_key, attachments=normalized_attachments)
               if not attachment_errors else base_dossier)
    morphology = dossier["morphology"]

    scene_status = dict(scene or _scene_status())
    blockers = list(controller_blockers)
    blockers.extend(f"scene_asset_missing:{item}" for item in scene_status.get("missing", []))
    blockers.extend(f"attachment_invalid:{item}" for item in attachment_errors)
    if (protocol_id == "aerial_scan_v1"
            and controller_cfg != _AERIAL_POSITION_CONTROLLERS.get(robot_key)):
        blockers.append("protocol_not_executable:aerial_controller_has_no_paired_goal_position_adapter")

    family = morphology["family"]
    spawn_xy = _SPAWN_BY_PROTOCOL[protocol_id]
    spawn_z = morphology["spawn_z_m"]["value"]
    scenario = _scenario(robot_key, controller_cfg, protocol_id, family, float(spawn_z), normalized_attachments)
    from EAI.scenario_spec.geometry import provider_map, validate_geometry, validate_connectivity
    from EAI.scenario_spec.evaluation import validate_evaluation
    from EAI.scenario_spec.validation import ScenarioError
    from demo.benchmark.provenance import canonical_sha256

    try:
        occupancy = provider_map(scene_key=SCENE_KEY)
        geometry_gate = {"status": "passed", "placement": validate_geometry(scenario, occupancy),
                         "connectivity": validate_connectivity(scenario, occupancy),
                         "nontrivial": validate_evaluation(scenario)}
    except (FileNotFoundError, ScenarioError) as exc:
        geometry_gate = {"status": "failed", "reason": str(exc)}
        blockers.append(f"geometry_gate_failed:{exc}")
    record = {
        "schema": SCHEMA,
        "version": VERSION,
        "configuration_id": profile_for_dossier(dossier)["identity"]["configuration_id"],
        "robot_key": robot_key,
        "dossier_snapshot_sha256": dossier_snapshot_hash(dossier),
        "scene": {"scene_key": SCENE_KEY, "usd_relative_paths": list(SCENE_RELATIVE_PATHS),
                   "asset_check": scene_status},
        "controller": {"cfg": controller_cfg, "mode": "default", "family": dossier["controller"]["family"],
                       "command_semantics": dossier["controller"]["command_semantics"],
                       "source_relative_path": controller_entry[0] if controller_entry else None,
                       "source_attribute": controller_entry[1] if controller_entry else None,
                       "asset_status": controller_status,
                       "asset_path": str(controller_file) if controller_file else None},
        "sensor_tools": {"attachments": normalized_attachments, **sensor},
        "spawn": {"position_xy_m": list(spawn_xy), "z_m": float(spawn_z),
                  "rotation_wxyz": [1.0, 0.0, 0.0, 0.0],
                  "condition": "factory_ground_floor_free_space_anchor",
                  "requires_isaac_settle": True},
        "protocol": {"id": protocol_id, **protocol,
                     "input_contract": "simulator.py -> open_simulator_session -> env_builder"},
        "physics_gate": {"status": "pending", "required_checks": [
            "asset_resolution", "spawn_settle", "controller_reset", "base_support", "route_execution",
        ], "manipulation_e7": "not_applicable_to_this_navigation_smoke"},
        "execution": {"status": "blocked" if blockers else "ready_for_isaac_smoke",
                      "blockers": sorted(set(blockers)),
                      "not_executable_reasons": sorted(set(blockers)),
                      "evidence_tier_if_passed": "E6",
                      "limitations": [
                          "one Factory layout and one configuration input",
                          "does not establish rated payload, grasp force, collision safety margin, wind resistance, load dynamics, or E7 physical manipulation",
                      ]},
        "scenario": scenario,
        "scenario_sha256": canonical_sha256(scenario),
        "geometry_gate": geometry_gate,
    }
    if physics_gate is not None:
        gate = copy.deepcopy(dict(physics_gate))
        gate_status = gate.get("status")
        if gate_status not in {"pending", "passed", "failed"}:
            raise ValueError("physics gate override status must be pending, passed, or failed")
        reasons = gate.get("reasons", [])
        if not isinstance(reasons, list) or any(not isinstance(item, str) or not item for item in reasons):
            raise ValueError("physics gate override reasons must be nonempty strings")
        record["physics_gate"] = gate
        if gate_status == "failed":
            failures = [f"physics_gate_failed:{item}" for item in reasons]
            if not reasons:
                failures = ["physics_gate_failed:unspecified"]
            record["execution"]["status"] = "not_executable"
            record["execution"]["blockers"] = sorted(set(record["execution"]["blockers"] + failures))
            record["execution"]["not_executable_reasons"] = list(record["execution"]["blockers"])
    return record


def build_suite(*, physics_gates: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    from EAI.hmrs_env.env_diy.catalog import robot_keys

    gates = physics_gates or {}
    records = [build_record(robot_key, physics_gate=gates.get(robot_key)) for robot_key in robot_keys()]
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "scene_key": SCENE_KEY,
        "robot_count": len(records),
        "records": records,
        "summary": {
            "ready_for_isaac_smoke": sum(item["execution"]["status"] == "ready_for_isaac_smoke" for item in records),
            "blocked": sum(item["execution"]["status"] == "blocked" for item in records),
            "protocols": sorted({item["protocol"]["id"] for item in records}),
        },
        "policy": {
            "candidate_pool": "all connected EAI robots",
            "unknown_physics": "pending evidence; never coerced to pass",
            "runtime_boundary": "only simulator.py/open_simulator_session may execute the scenario",
        },
    }


def validate_suite(suite: Mapping[str, Any]) -> dict[str, Any]:
    from demo.benchmark.provenance import canonical_sha256
    from EAI.scenario_spec.validation import validate_scenario
    from tools.scenario_generation.capability_profile import profile_for_dossier
    from tools.scenario_generation.robot_dossier import robot_dossier

    if suite.get("schema") != SCHEMA or suite.get("version") != VERSION:
        raise ValueError("unsupported configuration smoke suite schema")
    records = suite.get("records")
    if not isinstance(records, list) or len(records) != 14:
        raise ValueError("the Factory configuration smoke suite must contain all 14 robots")
    keys = [record.get("robot_key") for record in records]
    if len(set(keys)) != 14:
        raise ValueError("configuration smoke suite contains duplicate robot keys")
    for record in records:
        scenario = record["scenario"]
        validate_scenario(scenario)
        if record.get("version") != VERSION or len(scenario["robots"]) != 1:
            raise ValueError("configuration smoke requires a current single-robot record")
        if record.get("scenario_sha256") != canonical_sha256(scenario):
            raise ValueError("configuration smoke scenario digest differs from input")
        robot = scenario["robots"][0]
        if (robot["type"] != record["robot_key"]
                or robot["controller"]["cfg"] != record["controller"]["cfg"]
                or robot["spawn_pose"]["position"] != [*record["spawn"]["position_xy_m"], record["spawn"]["z_m"]]
                or robot["spawn_pose"]["rotation"] != record["spawn"]["rotation_wxyz"]
                or [item["type"] for item in robot["attachments"]] != record["sensor_tools"]["attachments"]):
            raise ValueError("configuration smoke metadata differs from scenario")
        dossier = robot_dossier(record["robot_key"], attachments=record["sensor_tools"]["attachments"])
        if record.get("configuration_id") != profile_for_dossier(dossier)["identity"]["configuration_id"]:
            raise ValueError("configuration smoke identity differs from capability profile")
        if record.get("scene", {}).get("scene_key") != SCENE_KEY or scenario.get("scene_key") != SCENE_KEY:
            raise ValueError("every configuration smoke record must target Factory")
        if record.get("protocol", {}).get("id") not in PROTOCOLS:
            raise ValueError("unknown configuration smoke protocol")
        status = record.get("execution", {}).get("status")
        if status not in {"blocked", "ready_for_isaac_smoke", "not_executable"}:
            raise ValueError("configuration smoke records require an explicit execution status")
        if status == "blocked" and not record.get("execution", {}).get("blockers"):
            raise ValueError("blocked configuration smoke records require blockers")
        if status == "ready_for_isaac_smoke" and record.get("geometry_gate", {}).get("status") != "passed":
            raise ValueError("ready smoke inputs require geometry and nontriviality checks")
        if record.get("physics_gate", {}).get("status") == "failed" and status not in {"blocked", "not_executable"}:
            raise ValueError("a failed physics gate requires blocked or not_executable status")
        if record.get("physics_gate", {}).get("status") not in {"pending", "passed", "failed"}:
            raise ValueError("physics gate must be explicit")
    return dict(suite)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--physics-gates", type=Path,
                        help="JSON object keyed by robot_key with status/reasons overrides")
    args = parser.parse_args(argv)
    physics_gates = None
    if args.physics_gates is not None:
        physics_gates = json.loads(args.physics_gates.read_text(encoding="utf-8"))
        if not isinstance(physics_gates, dict):
            raise ValueError("--physics-gates must contain a JSON object")
    suite = validate_suite(build_suite(physics_gates=physics_gates))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(suite["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
