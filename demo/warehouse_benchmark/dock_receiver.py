"""Private native dock receiver; trusted host readbacks, never agent claims.

This checks a released object's full declared envelope inside the candidate
dock. It does not certify the dock's physical service installation or lift/load
sharing. The host owns the stage, physics step and independent pose provider.
"""

from copy import deepcopy
import math

from demo.benchmark.goal_native_attachment import _check_seal, _rotate, _sample, _seal


def dock_geometry(deployment):
    rows = deployment.get("region_bindings", deployment.get("scene_overlay", {}).get("region_bindings", []))
    if isinstance(rows, dict):
        rows = rows.values()
    row = next((row for row in rows if row.get("id") == "outbound_dock"), None)
    geometry = row.get("candidate_geometry", {}) if row else {}
    bounds = geometry.get("aabb_xy_m")
    if (not isinstance(bounds, list) or len(bounds) != 4
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds)
            or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
        raise ValueError("deployment has no valid outbound_dock candidate AABB")
    return list(bounds)


def envelope_bounds(pose, size):
    if len(size) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in size):
        raise ValueError("invalid declared object size")
    corners = []
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                delta = _rotate(pose["rotation_wxyz"], [sx*size[0]/2, sy*size[1]/2, sz*size[2]/2])
                corners.append([a+b for a, b in zip(pose["position_m"], delta)])
    return [min(p[i] for p in corners) for i in range(3)] + [max(p[i] for p in corners) for i in range(3)]


def enabled_object_joints(stage, body_path):
    from pxr import UsdPhysics
    paths = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        targets = [*joint.GetBody0Rel().GetTargets(), *joint.GetBody1Rel().GetTargets()]
        if joint.GetJointEnabledAttr().Get() is not False and body_path in map(str, targets):
            paths.append(str(prim.GetPath()))
    return paths


class EpisodeDockReceiver:
    def __init__(self, *, deployment, live, object_id):
        self.bounds = dock_geometry(deployment)
        self.live = live
        self.object_id = object_id
        self.physical = deepcopy(next(row["physical_properties"] for row in deployment["object_instances"]
                                      if row["public_id"] == object_id))
        self.state = "open"

    def receive(self, *, episode_id, step, release_step, stage, attachment_receipt, detach_receipt):
        if self.state != "open":
            raise ValueError("dock receiver already issued its receipt")
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError("dock receipt requires episode identity")
        if type(step) is not int or type(release_step) is not int or release_step < 0 or step <= release_step:
            raise ValueError("dock receipt requires a post-release physics step")
        binding = self.live.bindings["objects"][self.object_id]
        body = binding["body_path"]
        pose = _sample(self.live, body)
        if pose["physics_step"] != step or self.live.step != step:
            raise ValueError("dock object readback is not from the current physics step")
        native_mass = pose.get("mass_kg")
        if type(native_mass) not in (int, float) or not math.isclose(native_mass, self.physical["mass_kg"], rel_tol=1e-5):
            raise ValueError("dock object mass differs from frozen object identity")
        _check_seal(attachment_receipt)
        _check_seal(detach_receipt)
        stage_id = stage.GetRootLayer().identifier
        edit_layer_id = stage.GetEditTarget().GetLayer().identifier
        joints = attachment_receipt.get("joint_readback", [])
        expected = {row["prim_path"] for row in joints}
        if (attachment_receipt.get("schema") != "eai.goal_attachment_receipt"
                or attachment_receipt.get("status") != "committed"
                or attachment_receipt.get("object_id") != self.object_id
                or attachment_receipt.get("stage_identifier") != stage_id
                or attachment_receipt.get("edit_layer_identifier") != edit_layer_id
                or not expected or len(expected) != len(joints)
                or any(row.get("body1") != [body]
                       or row.get("owner_token") != attachment_receipt.get("token")
                       or row.get("joint_enabled") is not True for row in joints)
                or not attachment_receipt.get("token")
                or detach_receipt.get("schema") != "eai.goal_detachment_receipt"
                or detach_receipt.get("status") != "detached"
                or detach_receipt.get("object_id") != self.object_id
                or detach_receipt.get("stage_identifier") != stage_id
                or detach_receipt.get("edit_layer_identifier") != edit_layer_id
                or detach_receipt.get("attachment_receipt_sha256") != attachment_receipt["sha256"]
                or detach_receipt.get("token") != attachment_receipt["token"]
                or detach_receipt.get("already_detached") is not False
                or detach_receipt.get("before_removal_readback") != joints
                or set(detach_receipt.get("removed_joint_paths", [])) != expected
                or type(detach_receipt.get("attachment_physics_step")) is not int
                or release_step < detach_receipt["attachment_physics_step"]):
            raise ValueError("dock release is not bound to the object's attachment")
        if any(stage.GetPrimAtPath(path) for path in expected) or enabled_object_joints(stage, body):
            raise ValueError("dock object still has an attachment joint")
        box = envelope_bounds(pose, self.physical["size_m"])
        low_x, low_y, high_x, high_y = self.bounds
        if box[0] < low_x or box[1] < low_y or box[3] > high_x or box[4] > high_y:
            raise ValueError("released object footprint is outside dock AABB")
        # Warehouse candidate dock uses ground z=0. Do not accept a floating
        # or buried object merely because its XY center is inside the region.
        if not -.02 <= box[2] <= .04:
            raise ValueError("released object is not at the candidate dock support height")
        self.state = "accepted"
        return _seal({
            "schema": "eai.benchmark.warehouse_episode_dock_receipt_v1",
            "episode_id": episode_id, "receipt_id": f"dock-receipt-{episode_id}",
            "state": "ready", "object_id": self.object_id, "object_runtime_path": body,
            "object_pose_m": list(pose["position_m"]), "object_aabb_xyz_m": box,
            "target_ref": "outbound_dock", "target_aabb_xy_m": list(self.bounds),
            "physics_step": step, "release_step": release_step,
            "removed_joint_paths": sorted(expected), "enabled_object_joints": [],
            "stage_identifier": stage_id, "edit_layer_identifier": edit_layer_id,
            "attachment_receipt_sha256": attachment_receipt["sha256"],
            "detach_receipt_sha256": detach_receipt["sha256"],
            "source": "warehouse_episode_dock_receiver", "scoreable": False,
            "scope": "candidate_dock_post_release_envelope_microdiagnostic",
        })
