"""Read-only USD geometry + Isaac tensor host adapter for current occupancy.

collect_native_occupancy(stage, scene, *, robot_bindings, readback_context,
                         now_s, held_bindings=(), joint_reader=None, max_age_s=.1,
                         shape_bounds_reader=None)

robot_bindings is the complete list of {id, scene_key, instance_index,
root_path, activity: active|idle}, covering EVERY scene.articulations instance.
held_bindings is {id, scene_key, instance_index, root_path, holder_id, joint_path}
for single-body scene.rigid_objects held by one robot. Paired objects instead
provide holder_bindings=[{holder_id, joint_path}, {holder_id, joint_path}]. Other scene rigid
objects remain external obstacles for the existing PhysX scene query.

readback_context requires scene_id, snapshot_id, source_id, observed_at_s,
physics_step, frame_id='USD_world', meters_per_unit=1, stage_identifier,
physx_view_frame='USD_world', readback_frozen=True, kinematics_current=True.
The host MUST pause stepping and refresh articulation kinematics before the
read, and attest the tensor view uses the global frame (no subspace transform).
No USD world translation is used as a live pose. No profile/extent fallback.

For holds, joint_reader(path) must return current PhysX readback with the same
context (scene/snapshot/frame/units/time/step), source_id, joint_path,
kind='physx_fixed_joint', body0, body1, enabled=True, broken=False. A stage-only
joint declaration or an attachment request is insufficient. This module does
not create the reader, mutate the stage, or certify the solver/readback source.

All links remain in body_audit. Fully loaded bodies with no enabled CollisionAPI
are known noncolliding and have no row in the collision-only snapshot/roster.
Entities are never omitted: an entirely noncolliding entity remains blocked
because the frozen downstream contract requires a nonempty collision inventory.
Unsupported geometry and missing links block the whole snapshot. A supplied
same-step CPU PhysX property reader can cover convex decomposition and Xform
colliders after complete path and callback checks. SDF, deformables, point
instancers, unloaded payloads, animated geometry and sheared/reflected rigid-body
frames remain blocked.
"""

from copy import deepcopy
import hashlib
import itertools
import json
import math
from pathlib import Path

from . import goal_live_occupancy as live


MAX_PRIMS = 200_000
MAX_POINTS = 1_000_000


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("finite numeric measurement required")
    return float(value)


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("explicit identity required")
    return value


def _path(value):
    from pxr import Sdf
    _name(value)
    path = Sdf.Path(value)
    if not path.IsAbsolutePath() or not path.IsPrimPath() or path == Sdf.Path.absoluteRootPath:
        raise ValueError("absolute prim identity required")
    return value


def _vec(value, n):
    if not isinstance(value, (tuple, list)) or len(value) != n:
        raise ValueError(f"expected {n} measurement components")
    return [_number(v) for v in value]


def _host_array(value):
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return deepcopy(value)


def _pose(row):
    row = _vec(row, 7)
    quat = [row[6], *row[3:6]]  # PhysX tensor XYZW -> occupancy WXYZ
    if abs(math.hypot(*quat) - 1) > 1e-6:
        raise ValueError("nonunit live link quaternion")
    return row[:3], quat


def _get(attr):
    if not attr or attr.ValueMightBeTimeVarying():
        raise ValueError("missing or time-varying geometry attribute")
    value = attr.Get()
    if value is None:
        raise ValueError("unresolved geometry attribute")
    return value


def _local_geometry(prim):
    """Source-geometry enclosure, never authored extent/visual aggregate bbox."""
    from pxr import UsdGeom, UsdPhysics
    kind = prim.GetTypeName()
    if any("Deformable" in a or "Particle" in a for a in prim.GetAppliedSchemas()):
        raise ValueError("unsupported deformable collision geometry")
    if any(str(a.GetName()).startswith("primvars:skel:") for a in prim.GetAttributes()):
        raise ValueError("unsupported skinned collision geometry")
    measured = {"type": kind}
    if prim.IsA(UsdGeom.Mesh):
        mesh = UsdGeom.Mesh(prim)
        approximation = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
        approximation = "none" if approximation is None else str(approximation)
        if approximation not in ("none", "convexHull"):
            raise ValueError(f"unsupported cooked mesh approximation: {approximation}")
        raw = _get(mesh.GetPointsAttr())
        if not 3 <= len(raw) <= MAX_POINTS:
            raise ValueError("empty or excessive mesh point inventory")
        points = [_vec(list(p), 3) for p in raw]
        counts = list(_get(mesh.GetFaceVertexCountsAttr()))
        indices = list(_get(mesh.GetFaceVertexIndicesAttr()))
        if (not counts or any(n < 3 for n in counts) or sum(counts) != len(indices)
                or any(i < 0 or i >= len(points) for i in indices)):
            raise ValueError("invalid mesh topology")
        low = [min(p[i] for p in points) for i in range(3)]
        high = [max(p[i] for p in points) for i in range(3)]
        measured.update(approximation=approximation, point_count=len(points),
                        geometry_sha256=_hash([points, counts, indices]))
    else:
        if prim.IsA(UsdGeom.Cube):
            size = _number(_get(UsdGeom.Cube(prim).GetSizeAttr()))
            if size <= 0:
                raise ValueError("nonpositive cube size")
            half = [size / 2] * 3
            measured["size"] = size
        elif prim.IsA(UsdGeom.Sphere):
            radius = _number(_get(UsdGeom.Sphere(prim).GetRadiusAttr()))
            if radius <= 0:
                raise ValueError("nonpositive sphere radius")
            half = [radius] * 3
            measured["radius"] = radius
        elif kind in ("Capsule", "Cylinder", "Cone"):
            shape = getattr(UsdGeom, kind)(prim)
            radius = _number(_get(shape.GetRadiusAttr()))
            height = _number(_get(shape.GetHeightAttr()))
            axis = str(_get(shape.GetAxisAttr()))
            if radius <= 0 or height <= 0 or axis not in ("X", "Y", "Z"):
                raise ValueError("invalid axial primitive geometry")
            half = [radius] * 3
            half["XYZ".index(axis)] = height / 2 + (radius if kind == "Capsule" else 0)
            measured.update(radius=radius, height=height, axis=axis)
        else:
            raise ValueError(f"unsupported CollisionAPI geometry: {kind}")
        low, high = [-x for x in half], half
    return low, high, measured


def _requires_cooked_bounds(prim):
    from pxr import UsdGeom, UsdPhysics
    if prim.IsA(UsdGeom.Mesh):
        approximation = UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get()
        if str(approximation) == "convexDecomposition":
            mesh = UsdGeom.Mesh(prim)
            for attr in (mesh.GetPointsAttr(), mesh.GetFaceVertexCountsAttr(), mesh.GetFaceVertexIndicesAttr()):
                _get(attr)
            return True
    return prim.GetTypeName() == "Xform"


def _body_geometry(stage, body, colliders, *, shape_bounds_reader=None, physics_step=None):
    from pxr import Gf, UsdGeom
    cache = UsdGeom.XformCache()
    world = cache.GetLocalToWorldTransform(body)
    basis = [world.TransformDir(Gf.Vec3d(*(int(i == j) for j in range(3)))) for i in range(3)]
    scales = [float(v.GetLength()) for v in basis]
    if (any(not math.isfinite(s) or s <= 0 for s in scales)
            or world.GetDeterminant() <= 0
            or any(abs(float(basis[i] * basis[j]) / (scales[i]*scales[j])) > 1e-6
                   for i in range(3) for j in range(i))):
        raise ValueError("unsupported sheared/reflected/degenerate body frame")
    boxes, evidence = [], []
    native = False
    for collider in colliders:
        if _requires_cooked_bounds(collider):
            native = True
        else:
            _local_geometry(collider)
    if native:
        if shape_bounds_reader is None:
            raise ValueError("cooked or Xform collision geometry requires native property bounds")
        bounds, evidence = shape_bounds_reader(stage, body, colliders)
        paths = [str(prim.GetPath()) for prim in colliders]
        if (not isinstance(evidence, list) or len(evidence) != len(paths)
                or sorted(row.get("collision_path") for row in evidence) != sorted(paths)):
            raise ValueError("native property collider inventory mismatch")
        for row in evidence:
            query = row.get("query", {})
            if (query.get("body_path") != str(body.GetPath())
                    or query.get("physics_step") != physics_step
                    or query.get("complete") is not True
                    or row.get("property_callback", {}).get("path") != row["collision_path"]):
                raise ValueError("native property query provenance incomplete")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValueError("invalid native property body bounds")
        low, high = _vec(bounds[0], 3), _vec(bounds[1], 3)
        if any(a >= b for a, b in zip(low, high)):
            raise ValueError("invalid native property body bounds")
        return [low, high], evidence
    for collider in colliders:
        cursor = collider
        while cursor and cursor != body:
            xform = UsdGeom.Xformable(cursor)
            if xform and (xform.GetResetXformStack() or xform.TransformMightBeTimeVarying()):
                raise ValueError("detached or animated collider transform")
            cursor = cursor.GetParent()
        relative, reset = cache.ComputeRelativeTransform(collider, body)
        if reset:
            raise ValueError("collider transform resets body frame")
        low, high, source = _local_geometry(collider)
        points = [relative.Transform(Gf.Vec3d(*p)) for p in itertools.product(*zip(low, high))]
        transformed = [[_number(float(p[i])*scales[i]) for i in range(3)] for p in points]
        bounds = [[min(p[i] for p in transformed) for i in range(3)],
                  [max(p[i] for p in transformed) for i in range(3)]]
        boxes.append(bounds)
        evidence.append({"collision_path": str(collider.GetPath()),
                         "instance_proxy": collider.IsInstanceProxy(),
                         "geometry": source, "body_local_bounds_m": bounds,
                         "collider_to_body_matrix": [list(row) for row in relative],
                         "effective_body_scale": scales})
    if not boxes:
        raise ValueError("body has no enabled measurable colliders; cannot omit body")
    bounds = [[min(b[0][i] for b in boxes) for i in range(3)],
              [max(b[1][i] for b in boxes) for i in range(3)]]
    if any(lo >= hi for lo, hi in zip(*bounds)):
        raise ValueError("degenerate collision envelope")
    return bounds, evidence


def _prims(stage):
    from pxr import Usd
    result = []
    # Default traversal excludes unloaded prims; omitting a payload could make
    # an unknown collider subtree look like a known noncolliding body.
    predicate = Usd.PrimIsActive & Usd.PrimIsDefined & ~Usd.PrimIsAbstract
    for prim in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies(predicate)):
        if len(result) >= MAX_PRIMS:
            raise ValueError("stage audit prim budget exceeded")
        result.append(prim)
    return result


def _context(stage, context, now_s, max_age_s):
    from pxr import UsdGeom
    if not isinstance(context, dict):
        raise ValueError("trusted readback context required")
    for key in ("scene_id", "snapshot_id", "source_id"):
        _name(context.get(key))
    now, observed, age = map(_number, (now_s, context.get("observed_at_s"), max_age_s))
    if not 0 <= age <= 1 or not 0 <= observed <= now or now-observed > age:
        raise ValueError("stale/future readback or invalid age bound")
    if (context.get("frame_id") != "USD_world" or context.get("physx_view_frame") != "USD_world"
            or type(context.get("meters_per_unit")) not in (float, int)
            or context["meters_per_unit"] != 1
            or UsdGeom.GetStageMetersPerUnit(stage) != 1 or UsdGeom.GetStageUpAxis(stage) != "Z"):
        raise ValueError("explicit metric Z-up USD_world tensor frame required")
    if (context.get("stage_identifier") != stage.GetRootLayer().identifier
            or context.get("readback_frozen") is not True or context.get("kinematics_current") is not True
            or type(context.get("physics_step")) is not int or context["physics_step"] < 0):
        raise ValueError("stage/step/frozen readback/kinematics context missing")
    return deepcopy(context)


def _views(scene, bindings):
    """Read each view once, validate every row without squeezing away instances."""
    articulations = scene.articulations
    if not isinstance(bindings, (tuple, list)) or not 0 < len(bindings) <= live.MAX_ENTITIES:
        raise ValueError("complete bounded robot bindings required")
    expected, samples = set(), {}
    for key, articulation in articulations.items():
        view = articulation.root_physx_view
        paths, transforms = deepcopy(view.link_paths), _host_array(view.get_link_transforms())
        if not isinstance(paths, (tuple, list)) or not paths or len(paths) != len(transforms):
            raise ValueError("PhysX link path/instance tensor shape mismatch")
        for index, (row_paths, row_poses) in enumerate(zip(paths, transforms)):
            if (not isinstance(row_paths, (tuple, list)) or not row_paths
                    or len(row_paths) != len(row_poses) or len(set(row_paths)) != len(row_paths)):
                raise ValueError("PhysX per-instance link inventory mismatch")
            expected.add((key, index))
            samples[key, index] = {_path(p): _pose(t) for p, t in zip(row_paths, row_poses)}
    actual = []
    for binding in bindings:
        if type(binding.get("instance_index")) is not int or binding["instance_index"] < 0:
            raise ValueError("explicit nonnegative articulation instance index required")
        actual.append((_name(binding.get("scene_key")), binding["instance_index"]))
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError("bindings must cover every scene articulation instance, including idle")
    if sum(len(row) for row in samples.values()) > live.MAX_BODIES:
        raise ValueError("body readback budget exceeded")
    return samples


def _held_pose(scene, binding):
    view = scene.rigid_objects[binding["scene_key"]].root_physx_view
    paths, rows = deepcopy(view.prim_paths), _host_array(view.get_transforms())
    index = binding.get("instance_index")
    if (type(index) is not int or index < 0 or len(paths) != len(rows) or index >= len(paths)
            or paths[index] != binding["root_path"]):
        raise ValueError("held rigid-object PhysX identity/instance mismatch")
    return {paths[index]: _pose(rows[index])}


def _joint(stage, binding, robot_paths, context, reader):
    from pxr import UsdPhysics
    path = _path(binding.get("joint_path"))
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsA(UsdPhysics.FixedJoint):
        raise ValueError("actual FixedJoint prim required for hold")
    joint = UsdPhysics.FixedJoint(prim)
    targets = [list(map(str, rel.GetTargets())) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel())]
    if any(len(row) != 1 for row in targets) or joint.GetJointEnabledAttr().Get() is not True:
        raise ValueError("enabled single-pair attachment joint required")
    a, b = targets[0][0], targets[1][0]
    other = binding["root_path"]
    if not ((a in robot_paths and b == other) or (b in robot_paths and a == other)):
        raise ValueError("joint endpoints disagree with actual holder/object bodies")
    if reader is None:
        raise ValueError("PhysX joint readback required; USD authorship is insufficient")
    sample = deepcopy(reader(path))
    for key in ("scene_id", "snapshot_id", "frame_id", "meters_per_unit", "observed_at_s", "physics_step"):
        if sample.get(key) != context[key] or type(sample.get(key)) is bool:
            raise ValueError("incoherent live joint source context")
    _name(sample.get("source_id"))
    if (sample.get("kind") != "physx_fixed_joint" or sample.get("joint_path") != path
            or sample.get("body0") != a or sample.get("body1") != b
            or sample.get("enabled") is not True or sample.get("broken") is not False):
        raise ValueError("live joint disabled/broken/unknown or identity mismatch")
    return {**context, "kind": "fixed_attachment_readback", "object_id": binding["id"],
            "holder_id": binding["holder_id"], "joint_path": path,
            "body0": a, "body1": b, "physx_readback": sample,
            "solver_response_validated": False}


def _joint_body_target(stage, path):
    """Resolve an Xformable endpoint to its nearest enabled USD rigid body.

    Do not skip an unknown/disabled nearer body to claim a measured ancestor.
    Entity ownership is checked separately against exact live body identities.
    """
    from pxr import UsdGeom, UsdPhysics
    result = {"target_path": path, "body_path": None}
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsActive() or not prim.IsLoaded() or not UsdGeom.Xformable(prim):
        return {**result, "reason": "missing/inactive/unloaded/non-Xformable joint target"}
    while prim:
        if not prim.IsActive() or not prim.IsLoaded():
            return {**result, "reason": "inactive/unloaded joint target ancestor"}
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            if UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get() is not True:
                return {**result, "reason": "nearest joint target rigid body is disabled/unknown"}
            return {**result, "body_path": str(prim.GetPath())}
        prim = prim.GetParent()
    return {**result, "reason": "joint target has no rigid body ancestor"}


def collect_native_occupancy(stage, scene, *, robot_bindings, readback_context,
                             now_s, held_bindings=(), joint_reader=None, max_age_s=0.1,
                             shape_bounds_reader=None):
    """Return status, snapshot, expected_inventory, occupancy and body/joint audits.

    On ANY unknown geometry or missing identity: status='blocked', snapshot=None,
    expected_inventory=[]; no partial list can masquerade as complete occupancy.
    joint_audit preserves raw targets and nearest enabled rigid-body ancestors;
    only exact measured-body ownership can classify a joint as internal.
    Successful inputs can be passed directly to goal_live_occupancy.check_live_segment.
    Source assertions are trusted host inputs, not authentication or solver proof.
    """
    result = {"schema": "eai.benchmark.native_occupancy", "version": 1,
              "status": "blocked", "snapshot": None, "expected_inventory": [],
              "occupancy": None, "body_audit": [], "joint_audit": [], "blockers": [], "stage_writes": 0,
              "measurement_authenticity_validated": False, "scoreable": False,
              "source_files": {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                               for p in (__file__, live.__file__)}}
    try:
        from pxr import UsdGeom, UsdPhysics
        context = _context(stage, readback_context, now_s, max_age_s)
        samples = _views(scene, robot_bindings)
        if not isinstance(held_bindings, (tuple, list)) or len(held_bindings)+len(robot_bindings) > live.MAX_ENTITIES:
            raise ValueError("bounded held-object inventory required")
        bindings = [*robot_bindings, *held_bindings]
        ids, roots, all_poses = set(), [], {}
        prims = _prims(stage)
        robot_body_sets = {}
        for binding in bindings:
            identifier, root_path = _name(binding.get("id")), _path(binding.get("root_path"))
            if identifier in ids or any(root_path == r or root_path.startswith(r+"/") or r.startswith(root_path+"/") for r in roots):
                raise ValueError("overlapping entity identity/root binding")
            ids.add(identifier); roots.append(root_path)
            root = stage.GetPrimAtPath(root_path)
            if not root or not root.IsActive() or not root.IsLoaded():
                raise ValueError("missing/unloaded entity root")
            robot = binding in robot_bindings
            if robot:
                if binding.get("activity") not in ("active", "idle") or binding.get("holder_id") is not None:
                    raise ValueError("explicit active/idle robot activity and no holder alias required")
                poses = samples[binding["scene_key"], binding["instance_index"]]
                robot_body_sets[identifier] = set(poses)
            else:
                poses = _held_pose(scene, binding)
            descendants = [p for p in prims if p.GetPath().HasPrefix(root.GetPath())]
            if any(not p.IsLoaded() or p.IsA(UsdGeom.PointInstancer) for p in descendants):
                raise ValueError("unloaded or point-instanced robot/held geometry unsupported")
            bodies = {str(p.GetPath()): p for p in descendants if p.HasAPI(UsdPhysics.RigidBodyAPI)}
            if set(bodies) != set(poses):
                raise ValueError(f"USD rigid bodies != PhysX links for {identifier}: usd={sorted(bodies)} physx={sorted(poses)}")
            if any(UsdPhysics.RigidBodyAPI(p).GetRigidBodyEnabledAttr().Get() is not True for p in bodies.values()):
                raise ValueError("disabled/unknown body in live link inventory")
            grouped = {p: [] for p in bodies}
            disabled = {p: [] for p in bodies}
            for prim in descendants:
                if not prim.HasAPI(UsdPhysics.CollisionAPI):
                    continue
                enabled = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get()
                if enabled is not True and enabled is not False:
                    raise ValueError("unknown collider enable state")
                parent = prim
                while parent and str(parent.GetPath()) not in bodies:
                    parent = parent.GetParent()
                if not parent:
                    if enabled is False:
                        continue
                    raise ValueError(f"collider lacks a measured body: {prim.GetPath()}")
                if enabled is False:
                    disabled[str(parent.GetPath())].append(str(prim.GetPath()))
                else:
                    grouped[str(parent.GetPath())].append(prim)
            all_poses[identifier] = (binding, poses, bodies, grouped, robot, disabled)
        if sum(len(row[1]) for row in all_poses.values()) > live.MAX_BODIES:
            raise ValueError("total body readback budget exceeded")
        snapshot = {**context, "bodies": [], "attachments": []}
        roster = []
        for identifier, (binding, poses, bodies, grouped, robot, disabled) in all_poses.items():
            entity = {"id": identifier, "kind": "robot" if robot else "held_object", "body_ids": [], "collision_paths": []}
            if robot:
                entity["activity"] = binding["activity"]
            else:
                holders = binding.get("holder_bindings")
                if holders is not None:
                    if (binding.get("holder_id") is not None or binding.get("joint_path") is not None
                            or not isinstance(holders, list) or len(holders) != 2
                            or any(not isinstance(row, dict) for row in holders)):
                        raise ValueError("paired held binding requires two explicit joint owners")
                    ids = [_name(row.get("holder_id")) for row in holders]
                    paths = [_path(row.get("joint_path")) for row in holders]
                    if len(set(ids)) != 2 or len(set(paths)) != 2:
                        raise ValueError("paired held joint identities must be distinct")
                    entity["holder_ids"] = ids
                    result["version"] = 2
                    for holder in holders:
                        joint_binding = {**binding, **holder}
                        snapshot["attachments"].append(_joint(stage, joint_binding,
                            robot_body_sets[holder["holder_id"]], context, joint_reader))
                else:
                    entity["holder_id"] = binding["holder_id"]
                    snapshot["attachments"].append(_joint(stage, binding,
                        robot_body_sets[binding["holder_id"]], context, joint_reader))
            for body_path, (position, quat) in poses.items():
                audit = {"entity_id": identifier, "body_path": body_path, "status": "blocked",
                         "collision_paths": [str(p.GetPath()) for p in grouped[body_path]],
                         "disabled_collision_paths": disabled[body_path],
                         "position_xyz_m": position, "orientation_wxyz": quat,
                         "pose_source": {**context, "kind": "physx_body_pose", "body_prim_path": body_path}}
                result["body_audit"].append(audit)
                if not grouped[body_path]:
                    audit.update(status="known_noncolliding", occupancy="empty",
                                 reason="all_collision_apis_disabled" if disabled[body_path] else "no_collision_api_in_loaded_body_subtree")
                    continue
                try:
                    bounds, evidence = _body_geometry(stage, bodies[body_path], grouped[body_path],
                        shape_bounds_reader=shape_bounds_reader, physics_step=context["physics_step"])
                    revision = _hash(evidence)
                    source = {**context, "body_prim_path": body_path, "configuration_revision": revision}
                    body = {"id": body_path, "entity_id": identifier, "prim_path": body_path,
                            "collision_paths": audit["collision_paths"], "position_xyz_m": position,
                            "orientation_wxyz": quat, "bounds_frame": "body_local",
                            "local_collision_bounds_m": bounds,
                            "pose_source": {**source, "kind": "physx_body_pose", "api": "get_link_transforms" if robot else "get_transforms"},
                            "bounds_source": {**source, "kind": "physx_collision_bounds" if
                                              any("property_callback" in row for row in evidence) else "usd_collision_bounds",
                                              "covers_all_enabled_colliders": True,
                                              "collider_measurements": evidence}}
                    snapshot["bodies"].append(body)
                    entity["body_ids"].append(body_path)
                    entity["collision_paths"].extend(audit["collision_paths"])
                    audit.update(status="measured", local_collision_bounds_m=bounds)
                except Exception as exc:
                    audit["reason"] = f"{type(exc).__name__}:{exc}"
                    result["blockers"].append(f"{body_path}:{audit['reason']}")
            if not entity["body_ids"] and all(a["status"] == "known_noncolliding"
                    for a in result["body_audit"] if a["entity_id"] == identifier):
                result["blockers"].append(f"{identifier}:entirely_noncolliding_entity_not_representable_in_frozen_occupancy_contract")
            roster.append(entity)
        # Discover unlisted inter-entity joints, including joints outside roots.
        owner = {p: identifier for identifier, row in all_poses.items() for p in row[1]}
        declared = {holder["joint_path"] for binding in held_bindings
                    for holder in binding.get("holder_bindings", [binding])}
        root_paths = [stage.GetPrimAtPath(root).GetPath() for root in roots]
        for prim in prims:
            if not prim.IsA(UsdPhysics.Joint):
                continue
            joint = UsdPhysics.Joint(prim)
            if joint.GetJointEnabledAttr().Get() is False:
                continue
            endpoints = [list(rel.GetTargets()) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel())]
            targets = [path for row in endpoints for path in row]
            resolved = [_joint_body_target(stage, str(path)) for path in targets]
            # Prefixes only decide audit scope; they NEVER establish ownership.
            related = (any(row["body_path"] in owner for row in resolved)
                       or any(path.HasPrefix(root) for root in root_paths
                              for path in [prim.GetPath(), *targets]))
            if not related:
                continue
            audit = {"joint_path": str(prim.GetPath()), "status": "blocked", "endpoints": resolved}
            result["joint_audit"].append(audit)
            if joint.GetJointEnabledAttr().Get() is not True or any(len(row) != 1 for row in endpoints):
                raise ValueError(f"missing/unknown joint enable state or endpoint pair: {prim.GetPath()}")
            if any(row["body_path"] not in owner for row in resolved):
                raise ValueError(f"unresolved/unmeasured joint endpoint: {prim.GetPath()} {resolved}")
            owners = {owner[row["body_path"]] for row in resolved}
            if len(owners) > 1 and str(prim.GetPath()) not in declared:
                raise ValueError(f"unlisted external attachment/joint: {prim.GetPath()}")
            audit["status"] = "internal" if len(owners) == 1 else "declared_attachment"
        if result["blockers"]:
            return result
        measured = live.collect_current_occupancy(snapshot, expected_inventory=roster, now_s=now_s, max_age_s=max_age_s)
        result["occupancy"] = measured
        if measured["status"] != "current_occupancy":
            result["blockers"].extend(measured["blockers"])
            return result
        result.update(status="current_occupancy", snapshot=snapshot, expected_inventory=roster)
    except Exception as exc:
        result["blockers"].append(f"native_readback_unavailable:{type(exc).__name__}:{exc}")
    return result


__all__ = ["collect_native_occupancy"]
