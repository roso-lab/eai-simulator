import math
import xml.etree.ElementTree as ET

import pytest
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from EAI_assets.robots.kinova_physics import model_massless_interface


@pytest.fixture
def interface(tmp_path):
    source = tmp_path / "arm.urdf"
    source.write_text('''<robot name="fixture">
      <link name="end_effector_link"><inertial><mass value="0"/>
        <inertia ixx="0" ixy="0" ixz="0" iyy="0" iyz="0" izz="0"/>
      </inertial></link>
      <joint name="end_effector" type="fixed"><parent link="bracelet_link"/>
        <child link="end_effector_link"/><origin xyz="0 0 -0.061525" rpy="3.141592653589793 0 0"/>
      </joint></robot>''')
    layer = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(layer, 1)
    root = UsdGeom.Xform.Define(layer, "/gen3")
    layer.SetDefaultPrim(root.GetPrim())
    parent = UsdGeom.Xform.Define(layer, "/gen3/bracelet_link")
    parent_transform = Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d.ZAxis(), 37))
    parent_transform.SetTranslateOnly(Gf.Vec3d(1, 2, 3))
    parent.AddTransformOp().Set(parent_transform)
    UsdPhysics.RigidBodyAPI.Apply(parent.GetPrim())
    UsdPhysics.MassAPI.Apply(parent.GetPrim()).CreateMassAttr(0.364)
    old = UsdGeom.Xform.Define(layer, "/gen3/end_effector_link")
    relative = Gf.Matrix4d(1).SetRotate(Gf.Rotation(Gf.Vec3d.XAxis(), 180))
    relative.SetTranslateOnly(Gf.Vec3d(0, 0, -0.061525))
    old.AddTransformOp().Set(relative * parent_transform)
    UsdPhysics.RigidBodyAPI.Apply(old.GetPrim())
    UsdPhysics.MassAPI.Apply(old.GetPrim()).CreateMassAttr(1e-7)
    joint = UsdPhysics.FixedJoint.Define(layer, "/gen3/bracelet_link/end_effector")
    joint.CreateBody0Rel().SetTargets([parent.GetPath()])
    joint.CreateBody1Rel().SetTargets([old.GetPath()])
    root.GetPrim().CreateRelationship("isaac:physics:robotLinks").SetTargets([parent.GetPath(), old.GetPath()])
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.Xform.Define(stage, "/World/Arm").GetPrim().GetReferences().AddReference(layer.GetRootLayer().identifier)
    return stage, source, layer


def test_frame_preserves_pose_follows_wrist_and_leaves_source_unchanged(interface):
    stage, source, layer = interface
    old = stage.GetPrimAtPath("/World/Arm/end_effector_link")
    original_world = UsdGeom.XformCache().GetLocalToWorldTransform(old)
    source_before = layer.GetRootLayer().ExportToString()
    result = model_massless_interface(stage, "/World/Arm", str(source))
    frame = stage.GetPrimAtPath(result["frame_path"])
    assert Gf.IsClose(UsdGeom.XformCache().GetLocalToWorldTransform(frame), original_world, 1e-10)
    assert not old.IsActive()
    assert not stage.GetPrimAtPath(result["disabled_joint_path"]).IsActive()
    assert not frame.HasAPI(UsdPhysics.RigidBodyAPI)
    assert stage.GetPrimAtPath("/World/Arm").GetRelationship("isaac:physics:robotLinks").GetTargets() == [
        stage.GetPrimAtPath(result["parent_path"]).GetPath()]
    wrist = UsdGeom.Xformable(stage.GetPrimAtPath(result["parent_path"]))
    wrist.GetOrderedXformOps()[0].Set(Gf.Matrix4d(1).SetTranslate(Gf.Vec3d(4, 5, 6)))
    expected = Gf.Matrix4d(result["local_transform_row_vectors"]) * wrist.ComputeLocalToWorldTransform(0)
    assert Gf.IsClose(UsdGeom.XformCache().GetLocalToWorldTransform(frame), expected, 1e-10)
    assert layer.GetRootLayer().ExportToString() == source_before
    assert len([prim for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.RigidBodyAPI)]) == 1
    assert math.isclose(UsdPhysics.MassAPI(wrist.GetPrim()).GetMassAttr().Get(), 0.364, rel_tol=1e-6)


@pytest.mark.parametrize("damage", ["mass", "geometry", "tool", "transform", "wrong_joint", "units", "urdf_mass", "urdf_inertia"])
def test_reject_unsupported_interface_without_partial_edit(interface, damage):
    stage, source, _ = interface
    old = stage.GetPrimAtPath("/World/Arm/end_effector_link")
    if damage == "mass":
        UsdPhysics.MassAPI(old).GetMassAttr().Set(0.1)
    elif damage == "geometry":
        UsdGeom.Cube.Define(stage, str(old.GetPath()) + "/geometry")
    elif damage == "tool":
        tool = UsdPhysics.FixedJoint.Define(stage, "/World/Tool")
        tool.CreateBody0Rel().SetTargets([old.GetPath()])
    elif damage == "transform":
        UsdGeom.Xformable(old).GetOrderedXformOps()[0].Set(Gf.Matrix4d(1))
    elif damage == "wrong_joint":
        UsdPhysics.FixedJoint(stage.GetPrimAtPath("/World/Arm/bracelet_link/end_effector")).GetBody0Rel().SetTargets([])
    elif damage == "units":
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    else:
        xml = ET.parse(source)
        element = xml.find("link/inertial/mass" if damage == "urdf_mass" else "link/inertial/inertia")
        element.set("value" if damage == "urdf_mass" else "ixz", "0.01")
        xml.write(source)
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError):
        model_massless_interface(stage, "/World/Arm", str(source))
    assert stage.GetRootLayer().ExportToString() == before
