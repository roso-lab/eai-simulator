import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from EAI_assets.robots.kinova_inertials import apply_source_inertias, check_link_frames, principal_inertia, read_source
from kinova_inertia_audit import audit, tensor_difference


@pytest.fixture
def source_stage(tmp_path):
    urdf = tmp_path / "source.urdf"
    urdf.write_text('''<robot name="inertia_fixture"><link name="base_link"><inertial>
      <origin xyz="0.1 0.2 0.3" rpy="0 0 1.5707963267948966"/><mass value="2"/>
      <inertia ixx="0.02" ixy="0.001" ixz="0.002" iyy="0.03" iyz="0.003" izz="0.04"/>
      </inertial></link></robot>''')
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    root = UsdGeom.Xform.Define(stage, "/Arm")
    stage.SetDefaultPrim(root.GetPrim())
    root.AddRotateXYZOp().Set(Gf.Vec3f(20, 30, 40))
    base = UsdGeom.Xform.Define(stage, "/Arm/base_link")
    UsdPhysics.RigidBodyAPI.Apply(base.GetPrim())
    properties = UsdPhysics.MassAPI.Apply(base.GetPrim())
    properties.CreateMassAttr(2.001)
    properties.CreateCenterOfMassAttr(Gf.Vec3f(0.1, 0.2, 0.3))
    properties.CreateDiagonalInertiaAttr(Gf.Vec3f(0.02, 0.03, 0.04))
    return stage, urdf, tmp_path


def test_restores_rpy_rotated_full_tensor_and_retains_mass_and_com(source_stage):
    stage, urdf, _ = source_stage
    properties = UsdPhysics.MassAPI(stage.GetPrimAtPath("/Arm/base_link"))
    original_mass, original_com = properties.GetMassAttr().Get(), properties.GetCenterOfMassAttr().Get()
    report = apply_source_inertias(stage, "/Arm", urdf)
    rotation = np.array(Gf.Matrix3d(properties.GetPrincipalAxesAttr().Get())).T
    tensor = rotation @ np.diag(properties.GetDiagonalInertiaAttr().Get()) @ rotation.T
    expected = np.array([[0.03, -0.001, -0.003], [-0.001, 0.02, 0.002], [-0.003, 0.002, 0.04]])
    np.testing.assert_allclose(tensor, expected, atol=2e-8, rtol=0)
    assert properties.GetMassAttr().Get() == original_mass
    assert properties.GetCenterOfMassAttr().Get() == original_com
    assert report["hardware_calibration"] == "unknown"
    assert report["links"][0]["urdf_mass_kg"] == 2


def test_unknown_zero_quaternion_is_not_identity_tensor(source_stage):
    stage, urdf, directory = source_stage
    asset = directory / "arm.usda"
    stage.Export(str(asset))
    before = audit(asset, urdf)
    assert before["unknown_usd_tensor_links"] == ["base_link"]
    assert before["rows"][0]["usd_vs_urdf"] is None
    apply_source_inertias(stage, "/Arm", urdf)
    stage.Export(str(directory / "repaired.usda"))
    after = audit(directory / "repaired.usda", urdf)
    assert after["unknown_usd_tensor_links"] == []
    assert after["rows"][0]["usd_vs_urdf"]["match"]


@pytest.mark.parametrize("damage", ["mass", "com", "scale", "missing_product", "nan", "negative", "triangle"])
def test_reject_invalid_source_without_partial_stage_edit(source_stage, damage):
    stage, urdf, _ = source_stage
    properties = UsdPhysics.MassAPI(stage.GetPrimAtPath("/Arm/base_link"))
    if damage == "mass":
        properties.GetMassAttr().Set(8)
    elif damage == "com":
        properties.GetCenterOfMassAttr().Set(Gf.Vec3f(0, 0, 0))
    elif damage == "scale":
        UsdGeom.Xformable(stage.GetPrimAtPath("/Arm")).AddScaleOp().Set(Gf.Vec3f(0.5, 0.5, 0.5))
    else:
        xml = ET.parse(urdf)
        terms = xml.find("link/inertial/inertia")
        if damage == "missing_product":
            del terms.attrib["ixz"]
        else:
            terms.set("ixx", {"nan": "nan", "negative": "-1", "triangle": "1"}[damage])
        xml.write(urdf)
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError):
        apply_source_inertias(stage, "/Arm", urdf)
    assert stage.GetRootLayer().ExportToString() == before


def test_link_frame_mismatch_fails_before_any_inertia_edit(source_stage):
    stage, urdf, _ = source_stage
    xml = ET.parse(urdf)
    child = ET.SubElement(xml.getroot(), "link", name="child")
    child.append(ET.fromstring(ET.tostring(xml.find("link/inertial"))))
    joint = ET.SubElement(xml.getroot(), "joint", name="joint", type="fixed")
    ET.SubElement(joint, "parent", link="base_link")
    ET.SubElement(joint, "child", link="child")
    ET.SubElement(joint, "origin", xyz="0 0 1", rpy="0 0 0")
    xml.write(urdf)
    UsdGeom.Xform.Define(stage, "/Arm/child")
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match="link frames differ"):
        apply_source_inertias(stage, "/Arm", urdf)
    assert stage.GetRootLayer().ExportToString() == before


def test_products_cannot_be_hidden_by_equal_diagonal_terms():
    tensor = np.array([[0.02, 0.002, 0], [0.002, 0.03, 0.001], [0, 0.001, 0.04]])
    assert not tensor_difference(np.diag(np.diag(tensor)), tensor)["match"]
    values, axes = principal_inertia(tensor)
    assert math.isclose(np.linalg.det(axes), 1)
    np.testing.assert_allclose(axes @ np.diag(values) @ axes.T, tensor, atol=1e-15)


def test_missing_optional_origin_is_identity(source_stage):
    _, urdf, _ = source_stage
    xml = ET.parse(urdf)
    inertial = xml.find("link/inertial")
    inertial.remove(inertial.find("origin"))
    xml.write(urdf)
    source = read_source(urdf)
    np.testing.assert_array_equal(source["links"]["base_link"]["com_m"], [0, 0, 0])
    assert source["links"]["base_link"]["inertia_at_com_link_kg_m2"][0, 1] == 0.001
