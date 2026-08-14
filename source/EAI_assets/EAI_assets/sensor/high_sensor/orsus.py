import hashlib
import os
import re
import site
import sys
from pathlib import Path


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
    if os.environ.get("EAI_DISABLE_ORSUS_ROS_ENV", "").strip().casefold() in {"1", "true", "yes", "on"}:
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


_ORSUS_ROS_NAMESPACE_NODE_SUFFIXES = (
    "Orsus/Graphs/ROS2_publish_L_cam/ros2_camera_helper",
    "Orsus/Graphs/ROS2_publish_R_cam/ros2_camera_helper",
)

_ORSUS_CAMERA_GRAPH_PATHS = (
    "Orsus/Graphs/ROS2_publish_L_cam",
    "Orsus/Graphs/ROS2_publish_R_cam",
)
_ORSUS_ROS_GRAPH_PATHS = (
    "Orsus/Graphs/ROS2_publish_Lidar_Odom",
)
_ORSUS_LIDAR_PRIM_PATH = "Orsus/base_link/lidar_link/Orsus_Lidar"
_ORSUS_MID360_RTX_ASSET_PATH = str(
    Path(__file__).with_name("orsus_mid360_rtx.usda")
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


def _robot_name_from_orsus_path(specific_path: str) -> str:
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


def _orsus_ros_namespace_for_instance(cfg, specific_path: str) -> str:
    explicit_namespace = getattr(cfg, "ros_namespace", None)
    if explicit_namespace:
        return _normalize_ros_namespace(explicit_namespace)
    return _normalize_ros_namespace(_robot_name_from_orsus_path(specific_path))


def _orsus_runtime_graph_path(specific_path: str) -> str:
    instance_name = _sanitize_ros_name_component(
        specific_path.strip("/").replace("/", "_")
    )
    return f"/World/EAI_ORSUS_GRAPHS/{instance_name}"


def _set_node_namespace(stage, node_path: str, namespace: str) -> bool:
    node_prim = stage.GetPrimAtPath(node_path)
    if not node_prim or not node_prim.IsValid():
        return False
    namespace_attr = node_prim.GetAttribute("inputs:nodeNamespace")
    if not namespace_attr:
        return False
    namespace_attr.Set(namespace)
    return True


def _apply_orsus_ros_namespace(stage, specific_path: str, namespace: str) -> int:
    if not namespace:
        return 0
    updated = 0
    for suffix in _ORSUS_ROS_NAMESPACE_NODE_SUFFIXES:
        node_path = f"{specific_path}/{suffix}"
        if _set_node_namespace(stage, node_path, namespace):
            updated += 1
    return updated


def _set_orsus_publish_graphs_active(
    stage,
    orsus_root_path: str,
    graph_paths: tuple[str, ...],
    enabled: bool,
) -> int:
    """Set one group of embedded Orsus publisher graphs active or inactive."""
    updated = 0
    for rel_path in graph_paths:
        full_path = f"{orsus_root_path}/{rel_path}"
        prim = stage.GetPrimAtPath(full_path)
        if not prim.IsValid():
            continue

        try:
            prim.SetActive(bool(enabled))
            updated += 1
        except Exception as e:
            action = "enable" if enabled else "disable"
            print(f"[Orsus] ⚠️  Failed to {action} graph {full_path}: {e}", flush=True)

    return updated


# 🔥 立即执行配置 🔥
configure_ros_env()


import omni.usd
from pxr import Sdf, UsdPhysics
import isaaclab.sim as sim_utils
import isaaclab.sim.utils as prim_utils
from isaaclab.assets import AssetBaseCfg
from isaaclab.utils import configclass
from EAI_assets.asset_resolver import asset_path


def _orsus_runtime_asset_path(source_path: str) -> str:
    """Create a runtime copy without the non-instance-safe LiDAR/odometry graph."""
    source_path = str(Path(source_path).expanduser().resolve())
    source_stat = Path(source_path).stat()
    cache_key = f"v5:{source_path}:{source_stat.st_size}:{source_stat.st_mtime_ns}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:12]
    source = Path(source_path)
    runtime_cache = Path(
        os.environ.get(
            "EAI_RUNTIME_ASSET_CACHE",
            Path.home() / ".cache/eai-simulator/runtime-assets",
        )
    ).expanduser()
    runtime_cache.mkdir(parents=True, exist_ok=True)
    runtime_path = runtime_cache / f"{source.stem}.eai_runtime_{digest}.usdc"
    if runtime_path.is_file():
        return str(runtime_path)

    layer = Sdf.Layer.OpenAsAnonymous(source_path)
    if layer is None:
        raise RuntimeError(f"Failed to open Orsus asset: {source_path}")
    removed_paths = (
        Sdf.Path("/Root/Orsus/Graphs/ROS2_publish_Lidar_Odom"),
        Sdf.Path("/Root/Orsus/base_link/lidar_link/Orsus_Lidar"),
    )
    if layer.GetPrimAtPath(removed_paths[0]) is None:
        raise RuntimeError(f"Orsus LiDAR/odometry graph is missing: {source_path}")
    if layer.GetPrimAtPath(removed_paths[1]) is None:
        raise RuntimeError(f"Orsus LiDAR prim is missing: {source_path}")
    namespace_edit = Sdf.BatchNamespaceEdit()
    for removed_path in removed_paths:
        namespace_edit.Add(removed_path, Sdf.Path.emptyPath)
    if not layer.Apply(namespace_edit):
        raise RuntimeError(f"Failed to remove Orsus source graph: {source_path}")
    if not layer.Export(str(runtime_path)):
        raise RuntimeError(f"Failed to create Orsus runtime asset: {runtime_path}")
    return str(runtime_path)


orsus_source_path = asset_path("payloads/sensors/orsus/Orsus_fix_type.usd")
orsus_path = orsus_source_path

# 全局字典：临时存储每个 Orsus 实例的发布配置
# Key: prim_path, Value: bool
_orsus_ros_publish_config = {}
_orsus_camera_publish_config = {}
_orsus_disable_physics_config = {}
_orsus_ros_graph_requests: dict[str, tuple[str, str, str]] = {}
_orsus_ros_resources: dict[str, tuple[str, str, object]] = {}


def _create_orsus_rtx_lidar_publisher(
    stage,
    lidar_prim_path: str,
    namespace: str,
) -> tuple[str, object]:
    """Create the calibrated RTX LiDAR and attach its PointCloud2 writer."""
    import omni.kit.app
    import omni.replicator.core as rep
    from EAI_assets.sensor.low_sensor.ros_lidar import (
        _create_rtx_lidar_render_product,
        _destroy_rtx_lidar_render_product,
    )

    render_product_path = _create_rtx_lidar_render_product(
        stage,
        lidar_prim_path,
        sensor_asset_path=_ORSUS_MID360_RTX_ASSET_PATH,
        allow_official_asset_fallback=False,
    )
    if not render_product_path:
        raise RuntimeError(f"Failed to create Orsus RTX LiDAR at {lidar_prim_path}")

    writer = None
    try:
        writer = rep.writers.get("RtxLidarROS2PublishPointCloud")
        writer.initialize(
            nodeNamespace=namespace,
            topicName="cloud",
            frameId="mapping_init",
        )
        writer.attach([render_product_path])
        omni.kit.app.get_app().update()
    except Exception:
        detach = getattr(writer, "detach", None)
        if callable(detach):
            detach()
        _destroy_rtx_lidar_render_product(render_product_path)
        raise
    return render_product_path, writer


def setup_pending_orsus_ros_graphs() -> int:
    """Create RTX LiDAR publishers and instance-safe odometry graphs."""
    if not _orsus_ros_graph_requests:
        return 0

    import omni.graph.core as og

    keys = og.Controller.Keys
    created = 0
    stage = omni.usd.get_context().get_stage()
    for graph_path, (lidar_prim_path, chassis_prim_path, namespace) in tuple(
        _orsus_ros_graph_requests.items()
    ):
        render_product_path, writer = _create_orsus_rtx_lidar_publisher(
            stage,
            lidar_prim_path,
            namespace,
        )
        try:
            og.Controller.edit(
                {
                    "graph_path": graph_path,
                    "evaluator_name": "execution",
                    "pipeline_stage": (
                        og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION
                    ),
                },
                {
                    keys.CREATE_NODES: [
                        ("on_playback_tick", "omni.graph.action.OnPlaybackTick"),
                        (
                            "isaac_read_simulation_time",
                            "isaacsim.core.nodes.IsaacReadSimulationTime",
                        ),
                        (
                            "isaac_compute_odometry_node",
                            "isaacsim.core.nodes.IsaacComputeOdometry",
                        ),
                        (
                            "ros2_publish_odometry",
                            "isaacsim.ros2.bridge.ROS2PublishOdometry",
                        ),
                    ],
                    keys.SET_VALUES: [
                        (
                            "isaac_compute_odometry_node.inputs:chassisPrim",
                            [Sdf.Path(chassis_prim_path)],
                        ),
                        ("ros2_publish_odometry.inputs:nodeNamespace", namespace),
                        ("ros2_publish_odometry.inputs:topicName", "odometry"),
                        ("ros2_publish_odometry.inputs:odomFrameId", "mapping_init"),
                    ],
                    keys.CONNECT: [
                        (
                            "on_playback_tick.outputs:tick",
                            "isaac_compute_odometry_node.inputs:execIn",
                        ),
                        (
                            "isaac_compute_odometry_node.outputs:execOut",
                            "ros2_publish_odometry.inputs:execIn",
                        ),
                        (
                            "isaac_compute_odometry_node.outputs:position",
                            "ros2_publish_odometry.inputs:position",
                        ),
                        (
                            "isaac_compute_odometry_node.outputs:orientation",
                            "ros2_publish_odometry.inputs:orientation",
                        ),
                        (
                            "isaac_compute_odometry_node.outputs:linearVelocity",
                            "ros2_publish_odometry.inputs:linearVelocity",
                        ),
                        (
                            "isaac_compute_odometry_node.outputs:angularVelocity",
                            "ros2_publish_odometry.inputs:angularVelocity",
                        ),
                        (
                            "isaac_read_simulation_time.outputs:simulationTime",
                            "ros2_publish_odometry.inputs:timeStamp",
                        ),
                    ],
                },
            )
        except Exception:
            detach = getattr(writer, "detach", None)
            if callable(detach):
                detach()
            from EAI_assets.sensor.low_sensor.ros_lidar import (
                _destroy_rtx_lidar_render_product,
            )

            _destroy_rtx_lidar_render_product(render_product_path)
            lidar_prim = stage.GetPrimAtPath(lidar_prim_path)
            if lidar_prim and lidar_prim.IsValid():
                stage.RemovePrim(lidar_prim_path)
            raise
        _orsus_ros_resources[graph_path] = (
            lidar_prim_path,
            render_product_path,
            writer,
        )
        _orsus_ros_graph_requests.pop(graph_path, None)
        created += 1
    return created


def close_orsus_ros_resources() -> None:
    """Release RTX writers, render products, sensors, and odometry graphs."""
    resources = tuple(_orsus_ros_resources.items())
    _orsus_ros_resources.clear()
    _orsus_ros_graph_requests.clear()
    if not resources:
        return

    from EAI_assets.sensor.low_sensor.ros_lidar import (
        _destroy_rtx_lidar_render_product,
    )

    stage = omni.usd.get_context().get_stage()
    for graph_path, (lidar_prim_path, render_product_path, writer) in resources:
        detach = getattr(writer, "detach", None)
        if callable(detach):
            try:
                detach()
            except Exception as exc:
                print(f"[Orsus] Warning: Failed to detach RTX LiDAR writer: {exc}")
        try:
            _destroy_rtx_lidar_render_product(render_product_path)
        except Exception as exc:
            print(f"[Orsus] Warning: Failed to destroy RTX render product: {exc}")
        for prim_path in (graph_path, lidar_prim_path):
            try:
                prim = stage.GetPrimAtPath(prim_path)
                if prim and prim.IsValid():
                    stage.RemovePrim(prim_path)
            except Exception as exc:
                print(f"[Orsus] Warning: Failed to remove {prim_path}: {exc}")

    try:
        from omni.kit import app as kit_app

        kit_app.get_app().update()
    except Exception as exc:
        print(f"[Orsus] Warning: Failed to finalize RTX LiDAR cleanup: {exc}")


def _disable_orsus_payload_physics(stage, specific_path: str) -> tuple[bool, bool]:
    """Keep a mounted Orsus visual/sensor-only instead of extending the chassis."""
    base_link_path = f"{specific_path}/Orsus/base_link"
    collision_path = f"{base_link_path}/collisions"

    collision_disabled = False
    collision_prim = stage.GetPrimAtPath(collision_path)
    if collision_prim and collision_prim.IsValid():
        UsdPhysics.CollisionAPI(collision_prim).CreateCollisionEnabledAttr(False)
        collision_disabled = True

    mass_removed = False
    base_link_prim = stage.GetPrimAtPath(base_link_path)
    if (
        base_link_prim
        and base_link_prim.IsValid()
        and base_link_prim.HasAPI(UsdPhysics.MassAPI)
    ):
        mass_removed = base_link_prim.RemoveAPI(UsdPhysics.MassAPI)

    return collision_disabled, mass_removed


def _orsus_physics_disabled_for_instance(cfg, prim_path: str, specific_path: str) -> bool:
    if bool(getattr(cfg, "disable_physics", False)):
        return True
    return any(
        bool(_orsus_disable_physics_config.get(path_key, False))
        for path_key in (prim_path, specific_path, f"{specific_path}/Orsus")
    )


def spawn_and_fix_orsus(prim_path, cfg, translation, orientation):
    """
    自定义生成回调函数：
    1. 加载 USD 模型。
    2. 配置双目发布并登记 RTX LiDAR/odometry 运行时资源。
    """
    runtime_cfg = cfg.copy()
    runtime_cfg.usd_path = _orsus_runtime_asset_path(cfg.usd_path)
    sim_utils.spawn_from_usd(
        prim_path,
        runtime_cfg,
        translation,
        orientation,
    )

    # --- B. 立即执行修复逻辑 ---
    # print(f"[Orsus] ⚙️ Spawning at {prim_path}, applying fix...")
    target_prim_name = prim_path.split("/")[-1]  # "Orsus"
    parent_regex = prim_path.rpartition("/")[0]  # "/World/envs/env_.*/Robot/base"

    # 查找所有匹配的父级路径 (例如 env_0/Robot/base, env_1/Robot/base...)
    # 这一步依赖于 Robot 必须已经被创建了 (InteractiveScene 会保证顺序)
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)

    if not matched_parents:
        print(f"[Orsus] ⚠️ No parents found matching: {parent_regex}")
        # 如果找不到父级 (可能是单环境且没用正则)，尝试直接用原路径当做唯一路径
        resolved_paths = [prim_path]
    else:
        # 拼接回完整路径
        resolved_paths = [f"{p}/{target_prim_name}" for p in matched_parents]


    print(f"[Orsus] ⚙️ Spawning & Fixing for {len(resolved_paths)} instances...")
    stage = omni.usd.get_context().get_stage()

    # --- 2. 循环处理每一个实例 (关键修复点) ---
    for specific_path in resolved_paths:  # <--- 这里必须循环！
        if _orsus_physics_disabled_for_instance(cfg, prim_path, specific_path):
            collision_disabled, mass_removed = _disable_orsus_payload_physics(
                stage, specific_path
            )
            print(
                "[Orsus] Payload physics disabled: "
                f"collision={collision_disabled}, mass={mass_removed} ({specific_path})"
            )

        # LiDAR/odometry and camera publishers have independent tool gates.
        has_ros_gate = hasattr(cfg, "enable_ros_publish")
        has_camera_gate = hasattr(cfg, "enable_camera_publish")
        enable_ros_publish = bool(getattr(cfg, "enable_ros_publish", True))
        enable_camera_publish = bool(getattr(cfg, "enable_camera_publish", True))

        # Older callers used a plain UsdFileCfg and path maps. Keep that as a fallback.
        if not has_ros_gate:
            for path_key in [prim_path, specific_path, f"{specific_path}/Orsus"]:
                if path_key in _orsus_ros_publish_config:
                    enable_ros_publish = _orsus_ros_publish_config[path_key]
                    break
        if not has_camera_gate:
            for path_key in [prim_path, specific_path, f"{specific_path}/Orsus"]:
                if path_key in _orsus_camera_publish_config:
                    enable_camera_publish = _orsus_camera_publish_config[path_key]
                    break

        camera_graph_count = _set_orsus_publish_graphs_active(
            stage,
            specific_path,
            _ORSUS_CAMERA_GRAPH_PATHS,
            enable_camera_publish,
        )

        lidar_prim_path = f"{specific_path}/{_ORSUS_LIDAR_PRIM_PATH}"
        if enable_ros_publish:
            graph_path = _orsus_runtime_graph_path(specific_path)
            chassis_prim_path = str(Sdf.Path(specific_path).GetParentPath())
            namespace = _orsus_ros_namespace_for_instance(cfg, specific_path)
            _orsus_ros_graph_requests[graph_path] = (
                lidar_prim_path,
                chassis_prim_path,
                namespace,
            )
            print(f"[Orsus] RTX LiDAR requested: {lidar_prim_path}")
            print(
                f"[Orsus] Odometry requested: {graph_path}/"
                f"isaac_compute_odometry_node -> {chassis_prim_path}"
            )

        ros_graph_count = int(enable_ros_publish)

        if enable_ros_publish or enable_camera_publish:
            namespace = _orsus_ros_namespace_for_instance(cfg, specific_path)
            updated_count = _apply_orsus_ros_namespace(stage, specific_path, namespace)
            if updated_count:
                print(f"[Orsus] ✅ ROS namespace: {namespace} ({updated_count} publish nodes)")
            elif namespace:
                print(f"[Orsus] ⚠️ ROS namespace not applied for {specific_path}: {namespace}")

        print(
            "[Orsus] Publisher graphs: "
            f"lidar/odom={'on' if enable_ros_publish else 'off'} ({ros_graph_count}), "
            f"camera={'on' if enable_camera_publish else 'off'} ({camera_graph_count})",
            flush=True,
        )

@configclass
class OrsusSpawnCfg(sim_utils.UsdFileCfg):
    ros_namespace: str | None = None
    enable_ros_publish: bool = True  # 是否激活 LiDAR/odometry ROS2 发布节点
    enable_camera_publish: bool = True  # 是否激活左右相机 ROS2 发布节点
    disable_physics: bool = False


@configclass
class OrsusCfg(AssetBaseCfg):
    ros_namespace: str | None = None
    enable_ros_publish: bool = True
    enable_camera_publish: bool = True
    disable_physics: bool = False
    spawn = OrsusSpawnCfg(
        usd_path=orsus_path,
        func=spawn_and_fix_orsus,
    )
    asset_dependencies = (orsus_source_path, _ORSUS_MID360_RTX_ASSET_PATH)

    def __post_init__(self) -> None:
        if not isinstance(self.spawn, OrsusSpawnCfg):
            return
        self.spawn.ros_namespace = self.ros_namespace
        self.spawn.enable_ros_publish = self.enable_ros_publish
        self.spawn.enable_camera_publish = self.enable_camera_publish
        self.spawn.disable_physics = self.disable_physics
