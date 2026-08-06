# 环境说明

EAI 仿真环境采用 **JSON 配置 + 通用 Builder**。

## 配置位置

所有可启动环境都位于：

```text
source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json
```

推荐先运行仓库自带的 `robo` 环境：

```bash
python simulator.py --env robo
```

加载的是：

```text
source/EAI_hmrs/EAI_hmrs/envs/robo.json
```

`--env` 只传文件名，不包含 `.json` 后缀。名称可包含字母、数字、下划线和连字符。

`robo.json` 包含 human 和当前支持的其他机器人，并为每个对象启用键盘控制。human 存在时，仿真器会自动使用 CPU PhysX；不含 human 的环境仍可通过 `--device=cuda:0` 使用 GPU。

仓库内置环境：

| 名称 | 用途 |
|---|---|
| `robo` | 全部机器人与 human 的综合快速验证环境 |
| `keyboard` | Carter 最小键盘控制环境 |
| `nav2` | Factory + Carter Nav2 示例 |
| `EAI-Factory-v0` | 利用EAI仿真器实现复杂实验demo |

## 加载流程

```text
--env=<env_name>
  → source/EAI/EAI/hmrs_env/env_diy/storage.py 读取 JSON
  → source/EAI/EAI/hmrs_env/env_diy/flow.py 转换配置对象
  → EAI_hmrs/env_builder.py 构建场景、机器人和附件配置
  → EAI.hmrs_env.MultiRobotDirectEnv 创建仿真环境
```

`simulator.py` 是统一入口。外部 demo 应通过 `SimulatorLaunchConfig` 和 `open_simulator_session()` 启动环境，不应自行复制环境构建逻辑。

## JSON 结构

最小配置示例：

```json
{
  "version": 1,
  "task_name": "my_factory_env",
  "scene_key": "factory",
  "robots": [
    {
      "type": "scout",
      "controller": {
        "mode": "default",
        "cfg": "SCOUT_DIFF_CFG"
      },
      "visual": {
        "x": 0.5,
        "y": 0.5
      },
      "attachments": [
        {"type": "gshub", "controller": null},
        {"type": "ros", "controller": null}
      ]
    }
  ]
}
```

主要字段：

| 字段 | 说明 |
|---|---|
| `version` | JSON schema 版本，当前为 `1` |
| `task_name` | 环境保存名称 |
| `scene_key` | 场景类型，例如 `factory`、`plane` |
| `robots` | 机器人列表，顺序决定默认实例编号 |
| `controller` | 机器人控制器配置 |
| `attachments` | 宿主机器人的 payload：机械臂、传感器或工具 |
| `visual` | Env DIY 界面中的布局位置，不是仿真出生坐标 |
| `spawn_pose` | 可选仿真出生位姿 |

### 可选机器人初始位置

```json
"spawn_pose": {
  "position": [1.0, 2.0, 0.5],
  "rotation": [1.0, 0.0, 0.0, 0.0]
}
```

- `position` 为世界坐标 `[x, y, z]`。
- `rotation` 为四元数 `[w, x, y, z]`。
- 未提供 `spawn_pose` 时，Builder 使用通用默认排列。
- `python simulator.py --diy-3d` 会为每个 3D 编辑的宿主机器人写入完整的 `position` 与 `rotation`；两者缺一或向量长度不正确时会拒绝保存。
- demo 若需要实验专用初始位置，应通过 `SimulatorLaunchConfig.env_cfg_hook` 注入，不应修改通用 JSON。

## 实例名称

Builder 按机器人类型和出现顺序生成实例名：

```text
carter_1
m20_1
m20_2
scout_1
```

外部算法、ROS topic、附件控制器和 demo 配置必须使用这些实例名。

## Payloads：机械臂、传感器与工具

Env DIY 使用以下层级组织可挂载对象：

```text
Scenes
Robots                         # 宿主机器人
Payloads
  ├── Manipulators              # UR5、Z1
  └── Sensors                   # GS-Hub、LiDAR
Tools                          # ROS、Keyboard
```

UR5 和 Z1 是必须安装在宿主机器人上的机械臂，不是传感器，也不是可以独立生成的机器人。在 Env DIY 中可以为 Carter、Go2、B2、M20、Scout 和 Lite3 添加 `ur5` 或 `z1` payload。同一机器人上 UR5 和 Z1 不能同时挂载；UI、JSON 解析、存储加载和 Builder 都会检查这一互斥规则。

Builder 根据机器人类型选择 mount profile，把机械臂创建为独立 `<robot>_arm` articulation，再通过通用 FixedJoint 固定到宿主，并自动加载 `UR5_IK_CFG` 或 `Z1_IK_CFG`。模拟器只为实际挂载实例创建对应的 ROS2 OmniGraph。UR5 提供：

```text
/<robot>/ur5/target_pose
/<robot>/ur5/joint_command
/<robot>/ur5/joint_states
/<robot>/ur5/ee_pose
```

Z1 另外提供独立夹爪接口：

```text
/<robot>/z1/target_pose
/<robot>/z1/joint_command
/<robot>/z1/joint_states
/<robot>/z1/ee_pose
/<robot>/z1/gripper_command
/<robot>/z1/gripper_state
```

例如 Go2、B2 和两台 M20 都挂载 UR5 时，接口分别位于 `/go2_1/ur5/*`、`/b2_1/ur5/*`、`/m20_1/ur5/*`、`/m20_2/ur5/*`。控制器根据场景中实际注册的机器人动态工作，不限制实例数量，也不依赖硬编码机器人名单生成 topic。

通用物理挂载原语定义在 `source/EAI_assets/EAI_assets/robots/manipulator_mount.py`，UR5/Z1 的宿主 profile 分别位于 `ur5_mount.py` 和 `z1_mount.py`。不同宿主只配置安装刚体、局部安装位姿、质量/惯量比例和 self-collision；扩展新宿主时应新增 profile，不要复制整套 spawn 函数。

`ur5` 或 `z1` 附件本身即可启用机械臂 topic，不需要额外添加 `ros` 附件。`ros` 附件主要用于启用底盘的 `/<robot>/cmd_vel`。

完整消息格式、控制命令和状态读取方式参见[机械臂](ur5_control.md)。

## Env DIY

不传 `--env` 时进入 Env DIY：

```bash
python simulator.py --device=cuda:0
```

可视化窗口和终端快速模式使用相同的选择顺序：`Scenes → Robots → Payloads → Tools`。终端模式在 Payloads 中先选择 Manipulators，再选择 Sensors；Isaac Sim 3D 扩展默认停靠在右侧面板，`Payloads` 中使用 Manipulators/Sensors 两个分组。底层 JSON 仍使用 `robots[].attachments[]` 保存 payload，以兼容已有环境文件。

完成选择后可直接运行，也可保存到 `source/EAI_hmrs/EAI_hmrs/envs/`。再次启动时使用保存名称：

```bash
python simulator.py --env=<env_name> --device=cuda:0
```

### Isaac Sim 3D 运行前编辑

`--diy-3d` 是 Env DIY 的三维入口，与默认的可视化窗口和终端快速方式并行：

```bash
python simulator.py --diy-3d --device=cuda:0
```

> **持续优化中**：该入口适合开发、资产验证和控制器联调。插件布局、资产目录、下载状态和部分控制器接口可能随版本调整，建议每次运行前保留导出的 selection JSON。

插件会在 Isaac Sim 启动后停靠在右侧面板。选择 `Scenes`、`Robots`、`Payloads` 和 `Tools` 后，可以在 Viewport 中用 transform gizmo 或数值字段编辑真实三维位置；`Snap` 使用碰撞几何吸附到表面，`spawn_pose` 中的高度和旋转会写入正式环境。浏览器教程只展示对象所属关系，其中导出的 `visual.x/y` 是兼容占位值；轻量窗口的 `visual.x/y` 只表示 2D 布局。两者都不代表物理出生位置。

UR5/Z1 属于 `Payloads → Manipulators`，必须挂载到兼容宿主，不能作为独立机器人拖动。移动宿主时附件随宿主移动；机械臂控制器和 ROS2 topic 的使用方式见[机械臂](ur5_control.md)。

运行前可在卡片上单项下载资产，也可以使用 `Download all and run` 一次性准备当前 selection 所需的 USD、材质、纹理和 controller cfg。gated Hugging Face 资产通过 `Request`、终端 `hf auth login` 和 `Recheck` 完成授权，插件不接收或保存 token。

点击 `Run` 后，程序只使用一个 Isaac Sim AppLauncher：先销毁预览 Stage，再在同一个 Kit 进程中创建正式环境。某个机器人或附件生成失败时，其他已经成功的对象会保留；修正或下载依赖后可在原编辑器中重试。当前阶段只支持仿真运行前编辑，运行中的动态增删和移动属于后续功能。

交互式浏览器教程位于 <a href="env_diy_tutorial.html">Env DIY 工作台</a>。工作台通过 `Scene → Robot → Payload → Tool → Controller` 环境谱系引导配置并导出 selection JSON；如需编辑真实三维位置，请使用上述 `--diy-3d` 入口。

## 外部 Demo 接口

```python
from simulator import SimulatorLaunchConfig, open_simulator_session

launch = SimulatorLaunchConfig(
    env="EAI-Factory-v0",
    device="cuda:0",
    num_envs=1,
)

with open_simulator_session(launch) as session:
    env = session.env
    while session.simulation_app.is_running():
        # 生成 actions，然后调用 env.step(actions)
        pass
```

如需修改机器人初始位置：

```python
def configure_env(env_cfg):
    env_cfg.scene.robots["scout_1"].init_state.pos = (6.0, 5.5, 0.2)

launch = SimulatorLaunchConfig(
    env="EAI-Factory-v0",
    env_cfg_hook=configure_env,
)
```

具体 demo 参考 `demo/fire_rescue/README.md`。

## 添加新环境

推荐使用 Env DIY 生成 JSON。手工编写时也必须满足当前 schema，并确保所引用的机器人、控制器和附件配置能被 `EAI_hmrs/env_builder.py` 与 `EAI_hmrs/controller_loader.py` 解析。

新增后直接验证：

```bash
python simulator.py --env=<new_env_name> --device=cuda:0
```
