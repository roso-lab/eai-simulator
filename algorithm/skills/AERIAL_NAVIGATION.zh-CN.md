# 空中导航 Skill：人工三维拓扑与逐节点飞行

共享拓扑编辑器、来源冻结、assignment 执行和多机器人共享 tick 的接入见
[统一运行指南](AERIAL_RUNTIME.zh-CN.md)。持续任务冻结目标/预算/上下文；hold 也
推进时钟检查并计入整体预算。可传 `origin_ref` 和 `route_graph_digest` 绑定已标记
起点与地图，执行前检查真实起点。纯 provider 的保留 `target_yaw` 字段不代表能力，
统一 runtime 对该字段明确拒绝。

空中导航的流程是：**语义目标或 XYZ 坐标 → 人工拓扑节点 → 有向 Dijkstra 选路 → 按顺序发送节点位置**。
它与地面节点导航采用相同的生命周期，但距离包含高度，底层命令是世界坐标下的
`goal_position`。人工作者负责保证节点之间可以直接、无碰撞地通过。

## 先运行 GUI 看效果

演示入口为 [`navigation/examples/aerial_topology_gui_demo.py`](navigation/examples/aerial_topology_gui_demo.py)。
它在空地上创建一架 Iris，用真实 Isaac Sim 物理和现有位置控制器，执行六个人工节点。
路线包括垂直爬升、横移、改变高度和下降；默认正向、反向交替运行。

在仓库根目录执行：

```bash
conda run --no-capture-output -n env_isaaclab python \
  algorithm/skills/navigation/examples/aerial_topology_gui_demo.py \
  --device cuda:0
```

需要已安装的 Isaac Sim 5.1、Isaac Lab 2.x、仓库 EAI 包、可用的 GPU 和桌面显示。
Iris 的 USD、控制器及其依赖必须可用；缺少资源时，标准资源解析器可能需要访问已授权的资源仓库。
演示使用 `open_simulator_session(...)` 创建一个会话，不需要 ROS2，也不会改写已保存的环境配置。
运行时移除该演示选择中的传感器附件，以集中展示飞行控制。

查看窗口 **AerialTopologyNavigation** 中的 **Aerial node navigation** 面板：

| 显示或按钮 | 含义 |
| --- | --- |
| 蓝色节点和连线 | 手工指定的三维拓扑 |
| 橙色节点 | 当前需要到达的节点 |
| 绿色节点 | 本趟已经经过的节点 |
| 黄色线 | 原生位姿读回形成的真实飞行轨迹 |
| Start / Replay | 从当前位置重新选路，前往本趟目标 |
| Hold | 保持当前位置，保留路线进度 |
| Continue | 接着执行已有路线 |
| Replan | 从当前真实位置重新选路 |
| Stop | 保持当前位置、结束本趟并取消自动往返 |
| Repeat forward / backward | 到达后等待 4 个模拟秒，再换方向 |

取消 Repeat 后，到达终点会保持位置。关闭窗口即可退出；`--frames 0` 是默认值，表示一直打开。
`--frames` 限制的是导航阶段的控制帧数，不包含启动和 100 帧悬停预热。

一次有上限的 GUI 验证可运行：

```bash
conda run --no-capture-output -n env_isaaclab python \
  algorithm/skills/navigation/examples/aerial_topology_gui_demo.py \
  --device cuda:0 --frames 1800 --output-dir tmp/aerial-topology-gui-check
```

参数 `--no-repeat` 可关闭默认往返。`--help` 不启动 Isaac Sim，导入脚本也不会启动仿真或写文件。
默认输出目录是 `tmp/aerial-topology-gui/`，包含拓扑 JSON、`evidence.json` 和首次到达后的视口截图。
证据记录原生 XYZ、实际命令、节点进度、终点距离及终态保持命令；这些文件是本地运行产物，不提交到 Git。
演示标记为 `diagnostic_gui`，可用于观察和排查，不是正式任务评测结果。

## 1. 谁决定飞机怎么飞

实现位于 [`navigation/aerial_navigation.py`](navigation/aerial_navigation.py)，公开导入路径为：

```python
from algorithm.skills.navigation import (
    AerialNavigationProvider, AerialNavigationResponse,
    AerialPose, AerialNodeRouteTracker, select_aerial_node_route,
)
```

| 层次 | 负责什么 |
| --- | --- |
| 人工拓扑 | 定义节点 XYZ、语义目标绑定、连接关系与方向 |
| Provider | 请求校验、选路、节点推进、生命周期和命令编译 |
| 调用方 / executor | 校验世界状态、读取真实位姿、应用命令、推进仿真 |
| 现有飞行控制器 | 把位置目标变成机体和旋翼动作 |
| 外部 evaluator | 根据物理证据判断正式任务是否成功 |

Provider 是纯 Python 模块，不创建 Isaac Sim 应用、不调用 `env.step`，也不加载
voxel、occupancy、DWA 或局部代价地图。它不会自动避障、自动绕行或生成 detour。
地面多楼层导航和无人机搬运仍使用目标分支自己的接口，不被这份空中导航实现替换。

## 2. 节点、连接关系和折线

每个 node 都是世界坐标 `[x, y, z]`。边的 `from`、`to`、`directed` 决定哪些节点可以相连：

- `directed: true` 只允许 `from → to`。
- `directed: false` 允许两个方向。
- 边权重由两个端点的 XYZ 欧氏距离计算，不读取额外的边长或折线长度。

下面的拓扑先上升，再横向飞行：

```json
{
  "schema": "eai.navigation.topology",
  "version": 1,
  "scene_key": "plane",
  "coordinate_frame": "scene_world",
  "meters_per_unit": 1.0,
  "up_axis": "Z",
  "nodes": [
    {"id": "a", "position_xyz": [-1, 0, 1], "target_refs": []},
    {"id": "b", "position_xyz": [-1, 0, 2.5], "target_refs": []},
    {"id": "c", "position_xyz": [2, 0, 2.5], "target_refs": ["place:goal"]}
  ],
  "edges": [
    {"id": "ab", "from": "a", "to": "b", "directed": true,
     "polyline_xyz": [[-1, 0, 1], [-1, 0, 2.5]]},
    {"id": "bc", "from": "b", "to": "c", "directed": true,
     "polyline_xyz": [[-1, 0, 2.5], [2, 0, 2.5]]}
  ]
}
```

`polyline_xyz` 保留作编辑和可视化信息，不参与运行时选路或跟踪。
需要拐弯时必须创建中间 node；只给一条边增加折线点不会让飞机经过这些点。
实际飞行轨迹由位置控制器与物理状态决定，并不保证严格贴在线段上。

此 Provider 接受以米为单位、Z 向上的 `scene_world` 拓扑对象。
它校验有限坐标、唯一 ID、端点存在、边方向和非零三维边长。
场景源文件摘要、产品来源和场景适用性由调用方在提供拓扑前校验；Provider 本身不是产品文件加载器。

## 3. 请求怎么写

Skill ID 为 `eai.navigation.aerial_navigation`，版本为 `simulator.skill.v1`。
`target_ref` 和 `target_pose` 必须二选一：

```json
{
  "request_id": "flight-001/start",
  "task_id": "flight-001",
  "skill_id": "eai.navigation.aerial_navigation",
  "skill_version": "simulator.skill.v1",
  "robot_id": "iris_1",
  "target_ref": "place:goal",
  "parameters": {
    "intent": "start",
    "world_state_seq": 42,
    "mode": "goal",
    "frame": "world",
    "horizon_ticks": 1
  }
}
```

| 字段 | 规则 |
| --- | --- |
| `target_ref` | 非空字符串，匹配节点 `target_refs` |
| `target_pose` | 三个有限 XYZ 数值，必须在某个节点的到达半径内 |
| `intent` | `start / continue / hold / replan / stop`，默认 `start` |
| `world_state_seq` | 必填，非负整数，与 observation 的序号一致 |
| `mode` | 可选，只接受 `goal` |
| `frame` | 可选，只接受 `world` |
| `target_yaw` | 可选有限数值；保留字段，本实现不做终点航向对齐 |
| `horizon_ticks` | 可选正整数，随命令传给调用方 |
| `timing.duration_s` | 可选正数，任务持续时间上限 |
| `timing.deadline_s` | 可选正数，绝对模拟时间截止点 |
| `budget.duration_s` | 可选正数，与持续时间限制取较小值 |

不接受 `navigation_mode`、`target_level` 或 `vertical_link_ref`，所有高度变化由节点 Z 和连接关系表达。
Schema 查询使用 `simulator_skill_schema(skill_id)`；校验接收 `SkillRequest`，返回 schema 对象。

## 4. 如何接到调用方循环

调用方为每次操作提供 observation：

```python
observation = {
    "valid": True,
    "fresh": True,
    "world_state_seq": 42,
    "simulation_time_s": 0.0,
    "pose": AerialPose((-1.0, 0.0, 1.0)),
    "topology": topology,
}
```

`valid`、`fresh` 应来自真实的世界状态校验结果，不能在业务接入中无条件写成 True。
`dispatch`、`hold` 和 `stop` 不要求 `fresh: true`，但仍要求有效、序号一致且有可用实测位姿。
开始或重新选路时需要 `topology`；继续和保持不会重新读取它。

```python
from dataclasses import replace
from algorithm.skills.contracts import SkillRequest
from algorithm.skills.navigation import AerialNavigationProvider, AerialPose

provider = AerialNavigationProvider()
request = SkillRequest(
    "flight-001/start", provider.skill_id, "iris_1",
    task_id="flight-001", target_ref="place:goal",
    parameters={"intent": "start", "world_state_seq": 42},
)

# 只创建任务并编译当前实测位置的保持命令，不提前选路。
queued = provider.dispatch(request, observation=observation)

# start 根据 observation 中的最新位姿和拓扑选路。
response = provider.control(request, observation=observation)

# 每次仿真推进后，读取真实位姿和新的世界状态序号，再继续。
next_request = replace(request, parameters={"intent": "continue", "world_state_seq": 43})
next_observation = {
    "valid": True, "fresh": True, "world_state_seq": 43,
    "simulation_time_s": 0.1, "pose": AerialPose((-1.0, 0.0, 1.1)),
}
response = provider.control(next_request, observation=next_observation, dt_s=0.1)
```

每个 response 有 `state`、`plan` 和 `private_evidence`。
调用方把 `plan.commands` 中的位置目标写到现有控制器入口：

```python
for command in response.plan.commands:
    goal = torch.tensor([command.payload["target_pose"]], device=env.device)
    env.set_command(request.robot_id, "goal_position", goal)
```

然后由原有模拟器循环调用 `env.step(...)`。如何构造外部 action tensor 取决于所选控制器；
本 GUI 使用 Iris 原有位置控制器的零外部 action，使控制器消费上述位置目标。
零 action 和“去世界原点”的位置命令是不同入口，不能把 `[0, 0, 0]` 位置目标当成停止信号。

`plan(request, observation=...)` 仍作为统一 Provider 入口保留：不存在任务时先 dispatch，再 control，返回 `SkillPlan`。
需要读取导航状态、独立操作 dispatch 或处理控制器失败时，使用显式生命周期接口。

## 5. 每个生命周期操作做什么

| 操作 | 行为 |
| --- | --- |
| `dispatch` | 创建任务，编译当前实测位置的保持命令，不选路 |
| `start` | 从最新实测位置重新选路，开始逐节点执行 |
| `continue` | 继续路线；未 start 时保持位置、等待启动 |
| `hold` | 保持当前实测位置，保留 tracker 的节点索引 |
| `replan` | 从当前位置重新执行最近节点和 Dijkstra |
| `stop` | 保持当前实测位置，结束任务 |

除 dispatch 外，操作通过 `provider.control()` 的 `parameters.intent` 表达。
同一任务的机器人和目标不可变化。到达、失败或 stop 后任务是终态；再次运行需要新的 `task_id`。

选路先按 XYZ 距离找最近起点，再沿连接关系执行最短路。
同距离节点、路径和边按 ID 稳定排序。没有可达路线就失败，不创建额外直达航线。
每周期检查当前节点的三维距离；距离不超过默认 **0.30 m** 就推进到下一节点。
最后一个节点到达后，用本周期实测 XYZ 编译保持命令并返回 `arrived`。

超时使用 `simulation_time_s`；绝对截止时间要求该字段。
不提供模拟时钟时，可通过每次控制的 `dt_s` 累积持续时间（默认 0.1 秒）。
一旦选择模拟时钟，后续运动控制必须继续提供单调的模拟时间。
hold/stop 始终可用于保持；模拟时钟模式下保持期间的时间仍计入任务窗口，恢复时会检查是否超时。

调用方遇到控制器或物理步异常，应报告失败并尝试应用保持命令：

```python
response = provider.fail(task_id, error, hover_position=current_measured_xyz)
```

没有新的有效位姿时可以省略 `hover_position`，使用最近有效读回位置。
若命令入口本身不可用，返回保持计划并不能证明飞机已实际停住。
节点跟踪不检测物理障碍；被挡住时继续发送当前节点，直到显式控制或任务超时。

## 6. 状态和证据

公开状态包含 `task_id`、`status`、`execution_state`、`safe_action`、`world_state_seq` 和 `reason_code`。
`execution_state` 为 `holding / moving / arrived`；终态的 `status` 为
`succeeded / failed / cancelled`。命令格式如下：

```json
{
  "command_type": "goal_position",
  "payload": {"target_pose": [2, 0, 2.5], "frame": "world"}
}
```

路线节点、边 ID、图摘要、节点索引和终态保持命令放在 `private_evidence`。
`response.to_mapping()` 不包含私有证据；需要 opaque evidence reference 的业务接口由调用方保存证据并生成引用。
`planned` 表示命令编译成功，Provider 的 `arrived` 表示实测位姿满足节点阈值；
正式物理成功仍需外部 evaluator 检查原生轨迹和任务条件。

## 7. 检查与排查

```bash
python -B -c "from algorithm.skills.navigation import AerialNavigationProvider"
python -B algorithm/skills/navigation/examples/aerial_topology_gui_demo.py --help
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider \
  algorithm/skills/tests/test_navigation_provider.py
```

- 飞机切过拐角：确认转弯处有真实 node；折线中间点不会进入命令序列。
- 目标无法解析：检查 `target_refs` 绑定，或 target_pose 与节点的 XYZ 距离是否不超过 0.30 m。
- 只能单向走：检查 `directed`，反向通行需要双向边或单独反向边。
- 保持时飞向原点：检查是否把零位置目标当成停止命令。
- 没有自动绕障：这是人工无碰撞拓扑模式的约定，应修正节点和连接关系。
- GUI 无法打开：先确认 `env_isaaclab`、显示会话、GPU、Iris USD 和控制器资源。

轻量测试不代替 GUI 物理验证；观察效果时同时检查 `evidence.json` 的节点进度、终点三维距离和终态保持目标。
