"""Compile author-only deployment configurations without mutating scene assets.

Public task parameters are authoritative experimental values. Scene adapters
provide spatial/asset proposals with explicit evidence and blockers. A compiled
configuration never certifies that a native robot or USD actually implements
those values, nor promotes a reference backend to physical execution.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from .goal_driven_contract import validate_goal_task_spec
from .provenance import canonical_sha256, file_sha256

SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city")
SCHEMA = "eai.benchmark.goal_deployment"


def _index(rows, label):
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError(f"{label} requires nonempty ids")
        if row["id"] in result:
            raise ValueError(f"{label} duplicate id: {row['id']}")
        result[row["id"]] = row
    return result


def _sources(files):
    if not isinstance(files, dict) or not files:
        raise ValueError("deployment requires source file hashes")
    for name, digest in files.items():
        path = Path(name)
        if (not path.is_absolute() or not path.is_file()
                or not isinstance(digest, str) or file_sha256(path) != digest):
            raise ValueError(f"deployment source missing or changed: {name}")


def _rebuild_overlay(public_task, overlay):
    mode = overlay.get("source_verification_requested")
    if type(mode) is not bool:
        raise ValueError("overlay needs explicit source verification mode")
    if public_task["mission"]["scene_key"] == "city":
        from .goal_city_deployment import build_city_deployment_overlay
        path = overlay.get("source_context", {}).get("city_package_dir")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("city overlay needs its source package directory")
        return build_city_deployment_overlay(public_task, city_package_dir=path, verify_sources=mode)
    from .goal_indoor_deployment import build_indoor_deployment_overlay
    return build_indoor_deployment_overlay(public_task, verify_sources=mode)


def compile_goal_deployment(public_task, overlay):
    """Compile distinct runtime identities and canonical experimental parameters.

    Overlay is an author/adapter input, never a participant input. Keep its
    proposed geometry and evidence intact; immutable acceptance below checks
    sources and rebuilds the compiled fields, not just their saved pass flags.
    """
    validate_goal_task_spec(public_task)
    scene = public_task["mission"]["scene_key"]
    if scene not in SCENES or not isinstance(overlay, dict) or overlay.get("scene_key") != scene:
        raise ValueError("deployment overlay scene mismatch")
    if overlay.get("public_task_sha256") != canonical_sha256(public_task):
        raise ValueError("deployment overlay/public task mismatch")
    canonical_sha256(overlay)
    _sources(overlay.get("source_files"))
    world = public_task["public_world"]
    for key, expected in (("object_bindings", world["objects"]),
                          ("region_bindings", world["regions"]),
                          ("route_bindings", world["routes"]),
                          ("robot_spawns", public_task["candidate_robot_pool"])):
        rows = _index(overlay.get(key), key)
        if set(rows) != set(_index(expected, key)):
            raise ValueError(f"{key} must cover the exact public inventory")
        declaration_key = {"object_bindings": "public_object", "region_bindings": "public_region",
                           "route_bindings": "public_route", "robot_spawns": "public_robot"}[key]
        for item in expected:
            if canonical_sha256(rows[item["id"]].get(declaration_key)) != canonical_sha256(item):
                raise ValueError(f"{key} differs from public declaration: {item['id']}")
    for obj in world["objects"]:
        row = next(x for x in overlay["object_bindings"] if x["id"] == obj["id"])
        directive = row.get("deployment_directive")
        if directive is not None:
            if not isinstance(directive, dict) or any(
                    canonical_sha256(directive.get(key)) != canonical_sha256(value)
                    for key, value in obj["physical_properties"].items()):
                raise ValueError(f"object deployment directive differs from frozen physical properties: {obj['id']}")
    if not isinstance(overlay.get("blockers"), list):
        raise ValueError("overlay must declare blockers")
    robots = []
    spawns = _index(overlay["robot_spawns"], "robot_spawns")
    counters = {}
    for candidate in public_task["candidate_robot_pool"]:
        robot_type = candidate["type"]
        if not re.fullmatch(r"[a-z][a-z0-9_]*", robot_type):
            raise ValueError("unsafe runtime robot type")
        counters[robot_type] = counters.get(robot_type, 0) + 1
        runtime_id = f"{robot_type}_{counters[robot_type]}"
        spawn = deepcopy(spawns[candidate["id"]])
        if "instance_id" in spawn:
            spawn["instance_id"] = runtime_id
        if "deployment_directive" in spawn:
            spawn["deployment_directive"]["instance_id"] = runtime_id
        robots.append({
            "public_id": candidate["id"], "runtime_id": runtime_id,
            "prim_path": f"/World/envs/env_0/{runtime_id}",
            "command_namespace": f"/{runtime_id}", "backend_state_key": runtime_id,
            "type": robot_type, "public_declaration": deepcopy(candidate),
            "physical_profile": deepcopy(candidate["physical_profile"]),
            "spawn_binding": spawn,
            "assembly_policy": "instantiate_separate_entity_and_configure_declared_skill_interfaces",
            "configuration_applied_validated": False,
        })
    objects = []
    bindings = _index(overlay["object_bindings"], "object_bindings")
    for index, obj in enumerate(world["objects"], 1):
        runtime_id = f"goal_object_{index}"
        binding = deepcopy(bindings[obj["id"]])
        if "deployment_directive" in binding:
            binding["deployment_directive"]["instance_id"] = runtime_id
        objects.append({
            "public_id": obj["id"], "runtime_id": runtime_id,
            "prim_path": f"/World/envs/env_0/{runtime_id}",
            "physical_properties": deepcopy(obj["physical_properties"]),
            "scene_binding": binding,
            "parameter_policy": "apply_frozen_public_values_then_read_back_before_admission",
            "native_geometry_policy": "reject_mismatch_until_explicit_geometry_configuration_is_reviewed",
            "configuration_applied_validated": False,
        })
    result = {
        "schema": SCHEMA, "version": 1, "visibility": "author_only",
        "scene_key": scene, "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "public_task": deepcopy(public_task), "scene_overlay": deepcopy(overlay),
        "robot_instances": robots, "object_instances": objects,
        "region_bindings": deepcopy(overlay["region_bindings"]),
        "route_bindings": deepcopy(overlay["route_bindings"]),
        "backend_contract": deepcopy(public_task["execution_contract"]),
        "compiler_sources": {str(Path(__file__).absolute()): file_sha256(Path(__file__))},
        "review": {
            "configuration_consistency_validated": True,
            "independent_instance_ids_validated": True,
            "parameter_basis": "frozen_public_experimental_values",
            "scene_binding_validated": False, "backend_conformance_validated": False,
            "physical_execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False,
            "blockers": deepcopy(overlay["blockers"]) + [
                "deployment_configuration_not_applied_and_read_back",
                "native_geometry_and_robot_assembly_conformance_pending",
                "reliable_backend_physical_adapter_pending"],
        },
    }
    return {**result, "sha256": canonical_sha256(result)}


def validate_goal_deployment(deployment):
    """Recompile canonical fields and verify all source files; no pass flag trust."""
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    if not isinstance(deployment.get("public_task"), dict) or not isinstance(deployment.get("scene_overlay"), dict):
        raise ValueError("deployment needs public task and overlay")
    overlay = _rebuild_overlay(deployment["public_task"], deployment["scene_overlay"])
    if canonical_sha256(overlay) != canonical_sha256(deployment["scene_overlay"]):
        raise ValueError("scene overlay differs from current source-derived bindings")
    expected = compile_goal_deployment(deployment.get("public_task"), deployment.get("scene_overlay"))
    if canonical_sha256(deployment) != canonical_sha256(expected):
        raise ValueError("deployment differs from frozen public parameters/independent instance configuration")
    return {"validated": True, "scope": "configuration_consistency_not_scene_execution",
            "scene_key": expected["scene_key"], "sha256": expected["sha256"], "scoreable": False}


def verify_configuration_readback(deployment, readback):
    """Compare host-owned configuration values; does not attest who measured them.

    A simulator adapter must supply distinct entity identities, actual applied
    robot profiles/object parameters and the compiled deployment hash. This is
    an independent configuration check, not a body/contact/skill calibration.
    """
    validate_goal_deployment(deployment)
    if not isinstance(readback, dict) or readback.get("deployment_sha256") != deployment["sha256"]:
        raise ValueError("configuration readback deployment mismatch")
    canonical_sha256(readback)
    expected = {r["runtime_id"]: ("robot", r) for r in deployment["robot_instances"]}
    expected.update({r["runtime_id"]: ("object", r) for r in deployment["object_instances"]})
    actual = _index(readback.get("entities"), "readback entities")
    if actual.keys() != expected.keys():
        raise ValueError("configuration readback entity inventory mismatch")
    identities = set()
    for runtime_id, (kind, row) in expected.items():
        entity = actual[runtime_id]
        identity = entity.get("entity_identity")
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError("configuration readback aliases an entity or lacks identity")
        identities.add(identity)
        field = "physical_profile" if kind == "robot" else "physical_properties"
        if (entity.get("kind") != kind or entity.get("prim_path") != row["prim_path"]
                or entity.get("public_id") != row["public_id"]
                or canonical_sha256(entity.get(field)) != canonical_sha256(row[field])):
            raise ValueError(f"configuration readback differs from frozen public parameters: {runtime_id}")
        if kind == "robot":
            for key in ("type", "command_namespace", "backend_state_key", "public_declaration"):
                if canonical_sha256(entity.get(key)) != canonical_sha256(row[key]):
                    raise ValueError(f"configuration readback differs from robot interfaces: {runtime_id}/{key}")
    return {"validated": True, "scope": "host_configuration_value_comparison",
            "readback_sha256": canonical_sha256(readback), "entity_count": len(actual),
            "measurement_authenticity_validated": False, "physical_execution_validated": False,
            "business_outcomes_validated": False, "scoreable": False}


def build_scene_deployment(scene_key, *, city_package_dir=None, verify_sources=True):
    if scene_key == "city":
        from .goal_city_deployment import load_city_deployment_public, build_city_deployment_overlay
        public = load_city_deployment_public(city_package_dir, verify_sources=verify_sources)
        overlay = build_city_deployment_overlay(public, city_package_dir=city_package_dir,
                                                 verify_sources=verify_sources)
    else:
        from .goal_driven_tasks import build_goal_task_spec
        from .goal_indoor_deployment import build_indoor_deployment_overlay
        public = build_goal_task_spec(scene_key)
        overlay = build_indoor_deployment_overlay(public, verify_sources=verify_sources)
    return compile_goal_deployment(public, overlay)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--output-dir", required=True, type=Path)
    audit.add_argument("--city-package", required=True, type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify":
        print(json.dumps(validate_goal_deployment(json.loads(args.report.read_text())), indent=2))
        return 0
    reports = [build_scene_deployment(scene, city_package_dir=args.city_package) for scene in SCENES]
    args.output_dir.mkdir(parents=True, exist_ok=False)
    for report in reports:
        with (args.output_dir / f"{report['scene_key']}.json").open("x") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    print(json.dumps({"scene_count": len(reports), "output_dir": str(args.output_dir), "scoreable": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
