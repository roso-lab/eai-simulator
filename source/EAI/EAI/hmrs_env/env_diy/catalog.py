"""Pure Env DIY catalog and validation helpers.

This module is intentionally independent from Tk, Isaac Sim, and Isaac Lab so
that terminal, lightweight window, and Isaac extension frontends share the
same selection vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping


SCENE_CHOICES = (
    ("plane", "Plane flat ground"),
    ("warehouse", "Warehouse"),
    ("factory", "Factory"),
    ("airs", "AIRS scene"),
    ("garden", "Garden"),
    ("desert", "Desert"),
    ("hospital", "Hospital"),
)

ROBOT_KEYS = (
    "carter",
    "pepper",
    "go2",
    "b2",
    "m20",
    "scout",
    "mushr_v2",
    "g1",
    "cf2x",
    "iris",
    "pegasus",
    "lite3",
    "coco",
)

ROBOT_LABELS = {
    "carter": "Carter differential base",
    "pepper": "Pepper holonomic base",
    "go2": "Unitree Go2",
    "b2": "Unitree B2",
    "m20": "DeepRobotics M20",
    "scout": "Scout mobile base",
    "mushr_v2": "MuSHR Nano v2 Ackermann base",
    "coco": "Coco AIRS Ackermann base",
    "g1": "Unitree G1",
    "cf2x": "Crazyflie CF2X",
    "iris": "Pegasus 3DR Iris",
    "pegasus": "Pegasus research quadrotor",
    "lite3": "DeepRobotics Lite3",
}

# Robots that carry a built-in monocular camera, so the Camera tool does not
# require a Orsus stereo payload. Aerial robots publish through the aerial
# sensor suite; MuSHR publishes through its own front-facing camera.
BUILTIN_CAMERA_ROBOTS = frozenset({"cf2x", "iris", "pegasus", "mushr_v2"})

_DEFAULT_CONTROLLER_CFG = {
    "carter": "CARTER_DIFF_CFG",
    "pepper": "PEPPER_HOLONOMIC_CFG",
    "go2": "GO2_VELOCITY_RSL_CFG",
    "b2": "B2_VELOCITY_RSL_CFG",
    "m20": "M20_ROUGH_RSL_CFG",
    "scout": "SCOUT_DIFF_CFG",
    "mushr_v2": "MUSHR_ACKERMANN_CFG",
    "coco": "COCO_ACKERMANN_CFG",
    "g1": "G1_SKRL_CFG",
    "cf2x": "QUADCOPTER_GOAL_SKRL_CFG",
    "iris": "PEGASUS_IRIS_POSITION_CFG",
    "pegasus": "PEGASUS_X4_POSITION_CFG",
    "lite3": "LITE3_VELOCITY_RSL_CFG",
}

_CONTROLLER_CFG_NAMES = (
    "CARTER_DIFF_CFG",
    "PEPPER_HOLONOMIC_CFG",
    "GO2_VELOCITY_RSL_CFG",
    "B2_VELOCITY_RSL_CFG",
    "M20_ROUGH_RSL_CFG",
    "SCOUT_DIFF_CFG",
    "MUSHR_ACKERMANN_CFG",
    "MUSHR_RWD_ACKERMANN_CFG",
    "COCO_ACKERMANN_CFG",
    "G1_SKRL_CFG",
    "QUADCOPTER_GOAL_SKRL_CFG",
    "PEGASUS_IRIS_POSITION_CFG",
    "PEGASUS_IRIS_ROTOR_CFG",
    "PEGASUS_X4_POSITION_CFG",
    "PEGASUS_X4_ROTOR_CFG",
    "LITE3_VELOCITY_RSL_CFG",
    "UR5_IK_CFG",
    "Z1_IK_CFG",
)


@dataclass(frozen=True)
class AttachmentCatalogEntry:
    name: str
    asset_cfg: str | None
    controller_cfg: str | None
    supported_robots: tuple[str, ...]
    asset_cfg_by_robot: dict[str, str] = field(default_factory=dict)
    robot_asset_variant_by_robot: dict[str, str] = field(default_factory=dict)
    visual_only: bool = False
    category: str = "sensor"

    def supports(self, robot_type: str) -> bool:
        return canonical_robot_type(robot_type) in self.supported_robots

    def asset_cfg_for(self, robot_type: str) -> str | None:
        return self.asset_cfg_by_robot.get(canonical_robot_type(robot_type), self.asset_cfg)

    def robot_asset_variant_for(self, robot_type: str) -> str | None:
        return self.robot_asset_variant_by_robot.get(canonical_robot_type(robot_type))


@dataclass(frozen=True)
class RobotCatalogEntry:
    name: str
    default_controller_cfg: str | None


def canonical_robot_type(robot_type: str) -> str:
    value = str(robot_type).strip()
    if value.lower() == "m20":
        return "m20"
    return value.lower()


def scene_choices() -> tuple[tuple[str, str], ...]:
    return SCENE_CHOICES


def robot_keys() -> tuple[str, ...]:
    return ROBOT_KEYS


def robot_label(robot_type: str) -> str:
    return ROBOT_LABELS.get(canonical_robot_type(robot_type), str(robot_type))


def robot_catalog() -> dict[str, RobotCatalogEntry]:
    entries = {
        key: RobotCatalogEntry(key, _DEFAULT_CONTROLLER_CFG.get(key))
        for key in ROBOT_KEYS
    }
    # Keep the window's historical display spelling as a compatibility alias.
    entries["M20"] = entries["m20"]
    return entries


def default_controller_cfg(robot_type: str) -> str:
    key = canonical_robot_type(robot_type)
    try:
        return _DEFAULT_CONTROLLER_CFG[key]
    except KeyError as exc:
        raise ValueError(f"Unknown robot type '{robot_type}'.") from exc


def controller_cfg_names() -> tuple[str, ...]:
    return _CONTROLLER_CFG_NAMES


def _attachment_entries() -> tuple[AttachmentCatalogEntry, ...]:
    orsus_hosts = (
        "carter", "go2", "b2", "m20", "scout", "coco", "lite3",
    )
    lidar_hosts = ("carter", "go2", "b2", "m20", "scout", "mushr_v2", "coco", "lite3")
    ur5_hosts = ("go2", "b2", "m20", "scout", "lite3")
    z1_hosts = ("carter", "go2", "b2", "m20", "scout", "lite3")
    return (
        AttachmentCatalogEntry(
            name="orsus",
            asset_cfg="OrsusCfg",
            controller_cfg=None,
            supported_robots=orsus_hosts,
            category="sensor",
        ),
        AttachmentCatalogEntry(
            name="lidar",
            asset_cfg="RosLidarCfg",
            controller_cfg=None,
            supported_robots=lidar_hosts,
            category="sensor",
        ),
        AttachmentCatalogEntry(
            name="ur5",
            asset_cfg="MountedUr5ArmCfg",
            controller_cfg="UR5_IK_CFG",
            supported_robots=ur5_hosts,
            category="manipulator",
        ),
        AttachmentCatalogEntry(
            name="z1",
            asset_cfg="MountedZ1ArmCfg",
            controller_cfg="Z1_IK_CFG",
            supported_robots=z1_hosts,
            category="manipulator",
        ),
    )


def attachment_catalog() -> dict[str, AttachmentCatalogEntry]:
    return {entry.name: entry for entry in _attachment_entries()}


def tool_catalog() -> dict[str, AttachmentCatalogEntry]:
    return {
        "camera": AttachmentCatalogEntry(
            name="camera",
            asset_cfg=None,
            controller_cfg=None,
            supported_robots=(
                "carter", "go2", "b2", "m20", "scout", "mushr_v2", "coco", "lite3",
                "cf2x", "iris", "pegasus",
            ),
            category="tool",
        ),
        "ros": AttachmentCatalogEntry(
            name="ros",
            asset_cfg=None,
            controller_cfg=None,
            supported_robots=(
                "carter", "go2", "b2", "m20", "scout", "mushr_v2",
                "coco", "lite3", "pepper", "g1", "cf2x", "iris", "pegasus",
            ),
            category="tool",
        ),
        "keyboard": AttachmentCatalogEntry(
            name="keyboard",
            asset_cfg=None,
            controller_cfg=None,
            supported_robots=("carter", "go2", "b2", "m20", "scout", "mushr_v2", "coco", "pepper", "g1", "cf2x", "iris", "pegasus", "lite3"),
            category="tool",
        ),
    }


def attachment_entry(attachment_type: str) -> AttachmentCatalogEntry:
    key = str(attachment_type).strip().lower()
    entry = attachment_catalog().get(key) or tool_catalog().get(key)
    if entry is not None:
        return entry
    return AttachmentCatalogEntry(
        name=key,
        asset_cfg=None,
        controller_cfg=None,
        supported_robots=ROBOT_KEYS,
        visual_only=True,
        category="sensor",
    )


def attachment_supported(robot_type: str, attachment_type: str) -> bool:
    return attachment_entry(attachment_type).supports(robot_type) and not attachment_entry(attachment_type).visual_only


def validate_attachment_types(robot_type: str, attachment_types: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and validate payload/tool names for one host robot."""
    host = canonical_robot_type(robot_type)
    if host not in ROBOT_KEYS:
        raise ValueError(f"Unknown robot type '{robot_type}'.")
    normalized: list[str] = []
    manipulator: str | None = None
    for raw_type in attachment_types:
        attachment_type = str(raw_type).strip().lower()
        entry = attachment_entry(attachment_type)
        if entry.visual_only or not entry.supports(host):
            raise ValueError(f"Attachment '{attachment_type}' is not supported by robot '{host}'.")
        if attachment_type in {"ur5", "z1"}:
            if manipulator is not None and manipulator != attachment_type:
                raise ValueError(f"Robot '{host}' cannot attach both UR5 and Z1.")
            manipulator = attachment_type
        if attachment_type not in normalized:
            normalized.append(attachment_type)
    if "camera" in normalized and host not in BUILTIN_CAMERA_ROBOTS and "orsus" not in normalized:
        raise ValueError(
            f"Camera tool on robot '{host}' requires the Orsus payload."
        )
    return tuple(normalized)


def normalize_spawn_pose(value: Mapping[str, Any] | None) -> dict[str, tuple[float, ...]] | None:
    """Validate and normalize a physical pose; ``None`` keeps legacy defaults."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("spawn_pose must be an object.")
    if "position" not in value:
        raise ValueError("spawn_pose.position is required.")
    if "rotation" not in value:
        raise ValueError("spawn_pose.rotation is required.")
    position = _finite_float_vector(value["position"], 3, "spawn_pose.position")
    rotation = _finite_float_vector(value["rotation"], 4, "spawn_pose.rotation")
    norm = math.sqrt(sum(component * component for component in rotation))
    if norm == 0.0:
        raise ValueError("spawn_pose.rotation quaternion cannot be zero.")
    rotation = tuple(component / norm for component in rotation)
    return {"position": position, "rotation": rotation}


def spawn_pose_to_dict(value: Mapping[str, Any] | None) -> dict[str, list[float]] | None:
    normalized = normalize_spawn_pose(value)
    if normalized is None:
        return None
    return {key: [float(item) for item in vector] for key, vector in normalized.items()}


def _finite_float_vector(value: Any, length: int, field_name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must contain exactly {length} values.")
    try:
        items = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain numeric values.") from exc
    if len(items) != length:
        raise ValueError(f"{field_name} must contain exactly {length} values.")
    if not all(math.isfinite(item) for item in items):
        raise ValueError(f"{field_name} must contain finite values.")
    return items
