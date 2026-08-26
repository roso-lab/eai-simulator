# EAI Simulator + Nav2

本目录提供 EAI Simulator 的可运行 Nav2 集成。Simulator 发布里程计和点云，并接收 /<robot>/cmd_vel；本目录负责生成机器人相关 Nav2 配置、补全 TF、把点云转换成 laser scan，并启动定位和导航。

所有命令从仓库根目录执行。Isaac Sim 使用 env_isaaclab；Nav2、RViz、ROS CLI、send_goal.py 和 tf_bridge.py 使用所选系统 ROS 的 Python。两套环境必须分开。tracked 的 nav2 环境选择 Factory 场景、carter_1、Orsus 和 Navigation I/O。Orsus 与 RTX LiDAR 发布需要 GUI rendering，因此不是 headless smoke test。

## 一键启动

```bash
bash algorithm/nav2/run_nav2.sh
bash algorithm/nav2/run_nav2.sh --rviz
```

脚本启动 simulator.py --env=nav2，等待 cmd_vel bridge，再以所选 ROS distribution 启动 Nav2。清理逻辑只向脚本创建的进程组发送信号，不要使用宽泛的 pkill。日志和生成配置写入权限为 0700 的临时目录。

## 手动启动

在 Conda 终端启动 Isaac Sim：

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python simulator.py --env=nav2
```

在单独的系统 ROS 终端启动 Nav2：

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch algorithm/nav2/nav2.launch.py \\
  robot_name:=carter_1 robot_type:=Carter sensor:=auto scene:=factory rviz:=true
```

发送目标：

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
/usr/bin/python3 algorithm/nav2/send_goal.py --x -5.0 --y -8.0
```

send_goal.py 只有在 Nav2 返回 STATUS_SUCCEEDED 时返回 0；server 不可用、拒绝、取消、中止或超时返回非零。默认 goal response/result 等待时间为 10/300 秒，可覆盖。

## 地图、传感器和位姿

`nav2_profiles.yaml` 将全部 7 个可选场景映射到 `EAI_USD_ROOT`（默认 `<repo>/usd`）下由 provider 管理的 `scene/<scene>/<scene>_map.yaml`。应先通过 Simulator/Env DIY 资产预检下载场景；Nav2 会检查 YAML 及其引用图片，不再自行生成 Plane 地图。需要自定义地图时可传 `map:=/absolute/path/to/map.yaml`。`sensor:=auto` 从 `tmp/runtime_interfaces.json` 读取 `robot_name` 对应的唯一 Orsus 或 lidar attachment；不传 `pose` 时，同一 snapshot 提供 AMCL 初始位姿。snapshot 必须是版本 1、PID 存活、时间不超过 5 秒且 scene/robot 匹配。也可以明确传 `sensor:=orsus` 或 `lidar`，以及 `pose:=x,y,yaw` 来绕过自动检查。不要为同一机器人同时开启 Orsus 和 LiDAR publisher，因为两者都使用 `cloud` 和 `odometry` topic。

## TF 和点云

tf_bridge.py 将里程计发布为动态 odom -> base_link，发布 base_link -> lidar_link，并把 cloud 转发到 scan_cloud。pointcloud_to_laserscan 将点云转换到 base_link、应用 profile 过滤并发布 scan；AMCL 负责 map -> odom。

## 参数和生成文件

nav2.launch.py 接收 robot_name、robot_type、sensor、scene、map、pose、runtime_snapshot 和 rviz。nav2_setup.py 默认在 owner-private 临时目录生成 nav2_params.yaml、pointcloud_to_laserscan.yaml、view.rviz 和 meta.txt。生成文件是运行时输出，不要提交 Git。
