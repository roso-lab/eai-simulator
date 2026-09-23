# Restaurant 算法 episode 能力与执行边界

本文说明 `goal_restaurant_algorithm_episode.py` 的公开能力翻译与当前原生执行覆盖。
它不修改冻结题包、评分或 release gate；运行仍保持 `execution_validated=false`、
`scoreable=false`，直到产生并独立核验新的原生 episode 归档。

## 三层语义（必须区分）

| 层 | 含义 | 本适配器现状 |
| --- | --- | --- |
| 1. 公开且协议合法 | 共享/派生题包声明的候选、区域 access_modes、技能能力；合法分配不得被适配器伪装成“错误决策” | `restaurant_staging` 同时公开 ground+aerial；Iris 公开具备 `relay`；kitchen/dining 仅 ground |
| 2. 当前可执行 | 原生 driver / readiness 服务真正能完成的动作 | 两台 Carter：导航+地面支撑观察+返回后 relay；Iris：仅在 overview 锚点静止 hover 下做非运动 event-bus relay |
| 3. 明确失败 | 第 1 层合法但第 2 层未覆盖时，必须在计划展开或执行层失败，不得伪成功，也不得把 authoring-only 升为 `execution_validated` | Iris 分配 kitchen/dining inspection → `ValueError`；缺少 native driver → `RuntimeError`；gate 仍为 false |

## 公开契约

- `kitchen_service` 和 `dining_service` 仅允许地面到达；两项 inspection 因此要求
  `ground_navigation + observe`，并继续由带原生路线和地面支撑查询的 Carter 执行。
- `restaurant_staging` 同时公开 `ground_navigation` 和 `aerial_navigation`。适配器不得把它
  描述为 ground-only，也不得以虚构的 `ground_navigation` 条件预先淘汰空中 relay。
- `relay` 的技能本体只要求 `relay`；readiness 服务只要求同 episode 的两处已记录观察、
  严格递增的模拟步/时间以及 publication 先于 receipt。服务没有 staging、移动方式或
  producer 地点前置条件。
- EMOS 将 readiness relay 建模为正式的 targetless 子任务（`PositionRule(kind="none")`）：
  不进入 `subtask_positions`，`RobotTask.target_xy` 为 `None`，不得回退为虚构 staging
  或 `(0,0)`。

## 当前执行覆盖

`restaurant_iris_1` 实际出生在公开的 `restaurant_overview` 锚点
`(8.325, 7.675, 3.0)`。它没有厨房/用餐区的空中路线、服务区观察或地面支撑查询驱动，
所以把 inspection 分配给 Iris 仍会明确失败。它在整个 episode 中由
`SharedIdleHolds` 每步发送真实 hover 目标，不通过符号状态或瞬移改变位置。

Iris 被分配 readiness relay 时，计划把 relay 绑定到它当前的
`restaurant_overview`。`SharedClockRelayDriver` 在真实共享模拟时钟上保持所有机器人、
连续推进两个物理控制步，读取并记录 Iris 的 root state，然后调用与 Carter 相同的
`RestaurantReadinessBackend.publish_relay()` 和 `commit_readiness_receipt()`。因此该路径
证明的是静止空中成员完成事件总线发布，不声称 Iris 获得了餐厅导航或地面观察能力。

两台 Carter 的导航、观察、返回 staging 和 relay 路径保持不变；Carter relay 仍绑定
`restaurant_staging`。归档验证把 native relay 行与 readiness service 的
`relay_publication` / `business_receipt` 及两侧 step/time **按严格类型**绑定：
`simulation_step` 必须是非 bool 的 `int`，`time_s` 必须是非 bool 的有限 numeric；
字符串或 bool 不得通过 `int()`/`float()` 隐式放行。仅 pose 通过不足以让归档过关。
共享八锚点池中未进入本算法池的成员仍是 authoring-only，不得记为执行已验证。

## EMOS 位置来源（P2 已关闭 · focused 证据）

`RestaurantEmosParticipant` 构造 EMOS manager 时**不再**依赖 `base_env=None` 的静默
`(0,0,0)` 回读。位置通过 `EMOSDiscussionManager.build_from_agent_specs(..., get_robot_pos_fn=...)`
显式注入，并写入 trace / `AssignmentRequest.position_source`：

| `position_source` | 含义 | 何时使用 |
| --- | --- | --- |
| `public_spawn_anchor` | 冻结公开 spawn 图：Carter 各自 staging spawn；Iris 为 overview 锚点 `(8.325, 7.675, 3.0)` | 当前 focused / authoring-only 分配与离线 150-test 证据 |
| `native_root_state` | 调用方提供的当前 PhysX / articulation root-state 回调 | 原生 episode 接线时必须注入；不得把 spawn 图标成该 source |

优先级：调用方 `get_robot_pos_fn`（默认标为 `native_root_state`）> 公开 spawn snapshot。
两者都缺（例如声明了非 spawn 的 source 却未给回调）时 fail-fast，**禁止**造零点。
公开 spawn 注入**不改变**能力约束：Iris 仍只能合法承担 `readiness_relay`，不得分到
kitchen/dining inspection。

**本 focused 证据使用 `public_spawn_anchor`，不是原生 PhysX 采样验证。** Formal
`run_worker()` 在 settle 后仅为 EMOS 构造并注入 `RestaurantNativeRootStateProvider`，从三台
实际 articulation 的 13D `root_state_w` 读取位置；缺 articulation、缺 root-state、非有限
状态或未覆盖三台机器的采样都会 fail-fast。provider 的 `position_source`、每台机器的 native
id、13D 样本和实际整数 physics step 同时写入 EMOS participant trace 与
`goal_episode_source.json`，归档 verifier 会检查两处 provenance 一致。TeamWeaver 不消费
位置 callback，也不构造 provider；它的 formal 归档只能声明
`allocator_does_not_consume_positions`，不得出现虚假的 capture 或 samples。
