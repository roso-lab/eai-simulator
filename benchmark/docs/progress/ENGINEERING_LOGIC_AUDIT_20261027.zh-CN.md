# 整体工程逻辑审计（2026-10-27）

## 结论

当前工程与此前设计的**总体分层已经明确，但真实场景执行后端仍未闭合**。本轮已补上
benchmark-owned 的 `TransportCommand` 和运行时质量/负载准入边界；`PickPlaceExecutor`
现在只保留理想化末端成功语义，并在任何资源租约、backend.start 或 attachment 之前
执行准入。仍未完成的是把具体场景 resolver 和 `ExecutionFeedback` 生产适配器接到
正式场景 runtime，同时删除各物理 adapter `start()` 内会把几何问题转成协作失败的
旧准入分支。底层 controller 的运动计算和调用本身必须保留。

多机器人碰撞策略也已按当前设计固定：`MultiRobotDirectEnvCfg` 的
`ignore_robot_robot_collisions` 默认值为 `True`。环境第一次 reset 时为每个环境实例
建立 robot collision groups，并两两过滤不同机器人实体之间的 PhysX contact；机器人
与地面、墙、家具、物体和支撑面的碰撞仍然保留。实体组包含 `controllers` 中登记的
宿主机器人以及名称为 `宿主名` 或 `宿主名_附件` 的挂载 articulation 根路径，因此
机械臂、传感器等属于同一整体，不会被误当成另一台机器人。该设置不改变单个实体的
`enabled_self_collisions`；无法归属的 scene articulation 会作为独立实体参与过滤。

因此，当前代码可以证明“设计组件存在”，不能证明“系统按照设计运行”。D5 暂时不能把质量变化或搬运失败当作已经实现的工程机制。

## 逐环节对照

| 设计环节 | 当前代码证据 | 状态 | 判断 |
|---|---|---|---|
| 物体质量来自场景/物理 JSON | `source/EAI/EAI/scenario_spec/operators.py` 将动态物体写入 `physics.mass_kg`；`ideal_grasp.py` 读取该字段 | 已实现（场景层） | 数据源符合设计 |
| 机器人负载来自机器人简历 | `benchmark/robot_resume/robot_pool_resume.v1.json` 含 `manipulation.payload_kg`；`TransportAdmissionResolver` 读取 `manipulation.payload_kg` | **边界已实现** | 仍需由场景 runtime 注入实际简历记录 |
| 质量与负载在搬运 skill 入口比较 | `task/transport.py` 的 `TransportAdmissionResolver.check()` 比较场景记录和机器人简历；`PickPlaceExecutor` 在租约/后端前调用 `transport_admission` | **已接通边界** | 这是 D1 和协作搬运的核心准入 |
| 被测系统语义 JSON 进入统一 skill | `EaiTaskPackage → AlgorithmDecision` 已固定；新增 `heterocoord.transport_command.v1` 作为 benchmark 内部第二层协议 | **协议已冻结，生产适配器待接** | 被测系统仍不直接调用 skill |
| 超载返回 `false/0` | `TransportAdmissionResult.result_code == 0`；`PickPlaceExecutor.execute()` 在 admission 失败时提前返回 `False` | **已实现** | 不进入 backend.start，不建立 attachment |
| `false/0` 回到协作调度器 | `benchmark.runtime` 需要 `ExecutionFeedback`，`task.SkillTaskExecutor` 需要 `ExecutionReceipt`；当前没有把 `PickPlaceExecutor` 的布尔结果接成这两个统一结果的生产适配器 | **未接通** | 即使手工得到 `false`，也不能据此证明 episode ledger 已收到标准失败 |
| 超载时不建立粘连 | admission 位于资源租约和 `backend.start()` 之前；`task/tests/test_transport.py` 验证超载返回 0 | **已实现（单元边界）** | 需要场景 runtime 集成证据 |
| 失败记录但 episode 不被错误打断 | `TaskRunner.run_orchestrated()` 会记录 `ExecutionUpdate`，将失败节点设为 `failed`，阻塞其后继；独立 ready 节点仍可继续。换机器人需要显式 `replan` | 已实现（调度层） | 续跑语义存在，但不是自动换机器人 |
| 导航与取放分离 | `NavigationExecutor`/`NavigationTaskExecutor` 与 `PickPlaceExecutor` 是两套接口；pick/place 注释明确由调用者负责导航 | 已实现 | 与设计一致 |
| 理想化抓取/放置 | `PickPlaceExecutor` 以及统一 `SkillTaskExecutor` 对 `eai.manipulation.pick_place` 只保留语义点到达 + transport admission 的成功定义；底层 controller 仍负责运动 | **执行器已按新语义判定** | 需删除 adapter 中把 controller 几何问题转成协作失败的旧准入分支 |
| 到达正确末端语义点即可成功 | executor 不再把几何、IK、工具轴、可达性或 `ready/stopped/clean` 作为业务成功条件 | **已对齐执行器边界** | controller 仍被调用，但其内部运动计算不能再成为 D1/D5 协作谓词 |
| 当前场景执行后端可执行搬运 DAG | `tools/scenario_generation/execute_episode.py` 明确 fail-closed；测试标注 legacy `demo.benchmark` 后端不在当前工程 | **未实现** | 目前没有可认证的场景搬运运行链 |

## 关键证据

1. [simulator_schema.py](/home/pupil/HeteroCoord/algorithm/skills/simulator_schema.py:242) 的 pick/place schema 仍不接收 `mass_kg` 或 `payload_kg`；它们由内部 transport resolver 读取，避免被测系统伪造。
2. [transport.py](/home/pupil/HeteroCoord/task/transport.py:29) 定义 `TransportCommand` 和 `TransportAdmissionResolver`；[pick_place.py](/home/pupil/HeteroCoord/task/executors/pick_place.py:24) 在 backend 前执行准入。
3. [ideal_grasp.py](/home/pupil/HeteroCoord/source/EAI/EAI/scenario_spec/ideal_grasp.py:230) 的 oracle 仍可用于静态可行性筛选，但运行时准入不再只依赖 oracle。
4. [task/runner.py](/home/pupil/HeteroCoord/task/runner.py:567) 证明失败会被保留为 DAG 状态；[task/runner.py](/home/pupil/HeteroCoord/task/runner.py:647) 证明替代机器人必须由显式 `replan` 提供。
5. [execute_episode.py](/home/pupil/HeteroCoord/tools/scenario_generation/execute_episode.py:1) 当前场景执行边界明确拒绝启动旧的 `demo.benchmark` 后端；因此 oracle 通过不等于搬运实际执行通过。

## 对此前设计的准确判断

此前设计的**概念结构是对的**：语义地图/物理 JSON 提供物体质量，机器人简历提供负载，导航到语义点，搬运 skill 做理想化取放，失败作为可记录的任务结果并允许重新规划。现在协议和准入边界已经补齐，剩下的是场景 runtime 的具体接线。

但此前设计要求的是一条运行时闭环：

```text
被测系统语义搬运 JSON
  → 搬运 skill admission
  → object.mass_kg 与 robot.payload_kg 比较
  → false/0 且无 attachment
  → ExecutionFeedback / ledger 记录
  → 独立任务继续或显式 replan
```

现在的代码边界已经接近：

```text
被测系统 `AlgorithmDecision`
  → `TransportCommand`
  → runtime admission（mass vs payload）
  → navigation 到语义点
  → idealized pick/place
  → `false/0` 或执行反馈
  → DAG runner / 显式 replan
```

尚缺的唯一生产接线是：由场景 runtime 提供 `object_ref → physics.mass_kg`、
`robot_id → manipulation.payload_kg` 的 resolver，并把布尔结果接入实际
`ExecutionUpdate` dispatch；`TransportAdmissionResult.to_execution_feedback()`
已经提供 `ExecutionFeedback(failure_code=INFEASIBLE_TEAM)` 的标准转换。

## 建议的工程推进顺序

1. 由场景 runtime 注入 `object_ref → physics.mass_kg` 与 `robot_id → manipulation.payload_kg` 的 resolver，接到 `PickPlaceExecutor(transport_admission=...)`。
2. 把 admission 结果包装为 `ExecutionFeedback(failure_code=INFEASIBLE_TEAM)` 并写入统一 ledger；`TransportAdmissionResolver.records` 已提供准入证据字段。
3. 增加最小闭环测试：物体 4 kg，机器人 A 3 kg，机器人 B 5 kg；A 返回 `false/0` 且无粘连，B 可继续；A 失败后独立任务继续，替代 B 只有在显式 replan 时接管。
4. 逐个把具体 arm backend 的几何/反馈检查降级为底层诊断，不再让它们改变 D1/D5 的理想化协作谓词。
5. 在上述测试通过并有真实运行证据后，再把 D5 范例绑定到这条机制；在此之前 D5 文档只能标为设计候选。
