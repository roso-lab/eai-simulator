"""Source-checked conversion of the Gen3 massless tool interface."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET


def model_massless_interface(stage, arm_path: str, urdf_path: str) -> dict:
    """Replace only a verified empty interface with a wrist-attached frame."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    source_path = Path(urdf_path).expanduser().resolve()
    source_bytes = source_path.read_bytes()
    source = ET.fromstring(source_bytes)
    links = source.findall("link[@name='end_effector_link']")
    joints = source.findall("joint[@name='end_effector']")
    if len(links) != 1 or len(joints) != 1:
        raise ValueError("Expected one URDF end_effector_link and end_effector joint")
    link, joint = links[0], joints[0]
    inertial = link.find("inertial")
    if inertial is None or inertial.find("mass") is None or inertial.find("inertia") is None:
        raise ValueError("Source interface requires explicit zero mass and all six inertia terms")
    values = [float(inertial.find("mass").attrib["value"])]
    values.extend(float(inertial.find("inertia").attrib[key])
                  for key in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz"))
    if any(value != 0.0 for value in values) or link.find("visual") is not None or link.find("collision") is not None:
        raise ValueError("Only a zero-mass, zero-inertia, geometry-free source interface is supported")
    if (joint.get("type") != "fixed" or joint.find("parent") is None or joint.find("child") is None
            or joint.find("parent").get("link") != "bracelet_link"
            or joint.find("child").get("link") != "end_effector_link"):
        raise ValueError("Source interface must be fixed to bracelet_link")
    origin = joint.find("origin")
    if origin is None:
        raise ValueError("Source interface requires an explicit fixed-joint origin")
    translation = [float(value) for value in origin.get("xyz", "0 0 0").split()]
    rotation = [float(value) for value in origin.get("rpy", "0 0 0").split()]
    if len(translation) != 3 or len(rotation) != 3 or not all(map(math.isfinite, translation + rotation)):
        raise ValueError("Invalid URDF interface transform")
    expected = Gf.Matrix4d(1)
    for axis, angle in zip((Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.ZAxis()), rotation):
        expected *= Gf.Matrix4d(1).SetRotate(Gf.Rotation(axis, math.degrees(angle)))
    expected.SetTranslateOnly(Gf.Vec3d(*translation))

    old_path = f"{arm_path}/end_effector_link"
    parent_path = f"{arm_path}/bracelet_link"
    joint_path = f"{parent_path}/end_effector"
    frame_path = f"{parent_path}/end_effector_link"
    old = stage.GetPrimAtPath(old_path)
    parent = stage.GetPrimAtPath(parent_path)
    fixed_prim = stage.GetPrimAtPath(joint_path)
    if not old or not parent or not fixed_prim or not fixed_prim.IsA(UsdPhysics.FixedJoint):
        raise ValueError("USD interface, wrist or fixed joint missing")
    if not old.HasAPI(UsdPhysics.RigidBodyAPI) or not parent.HasAPI(UsdPhysics.RigidBodyAPI):
        raise ValueError("Expected USD source interface and wrist rigid bodies")
    mass_attr = old.GetAttribute("physics:mass")
    mass = mass_attr.Get() if mass_attr and mass_attr.HasAuthoredValue() else None
    if mass is None or not math.isfinite(mass) or not 0 <= mass <= 1e-6:
        raise ValueError("USD interface is not a tiny mass placeholder; refusing to remove real mass")
    if old.GetAllChildren() or old.HasAPI(UsdPhysics.CollisionAPI) or old.IsA(UsdGeom.Gprim):
        raise ValueError("USD interface must be empty and geometry-free")
    if stage.GetPrimAtPath(frame_path):
        raise ValueError("Replacement interface frame already exists")
    if not math.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1.0):
        raise ValueError("Kinova interface conversion requires a stage in metres")
    fixed = UsdPhysics.FixedJoint(fixed_prim)
    if fixed.GetBody0Rel().GetTargets() != [Sdf.Path(parent_path)] or fixed.GetBody1Rel().GetTargets() != [Sdf.Path(old_path)]:
        raise ValueError("USD interface joint must connect wrist to placeholder")
    robot_links = stage.GetPrimAtPath(arm_path).GetRelationship("isaac:physics:robotLinks")
    allowed_references = {fixed.GetBody1Rel().GetPath()}
    if robot_links:
        allowed_references.add(robot_links.GetPath())
    for prim in Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()):
        for relationship in prim.GetRelationships():
            for target in relationship.GetTargets():
                if target.HasPrefix(old.GetPath()) and relationship.GetPath() not in allowed_references:
                    raise ValueError(f"Unsupported reference to interface: {relationship.GetPath()}")
    cache = UsdGeom.XformCache()
    relative = cache.GetLocalToWorldTransform(old) * cache.GetLocalToWorldTransform(parent).GetInverse()
    transform_error = max(abs(relative[row][column] - expected[row][column])
                          for row in range(4) for column in range(4))
    if not math.isfinite(transform_error) or transform_error > 1e-4:
        raise ValueError(f"USD/URDF interface transform mismatch: {transform_error}")
    old.SetActive(False)
    fixed_prim.SetActive(False)
    if robot_links:
        robot_links.SetTargets([target for target in robot_links.GetTargets() if target != old.GetPath()])
    frame = UsdGeom.Xform.Define(stage, frame_path)
    frame.AddTransformOp().Set(relative)
    report = {
        "applied": True, "status": "source_resolved_runtime_interface_only",
        "source_urdf": str(source_path), "source_urdf_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_mass_kg": 0.0, "removed_placeholder_mass_kg": float(mass),
        "old_path": old_path, "disabled_joint_path": joint_path,
        "updated_robot_links_registry": bool(robot_links),
        "frame_path": frame_path, "parent_path": parent_path,
        "local_transform_row_vectors": [list(relative[row]) for row in range(4)],
        "max_source_transform_element_error": transform_error,
        "hardware_calibration": "unknown",
    }
    stage.GetPrimAtPath(arm_path).CreateAttribute("eai:masslessInterface", Sdf.ValueTypeNames.String).Set(
        json.dumps(report, allow_nan=False))
    return report
