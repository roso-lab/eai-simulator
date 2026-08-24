import os
import re
import urllib.request

from EAI_assets.ros_config import (
    configure_ros_env as _configure_shared_ros_env,
    find_isaac_ros_bridge_path as _find_shared_isaac_ros_bridge_path,
)


def _find_isaac_ros_bridge_path(ros_distro: str | None = None):
    return _find_shared_isaac_ros_bridge_path(ros_distro)


def configure_ros_env():
    disabled_values = {"1", "true", "yes", "on"}
    if any(
        os.environ.get(variable, "").strip().casefold() in disabled_values
        for variable in ("EAI_DISABLE_SENSOR_ROS_ENV", "EAI_DISABLE_ORSUS_ROS_ENV")
    ):
        return None

    isaac_ros_path = _configure_shared_ros_env()
    if not isaac_ros_path:
        print("[EnvSetup] Warning: Could not find Isaac ROS Bridge path automatically.")
        return

    print(f"[EnvSetup] Found Isaac ROS Path: {isaac_ros_path}")


_ROS_LIDAR_NAMESPACE_NODE_SUFFIXES = (
    "Lidar/Graphs/ROS2_publish_Lidar_Odom/ros2_rtx_lidar_helper",
    "Lidar/Graphs/ROS2_publish_Lidar_Odom/ros2_publish_odometry",
)

_ROS_LIDAR_HELPER_NODE_SUFFIX = "Lidar/Graphs/ROS2_publish_Lidar_Odom/ros2_rtx_lidar_helper"
_ROS_LIDAR_ODOMETRY_NODE_SUFFIX = "Lidar/Graphs/ROS2_publish_Lidar_Odom/isaac_compute_odometry_node"
_ROS_LIDAR_SENSOR_SUFFIX = "Lidar/base_link/lidar_link/RtxLidar"
_OFFICIAL_RTX_LIDAR_ASSET_NAME = "HESAI_XT32_SD10.usd"
_OFFICIAL_RTX_LIDAR_ASSET_BASE_URL = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.1"
    "/Isaac/Sensors/HESAI/XT32_SD10/"
)
_OFFICIAL_RTX_LIDAR_ASSET_FILES = (
    "HESAI_XT32_SD10.usd",
    "Hesai_XT32_SD10.usda",
    "materials/materials.usd",
)
_RTX_LIDAR_SENSOR_PARAMS = {
    "config": "HESAI_XT32_SD10",
    "translation": (0, 0, 0),
}
_RTX_LIDAR_RENDER_VARS = ["GenericModelOutput", "RtxSensorMetadata"]
_RTX_LIDAR_REQUIRED_SCHEMA = "OmniSensorGenericLidarCoreAPI"


def _rtx_lidar_extension_names(*, modern: bool) -> tuple[str, ...]:
    sensor_extension = (
        "isaacsim.sensors.experimental.rtx" if modern else "isaacsim.sensors.rtx"
    )
    return (
        "isaacsim.ros2.bridge",
        sensor_extension,
        "omni.sensors.nv.lidar",
        "omni.usd.schema.omni_sensors",
        "omni.replicator.core",
    )


_ROS_LIDAR_RENDER_PRODUCT_HANDLES = []
_ROS_LIDAR_STAGE_EVENT_SUB = None


def _destroy_ros_lidar_render_products() -> None:
    handles = tuple(_ROS_LIDAR_RENDER_PRODUCT_HANDLES)
    _ROS_LIDAR_RENDER_PRODUCT_HANDLES.clear()
    for handle in handles:
        destroy = getattr(handle, "destroy", None)
        if not callable(destroy):
            continue
        try:
            destroy()
        except Exception as exc:
            print(f"[RosLidar] Warning: Failed to destroy RTX LiDAR render product: {exc}")


def _destroy_rtx_lidar_render_product(
    render_product_path: str,
) -> bool:
    """Destroy one render product created by ``_create_rtx_lidar_render_product``."""
    expected_path = str(render_product_path)
    for index, handle in reversed(tuple(enumerate(_ROS_LIDAR_RENDER_PRODUCT_HANDLES))):
        handle_path = str(getattr(handle, "path", handle))
        if handle_path != expected_path:
            continue
        _ROS_LIDAR_RENDER_PRODUCT_HANDLES.pop(index)
        destroy = getattr(handle, "destroy", None)
        if callable(destroy):
            destroy()
        return True
    return False


def _on_ros_lidar_stage_event(event) -> None:
    import omni.usd

    if event.type == int(omni.usd.StageEventType.CLOSING):
        _destroy_ros_lidar_render_products()


def _ensure_ros_lidar_stage_closing_subscription() -> None:
    global _ROS_LIDAR_STAGE_EVENT_SUB
    if _ROS_LIDAR_STAGE_EVENT_SUB is not None:
        return
    import omni.usd

    _ROS_LIDAR_STAGE_EVENT_SUB = (
        omni.usd.get_context()
        .get_stage_event_stream()
        .create_subscription_to_pop(
            _on_ros_lidar_stage_event,
            name="EAI ROS LiDAR render product cleanup",
        )
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


def _robot_name_from_ros_lidar_path(specific_path: str) -> str:
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


def _ros_lidar_namespace_for_instance(cfg, specific_path: str) -> str:
    explicit_namespace = getattr(cfg, "ros_namespace", None)
    if explicit_namespace:
        return _normalize_ros_namespace(explicit_namespace)
    return _normalize_ros_namespace(_robot_name_from_ros_lidar_path(specific_path))


def _set_node_namespace(stage, node_path: str, namespace: str) -> bool:
    node_prim = stage.GetPrimAtPath(node_path)
    if not node_prim or not node_prim.IsValid():
        return False
    namespace_attr = node_prim.GetAttribute("inputs:nodeNamespace")
    if not namespace_attr:
        return False
    namespace_attr.Set(namespace)
    return True


def _apply_ros_lidar_namespace(stage, specific_path: str, namespace: str) -> int:
    if not namespace:
        return 0
    updated = 0
    for suffix in _ROS_LIDAR_NAMESPACE_NODE_SUFFIXES:
        node_path = f"{specific_path}/{suffix}"
        if _set_node_namespace(stage, node_path, namespace):
            updated += 1
    return updated


def _set_relationship_target(stage, node_path: str, rel_name: str, target_path: str) -> bool:
    node_prim = stage.GetPrimAtPath(node_path)
    if not node_prim or not node_prim.IsValid():
        return False
    rel = node_prim.GetRelationship(rel_name)
    if not rel:
        rel = node_prim.CreateRelationship(rel_name)
    rel.SetTargets([target_path])
    return True


def _parent_path(path: str) -> str:
    return str(path).rstrip("/").rsplit("/", 1)[0]


def _set_node_attr(stage, node_path: str, attr_name: str, value) -> bool:
    node_prim = stage.GetPrimAtPath(node_path)
    if not node_prim or not node_prim.IsValid():
        return False
    attr = node_prim.GetAttribute(attr_name)
    if not attr:
        return False
    attr.Set(value)
    return True


def _download_official_lidar_usd(cache_dir: str | None = None) -> str | None:
    cache_dir = cache_dir or os.environ.get(
        "EAI_LIDAR_ASSET_CACHE",
        os.path.join(os.path.expanduser("~"), ".cache", "eai-simulator", "assets"),
    )
    target_path = os.path.join(cache_dir, _OFFICIAL_RTX_LIDAR_ASSET_NAME)
    target_paths = [os.path.join(cache_dir, relative_path) for relative_path in _OFFICIAL_RTX_LIDAR_ASSET_FILES]
    if all(os.path.exists(path) for path in target_paths):
        return target_path

    for relative_path, file_path in zip(_OFFICIAL_RTX_LIDAR_ASSET_FILES, target_paths, strict=True):
        if os.path.exists(file_path):
            continue
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            urllib.request.urlretrieve(_OFFICIAL_RTX_LIDAR_ASSET_BASE_URL + relative_path, file_path)
        except Exception as exc:
            print(f"[RosLidar] Warning: Failed to download official LiDAR asset: {exc}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            return None

    if not all(os.path.exists(path) for path in target_paths):
        for file_path in target_paths:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
        return None

    return target_path


def _prim_is_valid(prim) -> bool:
    return bool(prim) and prim.IsValid()


def _find_first_prim_path_by_type(prim, type_name: str) -> str | None:
    if not _prim_is_valid(prim):
        return None
    if prim.GetTypeName() == type_name:
        return str(prim.GetPath())
    get_children = getattr(prim, "GetChildren", None)
    if not get_children:
        return None
    for child in get_children():
        child_path = _find_first_prim_path_by_type(child, type_name)
        if child_path:
            return child_path
    return None


def _find_rtx_lidar_sensor_path(stage, lidar_root_path: str) -> str | None:
    return _find_first_prim_path_by_type(stage.GetPrimAtPath(lidar_root_path), "OmniLidar")


def _create_rtx_lidar_from_experimental_api(lidar_prim_path: str) -> str:
    from isaacsim.sensors.experimental.rtx import Lidar

    sensor = Lidar.create(
        path=lidar_prim_path,
        config=_RTX_LIDAR_SENSOR_PARAMS["config"],
    )
    return str(sensor.paths[0])


def _ensure_rtx_lidar_schema(stage, lidar_prim_path: str) -> bool:
    lidar_prim = stage.GetPrimAtPath(lidar_prim_path)
    if not lidar_prim or not lidar_prim.IsValid() or lidar_prim.GetTypeName() != "OmniLidar":
        return False
    if not lidar_prim.HasAPI(_RTX_LIDAR_REQUIRED_SCHEMA):
        try:
            lidar_prim.ApplyAPI(_RTX_LIDAR_REQUIRED_SCHEMA)
        except Exception as exc:
            print(f"[RosLidar] Warning: Failed to apply {_RTX_LIDAR_REQUIRED_SCHEMA}: {exc}")
            return False
    return lidar_prim.HasAPI(_RTX_LIDAR_REQUIRED_SCHEMA)


def _create_rtx_lidar_from_asset(
    stage,
    lidar_root_path: str,
    lidar_asset_path: str,
) -> str | None:
    existing_prim = stage.GetPrimAtPath(lidar_root_path)
    if _prim_is_valid(existing_prim):
        stage.RemovePrim(lidar_root_path)
    try:
        try:
            from isaacsim.core.utils.stage import add_reference_to_stage
        except ModuleNotFoundError:
            # The stage helper was removed in Isaac Sim 6.0.  Authoring the
            # reference directly keeps the fallback independent of the
            # deprecated ``isaacsim.core.utils`` extension.
            root = stage.DefinePrim(lidar_root_path, "Xform")
            root.GetReferences().AddReference(lidar_asset_path)
        else:
            add_reference_to_stage(
                usd_path=lidar_asset_path,
                prim_path=lidar_root_path,
                prim_type="Xform",
            )
    except Exception as exc:
        print(
            "[RosLidar] Warning: Failed to reference RTX LiDAR asset "
            f"{lidar_asset_path}: {exc}"
        )
        return None
    return _find_rtx_lidar_sensor_path(stage, lidar_root_path)


def _create_rtx_lidar_from_downloaded_asset(stage, lidar_root_path: str) -> str | None:
    asset_path = _download_official_lidar_usd()
    if not asset_path:
        return None
    return _create_rtx_lidar_from_asset(stage, lidar_root_path, asset_path)


def spawn_ros_lidar_preview_visual(stage, specific_path: str) -> str:
    """Add the visible HESAI model to a preview-only ROS LiDAR shell."""
    lidar_prim_path = f"{str(specific_path).rstrip('/')}/{_ROS_LIDAR_SENSOR_SUFFIX}"
    sensor_path = _create_rtx_lidar_from_downloaded_asset(stage, lidar_prim_path)
    if not sensor_path:
        raise RuntimeError(
            "LiDAR preview model could not be loaded at "
            f"{lidar_prim_path}; check network access or EAI_LIDAR_ASSET_CACHE"
        )
    return sensor_path


def _create_rtx_lidar_render_product(
    stage,
    lidar_prim_path: str,
    *,
    sensor_asset_path: str | None = None,
    allow_official_asset_fallback: bool = True,
    create_render_product: bool = True,
) -> str | None:
    """Ensure the RTX sensor exists and optionally create its render product."""
    try:
        from isaacsim.core.experimental.utils.app import enable_extension
        modern = True
    except ModuleNotFoundError:
        # Isaac Sim 5.x kept the helper in the deprecated core.utils package.
        from isaacsim.core.utils.extensions import enable_extension
        modern = False
    from pxr import Gf
    import omni.kit.app
    import omni.kit.commands

    extension_failed = False
    for extension_name in _rtx_lidar_extension_names(modern=modern):
        if enable_extension(extension_name) is False:
            extension_failed = True
    if extension_failed:
        print("[RosLidar] Warning: RTX LiDAR extensions are unavailable; keeping the authored sensor shell.")
        return None
    omni.kit.app.get_app().update()
    import omni.replicator.core as rep

    if sensor_asset_path:
        sensor_path = _create_rtx_lidar_from_asset(
            stage,
            lidar_prim_path,
            sensor_asset_path,
        )
    else:
        sensor_path = _find_rtx_lidar_sensor_path(stage, lidar_prim_path)
    if not sensor_asset_path and not sensor_path and modern:
        try:
            sensor_path = _create_rtx_lidar_from_experimental_api(lidar_prim_path)
        except Exception as exc:
            # Isaac Sim 6 may author the configured child prim before its
            # wrapper rejects an older asset that lacks the current core API.
            # Reuse that authored sensor and apply the schema below.
            sensor_path = _find_rtx_lidar_sensor_path(stage, lidar_prim_path)
            if not sensor_path:
                print(f"[RosLidar] Warning: Failed to create RTX LiDAR with the Isaac Sim 6 API: {exc}")
    if not sensor_asset_path and not sensor_path and not modern:
        existing_prim = stage.GetPrimAtPath(lidar_prim_path)
        if _prim_is_valid(existing_prim):
            stage.RemovePrim(lidar_prim_path)

        parent_path, _separator, sensor_name = lidar_prim_path.rpartition("/")
        try:
            success, sensor = omni.kit.commands.execute(
                "IsaacSensorCreateRtxLidar",
                path=f"/{sensor_name}",
                parent=parent_path or None,
                orientation=Gf.Quatd(1.0, 0.0, 0.0, 0.0),
                **_RTX_LIDAR_SENSOR_PARAMS,
            )
        except Exception as exc:
            print(f"[RosLidar] Warning: Failed to create official RTX LiDAR: {exc}")
            success, sensor = False, None

        if success and sensor:
            sensor_root_path = str(sensor.GetPath())
            sensor_path = (
                _find_rtx_lidar_sensor_path(stage, sensor_root_path)
                or _find_rtx_lidar_sensor_path(stage, lidar_prim_path)
            )

    if not sensor_path and not modern and allow_official_asset_fallback:
        sensor_path = _create_rtx_lidar_from_downloaded_asset(stage, lidar_prim_path)
    if not sensor_path:
        return None
    if not _ensure_rtx_lidar_schema(stage, sensor_path):
        return None
    if not create_render_product:
        return sensor_path

    omni.kit.app.get_app().update()
    render_product_name = "RosLidar_" + _sanitize_ros_name_component(sensor_path)
    render_product = rep.create.render_product(
        sensor_path,
        resolution=(128, 128),
        render_vars=_RTX_LIDAR_RENDER_VARS,
        name=render_product_name,
        force_new=True,
    )
    render_product_path = str(getattr(render_product, "path", render_product))
    _set_render_product_camera(stage, render_product_path, sensor_path)
    omni.kit.app.get_app().update()
    _ensure_ros_lidar_stage_closing_subscription()
    _ROS_LIDAR_RENDER_PRODUCT_HANDLES.append(render_product)
    return render_product_path


def _set_render_product_camera(stage, render_product_path: str, camera_prim_path: str) -> None:
    """Bind a sensor prim to a render product across Isaac Sim releases."""
    try:
        from isaacsim.core.utils.render_product import set_camera_prim_path
    except ModuleNotFoundError:
        from pxr import Sdf, UsdRender

        product = UsdRender.Product(stage.GetPrimAtPath(render_product_path))
        if not product:
            raise RuntimeError(f"Invalid renderProduct {render_product_path!r}")
        camera = stage.GetPrimAtPath(camera_prim_path)
        if not camera.IsValid():
            raise RuntimeError(f"Invalid camera prim {camera_prim_path!r}")
        product.GetCameraRel().SetTargets([Sdf.Path(camera_prim_path)])
        return
    set_camera_prim_path(render_product_path, camera_prim_path)


def _apply_ros_lidar_helper_render_product(stage, specific_path: str, render_product_path: str) -> int:
    helper_node = f"{specific_path}/{_ROS_LIDAR_HELPER_NODE_SUFFIX}"
    updated = 0
    if _set_node_attr(stage, helper_node, "inputs:renderProductPath", render_product_path):
        updated += 1
    if _set_node_attr(stage, helper_node, "inputs:enabled", True):
        updated += 1
    return updated


def _repair_ros_lidar_odometry_graph(stage, specific_path: str) -> int:
    """Repair legacy provider graph connections before OmniGraph evaluates them."""
    graph_root = f"{specific_path}/Lidar/Graphs/ROS2_publish_Lidar_Odom"
    compute_node = f"{graph_root}/isaac_compute_odometry_node"
    publish_node = f"{graph_root}/ros2_publish_odometry"
    context_node = f"{graph_root}/ros2_context"
    repaired = 0

    connection_targets = {
        f"{publish_node}.inputs:execIn": f"{compute_node}.outputs:execOut",
        f"{publish_node}.inputs:context": f"{context_node}.outputs:context",
    }
    for destination, source in connection_targets.items():
        attribute = stage.GetAttributeAtPath(destination)
        if not attribute.IsValid():
            continue
        current = [str(item) for item in attribute.GetConnections()]
        if current == [source]:
            continue
        attribute.SetConnections([source])
        repaired += 1
    return repaired


def _fix_ros_lidar_relationships(stage, specific_path: str) -> int:
    fixed = 0
    odom_node = f"{specific_path}/{_ROS_LIDAR_ODOMETRY_NODE_SUFFIX}"
    chassis_prim = _parent_path(specific_path)

    if _set_relationship_target(stage, odom_node, "inputs:chassisPrim", chassis_prim):
        fixed += 1
    return fixed


configure_ros_env()


import omni.usd
import isaaclab.sim as sim_utils
import isaaclab.sim.utils as prim_utils
from isaaclab.assets import AssetBaseCfg
from isaaclab.utils import configclass
from EAI_assets.asset_resolver import asset_path


ros_lidar_path = asset_path("payloads/sensors/lidar/ros_lidar.usda")


def spawn_and_fix_ros_lidar(prim_path, cfg, translation, orientation):
    sim_utils.spawn_from_usd(
        prim_path,
        cfg,
        translation,
        orientation,
    )

    target_prim_name = prim_path.split("/")[-1]
    parent_regex = prim_path.rpartition("/")[0]
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    if not matched_parents:
        print(f"[RosLidar] Warning: No parents found matching: {parent_regex}")
        resolved_paths = [prim_path]
    else:
        resolved_paths = [f"{p}/{target_prim_name}" for p in matched_parents]

    print(f"[RosLidar] Spawning & fixing for {len(resolved_paths)} instances...")
    stage = omni.usd.get_context().get_stage()
    for specific_path in resolved_paths:
        graph_path = f"{specific_path}/Lidar/Graphs/ROS2_publish_Lidar_Odom"
        graph_prim = stage.GetPrimAtPath(graph_path)
        if not graph_prim.IsValid():
            print(f"[RosLidar] Warning: Graph not found at {specific_path}, skipping fix.")
            continue

        enable_ros_publish = bool(getattr(cfg, "enable_ros_publish", True))
        graph_prim.SetActive(enable_ros_publish)
        lidar_prim_path = f"{specific_path}/{_ROS_LIDAR_SENSOR_SUFFIX}"
        if not enable_ros_publish:
            sensor_path = _create_rtx_lidar_render_product(
                stage,
                lidar_prim_path,
                create_render_product=False,
            )
            if sensor_path:
                print(f"[RosLidar] Physical RTX LiDAR: {sensor_path}")
            else:
                print(f"[RosLidar] Warning: Failed to create physical RTX LiDAR at {lidar_prim_path}")
            print(f"[RosLidar] Publisher graph: off ({specific_path})")
            continue

        namespace = _ros_lidar_namespace_for_instance(cfg, specific_path)
        fixed_count = _fix_ros_lidar_relationships(stage, specific_path)
        repaired_count = _repair_ros_lidar_odometry_graph(stage, specific_path)
        if repaired_count:
            print(f"[RosLidar] Repaired odometry graph for {specific_path} ({repaired_count})")
        if fixed_count:
            print(f"[RosLidar] Fixed graph relationships for {specific_path} ({fixed_count})")
        else:
            print(f"[RosLidar] Warning: No graph relationships fixed for {specific_path}")

        updated_count = _apply_ros_lidar_namespace(stage, specific_path, namespace)
        if updated_count:
            print(f"[RosLidar] ROS namespace: {namespace} ({updated_count} publish nodes)")
        elif namespace:
            print(f"[RosLidar] Warning: ROS namespace not applied for {specific_path}: {namespace}")

        render_product_path = _create_rtx_lidar_render_product(stage, lidar_prim_path)
        if render_product_path and _apply_ros_lidar_helper_render_product(stage, specific_path, render_product_path) == 2:
            print(f"[RosLidar] RTX LiDAR helper: {namespace}/cloud ({render_product_path})")
        else:
            print(f"[RosLidar] Warning: Failed to configure RTX LiDAR helper for {lidar_prim_path}")


@configclass
class RosLidarSpawnCfg(sim_utils.UsdFileCfg):
    ros_namespace: str | None = None
    enable_ros_publish: bool = True


@configclass
class RosLidarCfg(AssetBaseCfg):
    ros_namespace: str | None = None
    enable_ros_publish: bool = True
    spawn = RosLidarSpawnCfg(
        usd_path=ros_lidar_path,
        func=spawn_and_fix_ros_lidar,
    )
    asset_dependencies = (ros_lidar_path,)

    def __post_init__(self) -> None:
        if not isinstance(self.spawn, RosLidarSpawnCfg):
            return
        self.spawn.ros_namespace = self.ros_namespace
        self.spawn.enable_ros_publish = self.enable_ros_publish
