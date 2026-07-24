#!/usr/bin/env python3
"""
nav2_setup.py —— 按"机器人类型 + 场景"生成 Nav2 配置文件（params/rviz/pc2scan）。

让 Nav2 导航能适配 EAI 多机仿真器不同的机器人/场景，而不是写死 carter+factory。
读取 nav2_profiles.yaml（物理参数表 + 场景地图表）和 *.template.* 模板，
把占位符替换成具体值，输出到 --out 目录（默认 /tmp/eai_nav2_<robot>）。

典型用法（由 nav2.launch.py 自动调用，一般不用手动跑）：
    python3 nav2_setup.py --robot carter_1 --robot-type Carter --scene factory \\
        --out /tmp/eai_nav2_carter_1

参数：
    --robot       机器人实例名（ROS 话题命名空间，如 carter_1 / go2_1）。必填。
    --robot-type  机器人类型（查 robot_profiles，如 Carter/Go2/B2/Scout）。
                  不填则默认用 --robot 首段首字母大写猜测，查不到用 default_profile。
    --scene       场景名（查 scene_maps 找地图，并校验活动仿真场景）。默认 factory。
    --map         显式指定地图 yaml（覆盖 scene 查表）。
    --pose        显式初始位姿 "x,y,yaw"（覆盖活动仿真位姿）。
    --runtime-snapshot  活动仿真运行时快照；自动初始位姿的数据源。
    --out         输出目录。默认 /tmp/eai_nav2_<robot>。

输出：<out>/{nav2_params.yaml, pointcloud_to_laserscan.yaml, view.rviz, meta.txt}
并把地图绝对路径打印到 stdout 最后一行（launch 用它传给 map_server）。
"""

import argparse
import json
import math
import os
import sys
import time

try:
    import yaml
except ImportError:
    sys.stderr.write("需要 pyyaml（在系统 ROS2 环境运行：source /opt/ros/humble/setup.bash）\n")
    raise

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", "..", ".."))
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


def load_profiles():
    with open(PROFILES) as f:
        return yaml.safe_load(f)


def resolve_profile(profiles, robot_type, robot_name):
    """按 robot_type 查物理参数；查不到猜测；再查不到用 default（告警）。"""
    table = profiles["robot_profiles"]
    if robot_type and robot_type in table:
        return robot_type, dict(table[robot_type])
    # 猜测：用实例名首段首字母大写（carter_1 -> Carter）
    guess = robot_name.split("_")[0].capitalize()
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


def resolve_runtime_pose(
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
    s = open(template_path).read()
    for k, v in subs.items():
        s = s.replace(k, str(v))
    return s


def main():
    ap = argparse.ArgumentParser(description="生成 Nav2 配置（按机器人/场景）")
    ap.add_argument("--robot", required=True, help="机器人实例名 = ROS 命名空间")
    ap.add_argument("--robot-type", default=None, help="机器人类型（查物理参数表）")
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
        pose, pose_source = resolve_pose(
            args.robot,
            args.scene,
            args.pose,
            args.runtime_snapshot,
        )
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

    pc2scan = render(PC2SCAN_TPL, {
        "@@SCAN_Z_MIN@@": prof["scan_z_min"],
        "@@SCAN_Z_MAX@@": prof["scan_z_max"],
        "@@SCAN_RANGE_MIN@@": prof.get("scan_range_min", prof["robot_radius"]),
    })
    rviz = render(RVIZ_TPL, {"@@ROBOT@@": args.robot})

    params_out = os.path.join(out_dir, "nav2_params.yaml")
    pc2scan_out = os.path.join(out_dir, "pointcloud_to_laserscan.yaml")
    rviz_out = os.path.join(out_dir, "view.rviz")
    open(params_out, "w").write(params)
    open(pc2scan_out, "w").write(pc2scan)
    open(rviz_out, "w").write(rviz)

    lidar_xyz = prof["lidar_xyz"]
    lidar_rpy = prof.get("lidar_rpy", [0.0, 0.0, 0.0])
    with open(os.path.join(out_dir, "meta.txt"), "w") as f:
        f.write(f"robot={args.robot}\nrobot_type={robot_type}\nscene={args.scene}\n")
        f.write(
            f"map={map_path}\npose={pose}\npose_source={pose_source}\n"
            f"motion_model={motion}\n"
        )
        f.write(f"lidar_xyz={lidar_xyz}\n")
        f.write(f"lidar_rpy={lidar_rpy}\n")
        f.write(f"xy_goal_tolerance={params_subs['@@XY_GOAL_TOLERANCE@@']}\n")
        f.write(f"yaw_goal_tolerance={params_subs['@@YAW_GOAL_TOLERANCE@@']}\n")
        f.write(f"inflation_radius={params_subs['@@INFLATION_RADIUS@@']}\n")

    print(f"[nav2_setup] ✅ 已生成 Nav2 配置到 {out_dir}")
    print(f"  机器人={args.robot} 类型={robot_type} 场景={args.scene}")
    print(f"  运动模型={prof['motion_model']} 半径={prof['robot_radius']} "
          f"雷达偏移={lidar_xyz} 雷达姿态={lidar_rpy}")
    print(f"  地图={map_path or '（无，需 --map 或先建图）'}")
    print(f"  初始位姿={pose}")
    # 把关键值输出成 KEY=VALUE 供 launch/脚本解析（最后几行固定格式）
    print(f"PARAMS={params_out}")
    print(f"PC2SCAN={pc2scan_out}")
    print(f"RVIZ={rviz_out}")
    print(f"LIDAR_XYZ={lidar_xyz[0]},{lidar_xyz[1]},{lidar_xyz[2]}")
    print(f"LIDAR_RPY={lidar_rpy[0]},{lidar_rpy[1]},{lidar_rpy[2]}")
    print(f"POSE_SOURCE={pose_source}")
    print(f"MAP={map_path or ''}")


if __name__ == "__main__":
    main()
