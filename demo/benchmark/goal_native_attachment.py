"""Ideal fixed-point constraints using fresh, caller-owned PhysX body samples.

No simulator, IK or VLA is started. Public task and body bindings are trusted
reviewed host inputs, never participant authority. The host must pause physics
advancement during each synchronous call and supply fresh poses from one step.
The provider contract is documented by prepare_attachment. A source label is
not authentication: the host owns the actual PhysX query implementation.
"""

from copy import deepcopy
import hashlib
import json
import math
import uuid


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _seal(value):
    return {**value, "sha256": _digest(value)}


def _check_seal(value):
    if value.get("sha256") != _digest({k: v for k, v in value.items() if k != "sha256"}):
        raise ValueError("attachment record changed")


def _vec(value, size):
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or any(isinstance(x, bool) or not isinstance(x, (float, int))
                   or not math.isfinite(x) for x in value)):
        raise ValueError("invalid live pose vector")
    return list(map(float, value))


def _mul(a, b):
    w, x, y, z = a
    v, i, j, k = b
    return [w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v]


def _inverse(q):
    return [q[0], -q[1], -q[2], -q[3]]


def _rotate(q, point):
    return _mul(_mul(q, [0, *point]), _inverse(q))[1:]


def _body(stage, binding, *, is_object):
    from pxr import Sdf, UsdGeom, UsdPhysics
    body_path, root_path = binding["body_path"], binding["root_path"]
    for path in (body_path, root_path):
        if not isinstance(path, str) or not Sdf.Path.IsValidPathString(path):
            raise ValueError("invalid bound body/root path")
        if not Sdf.Path(path).IsAbsolutePath() or not Sdf.Path(path).IsPrimPath():
            raise ValueError("body/root must be absolute prim paths")
    root, body = stage.GetPrimAtPath(root_path), stage.GetPrimAtPath(body_path)
    if (not root or not root.IsActive() or not body or not body.IsActive() or not body.IsLoaded()
            or not body.GetPath().HasPrefix(root.GetPath()) or (is_object and body_path != root_path)):
        raise ValueError("body is not an active descendant of its reviewed root")
    rigid = UsdPhysics.RigidBodyAPI(body)
    # Get() resolves the schema fallback True when enabled is unauthored.
    # An absent RigidBodyAPI is still rejected; unknown/blocked values fail closed.
    if not rigid or rigid.GetRigidBodyEnabledAttr().Get() is not True:
        raise ValueError("bound prim is not an enabled RigidBody")
    if is_object and rigid.GetKinematicEnabledAttr().Get() is not False:
        raise ValueError("object body must be dynamic")
    # Local authored hierarchy scales remain valid under Fabric. World position
    # from USD is deliberately never used for reach or joint-frame construction.
    cache = UsdGeom.XformCache()
    transform = cache.GetLocalToWorldTransform(body)
    from pxr import Gf
    basis = [transform.TransformDir(Gf.Vec3d(*(int(i == j) for j in range(3)))) for i in range(3)]
    if any(abs(float(basis[i] * basis[j]) - int(i == j)) > 1e-5
           for i in range(3) for j in range(3)):
        raise ValueError("scaled or sheared rigid body frames are unsupported")
    return body


def _sample(provider, path):
    sample = deepcopy(provider(path))
    if (not isinstance(sample, dict) or sample.get("source") != "physx"
            or sample.get("body_path") != path or sample.get("active") is not True):
        raise ValueError("live PhysX sample identity/source/active mismatch")
    step = sample.get("physics_step")
    if type(step) is not int or step < 0:
        raise ValueError("live sample requires a nonnegative physics_step")
    position = _vec(sample.get("position_m"), 3)
    rotation = _vec(sample.get("rotation_wxyz"), 4)
    if abs(sum(x*x for x in rotation) - 1) > 1e-5:
        raise ValueError("live rotation must be a unit quaternion")
    return {**sample, "position_m": position, "rotation_wxyz": rotation}


def _joint_evidence(stage, path):
    from pxr import UsdPhysics
    prim = stage.GetPrimAtPath(path)
    if not prim or not prim.IsA(UsdPhysics.FixedJoint):
        raise ValueError("attachment joint readback missing or wrong type")
    joint = UsdPhysics.FixedJoint(prim)
    def quat(attribute):
        value = attribute.Get()
        return [float(value.GetReal()), *map(float, value.GetImaginary())]
    return {"prim_path": path, "body0": list(map(str, joint.GetBody0Rel().GetTargets())),
            "body1": list(map(str, joint.GetBody1Rel().GetTargets())),
            "local_pos0": list(joint.GetLocalPos0Attr().Get()),
            "local_pos1": list(joint.GetLocalPos1Attr().Get()),
            "local_rot0_wxyz": quat(joint.GetLocalRot0Attr()),
            "local_rot1_wxyz": quat(joint.GetLocalRot1Attr()),
            "joint_enabled": joint.GetJointEnabledAttr().Get(),
            "collision_enabled": joint.GetCollisionEnabledAttr().Get(),
            "exclude_from_articulation": joint.GetExcludeFromArticulationAttr().Get(),
            "owner_token": prim.GetCustomDataByKey("goal_attachment_owner")}


def _evaluate(stage, request, active_member_ids, provider, *, reach_diagnostics=None):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    from .goal_driven_contract import validate_goal_task_spec
    public = request["public_task"]
    validate_goal_task_spec(public)
    if (UsdGeom.GetStageUpAxis(stage) != "Z"
            or not math.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1, abs_tol=1e-12)):
        raise ValueError("attachment stage must be meters/Z up")
    active = set(active_member_ids)
    robots = {r["id"]: r for r in public["candidate_robot_pool"]}
    if not active <= robots.keys():
        raise ValueError("active set contains unknown members")
    objects = {r["id"]: r for r in public["public_world"]["objects"]}
    if request["object_id"] not in objects:
        raise ValueError("unknown public object")
    obj = objects[request["object_id"]]["physical_properties"]
    modes = {r["id"]: r for r in obj["handling_modes"]}
    if request["mode_id"] not in modes:
        raise ValueError("unknown public handling mode")
    slots = modes[request["mode_id"]]["slots"]
    carriers = request["carriers"]
    if (not isinstance(carriers, dict) or len(carriers) != len(slots)
            or set(carriers.values()) != {slot["point_id"] for slot in slots}
            or len(set(carriers.values())) != len(carriers)):
        raise ValueError("carriers must cover mode slots exactly once")
    if not set(carriers) <= active:
        raise ValueError("carrier member is not currently active")
    binding = request["body_bindings"]
    object_binding = binding["objects"][request["object_id"]]
    body = _body(stage, object_binding, is_object=True)
    object_path = str(body.GetPath())
    poses = {object_path: _sample(provider, object_path)}
    object_pose = poses[object_path]
    mass = object_pose.get("mass_kg")
    if (type(mass) not in (int, float) or not math.isfinite(mass)
            or not math.isclose(mass, obj["mass_kg"], rel_tol=1e-5, abs_tol=1e-6)):
        raise ValueError("actual PhysX object mass differs from frozen mass")
    points = {p["id"]: p for p in obj["grasp_points"]}
    point_members = {point: member for member, point in carriers.items()}
    result, used = [], {object_path}
    skill, interface = (("pick", "manipulator") if len(slots) == 1
                        else ("cooperative_carry", "cooperative_transport"))
    for slot in slots:
        point = points[slot["point_id"]]
        member = point_members[point["id"]]
        robot = robots[member]
        profile = robot["physical_profile"]
        if (robot["mobility"] != "ground" or skill not in robot["public_capabilities"]
                or interface not in robot["interfaces"]
                or point["interface"] not in profile["grasp_interfaces"]):
            raise ValueError("member skill/interface does not satisfy public handling mode")
        load = obj["mass_kg"] * slot["load_fraction"]
        if load > profile["effective_payload_kg"]:
            raise ValueError("member allocated load exceeds public payload")
        if math.dist([0, 0, 0], point["position_m"]) > profile["reach_m"]:
            raise ValueError("public local fixed point exceeds declared reach")
        robot_body = _body(stage, binding["members"][member], is_object=False)
        path = str(robot_body.GetPath())
        if path in used:
            raise ValueError("member/object rigid body aliases another binding")
        used.add(path)
        pose = poses[path] = _sample(provider, path)
        # Object-local frame reads are independent of stale world pose under Fabric.
        fixed_path = object_path + "/GraspPoints/" + point["id"]
        fixed = stage.GetPrimAtPath(fixed_path)
        if not fixed or not fixed.IsActive():
            raise ValueError("actual fixed-point prim is missing")
        transform = UsdGeom.XformCache().ComputeRelativeTransform(fixed, body)[0]
        local = list(transform.Transform(Gf.Vec3d(0)))
        if math.dist(local, point["position_m"]) > 1e-5:
            raise ValueError("actual object-local fixed point differs from public frame")
        if fixed.GetCustomDataByKey("goal_interface") != point["interface"]:
            raise ValueError("fixed-point interface declaration mismatch")
        delta = _rotate(object_pose["rotation_wxyz"], point["position_m"])
        world = [object_pose["position_m"][i] + delta[i] for i in range(3)]
        distance = math.dist(pose["position_m"], world)
        if reach_diagnostics is not None:
            # Observe the exact check, including the rejecting member. Never
            # resample or continue checking later slots after a rejection.
            reach_diagnostics.append({
                "agent_id": member, "point_id": point["id"],
                "body_path": path, "object_body_path": object_path,
                "root_position_m": deepcopy(pose["position_m"]),
                "object_position_m": deepcopy(object_pose["position_m"]),
                "object_rotation_wxyz": deepcopy(object_pose["rotation_wxyz"]),
                "fixed_point_local_m": deepcopy(point["position_m"]),
                "fixed_point_world_m": world,
                "distance_3d_m": distance, "reach_m": profile["reach_m"],
                "within_reach": distance <= profile["reach_m"] + 1e-8,
                "physics_step": pose["physics_step"],
                "object_physics_step": object_pose["physics_step"],
            })
        # A planned rally may stand outside the declared connector length.
        # The joint then records that live separation. GOAL-01 still rejects it.
        if distance > profile["reach_m"] + 1e-8 and request.get("ideal_connector") is not True:
            raise ValueError("current PhysX root-to-fixed-point distance exceeds reach")
        local0 = _rotate(_inverse(pose["rotation_wxyz"]),
                         [world[i] - pose["position_m"][i] for i in range(3)])
        result.append({"member_id": member, "point_id": point["id"],
                       "body0": path, "body1": object_path, "fixed_point_prim_path": fixed_path,
                       "load_fraction": slot["load_fraction"], "allocated_load_kg": load,
                       "reach_m": profile["reach_m"], "measured_distance_m": distance,
                       "anchor_world_m": world, "local_pos0": local0,
                       "local_pos1": deepcopy(point["position_m"]),
                       "local_rot0_wxyz": _mul(_inverse(pose["rotation_wxyz"]),
                                               object_pose["rotation_wxyz"]),
                       "local_rot1_wxyz": [1, 0, 0, 0]})
    if len({pose["physics_step"] for pose in poses.values()}) != 1:
        raise ValueError("PhysX samples are not from the same physics step")
    # Refuse existing constraints on this object and existing adapter holds on
    # members. Internal robot articulation joints are not attachment holds.
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        if joint.GetJointEnabledAttr().Get() is False:
            continue
        targets = set(map(str, [*joint.GetBody0Rel().GetTargets(), *joint.GetBody1Rel().GetTargets()]))
        if object_path in targets or (prim.GetCustomDataByKey("goal_attachment_owner") and targets & used):
            raise ValueError("object or member already has an attachment constraint")
    return {"bindings": result, "live_samples": poses,
            "physics_step": object_pose["physics_step"]}


def prepare_attachment(stage, public_task, *, object_id, mode_id, carriers,
                       active_member_ids, body_bindings, pose_provider,
                       reach_diagnostics=None, ideal_connector=False):
    """Read-only preflight. carriers maps public member ID -> public point ID.

    body_bindings has objects/members maps, each public ID ->
    {root_path, body_path}. Object body_path must equal its root_path.
    pose_provider(body_path) must query a fresh PhysX sample and return:
    {source: 'physx', body_path, active: True, physics_step: int,
     position_m: [x,y,z], rotation_wxyz: [w,x,y,z], mass_kg: float}.
    Mass is required for the object; body/root poses must not come from USD.
    All samples in a call must belong to one paused physical step. Root-to-point
    reach is an ideal connector rule, not manipulator workspace/IK evidence.
    ``ideal_connector=True`` records a live separation outside that length and
    lets commit author the joint at the current pose. The object is not moved.
    Optional ``reach_diagnostics`` receives each reach measurement actually
    attempted, even on failure; later members may remain unchecked.
    """
    request = deepcopy({"public_task": public_task, "object_id": object_id,
                        "mode_id": mode_id, "carriers": carriers, "body_bindings": body_bindings})
    if ideal_connector:
        request["ideal_connector"] = True
    checks = _evaluate(stage, request, active_member_ids, pose_provider,
                       reach_diagnostics=reach_diagnostics)
    return _seal({"schema": "eai.goal_attachment_prepared", "version": 1,
                  "status": "prepared", "token": uuid.uuid4().hex,
                  "stage_identifier": stage.GetRootLayer().identifier,
                  "edit_layer_identifier": stage.GetEditTarget().GetLayer().identifier,
                  "request": request, "preflight": checks, "stage_writes": 0})


def cancel_attachment(prepared):
    """Cancel a not-yet-committed record in place; no stage access or effects."""
    _check_seal(prepared)
    if prepared["status"] not in {"prepared", "cancelled"}:
        raise ValueError("only an uncommitted preparation can be cancelled")
    payload = {k: v for k, v in prepared.items() if k != "sha256"}
    payload["status"] = "cancelled"
    prepared.clear()
    prepared.update(_seal(payload))
    return {"status": "cancelled", "token": prepared["token"], "stage_writes": 0}


def _author_joint(stage, path, binding, token):
    from pxr import Gf, Sdf, UsdPhysics
    joint = UsdPhysics.FixedJoint.Define(stage, path)
    joint.GetPrim().SetCustomDataByKey("goal_attachment_owner", token)
    joint.CreateBody0Rel().SetTargets([Sdf.Path(binding["body0"])])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(binding["body1"])])
    joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*binding["local_pos0"]))
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(*binding["local_pos1"]))
    for field, attr in (("local_rot0_wxyz", joint.CreateLocalRot0Attr()),
                        ("local_rot1_wxyz", joint.CreateLocalRot1Attr())):
        value = binding[field]
        attr.Set(Gf.Quatf(value[0], Gf.Vec3f(*value[1:])))
    joint.CreateJointEnabledAttr().Set(True)
    joint.CreateCollisionEnabledAttr().Set(True)
    # Keep the object independent of the robot's reduced-coordinate articulation.
    # This remains a solver constraint, including when two members hold one object.
    joint.CreateExcludeFromArticulationAttr().Set(True)


def _remove_prim(stage, path):
    """Allow tests to inject deterministic USD removal failures."""
    return stage.RemovePrim(path)


def commit_attachment(stage, prepared, *, active_member_ids, pose_provider,
                      reach_diagnostics=None):
    """Recheck fresh poses and active state, then author only owned joint prims.

    Frames coincide at commit poses; bodies are never teleported. Fixed joints
    constrain the current relative transform. The host owns solver scheduling.
    """
    _check_seal(prepared)
    if prepared["status"] != "prepared":
        raise ValueError("attachment is not an uncommitted preparation")
    layer = stage.GetEditTarget().GetLayer()
    if (stage.GetRootLayer().identifier != prepared["stage_identifier"]
            or layer.identifier != prepared["edit_layer_identifier"] or not layer.permissionToEdit):
        raise ValueError("attachment stage/edit target changed or is not editable")
    checks = _evaluate(stage, prepared["request"], active_member_ids, pose_provider,
                       reach_diagnostics=reach_diagnostics)
    if checks["physics_step"] < prepared["preflight"]["physics_step"]:
        raise ValueError("PhysX samples regressed to an older step")
    token = prepared["token"]
    paths = [binding["body1"] + f"/GoalAttachment_{token}_{i}"
             for i, binding in enumerate(checks["bindings"])]
    if any(stage.GetPrimAtPath(path) for path in paths):
        raise ValueError("attachment joint path already exists")
    authored = []
    try:
        for path, binding in zip(paths, checks["bindings"]):
            authored.append(path)
            _author_joint(stage, path, binding, token)
        readback = [_joint_evidence(stage, path) for path in paths]
        for binding, read in zip(checks["bindings"], readback):
            if (read["body0"] != [binding["body0"]] or read["body1"] != [binding["body1"]]
                    or read["owner_token"] != token or read["joint_enabled"] is not True
                    or read["collision_enabled"] is not True
                    or read["exclude_from_articulation"] is not True
                    or any(math.dist(binding[key], read[key]) > 1e-5 for key in
                           ("local_pos0", "local_pos1", "local_rot0_wxyz", "local_rot1_wxyz"))):
                raise ValueError("authored joint readback mismatch")
    except Exception:
        for path in reversed(authored):
            stage.RemovePrim(path)
        raise
    receipt = _seal({"schema": "eai.goal_attachment_receipt", "version": 1,
                     "status": "committed", "token": token,
                     "object_id": prepared["request"]["object_id"],
                     "stage_identifier": stage.GetRootLayer().identifier,
                     "edit_layer_identifier": layer.identifier,
                     "public_task_sha256": _digest(prepared["request"]["public_task"]),
                     "checks": checks, "joint_readback": readback,
                     "solver_response_validated": False, "joint_load_sharing_validated": False,
                     "coordinated_motion_validated": False, "scoreable": False})
    updated = {k: v for k, v in prepared.items() if k != "sha256"}
    updated["status"] = "committed"
    prepared.clear()
    prepared.update(_seal(updated))
    return receipt


def detach_attachment(stage, receipt):
    """Atomically remove unchanged, receipt-owned joints from the edit layer."""
    _check_seal(receipt)
    layer = stage.GetEditTarget().GetLayer()
    expected_rows = receipt.get("joint_readback")
    if (receipt.get("schema") != "eai.goal_attachment_receipt"
            or receipt.get("status") != "committed"
            or not isinstance(expected_rows, list) or not expected_rows
            or len({row.get("prim_path") for row in expected_rows}) != len(expected_rows)
            or stage.GetRootLayer().identifier != receipt["stage_identifier"]
            or layer.identifier != receipt["edit_layer_identifier"] or not layer.permissionToEdit):
        raise ValueError("detach receipt stage/edit target mismatch")
    present = []
    for expected in expected_rows:
        path = expected["prim_path"]
        if not stage.GetPrimAtPath(path):
            continue
        actual = _joint_evidence(stage, path)
        if actual != expected or actual["owner_token"] != receipt["token"]:
            raise ValueError("refusing to detach a changed/unowned joint")
        if any(spec.layer != layer for spec in stage.GetPrimAtPath(path).GetPrimStack()):
            raise ValueError("detach would expose another layer's joint")
        present.append(actual)
    if present and len(present) != len(expected_rows):
        raise ValueError("refusing to detach a partially missing joint set")
    if present:
        layer_before = layer.ExportToString()
        try:
            for actual in present:
                if not _remove_prim(stage, actual["prim_path"]):
                    raise RuntimeError("failed to remove owned joint")
        except BaseException as exc:
            try:
                restored = layer.ImportFromString(layer_before)
                if restored is False or layer.ExportToString() != layer_before:
                    raise RuntimeError("edit layer differs after detach rollback")
            except BaseException as rollback_exc:
                raise RuntimeError("failed to roll back partial joint detach") from rollback_exc
            raise
    latest_step = receipt["checks"]["physics_step"]
    lift = receipt.get("lift_actuation")
    if isinstance(lift, dict) and type(lift.get("physics_step")) is int:
        latest_step = max(latest_step, lift["physics_step"])
    return _seal({"schema": "eai.goal_detachment_receipt", "version": 1,
                  "status": "detached", "token": receipt["token"],
                  "object_id": receipt["object_id"],
                  "stage_identifier": receipt["stage_identifier"],
                  "edit_layer_identifier": receipt["edit_layer_identifier"],
                  "attachment_receipt_sha256": receipt["sha256"],
                  "attachment_physics_step": latest_step,
                  "removed_joint_paths": [row["prim_path"] for row in present],
                  "before_removal_readback": present, "already_detached": not present,
                  "solver_response_validated": False})


__all__ = ["prepare_attachment", "commit_attachment", "cancel_attachment", "detach_attachment"]
