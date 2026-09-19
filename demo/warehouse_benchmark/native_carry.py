"""Bounded ideal connector actuation; dynamic bodies remain PhysX-owned.

Changing a robot-side constraint target models the declared reliable fixed
grasp. It is not an arm/IK implementation. No body pose, mass, velocity, or
collision property is written, and a target update is never execution proof.
"""

from copy import deepcopy
import math

from demo.benchmark import goal_native_attachment as attachment


def shift_lift_target(stage, receipt, *, delta_z_m, pose_provider, active_member_ids):
    """Advance every carrier's commanded lift height by at most 2 mm.

    Call once per real physics control step. The returned sealed receipt
    replaces the old one for subsequent updates and detach. Reach, ownership,
    same-step samples and active membership are checked before any USD write.
    """
    from pxr import Gf, UsdPhysics

    attachment._check_seal(receipt)
    if (type(delta_z_m) not in (float, int) or not math.isfinite(delta_z_m)
            or not 0 < abs(delta_z_m) <= .002):
        raise ValueError("connector lift increment must be nonzero and at most 2 mm")
    if (receipt.get("schema") != "eai.goal_attachment_receipt"
            or receipt.get("status") != "committed"
            or stage.GetRootLayer().identifier != receipt["stage_identifier"]
            or stage.GetEditTarget().GetLayer().identifier != receipt["edit_layer_identifier"]
            or not stage.GetEditTarget().GetLayer().permissionToEdit):
        raise ValueError("lift receipt stage/edit target mismatch")
    previous = receipt.get("lift_actuation", {})
    height = previous.get("target_offset_m", 0.) + delta_z_m
    if not -.000001 <= height <= .25:
        raise ValueError("connector lift exceeds bounded vertical stroke")
    rows = receipt["joint_readback"]
    baselines = previous.get("baseline_local_pos0") or {
        row["prim_path"]: deepcopy(row["local_pos0"]) for row in rows}
    if set(baselines) != {row["prim_path"] for row in rows}:
        raise ValueError("lift baseline does not cover committed joints")
    height = max(0., height)
    bindings = receipt["checks"]["bindings"]
    if not rows or len(rows) != len(bindings):
        raise ValueError("lift requires all committed carrier constraints")
    active = set(active_member_ids)
    targets, steps = [], set()
    for row, binding in zip(rows, bindings):
        if binding["member_id"] not in active:
            raise ValueError("lift carrier is no longer active; safe hold required")
        if attachment._joint_evidence(stage, row["prim_path"]) != row:
            raise ValueError("refusing to actuate changed/unowned joint")
        robot = attachment._sample(pose_provider, row["body0"][0])
        obj = attachment._sample(pose_provider, row["body1"][0])
        steps.update((robot["physics_step"], obj["physics_step"]))
        # Recompute from immutable committed anchors. Integrating local deltas
        # at changing attitudes leaves a residual after a lift/lower cycle.
        delta = attachment._rotate(attachment._inverse(robot["rotation_wxyz"]), [0., 0., height])
        target = [a+b for a, b in zip(baselines[row["prim_path"]], delta)]
        if math.dist([0., 0., 0.], target) > binding["reach_m"]:
            raise ValueError("lift target exceeds public connector reach")
        targets.append(target)
    if len(steps) != 1 or next(iter(steps)) <= previous.get("physics_step", -1):
        raise ValueError("lift requires a fresh common physics step")
    if next(iter(steps)) < receipt["checks"]["physics_step"]:
        raise ValueError("lift sample predates attachment")
    changed = []
    try:
        for row, target in zip(rows, targets):
            joint = UsdPhysics.FixedJoint.Get(stage, row["prim_path"])
            changed.append((joint, row["local_pos0"]))
            if not joint.GetLocalPos0Attr().Set(Gf.Vec3f(*target)):
                raise RuntimeError("connector target write failed")
        updated = deepcopy(receipt)
        updated.pop("sha256")
        updated["joint_readback"] = [attachment._joint_evidence(stage, row["prim_path"]) for row in rows]
        updated["lift_actuation"] = {"physics_step": next(iter(steps)), "target_offset_m": height,
                                      "baseline_local_pos0": deepcopy(baselines),
                                      "mechanism": "robot_side_constraint_target", "body_pose_writes": 0}
        return attachment._seal(updated)
    except Exception:
        for joint, old in changed:
            joint.GetLocalPos0Attr().Set(Gf.Vec3f(*old))
        raise
