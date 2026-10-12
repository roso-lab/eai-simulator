# 无人机 Pick/Place Skill

执行链为：

```text
SkillRequest → 纯 Provider（当前阶段命令）→ executor → 现有位置控制器 / PhysX FixedJoint
```

`algorithm/skills/navigation/aerial_navigation.py` 提供人工 XYZ 节点导航；
`algorithm/skills/aerial_transport/` 提供运输状态机、坐标变换和轨迹；
`task/executors/aerial_pick_place.py` 读取真实状态、写入命令、管理资源并执行 USD 修改。
Provider 不导入 Isaac、Torch、ROS 或 OpenUSD，不创建环境，也不判定物理任务成功。
地面导航和 Pepper 的原有接口保持兼容。

## 1. 原理与阶段

```text
导航并精确对齐取物点上方
→ create_support：临时世界 FixedJoint 支撑物体
→ disable_collision：关闭物体已有碰撞
→ descending_to_pick：缓慢垂直下降
→ attach：两个实测锚点 Z 距离 ≤ 0.01 m 且 XY 距离 ≤ 0.01 m
→ remove_support：移除临时支撑
→ ascending_from_pick：恢复取物下降前的实测高度
→ navigating_to_place：导航并对齐放置点上方
→ descending_to_place：物体锚点到达放置位置
→ release：detach，并在下次物理步前恢复碰撞
→ ascending_from_place：恢复放置下降前的实测高度
```

每个周期只产生当前阶段的 `goal_position`，以及至多一个 `transport_mutation`。
修改命令带 `operation + confirmation_id`；executor 实际应用后返回私有确认，
Provider 在下一周期接收匹配且 `applied=True` 的确认才进入下一阶段。
重复执行同一确认 ID 是幂等的；错误或跨任务确认被拒绝。
attach 前 executor 用最新 PhysX 位姿再次检查 1 cm 门槛。

导航以 XYZ 欧氏距离运行有向 Dijkstra，最近节点接入、等距按 ID 排序。
节点到达半径为 0.30 m；运输终点另做 0.01 m 精确对齐。
拓扑是 host 提供的 `nodes/edges` 映射，节点为 `id/position_xyz/target_refs`，
边为 `id/from/to/directed`，默认双向，允许非零的纯垂直边。
显式坐标元数据必须为 `frame=scene_world`、`meters_per_unit=1`、`up_axis=Z`。
拓扑作者负责节点间及最近节点接入段可通行，转弯必须写成中间节点。
不使用占据地图、OBB、DWA、避障或 native 碰撞查询。

选路和稳定图摘要的总复杂度为 `O((V+E) log(V+E))`、空间 `O(V+E)`；
正常控制周期的坐标变换和轨迹采样为 `O(1)`，节点连续切换在整条路线中为 `O(V)`。
碰撞备份/恢复只在阶段切换发生，成本为物体碰撞 prim 数量 `O(C)`。
执行证据累计空间 `O(T)`；运行中只返回当前样本，终态才复制完整轨迹。

## 2. 锚点与物理规则

所有坐标使用同一世界坐标系，单位为米；四元数内部使用归一化 `wxyz`。
host 显式提供 `pick_anchor_world_m` 和 `place_anchor_world_m`，不从包围盒猜测。
dispatch 时用真实物体位姿将抓取世界点转换为物体局部点，随后每周期使用当前
PhysX 刚体位姿重建同一个点。原始 USD xform 不能替代实时位姿。

默认无人机锚点为 host 解析出的无人机刚体原点；可通过 executor/bridge 的
`drone_anchor_local_m` 指定固定的机体局部挂载点，例如烟测显式使用 `(0,0,-0.30)` m。
host 需保证该点与模型几何相容，避免释放后物体和机体重叠，不自动推断模型尺寸。
控制器 root 和该挂载点的
实测偏移参与取物对齐；放置计算使用当前携带物体锚点与控制器 root 的偏移，
因此物体达到放置锚点时，无人机 root Z 不一定等于放置 Z。
物体转动后局部锚点仍随物体运动；精确门槛不满足时继续保持目标，最终由预算
结束任务，不能把发过目标命令当成到达。

关节连接当前物体世界锚点，双方完整 local position/rotation 都由实测位姿计算。
先禁用关节，配置局部帧、body relationships、碰撞与 articulation exclusion，
最后启用。世界支撑 body0 为空，body1 为物体。只删除本 bridge 创建的关节；
不覆盖、重命名或删除别的 runtime 的关节，不移动物体来伪造抓取。

仅对已有 `UsdPhysics.CollisionAPI` 的 prim 写入 `physics:collisionEnabled=False`。
恢复时复制原编辑层的完整属性 spec（含时间采样），原来没有写入时移除本次覆盖，
让参考/较弱层意见恢复。不给其他 prim 新增 CollisionAPI。
质量、惯量、重力、刚体启用、阻尼和无人机碰撞均保持原样。
payload 的真实质量会影响原位置控制器的承载能力；Skill 不代调控制器增益或载荷补偿。

## 3. 轨迹与默认值

`VerticalProfile.sample(elapsed_s)` 给出连续梯形或短距离三角形的静止到静止轨迹。
加速段 `s=at²/2`，巡航段恒速，减速段按剩余时间回算，端点速度为零。
下降轨迹完成且实测锚点满足门槛后才连接/释放，避免提前截断减速段。
executor 的真实模拟时间决定进度，固定 XY，只改变 Z；不进行碰撞检查。
速度和加速度约束属于目标轨迹，实际刚体运动仍由原控制器与物理系统决定。

| 参数 | 默认值 / 规则 |
|---|---|
| vertical_speed_m_s | 0.1 m/s，有限正数 |
| vertical_acceleration_m_s2 | 0.5 m/s²，有限正数 |
| phase_timeout_s | 60 模拟秒，有限正数 |
| 连接 Z / XY 容差 | 均为 0.01 m，不接受公开改写 |
| timing.duration_s / budget.duration_s | 可选总活动模拟秒预算，取较小值 |
| executor timeout_s | 可选，从 dispatch 起算，包含 hold 时间 |

hold 保留阶段、路线、局部锚点与目标高度，向当前实测位置发送悬停命令。
恢复后从当前实测 Z 重建静止起步轨迹；活动阶段计时暂停，executor 的绝对预算
继续计时。start 对已启动任务不会重启抓取；replan 仅在导航阶段重新选路。

## 4. 请求与生命周期

Skill ID 为 `eai.transport.aerial_pick_place`，合同版本为 `simulator.skill.v1`。
未知字段、缺字段、非有限坐标、错误维度、frame、模式、预算和失效观测均拒绝。
`handoff_ref` 保留声明但当前不支持；只支持单机 `robot_ids=[robot_id]` 和 `fixed_joint`。
运输不使用顶层 target_ref/target_pose，目标由 source/destination 和世界锚点声明。

```python
from algorithm.skills import SkillRequest
from algorithm.skills.aerial_transport import AerialPickPlaceProvider

request = SkillRequest(
    request_id="carry-1/start", task_id="carry-1",
    skill_id="eai.transport.aerial_pick_place", robot_id="iris_1",
    parameters={
        "intent": "start", "world_state_seq": 1820,
        "object_ref": "box:1", "source_ref": "place:source",
        "destination_ref": "place:destination", "robot_ids": ["iris_1"],
        "carry_mode": "fixed_joint", "frame": "world",
        "pick_anchor_world_m": [0.0, 0.0, 0.8],
        "place_anchor_world_m": [0.6, 0.0, 0.8],
    },
    budget={"duration_s": 120.0},
)
provider = AerialPickPlaceProvider()
plan = provider.plan(request, observation=validated_observation)
```

host 观测包含 `valid=True`、移动时 `fresh=True`、匹配的 `world_state_seq`、
`simulation_time_s`、`drone_pose: RigidPose`、两个实测世界锚点和已校验拓扑。
确认通过私有 `confirmation` 进入。序号必须为非负整数；host 校验来源与 freshness。
Provider 自己不能通过调用者标记推断观测可信。

Provider 提供 `plan`、`dispatch`、`control`、`response`、`fail` 和 `active_task_ids`。
plan 首次建立任务并编译一个周期；dispatch 只排队并生成悬停目标。
任务键为 task_id，缺省为 request_id；持续控制应显式提供稳定 task_id。
任务建立后机器人、锚点、物体、路线目标、速度、预算与 branch provenance 固定，
仅 request_id、世界状态序号、intent 和快照上下文可更新。终态拒绝继续控制。

| intent | 行为 |
|---|---|
| start | 首次启动；已启动任务保留进度 |
| continue | 继续；未启动则 awaiting_start |
| hold | 悬停并保留进度；允许模拟时间暂停 |
| replan | 只重新选择当前导航阶段的路线；其他阶段拒绝 |
| stop | 终态 cancelled；已抓取时悬停并保留连接 |

抓取前取消/失败恢复原碰撞并删除临时支撑。抓取后的 stop、超时和失败保留
FixedJoint、物体禁碰撞及 robot/object 租约，不自动丢弃物体。
租约进入 quarantine，host 确认恢复条件后显式调用 `host_release`。
恢复或悬停命令应用失败也保留占用，记录错误；不声称已经物理悬停。

## 5. EAI 执行接入

调用者使用 `simulator.open_simulator_session(SimulatorLaunchConfig(...))` 创建环境；
executor 不创建第二个 AppLauncher，不下载资产，不接管 session 关闭。
默认仅支持一个模拟环境和 `goal_position` 位置控制器，直接转子控制模式拒绝。

```python
from dataclasses import replace
from task.executors.aerial_pick_place import AerialPickPlaceExecutor
from task.resources import ResourceManager

executor = AerialPickPlaceExecutor(
    session.base_env, topology=validated_xyz_topology,
    world_state_validator=validate_world_state,  # callback(seq, motion)
    reference_resolver=resolve_rigid_body,       # callback("robot" | "object", ref) → USD body path
    resource_manager=scene_resource_manager,    # 同一 scene 的 executor 共享一个 manager
    timeout_s=120.0,
    drone_anchor_local_m=(0.0, 0.0, -0.30),  # host 显式配置的挂载点
)
queued = executor.dispatch(request)
running = executor.control(request)
continued = executor.control(replace(request, request_id="carry-1/continue",
    parameters={**request.parameters, "intent": "continue", "world_state_seq": next_seq}))
# 终态持物任务仅由 host 恢复；quiescent=True 是 host 的明确恢复声明。
# executor.host_release("carry-1", quiescent=True)
```

默认读取 root_pos_w/root_quat_w 和 live PhysX tensor view，时间读取 sim.current_time
或 common_step_counter × step_dt。目标用延迟加载的 Torch 写为 `[1,3]` tensor，
经 `env.set_command(robot, "goal_position", ...)` 保存，再用零外部 XYZ 经 `env.step`
触发已有控制器推算转子输出。零目标不会被“非零外部命令”检测忽略。

可注入 `pose_reader(robot_id)`、`simulation_time_reader()`、
`action_factory(env, robot_id, xyz)`、`advance_hook(actions)` 和 bridge。
自定义 bridge 位姿读取器必须是实时刚体位姿。
默认 object_ref 只接受绝对刚体路径；符号引用需 host resolver。
无人机刚体使用 articulation 首个 root link；存在歧义时必须注入 resolver。
解析后的 object path 用作租约身份，避免不同符号引用同时占用同一物体。

多机器人 host 可以通过 advance_hook 聚合动作，但回调必须同步实际应用并推进；
只把动作排队不能当作确认。高层 Assignment 映射、异步合并步进 handle、
持物恢复策略与外部 evaluator 属于 host；不要将 retained attachment 报为 quiescent
或让通用 BoundSkill 自动释放租约。需要持续悬停时，host 应继续推进原位置控制器。

## 6. 输出与证据边界

公开响应只有 `transport_state + skill_plan`。state 中阶段、状态、reason code 和
attached 是控制流程信息；`succeeded` 表示基于注入观测的命令循环完成。
`planned`、`command_applied` 与控制状态都不能代替物理任务判定。
缺少 evaluator 时 `physical_outcome="unknown"`。

metadata 记录 schema/provider/version、规范请求摘要、task ID 和 branch context。
移动周期另记录本次位姿、锚点、模拟时间与世界序号组成的 observation_digest。
原生 joint 路径、局部锚点、路线节点、图摘要、模拟时间、真实位姿、执行确认和
恢复错误留在 `private_evidence`。运行中返回当前执行样本与累计数量，终态返回
完整轨迹。运行输出写入 tmp 或 host 指定目录，不提交为 maintained fixture。
host 应冻结拓扑产品身份并校验场景来源，Provider 不自行读取可变地图文件。

| 错误 | 处理 |
|---|---|
| invalid_request / 任务身份冲突 | plan 返回无命令 invalid_request；显式控制 API 拒绝 |
| 无可达路线、锚点不合法、执行异常 | failed，抓取前回滚，抓取后保留 |
| timeout | failed，悬停；依抓取状态回滚或保留 |
| 碰撞恢复或悬停应用失败 | 私有证据记录错误，隔离租约，等待 host |

不支持 handoff、多机协作抓取、批量环境、自动抓取点检测、避障、转子直接控制
或自动 payload 补偿。注册条目独立于 Pepper，不能因此声明其他无人机资产都已验证。

## 7. 验证边界

本分支不跟踪该 Skill 的测试与烟测脚本。轻量合同测试、OpenUSD 写入测试和
Iris cycle / retained-stop 烟测曾随开发提交 `bb605fc3` 进入本分支历史并全部
通过，随后按发布要求移除；host 需要复验时应从该提交取回脚本，或在自有环境
中重建等价检查。运行时日志与 JSON 输出不属于源码。

历史验证记录：轻量合同、地面导航、Pepper 和 task executor 回归 320 项通过，
OpenUSD 测试 4 项通过（env_isaaclab）。完整循环中，物体落在支座上并在额外
2 模拟秒后保持约 `(0.606,-0.0004,0.675)` m，无人机恢复至约 1.30 m；5 g 质量
及碰撞原值和写入状态保持一致。持物停止保留关节、禁碰撞和隔离租约，继续仿真
2 秒位置漂移约 1.2 mm，host 显式释放后物理属性及资源恢复。这些数值描述该
夹具的观测，不认证其他载荷；任何回归或烟测都不能替代外部 evaluator，缺少
evaluator 时物理结果保持 unknown。

当前 Isaac 的 PhysicsUSD 在创建携带关节时仍输出 `disjointed body transforms`
警告；已有变换同步调用，实测挂接样本未出现大幅位移，完整循环与保留停止都完成。
host 应保留原始日志和锚点/关节证据，不能用命令状态隐藏这条 native 警告。
