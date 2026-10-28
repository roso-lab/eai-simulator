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
    ("desert", "Desert"),
    ("hospital", "Hospital"),
    ("rmuc2024", "RMUC 2024"),
    ("warehouse_nvd", "Warehouse NVD benchmark overlay"),
    ("modern_house_nvd", "Modern House NVD benchmark overlay"),
    ("city", "City (local benchmark)"),
    ("restaurant", "Restaurant authoring scene"),
    ("restaurant_benchmark", "Restaurant benchmark candidate overlay"),
    ("factory_benchmark", "Factory (local benchmark)"),
    ("hssd_room1", "HSSD Room 1 (converted, local)"),
    ("room_06", "Room 06 multi-level benchmark"),
)

ROBOT_KEYS = (
    "carter",
    "simple_car",
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
    "microduck",
)

ROBOT_LABELS = {
    "carter": "Carter differential base",
    "simple_car": "SimpleCar Gazebo planar base",
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
    "microduck": "MicroDuck biped (XL330)",
}

TOOL_LABELS = {
    "camera": "Camera",
    "navigation_io": "Navigation I/O",
    "keyboard": "Keyboard",
}

TOOL_ASSET_NAMES = {
    "camera": "camera",
    "navigation_io": "navigation_io",
    "keyboard": "keyboard",
}

# Robots carrying a native camera suite. The Camera tool gates publication.
BUILTIN_CAMERA_ROBOTS = frozenset({"cf2x", "iris", "pegasus", "simple_car"})

# Only aerial robots use detachable-payload names as aliases for their built-in
# sensor suite. SimpleCar has its own C01/dual-camera contract and must not silently
# masquerade as Orsus, RealSense, or a generic LiDAR.
BUILTIN_SENSOR_ROBOTS = frozenset({"cf2x", "iris", "pegasus"})
BUILTIN_SENSOR_CAPABILITIES = {
    "orsus": ("camera", "lidar"),
    "realsense_d455": ("camera",),
    "lidar": ("lidar",),
}

# PhysX launch tests show that these otherwise host-compatible payload pairs
# overlap or destabilize the host articulation when mounted together.
PAYLOAD_MOUNT_CONFLICTS = (
    ("carter", "z1", "orsus"),
    ("carter", "z1", "lidar"),
    ("carter", "z1", "octagon_aio_c01"),
    ("carter", "ur5", "orsus"),
    ("carter", "ur5", "lidar"),
    ("carter", "ur5", "octagon_aio_c01"),
    ("go2", "ur5", "orsus"),
    ("go2", "z1", "orsus"),
    ("go2", "d1", "z1"),
    ("go2", "d1", "ur5"),
    ("carter", "d1", "z1"),
    ("carter", "d1", "ur5"),
    ("b2", "d1", "z1"),
    ("b2", "d1", "ur5"),
    ("m20", "d1", "z1"),
    ("m20", "d1", "ur5"),
    ("scout", "d1", "z1"),
    ("scout", "d1", "ur5"),
    ("lite3", "d1", "z1"),
    ("lite3", "d1", "ur5"),
)

# Attachment types that occupy the single manipulator slot of a host robot.
MANIPULATOR_TYPES = ("ur5", "ur5_bare", "z1", "d1")

# Results from the unscaled UR5 + Robotiq 2F-85 stationary mount matrix.
# ``conditional`` means the evidence is limited to the stationary fixture.
UR5_DEPLOYMENT_STATUS = {
    "carter": ("rejected", "21.9 kg 顶部质量＋0.414 m 挂点力臂在初始沉降瞬态内即把速度阻尼轮小车掀翻弹飞（0.25 s 内跌落 0.19 m、倾至 0.30 rad，漂移 1.8 m）；速度模式车轮无恢复力矩，当前挂载与轮控制组合不可用。"),
    "scout": ("conditional", "未缩放静止姿态与平均对地反力通过；瞬时接触支撑包络仍有缺失/越界，未验证倾覆裕度或行走操作。"),
    "b2": ("conditional", "已在静止专用 PD 下通过未缩放摆臂和对地支撑检查；未验证默认行走控制器或行走中操作。"),
    "go2": ("rejected", "未缩放 UR5 在静止测试中倾覆，当前挂载位置和控制不能稳定。"),
    "m20": ("rejected", "未缩放 UR5 在静止测试中失稳，当前挂载位置和控制不能稳定。"),
    "lite3": ("rejected", "裸载对照为稳定的内在倾斜姿态（恒定 18°、反力为体重 82%）；装载 UR5 后完全翻倒（对地反力 0）。差分明确指向 21.9 kg 顶部载荷；裸载基线未达门禁，结论限于当前挂载位置。"),
}

Z1_DEPLOYMENT_STATUS = {
    "scout": ("conditional", "仅完成未缩放静止摆臂测试，尚未验证行走中操作。"),
    "b2": ("conditional", "仅完成未缩放静止摆臂测试，尚未验证行走中操作。"),
    "go2": ("conditional", "仅完成未缩放静止摆臂测试，尚未验证行走中操作。"),
    "m20": ("conditional", "未缩放 Z1 静止摆臂、对地反力与漂移门禁通过；未验证行走中操作。"),
    "carter": ("conditional", "未缩放 Z1 静止摆臂、对地反力与漂移门禁通过；宿主 54 kg 为 USD 声明值且 40 kg 集中在两驱动轮（分布偏乐观），未验证行走中操作。"),
    "lite3": ("rejected", "裸载对照为稳定的内在倾斜姿态（恒定 18°、前后足受力 2/46 N 严重偏载、反力为体重 82%）；装载 Z1 后同一 fixture 下倾角增加 77°、对地反力降为 0（完全翻倒）。差分明确指向 5.2 kg 顶部载荷；但裸载基线未达门禁，结论限于当前挂载位置。"),
}

_DEFAULT_CONTROLLER_CFG = {
    "carter": "CARTER_DIFF_CFG",
    "simple_car": "SIMPLE_CAR_PLANAR_CFG",
    "pepper": "PEPPER_PICK_PLACE_CFG",
    "go2": "GO2_MIXED_STAIRS_CFG",
    "b2": "B2_MIXED_STAIRS_CFG",
    "m20": "M20_ROUGH_RSL_CFG",
    "scout": "SCOUT_DIFF_CFG",
    "mushr_v2": "MUSHR_ACKERMANN_CFG",
    "coco": "COCO_ACKERMANN_CFG",
    "g1": "G1_DWAQ_CFG",
    "cf2x": "QUADCOPTER_GOAL_SKRL_CFG",
    "iris": "PEGASUS_IRIS_POSITION_CFG",
    "pegasus": "PEGASUS_X4_POSITION_CFG",
    "lite3": "LITE3_VELOCITY_RSL_CFG",
    # MicroDuck 裸足式机器人：不进入任何挂件宿主集合（无传感器/机械臂挂载）。
    "microduck": "MICRODUCK_VELSTAND_CFG",
}

_CONTROLLER_CFG_NAMES = (
    "CARTER_DIFF_CFG",
    "SIMPLE_CAR_PLANAR_CFG",
    "GO2_MIXED_STAIRS_CFG",
    "GO2_EXTREME_PARKOUR_TEACHER_CFG",
    "B2_VELOCITY_RSL_CFG",
    "B2_MIXED_STAIRS_CFG",
    "M20_ROUGH_RSL_CFG",
    "M20_NAV_RSL_CFG",
    "SCOUT_DIFF_CFG",
    "MUSHR_ACKERMANN_CFG",
    "MUSHR_RWD_ACKERMANN_CFG",
    "COCO_ACKERMANN_CFG",
    "G1_DWAQ_CFG",
    "PEPPER_PICK_PLACE_CFG",
    "QUADCOPTER_GOAL_SKRL_CFG",
    "PEGASUS_IRIS_POSITION_CFG",
    "PEGASUS_IRIS_ROTOR_CFG",
    "PEGASUS_X4_POSITION_CFG",
    "PEGASUS_X4_ROTOR_CFG",
    "LITE3_VELOCITY_RSL_CFG",
    "UR5_IK_CFG",
    "Z1_IK_CFG",
    "D1_IK_CFG",
    "MICRODUCK_VELSTAND_CFG",
)

_CONTROLLER_CFG_ROBOTS = {
    "M20_NAV_RSL_CFG": ("m20",),
    "GO2_MIXED_STAIRS_CFG": ("go2",),
    "GO2_EXTREME_PARKOUR_TEACHER_CFG": ("go2",),
    "B2_VELOCITY_RSL_CFG": ("b2",),
    "B2_MIXED_STAIRS_CFG": ("b2",),
}


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


def tool_label(tool_type: str) -> str:
    key = str(tool_type).strip().lower()
    return TOOL_LABELS.get(key, str(tool_type))


def tool_asset_name(tool_type: str) -> str:
    key = str(tool_type).strip().lower()
    return TOOL_ASSET_NAMES.get(key, str(tool_type))


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


def controller_cfgs_for_robot(robot_type: str) -> tuple[str, ...]:
    """Return the registered motion-controller choices for one robot type."""
    key = canonical_robot_type(robot_type)
    default = default_controller_cfg(key)
    choices = [default]
    choices.extend(
        cfg for cfg, supported in _CONTROLLER_CFG_ROBOTS.items()
        if key in supported and cfg != default
    )
    return tuple(choices)


def _attachment_entries() -> tuple[AttachmentCatalogEntry, ...]:
    orsus_hosts = (
        "carter", "go2", "b2", "m20", "scout", "coco", "lite3",
    )
    # Keep terminal and 3D authoring aligned with the option 1 visual catalog.
    lidar_hosts = (
        "carter", "go2", "b2", "m20", "scout", "mushr_v2", "coco", "lite3",
    )
    # HeteroCoord treats UR5 + Robotiq 2F-85 as a standalone robot. Mounted
    # UR5 variants are excluded from every host robot's formal attachment pool.
    ur5_hosts: tuple[str, ...] = ()
    # 2026-10-22 装配政策：go2 与 lite3 仅可装 D1（不再放行 Z1；lite3 的 Z1
    # 物理筛查结论保留在 Z1_DEPLOYMENT_STATUS 作为历史证据）；其余原 Z1 宿主
    # 在保留 Z1 资格的同时放开 D1（单机械臂槽互斥）。
    z1_hosts = ("carter", "b2", "m20", "scout")
    # D1 目前仅 go2 有已验证的挂载外参；carter/b2/m20/scout 复用各自 Z1 挂点，
    # lite3 按 TORSO 背部高度取值，其余宿主待物理筛选后再放开。
    d1_hosts = ("carter", "go2", "b2", "m20", "scout", "lite3")
    # RealSense D455 与 Orsus 一样是可解耦装载的传感器载荷；
    # pepper 头顶装载作为首轮验证宿主，其余沿用 Orsus 兼容宿主集合。
    realsense_d455_hosts = ("pepper", "mushr_v2", "g1") + orsus_hosts
    return (
        AttachmentCatalogEntry(
            name="orsus",
            asset_cfg="OrsusCfg",
            controller_cfg=None,
            supported_robots=orsus_hosts,
            category="sensor",
        ),
        AttachmentCatalogEntry(
            name="realsense_d455",
            asset_cfg="RealSenseD455Cfg",
            controller_cfg=None,
            supported_robots=realsense_d455_hosts,
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
            # C01 与独立雷达载荷同挂载体系：宿主集合沿用 lidar_hosts，
            # builder 在 lidar_mount_link/lidar_offset 处 spawn OctagonAioC01。
            name="octagon_aio_c01",
            asset_cfg="OctagonAioC01Cfg",
            controller_cfg=None,
            supported_robots=lidar_hosts,
            category="sensor",
        ),
        AttachmentCatalogEntry(
            # simple_car 内置传感器套件的 Mid-360 变体声明：运行时由
            # simple_car_sensor_suite 直接创建 RTX lidar，无需 spawn 场景资产；
            # 不声明则默认 octagon_aio_c01。EAI_SIMPLE_CAR_LIDAR_VARIANT
            # 环境变量仍作兼容回退，但 attachment 声明优先。
            name="mid360",
            asset_cfg=None,
            controller_cfg=None,
            supported_robots=("simple_car",),
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
            name="ur5_bare",
            asset_cfg="MountedUr5BareArmCfg",
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
        AttachmentCatalogEntry(
            name="d1",
            asset_cfg="MountedD1ArmCfg",
            controller_cfg="D1_IK_CFG",
            supported_robots=d1_hosts,
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
                "carter", "simple_car", "go2", "b2", "m20", "scout", "mushr_v2", "coco", "lite3",
                "cf2x", "iris", "pegasus", "pepper", "g1",
            ),
            category="tool",
        ),
        "navigation_io": AttachmentCatalogEntry(
            name="navigation_io",
            asset_cfg=None,
            controller_cfg=None,
            supported_robots=(
                "carter", "simple_car", "go2", "b2", "m20", "scout", "mushr_v2",
                "coco", "lite3", "pepper", "g1", "cf2x", "iris", "pegasus",
            ),
            category="tool",
        ),
        "keyboard": AttachmentCatalogEntry(
            name="keyboard",
            asset_cfg=None,
            controller_cfg=None,
            supported_robots=("carter", "simple_car", "go2", "b2", "m20", "scout", "mushr_v2", "coco", "pepper", "g1", "cf2x", "iris", "pegasus", "lite3"),
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


def manipulator_deployment_status(robot_type: str, attachment_type: str) -> tuple[str, str] | None:
    """Return the evidence status for a screened selectable or retained candidate mount."""
    key = str(attachment_type).strip().lower()
    if key == "z1":
        return Z1_DEPLOYMENT_STATUS.get(canonical_robot_type(robot_type),
                                         ("unknown", "缺少该宿主的未缩放质量、重心和动力学验证。"))
    if key not in {"ur5", "ur5_bare"}:
        return None
    return UR5_DEPLOYMENT_STATUS.get(canonical_robot_type(robot_type),
                                     ("unknown", "缺少该宿主的未缩放质量、重心和动力学验证。"))


def builtin_sensor_capabilities(
    robot_type: str,
    attachment_type: str,
) -> tuple[str, ...]:
    """Describe built-in aerial capabilities replacing an unavailable payload."""
    host = canonical_robot_type(robot_type)
    if host not in BUILTIN_SENSOR_ROBOTS:
        return ()
    return BUILTIN_SENSOR_CAPABILITIES.get(
        str(attachment_type).strip().lower(),
        (),
    )


def payload_mount_conflict(
    robot_type: str,
    attachment_types: list[str] | tuple[str, ...] | set[str],
) -> tuple[str, str] | None:
    """Return the first physics-incompatible manipulator/sensor pair, if any."""
    host = canonical_robot_type(robot_type)
    normalized = {str(item).strip().lower() for item in attachment_types}
    if "ur5_bare" in normalized:
        normalized.add("ur5")
    for conflict_host, manipulator, sensor in PAYLOAD_MOUNT_CONFLICTS:
        if host == conflict_host and {manipulator, sensor} <= normalized:
            return manipulator, sensor
    return None


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
        if attachment_type in MANIPULATOR_TYPES:
            if manipulator is not None and manipulator != attachment_type:
                raise ValueError(f"Robot '{host}' cannot attach more than one manipulator.")
            manipulator = attachment_type
        if attachment_type not in normalized:
            normalized.append(attachment_type)
    if "orsus" in normalized and "lidar" in normalized:
        raise ValueError(f"Robot '{host}' cannot attach both Orsus and LiDAR.")
    for other_type, other_label in (("orsus", "Orsus"), ("lidar", "LiDAR")):
        if "octagon_aio_c01" in normalized and other_type in normalized:
            raise ValueError(
                f"Robot '{host}' cannot attach both OCTAGON-AIO-C01 and {other_label}."
            )
        mount_conflict = payload_mount_conflict(host, normalized)
        if mount_conflict is not None:
            manipulator, sensor = mount_conflict
            display_names = {
                "ur5": "UR5",
                "z1": "Z1",
                "d1": "D1",
                "orsus": "Orsus",
                "lidar": "LiDAR",
                "octagon_aio_c01": "OCTAGON-AIO-C01",
            }
            raise ValueError(
                f"Robot '{host}' cannot attach both "
                f"{display_names[manipulator]} and {display_names[sensor]}."
            )
    for manipulator in MANIPULATOR_TYPES:
        if manipulator in normalized:
            status = manipulator_deployment_status(host, manipulator)
            if status is not None and status[0] in {"rejected", "unknown"}:
                raise ValueError(
                    f"{manipulator.upper()} on '{host}' is {status[0]} by the physical screen: {status[1]}"
                )
    if "camera" in normalized and host not in BUILTIN_CAMERA_ROBOTS and not (
        "orsus" in normalized or "realsense_d455" in normalized
    ):
        raise ValueError(
            f"Camera tool on robot '{host}' requires the Orsus or RealSense D455 payload."
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
