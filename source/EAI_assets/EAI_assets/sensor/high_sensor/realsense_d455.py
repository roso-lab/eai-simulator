"""Intel RealSense D455 传感器附件（与 Orsus 对齐的实现）。

对齐 Orsus（``source/EAI_assets/EAI_assets/sensor/high_sensor/orsus.py``）的
集成方式：

- USD 资产内置 OmniGraph 发布图（GS_Hub/Orsus 同款模板）：
    ``Graphs/ROS2_publish_RGB``         -> /<robot>/RealsenseD455_rgb        (camera tool)
    ``Graphs/ROS2_publish_Depth``       -> /<robot>/RealsenseD455_depth      (camera tool)
    ``Graphs/ROS2_publish_CameraInfo``  -> /<robot>/RealsenseD455_camera_info (camera tool)
    ``Graphs/ROS2_publish_IMU``         -> /<robot>/RealsenseD455_imu        (ros tool)
- camera tool 与 ros tool 两个开关相互独立：camera 只开关图像图，
  ros 只开关 IMU 图（spawn 时用 prim.SetActive 门控，Orsus 同款做法）。
- 每个机器人实例的 ROS namespace 由 spawn 时覆写各发布节点的
  ``inputs:nodeNamespace`` 得到（与 Orsus 的 ``_apply_orsus_ros_namespace`` 一致）。
- 载荷为纯传感器载荷：资产本身不再自带刚体（spawn 兜底清理旧版资产可能残留的
  RigidBody/Mass/Collision API），避免嵌套刚体干扰宿主机器人，也避免 GPU 流水线
  下 IsaacImuSensor 读取关节链刚体触发 ``PxDirectGPUAPI::getArticulationData``。
- IMU 数据由 ``EAI.hmrs_ros.realsense_d455_imu.RealSenseD455ImuManager`` 在 env
  步进后用机器人根状态合成，写入内嵌 ``ROS2_publish_IMU`` 图（该图已改为
  on_playback_tick 驱动、Python 喂数）；spawn 时经 ``_REALSENSE_INSTANCE_REGISTRY``
  登记每个实例供管理器使用。
"""

import hashlib
import os
import re
from pathlib import Path

from EAI_assets.sensor.high_sensor.orsus import (
    _find_isaac_ros_bridge_path,
    configure_ros_env,
)

# ==============================================================================
# 1. 自动环境配置（与 Orsus 共享同一套逻辑，保证 rmw / bridge 路径一致）
# ==============================================================================
configure_ros_env()

import omni.usd  # noqa: E402
from pxr import Sdf  # noqa: E402
import isaaclab.sim as sim_utils  # noqa: E402
import isaaclab.sim.utils as prim_utils  # noqa: E402
from isaaclab.assets import AssetBaseCfg  # noqa: E402
from isaaclab.utils import configclass  # noqa: E402
from EAI_assets.asset_resolver import asset_path  # noqa: E402

# ==============================================================================
# 2. 资产路径与图结构常量
# ==============================================================================
_REALSENSE_SOURCE_ASSET_PATH = asset_path("payloads/sensors/realsense_d455/rsd455_d455.usd")

# 相机发布图（camera tool 门控）
_REALSENSE_CAMERA_GRAPH_PATHS = (
    "Graphs/ROS2_publish_RGB",
    "Graphs/ROS2_publish_Depth",
    "Graphs/ROS2_publish_CameraInfo",
)
# IMU 发布图（ros tool 门控）
_REALSENSE_IMU_GRAPH_PATHS = (
    "Graphs/ROS2_publish_IMU",
)
# 发布节点的 namespace 输入属性所在节点（相对传感器根）
_REALSENSE_NAMESPACE_NODE_SUFFIXES = (
    "Graphs/ROS2_publish_RGB/ros2_camera_helper",
    "Graphs/ROS2_publish_Depth/ros2_camera_helper",
    "Graphs/ROS2_publish_CameraInfo/ros2_camera_info_helper",
    "Graphs/ROS2_publish_IMU/ros2_publish_imu",
)

# spawn 时登记的实例表：sensor_root_path -> robot_name。
# 供 EAI.hmrs_ros.realsense_d455_imu.RealSenseD455ImuManager 在 env 步进后
# 合成并写入 IMU 数据（载荷不再使用 isaacsim.sensors.physics 的 IsaacImuSensor）。
_REALSENSE_INSTANCE_REGISTRY: dict[str, str] = {}


def _sanitize_ros_name_component(component: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(component).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = f"robot_{cleaned}"
    return cleaned


def _normalize_ros_namespace(namespace: str | None) -> str:
    if namespace is None:
        return ""
    parts = [
        _sanitize_ros_name_component(part)
        for part in str(namespace).strip().strip("/").split("/")
    ]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    return "/" + "/".join(parts)


def _robot_name_from_specific_path(specific_path: str) -> str:
    """从实例 prim 路径推导机器人实例名（/World/envs/env_0/carter_1/... -> carter_1）。"""
    parts = [part for part in str(specific_path).split("/") if part]
    for index, part in enumerate(parts):
        if part == "envs" and index + 2 < len(parts) and parts[index + 1].startswith("env_"):
            return parts[index + 2]
    for index, part in enumerate(parts):
        if re.fullmatch(r"env_\d+", part) and index + 1 < len(parts):
            return parts[index + 1]
    if "World" in parts:
        world_index = parts.index("World")
        if world_index + 1 < len(parts):
            return parts[world_index + 1]
    if parts:
        return parts[0]
    return ""


def _set_node_namespace(stage, node_path: str, namespace: str) -> bool:
    node_prim = stage.GetPrimAtPath(node_path)
    if not node_prim or not node_prim.IsValid():
        return False
    namespace_attr = node_prim.GetAttribute("inputs:nodeNamespace")
    if not namespace_attr:
        return False
    namespace_attr.Set(namespace)
    return True


def _apply_realsense_ros_namespace(stage, sensor_root_path: str, namespace: str) -> int:
    """为实例内所有发布节点覆写 ROS namespace（对齐 Orsus）。"""
    if not namespace:
        return 0
    updated = 0
    for suffix in _REALSENSE_NAMESPACE_NODE_SUFFIXES:
        node_path = f"{sensor_root_path}/{suffix}"
        if _set_node_namespace(stage, node_path, namespace):
            updated += 1
    return updated


def _set_realsense_publish_graphs_active(
    stage,
    sensor_root_path: str,
    graph_paths: tuple[str, ...],
    enabled: bool,
) -> int:
    """激活/去激活一组内置发布图（对齐 Orsus 的 ``_set_orsus_publish_graphs_active``）。"""
    updated = 0
    for rel_path in graph_paths:
        full_path = f"{sensor_root_path}/{rel_path}"
        prim = stage.GetPrimAtPath(full_path)
        if not prim.IsValid():
            continue
        try:
            prim.SetActive(bool(enabled))
            updated += 1
        except Exception as exc:
            action = "enable" if enabled else "disable"
            print(f"[RealsenseD455] ⚠️ Failed to {action} graph {full_path}: {exc}", flush=True)
    return updated


def _disable_realsense_payload_physics(stage, sensor_root_path: str) -> tuple[bool, bool]:
    """剥离载荷残留物理体，使 D455 成为纯传感器载荷（Orsus 同款思路）。

    资产 ``rsd455.usd`` 已不再自带刚体；本函数作为兜底，清理旧版资产可能残留的
    RigidBody / Mass / Collision API。载荷刚性固定在宿主链接上，IsaacImuSensor
    会自动向上找到最近的已启用刚体（宿主链接），随宿主运动输出加速度/角速度。

    注意：不要在载荷 prim 上重新 Apply CollisionAPI —— 那会在 kinematic 刚体上
    生成三角网格碰撞体（approximation None），触发 PhysX ``Parse collision …
    cannot be a part of a dynamic body`` 报错。刚体嵌套（载荷刚体挂在宿主刚体
    下）也不被 PhysX 支持，因此正确的做法是彻底移除载荷刚体。
    """
    # 注意：USD reference 组合后资产内容位于 <sensor_root>/RealsenseD455/ 下
    body_path = f"{sensor_root_path}/RealsenseD455/RSD455"
    body_prim = stage.GetPrimAtPath(body_path)
    if not body_prim or not body_prim.IsValid():
        # 兼容非双层嵌套的组合
        body_path = f"{sensor_root_path}/RSD455"
        body_prim = stage.GetPrimAtPath(body_path)

    if not body_prim or not body_prim.IsValid():
        print(f"[RealsenseD455] ⚠️ Payload body not found at {body_path}", flush=True)
        return False, False

    body_removed = False
    for api_name in ("PhysicsRigidBodyAPI", "PhysxRigidBodyAPI", "PhysicsMassAPI"):
        try:
            if body_prim.HasAPI(api_name) and body_prim.RemoveAPI(api_name):
                body_removed = True
        except Exception as exc:
            print(f"[RealsenseD455] ⚠️ Failed to remove {api_name}: {exc}", flush=True)

    collision_removed = False
    for api_name in ("PhysicsCollisionAPI", "PhysicsMeshCollisionAPI", "PhysxCollisionAPI"):
        try:
            if body_prim.HasAPI(api_name) and body_prim.RemoveAPI(api_name):
                collision_removed = True
        except Exception as exc:
            print(f"[RealsenseD455] ⚠️ Failed to remove {api_name}: {exc}", flush=True)

    return body_removed, collision_removed


def spawn_realsense_d455(prim_path, cfg, translation, orientation):
    """自定义生成回调（对齐 Orsus 的 ``spawn_and_fix_orsus``）：

    1. 加载 USD 资产；
    2. 展开 ``{ENV_REGEX_NS}`` 匹配到的每个实例；
    3. 按 tool 门控激活/去激活内置发布图；
    4. 覆写每个实例的 ROS namespace；
    5. 剥离载荷残留刚体/碰撞，使 D455 成为纯传感器载荷。
    """
    _REALSENSE_INSTANCE_REGISTRY.clear()

    sim_utils.spawn_from_usd(
        prim_path,
        cfg,
        translation,
        orientation,
    )

    target_prim_name = prim_path.split("/")[-1]  # "RealsenseD455"
    parent_regex = prim_path.rpartition("/")[0]

    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    if not matched_parents:
        print(f"[RealsenseD455] ⚠️ No parents found matching: {parent_regex}")
        resolved_paths = [prim_path]
    else:
        resolved_paths = [f"{p}/{target_prim_name}" for p in matched_parents]

    print(f"[RealsenseD455] ⚙️ Spawning & fixing for {len(resolved_paths)} instances...", flush=True)
    stage = omni.usd.get_context().get_stage()

    enable_camera_publish = bool(getattr(cfg, "enable_camera_publish", True))
    enable_imu_publish = bool(getattr(cfg, "enable_imu_publish", True))
    disable_physics = bool(getattr(cfg, "disable_physics", True))

    for specific_path in resolved_paths:
        if enable_imu_publish:
            robot_name = _robot_name_from_specific_path(specific_path)
            _REALSENSE_INSTANCE_REGISTRY[specific_path] = robot_name

        if disable_physics:
            body_removed, collision_removed = _disable_realsense_payload_physics(
                stage, specific_path
            )
            print(
                "[RealsenseD455] Payload physics: "
                f"body_stripped={body_removed}, collision_stripped={collision_removed} ({specific_path})",
                flush=True,
            )

        camera_graph_count = _set_realsense_publish_graphs_active(
            stage,
            specific_path,
            _REALSENSE_CAMERA_GRAPH_PATHS,
            enable_camera_publish,
        )
        imu_graph_count = _set_realsense_publish_graphs_active(
            stage,
            specific_path,
            _REALSENSE_IMU_GRAPH_PATHS,
            enable_imu_publish,
        )


        if enable_camera_publish or enable_imu_publish:
            namespace = _normalize_ros_namespace(
                getattr(cfg, "ros_namespace", None)
                or _robot_name_from_specific_path(specific_path)
            )
            updated_count = _apply_realsense_ros_namespace(stage, specific_path, namespace)
            if updated_count:
                print(
                    f"[RealsenseD455] ✅ ROS namespace: {namespace} ({updated_count} publish nodes)",
                    flush=True,
                )
            elif namespace:
                print(
                    f"[RealsenseD455] ⚠️ ROS namespace not applied for {specific_path}: {namespace}",
                    flush=True,
                )

        print(
            "[RealsenseD455] Publisher graphs: "
            f"camera={'on' if enable_camera_publish else 'off'} ({camera_graph_count}), "
            f"imu={'on' if enable_imu_publish else 'off'} ({imu_graph_count})",
            flush=True,
        )


@configclass
class RealSenseD455SpawnCfg(sim_utils.UsdFileCfg):
    ros_namespace: str | None = None
    enable_camera_publish: bool = True   # camera tool 门控：rgb/depth/camera_info
    enable_imu_publish: bool = True      # ros tool 门控：imu
    disable_physics: bool = True


@configclass
class RealSenseD455Cfg(AssetBaseCfg):
    ros_namespace: str | None = None
    enable_camera_publish: bool = True
    enable_imu_publish: bool = True
    disable_physics: bool = True
    spawn = RealSenseD455SpawnCfg(
        usd_path=_REALSENSE_SOURCE_ASSET_PATH,
        func=spawn_realsense_d455,
    )
    asset_dependencies = (_REALSENSE_SOURCE_ASSET_PATH,)

    def __post_init__(self) -> None:
        if not isinstance(self.spawn, RealSenseD455SpawnCfg):
            return
        self.spawn.ros_namespace = self.ros_namespace
        self.spawn.enable_camera_publish = self.enable_camera_publish
        self.spawn.enable_imu_publish = self.enable_imu_publish
        self.spawn.disable_physics = self.disable_physics
