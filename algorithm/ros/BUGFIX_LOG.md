# 🔧 修复记录 - ROS2 Nav2 测试脚本

## 问题 1: 参数冲突
**错误信息**:
```
ValueError: The passed ArgParser object already has the field 'device'
```

**原因**: 
`AppLauncher.add_app_launcher_args()` 已经添加了 `--device` 和 `--headless` 参数，我们的脚本重复定义了这些参数。

**修复**:
移除了自定义的 `--device` 和 `--headless` 参数，直接使用 `AppLauncher` 提供的。

## 问题 2: 模块导入顺序
**错误信息**:
```
ModuleNotFoundError: No module named 'torch'
```

**原因**:
在 `AppLauncher` 初始化之前导入了 `torch`，此时 Isaac Sim 环境尚未设置。

**修复**:
调整导入顺序：
1. 先解析参数
2. 初始化 `AppLauncher`
3. 启用 ROS2 Bridge
4. 最后导入 `torch` 和其他依赖模块

## 问题 3: 缺少 sim_utils 导入
**原因**:
在场景配置中使用了 `sim_utils.DomeLightCfg` 但未导入。

**修复**:
添加 `import isaaclab.sim as sim_utils`

---

## ✅ 修复后的脚本结构

```python
# 1. 标准库导入
import argparse
import sys
from pathlib import Path

# 2. 添加项目路径
repo_root = Path(__file__).resolve().parents[2]
for rel in ("source/EAI", "source/EAI_assets", "source/EAI_hmrs"):
    ...

# 3. 导入 AppLauncher（无需 torch）
from isaaclab.app import AppLauncher

# 4. 解析参数
parser = argparse.ArgumentParser(...)
parser.add_argument("--robot", ...)
parser.add_argument("--spawn_x", ...)
# 注意：不添加 --device, --headless

AppLauncher.add_app_launcher_args(parser)  # 这里会添加 --device 等
args = parser.parse_args()

# 5. 启动 Isaac Sim
app_launcher = AppLauncher(args)
simulation_app = app_launcher.app

# 6. 启用扩展
from isaacsim.core.utils.extensions import enable_extension
enable_extension("isaacsim.ros2.bridge")

# 7. 现在可以安全导入 torch 等模块
import torch
import gymnasium as gym
import isaaclab.sim as sim_utils
...
```

---

## 🚀 现在可以运行

```bash
cd .
conda activate env_isaaclab

# 基础测试
python algorithm/ros/tools/ros2_nav2_test.py --robot carter

# 指定设备和位置
python algorithm/ros/tools/ros2_nav2_test.py --robot carter --device cuda:0 --spawn_x -5.0

# 无头模式
python algorithm/ros/tools/ros2_nav2_test.py --robot carter --headless
```

---

## 📝 注意事项

1. **--device 参数**: 现在由 `AppLauncher` 提供，支持 `cuda:0`, `cpu` 等
2. **--headless 参数**: 现在由 `AppLauncher` 提供，是一个 flag（不需要值）
3. **自定义参数**: 只保留了机器人相关的参数（--robot, --spawn_x/y/z）

---

**修复日期**: 2026-07-02  
**状态**: ✅ 已修复并验证

---

## 问题 4: nav 任务 - ROS2 节点类型名过时（Isaac Sim 5.1）

**现象**: `og.Controller.edit()` 报 "Failed to wrap graph in node"，回退到 USD API 创建了一个**无功能**的 prim。

**根因**: 代码用的是旧节点类型名 `omni.isaac.ros2_bridge.ROS2SubscribeTwist`，Isaac Sim 5.1 已改名。

**修复**: 在 `twist_subscriber.py` 使用正确的类型名：
- `isaacsim.ros2.bridge.ROS2SubscribeTwist`
- `isaacsim.ros2.bridge.ROS2Context`
- `omni.graph.action.OnPlaybackTick`

从 `$HOME/isaacsim/source/extensions/isaacsim.ros2.bridge/nodes/Ogn*.ogn` 核对属性名。

## 问题 5: 订阅节点从不执行（缺少 execIn/context 连线）

**现象**: 话题订阅者存在（`Subscription count: 1`），但发布 cmd_vel 后机器人不动。

**根因**: 只创建了 `ROS2SubscribeTwist` 节点，没有驱动其 `execIn`，节点每帧都不执行。

**修复**: `create()` 重写为构建完整图：
```
OnPlaybackTick.tick → ROS2SubscribeTwist.execIn
ROS2Context.context → ROS2SubscribeTwist.context
```

## 问题 6: 读取运行时输出用错 API（关键）

**现象**: 节点执行了，但 `get_velocities()` 始终返回 (None, None) / 0。

**根因**: 用 `stage.GetPrimAtPath(path).GetAttribute("outputs:...").Get()` 只能读到 USD authored/default 值，读不到 OmniGraph 运行时计算结果（结果在 Fabric 缓存里）。而且属性名应为 `outputs:linearVelocity` / `outputs:angularVelocity`（不是 `outputs:linear`）。

**修复**: 改用 OmniGraph Controller API：
```python
attr = og.Controller.attribute(f"{node_path}.outputs:linearVelocity")
value = og.Controller.get(attr)
```

## ✅ 端到端验证结果

```
发布: ros2 topic pub /carter_1/cmd_vel {linear.x:0.5, angular.z:0.3}
仿真读取: [Keyboard] carter_1 (1/1) vx=0.50 vy=0.00 wz=0.30
停止命令: [Keyboard] carter_1 (1/1) vx=0.00 vy=0.00 wz=0.00
传感器: /carter_1/cloud @ ~4Hz, /carter_1/odometry @ ~4Hz
```

启动方式（GUI 模式）：
```bash
conda activate env_isaaclab
python simulator.py --env=nav2
```

**验证日期**: 2026-07-02  
**状态**: ✅ 端到端验证通过 - ROS2 cmd_vel 双向控制成功
