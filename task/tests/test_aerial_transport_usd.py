"""Real OpenUSD authoring tests. Run with env_isaaclab; not PhysX stepping."""
import math
import pytest
pxr = pytest.importorskip("pxr")
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
from algorithm.skills import SkillContractError
from algorithm.skills.aerial_transport import RigidPose
from task.executors.aerial_pick_place import PhysxAerialTransportBridge


def setup():
    stage = Usd.Stage.CreateInMemory()
    robot = UsdGeom.Xform.Define(stage, "/World/Drone").GetPrim()
    obj = UsdGeom.Xform.Define(stage, "/World/Box").GetPrim()
    for p in (robot, obj):
        UsdPhysics.RigidBodyAPI.Apply(p)
    for name in ("implicit", "true", "false", "animated"):
        p = UsdGeom.Cube.Define(stage, "/World/Box/"+name).GetPrim()
        api = UsdPhysics.CollisionAPI.Apply(p)
        if name in {"true", "false"}:
            api.CreateCollisionEnabledAttr().Set(name == "true")
        elif name == "animated":
            api.CreateCollisionEnabledAttr().Set(True, 0.)
            api.GetCollisionEnabledAttr().Set(False, 1.)
    UsdGeom.Xform.Define(stage, "/World/Box/untouched")
    mass = UsdPhysics.MassAPI.Apply(obj)
    mass.CreateMassAttr().Set(.123)
    mass.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(.1, .2, .3))
    for name, typ, value in (("physxRigidBody:disableGravity", Sdf.ValueTypeNames.Bool, False),
                             ("physxRigidBody:linearDamping", Sdf.ValueTypeNames.Float, .4),
                             ("physxRigidBody:angularDamping", Sdf.ValueTypeNames.Float, .5)):
        obj.CreateAttribute(name, typ).Set(value)
    angle = math.pi/4
    poses = {"/World/Box": RigidPose((1, 2, .5), (math.cos(angle), 0, 0, math.sin(angle))),
             "/World/Drone": RigidPose((1, 2, .75), (math.cos(angle/2), math.sin(angle/2), 0, 0))}
    bridge = PhysxAerialTransportBridge(stage, pose_reader=lambda p: poses[p])
    bridge.bind("t", "/World/Drone", "/World/Box", (1, 2, .75))
    return stage, poses, bridge


def test_joint_frames_rotate_to_same_world_anchor_and_preserve_mass_properties():
    stage, poses, bridge = setup()
    before = stage.GetRootLayer().ExportToString()
    bridge.apply("t", "create_support", "1")
    support = UsdPhysics.FixedJoint.Get(stage, bridge.evidence("t")["support_joint_path"])
    assert support.GetBody0Rel().GetTargets() == []
    assert list(support.GetLocalPos0Attr().Get()) == pytest.approx((1, 2, .75))
    bridge.apply("t", "disable_collision", "2")
    bridge.apply("t", "attach", "3")
    path = bridge.evidence("t")["joint_path"]
    joint = UsdPhysics.FixedJoint.Get(stage, path)
    assert joint.GetJointEnabledAttr().Get() and joint.GetExcludeFromArticulationAttr().Get()
    for pose, local_pos, local_rot in ((poses["/World/Drone"], joint.GetLocalPos0Attr(), joint.GetLocalRot0Attr()),
                                      (poses["/World/Box"], joint.GetLocalPos1Attr(), joint.GetLocalRot1Attr())):
        assert pose.to_world(tuple(local_pos.Get())) == pytest.approx((1, 2, .75))
        q = local_rot.Get()
        expected = (pose.orientation_wxyz[0], *(-x for x in pose.orientation_wxyz[1:]))
        assert (q.GetReal(), *q.GetImaginary()) == pytest.approx(expected)
    bridge.apply("t", "remove_support", "4")
    bridge.apply("t", "release", "5")
    assert not stage.GetPrimAtPath(path)
    # Strip the empty, bridge-owned Scope for comparison only; bridge deletes
    # its joints and never removes another runtime's stage contents.
    stage.RemovePrim(bridge._scope)
    assert stage.GetRootLayer().ExportToString() == before


def test_collision_authored_state_restores_through_stronger_layer_and_no_new_api():
    stage, poses, old = setup()
    base = stage.GetRootLayer().ExportToString()
    stage.SetEditTarget(stage.GetSessionLayer())
    bridge = PhysxAerialTransportBridge(stage, pose_reader=lambda p: poses[p])
    bridge.bind("t", "/World/Drone", "/World/Box", (1, 2, .75))
    states = {}
    for name in ("implicit", "true", "false", "animated"):
        p = stage.GetPrimAtPath("/World/Box/"+name)
        attr = UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr()
        states[name] = attr.Get(), attr.HasAuthoredValueOpinion(), attr.GetTimeSamples()
    bridge.apply("t", "create_support", "1")
    bridge.apply("t", "disable_collision", "2")
    assert all(UsdPhysics.CollisionAPI(stage.GetPrimAtPath("/World/Box/"+name)).GetCollisionEnabledAttr().Get() is False for name in states)
    bridge.apply("t", "rollback", "3")
    for name, expected in states.items():
        attr = UsdPhysics.CollisionAPI(stage.GetPrimAtPath("/World/Box/"+name)).GetCollisionEnabledAttr()
        assert (attr.Get(), attr.HasAuthoredValueOpinion(), attr.GetTimeSamples()) == expected
        assert not stage.GetSessionLayer().GetAttributeAtPath(attr.GetPath())
    assert not stage.GetPrimAtPath("/World/Box/untouched").HasAPI(UsdPhysics.CollisionAPI)
    assert stage.GetRootLayer().ExportToString() == base


def test_live_anchor_refresh_ownership_and_failure_do_not_delete_foreign_joint():
    stage, poses, bridge = setup()
    foreign = UsdPhysics.FixedJoint.Define(stage, "/World/foreign")
    with pytest.raises(SkillContractError, match="owned"):
        bridge._remove(str(foreign.GetPath()))
    poses["/World/Box"] = RigidPose((2, 3, .5), (1, 0, 0, 0))
    assert bridge.measure("t")[1] == pytest.approx((2, 3, .75))
    bridge.apply("t", "create_support", "1")
    bridge.apply("t", "disable_collision", "2")
    with pytest.raises(SkillContractError, match="1 cm"):
        bridge.apply("t", "attach", "3")
    bridge.apply("t", "rollback", "4")
    assert stage.GetPrimAtPath("/World/foreign")


def test_drone_local_mount_rotates_and_admission_uses_mount_instead_of_root():
    stage, poses, old = setup()
    bridge = PhysxAerialTransportBridge(stage, pose_reader=lambda p: poses[p], drone_anchor_local_m=(0, 0, -.2))
    bridge.bind("t", "/World/Drone", "/World/Box", (1, 2, .75))
    d = .2/math.sqrt(2)
    assert bridge.measure("t")[0] == pytest.approx((1, 2+d, .75-d))
    bridge.apply("t", "create_support", "1")
    bridge.apply("t", "disable_collision", "2")
    with pytest.raises(SkillContractError, match="1 cm"):
        bridge.apply("t", "attach", "3")
    poses["/World/Drone"] = RigidPose((1, 2-d, .75+d), poses["/World/Drone"].orientation_wxyz)
    bridge.apply("t", "attach", "3")
    assert bridge.attached("t")
    joint = UsdPhysics.FixedJoint.Get(stage, bridge.evidence("t")["joint_path"])
    assert tuple(joint.GetLocalPos0Attr().Get()) == pytest.approx((0, 0, -.2))
