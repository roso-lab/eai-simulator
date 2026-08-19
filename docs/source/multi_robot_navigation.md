# 多机导航（db-CBS）

EAI 的多机导航组件为同一场景中的地面机器人统一生成无冲突轨迹，并在一个 `SimulatorSession` 内完成轨迹跟踪。默认规划后端是仓库内置的 db-CBS；调用方继续拥有 Isaac Sim 应用、环境和逐帧仿真循环。

该组件不启动 ROS、Nav2 或第二个模拟器进程。ROS/Nav2 仍可作为独立导航方案使用，但不是本工作流的依赖。

## 工作边界

- `algorithm/dbcbs/` 保存 db-CBS、定制 OMPL、dynoplan/dynobench、运动基元下载与校验元数据、原生构建脚本和同步轨迹运行时。
- `algorithm/multi_robot_navigation/` 负责从 EAI 场景读取机器人、选择占据栅格地图、提交规划、生成控制命令及提供可选的视口 UI。
- `algorithm/multi_robot_navigation/test/main.py` 负责启动一个 EAI 会话，并提供交互目标、显式目标和位置交换三种集成测试方式。
- `source/EAI_hmrs/EAI_hmrs/envs/dbcbs_slam_team.json` 是维护的 Factory 演示环境，包含 Carter、Scout 和 Go2。

当前组件只支持 `num_envs=1`。它默认排除 CF2X、Iris、Pegasus 等空中机器人，只管理场景中的地面 articulation。

## 运行流程

```text
EAI 场景选择 + 机器人当前位姿 + 每台机器人的目标点
        ↓
占据栅格地图 → 障碍物方框 + 规划坐标系
        ↓
db-CBS 对参与机器人进行批量冲突约束规划
        +
未分配目标的地面机器人作为静态占位障碍
        ↓
dynoplan 优化 → 同步的双积分器轨迹
        ↓
连续轨迹净空校验 → 逐帧速度/位置目标命令
        ↓
距离过近时全队保持 → 后台重新规划 → 继续执行
```

初始规划和运行时重规划都在单个后台 worker 中执行。规划期间 `compute_actions()` 返回保持命令，Isaac Sim 主循环不会被原生规划器阻塞。

## 首次构建

先按[安装指南](installation.md)准备 Isaac Lab 的 `env_isaaclab` 环境。db-CBS 原生目标还需要 CMake、C++17、Boost、Eigen、FCL、yaml-cpp 和 Crocoddyl。构建脚本只从当前 Conda 环境、`/opt/openrobots`、`/opt/ros/humble` 及已有加载路径解析这些库，不会安装系统软件包。

```bash
cd /home/airs/eai-simulator
conda activate env_isaaclab
python -m pip install --no-deps crocoddyl==2.0.2
EAI_DBCBS_BUILD_JOBS=8 algorithm/dbcbs/build_native.sh
```

生成的二进制位于 `algorithm/dbcbs/native/build/db_cbs`。整个 `native/build/` 目录是可重建的本地输出，不应提交到 Git。

构建脚本会校验本地 `double_integrator_0_sorted.msgpack`。文件缺失时，它只从上游 db-CBS README 指向的 TUB Cloud 共享下载这一个文件，且仅在大小与 SHA-256 都匹配后安装；已有文件只做离线校验。该 payload 是本地运行数据，不进入 Git 或 Git LFS。

## 交互式集成测试

启动维护的三机器人 Factory 场景：

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env dbcbs_slam_team --real-time
```

没有指定 `--goal` 或 `--exchange` 时，演示默认打开 `EAI Multi-Robot Navigation` 面板：

1. 在 Isaac Sim 视口中单击一台地面机器人。
2. 单击地面或其他可拾取表面，为该机器人设置世界坐标目标。
3. 为需要参与任务的其他机器人重复以上操作。
4. 单击 **Start Navigation**，一次提交所有已分配目标。

黄色圆环标识当前选择；每台机器人使用独立颜色显示目标和剩余路径。没有目标的地面机器人保持停止，并在规划中占据当前位置。

交互模式需要可见视口，不能与 `--headless` 一起使用。

## 命令行任务

使用 `--goal ROBOT:X,Y` 可以重复指定部分机器人：

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env dbcbs_slam_team \
  --goal carter_1:3.0,3.0 \
  --goal go2_1:-2.0,1.0 \
  --real-time
```

让所有地面机器人循环交换初始位置：

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env dbcbs_slam_team --exchange --real-time
```

非交互任务可加 `--headless`。`--max-seconds` 设置任务超时，`--hold-seconds` 设置完成后保留仿真的时间；这两个参数不改变规划器的超时。

## 地图

`from_session()` 根据环境 JSON 的 `scene_key` 自动选择 `algorithm/multi_robot_navigation/maps/` 下的地图。当前维护的键为：

```text
airs, desert, factory, garden, hospital, plane, warehouse
```

场景几何发生变化或使用自定义场景时，应显式传入匹配的地图：

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env my_env \
  --map-yaml /absolute/path/to/map.yaml \
  --goal carter_1:2.0,-1.0 \
  --real-time
```

地图使用常见的占据栅格 YAML 字段：`image`、`resolution`、`origin`、`occupied_thresh`、`free_thresh` 和 `negate`。图像和原点必须与 EAI 世界坐标一致；当前 db-CBS 转换只接受 `origin` 的 yaw 为零。

## 规划与安全语义

每台机器人都有平面包围半径。默认值按机器人类型解析，也可以通过 `dbcbs_robot_radii` 按类型或实例名覆盖。规划时还会加入 `dbcbs_safety_margin`：

```text
有效半径 = 机器人半径 + 安全边距
两机器人最小间距 = 左机器人有效半径 + 右机器人有效半径
```

同一批目标必须全部成功才会安装新轨迹。优化结果还会在线性插值后的连续时间段上检查两两净空；违反有效半径的结果会被拒绝，而不是直接下发。

执行过程中，任意任务机器人接近其他地面机器人时，全队先输出保持命令，再从当前位姿对原任务目标进行后台重规划。失败的重规划不会取消任务，会保留目标并按照重试间隔继续尝试。

`start_navigation()` 对 db-CBS 返回的是“已接受规划请求”，不是最终规划成功。最终状态应从 `state().planning` 和 `state().planning_error` 读取。

## 接入自己的会话

```python
from algorithm.multi_robot_navigation.eai_plugin import (
    EaiMultiRobotNavigationPlugin,
)
from simulator import SimulatorLaunchConfig, open_simulator_session

config = SimulatorLaunchConfig(
    env="dbcbs_slam_team",
    enable_ros_bridge_extension=False,
)

with open_simulator_session(config) as simulator:
    navigation = EaiMultiRobotNavigationPlugin.from_session(simulator)
    try:
        navigation.set_goal("carter_1", (3.0, -2.0))
        navigation.set_goal("go2_1", (-1.0, 4.0))
        navigation.start_navigation()

        while simulator.simulation_app.is_running():
            simulator.env.step(navigation.compute_actions())
            state = navigation.state()
            if state.planning_error:
                raise RuntimeError(state.planning_error)
    finally:
        navigation.close()
```

`compute_actions()` 只返回由组件管理的地面机器人命令。若宿主还控制空中机器人或其他 agent，应在 `env.step()` 前合并其他组件的命令。关闭宿主时必须调用 `close()`，以取消排队任务并释放规划 worker；正在运行的原生规划器仍受 `dbcbs_planning_timeout` 限制。

## 关键参数

| 参数 | 默认值 | 作用 |
| --- | ---: | --- |
| `planner_backend` | `"dbcbs"` | 使用仓库内置 db-CBS；`"global"` 仅用于旧兼容路径。 |
| `dbcbs_planning_timeout` | `60.0` | 单次原生规划或重规划的最长秒数。 |
| `dbcbs_robot_radii` | `None` | 按机器人类型或实例名覆盖平面半径。 |
| `dbcbs_safety_margin` | `0.10` | 加到每台机器人半径上的安全边距，单位为米。 |
| `dbcbs_coarsen_factor` | `4` | 占据栅格转障碍物方框前的降采样因子。 |
| `dbcbs_replan_clearance` | `0.25` | 超出硬净空之外，用于提前触发重规划的距离。 |
| `dbcbs_replan_retry_interval` | `0.50` | 重规划失败后的最短重试间隔，单位为秒。 |
| `exclude_aerial` | `True` | 从导航团队中排除已知空中机器人类型。 |

## 当前限制

- 规划模型是固定 `0.1 s` 步长的二维双积分器；高度和完整刚体动力学不进入 db-CBS 问题。
- 地图是静态输入。组件会处理机器人之间的运行时接近事件，但不会从传感器自动更新障碍栅格。
- 自定义场景必须提供与世界坐标和几何一致的地图。
- 仅支持单个并行环境；批量 `num_envs > 1` 会被拒绝。

轻量测试不会启动 Isaac Sim：

```bash
PYTHONPATH="$PWD" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q \
  algorithm/multi_robot_navigation/test_plugin.py
```

## 来源与致谢

本集成基于 [IMRCLab/db-CBS](https://github.com/IMRCLab/db-CBS)（MIT）。
