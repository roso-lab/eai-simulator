#!/usr/bin/env python3
"""Build and validate the Gazebo SimpleCar robot and supplied C01 sensor assets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

GAZEBO_SOURCE_COMMIT = "772445543473089ee8242771e1d946402162fe04"
SOURCE_HASHES = {
    "step": "9a876860d5a16f36ca01adbcb76666565ab30a7676d51fc1cfb1f685078e9283",
    "spec": "e2aa71974d227ef443f7cb6ecf039cfa72eb24efeb27c66e372d30808cfbcac0",
    "manual": "9e4c2317fb5699f9461046541627f714e1155921631b589c95e6f6b3dd40090f",
    "assembly": "570203e6dc7a976b19e8f3e33bd2e5607dff1c5969caa4b935e14122d5b926d9",
    "imu_text": "7f92f6f97a6d22037512edea290be5a5343aa62ac12a361e5f3a2f80cbbda258",
    "imu_diagram": "0f6bae9285604b1cc06893b3ed37df2b15d9ca72f2157feaca36d03a3c544515",
    "camera_doc": "aa10113e3a42a9860fb231338225bc2ec5937aea90b3336aefea3284054239ee",
    "cyber_sensor_archive": "1b4ad3d039ede4015e6b5477a0a89506946f090bbb277aa3f857858827401b68",
}
CHASSIS_SIZE_M = (0.65, 0.32, 0.40)
CHASSIS_CLEARANCE_M = 0.14
WHEEL_RADIUS_M = 0.06
WHEEL_X_M = 0.20
WHEEL_Y_M = 0.10
WHEEL_Z_M = -0.28
# X follows the 2026-07-31 board decision (gazebo-sim lidar_offset_x and the
# basic_components robot_tf_publisher octagon fragment): the C01 sits on the
# trunk centre line, 0.10 m behind the head front edge which is flush with the
# footprint front (x=+0.30), i.e. base-frame x = 0.20.
LIDAR_MOUNT_BASE_FOOTPRINT_M = (0.2, 0.0, 0.61459)
IMU_OFFSET_VENDOR_M = (0.01890, 0.02464, -0.0535)
IMU_OFFSET_ROS_M = (0.01890, -0.02464, -0.0535)
CAMERA_DEFAULT_BASELINE_M = 0.12
CAMERA_DEFAULT_POSITION_M = (0.326, 0.0, 0.40)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_prim_specs(spec):
    yield spec
    for child in spec.nameChildren:
        yield from _iter_prim_specs(child)


def _missing_visual_reference_targets(layer) -> tuple[str, ...]:
    missing: set[str] = set()
    for root in layer.rootPrims:
        for spec in _iter_prim_specs(root):
            for reference in spec.referenceList.GetAppliedItems():
                if str(reference.assetPath) or not reference.primPath:
                    continue
                target = str(reference.primPath)
                if target.startswith("/visuals/") and not layer.GetPrimAtPath(target):
                    missing.add(target)
    return tuple(sorted(missing))


def _repair_visual_reference_targets(layer) -> tuple[str, ...]:
    from pxr import Sdf

    missing = _missing_visual_reference_targets(layer)
    if not missing:
        return ()
    visuals = layer.GetPrimAtPath("/visuals")
    if not visuals:
        visuals = Sdf.CreatePrimInLayer(layer, "/visuals")
        visuals.typeName = "Scope"
    for target in missing:
        parent_path, _, name = target.rpartition("/")
        parent = layer.GetPrimAtPath(parent_path)
        if not parent:
            parent = Sdf.CreatePrimInLayer(layer, parent_path)
            parent.typeName = "Scope"
        prim = Sdf.PrimSpec(parent, name, Sdf.SpecifierDef, "Xform")
        prim.documentation = "Empty visual target for a link without authored geometry."
    layer.Save()
    remaining = _missing_visual_reference_targets(layer)
    if remaining:
        raise ValueError("SimpleCar visual references remain unresolved: " + ", ".join(remaining))
    return missing


def _git(source_repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source_repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_paths(repo_root: Path) -> dict[str, Path]:
    tmp = repo_root / "tmp"
    radar = tmp / "小雷达相关资料"
    return {
        "step": radar / "OCTAGON-AIO-ZJ-01B模型.STEP",
        "spec": radar / "OCTAGON-AIO-C01产品技术规格书V1.0.pdf",
        "manual": radar / "OCTAGON-AIO-C01 3D Lidar-用户手册（V2.0）.pdf",
        "assembly": radar / "OCTAGON-AIO-ZJ-01B组件图.pdf",
        "imu_text": radar / "IMU雷达内部位置偏移量.txt",
        "imu_diagram": radar / "IMU偏移图示.png",
        "camera_doc": tmp / "SG-VAS-SL-A1主控相机参数(1).docx",
        "cyber_sensor_archive": radar / "CyberSensor3D_PV3.2.1.rar",
    }


def _validate_sources(repo_root: Path, gazebo_repo: Path) -> dict[str, Path]:
    paths = _source_paths(repo_root)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing supplied SimpleCar source material: " + ", ".join(missing))
    for name, expected_hash in SOURCE_HASHES.items():
        actual = _sha256(paths[name])
        if actual != expected_hash:
            raise ValueError(f"Source hash changed for {name}: {actual}")
    if _git(gazebo_repo, "cat-file", "-t", GAZEBO_SOURCE_COMMIT) != "commit":
        raise ValueError(f"Gazebo source commit is unavailable: {GAZEBO_SOURCE_COMMIT}")
    property_xacro = _git(
        gazebo_repo,
        "show",
        f"{GAZEBO_SOURCE_COMMIT}:gazebo_sim/urdf/robot/property.xacro",
    )
    required_fragments = (
        'name="car_length" value="0.65"',
        'name="car_width" value="0.32"',
        'name="car_height" value="0.40"',
        'name="wheel_radius" value="0.06"',
        'name="wheel_joint_x" value="0.2"',
        'name="distance" value="0.14"',
        'name="lidar_hz" value="30"',
        'name="lidar_lasers" value="40"',
        'name="lidar_samples" value="168"',
        'name="lidar_min_range" value="0.05"',
        'name="lidar_max_range" value="50.0"',
    )
    changed = [item for item in required_fragments if item not in property_xacro]
    if changed:
        raise ValueError("Gazebo SimpleCar contract changed: " + ", ".join(changed))
    return paths


def _box_inertia(mass: float, size: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = size
    return (
        mass * (y * y + z * z) / 12.0,
        mass * (x * x + z * z) / 12.0,
        mass * (x * x + y * y) / 12.0,
    )


def _urdf_text() -> str:
    chassis_mass = 3.0
    ixx, iyy, izz = _box_inertia(chassis_mass, CHASSIS_SIZE_M)
    wheel_mass = 4.0
    wheel_inertia = 0.4 * wheel_mass * WHEEL_RADIUS_M * WHEEL_RADIUS_M
    wheel_links: list[str] = []
    wheel_joints: list[str] = []
    for name, x, y in (
        ("front_left", WHEEL_X_M, WHEEL_Y_M),
        ("rear_left", -WHEEL_X_M, WHEEL_Y_M),
        ("front_right", WHEEL_X_M, -WHEEL_Y_M),
        ("rear_right", -WHEEL_X_M, -WHEEL_Y_M),
    ):
        wheel_links.append(f'''  <link name="{name}_wheel_link">
    <inertial><mass value="{wheel_mass}"/><inertia ixx="{wheel_inertia}" ixy="0" ixz="0" iyy="{wheel_inertia}" iyz="0" izz="{wheel_inertia}"/></inertial>
    <visual><geometry><sphere radius="{WHEEL_RADIUS_M}"/></geometry><material name="wheel"><color rgba="0.04 0.05 0.06 1"/></material></visual>
    <collision><geometry><sphere radius="{WHEEL_RADIUS_M}"/></geometry></collision>
  </link>''')
        wheel_joints.append(f'''  <joint name="{name}_wheel_joint" type="fixed">
    <parent link="base_link"/><child link="{name}_wheel_link"/>
    <origin xyz="{x} {y} {WHEEL_Z_M}" rpy="0 0 0"/>
  </joint>''')
    base_center_z = CHASSIS_CLEARANCE_M + CHASSIS_SIZE_M[2] / 2.0
    lidar_relative_z = LIDAR_MOUNT_BASE_FOOTPRINT_M[2] - base_center_z
    camera_relative_z = CAMERA_DEFAULT_POSITION_M[2] - base_center_z
    return f'''<?xml version="1.0"?>
<robot name="simple_car">
  <material name="chassis"><color rgba="0.16 0.19 0.22 1"/></material>
  <material name="sensor"><color rgba="0.72 0.74 0.76 1"/></material>
  <link name="base_link">
    <inertial><mass value="{chassis_mass}"/><inertia ixx="{ixx}" ixy="0" ixz="0" iyy="{iyy}" iyz="0" izz="{izz}"/></inertial>
    <visual><geometry><box size="{' '.join(map(str, CHASSIS_SIZE_M))}"/></geometry><material name="chassis"/></visual>
    <collision><geometry><box size="{' '.join(map(str, CHASSIS_SIZE_M))}"/></geometry></collision>
  </link>
  <link name="base_footprint"/>
  <joint name="base_footprint_joint" type="fixed"><parent link="base_link"/><child link="base_footprint"/><origin xyz="0 0 {-base_center_z}"/></joint>
{chr(10).join(wheel_links)}
{chr(10).join(wheel_joints)}
  <link name="lidar_link"/>
  <joint name="lidar_mount_joint" type="fixed"><parent link="base_link"/><child link="lidar_link"/><origin xyz="{LIDAR_MOUNT_BASE_FOOTPRINT_M[0]} {LIDAR_MOUNT_BASE_FOOTPRINT_M[1]} {lidar_relative_z}" rpy="0 0 0"/></joint>
  <link name="c01_body_link">
    <inertial><origin xyz="0 0 0.0389"/><mass value="0.42"/><inertia ixx="0.00041" ixy="0" ixz="0" iyy="0.00041" iyz="0" izz="0.00038"/></inertial>
    <visual><origin xyz="0 0 0.0389"/><geometry><box size="0.073 0.07375 0.0778"/></geometry><material name="sensor"/></visual>
    <collision><origin xyz="0 0 0.0389"/><geometry><box size="0.073 0.07375 0.0778"/></geometry></collision>
  </link>
  <joint name="c01_body_joint" type="fixed"><parent link="lidar_link"/><child link="c01_body_link"/></joint>
  <link name="imu_link"/>
  <joint name="imu_mount_joint" type="fixed"><parent link="lidar_link"/><child link="imu_link"/><origin xyz="{' '.join(map(str, IMU_OFFSET_ROS_M))}"/></joint>
  <link name="camera_left_link"/>
  <joint name="camera_left_mount_joint" type="fixed"><parent link="base_link"/><child link="camera_left_link"/><origin xyz="{CAMERA_DEFAULT_POSITION_M[0]} {CAMERA_DEFAULT_BASELINE_M / 2.0} {camera_relative_z}"/></joint>
  <link name="camera_right_link"/>
  <joint name="camera_right_mount_joint" type="fixed"><parent link="base_link"/><child link="camera_right_link"/><origin xyz="{CAMERA_DEFAULT_POSITION_M[0]} {-CAMERA_DEFAULT_BASELINE_M / 2.0} {camera_relative_z}"/></joint>
</robot>
'''


def _lidar_profile() -> dict[str, object]:
    elevations = [round(-15.0 + index * (60.0 / 39.0), 8) for index in range(40)]
    return {
        "class": "sensor",
        "type": "lidar",
        "name": "OCTAGON-AIO-C01",
        "driveWorksId": "GENERIC",
        "profile": {
            "scanType": "rotary",
            "intensityProcessing": "normalization",
            "rotationDirection": "CCW",
            "rayType": "IDEALIZED",
            "nearRangeM": 0.05,
            "minDistBetweenEchos": 0.05,
            "farRangeM": 50.0,
            "startAzimuthDeg": 0.0,
            "endAzimuthDeg": 360.0,
            "upElevationDeg": 45.0,
            "downElevationDeg": -15.0,
            "rangeResolutionM": 0.01,
            "rangeAccuracyM": 0.015,
            "avgPowerW": 0.0092,
            "minReflectance": 0.1,
            "minReflectanceRange": 25.0,
            "wavelengthNm": 905.0,
            "pulseTimeNs": 4,
            "azimuthErrorMean": 0.0,
            "azimuthErrorStd": 0.0,
            "elevationErrorMean": 0.0,
            "elevationErrorStd": 0.0,
            "maxReturns": 1,
            "scanRateBaseHz": 30.0,
            "reportRateBaseHz": 5000,
            "numberOfEmitters": 40,
            "emitterStateCount": 1,
            "emitterStates": [{
                "azimuthDeg": [0.0] * 40,
                "elevationDeg": elevations,
                "fireTimeNs": [0] * 40,
            }],
            "intensityMappingType": "LINEAR",
        },
        "eaiNotes": {
            "scanApproximation": "40-emitter deterministic rotary grid; supplied sensor is non-repeating",
            "pointsPerSecond": 200000,
            "authoritativeScanRateHz": 30,
            "olderManualScanRateHz": 14,
            "unspecifiedIdealizedDefaults": [
                "avgPowerW", "wavelengthNm", "pulseTimeNs", "intensityMappingType", "maxReturns"
            ],
        },
    }


def _mid360_profile() -> dict[str, object]:
    # Mirrors the Gazebo overlay's idealized Mid-360 guest approximation
    # (property.xacro mid360 set: 10 Hz, 16 lasers x 350 samples, 0.10-130.0 m,
    # -7..53 deg vertical window, 20 deg forward holder pitch applied at mount).
    elevations = [round(-7.0 + index * 4.0, 8) for index in range(16)]
    return {
        "class": "sensor",
        "type": "lidar",
        "name": "MID360",
        "driveWorksId": "GENERIC",
        "profile": {
            "scanType": "rotary",
            "intensityProcessing": "normalization",
            "rotationDirection": "CCW",
            "rayType": "IDEALIZED",
            "nearRangeM": 0.10,
            "minDistBetweenEchos": 0.05,
            "farRangeM": 130.0,
            "startAzimuthDeg": 0.0,
            "endAzimuthDeg": 360.0,
            "upElevationDeg": 53.0,
            "downElevationDeg": -7.0,
            "rangeResolutionM": 0.01,
            "rangeAccuracyM": 0.015,
            "avgPowerW": 0.0092,
            "minReflectance": 0.1,
            "minReflectanceRange": 25.0,
            "wavelengthNm": 905.0,
            "pulseTimeNs": 4,
            "azimuthErrorMean": 0.0,
            "azimuthErrorStd": 0.0,
            "elevationErrorMean": 0.0,
            "elevationErrorStd": 0.0,
            "maxReturns": 1,
            "scanRateBaseHz": 10.0,
            "reportRateBaseHz": 3500,
            "numberOfEmitters": 16,
            "emitterStateCount": 1,
            "emitterStates": [{
                "azimuthDeg": [0.0] * 16,
                "elevationDeg": elevations,
                "fireTimeNs": [0] * 16,
            }],
            "intensityMappingType": "LINEAR",
        },
        "eaiNotes": {
            "scanApproximation": "16-emitter deterministic rotary grid; supplied sensor is non-repeating",
            "pointsPerSecond": 56000,
            "authoritativeScanRateHz": 10,
            "gazeboParity": "16 lasers x 350 samples x 10 Hz; 0.10-130.0 m; pitch 0.349066 rad",
            "unspecifiedIdealizedDefaults": [
                "avgPowerW", "wavelengthNm", "pulseTimeNs", "intensityMappingType", "maxReturns"
            ],
        },
    }


def _wrapper_text(core_default_prim: str) -> str:
    return f'''#usda 1.0
(
    customLayerData = {{
        string gazeboSourceCommit = "{GAZEBO_SOURCE_COMMIT}"
        string sensorSourceStepSha256 = "{SOURCE_HASHES['step']}"
    }}
    defaultPrim = "SimpleCar"
    metersPerUnit = 1
    upAxis = "Z"
)

# Robot-only layer: the chassis, planar supports, and the lidar holder that
# belongs to this Gazebo-derived robot description. The built-in C01 sensor
# visual is referenced from the shared sensor payload at
# usd/payloads/sensors/octagon_aio_c01/ (materials live in that CAD layer).
def Xform "SimpleCar" (
    prepend references = @./gshub_core.usd@<{core_default_prim}>
)
{{
    over "lidar_link"
    {{
        double3 xformOp:translate = (0.2, 0, 0.27459)

        def Xform "c01_visual" (
            prepend references = @../../payloads/sensors/octagon_aio_c01/octagon_aio_c01_visual.usda@
        )
        {{
            custom string eai:collisionPolicy = "visual-only; simplified body collision is authored in the URDF"
        }}

        def Cylinder "lidar_holder_visual" (
            customData = {{
                string "eai:collisionPolicy" = "visual-only"
            }}
        )
        {{
            double height = 0.0619
            color3f[] primvars:displayColor = [(0.05, 0.05, 0.05)]
            double radius = 0.03
            double3 xformOp:translate = (0, 0, -0.04359)
            uniform token[] xformOpOrder = ["xformOp:translate"]
        }}
    }}

    over "joints"
    {{
        over "lidar_mount_joint"
        {{
            point3f physics:localPos0 = (0.2, 0, 0.27459)
        }}
    }}
}}
'''


def _payload_wrapper_text() -> str:
    return f'''#usda 1.0
(
    customLayerData = {{
        string purpose = "Attachable OCTAGON-AIO-C01 sensor payload visual"
        string materialSpec = "OCTAGON-AIO-C01 product spec V1.0 (2025-07-18): aluminium-alloy silver-white housing (sandblasted), polycarbonate optical cover (high-gloss black), 73 x 73.75 x 77.8 mm, 420 g, 4x M3 bottom mounting holes"
        string sensorSourceStepSha256 = "{SOURCE_HASHES['step']}"
    }}
    defaultPrim = "OctagonAioC01"
    metersPerUnit = 1
    upAxis = "Z"
)

# Sensor frame: the origin is the lidar scan origin, matching the native
# OmniLidar prim the runtime creates as a child of this payload root. The
# housing spans z = -10.3 mm .. +67.5 mm in this frame after the CAD
# rotation below. Hosts mount this root directly on their lidar payload
# flange (RobotOption lidar_mount_link + lidar_offset); no adapter
# geometry belongs here.
#
# The referenced CAD layer is de-instanced and carries the spec-derived
# materials itself (/octagon_aio_c01/Looks: HousingAluminumSilver,
# CoverPolycarbonateBlack, FastenerSteelDark), so it also renders
# correctly when referenced from simple_car.usda.
def Xform "OctagonAioC01"
{{
    def Xform "manufacturer_visual" (
        prepend references = @./octagon_aio_c01.usd@
    )
    {{
        custom string eai:collisionPolicy = "visual-only; the native OmniLidar runtime is created as a sibling child of this payload root"
        float xformOp:rotateX = 90
        float xformOp:rotateZ = 90
        uniform token[] xformOpOrder = ["xformOp:rotateZ", "xformOp:rotateX"]

        over "octagon_aio_c01"
        {{
            # Hide the 306.7 mm flying cable; it is not part of the mounted
            # sensor envelope.
            over "tn__M12A12P_sA8phy5atL"
            {{
                token visibility = "invisible"
            }}
        }}
    }}
}}
'''


# Spec V1.0 items 23-25 plus the manufacturer product reference image: the
# octagonal base band, bottom plate, and side connector are sandblasted
# silver-white aluminium; the upper cylinder is the high-gloss black PC
# optical cover; fasteners are dark steel. USD instanceable metadata cannot
# be overridden from an outer referencing layer, so the de-instance opinions
# and these material definitions/bindings are authored inside the CAD layer.
CAD_MATERIALS = {
    "HousingAluminumSilver": ("tn__OCTAAIOSJG01D", "tn__OCTAAIOSJG02B", "tn__OCTAAIOSJG03C"),
    "CoverPolycarbonateBlack": ("tn__20250529_XC58",),
    "FastenerSteelDark": ("tn__crossrecessed",),
}
CAD_MATERIAL_PARAMS = {
    "HousingAluminumSilver": ((0.85, 0.86, 0.88), 0.9, 0.42),
    "CoverPolycarbonateBlack": ((0.012, 0.012, 0.014), 0.0, 0.1),
    "FastenerSteelDark": ((0.062, 0.062, 0.069), 0.85, 0.35),
}
CAD_CABLE_PART_PREFIX = "tn__M12A12P"


def _materialize_cad(stage, cad_path) -> None:
    """De-instance the converted CAD and bind spec-derived materials in place."""
    from pxr import Gf, Sdf, UsdShade

    stage.SetEditTarget(Usd.EditTarget(stage.GetRootLayer()))
    for prim in list(stage.Traverse()):
        if prim.IsInstanceable():
            prim.SetInstanceable(False)

    materials = {}
    for name, (diffuse, metallic, roughness) in CAD_MATERIAL_PARAMS.items():
        path = f"/octagon_aio_c01/Looks/{name}"
        material = UsdShade.Material.Define(stage, path)
        shader = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(*diffuse)
        )
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(1.0)
        material.CreateSurfaceOutput().ConnectToSource(
            shader.ConnectableAPI(), "surface"
        )
        materials[name] = material

    def material_for(part_name: str):
        if part_name.startswith(CAD_CABLE_PART_PREFIX):
            return None
        for name, prefixes in CAD_MATERIALS.items():
            if any(part_name.startswith(prefix) for prefix in prefixes):
                return name
        raise RuntimeError(f"Unclassified OCTAGON CAD part: {part_name}")

    bound = 0
    inner = stage.GetPrimAtPath("/octagon_aio_c01/octagon_aio_c01")
    if not inner.IsValid():
        raise RuntimeError("Converted OCTAGON CAD is missing its container prim")
    for part in inner.GetChildren():
        name = material_for(part.GetName())
        if name is None:
            continue
        for prim in Usd.PrimRange(part):
            if prim.GetTypeName() != "Mesh":
                continue
            UsdShade.MaterialBindingAPI(prim).Bind(materials[name])
            bound += 1
    if bound == 0:
        raise RuntimeError("OCTAGON CAD materialization bound no meshes")
    stage.GetRootLayer().Save()


def _write_thumbnail(path: Path) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 420), (236, 238, 240))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((90, 170, 550, 330), radius=18, fill=(44, 51, 58), outline=(13, 16, 20), width=5)
    for x in (145, 495):
        for y in (170, 330):
            draw.ellipse((x - 36, y - 36, x + 36, y + 36), fill=(18, 20, 23), outline=(95, 100, 106), width=4)
    draw.rectangle((338, 112, 356, 170), fill=(110, 116, 122))
    draw.rounded_rectangle((316, 52, 378, 120), radius=7, fill=(195, 199, 202), outline=(59, 65, 70), width=4)
    draw.ellipse((322, 60, 372, 110), outline=(22, 118, 152), width=5)
    draw.rectangle((520, 220, 556, 282), fill=(31, 34, 38), outline=(4, 5, 6), width=3)
    draw.line((90, 250, 36, 250), fill=(0, 137, 174), width=7)
    draw.polygon(((36, 250), (57, 237), (57, 263)), fill=(0, 137, 174))
    image.save(path, format="PNG", optimize=True)


def _generated_hashes(target: Path) -> dict[str, object]:
    return {
        path.relative_to(target).as_posix(): {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.name != "source.json"
    }


def _robot_metadata(paths: dict[str, Path], target: Path, gazebo_repo: Path) -> dict[str, object]:
    return {
        "asset": "SimpleCar Gazebo-compatible robot with OCTAGON-AIO-C01 and SG-VAS-SL-A1 simulation contract",
        "gazebo_source_repository": _git(gazebo_repo, "remote", "get-url", "origin"),
        "gazebo_source_commit": GAZEBO_SOURCE_COMMIT,
        "gazebo_source_files": [
            "gazebo_sim/urdf/robot/property.xacro",
            "gazebo_sim/urdf/robot/car_base.urdf.xacro",
            "gazebo_sim/urdf/robot/plugins/gazebo_control_plugin_prefix.xacro",
        ],
        "supplied_source_hashes": {name: _sha256(path) for name, path in paths.items()},
        "license": {
            "gazebo_robot_description": "Apache-2.0",
            "supplied_manufacturer_material": "No redistribution license found; provider publication requires maintainer permission review",
        },
        "sensor_payload_location": (
            "C01/Mid-360 sensor payload files live in usd/payloads/sensors/"
            "{octagon_aio_c01,mid360}; this robot layer references the shared "
            "materialized C01 payload visual for its built-in suite"
        ),
        "robot_contract": {
            "chassis_size_m": list(CHASSIS_SIZE_M),
            "ground_clearance_m": CHASSIS_CLEARANCE_M,
            "support_sphere_radius_m": WHEEL_RADIUS_M,
            "wheel_centers_xy_m": [WHEEL_X_M, WHEEL_Y_M],
            "source_mass_kg": {"chassis": 3.0, "each_support_sphere": 4.0, "holder": 0.1},
            "footprint_m": {"front": 0.30, "rear": 0.35, "left": 0.16, "right": 0.16},
            "gazebo_control": "50 Hz odometry planar root velocity plugin; control update 20 Hz",
            "isaac_control": "gravity-free planar root velocity with non-contact visual support spheres",
            "physx_workaround": "Sphere-floor contact locks planar yaw in Isaac Sim 5.1; chassis collision remains active",
            "dynamics_warning": "Gazebo masses and frictionless support spheres are validation proxies, not measured hardware dynamics",
        },
        "lidar_contract": {
            "frame": "lidar_link",
            "mount_frame": "base_footprint",
            "position_m": list(LIDAR_MOUNT_BASE_FOOTPRINT_M),
            "rpy_rad": [0.0, 0.0, 0.0],
            "coordinate_system": "+X forward, +Y left, +Z up",
            "horizontal_fov_deg": 360, "vertical_fov_deg": [-15, 45],
            "range_m": [0.05, 50.0], "scan_rate_hz": 30,
            "manual_scan_rate_discrepancy_hz": 14, "points_per_second": 200000,
            "gazebo_grid_points_per_second": 201600,
            "scan_pattern": "deterministic grid approximation of a non-repeating scanner",
        },
        "mid360_contract": {
            "frame": "lidar_link",
            "mount_frame": "base_footprint",
            "position_m": list(LIDAR_MOUNT_BASE_FOOTPRINT_M),
            "pitch_rad": 0.349066,
            "horizontal_fov_deg": 360, "vertical_fov_deg": [-7, 53],
            "range_m": [0.10, 130.0], "scan_rate_hz": 10,
            "points_per_second": 56000,
            "gazebo_parity": "16 lasers x 350 samples x 10 Hz from property.xacro mid360 defaults",
            "scan_pattern": "deterministic grid approximation of a non-repeating scanner",
        },
        "imu_contract": {
            "rate_hz": 200, "vendor_text_offset_m": list(IMU_OFFSET_VENDOR_M),
            "ros_default_offset_m": list(IMU_OFFSET_ROS_M),
            "y_sign_resolution": "diagram places IMU on sensor-right; converted to ROS +Y-left as negative Y; keep configurable until manufacturer confirms signed transform",
        },
        "camera_contract": {
            "count": 2, "resolution": [1920, 1080], "rate_hz": 30,
            "shutter": "global", "pixel_format": "RAW10", "focal_length_mm": 3.0,
            "f_number": 2.2, "fov_deg": {"horizontal": 120, "vertical": 65, "diagonal": 144},
            "simulation_default_mount_m": {"x": CAMERA_DEFAULT_POSITION_M[0], "z": CAMERA_DEFAULT_POSITION_M[2], "baseline": CAMERA_DEFAULT_BASELINE_M},
            "mount_warning": "No manufacturer baseline/extrinsics were supplied; this default is configurable and is not calibration data",
            "optics_warning": "Supplied focal length, pixel pitch, resolution, and stated FOV are not mutually pinhole-consistent; simulation prioritizes stated FOV",
        },
        "generated": _generated_hashes(target),
    }


def _sensor_metadata(paths: dict[str, Path], target: Path) -> dict[str, object]:
    return {
        "asset": (
            "OCTAGON-AIO-C01 3D lidar sensor payload shared by simple_car's "
            "built-in suite and the generic octagon_aio_c01 attachment"
        ),
        "specification": {
            "document": "OCTAGON-AIO-C01产品技术规格书 V01 (2025-07-18, 嘉兴赛感智能科技有限公司)",
            "dimensions_mm": [73.0, 73.75, 77.8],
            "mass_g": 420,
            "housing_material": "aluminium alloy, silver white, sandblasted (items 23/25)",
            "optical_cover_material": "polycarbonate, high-gloss black window band on the upper cylinder (item 24)",
            "mounting": "4x M3 screws, 5 mm deep, bottom face",
            "laser_wavelength_nm": 905,
            "max_range_m": {"reflectance_90": 50.0, "reflectance_10": 25.0},
            "blind_zone_m": 0.05,
            "fov_deg": {"horizontal": 360.0, "vertical": [-15.0, 45.0]},
            "scan_rate_hz": 30.0,
            "points_per_second": 200000,
            "imu_rate_hz": 200,
        },
        "materials_usd": {
            "source": "specification items 23-25 plus the manufacturer product reference image",
            "layers": "authored inside octagon_aio_c01.usd at /octagon_aio_c01/Looks",
            **{
                name: {
                    "diffuseColor": list(params[0]),
                    "metallic": params[1],
                    "roughness": params[2],
                }
                for name, params in CAD_MATERIAL_PARAMS.items()
            },
        },
        "sensor_frame": {
            "origin": "lidar scan origin; the runtime creates the native OmniLidar as a child of the payload root",
            "housing_extent_m": [[-0.0376, -0.0405, -0.0103], [0.037, 0.0325, 0.0675]],
            "cable": "306.7 mm flying cable hidden via visibility over in the wrapper; not part of the mounted envelope",
        },
        "mounting_policy": (
            "hosts mount the payload root on their lidar payload flange "
            "(RobotOption lidar_mount_link + lidar_offset), the same placement "
            "as the native RosLidar payload; no adapter geometry is included"
        ),
        "cad_source": {
            "step_sha256": SOURCE_HASHES["step"],
            "converter": "omni.kit.converter.hoops_core 509.1.0",
            "materialization": (
                "de-instanced and rebound in place; USD instanceable metadata "
                "cannot be overridden from an outer referencing layer"
            ),
        },
        "license": {
            "cad_model": "No redistribution license found; provider publication requires maintainer permission review",
        },
        "generated": _generated_hashes(target),
    }


def _convert_assets(paths: dict[str, Path], target: Path) -> None:
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True, "fast_shutdown": True})
    try:
        import omni.kit.app
        import omni.kit.commands
        from pxr import Usd

        manager = omni.kit.app.get_app().get_extension_manager()
        if not manager.set_extension_enabled_immediate("omni.kit.converter.hoops_core", True):
            raise RuntimeError("Failed to enable omni.kit.converter.hoops_core")
        app.update()
        from omni.kit.converter.hoops_core import get_instance

        sensor_dir = target / "sensor" / "octagon_aio_c01"
        robot_dir = target / "robot"
        sensor_dir.mkdir(parents=True, exist_ok=True)
        robot_dir.mkdir(parents=True, exist_ok=True)

        converter = get_instance()
        if converter is None:
            raise RuntimeError("HOOPS converter instance is unavailable")
        cad_path = sensor_dir / "octagon_aio_c01.usd"
        options = {
            "bInstancing": "true", "bGlobalXforms": "false", "bConvertHidden": "false",
            "bOptimize": "true", "bDedup": "true", "iTessLOD": "2",
            "bAccurateSurfaceCurvatures": "true", "bUseNormals": "true",
            "bUseMaterials": "true", "materialType": "1", "bReportProgress": "true",
            "bConvertCurves": "false", "bConvertMetadata": "false", "iUpAxis": "0",
            "dMetersPerUnit": "1.0", "sOptimizeConfig": "",
        }
        _output, status = asyncio.get_event_loop().run_until_complete(
            converter.create_converter_task(str(paths["step"]), str(cad_path), options)
        )
        if status.error_code != 0 or not cad_path.is_file():
            raise RuntimeError(f"STEP conversion failed: {status.error_code} {status.error_msg}")

        cad_stage = Usd.Stage.Open(str(cad_path))
        if cad_stage is None:
            raise RuntimeError(f"Converted OCTAGON CAD is unreadable: {cad_path}")
        _materialize_cad(cad_stage, cad_path)
        (sensor_dir / "octagon_aio_c01_visual.usda").write_text(
            _payload_wrapper_text(), encoding="utf-8"
        )

        urdf_path = robot_dir / "simple_car.urdf"
        urdf_path.write_text(_urdf_text(), encoding="utf-8")
        if not manager.set_extension_enabled_immediate("isaacsim.asset.importer.urdf", True):
            raise RuntimeError("Failed to enable the installed Isaac URDF importer")
        app.update()
        from isaacsim.asset.importer.urdf._urdf import UrdfJointTargetType

        ok, import_config = omni.kit.commands.execute("URDFCreateImportConfig")
        if not ok:
            raise RuntimeError("Failed to create URDF import configuration")
        import_config.set_distance_scale(1.0)
        import_config.set_make_default_prim(True)
        import_config.set_create_physics_scene(False)
        import_config.set_density(0.0)
        import_config.set_convex_decomp(False)
        import_config.set_collision_from_visuals(False)
        import_config.set_merge_fixed_joints(False)
        import_config.set_fix_base(False)
        import_config.set_self_collision(False)
        import_config.set_parse_mimic(False)
        import_config.set_replace_cylinders_with_capsules(False)
        ok, robot_model = omni.kit.commands.execute(
            "URDFParseFile",
            urdf_path=str(urdf_path),
            import_config=import_config,
        )
        if not ok:
            raise RuntimeError(f"Failed to parse SimpleCar URDF: {urdf_path}")
        for joint in robot_model.joints.values():
            joint.drive.set_target_type(UrdfJointTargetType.JOINT_DRIVE_NONE)
            joint.drive.set_strength(0.0)
            joint.drive.set_damping(0.0)
        core_path = robot_dir / "gshub_core.usd"
        ok, _ = omni.kit.commands.execute(
            "URDFImportRobot",
            urdf_path=str(urdf_path),
            urdf_robot=robot_model,
            import_config=import_config,
            dest_path=str(core_path),
        )
        if not ok or not core_path.is_file():
            raise RuntimeError(f"Failed to import SimpleCar URDF: {urdf_path}")
        from pxr import Sdf

        base_configuration = core_path.parent / "configuration" / "gshub_core_base.usd"
        base_layer = Sdf.Layer.FindOrOpen(str(base_configuration))
        if base_layer is None:
            raise RuntimeError(f"Generated SimpleCar base layer is unreadable: {base_configuration}")
        _repair_visual_reference_targets(base_layer)
        stage = Usd.Stage.Open(str(core_path))
        if stage is None or not stage.GetDefaultPrim().IsValid():
            raise RuntimeError(f"Generated SimpleCar USD has no default prim: {core_path}")
        default_prim = str(stage.GetDefaultPrim().GetPath())
        (robot_dir / "simple_car.usda").write_text(_wrapper_text(default_prim), encoding="utf-8")
        (target / ".conversion-complete").write_text(default_prim + "\n", encoding="utf-8")
    except BaseException:
        (target / ".conversion-error").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        app.close()


def install(repo_root: Path, gazebo_repo: Path) -> None:
    paths = _validate_sources(repo_root, gazebo_repo)
    robot_target = repo_root / "usd" / "robot" / "simple_car"
    sensor_target = repo_root / "usd" / "payloads" / "sensors" / "octagon_aio_c01"
    mid360_target = repo_root / "usd" / "payloads" / "sensors" / "mid360"
    thumbnail = repo_root / "usd" / "picture" / "processed" / "robot" / "simple_car.png"
    for directory in (robot_target, sensor_target, mid360_target):
        directory.mkdir(parents=True, exist_ok=True)
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="eai-simple_car-") as temp_dir:
        generated = Path(temp_dir) / "simple_car"
        sensor_generated = generated / "sensor" / "octagon_aio_c01"
        mid360_generated = generated / "mid360"
        sensor_generated.mkdir(parents=True)
        mid360_generated.mkdir(parents=True)
        (sensor_generated / "OCTAGON_AIO_C01.json").write_text(json.dumps(_lidar_profile(), indent=2) + "\n", encoding="utf-8")
        (mid360_generated / "MID360.json").write_text(json.dumps(_mid360_profile(), indent=2) + "\n", encoding="utf-8")
        conversion = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--repo-root",
                str(repo_root),
                "--gazebo-repo",
                str(gazebo_repo),
                "--convert-output",
                str(generated),
            ],
            check=False,
        )
        complete = generated / ".conversion-complete"
        if conversion.returncode != 0 or not complete.is_file():
            error_path = generated / ".conversion-error"
            details = error_path.read_text(encoding="utf-8") if error_path.is_file() else "no child traceback"
            raise RuntimeError(
                f"SimpleCar conversion worker failed with code {conversion.returncode}: {details}"
            )
        complete.unlink()
        error_path = generated / ".conversion-error"
        if error_path.is_file():
            error_path.unlink()
        for target, source in (
            (robot_target, generated / "robot"),
            (sensor_target, sensor_generated),
            (mid360_target, mid360_generated),
        ):
            for item in tuple(target.iterdir()):
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            shutil.copytree(source, target, dirs_exist_ok=True)
    _write_thumbnail(thumbnail)
    (robot_target / "source.json").write_text(json.dumps(_robot_metadata(paths, robot_target, gazebo_repo), indent=2) + "\n", encoding="utf-8")
    (sensor_target / "source.json").write_text(json.dumps(_sensor_metadata(paths, sensor_target), indent=2) + "\n", encoding="utf-8")
    check(repo_root, gazebo_repo=gazebo_repo)


def check(repo_root: Path, *, gazebo_repo: Path | None = None) -> None:
    from pxr import Sdf

    robot_target = repo_root / "usd" / "robot" / "simple_car"
    sensor_target = repo_root / "usd" / "payloads" / "sensors" / "octagon_aio_c01"
    mid360_target = repo_root / "usd" / "payloads" / "sensors" / "mid360"
    required = (
        robot_target / "simple_car.usda", robot_target / "gshub_core.usd",
        robot_target / "simple_car.urdf", robot_target / "source.json",
        sensor_target / "octagon_aio_c01.usd", sensor_target / "octagon_aio_c01_visual.usda",
        sensor_target / "OCTAGON_AIO_C01.json", sensor_target / "source.json",
        mid360_target / "MID360.json",
        repo_root / "usd" / "picture" / "processed" / "robot" / "simple_car.png",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Incomplete SimpleCar installation: " + ", ".join(missing))
    profile = json.loads((sensor_target / "OCTAGON_AIO_C01.json").read_text(encoding="utf-8"))
    robot_metadata = json.loads((robot_target / "source.json").read_text(encoding="utf-8"))
    sensor_metadata = json.loads((sensor_target / "source.json").read_text(encoding="utf-8"))
    base_layer = Sdf.Layer.FindOrOpen(str(robot_target / "configuration" / "gshub_core_base.usd"))
    if base_layer is None:
        raise ValueError("Generated SimpleCar base layer is unreadable")
    unresolved_visuals = _missing_visual_reference_targets(base_layer)
    if unresolved_visuals:
        raise ValueError(
            "SimpleCar visual references remain unresolved: " + ", ".join(unresolved_visuals)
        )
    robot = ET.fromstring((robot_target / "simple_car.urdf").read_text(encoding="utf-8"))
    joint_names = {joint.attrib["name"] for joint in robot.findall("joint")}
    expected_joints = {
        "front_left_wheel_joint", "rear_left_wheel_joint", "front_right_wheel_joint",
        "rear_right_wheel_joint", "lidar_mount_joint", "imu_mount_joint",
    }
    if not expected_joints <= joint_names:
        raise ValueError("Generated SimpleCar URDF is missing required joints")
    lidar = profile["profile"]
    expected_profile = {
        "nearRangeM": 0.05,
        "farRangeM": 50.0,
        "startAzimuthDeg": 0.0,
        "endAzimuthDeg": 360.0,
        "upElevationDeg": 45.0,
        "downElevationDeg": -15.0,
        "scanRateBaseHz": 30.0,
        "reportRateBaseHz": 5000,
        "numberOfEmitters": 40,
    }
    for key, value in expected_profile.items():
        if lidar.get(key) != value:
            raise ValueError(f"SimpleCar LiDAR profile changed: {key}")
    for relative, detail in robot_metadata["generated"].items():
        path = robot_target / relative
        if not path.is_file() or _sha256(path) != detail["sha256"]:
            raise ValueError(f"Generated SimpleCar robot asset hash mismatch: {relative}")
    for relative, detail in sensor_metadata["generated"].items():
        path = sensor_target / relative
        if not path.is_file() or _sha256(path) != detail["sha256"]:
            raise ValueError(f"Generated OCTAGON sensor payload hash mismatch: {relative}")
    if gazebo_repo is not None:
        _validate_sources(repo_root, gazebo_repo)
    print(
        f"PASS: SimpleCar robot assets under {robot_target} and OCTAGON sensor "
        f"payload under {sensor_target} are complete"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--gazebo-repo", type=Path, default=Path("/home/pupil/gazebo-sim"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--convert-output", type=Path)
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    gazebo_repo = args.gazebo_repo.resolve()
    if args.convert_output is not None:
        output = args.convert_output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        _convert_assets(_validate_sources(repo_root, gazebo_repo), output)
    elif args.check:
        check(repo_root, gazebo_repo=gazebo_repo)
    else:
        install(repo_root, gazebo_repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
