# EAI 仿真器 + Nav2 自主导航

在 Factory 场景中用 Nav2 对仿真机器人做自主导航。仿真侧发布 odometry/点云/相机并订阅
`/<robot>/cmd_vel`，本目录补齐 Nav2 需要的 TF、激光扫描、定位与导航栈。

## 架构

```
Isaac Sim (GUI, conda env_isaaclab)          系统 ROS2 (humble)
  simulator.py --env=nav2                      nav2.launch.py
  └─ GSHub 发布:                                 ├─ tf_bridge.py
     /carter_1/odometry (mapping_init→base_link)  │   odom→base_link (动态)
     /carter_1/cloud   (frame=mapping_init)        │   base_link→lidar_link (静态)
     /carter_1/GS_Hub_{L,R}_cam                     │   点云重发布 frame=lidar_link → /scan_cloud
  └─ 订阅:                                        ├─ pointcloud_to_laserscan: /scan_cloud→/scan
     /carter_1/cmd_vel ◄──────────────────────┤─ map_server: factory_map.yaml
                                                 ├─ amcl: map→odom (活动仿真实际 root pose)
                                                 └─ Nav2: planner/controller/bt/behavior/smoother
                                                     controller→cmd_vel_nav→velocity_smoother→/carter_1/cmd_vel
```

## 运行

### Factory + Carter + GS-Hub 示例
```bash
# 终端 1：仿真（必须 GUI 模式，见下方注意）
conda activate env_isaaclab
python simulator.py --env=nav2 --num_envs=1 --device=cuda:0

# 终端 2：Nav2 栈和 RViz（系统 ROS2）
source /opt/ros/humble/setup.bash
cd ~/eai-simulator
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory rviz:=true

# 终端 3：发送导航目标（系统 ROS2）
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -5.0 --y -8.0
```

`nav2` 位于 `source/EAI_hmrs/EAI_hmrs/envs/nav2.json`。它选择 Factory 场景和 Carter，并挂载 GS-Hub 与 ROS tool。Builder 生成的实例名是 `carter_1`，Nav2 的 `robot_name` 必须保持一致。

Nav2 启动时会读取 `tmp/runtime_interfaces.json`，使用活动仿真中对应机器人的实际世界位姿初始化 AMCL。因此 JSON/Env DIY 3D 自定义位置和 Builder 自动排列位置都不需要另行登记。

### 一键启动（推荐，含自动清理）
```bash
bash algorithm/ros/nav2/run_nav2.sh          # 启动仿真+Nav2
bash algorithm/ros/nav2/run_nav2.sh --rviz   # 额外启动 RViz 可视化
# Ctrl+C 会自动杀掉仿真+Nav2+RViz，释放 GPU 内存
```

### 手动分步
```bash
# 终端 1：仿真（必须 GUI 模式，见下方注意）
conda activate env_isaaclab
python simulator.py --env=nav2

# 终端 2：Nav2 栈（系统 ROS2）
source /opt/ros/humble/setup.bash
cd ~/eai-simulator
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory           # 不带 RViz
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory rviz:=true # 带 RViz

# 终端 3：发送导航目标（系统 ROS2）
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -7.97 --y -6.53
```

成功时终端 2 会打印 `Reached the goal!`，终端 3 打印 `导航结束，状态码: 4`(SUCCEEDED)。

### RViz 可视化

```bash
# 方式 A：随 Nav2 一起启动
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory rviz:=true

# 方式 B：单独启动（Nav2 已在跑时）
source /opt/ros/humble/setup.bash
ros2 launch algorithm/ros/nav2/rviz.launch.py
```

RViz 里能看到：地图、全局/局部 costmap、全局/局部路径、激光扫描(`/carter_1/scan`)、
AMCL 粒子云、TF 树、机器人足迹。可用工具栏：
- **2D Pose Estimate** — 手动设/纠正初始位姿（发到 `/initialpose`）
- **Nav2 Goal / 2D Goal Pose** — 鼠标点目标点（发到 `/goal_pose`，比命令行更直观）

RViz 配置由 `nav2_setup.py` 从 `nav2_view.template.rviz` 生成到 `/tmp/eai_nav2_<robot>/view.rviz`（Fixed Frame = `map`，scan 话题自动带机器人命名空间）。没有机器人 URDF，所以 RobotModel 显示为空属正常，用 TF 显示看机器人位姿即可。

## GSHub 专用点云处理

GSHub/Mid360 的 ROS 输出不能直接交给 Nav2 使用，本目录把专用处理收口在
`tf_bridge.py`、`nav2_profiles.yaml` 和 `pointcloud_to_laserscan.template.yaml`：

- GSHub 发布的 odometry frame 是 `mapping_init`，点云 frame 也写成 `mapping_init`，但点云数值实际是 Mid360 传感器坐标。
- `tf_bridge.py` 把 odometry 语义接成标准 TF `odom -> base_link`，并把点云重发到 `/<robot>/scan_cloud`，frame 改为 `lidar_link`。
- Carter 上的 GSHub 前向 Mid360 有下倾姿态，`nav2_profiles.yaml` 为 Carter 记录 `lidar_rpy: [0.0, 0.339, 0.0]`。
- `pointcloud_to_laserscan` 使用 `target_frame: base_link` 后再做高度切片，避免把下倾雷达看到的地面投成车前横向假障碍。
- `scan_range_min` 至少覆盖车体半径，用来过滤近距离自体/安装结构回波。

调试时优先看这两个处理后的话题：

```bash
ros2 topic echo --once /<robot>/scan --field ranges
ros2 topic info /<robot>/scan -v
```

RViz 里如果只检查传感器干净程度，Fixed Frame 可临时设为 `base_link` 或 `odom`，
打开 `/<robot>/scan` 和 TF；不要用原始 `/<robot>/cloud` 判断 Nav2 障碍，因为原始点云包含地面/天花板等全量回波。
如果 Nav2 lifecycle 已 reset/unconfigured，global/local costmap 可能保留旧显示或缺少 `map -> base_link`，
这时也不要用 costmap 判断点云是否干净，先重启 Nav2 或只看 `/scan`。

## ⚠️ 重要注意事项

1. **必须 GUI 模式**：`--headless` 会让 GSHub 停止发布所有传感器/里程计（ROS2 发布图依赖渲染管线）；`--headless --enable_cameras` 会崩溃（Prim is not a Lidar）。所以仿真只能带界面跑。不用时请及时 Ctrl+C / `run_nav2.sh` 自动清理，避免占显存。

2. **目标点必须在自由空间**：地图里墙/障碍是致命代价，把目标设在墙里或膨胀区内规划器会失败（`failed to create plan`）。可用完全空闲的点，例如 `(-7.97,-6.53)`、`(0.0,0.0)`。机器人位置以当前运行时快照为准。

3. **系统 vs conda python**：发目标、手动跑 ROS 脚本一律用系统环境（`source /opt/ros/humble/setup.bash` + `/usr/bin/python3`）；conda 里没有 ROS Humble 可用的 rclpy。

4. **崩溃后清显存**：仿真被强杀后可能残留占显存的进程，重启前 `pkill -9 -f keyboard.py`。

## 文件说明

| 文件 | 作用 |
|------|------|
| `nav2_profiles.yaml` | **机器人/场景映射表**：每种机器人的物理/运动参数和场景→地图。拓展入口 |
| `nav2_setup.py` | 生成器：读运行时快照、profiles 和模板 → 生成具体的 params/pc2scan/rviz 到 `/tmp/eai_nav2_<robot>/` |
| `nav2_params.template.yaml` | Nav2 参数模板（`@@占位符@@` 由 nav2_setup 替换）|
| `pointcloud_to_laserscan.template.yaml` | 点云切激光模板（高度切片按机器人参数化）|
| `nav2_view.template.rviz` | RViz 显示模板（scan 话题命名空间参数化）|
| `tf_bridge.py` | 补 TF（odom→base_link、base_link→lidar_link），点云 frame 重写为 lidar_link |
| `nav2.launch.py` | 统一 Nav2 入口：按 robot_name/robot_type/scene 编排全部节点（先调 nav2_setup 生成配置，再拉起 tf_bridge+pc2scan+map+amcl+Nav2）|
| `carter_nav2.launch.py` | 旧命令兼容 wrapper，内部转发到 `nav2.launch.py` |
| `rviz.launch.py` | 单独启动 RViz |
| `send_goal.py` | 发送 NavigateToPose 目标 |
| `run_nav2.sh` | 一键启动 carter+factory + Ctrl+C 自动清理（`--rviz` 带可视化）|

## 换机器人 / 换场景（可拓展）

导航配置按「机器人类型 + 场景」自动生成，不再写死 carter+factory：

```bash
# 换机器人（例如 Env DIY 临时生成的 lite3_1）
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=lite3_1 robot_type:=Lite3 scene:=factory rviz:=true

# 换场景（例如交互选择出的 go2_1 + plane）
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=go2_1 robot_type:=Go2 scene:=plane

# 显式指定地图 / 初始位姿
ros2 launch algorithm/ros/nav2/nav2.launch.py \
    robot_name:=carter_1 robot_type:=Carter map:=/abs/x.yaml pose:=-3.0,0.0,0.0
```

launch 参数：
- `robot_name` — 机器人实例名 = ROS 话题命名空间（Env DIY 按类型生成 `carter_1`/`go2_1`；`nav2.json` 生成 `carter_1`）
- `robot` — 旧参数别名；新命令优先用 `robot_name`
- `robot_type` — 机器人类型，查 `nav2_profiles.yaml` 的物理参数（Carter/Go2/B2/Scout…）。空则按实例名首段猜测
- `scene` — 场景名，用于选择地图并校验活动仿真场景
- `map` — 可选显式地图 yaml，覆盖场景地图表
- `pose` — 可选显式初始位姿 `x,y,yaw`；未指定时从活动仿真的运行时快照读取机器人实际世界位姿
- `runtime_snapshot` — 运行时快照路径，默认 `tmp/runtime_interfaces.json`
- `rviz` — `true` 时同时开 RViz

AMCL 位姿优先级为：显式 `pose:=x,y,yaw`，然后是活动仿真的实际 root pose。自动模式要求快照心跳不超过 5 秒，且 PID、场景和机器人实例均匹配。验证失败时 Nav2 会停止启动，不会回退到静态出生点。

### 新增一个机器人/场景

编辑 `nav2_profiles.yaml`：
- 新机器人：在 `robot_profiles` 加一条（键 = 控制器 cfg 的 `robot_type` 字符串），填 `motion_model`（differential/omni）、`robot_radius`、`lidar_xyz`、速度/加速度、`scan_z_min/max`
- 新场景地图：在 `scene_maps` 加 `<场景>: <地图yaml相对路径>`（需要先有 2D 占用地图）

### 当前限制

- **factory 有现成占用地图；plane 会自动生成空白占用图**。其他场景（warehouse/airs/garden/desert）暂无 `*_map.yaml`，需先离线生成占用图或用 slam_toolbox 边走边建，再登记到 `scene_maps`。
- **一键脚本 `run_nav2.sh` 默认跑 `nav2.json` 中的 Carter + Factory 配置**。其他机器人或场景通过 Env DIY 临时生成 JSON，不在仓库中保留组合样例。
