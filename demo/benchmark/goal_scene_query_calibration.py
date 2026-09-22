"""Read-only native PhysX scene-query callback calibration.

The observations classify neither collisions nor route safety. The ordinary
prim sentinel must be established independently before self-hit exclusion.
"""

from __future__ import annotations

from typing import Any, Mapping


MAX_CALLBACKS = 64
MAX_STAGE_PRIMS = 200_000
PROPERTY_BOUNDS_TOLERANCE_M = 1e-3


def _bounds_center(bounds):
    return [(float(low) + float(high)) / 2 for low, high in zip(*bounds)]


def _query(query, kind, *, center, half, distance=0.0, stop_first=False,
           argument_mode="tuple"):
    row = {"operation": kind, "center_xyz_m": list(center), "half_extents_m": list(half),
           "stop_first": stop_first, "argument_mode": argument_mode,
           "callbacks": [], "status": "unknown"}

    def callback(hit):
        fields = hit.get if isinstance(hit, dict) else lambda name: getattr(hit, name, None)
        raw_index = fields("protoIndex")
        entry = {"ordinal": len(row["callbacks"]),
                 "collision": str(fields("collision")),
                 "rigid_body": str(fields("rigid_body")),
                 "protoIndex": int(raw_index) if raw_index is not None else None,
                 "protoIndex_type": type(raw_index).__name__}
        for key in ("collision", "rigid_body"):
            encoded = fields(key + "_encoded")
            if encoded is None:
                entry[key + "_encoded"] = None
                entry[key + "_decoded"] = None
                continue
            entry[key + "_encoded"] = list(encoded)
            try:
                from pxr import PhysicsSchemaTools
                entry[key + "_decoded"] = str(PhysicsSchemaTools.decodeSdfPath(*encoded))
            except Exception as exc:
                entry[key + "_decode_error"] = f"{type(exc).__name__}: {exc}"
        if kind == "sweep":
            distance_value = fields("distance")
            entry["distance_m"] = float(distance_value) if distance_value is not None else None
        row["callbacks"].append(entry)
        return len(row["callbacks"]) < MAX_CALLBACKS and not stop_first

    try:
        if argument_mode == "sdk_types":
            import carb
            from pxr import Gf
            box_half, box_center = Gf.Vec3f(*half), Gf.Vec3f(*center)
            rotation, direction = carb.Float4(0., 0., 0., 1.), Gf.Vec3f(1., 0., 0.)
        else:
            box_half, box_center = tuple(half), tuple(center)
            rotation, direction = (0., 0., 0., 1.), (1., 0., 0.)
        if kind == "overlap":
            returned = query.overlap_box(box_half, box_center, rotation, callback, False)
            if type(returned) is not int:
                raise ValueError("overlap did not return an integer")
        else:
            row.update(direction_xyz=[1., 0., 0.], distance_m=distance)
            returned = query.sweep_box_all(box_half, box_center, rotation, direction,
                                           distance, callback, True)
            if type(returned) is not bool:
                raise ValueError("sweep did not return a boolean")
        row.update(status="observed", raw_return=returned,
                   callback_count=len(row["callbacks"]),
                   callback_limit_reached=len(row["callbacks"]) >= MAX_CALLBACKS)
    except Exception as exc:
        row.update(error=f"{type(exc).__name__}: {exc}",
                   callback_count=len(row["callbacks"]))
    return row


def _stage_instance_inventory(stage):
    result = {"scope": "active_loaded_composed_usd_prims", "status": "complete",
              "prim_count": 0, "point_instancer_paths": [], "instance_root_paths": [],
              "instanceable_paths": [], "limits": [
                  "unloaded_or_inactive_prims_not_traversed",
                  "not_a_physx_internal_shape_inventory"]}
    for prim in stage.Traverse():
        result["prim_count"] += 1
        if result["prim_count"] > MAX_STAGE_PRIMS:
            result["status"] = "inventory_limit_exceeded"
            break
        if prim.GetTypeName() == "PointInstancer":
            result["point_instancer_paths"].append(str(prim.GetPath()))
        if prim.IsInstance():
            result["instance_root_paths"].append(str(prim.GetPath()))
        if prim.IsInstanceable():
            result["instanceable_paths"].append(str(prim.GetPath()))
    return result


def _property_colliders(occupancy, physics_step):
    owners = {}
    for entity in occupancy["entities"]:
        for body in entity["bodies"]:
            bounds = body.get("bounds_source", {})
            pose = body.get("pose_source", {})
            paths = body.get("collision_paths", [])
            measurements = bounds.get("collider_measurements", [])
            measured_paths = [row.get("collision_path") for row in measurements]
            complete = (bounds.get("kind") == "physx_collision_bounds"
                        and bounds.get("physics_step") == physics_step
                        and pose.get("physics_step") == physics_step
                        and bounds.get("configuration_revision") == pose.get("configuration_revision")
                        and bounds.get("covers_all_enabled_colliders") is True
                        and bool(paths) and len(paths) == len(set(paths)) == len(measured_paths)
                        and set(paths) == set(measured_paths)
                        and all(row.get("query", {}).get("complete") is True
                                and row["query"].get("physics_step") == physics_step
                                and row.get("property_callback", {}).get("path") == row.get("collision_path")
                                for row in measurements))
            for path in paths:
                owners.setdefault(path, []).append({"entity_id": entity["id"],
                    "body_id": body["body_id"], "property_inventory_complete": complete})
    return owners


def _robot_property_controls(stage, entities, property_reader, physics_step, snapshot, cases):
    controls = []
    snapshot_bodies = {row["id"]: row for row in snapshot.get("bodies", [])}
    hit_bodies = {hit["rigid_body"] for case in cases for row in case["queries"]
                  for hit in row["callbacks"]}
    for identifier, entity in entities.items():
        for body in entity["bodies"]:
            if (body["body_id"] not in hit_bodies
                    or body.get("bounds_source", {}).get("kind") == "physx_collision_bounds"):
                continue
            control = {"entity_id": identifier, "body_id": body["body_id"],
                       "status": "unknown", "physics_step": physics_step,
                       "expected_collision_paths": list(body["collision_paths"])}
            if property_reader is None:
                control["reason"] = "property_reader_unavailable"
                controls.append(control)
                continue
            try:
                prim = stage.GetPrimAtPath(body["body_id"])
                colliders = [stage.GetPrimAtPath(path) for path in body["collision_paths"]]
                if not prim.IsValid() or any(not collider.IsValid() for collider in colliders):
                    raise ValueError("body or collider absent from composed stage")
                bounds, measurements = property_reader(stage, prim, colliders)
                paths = [row["collision_path"] for row in measurements]
                complete = (len(paths) == len(set(paths)) == len(body["collision_paths"])
                            and set(paths) == set(body["collision_paths"])
                            and all(row.get("property_callback", {}).get("path") == row["collision_path"]
                                    and row.get("query", {}).get("complete") is True
                                    and row["query"].get("physics_step") == physics_step
                                    and row["query"].get("body_path") == body["body_id"]
                                    for row in measurements))
                source_bounds = body["bounds_source"]
                if source_bounds.get("physics_step") != physics_step:
                    raise ValueError("USD and property bounds belong to different steps")
                snapshot_body = snapshot_bodies.get(body["body_id"])
                if (snapshot_body is None or snapshot_body.get("bounds_source", {}).get("physics_step") != physics_step
                        or snapshot_body["bounds_source"].get("configuration_revision")
                        != source_bounds.get("configuration_revision")):
                    raise ValueError("same-step source body bounds unavailable")
                if (not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                        or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in bounds)):
                    raise ValueError("property reader returned invalid local bounds")
                bounds_error = max(abs(a-b) for actual, expected in zip(bounds, snapshot_body["local_collision_bounds_m"])
                                   for a, b in zip(actual, expected))
                control.update(local_collision_bounds_m=bounds, collider_measurements=measurements,
                               max_usd_bounds_error_m=bounds_error,
                               status="property_matched" if complete and bounds_error <= PROPERTY_BOUNDS_TOLERANCE_M
                                      else "unknown",
                               reason=None if complete and bounds_error <= PROPERTY_BOUNDS_TOLERANCE_M
                                      else "property_inventory_or_usd_bounds_mismatch")
            except Exception as exc:
                control["reason"] = f"{type(exc).__name__}: {exc}"
            controls.append(control)
    return controls


def calibrate_scene_queries(query: Any, occupancy: Mapping[str, Any], stage: Any,
                            *, physics_step: int, object_readback: Mapping[str, Any],
                            property_reader=None, snapshot=None) -> dict[str, Any]:
    """Compare robot and task-object hits at one frozen physics step."""
    entities = {row["id"]: row for row in occupancy["entities"]}
    cases = []
    for identifier, suffix in (("warehouse_aerial_1", "/body"),
                               ("warehouse_ground_1", "/base_link")):
        entity = entities[identifier]
        body = next(row for row in entity["bodies"] if row["body_id"].endswith(suffix))
        bounds = body["world_aabb_m"]
        center = _bounds_center(bounds)
        sweep = [bounds[0][0] - .25, center[1], center[2]]
        cases.append({"id": identifier, "body_id": body["body_id"],
                      "world_aabb_m": bounds,
                      "queries": [
                          _query(query, "overlap", center=center, half=[.04] * 3),
                          _query(query, "overlap", center=center, half=[.04] * 3,
                                 argument_mode="sdk_types"),
                          _query(query, "overlap", center=center, half=[.04] * 3,
                                 stop_first=True),
                          _query(query, "sweep", center=sweep, half=[.04] * 3,
                                 distance=bounds[1][0] - bounds[0][0] + .5),
                          _query(query, "sweep", center=sweep, half=[.04] * 3,
                                 distance=bounds[1][0] - bounds[0][0] + .5,
                                 argument_mode="sdk_types"),
                      ]})
    crate = next(row for row in object_readback["objects"] if row["public_id"] == "order_crate_01")
    crate_center = crate["physx"]["position_m"]
    cases.append({"id": "order_crate_01", "body_id": crate["prim_path"],
                  "queries": [_query(query, "overlap", center=crate_center, half=[.04] * 3),
                              _query(query, "overlap", center=crate_center, half=[.04] * 3,
                                     argument_mode="sdk_types"),
                              _query(query, "overlap", center=crate_center, half=[.04] * 3,
                                     stop_first=True)]})
    cases.append({"id": "empty_z_100_m", "queries": [
        _query(query, "overlap", center=[0., 0., 100.], half=[.04] * 3)]})
    paths = {hit["collision"] for case in cases for row in case["queries"]
             for hit in row["callbacks"]}
    prims = []
    for path in sorted(paths):
        prim = stage.GetPrimAtPath(path)
        ancestors = []
        instance_roots = []
        current = prim
        while current and current.IsValid():
            if current.GetTypeName() == "PointInstancer":
                ancestors.append(str(current.GetPath()))
            if current.IsInstance():
                instance_roots.append(str(current.GetPath()))
            current = current.GetParent()
        prims.append({"collision": path, "prim_valid": bool(prim and prim.IsValid()),
                      "prim_type": prim.GetTypeName() if prim and prim.IsValid() else None,
                      "is_instance": bool(prim.IsInstance()) if prim and prim.IsValid() else None,
                      "is_instance_proxy": bool(prim.IsInstanceProxy()) if prim and prim.IsValid() else None,
                      "instance_root_ancestors": instance_roots,
                      "point_instancer_ancestors": ancestors})
    metadata = {row["collision"]: row for row in prims}
    owners = _property_colliders(occupancy, physics_step)
    property_controls = _robot_property_controls(stage, entities, property_reader, physics_step,
                                                 snapshot or {}, cases)
    for control in property_controls:
        if control["status"] == "property_matched":
            for path in control["expected_collision_paths"]:
                candidates = owners.get(path, [])
                if len(candidates) == 1 and candidates[0]["body_id"] == control["body_id"]:
                    candidates[0]["property_inventory_complete"] = True
    cross_checks = []
    for case in cases:
        for row in case["queries"]:
            for hit in row["callbacks"]:
                candidates = owners.get(hit["collision"], [])
                prim = metadata[hit["collision"]]
                paths_agree = all(hit.get(key + "_encoded") is not None
                                  and hit.get(key + "_decoded") == hit[key]
                                  and key + "_decode_error" not in hit
                                  for key in ("collision", "rigid_body"))
                exact = (len(candidates) == 1 and candidates[0]["body_id"] == hit["rigid_body"]
                         and candidates[0]["property_inventory_complete"]
                         and paths_agree and prim["prim_valid"]
                         and not prim["point_instancer_ancestors"])
                relation = "unknown_identity"
                if exact:
                    relation = ("property_matched_self_path" if candidates[0]["entity_id"] == case["id"]
                                else "property_matched_other_robot_path")
                cross_checks.append({"case_id": case["id"], "operation": row["operation"],
                    "argument_mode": row["argument_mode"], "hit_ordinal": hit["ordinal"],
                    "collision": hit["collision"], "rigid_body": hit["rigid_body"],
                    "protoIndex": hit["protoIndex"], "encoded_paths_agree": paths_agree,
                    "property_owner_ids": [candidate["entity_id"] for candidate in candidates],
                    "relation": relation})
    return {"schema": "eai.benchmark.scene_query_calibration", "version": 1,
            "status": "diagnostic_only", "physics_step": physics_step,
            "ordinary_hit_sentinel": 0xFFFFFFFF, "cases": cases,
            "hit_prim_metadata": prims,
            "property_controls": property_controls,
            "hit_path_cross_checks": cross_checks,
            "stage_instance_inventory": _stage_instance_inventory(stage),
            "scene_clearance_validated": False,
            "scoreable": False}


__all__ = ["calibrate_scene_queries"]
