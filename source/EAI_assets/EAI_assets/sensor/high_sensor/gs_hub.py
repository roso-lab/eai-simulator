import os
import re
import sys
import site


def _find_isaac_ros_bridge_path(ros_distro: str = "humble"):
    existing = os.environ.get("ISAAC_ROS_PATH")
    if existing and os.path.exists(existing):
        return existing

    relative = os.path.join("exts", "isaacsim.ros2.bridge", ros_distro)
    roots = []
    env_root = os.environ.get("EAI_ISAACSIM_ROOT") or os.environ.get("ISAACSIM_ROOT")
    if env_root:
        roots.append(env_root)
    roots.extend(
        [
            os.path.expanduser("~/isaacsim"),
            os.path.expanduser("~/IsaacSim"),
            os.path.expanduser("~/isaac-sim"),
            os.path.expanduser("~/isaacsim-6.0.1"),
        ]
    )
    for root in roots:
        for candidate in (
            os.path.join(root, "_build", "linux-x86_64", "release", relative),
            os.path.join(root, relative),
        ):
            if os.path.exists(candidate):
                return candidate

    target_suffix = os.path.join("isaacsim", "exts", "isaacsim.ros2.bridge", ros_distro)
    search_paths = site.getsitepackages() + [site.getusersitepackages()]
    for path in search_paths:
        candidate = os.path.join(path, target_suffix)
        if os.path.exists(candidate):
            return candidate

    return None


# ==============================================================================
# 1. 自动环境配置逻辑 (替代 .sh 脚本)
# 必须放在所有 isaaclab/omni/ros 导入之前执行
# ==============================================================================
def configure_ros_env():
    """
    自动查找 Isaac Sim ROS2 Bridge 路径并配置环境变量。
    """
    if os.environ.get("EAI_DISABLE_GSHUB_ROS_ENV", "").strip().casefold() in {"1", "true", "yes", "on"}:
        return None

    # 1. 设置基础变量
    os.environ["ROS_DISTRO"] = "humble"
    os.environ["RMW_IMPLEMENTATION"] = "rmw_fastrtps_cpp"

    # 2. 动态搜索路径 (复用之前的逻辑)
    isaac_ros_path = _find_isaac_ros_bridge_path(os.environ["ROS_DISTRO"])

    if not isaac_ros_path:
        print("[EnvSetup] ⚠️ Warning: Could not find Isaac ROS Bridge path automatically.")
        return

    print(f"[EnvSetup] ✅ Found Isaac ROS Path: {isaac_ros_path}")

    # 3. 设置 ISAAC_ROS_PATH
    os.environ["ISAAC_ROS_PATH"] = isaac_ros_path

    # 4. 更新 LD_LIBRARY_PATH
    # 注意：在 Python 内部修改 LD_LIBRARY_PATH 对当前进程已加载的库无效，
    # 但 Isaac Sim 的扩展加载器通常会读取 os.environ，所以这步依然有用。
    lib_path = os.path.join(isaac_ros_path, "lib")
    current_ld = os.environ.get("LD_LIBRARY_PATH", "")
    if lib_path not in current_ld:
        os.environ["LD_LIBRARY_PATH"] = f"{current_ld}:{lib_path}" if current_ld else lib_path

    # 5. 更新 AMENT_PREFIX_PATH
    current_ament = os.environ.get("AMENT_PREFIX_PATH", "")
    if isaac_ros_path not in current_ament:
        os.environ["AMENT_PREFIX_PATH"] = f"{current_ament}:{isaac_ros_path}" if current_ament else isaac_ros_path


_GSHUB_ROS_NAMESPACE_NODE_SUFFIXES = (
    "GS_Hub/Graphs/ROS2_publish_L_cam/ros2_camera_helper",
    "GS_Hub/Graphs/ROS2_publish_R_cam/ros2_camera_helper",
    "GS_Hub/Graphs/ROS2_publish_Lidar_Odom/ros2_publish_point_cloud",
    "GS_Hub/Graphs/ROS2_publish_Lidar_Odom/ros2_publish_odometry",
)


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


def _robot_name_from_gshub_path(specific_path: str) -> str:
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


def _gshub_ros_namespace_for_instance(cfg, specific_path: str) -> str:
    explicit_namespace = getattr(cfg, "ros_namespace", None)
    if explicit_namespace:
        return _normalize_ros_namespace(explicit_namespace)
    return _normalize_ros_namespace(_robot_name_from_gshub_path(specific_path))


def _set_node_namespace(stage, node_path: str, namespace: str) -> bool:
    node_prim = stage.GetPrimAtPath(node_path)
    if not node_prim or not node_prim.IsValid():
        return False
    namespace_attr = node_prim.GetAttribute("inputs:nodeNamespace")
    if not namespace_attr:
        return False
    namespace_attr.Set(namespace)
    return True


def _apply_gshub_ros_namespace(stage, specific_path: str, namespace: str) -> int:
    if not namespace:
        return 0
    updated = 0
    for suffix in _GSHUB_ROS_NAMESPACE_NODE_SUFFIXES:
        node_path = f"{specific_path}/{suffix}"
        if _set_node_namespace(stage, node_path, namespace):
            updated += 1
    return updated


def _disable_gshub_ros_publishers(stage, gshub_root_path: str) -> int:
    """禁用 GSHub 的 ROS2 publish OmniGraph 节点。

    方法：直接禁用（SetActive False）或删除 Graph prim，彻底阻止节点执行。
    """
    # GSHub 的 4 个 publish Graph 相对路径（父级，包含整个 publish 子图）
    PUBLISH_GRAPH_PATHS = [
        "GS_Hub/Graphs/ROS2_publish_Lidar_Odom",
        "GS_Hub/Graphs/ROS2_publish_L_cam",
        "GS_Hub/Graphs/ROS2_publish_R_cam",
    ]

    disabled = 0
    for rel_path in PUBLISH_GRAPH_PATHS:
        full_path = f"{gshub_root_path}/{rel_path}"
        prim = stage.GetPrimAtPath(full_path)
        if not prim.IsValid():
            continue

        try:
            # 方法：SetActive(False) 禁用整个 Graph（包含其下所有节点）
            prim.SetActive(False)
            disabled += 1
        except Exception as e:
            print(f"[GSHub] ⚠️  Failed to disable graph {full_path}: {e}", flush=True)

    return disabled


# 🔥 立即执行配置 🔥
configure_ros_env()


import omni.usd
from pxr import Sdf
import isaaclab.sim as sim_utils
import isaaclab.sim.utils as prim_utils
from isaaclab.assets import AssetBaseCfg
from isaaclab.utils import configclass
from EAI_assets.asset_resolver import asset_path

gs_hub_path = asset_path("payloads/sensors/gs_hub/GS_Hub_fix_type.usd")

# 全局字典：临时存储每个 GSHub 实例的 enable_ros_publish 配置
# Key: prim_path, Value: bool
_gshub_ros_publish_config = {}

def spawn_and_fix_gshub(prim_path, cfg, translation, orientation):
    """
    自定义生成回调函数：
    1. 加载 USD 模型。
    2. 立即修复内部 Graph 的 chassisPrim 连接。
    """
    # --- A. 标准流程：调用 Isaac Lab 默认加载器 ---
    # 这会在场景中生成 USD 模型
    sim_utils.spawn_from_usd(
        prim_path,
        cfg,
        translation,
        orientation
    )

    # --- B. 立即执行修复逻辑 ---
    # print(f"[GSHub] ⚙️ Spawning at {prim_path}, applying fix...")
    target_prim_name = prim_path.split("/")[-1]  # "GSHub"
    parent_regex = prim_path.rpartition("/")[0]  # "/World/envs/env_.*/Robot/base"

    # 查找所有匹配的父级路径 (例如 env_0/Robot/base, env_1/Robot/base...)
    # 这一步依赖于 Robot 必须已经被创建了 (InteractiveScene 会保证顺序)
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)

    if not matched_parents:
        print(f"[GSHub] ⚠️ No parents found matching: {parent_regex}")
        # 如果找不到父级 (可能是单环境且没用正则)，尝试直接用原路径当做唯一路径
        resolved_paths = [prim_path]
    else:
        # 拼接回完整路径
        resolved_paths = [f"{p}/{target_prim_name}" for p in matched_parents]


    print(f"[GSHub] ⚙️ Spawning & Fixing for {len(resolved_paths)} instances...")
    stage = omni.usd.get_context().get_stage()

    # --- 2. 循环处理每一个实例 (关键修复点) ---
    for specific_path in resolved_paths:  # <--- 这里必须循环！
        # 1. 寻找 Graph 路径
        # 尝试默认路径 .../GS_Hub/Graphs/...
        graph_path = f"{specific_path}/GS_Hub/Graphs/ROS2_publish_Lidar_Odom"
        if not stage.GetPrimAtPath(graph_path).IsValid():
            print(f"[GSHub] ⚠️ Warning: Graph not found at {specific_path}, skipping fix.")
            return

        # 2. 计算目标 Robot 路径 (Articulation Root)
        try:
            target_path = Sdf.Path(specific_path).GetParentPath()
        except Exception:
            print(f"[GSHub] ❌ Error resolving parent path for {specific_path}")
            return

        # 3. 定位节点并修改 Relationship
        node_path = f"{graph_path}/isaac_compute_odometry_node"
        node_prim = stage.GetPrimAtPath(node_path)

        if node_prim.IsValid():
            rel_name = "inputs:chassisPrim"
            rel = node_prim.GetRelationship(rel_name)

            # 如果关系不存在则创建
            if not rel:
                rel = node_prim.CreateRelationship(rel_name)

            # === 强制修复连接 ===
            rel.SetTargets([target_path])
            print(f"[GSHub] ✅ Fixed Odometry: {node_path} -> {target_path}")
        else:
            print(f"[GSHub] ❌ Odometry Node not found at {node_path}")

        # 如果禁用 ROS 发布,停用 publish 节点
        enable_ros_publish = True  # 默认开启（向后兼容）

        # 尝试从全局字典读取（多种路径格式）
        for path_key in [prim_path, specific_path, f"{specific_path}/GS_Hub"]:
            if path_key in _gshub_ros_publish_config:
                enable_ros_publish = _gshub_ros_publish_config[path_key]
                break

        if not enable_ros_publish:
            disabled_count = _disable_gshub_ros_publishers(stage, specific_path)
            print(f"[GSHub] 🔇 ROS publish disabled for {specific_path.split('/')[-3]}", flush=True)
        else:
            namespace = _gshub_ros_namespace_for_instance(cfg, specific_path)
            updated_count = _apply_gshub_ros_namespace(stage, specific_path, namespace)
            if updated_count:
                print(f"[GSHub] ✅ ROS namespace: {namespace} ({updated_count} publish nodes)")
            elif namespace:
                print(f"[GSHub] ⚠️ ROS namespace not applied for {specific_path}: {namespace}")

@configclass
class GSHubCfg(AssetBaseCfg):
    ros_namespace: str | None = None
    enable_ros_publish: bool = True  # 是否激活 ROS2 发布节点（默认 True 保持向后兼容）
    spawn = sim_utils.UsdFileCfg(
        usd_path=gs_hub_path,
        func=spawn_and_fix_gshub,
    )
    asset_dependencies = (gs_hub_path,)
