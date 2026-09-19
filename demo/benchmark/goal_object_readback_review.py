"""Independent object-configuration audit of a completed local physical probe.

Configuration matching is separate from collision cooking, contact support,
semantic region binding, grasp execution and business success.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .goal_physical_review import audit_physical_probe, _vector
from .goal_native_objects import _asset_path
from .provenance import canonical_sha256, file_sha256


def _close(actual, expected, label, *, tolerance=1e-4):
    if (type(actual) not in (int, float) or not math.isfinite(actual)
            or not math.isclose(actual, expected, rel_tol=1e-5, abs_tol=tolerance)):
        raise ValueError(f"object {label} differs from frozen configuration")


def review_object_snapshot(deployment, snapshot, *, stage_identifier):
    """Check values, not a producer's status/mismatch summary; no authentication."""
    if (snapshot.get("schema") != "eai.goal_native_object_readback"
            or snapshot.get("version") != 1 or snapshot.get("deployment_sha256") != deployment["sha256"]
            or snapshot.get("scoreable") is not False or snapshot.get("up_axis") != "Z"
            or snapshot.get("meters_per_unit") != 1.0
            or not isinstance(stage_identifier, str) or not stage_identifier
            or snapshot.get("stage_identifier") != stage_identifier):
        raise ValueError("object snapshot deployment/frame mismatch")
    expected = {row["public_id"]: row for row in deployment["object_instances"]}
    rows = snapshot.get("objects")
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or {row.get("public_id") for row in rows} != set(expected)):
        raise ValueError("object snapshot inventory incomplete or repeated")
    result = []
    for row in rows:
        spec = expected[row["public_id"]]
        for key in ("runtime_id", "prim_path"):
            if row.get(key) != spec[key]:
                raise ValueError("object native/public identity mismatch")
        if row.get("scoreable") is not False:
            raise ValueError("object configuration cannot confer scoring readiness")
        if row.get("status") != "measured":
            result.append({"public_id": row["public_id"], "configuration_readback_validated": False,
                           "reason": row.get("reason") or row.get("mismatches") or row.get("unknowns") or "unmeasured"})
            continue
        props = spec["physical_properties"]
        usd, live = row["usd"], row["physx"]
        if (usd.get("rigid_body_enabled") is not True or usd.get("kinematic_enabled") is not False
                or live.get("status") != "measured" or live.get("source") != "root_physx_view"
                or live.get("prim_path") != spec["prim_path"]):
            raise ValueError("object requires enabled dynamic body and live PhysX identity")
        _close(usd.get("authored_mass_kg"), props["mass_kg"], "authored mass")
        _close(live.get("mass_kg"), props["mass_kg"], "PhysX mass")
        _vector(live.get("position_m"), 3)
        quat = _vector(live.get("rotation_xyzw"), 4)
        _close(sum(v*v for v in quat), 1., "live rotation norm")
        bounds = usd["local_mesh_bounds_m"]
        for key in ("min", "max", "size", "center"):
            _vector(bounds.get(key), 3)
        for i in range(3):
            _close(bounds["size"][i], props["size_m"][i], "mesh size")
            _close(bounds["max"][i]-bounds["min"][i], bounds["size"][i], "bounds extent")
            _close((bounds["max"][i]+bounds["min"][i])/2, bounds["center"][i], "bounds center")
            _close(bounds["center"][i], 0., "centered root")
        meshes = bounds.get("meshes")
        collisions = usd.get("collisions")
        if (not isinstance(meshes, list) or not meshes or len(set(meshes)) != len(meshes)
                or any(not p.startswith(spec["prim_path"]+"/Geometry/") for p in meshes)
                or not isinstance(collisions, list) or len(collisions) != len(meshes)
                or {c["prim_path"] for c in collisions} != set(meshes)
                or any(c.get("enabled") is not True or c.get("approximation") != "convexHull" for c in collisions)):
            raise ValueError("object collision declaration coverage mismatch")
        points = row.get("fixed_points")
        expected_points = {p["id"]: p for p in props["grasp_points"]}
        if (not isinstance(points, list) or len(points) != len(expected_points)
                or {p["id"] for p in points} != set(expected_points)):
            raise ValueError("object fixed-point inventory mismatch")
        for point in points:
            wanted = expected_points[point["id"]]
            if (point.get("status") != "usd_measured"
                    or point.get("prim_path") != spec["prim_path"]+"/GraspPoints/"+point["id"]
                    or point.get("declared_interface") != wanted["interface"]
                    or point.get("physical_connector_verified") is not False):
                raise ValueError("object fixed-point frame/interface/claim mismatch")
            actual = _vector(point.get("position_local_m"), 3)
            for a, b in zip(actual, wanted["position_m"]):
                _close(a, b, "fixed point", tolerance=1e-5)
        native = row.get("native_source", {})
        sources = deployment["scene_overlay"]["source_files"]
        path = native.get("path")
        expected_path = str(_asset_path(spec, deployment))
        if (native.get("status") != "composed_reference_verified" or path != expected_path or path not in sources
                or native.get("sha256") != sources[path] or file_sha256(Path(path)) != sources[path]):
            raise ValueError("object native source is not frozen")
        if row.get("mismatches") or row.get("unknowns"):
            raise ValueError("measured object contains unresolved configuration issues")
        if any(row.get(key) is not False for key in ("collision_cooking_validated", "grasp_execution_validated")):
            raise ValueError("object readback cannot certify cooking or grasp execution")
        result.append({"public_id": row["public_id"], "configuration_readback_validated": True,
                       "actual_mass_kg": live["mass_kg"], "actual_local_mesh_size_m": bounds["size"],
                       "actual_position_m": live["position_m"], "fixed_point_count": len(points)})
    return result


def audit_object_probe(root):
    root = Path(root).absolute()
    native = audit_physical_probe(root)
    deployment = json.loads((root/"deployment.json").read_text())
    report = json.loads((root/"physical_report.json").read_text())
    stage = json.loads((root/"runtime_manifest.json").read_text())["stage"]
    initial = review_object_snapshot(deployment, report["initial_objects"], stage_identifier=stage["root_layer_identifier"])
    final = review_object_snapshot(deployment, report["final_objects"], stage_identifier=stage["root_layer_identifier"])
    result = {"schema": "eai.benchmark.goal_object_configuration_review", "version": 1,
              "scene_key": native["scene_key"], "run_id": native["run_id"],
              "physical_report_sha256": native["physical_report_sha256"],
              "initial_objects": initial, "final_objects": final,
              "all_declared_objects_read_back": bool(final) and all(r["configuration_readback_validated"] for r in initial+final),
              "semantic_region_binding_validated": False, "collision_cooking_validated": False,
              "support_contact_validated": False, "grasp_execution_validated": False, "scoreable": False}
    return {**result, "sha256": canonical_sha256(result)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(audit_object_probe(args.output_root), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
