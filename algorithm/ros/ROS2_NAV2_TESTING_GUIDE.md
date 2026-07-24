# ROS2 Nav2 导航测试指南

## 📋 快速开始

### 方法 1：基础 cmd_vel 测试（推荐首次测试）

#### 终端 1：启动仿真器
```bash
cd .
conda activate env_isaaclab

# Carter 小车
python algorithm/ros/tools/ros2_nav2_test.py --robot carter

# 或 Go2 四足机器人
python algorithm/ros/tools/ros2_nav2_test.py --robot go2

# 或 Scout 平台
python algorithm/ros/tools/ros2_nav2_test.py --robot scout

# 或 B2 人形机器人
python algorithm/ros/tools/ros2_nav2_test.py --robot b2
```

#### 终端 2：查看 ROS2 话题
```bash
# 查看所有话题
ros2 topic list

# 过滤导航相关话题
ros2 topic list | grep nav

# 应该看到:
# /carter_1/cmd_vel
# /carter_1/GS_Hub_L_cam
# /carter_1/GS_Hub_R_cam
# /carter_1/cloud
# /carter_1/odom
```

#### 终端 3：发送控制命令
```bash
# 方式 A：使用测试脚本（推荐）
python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --linear 0.5

# 方式 B：使用 ros2 命令行
ros2 topic pub --once /carter_1/cmd_vel geometry_msgs/msg/Twist \
    '{linear: {x: 0.5, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}'

# 持续前进（10Hz）
python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --linear 0.5 --rate 10

# 左转
python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --angular 0.5 --rate 10

# 前进 + 左转（画圆）
python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --linear 0.5 --angular 0.3 --rate 10
```

#### 终端 4：可视化传感器数据
```bash
# 查看相机和点云
python algorithm/ros/tools/vis_sensors.py --namespace /carter_1
```

---

## 🗺️ 方法 2：Nav2 完整导航栈（高级）

### 前置条件
```bash
# 安装 Nav2
sudo apt install ros-humble-navigation2 ros-humble-nav2-bringup

# 验证安装
ros2 pkg list | grep nav2
```

### 步骤 1：启动仿真器（终端 1）
```bash
cd .
conda activate env_isaaclab
python algorithm/ros/tools/ros2_nav2_test.py --robot carter
```

### 步骤 2：启动 Nav2（终端 2）
```bash
# 设置地图路径
export MAP_PATH=usd/scene/factory/factory_map.yaml

# 启动 Nav2（使用默认参数）
ros2 launch nav2_bringup navigation_launch.py \
    use_sim_time:=True \
    map:=$MAP_PATH

# 或者使用自定义参数（待创建）
# ros2 launch nav2_bringup navigation_launch.py \
#     use_sim_time:=True \
#     map:=$MAP_PATH \
#     params_file:=/tmp/eai_nav2_carter_1/nav2_params.yaml
```

### 步骤 3：设置初始位姿（终端 3）
```bash
# 使用 RViz2 设置初始位姿
ros2 run rviz2 rviz2

# 在 RViz2 中:
# 1. Add -> By topic -> /map -> Map
# 2. Add -> By topic -> /scan 或 /cloud -> PointCloud2
# 3. 使用 "2D Pose Estimate" 工具点击地图设置初始位姿
```

### 步骤 4：发送导航目标
```bash
# 方式 A：在 RViz2 中使用 "2D Nav Goal" 工具点击目标位置

# 方式 B：通过命令行发送目标
ros2 topic pub /goal_pose geometry_msgs/msg/PoseStamped \
    '{header: {frame_id: "map"}, pose: {position: {x: 0.0, y: 0.0, z: 0.0}}}'

# 方式 C：使用 Action（更完整）
ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose \
    "{pose: {header: {frame_id: 'map'}, pose: {position: {x: 0.0, y: 0.0, z: 0.0}}}}"
```

---

## 📊 传感器数据说明

### 发布的话题

#### 1. 相机图像
- **左相机**: `/{robot_name}/GS_Hub_L_cam` (sensor_msgs/Image)
- **右相机**: `/{robot_name}/GS_Hub_R_cam` (sensor_msgs/Image)
- **分辨率**: 取决于 GSHub 配置
- **编码**: rgb8

#### 2. 点云数据
- **话题**: `/{robot_name}/cloud` (sensor_msgs/PointCloud2)
- **用途**: 障碍物检测、代价地图构建
- **频率**: 与仿真步频一致

#### 3. 里程计
- **话题**: `/{robot_name}/odom` (nav_msgs/Odometry)
- **坐标系**: odom -> base_link
- **用途**: 定位、速度估计

#### 4. 控制输入（订阅）
- **话题**: `/{robot_name}/cmd_vel` (geometry_msgs/Twist)
- **格式**:
  - `linear.x`: 前进速度 (m/s)
  - `linear.y`: 横向速度 (m/s, 差速机器人忽略)
  - `angular.z`: 旋转速度 (rad/s)

---

## 🤖 机器人参数

### Carter 小车
```bash
python algorithm/ros/tools/ros2_nav2_test.py --robot carter
# 默认位置: (-7.6, -8.0, 0.0)
# 类型: 差速驱动
# 传感器: GSHub (双目相机 + 激光雷达)
# 最大速度: ~1.0 m/s
```

### Scout 平台
```bash
python algorithm/ros/tools/ros2_nav2_test.py --robot scout
# 默认位置: (-7.6, -8.0, 0.2)
# 类型: 差速驱动（更大平台）
# 最大速度: ~0.8 m/s
```

### Go2 四足机器人
```bash
python algorithm/ros/tools/ros2_nav2_test.py --robot go2
# 默认位置: (-7.6, -8.0, 0.4)
# 类型: 四足（使用 RSL RL 控制器）
# 最大速度: ~1.5 m/s
```

### B2 人形机器人
```bash
python algorithm/ros/tools/ros2_nav2_test.py --robot b2
# 默认位置: (-7.6, -8.0, 0.85)
# 类型: 人形（使用 RSL RL 控制器）
# 最大速度: ~1.0 m/s
```

---

## 🎯 自定义生成位置

```bash
# 指定初始位置
python algorithm/ros/tools/ros2_nav2_test.py \
    --robot carter \
    --spawn_x -5.0 \
    --spawn_y -5.0 \
    --spawn_z 0.0
```

### Factory 地图坐标参考
- **地图尺寸**: 约 20m x 20m
- **原点**: (-10.975, -10.125, 0.0)
- **分辨率**: 0.05 m/pixel
- **安全区域**: 
  - X: -10.0 到 10.0
  - Y: -10.0 到 10.0

---

## 🔧 故障排除

### 问题 1：无法看到 ROS2 话题
**症状**：`ros2 topic list` 不显示 `/carter_1/*` 话题

**解决方案**：
1. 确认仿真器已启动并看到 "Nav2 Bridge 已启动" 消息
2. 检查 ROS2 域 ID：`echo $ROS_DOMAIN_ID`
3. 确认 ROS2 环境已 source：`source /opt/ros/humble/setup.bash`
4. 检查 DDS 配置：`ros2 doctor --report`

### 问题 2：机器人不响应 cmd_vel
**症状**：发布命令后机器人不移动

**检查步骤**：
1. 确认话题名称正确：`ros2 topic info /carter_1/cmd_vel`
2. 查看是否有订阅者：应该看到 1 个订阅者
3. 回显发布的消息：`ros2 topic echo /carter_1/cmd_vel`
4. 检查仿真器终端是否有错误消息

### 问题 3：Nav2 无法加载地图
**症状**：Nav2 启动时报告 "Failed to load map"

**解决方案**：
1. 确认地图文件存在：`ls usd/scene/factory/factory_map.*`
2. 检查 YAML 文件格式：`cat factory_map.yaml`
3. 确认 PNG 文件路径正确（相对于 YAML 文件）
4. 检查文件权限：`chmod 644 factory_map.*`

### 问题 4：点云数据看不见
**症状**：`ros2 topic echo /carter_1/cloud` 无输出

**检查步骤**：
1. 确认 GSHub 传感器已初始化：查看仿真器启动日志
2. 等待几秒（传感器初始化需要时间）
3. 检查话题频率：`ros2 topic hz /carter_1/cloud`
4. 如果频率为 0，重启仿真器

---

## 📈 性能优化

### 降低点云发布频率
如果点云数据量太大，可以在 GSHub 配置中调整发布频率。

### 使用无头模式
```bash
python algorithm/ros/tools/ros2_nav2_test.py --robot carter --headless
```

### 调整仿真步长
在脚本中修改 `env_cfg.sim.dt`（需要修改代码）。

---

## 🎓 进阶使用

### 多机器人协同（未来支持）
```bash
# 终端 1: Carter
python algorithm/ros/tools/ros2_nav2_test.py --robot carter --spawn_x -5 --spawn_y -5

# 终端 2: Go2（需要修改为支持多实例）
# python algorithm/ros/tools/ros2_nav2_test.py --robot go2 --spawn_x 5 --spawn_y 5
```

### 自定义 Nav2 参数
修改 `algorithm/ros/nav2/nav2_params.template.yaml` 或
`algorithm/ros/nav2/nav2_profiles.yaml`，然后运行 `nav2_setup.py` 重新生成
`/tmp/eai_nav2_<robot>/nav2_params.yaml`。

### 集成 SLAM
```bash
# 使用 SLAM Toolbox 构建地图
ros2 launch slam_toolbox online_async_launch.py \
    use_sim_time:=True
```

---

## 📚 相关文件

- **仿真器脚本**: `algorithm/ros/tools/ros2_nav2_test.py`
- **测试脚本**: `algorithm/ros/tools/ros2_send_cmd_vel.py`
- **桥接类**: `source/EAI/EAI/hmrs_ros/cmd_vel_bridge.py`
- **地图文件**: `usd/scene/factory/factory_map.{yaml,png}`
- **传感器可视化**: `algorithm/ros/tools/vis_sensors.py`

---

## 🎉 完成！

现在你已经可以：
1. ✅ 在 Factory 场景中加载任意机器人
2. ✅ 通过 ROS2 cmd_vel 控制机器人移动
3. ✅ 接收传感器数据（相机、点云、里程计）
4. ✅ 集成 Nav2 导航栈进行自主导航

如有问题，请查看故障排除部分或提交 Issue。
