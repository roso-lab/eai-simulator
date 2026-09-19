"""Explicit URDF inertial tensors and source-frame checks for Gen3 candidates."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


def origin_transform(origin):
    translation = np.array([float(value) for value in (origin.get("xyz", "0 0 0") if origin is not None else "0 0 0").split()])
    angles = np.array([float(value) for value in (origin.get("rpy", "0 0 0") if origin is not None else "0 0 0").split()])
    if translation.shape != (3,) or angles.shape != (3,) or not np.isfinite(np.r_[translation, angles]).all():
        raise ValueError("URDF origin must have finite xyz and rpy triplets")
    roll, pitch, yaw = angles
    roll_rotation = np.array([[1, 0, 0], [0, math.cos(roll), -math.sin(roll)], [0, math.sin(roll), math.cos(roll)]])
    pitch_rotation = np.array([[math.cos(pitch), 0, math.sin(pitch)], [0, 1, 0], [-math.sin(pitch), 0, math.cos(pitch)]])
    yaw_rotation = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
    transform = np.eye(4)
    transform[:3, :3] = yaw_rotation @ pitch_rotation @ roll_rotation
    transform[:3, 3] = translation
    return transform


def principal_inertia(tensor):
    tensor = np.asarray(tensor, dtype=float)
    if tensor.shape != (3, 3) or not np.isfinite(tensor).all() or not np.allclose(tensor, tensor.T, atol=1e-12, rtol=0):
        raise ValueError("Inertia must be a finite symmetric 3x3 tensor about COM")
    values, axes = np.linalg.eigh(tensor)
    if values[0] <= 0 or values[-1] > values[0] + values[1] + 1e-12:
        raise ValueError("Massive-link inertia must be positive and satisfy the principal-moment triangle inequality")
    if np.linalg.det(axes) < 0:
        axes[:, 0] *= -1
    return values, axes


def read_source(path):
    path = Path(path).expanduser().resolve()
    source_bytes = path.read_bytes()
    root = ET.fromstring(source_bytes)
    links = {}
    for link in root.findall("link"):
        name = link.attrib["name"]
        if name in links:
            raise ValueError(f"Duplicate URDF link: {name}")
        inertial = link.find("inertial")
        if inertial is None or inertial.find("mass") is None or inertial.find("inertia") is None:
            raise ValueError(f"Missing explicit URDF mass/inertia: {name}")
        mass = float(inertial.find("mass").attrib["value"])
        terms = inertial.find("inertia").attrib
        if not all(key in terms for key in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")):
            raise ValueError(f"All six URDF inertia terms are required: {name}")
        tensor = np.array([[float(terms["ixx"]), float(terms["ixy"]), float(terms["ixz"])],
                           [float(terms["ixy"]), float(terms["iyy"]), float(terms["iyz"])],
                           [float(terms["ixz"]), float(terms["iyz"]), float(terms["izz"])]])
        origin = origin_transform(inertial.find("origin"))
        if not math.isfinite(mass) or mass < 0 or not np.isfinite(tensor).all():
            raise ValueError(f"Invalid URDF mass/inertia: {name}")
        if mass == 0:
            if np.any(tensor != 0):
                raise ValueError(f"Massless link must have zero inertia: {name}")
        else:
            principal_inertia(tensor)
        links[name] = {"mass_kg": mass, "com_m": origin[:3, 3],
                       "inertia_at_com_link_kg_m2": origin[:3, :3] @ tensor @ origin[:3, :3].T}
    if "base_link" not in links:
        raise ValueError("Expected Gen3 base_link in URDF")
    parents = {}
    for joint in root.findall("joint"):
        parent, child = joint.find("parent"), joint.find("child")
        if parent is None or child is None or joint.get("type") not in {"fixed", "continuous", "revolute", "prismatic"}:
            raise ValueError("Expected a fixed or one-DOF URDF joint with parent and child")
        parent, child = parent.get("link"), child.get("link")
        if parent not in links or child not in links or child in parents or child == "base_link":
            raise ValueError("Invalid URDF kinematic tree")
        parents[child] = (parent, origin_transform(joint.find("origin")))
    poses = {"base_link": np.eye(4)}
    while len(poses) < len(links):
        pending = {name: poses[parent] @ local for name, (parent, local) in parents.items()
                   if name not in poses and parent in poses}
        if not pending:
            raise ValueError("Disconnected or cyclic URDF kinematic tree")
        poses.update(pending)
    return {"path": str(path), "sha256": hashlib.sha256(source_bytes).hexdigest(), "links": links, "zero_poses": poses}


def check_link_frames(stage, arm_path, source):
    from pxr import UsdGeom

    meters = UsdGeom.GetStageMetersPerUnit(stage)
    base = stage.GetPrimAtPath(f"{arm_path}/base_link")
    if not base or not math.isfinite(meters) or meters <= 0:
        raise ValueError("Missing USD base_link or invalid stage units")
    cache = UsdGeom.XformCache()

    def world_matrix(prim):
        matrix = np.asarray(cache.GetLocalToWorldTransform(prim)).T.copy()
        rotation = matrix[:3, :3]
        if (not np.isfinite(matrix).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6, rtol=0)
                or not math.isclose(np.linalg.det(rotation), 1, abs_tol=1e-6)):
            raise ValueError(f"Non-rigid USD transform: {prim.GetPath()}")
        matrix[:3, 3] *= meters
        return matrix

    base_inverse = np.linalg.inv(world_matrix(base))
    errors = {}
    for name, link in source["links"].items():
        if link["mass_kg"] == 0:
            continue
        prim = stage.GetPrimAtPath(f"{arm_path}/{name}")
        if not prim:
            raise ValueError(f"Missing massive USD link: {name}")
        relative = base_inverse @ world_matrix(prim)
        expected = source["zero_poses"][name]
        position_error = float(np.max(np.abs(relative[:3, 3] - expected[:3, 3])))
        rotation_error = float(np.max(np.abs(relative[:3, :3] - expected[:3, :3])))
        if position_error > 1e-5 or rotation_error > 1e-4:
            raise ValueError(f"USD/URDF zero-pose link frames differ: {name}, position={position_error}, rotation={rotation_error}")
        errors[name] = {"max_translation_error_m": position_error, "max_rotation_element_error": rotation_error}
    return errors


def apply_source_inertias(stage, arm_path, urdf_path):
    """Restore all inertia products without changing authored massive-link masses."""
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics

    source = read_source(urdf_path)
    frame_errors = check_link_frames(stage, arm_path, source)
    meters = UsdGeom.GetStageMetersPerUnit(stage)
    kilograms = UsdPhysics.GetStageKilogramsPerUnit(stage)
    if not math.isfinite(kilograms) or kilograms <= 0:
        raise ValueError("Invalid USD mass units")
    pending = []
    for name in frame_errors:
        prim = stage.GetPrimAtPath(f"{arm_path}/{name}")
        properties = UsdPhysics.MassAPI(prim)
        mass_attr, com_attr = properties.GetMassAttr(), properties.GetCenterOfMassAttr()
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI) or not mass_attr.HasAuthoredValueOpinion() or not com_attr.HasAuthoredValueOpinion():
            raise ValueError(f"Explicit massive USD link and COM required: {name}")
        mass, com = float(mass_attr.Get()) * kilograms, np.array(com_attr.Get()) * meters
        declared = source["links"][name]
        if not math.isclose(mass, declared["mass_kg"], rel_tol=2e-3, abs_tol=2e-4):
            raise ValueError(f"USD/URDF mass mismatch: {name}")
        if com.shape != (3,) or not np.isfinite(com).all() or not np.allclose(com, declared["com_m"], atol=1e-7, rtol=0):
            raise ValueError(f"USD/URDF COM mismatch: {name}")
        values, axes = principal_inertia(declared["inertia_at_com_link_kg_m2"])
        quaternion = Gf.Matrix3d(*axes.T.reshape(-1).tolist()).ExtractRotation().GetQuat()
        pending.append((properties, values / (kilograms * meters ** 2), quaternion, {
            "link": name, "retained_usd_mass_kg": mass, "urdf_mass_kg": declared["mass_kg"],
            "source_inertia_at_com_link_kg_m2": declared["inertia_at_com_link_kg_m2"].tolist(),
            "frame_check": frame_errors[name],
        }))
    for properties, values, quaternion, _ in pending:
        properties.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(*values))
        properties.CreatePrincipalAxesAttr().Set(Gf.Quatf(quaternion))
    report = {"status": "urdf_declared_inertia_runtime_override", "source_urdf": source["path"],
              "source_urdf_sha256": source["sha256"], "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "mass_policy": "retain authored massive-link masses; report source rounding differences",
              "hardware_calibration": "unknown", "links": [item[3] for item in pending]}
    stage.GetPrimAtPath(arm_path).CreateAttribute("eai:sourceInertias", Sdf.ValueTypeNames.String).Set(
        json.dumps(report, allow_nan=False))
    return report
