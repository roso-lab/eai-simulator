"""FixedJoint authoring tests with explicit injected PhysX samples, no solver."""

from copy import deepcopy
import math

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from demo.benchmark import goal_native_attachment as attachment
from demo.benchmark.goal_driven_tasks import build_goal_task_spec


@pytest.fixture
def setup():
    public = build_goal_task_spec("warehouse_nvd")
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    samples, bindings = {}, {"objects": {}, "members": {}}

    def rigid(path, position, mass):
        root = UsdGeom.Xform.Define(stage, path)
        # Stale USD Fabric pose is intentionally far from actual injected pose.
        root.AddTranslateOp().Set(Gf.Vec3d(100, 200, 300))
        api = UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
        api.CreateRigidBodyEnabledAttr().Set(True)
        api.CreateKinematicEnabledAttr().Set(False)
        UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr().Set(mass)
        UsdPhysics.CollisionAPI.Apply(root.GetPrim()).CreateCollisionEnabledAttr().Set(True)
        samples[path] = {"source": "physx", "body_path": path, "active": True,
                         "physics_step": 10, "position_m": position,
                         "rotation_wxyz": [1, 0, 0, 0], "mass_kg": mass}

    for obj in public["public_world"]["objects"]:
        path = "/World/Objects/" + obj["id"]
        rigid(path, [0, 0, 0.3], obj["physical_properties"]["mass_kg"])
        bindings["objects"][obj["id"]] = {"root_path": path, "body_path": path}
        for point in obj["physical_properties"]["grasp_points"]:
            fixed = UsdGeom.Xform.Define(stage, path + "/GraspPoints/" + point["id"])
            fixed.AddTranslateOp().Set(Gf.Vec3d(*point["position_m"]))
            fixed.GetPrim().SetCustomDataByKey("goal_interface", point["interface"])
    for i, robot in enumerate(public["candidate_robot_pool"]):
        root = "/World/Robots/" + robot["id"]
        UsdGeom.Xform.Define(stage, root)
        path = root + "/base"
        rigid(path, [0, -0.3 if i % 2 else 0.3, 0.55], 10)
        bindings["members"][robot["id"]] = {"root_path": root, "body_path": path}
    return {"public": public, "stage": stage, "bindings": bindings, "samples": samples,
            "active": {robot["id"] for robot in public["candidate_robot_pool"]}}


def prepare(data, *, paired=False, carriers=None):
    return attachment.prepare_attachment(
        data["stage"], data["public"],
        object_id="order_crate_01" if paired else "order_carton_01",
        mode_id="paired_support" if paired else "single_grip",
        carriers=carriers or ({"warehouse_ground_1": "support_left", "warehouse_ground_2": "support_right"}
                              if paired else {"warehouse_ground_1": "center_grip"}),
        active_member_ids=data["active"], body_bindings=data["bindings"],
        pose_provider=lambda path: deepcopy(data["samples"][path]),
    )


def commit(data, prepared):
    return attachment.commit_attachment(data["stage"], prepared,
        active_member_ids=data["active"], pose_provider=lambda path: deepcopy(data["samples"][path]))


def snapshot(data):
    return data["stage"].GetRootLayer().ExportToString()


@pytest.mark.parametrize("paired", [False, True])
def test_prepare_cancel_is_zero_stage_effect_and_commit_cancelled_rejected(setup, paired):
    before = snapshot(setup)
    prepared = prepare(setup, paired=paired)
    assert snapshot(setup) == before
    assert attachment.cancel_attachment(prepared)["stage_writes"] == 0
    assert attachment.cancel_attachment(prepared)["status"] == "cancelled"
    with pytest.raises(ValueError, match="uncommitted"):
        commit(setup, prepared)
    assert snapshot(setup) == before


@pytest.mark.parametrize("paired", [False, True])
def test_joint_frames_coincide_at_live_pose_no_body_mutation_and_detach_idempotent(setup, paired):
    before = snapshot(setup)
    receipt = commit(setup, prepare(setup, paired=paired))
    assert len(receipt["joint_readback"]) == (2 if paired else 1)
    assert not receipt["solver_response_validated"]
    assert not receipt["coordinated_motion_validated"]
    assert not receipt["joint_load_sharing_validated"]
    for joint in receipt["joint_readback"]:
        pose0 = setup["samples"][joint["body0"][0]]
        pose1 = setup["samples"][joint["body1"][0]]
        p0 = [a + b for a, b in zip(pose0["position_m"], attachment._rotate(
            pose0["rotation_wxyz"], joint["local_pos0"]))]
        p1 = [a + b for a, b in zip(pose1["position_m"], attachment._rotate(
            pose1["rotation_wxyz"], joint["local_pos1"]))]
        assert p0 == pytest.approx(p1, abs=1e-6)
        assert joint["collision_enabled"] is True
        assert joint["joint_enabled"] is True
        assert joint["exclude_from_articulation"] is True
        native_joint = UsdPhysics.FixedJoint.Get(setup["stage"], joint["prim_path"])
        assert native_joint.GetExcludeFromArticulationAttr().HasAuthoredValueOpinion()
    removed = attachment.detach_attachment(setup["stage"], receipt)
    attachment._check_seal(removed)
    assert removed["attachment_receipt_sha256"] == receipt["sha256"]
    assert removed["object_id"] == receipt["object_id"]
    assert len(removed["removed_joint_paths"]) == len(receipt["joint_readback"])
    repeated = attachment.detach_attachment(setup["stage"], receipt)
    attachment._check_seal(repeated)
    assert repeated["already_detached"] is True
    # Exact stage roundtrip proves no mass, transform, collision, or other writes.
    assert snapshot(setup) == before


def test_commit_requeries_changed_poses_and_orientations(setup):
    prepared = prepare(setup)
    robot_path = setup["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    object_path = setup["bindings"]["objects"]["order_carton_01"]["body_path"]
    q = [math.sqrt(0.5), 0, 0, math.sqrt(0.5)]
    setup["samples"][robot_path].update(position_m=[0.3, 0, 0.55], rotation_wxyz=q)
    setup["samples"][object_path].update(position_m=[0.1, 0.1, 0.3], rotation_wxyz=[0, 1, 0, 0])
    for row in setup["samples"].values():
        row["physics_step"] = 11
    receipt = commit(setup, prepared)
    joint = receipt["joint_readback"][0]
    expected = receipt["checks"]["bindings"][0]
    assert receipt["checks"]["physics_step"] == 11
    assert joint["local_pos0"] == pytest.approx(expected["local_pos0"])
    world_rot0 = attachment._mul(q, joint["local_rot0_wxyz"])
    assert world_rot0 == pytest.approx([0, 1, 0, 0], abs=1e-6)
    assert expected["anchor_world_m"] == pytest.approx([0.1, 0.1, 0.15])


def test_unauthored_rigid_enabled_and_kinematic_use_schema_defaults(setup):
    for group in setup["bindings"].values():
        for binding in group.values():
            prim = setup["stage"].GetPrimAtPath(binding["body_path"])
            prim.RemoveProperty("physics:rigidBodyEnabled")
            prim.RemoveProperty("physics:kinematicEnabled")
            rigid = UsdPhysics.RigidBodyAPI(prim)
            assert not rigid.GetRigidBodyEnabledAttr().HasAuthoredValueOpinion()
            assert rigid.GetRigidBodyEnabledAttr().Get() is True
            assert rigid.GetKinematicEnabledAttr().Get() is False
    before = snapshot(setup)
    receipt = commit(setup, prepare(setup))
    attachment.detach_attachment(setup["stage"], receipt)
    assert snapshot(setup) == before


def test_blocked_rigid_enabled_is_not_assumed_true(setup):
    path = setup["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    UsdPhysics.RigidBodyAPI(setup["stage"].GetPrimAtPath(path)).GetRigidBodyEnabledAttr().Block()
    before = snapshot(setup)
    with pytest.raises(ValueError, match="enabled RigidBody"):
        prepare(setup)
    assert snapshot(setup) == before


@pytest.mark.parametrize("change, error", [
    ("inactive", "currently active"), ("distance", "distance exceeds reach"),
    ("disabled_body", "enabled RigidBody"), ("object_mass", "PhysX object mass"),
    ("stale_step", "older step"), ("mixed_steps", "same physics step"),
    ("source", "identity/source/active"), ("identity", "identity/source/active"),
    ("missing_point", "fixed-point prim"), ("nonrigid", "enabled RigidBody"),
    ("point_transform", "differs from public frame"),
])
def test_failed_commit_preconditions_leave_no_joint_or_body_effects(setup, change, error):
    prepared = prepare(setup)
    robot = setup["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    obj = setup["bindings"]["objects"]["order_carton_01"]["body_path"]
    if change == "inactive":
        setup["active"].remove("warehouse_ground_1")
    elif change == "distance":
        setup["samples"][robot]["position_m"] = [10, 0, 0]
    elif change == "disabled_body":
        UsdPhysics.RigidBodyAPI(setup["stage"].GetPrimAtPath(robot)).GetRigidBodyEnabledAttr().Set(False)
    elif change == "nonrigid":
        setup["stage"].GetPrimAtPath(robot).RemoveAPI(UsdPhysics.RigidBodyAPI)
    elif change == "object_mass":
        setup["samples"][obj]["mass_kg"] = 60
    elif change == "stale_step":
        for row in setup["samples"].values():
            row["physics_step"] = 1
    elif change == "mixed_steps":
        setup["samples"][robot]["physics_step"] = 11
    elif change == "source":
        setup["samples"][robot]["source"] = "usd"
    elif change == "identity":
        setup["samples"][robot]["body_path"] = "/Other"
    elif change == "missing_point":
        setup["stage"].RemovePrim(obj + "/GraspPoints/center_grip")
    elif change == "point_transform":
        setup["stage"].GetPrimAtPath(obj + "/GraspPoints/center_grip").GetAttribute(
            "xformOp:translate").Set(Gf.Vec3d(2, 0, 0))
    before = snapshot(setup)
    with pytest.raises(ValueError, match=error):
        commit(setup, prepared)
    assert snapshot(setup) == before
    assert prepared["status"] == "prepared"


def test_payload_interface_and_pair_slot_failures_are_read_only(setup):
    before = snapshot(setup)
    with pytest.raises(ValueError, match="skill/interface"):
        prepare(setup, carriers={"warehouse_ground_2": "center_grip"})
    with pytest.raises(ValueError, match="exactly once"):
        prepare(setup, paired=True, carriers={"warehouse_ground_1": "support_left"})
    with pytest.raises(ValueError, match="payload"):
        attachment.prepare_attachment(setup["stage"], setup["public"],
            object_id="order_crate_01", mode_id="single_grip",
            carriers={"warehouse_ground_1": "center_grip"}, active_member_ids=setup["active"],
            body_bindings=setup["bindings"], pose_provider=lambda p: setup["samples"][p])
    assert snapshot(setup) == before


def test_member_body_aliasing_is_rejected(setup):
    setup["bindings"]["members"]["warehouse_ground_2"] = deepcopy(
        setup["bindings"]["members"]["warehouse_ground_1"])
    with pytest.raises(ValueError, match="aliases"):
        prepare(setup, paired=True)


def test_partial_pair_authoring_failure_rolls_back_all_joint_prims(setup, monkeypatch):
    prepared = prepare(setup, paired=True)
    before = snapshot(setup)
    original = attachment._author_joint
    def fail_second(stage, path, binding, token):
        original(stage, path, binding, token)
        if path.endswith("_1"):
            raise RuntimeError("injected authoring failure")
    monkeypatch.setattr(attachment, "_author_joint", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        commit(setup, prepared)
    assert snapshot(setup) == before
    assert prepared["status"] == "prepared"


def test_partial_pair_detach_failure_rolls_back_all_joint_prims(setup, monkeypatch):
    receipt = commit(setup, prepare(setup, paired=True))
    before = snapshot(setup)
    original = attachment._remove_prim
    calls = []

    def fail_second(stage, path):
        calls.append(path)
        return original(stage, path) if len(calls) == 1 else False

    monkeypatch.setattr(attachment, "_remove_prim", fail_second)
    with pytest.raises(RuntimeError, match="failed to remove"):
        attachment.detach_attachment(setup["stage"], receipt)
    assert snapshot(setup) == before
    assert [attachment._joint_evidence(setup["stage"], row["prim_path"])
            for row in receipt["joint_readback"]] == receipt["joint_readback"]


def test_partially_missing_pair_refuses_to_remove_remaining_joint(setup):
    receipt = commit(setup, prepare(setup, paired=True))
    missing, remaining = (row["prim_path"] for row in receipt["joint_readback"])
    assert setup["stage"].RemovePrim(missing)
    before = snapshot(setup)
    with pytest.raises(ValueError, match="partially missing"):
        attachment.detach_attachment(setup["stage"], receipt)
    assert snapshot(setup) == before
    assert setup["stage"].GetPrimAtPath(remaining)


@pytest.mark.parametrize("change", ["status", "empty", "duplicate"])
def test_detach_rejects_sealed_forged_attachment_inventory(setup, change):
    receipt = commit(setup, prepare(setup, paired=True))
    forged = {key: deepcopy(value) for key, value in receipt.items() if key != "sha256"}
    if change == "status":
        forged["status"] = "prepared"
    elif change == "empty":
        forged["joint_readback"] = []
    else:
        forged["joint_readback"] = [forged["joint_readback"][0]] * 2
    forged = attachment._seal(forged)
    before = snapshot(setup)
    with pytest.raises(ValueError, match="stage/edit target"):
        attachment.detach_attachment(setup["stage"], forged)
    assert snapshot(setup) == before


def test_ideal_connector_commits_a_rally_separation_without_moving_the_object(setup):
    left = setup["bindings"]["members"]["warehouse_ground_1"]["body_path"]
    right = setup["bindings"]["members"]["warehouse_ground_2"]["body_path"]
    crate = setup["bindings"]["objects"]["order_crate_01"]["body_path"]
    setup["samples"][left]["position_m"] = [0.0, -1.65, 0.55]
    setup["samples"][right]["position_m"] = [0.0, 1.65, 0.55]
    before = snapshot(setup)
    with pytest.raises(ValueError, match="distance exceeds reach"):
        prepare(setup, paired=True)
    assert snapshot(setup) == before
    prepared = attachment.prepare_attachment(
        setup["stage"], setup["public"], object_id="order_crate_01",
        mode_id="paired_support",
        carriers={"warehouse_ground_1": "support_left", "warehouse_ground_2": "support_right"},
        active_member_ids=setup["active"], body_bindings=setup["bindings"],
        pose_provider=lambda path: deepcopy(setup["samples"][path]),
        ideal_connector=True)
    assert prepared["request"]["ideal_connector"] is True
    assert all(row["measured_distance_m"] > 1.2 for row in prepared["preflight"]["bindings"])
    assert snapshot(setup) == before
    receipt = commit(setup, prepared)
    assert len(receipt["joint_readback"]) == 2
    assert all(math.dist(row["local_pos0"], [0.0, 0.0, 0.0]) > 1.2
               for row in receipt["joint_readback"])
    translate = setup["stage"].GetPrimAtPath(crate).GetAttribute("xformOp:translate").Get()
    assert [translate[0], translate[1], translate[2]] == [100, 200, 300]
    assert setup["samples"][crate]["position_m"] == [0, 0, 0.3]


def test_existing_constraint_blocks_second_attach_and_changed_joint_refuses_detach(setup):
    receipt = commit(setup, prepare(setup))
    with pytest.raises(ValueError, match="already has"):
        prepare(setup)
    joint = UsdPhysics.FixedJoint.Get(setup["stage"], receipt["joint_readback"][0]["prim_path"])
    joint.GetJointEnabledAttr().Set(False)
    before = snapshot(setup)
    with pytest.raises(ValueError, match="changed/unowned"):
        attachment.detach_attachment(setup["stage"], receipt)
    assert snapshot(setup) == before
