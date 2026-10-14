# 地面导航 Skill：拓扑节点与航向 PID

地面导航采用简单的执行链：

```text
语义目标 → 拓扑节点路线 → 航向 PID → SkillPlan → EAI 底盘速度 [v, 0, wz]
```

纯 Provider 位于 `algorithm/skills/navigation/ground_navigation.py`；
真实位姿读取与速度应用位于 `task/executors/ground_navigation.py`。
Provider 保留每个任务的节点索引和 PID，只计算命令。executor 使用调用者已经
创建的 EAI 环境，通过 `env.step()` 应用命令。任务物理成败由外部 evaluator 判断。

## 1. 拓扑与选路

导航只需要语义目标和人工拓扑。节点字段为 `id`、`position_xyz`、`target_refs`，
边字段为 `id`、`from`、`to`、`directed`；未声明方向的边为双向。
位姿使用世界坐标中的米和弧度；拓扑与真实 root 位姿必须处于同一坐标系。

`topology.py` 提供编辑工具和 Provider 共用的执行结构校验。显式坐标元数据必须
为 `scene_world`、`meters_per_unit=1`、`up_axis=Z`；旧 host 的仅 nodes/edges
映射继续按这个既有约定解释。错误元数据、空/重复 ID、非有限或非数值 XYZ、
无效边与零 XY 长度边均拒绝；不增加避障、插点或自动生成路线。

每次 start 或 replan 都从当前真实位姿重新选路：

1. 按 XY 距离选择最近的 node；距离相同按 node ID 排序。
2. 按节点间 XY 直线距离运行有向 Dijkstra，选择目标绑定节点中的最短可达路线。
3. 等长候选按节点 ID 固定排序；只跟踪原有节点，不插点、不平滑、不做前视。

`target_ref` 匹配节点的目标绑定，可绑定多个节点。
`target_pose` 接受 XY 或 XYZ，按 XY 距离匹配到达半径内的最近 node，
等距按 ID 排序；没有匹配节点或路线不可达时，返回失败并生成零速度。
两种目标必须二选一。Z 用于位姿记录，不参与地面距离判定。

不加载全局栅格、局部代价地图、DWA、OBB 或任何静态、动态、native 碰撞查询。
edge polyline、length 和 profile clearance 不参与选路或跟踪。

## 2. 人工节点规则

相邻 node 默认可以直接通行，其位置、间距和通行条件由拓扑作者保证；
没有自动节点间距限制。

host 还需保证起始位置可以直接接入最近节点；现有 start/replan 保留“最近节点”
接入规则，不增加到最近节点的距离门槛。该接入段不代表已通过拓扑边或碰撞校验，
场景应把机器人放在已定义路线的起点/通行区域，不能借此推断任意离图位置可达。

弯道必须用中间节点表达。例如，道路从 A 经拐角 B 到 C，需建立
`A → B → C`。仅给 `A → C` 的 edge polyline 添加弯点时，
机器人仍朝 C 直行。狭窄弯道可增加节点，保证每段连接都处于可通行空间。
节点到达范围重叠时，会按顺序连续切换已在范围内的节点。

不自动避障、让行或生成障碍阻塞状态。模拟器物理碰撞正常保留。
无法前进时，任务继续跟踪，直到系统 hold、stop、replan 或执行窗口超时。

## 3. 航向 PID

```text
e = wrap(atan2(node_y - y, node_x - x) - yaw)
I = clamp(I + e * dt, -integral_limit, integral_limit)
D = 0（首次）；否则 wrap(e - previous_e) / dt
wz = clamp(Kp * e + Ki * I + Kd * D, -max_wz, max_wz)
v = 0（abs(e) > turn_threshold）；否则 cruise_speed
```

误差及微分中的角度差都归一化到 ±π。进入当前节点的 XY 距离阈值后依次
切换 node，并清空 PID；最后一个 node 到达后生成零速度，navigation_state
置为 succeeded。版本 1 兼容接受 target_yaw，但不执行终点航向对齐。
版本 2 的节点可带 heading_rad；到达终点位置和支持高度后再原地对齐该朝向，
或请求显式给出的 target_yaw。默认角度容差 0.10 rad，转向过程中仍检查位置和
支持高度；任务开始后不允许静默改变 target_yaw。未标记朝向时保持位置到达语义。

默认参数通过内部 `GroundTrackingConfig` 调整，不增加公开请求字段：

| 参数 | 默认值 |
|---|---|
| 巡航速度 | 0.5 m/s |
| Kp / Ki / Kd | 2.0 / 0.0 / 0.1 |
| 最大角速度 | 1.0 rad/s |
| 转向阈值 | 30° |
| 节点到达半径 | 0.30 m |
| 积分限幅 | ±1.0 rad·s |

纯 Provider 的 `control(..., dt_s=0.1)` 接受内部控制间隔。
executor 使用两次真实位姿采样之间的模拟时间；首个 PID 样本不取微分。
每个任务独立保存 PID、节点进度、模拟时间和执行证据。

## 4. 请求与生命周期

继续使用 `simulator.skill.v1`。公开参数只有 intent、world_state_seq、
mode、frame、target_yaw 和 horizon_ticks；PID 参数和路线不接受公开传入。
规范 skill_id 为 `eai.navigation.ground_navigation`，
兼容接受 `ground_navigation`。

```python
from algorithm.skills import SkillRequest
from algorithm.skills.navigation import GroundNavigationProvider

request = SkillRequest(
    request_id="navigate-1/start",
    skill_id="eai.navigation.ground_navigation",
    robot_id="carter_1",
    task_id="navigate-1",
    target_ref="place:goal",
    parameters={"intent": "start", "world_state_seq": 1820},
)
provider = GroundNavigationProvider()
# observation 由可信 host 提供，包含已校验的位姿、拓扑和世界状态。
plan = provider.plan(request, observation=observation)
```

Provider 的标准 plan 接口首次调用时建立任务，并编译一个控制周期。
需要显式排队的调用者可先调用 dispatch：它生成 holding 状态和零速度，不提前选路。

| intent | 行为 |
|---|---|
| start | 从当前真实位姿选路，重建节点索引和 PID |
| continue | 继续当前节点；尚未 start 时返回 awaiting_start 和零速度 |
| hold | 零速度，保留节点索引，清空 PID；模拟时间暂停时仍可调用 |
| replan | 从当前真实位姿重新选路，重建跟踪状态 |
| stop | 零速度，任务进入 cancelled |

world_state_seq 必须为非负整数。host 必须校验世界状态来源、有效性以及移动
意图的 freshness；Provider 还校验 observation 与请求序号一致。
task_id 缺省时使用 request_id；任务建立后 robot ID 和目标固定，终态任务拒绝继续控制。
移动 reason code 为 pid_follow_nodes，到达为 arrived，不可达为 route_unreachable。

mode、frame 保留兼容输入；当前 Provider 始终输出 base_link 中的 base_velocity，
不因 mode=goal 改用 goal_position。horizon_ticks 默认 1；executor 在移动期间
每个物理步检查超时，停车立即应用一次，不等待移动 horizon 完成。

## 5. 通用 EAI 执行接入

调用者先使用现有 simulator session API 创建环境，再注入已校验、来源绑定的
拓扑与世界状态校验回调。地面导航本身不创建 simulator、读取地图产品或下载资产。

```python
from dataclasses import replace
from task.executors.ground_navigation import GroundNavigationExecutor

executor = GroundNavigationExecutor(
    session.base_env,
    topology=validated_topology,
    world_state_validator=validate_world_state,  # callback(sequence, motion)
    timeout_s=120.0,  # 内部模拟秒预算；不属于公开请求参数
)
queued = executor.dispatch(request)  # 校验状态并实际应用零速度
running = executor.control(request)
# 后续周期由 host 更新 world_state_seq；也可发送 hold / replan / stop。
continued = executor.control(replace(
    request, request_id="navigate-1/continue",
    parameters={"intent": "continue", "world_state_seq": next_sequence},
))
```

默认位姿读取器读取 EAI scene 中的 root_pos_w 与 root_quat_w（wxyz），
明确返回 `GroundPose(position_m=(x, y, z), yaw_rad=yaw)`。
默认模拟时间读取器使用 sim.current_time；不提供该属性时，使用
common_step_counter × step_dt。默认动作工厂在执行时加载 Torch，
生成形状 [1, 3] 的速度 tensor，交给 env.step。

可注入 pose_reader、simulation_time_reader、action_factory 和 advance_hook；
多机器人 host 用 advance_hook 聚合动作并同步应用、推进环境；仅排队不能
当作命令应用确认。
该通用 executor 默认针对单个仿真环境；批量环境需提供独立的执行接入。
它是控制周期适配器，高层 Assignment 到 SkillRequest 的映射仍由任务 host 负责。

完成、取消、超时、不可达及执行异常都应用零速度。控制器应用失败后会尝试
零速度；若零命令也失败，任务保持 failed，私有证据记录错误，不能声称已物理停车。

## 6. 输出与证据边界

公开响应只有 navigation_state 和 skill_plan。路线节点、边 ID、原始位置、
root 位姿、模拟时间、命令确认和错误类型留在 private_evidence。
运行中返回本次执行样本与累计样本数，终态返回完整私有轨迹，避免逐周期复制
不断增长的轨迹。
SkillPlan.status=planned 只表示命令已编译；navigation_state=succeeded 表示
基于可信位姿的节点跟踪到达，不替代外部 evaluator 的任务物理成功判断。

每次 start/replan 的私有 `route_selection.route_graph_digest` 固定本次使用的
节点/绑定/有向边图，节点和边的数组重排不改变摘要；它不含装饰折线、profile 或
来源资产，不等同于完整拓扑产品摘要。任务 host 应另外冻结产品摘要与场景身份，
使用 `tools.topology_authoring.load_product(..., expected_digest=...)` 校验后注入。
地面导航不自行读取可变文件，运行证据不能仅依赖产品路径。

HeteroCoord 本次迁移提供通用节点 PID 和执行适配。City 产品加载器、City native
执行器及其地图来源校验仍属于源分支；此分支没有这些模块，也不复用源分支的
Carter/Go2 烟测证据作为迁移后的运行证明。

## 7. 聚焦验证

在仓库根目录按明确文件路径运行；不启动 Isaac、不要求 Torch 或地图资产：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs" \
python -m pytest -q \
  algorithm/skills/tests/test_ground_navigation.py \
  algorithm/skills/tests/test_contracts.py \
  task/tests/test_ground_navigation_executor.py \
  task/tests/test_runner.py \
  -p no:cacheprovider
```

测试覆盖 PID 跨 ±π、反向起步、转向切巡航、顺序节点切换、停车、暂停与重规划、
状态隔离、不可达、超时和异常零命令。测试用地图、OBB、DWA 与碰撞入口设置
为调用即报错；单测夹具和命令应用确认不能替代真实 native 运行证据。
