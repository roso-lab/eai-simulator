#!/usr/bin/env python3
"""
nav2_setup.py —— 按"机器人类型 + 场景"生成 Nav2 配置文件（params/rviz/pc2scan）。

让 Nav2 导航能适配 EAI 多机仿真器不同的机器人/场景，而不是写死 carter+factory。
读取 nav2_profiles.yaml（物理参数表 + 场景地图表）和 *.template.* 模板，
把占位符替换成具体值，输出到 --out 目录（默认 /tmp/eai_nav2_<robot>）。

典型用法（由 nav2.launch.py 自动调用，一般不用手动跑）：
    python3 nav2_setup.py --robot carter_1 --robot-type Carter --sensor auto --scene factory \\
        --out /tmp/eai_nav2_carter_1

参数：
    --robot       机器人实例名（ROS 话题命名空间，如 carter_1 / go2_1）。必填。
    --robot-type  机器人类型（查 robot_profiles，如 Carter/Go2/B2/Scout）。
                  不填则默认用 --robot 首段首字母大写猜测，查不到用 default_profile。
    --sensor      传感器类型：auto/orsus/lidar。auto 从运行时快照的 attachments 强校验。
    --scene       场景名（查 scene_maps 找地图，并校验活动仿真场景）。默认 factory。
    --map         显式指定地图 yaml（覆盖 scene 查表）。
    --pose        显式初始位姿 "x,y,yaw"（覆盖活动仿真位姿）。
    --runtime-snapshot  活动仿真运行时快照；自动初始位姿的数据源。
    --out         输出目录。默认 /tmp/eai_nav2_<robot>。

输出：<out>/{nav2_params.yaml, pointcloud_to_laserscan.yaml, view.rviz, meta.txt}
并把地图绝对路径打印到 stdout 最后一行（launch 用它传给 map_server）。

插件名适配：模板统一使用 Jazzy+ 的 "pkg::Name" 规范写法；生成时读取本机已安装
nav2 包的插件声明 XML（<class name> 优先，无则用 <class type>），自动改写为
pluginlib 实际声明的查找名——Humble 声明 "pkg/Name" 斜杠名，Jazzy+ 为双冒号名，
传错名字会让对应 Nav2 节点在 configure 阶段 FATAL。
"""

import argparse
import glob
import json
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

try:
    import yaml
except ImportError:
    ros_distro = os.environ.get("ROS_DISTRO", "humble")
    sys.stderr.write(f"需要 pyyaml（在系统 ROS2 环境运行：source /opt/ros/{ros_distro}/setup.bash）\n")
    raise

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", ".."))
DEFAULT_RUNTIME_SNAPSHOT = os.path.join(REPO_ROOT, "tmp", "runtime_interfaces.json")
MAX_RUNTIME_SNAPSHOT_AGE_SECONDS = 5.0

PROFILES = os.path.join(THIS_DIR, "nav2_profiles.yaml")
PARAMS_TPL = os.path.join(THIS_DIR, "nav2_params.template.yaml")
PC2SCAN_TPL = os.path.join(THIS_DIR, "pointcloud_to_laserscan.template.yaml")
RVIZ_TPL = os.path.join(THIS_DIR, "nav2_view.template.rviz")
PLANE_MAP_SIZE_M = 20.0
PLANE_MAP_RESOLUTION = 0.05
PLANE_MAP_ORIGIN = [-10.0, -10.0, 0.0]
DEFAULT_XY_GOAL_TOLERANCE = 0.25
DEFAULT_YAW_GOAL_TOLERANCE = 0.25
DEFAULT_PROGRESS_REQUIRED_MOVEMENT_RADIUS = 0.5
DEFAULT_PROGRESS_MOVEMENT_TIME_ALLOWANCE = 10.0
DEFAULT_INFLATION_RADIUS = 0.55
SENSOR_TYPES = ("orsus", "lidar")
ROBOT_TYPE_ALIASES = {
    "mushr_v2": "MuSHR Nano v2",
    "mushr nano v2": "MuSHR Nano v2",
    "coco": "Coco AIRS",
    "coco airs": "Coco AIRS",
}


def load_profiles():
    with open(PROFILES, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_profile(profiles, robot_type, robot_name):
    """按 robot_type 查物理参数；查不到猜测；再查不到用 default（告警）。"""
    table = profiles["robot_profiles"]
    names_by_casefold = {name.casefold(): name for name in table}
    requested = str(robot_type or "").strip()
    requested_name = ROBOT_TYPE_ALIASES.get(requested.casefold()) or names_by_casefold.get(requested.casefold())
    if requested_name:
        return requested_name, dict(table[requested_name])
    # 去掉实例编号后猜测（carter_1 -> Carter，mushr_v2_1 -> MuSHR Nano v2）。
    instance_type = robot_name.rsplit("_", 1)[0]
    guess = ROBOT_TYPE_ALIASES.get(instance_type.casefold(), instance_type.capitalize())
    guess = names_by_casefold.get(guess.casefold(), guess)
    if guess in table:
        print(f"[nav2_setup] ℹ️  未指定 --robot-type，按实例名猜测为 '{guess}'")
        return guess, dict(table[guess])
    print(f"[nav2_setup] ⚠️  机器人类型 '{robot_type or guess}' 未登记，使用 default_profile"
          f"（建议在 nav2_profiles.yaml 补一条）")
    return "default", dict(profiles["default_profile"])


def ensure_plane_map(out_dir):
    """Generate a blank occupancy map for the flat plane scene."""
    map_yaml = os.path.join(out_dir, "plane_map.yaml")
    map_image = os.path.join(out_dir, "plane_map.pgm")
    cells = int(PLANE_MAP_SIZE_M / PLANE_MAP_RESOLUTION)

    if not os.path.exists(map_image):
        with open(map_image, "wb") as f:
            f.write(f"P5\n{cells} {cells}\n255\n".encode("ascii"))
            f.write(bytes([254]) * cells * cells)

    with open(map_yaml, "w") as f:
        f.write("image: plane_map.pgm\n")
        f.write(f"resolution: {PLANE_MAP_RESOLUTION}\n")
        f.write(f"origin: {PLANE_MAP_ORIGIN}\n")
        f.write("negate: 0\n")
        f.write("occupied_thresh: 0.65\n")
        f.write("free_thresh: 0.196\n")

    return map_yaml


def resolve_map(profiles, scene, explicit_map, out_dir):
    if explicit_map:
        return os.path.abspath(explicit_map)
    if scene == "plane":
        return ensure_plane_map(out_dir)
    m = profiles.get("scene_maps", {}).get(scene)
    if m is None:
        return None
    return os.path.join(REPO_ROOT, m)


def pid_is_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def resolve_runtime_robot(
    snapshot_path,
    robot_name,
    scene,
    *,
    now=None,
    pid_checker=pid_is_alive,
):
    override = "Start/restart simulator.py or pass pose:=x,y,yaw."
    try:
        with open(snapshot_path, encoding="utf-8") as stream:
            snapshot = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not read runtime snapshot {snapshot_path}: {exc}. {override}"
        ) from exc
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        raise RuntimeError(f"Unsupported runtime snapshot {snapshot_path}. {override}")
    try:
        heartbeat = float(snapshot["heartbeat_at"])
        pid = int(snapshot["pid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Runtime snapshot is missing heartbeat or PID. {override}"
        ) from exc
    if not math.isfinite(heartbeat):
        raise RuntimeError(f"Runtime snapshot heartbeat is invalid. {override}")
    current = time.time() if now is None else now
    if max(0.0, current - heartbeat) > MAX_RUNTIME_SNAPSHOT_AGE_SECONDS:
        raise RuntimeError(f"Runtime snapshot is stale. {override}")
    if pid <= 0 or not pid_checker(pid):
        raise RuntimeError(
            f"Runtime snapshot simulator PID {pid} is not alive. {override}"
        )
    if snapshot.get("scene_key") != scene:
        raise RuntimeError(
            f"Runtime snapshot scene {snapshot.get('scene_key')!r} does not match "
            f"{scene!r}. {override}"
        )
    robots = snapshot.get("robots")
    if not isinstance(robots, list):
        raise RuntimeError(f"Runtime snapshot robot list is invalid. {override}")
    robot = next(
        (
            item
            for item in robots
            if isinstance(item, dict) and item.get("instance_name") == robot_name
        ),
        None,
    )
    if robot is None:
        raise RuntimeError(
            f"Robot {robot_name!r} is absent from runtime snapshot. {override}"
        )
    return robot


def resolve_runtime_pose(
    snapshot_path,
    robot_name,
    scene,
    *,
    now=None,
    pid_checker=pid_is_alive,
):
    robot = resolve_runtime_robot(
        snapshot_path,
        robot_name,
        scene,
        now=now,
        pid_checker=pid_checker,
    )
    override = "Start/restart simulator.py or pass pose:=x,y,yaw."
    world_pose = robot.get("world_pose")
    try:
        position = [float(value) for value in world_pose["position"]]
        rotation = [float(value) for value in world_pose["rotation"]]
        yaw = float(world_pose["yaw"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Robot {robot_name!r} has invalid world_pose. {override}"
        ) from exc
    values = position + rotation + [yaw]
    if (
        len(position) != 3
        or len(rotation) != 4
        or not all(math.isfinite(value) for value in values)
    ):
        raise RuntimeError(
            f"Robot {robot_name!r} has invalid world_pose. {override}"
        )
    return {"x": position[0], "y": position[1], "yaw": yaw}, "runtime_snapshot"


def resolve_sensor(
    requested_sensor,
    snapshot_path,
    robot_name,
    scene,
    *,
    now=None,
    pid_checker=pid_is_alive,
):
    if requested_sensor != "auto":
        return requested_sensor, "explicit"
    robot = resolve_runtime_robot(
        snapshot_path,
        robot_name,
        scene,
        now=now,
        pid_checker=pid_checker,
    )
    attachments = robot.get("attachments")
    if not isinstance(attachments, list):
        raise RuntimeError(
            f"Robot {robot_name!r} has invalid attachments in runtime snapshot. "
            "Pass sensor:=orsus or sensor:=lidar explicitly."
        )
    sensors = [sensor for sensor in SENSOR_TYPES if sensor in attachments]
    if len(sensors) == 1:
        return sensors[0], "runtime_snapshot"
    if not sensors:
        raise RuntimeError(
            f"Robot {robot_name!r} has neither Orsus nor LiDAR attached. "
            "Attach one sensor or pass sensor:=orsus/sensor:=lidar after verifying the simulation."
        )
    raise RuntimeError(
        f"Robot {robot_name!r} has both Orsus and LiDAR attached; both publish the same "
        "cloud/odometry topics. Keep only one sensor, or select sensor:=orsus/sensor:=lidar "
        "explicitly and disable the other publisher."
    )


def resolve_sensor_mount(profile, sensor):
    mounts = profile.get("sensor_mounts", {})
    mount = mounts.get(sensor) if isinstance(mounts, dict) else None
    if mount is None and sensor == "orsus" and "lidar_xyz" in profile:
        mount = {
            "xyz": profile["lidar_xyz"],
            "rpy": profile.get("lidar_rpy", [0.0, 0.0, 0.0]),
        }
    if not isinstance(mount, dict):
        raise ValueError(f"当前机器人 profile 不支持传感器 {sensor!r}")
    try:
        xyz = [float(value) for value in mount["xyz"]]
        rpy = [float(value) for value in mount.get("rpy", [0.0, 0.0, 0.0])]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"传感器 {sensor!r} 的安装标定无效") from exc
    if len(xyz) != 3 or len(rpy) != 3 or not all(
        math.isfinite(value) for value in xyz + rpy
    ):
        raise ValueError(f"传感器 {sensor!r} 的安装标定必须是有限的 xyz/rpy 三元组")
    return xyz, rpy


def resolve_nav_base_offset(profile):
    """Return the simulator-root -> Nav2-base local translation."""
    try:
        offset = [float(value) for value in profile.get("nav_base_offset_xyz", [0.0, 0.0, 0.0])]
    except (TypeError, ValueError) as exc:
        raise ValueError("nav_base_offset_xyz 必须是有限的 xyz 三元组") from exc
    if len(offset) != 3 or not all(math.isfinite(value) for value in offset):
        raise ValueError("nav_base_offset_xyz 必须是有限的 xyz 三元组")
    return offset


def resolve_navigation_sensor_mount(profile, sensor, base_offset_xyz):
    """Convert a physical mount from simulator-root coordinates to Nav2 base."""
    xyz, rpy = resolve_sensor_mount(profile, sensor)
    return [xyz[index] - base_offset_xyz[index] for index in range(3)], rpy


def resolve_navigation_pose(pose, base_offset_xyz):
    """Move a simulator-root planar pose to the configured Nav2 base point."""
    yaw = float(pose.get("yaw", 0.0))
    cos_yaw = math.cos(yaw)
    sin_yaw = math.sin(yaw)
    offset_x, offset_y = base_offset_xyz[:2]
    return {
        "x": float(pose["x"]) + cos_yaw * offset_x - sin_yaw * offset_y,
        "y": float(pose["y"]) + sin_yaw * offset_x + cos_yaw * offset_y,
        "yaw": yaw,
    }


def resolve_pose(robot_name, scene, explicit_pose, runtime_snapshot):
    if explicit_pose:
        try:
            values = [float(value) for value in explicit_pose.split(",")]
        except ValueError as exc:
            raise ValueError("--pose requires three finite values: x,y,yaw") from exc
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            raise ValueError("--pose requires three finite values: x,y,yaw")
        return {"x": values[0], "y": values[1], "yaw": values[2]}, "explicit"
    return resolve_runtime_pose(runtime_snapshot, robot_name, scene)


def render(template_path, subs):
    with open(template_path, encoding="utf-8") as stream:
        s = stream.read()
    for k, v in subs.items():
        s = s.replace(k, str(v))
    return s


PLUGIN_NAME_VALUE_RE = re.compile(
    r'(?P<prefix>\b(?:plugin|primary_controller):[ \t]*)(?P<q>["\']?)'
    r'(?P<name>[A-Za-z0-9_]+(?:::|/)[A-Za-z0-9_]+)(?P=q)'
)


def ros_prefixes():
    """本机 ROS2 安装前缀：AMENT_PREFIX_PATH → /opt/ros/$ROS_DISTRO → /opt/ros/*。"""
    prefixes = [
        prefix for prefix in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep) if prefix
    ]
    distro = os.environ.get("ROS_DISTRO", "").strip()
    if distro:
        prefixes.append(f"/opt/ros/{distro}")
    prefixes.extend(candidate for candidate in sorted(glob.glob("/opt/ros/*")) if os.path.isdir(candidate))
    ordered, seen = [], set()
    for prefix in prefixes:
        real = os.path.realpath(prefix)
        if real not in seen:
            seen.add(real)
            ordered.append(prefix)
    return ordered


def declared_plugin_names(package, prefixes):
    """读取已安装 ROS2 包的插件声明 XML，返回 pluginlib 查找名集合。

    pluginlib 查找名规则：<class name="..."> 优先，未声明 name 属性时用 type 属性。
    Humble 的 nav2 声明 "pkg/Name"（斜杠），Jazzy 起为 "pkg::Name"（双冒号）；
    传入与声明不一致的名字会在节点 configure 阶段 FATAL。
    """
    names = set()
    for prefix in prefixes:
        pkg_share = os.path.join(prefix, "share", package)
        package_xml = os.path.join(pkg_share, "package.xml")
        if not os.path.isfile(package_xml):
            continue
        try:
            export = ET.parse(package_xml).find("export")
        except ET.ParseError:
            continue
        if export is None:
            continue
        for tag in export:
            relative = tag.attrib.get("plugin")
            if not relative:
                continue
            plugin_xml = relative.replace("${prefix}", pkg_share)
            if not os.path.isfile(plugin_xml):
                continue
            try:
                plugin_tree = ET.parse(plugin_xml)
            except ET.ParseError:
                continue
            for cls in plugin_tree.iter("class"):
                name = cls.attrib.get("name") or cls.attrib.get("type")
                if name:
                    names.add(name)
    return names


def adapt_plugin_names(params_text, prefixes=None):
    """按本机已安装的插件声明改写参数中的插件查找名。

    模板统一使用 Jazzy+ 的 "pkg::Name" 规范写法；在声明斜杠查找名的旧发行版
    （如 Humble）上自动改写为声明的名字。查不到声明（包未安装/XML 解析失败）
    时保持原样。返回 (改写后的文本, {模板名: 声明名})。
    """
    if prefixes is None:
        prefixes = ros_prefixes()
    declared_cache = {}
    overrides = {}

    def rewrite(match):
        token = match.group("name")
        package = re.split(r"::|/", token, 1)[0]
        if package not in declared_cache:
            declared_cache[package] = declared_plugin_names(package, prefixes)
        for declared in declared_cache[package]:
            if declared.replace("::", "/") == token.replace("::", "/"):
                if declared != token:
                    overrides[token] = declared
                return (
                    match.group("prefix")
                    + match.group("q")
                    + declared
                    + match.group("q")
                )
        return match.group(0)

    return PLUGIN_NAME_VALUE_RE.sub(rewrite, params_text), overrides


def apply_navigation_plugin_profile(params, profile):
    """Replace the default NavFn/DWB pair for constrained drive kinematics."""
    controller_plugin = profile.get("controller_plugin", "dwb")
    planner_plugin = profile.get("planner_plugin", "navfn")
    if controller_plugin == "dwb" and planner_plugin == "navfn":
        return params
    if controller_plugin == "rotation_shim_dwb" and planner_plugin == "navfn":
        controller = params["controller_server"]["ros__parameters"]
        follow_path = controller["FollowPath"]
        follow_path.update({
            "plugin": "nav2_rotation_shim_controller::RotationShimController",
            "primary_controller": "dwb_core::DWBLocalPlanner",
            "angular_dist_threshold": 0.6,
            "angular_disengage_threshold": 0.2,
            "forward_sampling_distance": 0.5,
            "rotate_to_heading_angular_vel": 0.6,
            "max_angular_accel": float(profile["acc_lim_theta"]),
            "simulate_ahead_time": 1.0,
            "rotate_to_goal_heading": False,
            "closed_loop": True,
        })
        return params
    if (controller_plugin, planner_plugin) != (
        "regulated_pure_pursuit",
        "smac_hybrid",
    ):
        raise ValueError(
            "Unsupported Nav2 plugin pair: "
            f"controller={controller_plugin!r}, planner={planner_plugin!r}"
        )

    minimum_turning_radius = float(profile["minimum_turning_radius"])
    allow_reversing = bool(profile.get("allow_reversing", False))
    controller = params["controller_server"]["ros__parameters"]
    controller["FollowPath"] = {
        "plugin": (
            "nav2_regulated_pure_pursuit_controller::"
            "RegulatedPurePursuitController"
        ),
        "desired_linear_vel": float(
            profile.get("desired_linear_vel", profile["max_vel_x"])
        ),
        "lookahead_dist": 0.6,
        "min_lookahead_dist": 0.3,
        "max_lookahead_dist": 0.9,
        "lookahead_time": 1.5,
        "transform_tolerance": 0.2,
        "use_velocity_scaled_lookahead_dist": True,
        "min_approach_linear_velocity": 0.1,
        "approach_velocity_scaling_dist": 0.6,
        "use_collision_detection": True,
        "max_allowed_time_to_collision_up_to_carrot": 1.0,
        "use_regulated_linear_velocity_scaling": True,
        "use_cost_regulated_linear_velocity_scaling": True,
        "regulated_linear_scaling_min_radius": minimum_turning_radius,
        "regulated_linear_scaling_min_speed": 0.1,
        "cost_scaling_dist": 0.6,
        "cost_scaling_gain": 1.0,
        "inflation_cost_scaling_factor": 3.0,
        # An Ackermann axle cannot execute RPP's in-place heading rotation.
        "use_rotate_to_heading": False,
        "allow_reversing": allow_reversing,
        "max_angular_accel": float(profile["acc_lim_theta"]),
    }

    planner = params["planner_server"]["ros__parameters"]
    planner["GridBased"] = {
        "plugin": "nav2_smac_planner::SmacPlannerHybrid",
        "tolerance": float(profile.get("xy_goal_tolerance", 0.5)),
        "downsample_costmap": False,
        "allow_unknown": True,
        "max_iterations": 1000000,
        "max_planning_time": 5.0,
        "motion_model_for_search": "REEDS_SHEPP" if allow_reversing else "DUBIN",
        "angle_quantization_bins": 72,
        "analytic_expansion_ratio": 3.5,
        "analytic_expansion_max_length": max(5.0, 4.0 * minimum_turning_radius),
        "minimum_turning_radius": minimum_turning_radius,
        "reverse_penalty": 2.0,
        "change_penalty": 0.0,
        "non_straight_penalty": 1.2,
        "cost_penalty": 2.0,
        "lookup_table_size": 20.0,
        "cache_obstacle_heuristic": False,
        "smooth_path": True,
    }
    return params


def apply_costmap_geometry_profile(params, profile):
    """Use a base-relative polygon where a circular footprint is insufficient."""
    footprint = profile.get("footprint")
    if footprint is None:
        return params
    try:
        points = [[float(coordinate) for coordinate in point] for point in footprint]
    except (TypeError, ValueError) as exc:
        raise ValueError("footprint 必须是有限的 xy 点列表") from exc
    if len(points) < 3 or any(
        len(point) != 2 or not all(math.isfinite(value) for value in point)
        for point in points
    ):
        raise ValueError("footprint 必须至少包含三个有限的 xy 点")
    for costmap_name in ("local_costmap", "global_costmap"):
        costmap = params[costmap_name][costmap_name]["ros__parameters"]
        costmap.pop("robot_radius", None)
        # nav2_costmap_2d declares footprint as a string and parses the polygon
        # itself; ROS 2 parameters do not support nested numeric arrays.
        costmap["footprint"] = str(points)
    return params


def render_navigation_plugin_profile(params_text, profile):
    """Apply optional plugin/geometry settings, keeping default profiles byte-stable."""
    if (
        profile.get("controller_plugin", "dwb") == "dwb"
        and profile.get("planner_plugin", "navfn") == "navfn"
        and "footprint" not in profile
    ):
        return params_text
    params = yaml.safe_load(params_text)
    params = apply_navigation_plugin_profile(params, profile)
    params = apply_costmap_geometry_profile(params, profile)
    return yaml.safe_dump(params, sort_keys=False)


def main():
    ap = argparse.ArgumentParser(description="生成 Nav2 配置（按机器人/场景）")
    ap.add_argument("--robot", required=True, help="机器人实例名 = ROS 命名空间")
    ap.add_argument("--robot-type", default=None, help="机器人类型（查物理参数表）")
    ap.add_argument(
        "--sensor",
        choices=("auto", *SENSOR_TYPES),
        default="auto",
        help="点云/里程计传感器；auto 从活动仿真附件中检测",
    )
    ap.add_argument("--scene", default="factory", help="场景名（查地图并校验活动仿真）")
    ap.add_argument("--map", default=None, help="显式地图 yaml（覆盖 scene）")
    ap.add_argument("--pose", default=None, help="显式初始位姿 x,y,yaw（覆盖活动仿真）")
    ap.add_argument(
        "--runtime-snapshot",
        default=DEFAULT_RUNTIME_SNAPSHOT,
        help="活动仿真运行时快照，用于自动获取 AMCL 初始位姿",
    )
    ap.add_argument("--out", default=None, help="输出目录")
    args = ap.parse_args()

    profiles = load_profiles()
    robot_type, prof = resolve_profile(profiles, args.robot_type, args.robot)
    out_dir = args.out or f"/tmp/eai_nav2_{args.robot}"
    os.makedirs(out_dir, exist_ok=True)
    map_path = resolve_map(profiles, args.scene, args.map, out_dir)
    try:
        base_offset_xyz = resolve_nav_base_offset(prof)
        sensor, sensor_source = resolve_sensor(
            args.sensor,
            args.runtime_snapshot,
            args.robot,
            args.scene,
        )
        physical_lidar_xyz, _physical_lidar_rpy = resolve_sensor_mount(prof, sensor)
        lidar_xyz, lidar_rpy = resolve_navigation_sensor_mount(
            prof, sensor, base_offset_xyz
        )
        pose, pose_source = resolve_pose(
            args.robot,
            args.scene,
            args.pose,
            args.runtime_snapshot,
        )
        pose = resolve_navigation_pose(pose, base_offset_xyz)
    except (RuntimeError, ValueError) as exc:
        ap.error(str(exc))

    # amcl 运动模型：differential/omni -> Nav2 类名
    motion = ("nav2_amcl::DifferentialMotionModel"
              if prof["motion_model"] == "differential"
              else "nav2_amcl::OmniMotionModel")

    params_subs = {
        "@@ROBOT@@": args.robot,
        "@@MOTION_MODEL@@": motion,
        "@@POSE_X@@": pose["x"],
        "@@POSE_Y@@": pose["y"],
        "@@POSE_YAW@@": pose.get("yaw", 0.0),
        "@@ROBOT_RADIUS@@": prof["robot_radius"],
        "@@MAX_VEL_X@@": prof["max_vel_x"],
        "@@MAX_VEL_THETA@@": prof["max_vel_theta"],
        "@@ACC_LIM_X@@": prof["acc_lim_x"],
        "@@ACC_LIM_THETA@@": prof["acc_lim_theta"],
        "@@MIN_VEL_X@@": prof["min_vel_x"],
        "@@MIN_SPEED_XY@@": prof.get("min_speed_xy", max(0.0, prof["min_vel_x"])),
        "@@XY_GOAL_TOLERANCE@@": prof.get("xy_goal_tolerance", DEFAULT_XY_GOAL_TOLERANCE),
        "@@YAW_GOAL_TOLERANCE@@": prof.get("yaw_goal_tolerance", DEFAULT_YAW_GOAL_TOLERANCE),
        "@@PROGRESS_REQUIRED_MOVEMENT_RADIUS@@": prof.get(
            "progress_required_movement_radius",
            DEFAULT_PROGRESS_REQUIRED_MOVEMENT_RADIUS,
        ),
        "@@PROGRESS_MOVEMENT_TIME_ALLOWANCE@@": prof.get(
            "progress_movement_time_allowance",
            DEFAULT_PROGRESS_MOVEMENT_TIME_ALLOWANCE,
        ),
        "@@INFLATION_RADIUS@@": prof.get(
            "inflation_radius",
            max(DEFAULT_INFLATION_RADIUS, prof["robot_radius"]),
        ),
    }
    # 模板里 initial_pose 的 yaw 仍是字面 0.0；若 pose 有 yaw 再覆盖
    params = render(PARAMS_TPL, params_subs)
    if pose.get("yaw", 0.0) != 0.0:
        params = params.replace("      yaw: 0.0\n", f"      yaw: {pose['yaw']}\n", 1)
    params = render_navigation_plugin_profile(params, prof)
    params, plugin_overrides = adapt_plugin_names(params)

    pc2scan = render(PC2SCAN_TPL, {
        "@@SCAN_Z_MIN@@": prof["scan_z_min"],
        "@@SCAN_Z_MAX@@": prof["scan_z_max"],
        "@@SCAN_RANGE_MIN@@": prof.get("scan_range_min", prof["robot_radius"]),
    })
    rviz = render(RVIZ_TPL, {"@@ROBOT@@": args.robot})

    params_out = os.path.join(out_dir, "nav2_params.yaml")
    pc2scan_out = os.path.join(out_dir, "pointcloud_to_laserscan.yaml")
    rviz_out = os.path.join(out_dir, "view.rviz")
    for path, content in (
        (params_out, params),
        (pc2scan_out, pc2scan),
        (rviz_out, rviz),
    ):
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)

    with open(os.path.join(out_dir, "meta.txt"), "w", encoding="utf-8") as f:
        f.write(f"robot={args.robot}\nrobot_type={robot_type}\nscene={args.scene}\n")
        f.write(f"sensor={sensor}\nsensor_source={sensor_source}\n")
        f.write(
            f"map={map_path}\npose={pose}\npose_source={pose_source}\n"
            f"motion_model={motion}\n"
        )
        f.write(f"lidar_xyz={lidar_xyz}\n")
        f.write(f"lidar_rpy={lidar_rpy}\n")
        f.write(f"physical_lidar_xyz={physical_lidar_xyz}\n")
        f.write(f"nav_base_offset_xyz={base_offset_xyz}\n")
        f.write(f"xy_goal_tolerance={params_subs['@@XY_GOAL_TOLERANCE@@']}\n")
        f.write(f"yaw_goal_tolerance={params_subs['@@YAW_GOAL_TOLERANCE@@']}\n")
        f.write(f"inflation_radius={params_subs['@@INFLATION_RADIUS@@']}\n")
        f.write(f"controller_plugin={prof.get('controller_plugin', 'dwb')}\n")
        f.write(f"planner_plugin={prof.get('planner_plugin', 'navfn')}\n")
        f.write(f"plugin_name_overrides={plugin_overrides}\n")

    print(f"[nav2_setup] ✅ 已生成 Nav2 配置到 {out_dir}")
    print(f"  机器人={args.robot} 类型={robot_type} 场景={args.scene} 传感器={sensor}")
    print(f"  运动模型={prof['motion_model']} 半径={prof['robot_radius']} "
          f"雷达偏移={lidar_xyz} 雷达姿态={lidar_rpy}")
    print(f"  地图={map_path or '（无，需 --map 或先建图）'}")
    print(f"  初始位姿={pose}")
    if plugin_overrides:
        joined = ", ".join(f"{old} → {new}" for old, new in plugin_overrides.items())
        print(f"[nav2_setup] 🔁 插件名按本机 ROS2 安装适配: {joined}")
    # 把关键值输出成 KEY=VALUE 供 launch/脚本解析（最后几行固定格式）
    print(f"PARAMS={params_out}")
    print(f"PC2SCAN={pc2scan_out}")
    print(f"RVIZ={rviz_out}")
    print(f"SENSOR={sensor}")
    print(f"LIDAR_XYZ={lidar_xyz[0]},{lidar_xyz[1]},{lidar_xyz[2]}")
    print(f"LIDAR_RPY={lidar_rpy[0]},{lidar_rpy[1]},{lidar_rpy[2]}")
    print(
        f"BASE_OFFSET={base_offset_xyz[0]},{base_offset_xyz[1]},{base_offset_xyz[2]}"
    )
    print(f"POSE_SOURCE={pose_source}")
    print(f"MAP={map_path or ''}")


if __name__ == "__main__":
    main()
