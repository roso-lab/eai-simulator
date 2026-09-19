"""Current collision occupancy and fixed-configuration translation diagnostics.

Input belongs to a trusted runtime, never to a participant. Source labels and
hashes bind data but cannot authenticate who measured it. No USD, Isaac or ROS
imports, scene edits, profile fallback, or discrete-ray traversal claims.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path


MAX_ENTITIES = 128
MAX_BODIES = 256
MAX_HITS = 256
MAX_TRANSLATION_M = 100.0
EPSILON_M = 1e-6


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def _number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}: finite number required")
    return float(value)


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: identity/source missing")
    return value


def _path(value):
    _name(value, "prim path")
    if not value.startswith("/") or any(not p.isidentifier() for p in value[1:].split("/")):
        raise ValueError("canonical absolute prim path required")
    return value


def _vec(value, n=3):
    if not isinstance(value, (list, tuple)) or len(value) != n:
        raise ValueError(f"expected {n} coordinates")
    return [_number(v, "coordinate") for v in value]


def _index(rows, key, limit, label):
    if not isinstance(rows, list) or not 0 < len(rows) <= limit:
        raise ValueError(f"{label}: nonempty bounded inventory required")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label}: record required")
        identifier = _name(row.get(key), label)
        if identifier in result:
            raise ValueError(f"{label}: duplicate identity {identifier}")
        result[identifier] = row
    return result


def _fresh(record, now, max_age):
    observed = _number(record.get("observed_at_s"), "observed_at_s")
    if observed < 0 or observed > now or now - observed > max_age:
        raise ValueError("stale or future readback")
    return observed


def _context(record, snapshot, now, max_age):
    if not isinstance(record, dict):
        raise ValueError("source context missing")
    _name(record.get("source_id"), "source_id")
    if type(record.get("meters_per_unit")) not in (int, float):
        raise ValueError("source units must be numeric")
    for key in ("scene_id", "snapshot_id", "frame_id", "meters_per_unit", "observed_at_s"):
        if record.get(key) != snapshot.get(key):
            raise ValueError(f"incoherent source context: {key}")
    _fresh(record, now, max_age)


def _rotation(quaternion):
    w, x, y, z = _vec(quaternion, 4)
    norm = math.hypot(w, x, y, z)
    if abs(norm - 1.0) > 1e-6:
        raise ValueError("orientation must be a normalized WXYZ quaternion")
    w, x, y, z = (v / norm for v in (w, x, y, z))
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]


def _world_bounds(body):
    position = _vec(body.get("position_xyz_m"))
    rotation = _rotation(body.get("orientation_wxyz"))
    bounds = body.get("local_collision_bounds_m")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("local collision bounds missing; profiles are not readback")
    low, high = map(_vec, bounds)
    if any(a >= b for a, b in zip(low, high)):
        raise ValueError("collision envelope must have positive size in three axes")
    center = [(a+b)/2 for a,b in zip(low, high)]
    half = [(b-a)/2 for a,b in zip(low, high)]
    world_center = [position[i] + sum(rotation[i][j]*center[j] for j in range(3)) for i in range(3)]
    world_half = [sum(abs(rotation[i][j])*half[j] for j in range(3)) for i in range(3)]
    world = [_vec([c-h for c,h in zip(world_center, world_half)]),
             _vec([c+h for c,h in zip(world_center, world_half)])]
    if any(low >= high for low, high in zip(*world)):
        raise ValueError("world envelope lost positive size at coordinate precision")
    return world


def collect_current_occupancy(snapshot, *, expected_inventory, now_s, max_age_s=0.1):
    """Validate complete runtime inventories and transform collider-local AABBs.

    expected_inventory is a separate trusted entity roster including idle
    robots and held objects, with exact body_ids and collision_paths. Snapshot
    bodies carry PhysX body poses and measured collision bounds, each with a
    coherent scene/snapshot/frame/time/source/configuration revision context.
    Invalid input produces blocked evidence, never partial clear occupancy.
    """
    result = {"schema": "eai.benchmark.current_occupancy", "version": 1,
              "status": "blocked", "entities": [], "blockers": [],
              "source_files": {str(Path(__file__).resolve()): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              "measurement_authenticity_validated": False, "scoreable": False}
    try:
        now = _number(now_s, "now_s")
        max_age = _number(max_age_s, "max_age_s")
        if now < 0 or not 0 <= max_age <= 1.0:
            raise ValueError("invalid time or max_age outside [0,1] seconds")
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot missing")
        _name(snapshot.get("scene_id"), "scene_id")
        _name(snapshot.get("snapshot_id"), "snapshot_id")
        _name(snapshot.get("source_id"), "source_id")
        if snapshot.get("frame_id") != "USD_world" or type(snapshot.get("meters_per_unit")) not in (int, float) or snapshot["meters_per_unit"] != 1:
            raise ValueError("metric USD_world coordinates required")
        _fresh(snapshot, now, max_age)
        entities = _index(expected_inventory, "id", MAX_ENTITIES, "entity roster")
        bodies = _index(snapshot.get("bodies"), "id", MAX_BODIES, "body readbacks")
        expected_bodies, all_paths, body_paths = set(), set(), set()
        attachments = snapshot.get("attachments", [])
        if not isinstance(attachments, list) or len(attachments) > MAX_ENTITIES:
            raise ValueError("invalid attachment inventory")
        attached = {}
        for attachment in attachments:
            object_id = _name(attachment.get("object_id"), "attached object")
            if object_id in attached:
                raise ValueError("duplicate attachment")
            attached[object_id] = attachment
        for identifier, entity in entities.items():
            if entity.get("kind") not in ("robot", "held_object"):
                raise ValueError("only robot/held_object inventory supported")
            if entity["kind"] == "robot" and entity.get("activity") not in ("active", "idle"):
                raise ValueError("active or idle robot state required")
            if entity["kind"] == "robot" and entity.get("holder_id") is not None:
                raise ValueError("robot cannot declare a holder/self-exclusion alias")
            body_ids, paths = entity.get("body_ids"), entity.get("collision_paths")
            if not isinstance(body_ids, list) or not body_ids or len(set(body_ids)) != len(body_ids):
                raise ValueError("exact unique body inventory required")
            if not isinstance(paths, list) or not paths or len(set(paths)) != len(paths):
                raise ValueError("exact unique collider inventory required")
            paths = {_path(p) for p in paths}
            if expected_bodies.intersection(body_ids) or all_paths.intersection(paths):
                raise ValueError("body/collider aliases across entities")
            expected_bodies.update(body_ids)
            all_paths.update(paths)
            if entity["kind"] == "held_object":
                holder = entity.get("holder_id")
                if holder not in entities or entities[holder]["kind"] != "robot":
                    raise ValueError("held object holder identity missing")
                attachment = attached.get(identifier)
                _context(attachment, snapshot, now, max_age)
                if attachment.get("kind") != "fixed_attachment_readback" or attachment.get("holder_id") != holder:
                    raise ValueError("fixed live attachment binding required")
            elif identifier in attached:
                raise ValueError("robot cannot be a held object")
            measured, found_paths = [], set()
            for body_id in body_ids:
                body = bodies.get(body_id)
                if not body or body.get("entity_id") != identifier:
                    raise ValueError(f"body identity missing/mismatched: {body_id}")
                body_path = _path(body.get("prim_path"))
                if body_path in body_paths:
                    raise ValueError("aliased body prim identity")
                body_paths.add(body_path)
                if body.get("bounds_frame") != "body_local":
                    raise ValueError("collision bounds must declare body_local frame")
                local_paths = body.get("collision_paths")
                if not isinstance(local_paths, list) or not local_paths:
                    raise ValueError("body collider identity missing")
                if len(set(local_paths)) != len(local_paths) or found_paths.intersection(local_paths):
                    raise ValueError("duplicate body collider binding")
                for path in local_paths:
                    _path(path)
                    if path != body_path and not path.startswith(body_path + "/"):
                        raise ValueError("collider not within its measured body frame")
                found_paths.update(local_paths)
                pose, bounds = body.get("pose_source"), body.get("bounds_source")
                for source in (pose, bounds):
                    _context(source, snapshot, now, max_age)
                    _name(source.get("configuration_revision"), "configuration_revision")
                    if source.get("body_prim_path") != body_path:
                        raise ValueError("source is not bound to the measured body prim")
                if pose.get("kind") != "physx_body_pose" or bounds.get("kind") not in ("physx_collision_bounds", "usd_collision_bounds"):
                    raise ValueError("live pose/collision source required, not public profile")
                if pose["configuration_revision"] != bounds["configuration_revision"]:
                    raise ValueError("pose/collision configuration revision mismatch")
                if bounds.get("covers_all_enabled_colliders") is not True:
                    raise ValueError("collision envelope coverage unverified")
                measured.append({"body_id": body_id, "prim_path": body_path,
                                 "collision_paths": deepcopy(local_paths), "world_aabb_m": _world_bounds(body),
                                 "pose_source": deepcopy(pose), "bounds_source": deepcopy(bounds)})
            if found_paths != paths:
                raise ValueError("collider roster differs from measured bodies")
            result["entities"].append({"id": identifier, "kind": entity["kind"],
                "activity": entity.get("activity"), "holder_id": entity.get("holder_id"),
                "bodies": measured, "world_aabb_m": [
                    [min(b["world_aabb_m"][0][i] for b in measured) for i in range(3)],
                    [max(b["world_aabb_m"][1][i] for b in measured) for i in range(3)]]})
        if set(bodies) != expected_bodies or set(attached) != {k for k,v in entities.items() if v["kind"] == "held_object"}:
            raise ValueError("incomplete or extra body/held-object inventory")
        result.update(status="current_occupancy", observed_at_s=snapshot["observed_at_s"],
                      checked_at_s=now, max_age_s=max_age,
                      scene_id=snapshot["scene_id"], snapshot_id=snapshot["snapshot_id"],
                      input_sha256=_digest({"snapshot": snapshot, "expected_inventory": expected_inventory}),
                      frame_id="USD_world", meters_per_unit=1.0)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result["entities"] = []
        result["blockers"].append(f"invalid_current_occupancy:{exc}")
    return result


def _swept_intersection(moving, obstacle, delta):
    """Closed AABB translation interval via Minkowski difference/slab clipping."""
    enter, leave = 0.0, 1.0
    for i, motion in enumerate(delta):
        low = obstacle[0][i] - moving[1][i] - EPSILON_M
        high = obstacle[1][i] - moving[0][i] + EPSILON_M
        if motion == 0:
            if not low <= 0 <= high:
                return None
        else:
            first, last = sorted((low / motion, high / motion))
            enter, leave = max(enter, first), min(leave, last)
            if enter > leave:
                return None
    return [enter, leave]


def _scene_check(query, bounds, delta, excluded):
    center = [(a+b)/2 for a,b in zip(*bounds)]
    half = [(b-a)/2 + EPSILON_M for a,b in zip(*bounds)]
    length = math.hypot(*delta)
    checks = []
    operations = [("start_overlap", center), ("end_overlap", [a+b for a,b in zip(center, delta)])]
    if length > 0:
        operations.append(("segment_sweep", center))
    for kind, origin in operations:
        record = {"operation": kind, "center_xyz_m": origin, "half_extents_m": half,
                  "hits": [], "excluded_self_hits": [], "unsupported_instance_hits": [], "status": "unknown"}
        if kind == "segment_sweep":
            record.update(direction=[v/length for v in delta], distance_m=length)
        count, errors = 0, []

        def report(hit):
            nonlocal count
            count += 1
            if count > MAX_HITS:
                errors.append("scene_hit_limit")
                return False
            try:
                get = hit.get if isinstance(hit, dict) else lambda key: getattr(hit, key, None)
                path = _path(get("collision"))
                rigid_body = get("rigid_body")
                row = {"collision": path, "rigid_body": _path(rigid_body) if rigid_body else None}
                has_index = "protoIndex" in hit if isinstance(hit, dict) else hasattr(hit, "protoIndex")
                if has_index:
                    index = get("protoIndex")
                    row["protoIndex"] = index if type(index) in (int, str, bool) or index is None else repr(index)
                    # The native SDK uses UINT32_MAX for ordinary prim hits.
                    # A point-instance identity cannot use path-only self exclusion.
                    if type(index) is not int or index != 0xFFFFFFFF:
                        record["unsupported_instance_hits"].append(row)
                        raise ValueError("unsupported or invalid point-instancer protoIndex")
                if path in excluded and row["rigid_body"] != excluded[path]:
                    raise ValueError("self collider/rigid body identity missing or mismatched")
                if kind == "segment_sweep":
                    distance = _number(get("distance"), "hit distance")
                    if not 0 <= distance <= length + EPSILON_M:
                        raise ValueError("sweep hit outside candidate segment")
                    row["distance_m"] = distance
                # Only exact measured colliders of the mover/its held object.
                # An unrelated hit naming our rigid body is never excluded.
                record["excluded_self_hits" if path in excluded else "hits"].append(row)
            except (ValueError, TypeError) as exc:
                errors.append(str(exc))
                return False
            return True

        try:
            if kind == "segment_sweep":
                returned = query.sweep_box_all(tuple(half), tuple(origin), (0.,0.,0.,1.),
                    tuple(v/length for v in delta), length, report, True)
                if type(returned) is not bool or returned != bool(count):
                    errors.append("sweep_completion_callback_mismatch")
            else:
                returned = query.overlap_box(tuple(half), tuple(origin), (0.,0.,0.,1.), report, False)
                if type(returned) is not int or returned != count:
                    errors.append("overlap_completion_callback_mismatch")
            if errors:
                raise ValueError(";".join(errors))
            record["status"] = "blocked" if record["hits"] else "no_external_hit"
        except Exception as exc:
            record["reason"] = f"scene_query_failed:{type(exc).__name__}:{exc}"
        checks.append(record)
    return checks


def check_live_segment(snapshot, *, expected_inventory, moving_robot_id, translation_xyz_m,
                       now_s, query=None, query_context=None, max_age_s=0.1):
    """Check 3-D pure translation, keeping all current body orientations fixed.

    PhysX API: overlap_box(half,center,quat_xyzw,callback,False), and
    sweep_box_all(half,center,quat_xyzw,direction,distance,callback,True).
    Touching and initial penetration block; ground support is NOT exempted.
    Dynamic checks cover current frozen envelopes, not future actor motion.
    Native protoIndex must be the ordinary-hit sentinel 0xFFFFFFFF; explicit
    instance indices are preserved as unsupported evidence and never excluded.
    """
    occupancy = collect_current_occupancy(snapshot, expected_inventory=expected_inventory,
                                         now_s=now_s, max_age_s=max_age_s)
    result = {"schema": "eai.benchmark.live_segment_occupancy", "version": 1,
              "status": "blocked", "occupancy": occupancy,
              "scene_checks": [], "dynamic_intersections": [], "blockers": [],
              "motion_scope": "pure_translation_frozen_body_configuration",
              "dynamic_scope": "instantaneous_snapshot_not_future_trajectories",
              "algorithm": "continuous_box_sweep_and_analytic_swept_aabb_not_discrete_samples",
              "traversal_validated": False, "future_dynamic_clearance_validated": False,
              "measurement_authenticity_validated": False, "scoreable": False}
    if occupancy["status"] != "current_occupancy":
        result["blockers"].append("current_occupancy_unavailable")
        return result
    try:
        _name(moving_robot_id, "moving_robot_id")
        result["moving_robot_id"] = moving_robot_id
        delta = _vec(translation_xyz_m)
        if math.hypot(*delta) > MAX_TRANSLATION_M:
            raise ValueError("translation exceeds bounded query length")
        entities = {e["id"]: e for e in occupancy["entities"]}
        if moving_robot_id not in entities or entities[moving_robot_id]["kind"] != "robot":
            raise ValueError("moving robot identity missing")
        moving = [e for e in entities.values() if e["id"] == moving_robot_id or
                  (e["kind"] == "held_object" and e["holder_id"] == moving_robot_id)]
        moving_ids = {e["id"] for e in moving}
        excluded = {p: b["prim_path"] for e in moving for b in e["bodies"] for p in b["collision_paths"]}
        result.update(translation_xyz_m=delta, excluded_self_collision_paths=sorted(excluded))
        for entity in moving:
            for body in entity["bodies"]:
                for other in entities.values():
                    if other["id"] in moving_ids:
                        continue
                    for obstacle in other["bodies"]:
                        interval = _swept_intersection(body["world_aabb_m"], obstacle["world_aabb_m"], delta)
                        if interval is not None:
                            result["dynamic_intersections"].append({"moving_entity_id": entity["id"],
                                "moving_body_id": body["body_id"], "obstacle_entity_id": other["id"],
                                "obstacle_body_id": obstacle["body_id"], "segment_fraction_interval": interval})
        if result["dynamic_intersections"]:
            result["blockers"].append("other_robot_or_held_object_envelope_intersects")
        _context(query_context, snapshot, _number(now_s, "now_s"), _number(max_age_s, "max_age_s"))
        if query_context.get("kind") != "physx_scene_query" or query_context.get("static_colliders_included") is not True:
            raise ValueError("current static PhysX collision scene coverage missing")
        if query is None:
            raise ValueError("PhysX overlap/sweep query unavailable")
        result["query_context"] = deepcopy(query_context)
        for entity in moving:
            for body in entity["bodies"]:
                checks = _scene_check(query, body["world_aabb_m"], delta, excluded)
                result["scene_checks"].append({"entity_id": entity["id"], "body_id": body["body_id"], "checks": checks})
                if any(c["status"] != "no_external_hit" for c in checks):
                    result["blockers"].append("scene_overlap_sweep_blocked_or_unknown")
        result["blockers"] = sorted(set(result["blockers"]))
        if not result["blockers"]:
            result["status"] = "clear_at_snapshot_for_fixed_translation"
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result["blockers"].append(f"segment_check_unavailable:{exc}")
    return result


__all__ = ["collect_current_occupancy", "check_live_segment"]
