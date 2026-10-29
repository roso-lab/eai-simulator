# bar 任务设计定稿（Isaac-free 逻辑轨道）

版本：`bar_drink_delivery_v1`。本文件把 `production_bar` 的饮品生产、吧台交接、桌边配送和楼梯跨层任务冻结为可审计的逻辑轨道，逻辑时钟固定为 `0.02 s`。它用于算法内部比较，不替代 Isaac Sim 的碰撞、传感器、控制器或真机证据。当前 `robot_validated=false`、`paper_score_eligible=false`，状态为设计冻结、运行时绑定待完成。

## 1. 地图绑定与机器人池

地图产品为 `usd/scene/Production and logistics/bar/semantic_map/current`，场景键为 `production_bar`。地图 revision 为 `754f3e1731ca464a83455132375279a0`，semantic binding digest 为 `c85daa9af0b9c3d9e1daec654d6e1adedf743583f938e4adf853df6db7e8e999`。ground topology 为 59 nodes / 59 edges，aerial topology 为 0 nodes / 0 edges；bar 本任务族不使用空中机器人，跨一层与二层只登记地面楼梯路线。manifest 当前只声明 `1F`，但语义点包含 `floor2-dining_table-1/2`，并且拓扑存在 z>0 节点，因此二层目标、楼梯通行和楼层一致性仍需 runtime witness 验证。

机器人采用一个总池。每个 episode 由 runtime 从候选池选择三台实际执行机器人；未被选中的候选不绑定起点，也不占用任务资源。候选配置必须按能力指纹分别登记，不能把有臂和无臂配置合并：

| 候选配置 | 逻辑能力边界 | 限制或待验证项 |
|---|---|---|
| `Carter+D1` | 地面导航、D1 抓取/放置、制备和交接候选 | 携臂后的楼梯和载荷能力需 runtime 验证 |
| `M20+D1` | 地面导航、D1 抓取/放置、搬运候选 | `M20` 与 `M20+D1` 分开登记；楼梯和载荷需验证 |
| `B2+Z1` | 地面导航、Z1 抓取/放置、较高载荷候选 | 楼梯和载荷 witness 仍需按实例验证 |
| `M20` | 地面导航、移动搬运候选 | 无外部臂，不承担需要抓取的动作；楼梯需验证 |
| `lite3` | 地面观察/移动候选 | 不能承担 T4 楼梯运输，不能由名称推断抓取能力 |
| `G1` | 地面移动与抓放候选，技能按模式切换 | 仅用于 T1–T3；行走/抓取模式互斥，上楼不稳定、下楼未验证 |
| `Pepper` | 全向地面导航、原生双臂抓放候选 | 不套用 D1/Z1 载荷数值，楼梯与负载需验证 |

每个 episode 只冻结三类起点，并将其分配给实际选中的三台机器人：

| 任务标签 | 语义点 / 拓扑节点 | 默认职责 |
|---|---|---|
| `start_storage` | `floor1-storage_area` / ground `node:0004` | 取料、库存核验 |
| `start_production` | `workbench-1` / ground `node:0063` | 制备、装盘、内侧操作 |
| `start_reserve` | `floor1-charging_point` / ground `node:0066` | 楼梯运输、备用重分配或回充 |

候选机器人能否承担 M（移动搬运）、A（操作制备）、S（楼梯运输）或 R（备用重分配）角色，必须由 runtime capability digest、controller digest 和对应 witness 决定。T4 的实际执行者必须具备已验证的 `stair_capable`、跨层载荷和目标放置能力；`G1` 排除 T4，`lite3` 不得承担楼梯任务。未被选中的候选不绑定上述起点。

冻结语义点及绑定如下：

| 语义点 | 节点 | 任务用途 |
|---|---|---|
| `floor1-storage_area` | `node:0004` | 瓶装原料、杯子和托盘的逻辑库存源点 |
| `refrigerator` | `node:0018` | 冷藏饮品、冰块和易腐原料源点 |
| `workbench-1` | `node:0063` | 制备与装盘工作站 |
| `workbench-2` | `node:0058` | 并行制备候选工作站 |
| `workbench-3` | `node:0069` | 高难度任务备用工作站 |
| `floor1-bar_counter-1-external/inner` | `node:0031` / `node:0064` | 一层吧台外侧接收 / 内侧生产 |
| `floor1-bar_counter-2-external/inner` | `node:0029` / `node:0059` | 一层吧台外侧接收 / 内侧生产 |
| `floor1-bar_counter-3-external/inner` | `node:0028` / `node:0086` | 一层吧台外侧接收 / 内侧生产 |
| `floor1-bar_counter-4-external/inner` | `node:0025` / `node:0076` | 一层吧台外侧接收 / 内侧生产 |
| `floor1-bar_counter-5-external/inner` | `node:0073` / `node:0008` | 一层吧台外侧接收 / 内侧生产 |
| `floor1-dining_table-1..6` | `node:0033/0035/0036/0034/0040/0043` | 一层配送目标 |
| `floor2-dining_table-1/2` | `node:0053` / `node:0054` | 跨层配送目标，待楼层绑定验证 |
| `floor1-charging_point` | `node:0066` | 补能和安全回充资源 |

楼梯边为 `edge:0048`（`node:0046→node:0047`）和 `edge:0052`（`node:0050→node:0051`），两者 `traversal_mode=stairs` 且要求 `stair_capable`。当前 aerial topology 为空，不能把空中接力写成 bar 的合法替代路线。`semantic-footprints.json` 的 objects、regions、interaction_poses 和 routes 为空；订单、杯子、瓶子、冰块、托盘和交付终端只能作为逻辑对象，必须在 runtime adapter 中绑定 USD prim、质量、抓取姿态、放置容差和外部成功谓词。

## 2. 基础饮品制备与订单配送

基础任务从一楼吧台生成饮品订单。每个订单冻结 `order_id`、饮品品类、数量、目标桌、优先级和交付截止；任务轨道由 evaluator 绑定库存、订单版本和资源容量。典型流程是：从 `floor1-storage_area` 或 `refrigerator` 获取原料和杯子，在 `workbench-1/2/3` 制备并核验，经过吧台内外侧的 `release → accept` 交接，必要时通过楼梯运送到二层，再把饮品或托盘放到注册桌位，最后更新库存、订单和资源账本。

语义点到达不等于物料获取、制备、交接或交付完成。每个对象必须保持 `order_id`、品类、数量和所有权；临时放在工作台、储物区或吧台外侧不能单独关闭订单。`release` 和 `accept` 必须形成匹配的一对，交接后才允许配送端承担该对象。二层目标只能在楼层一致性和楼梯路线 witness 通过后启用。

基础轨道不自动计入所有论文关系：单机器人单批搬运只作为路径和终端谓词基线；只有制备端与配送端具有互补并行工作并强制交接时，才登记 P1/P2；连续营业窗口登记 P3-S；联合托盘动作登记 P3-J；工作台、吧台交接窗口、楼梯入口、窄通道和充电点按容量写入 P4 资源账本。

## 3. 事件协议与不可完成任务

事件按以下顺序执行并由外部 evaluator 确认：

1. **物料准备**：`material_bind → acquire → prepare → verify`。获取、制备和核验必须对应同一个对象与订单版本。
2. **吧台交接**：生产端执行 `release`，配送端执行匹配的 `accept`；没有成对事件不能开始交付。
3. **楼梯运输**：`stair_entry_acquire → stair_transit → stair_exit_release`。楼梯入口容量和 `stair_capable` 必须在资源账本中锁定；没有验证的二层路线只能记为 `feasibility-unverified`。
4. **联合搬运**：P3-J 的两名参与者必须在同一同步窗口具备载荷、抓取和在位证据；单方到达或自报不能完成联合事件。
5. **订单关闭**：目标桌放置、对象身份和数量核验、库存更新、资源释放及外部 terminal predicate 全部成立后，订单才可关闭。

以下四个任务正式注册为不可完成任务。每项的 `status` 固定为 `team-state-infeasible`，必须提供独立的 capability、time-window 或 reachability certificate，并输出 `not-deployable`。系统不得派发不满足能力的动作，需在最近安全状态释放非必要锁并执行 safe-stop。缺失 runtime binding、solver timeout、地图未验证或静态资产存在不属于合同级不可行原因，只能标记 `feasibility-unverified`。

| task_id | status | 关系签名 | 合同级空可行集原因 | 正确行为 |
|---|---|---|---|---|
| `bar_infeasible_no_stair_capability` | `team-state-infeasible` | P2 + P3-S + P4 | 跨层目标需要楼梯，但实际三机中没有 `stair_capable` 且无其他合法路线 | `not-deployable`；不派发楼梯动作，记录证书并 safe-stop |
| `bar_infeasible_insufficient_joint_manipulators` | `team-state-infeasible` | P3-J + P4 | 联合托盘要求至少两台满足抓取和载荷阈值的机器人，能力集合大小小于 2 | `not-deployable`；不伪造联合参与，锁安全释放后 safe-stop |
| `bar_infeasible_workbench_time_window` | `team-state-infeasible` | P2 + P4 | 容量为 1 的工作台串行占用超过固定截止，重叠占用又违反互斥 | `not-deployable`；不重叠抢占，不跳过 release，safe-stop |
| `bar_infeasible_unreachable_inner_handoff` | `team-state-infeasible` | P2 + P4 | 合同固定内侧交接点，受限导航图中无满足权限和能力的可达路径 | `not-deployable`；不伪造 release/accept，在安全点停止 |

`bar_infeasible_no_stair_capability` 的证书必须保存实际三机 roster digest、目标 `floor2-dining_table-*`、`edge:0048/0052` 和 `{r | stair_capable(r)}` 为空的证明；到达楼梯入口不算交付。`bar_infeasible_insufficient_joint_manipulators` 必须保存 `|{r: manipulation(r) ∧ payload(r)≥W}|<2`、载荷阈值和同步窗口；移动能力不能临时升级为联合操作能力。`bar_infeasible_workbench_time_window` 必须保存容量、持续时间区间、precedence/max-lag、截止时间和空可行集证明。`bar_infeasible_unreachable_inner_handoff` 必须保存受限图、固定内侧节点和访问权限；外侧点不能替代内侧点。

D1 只有在首次计划中声明 `not-deployable`、不派发违规动作、不伪造证书、无非必要资源占用并产生 evaluator 可验证 safe-stop 时才计为正确不可行识别。runtime 未绑定、证书缺失、运行时异常或 solver 超时均不计为不可行判断。

## 4. 四级难度与条件

四档任务均从总机器人池中选择三台实际执行机器人。任务 Tier 由冻结合同的八维需求决定：`dependency_depth`、`coordination_width`、`capability_scarcity`、`contention`、`hazard`、`communication_demand`、`branching_load` 和 `uncertainty`；运行时成功率、solver 结果和 condition factor 不参与等级判定。

| 难度 | task_id | 任务组成 | 主要测量维度 |
|---|---|---|---|
| T1 | `bar_delivery_t1` | 单订单冷藏饮品制备、吧台交接和一层交付 | 基础 P1/P2、物料身份、路径和终端放置 |
| T2 | `bar_delivery_t2` | 双订单备料、齐套、固定优先级顺序交付 | P1、P2、P4、订单顺序和资源账本 |
| T3 | `bar_service_t3` | 三订单连续服务、吧台内外交接和服务覆盖 | P1、P2、P3-S、P4、连续服务与通信 |
| T4 | `bar_crossfloor_t4` | 二层双托盘经楼梯运输、同步交接和交付 | P1、P2、P3-J、P3-S、P4、楼梯能力与危险 |

### T1：单订单冷藏饮品配送

**故事与路线。** 顾客在 `floor1-dining_table-1` 等待一杯冷藏饮品。实际执行三机中的 A 从 `refrigerator (node:0018)` 取料，在 `workbench-1 (node:0063)` 制备并核验；生产端在 `floor1-bar_counter-1-inner (node:0064)` release，M 在 `floor1-bar_counter-1-external (node:0031)` accept 后配送到 `floor1-dining_table-1 (node:0033)`。

**obligation 与约束。** `prepare_order_A < release_order_A < accept_order_A < deliver_order_A`；A 必须具备抓取/制备能力，M 必须具备移动和载荷能力，订单令牌和杯子只允许一个持有者。工作台与交接窗口容量为 1，变体可替换订单、工作台、目标桌和三台执行机器人的起点分配，但不能改变关系图。

**milestone 与成功。** `team_spawn_valid`、`order_prepared`、`release_accept_pair_valid`、`delivery_A_valid`、`episode_closeout`。成功要求品类、数量、订单 ID、release/accept 顺序、目标桌容差、资源释放和 terminal predicate 全部成立。

**难度来源。** 单条前置链、低协调宽度、两类互补能力、低争用和低分支负载构成 Tier 1；通信只需传递订单版本和交接状态。

### T2：双订单备料与顺序交付

**故事与路线。** 桌 2 需要冰饮，桌 3 需要无冰饮。A 在 `refrigerator`、`floor1-storage_area` 和 `workbench-1/2` 之间完成两种备料；M 按优先级先将订单 A 送到 `floor1-dining_table-2 (node:0035)`，再将订单 B 送到 `floor1-dining_table-3 (node:0036)`。

**obligation 与约束。** 两条订单链各自满足 `prepare → release → accept → deliver`，且 `deliver_A < deliver_B`。冰箱、工作台和交接窗口按容量 1 锁定；两个托盘、杯子和订单令牌不得互换。变体可替换两笔订单、候选工作台和目标桌，但保留齐套、优先级和资源关系。

**milestone 与成功。** `team_spawn_valid`、`orders_prepared`、`order_A_handoff_valid`、`order_A_delivery_valid`、`order_B_handoff_valid`、`order_B_delivery_valid`、`episode_closeout`。两笔订单都必须以正确品类和数量完成，资源无重叠占用，任一订单失败时不得复制另一订单的完成证据。

**难度来源。** 依赖深度、协调宽度、冰箱/工作台争用、订单顺序和候选工作站分支高于 T1；通信需同步优先级、版本和交接状态。

### T3：吧台内外多订单连续交接

**故事与路线。** 营业高峰前，A 在吧台内侧连续制备三杯不同饮品，M 在吧台外侧按顺序送往 `floor1-dining_table-4/5/6 (node:0034/0040/0043)`。候选内侧为 `floor1-bar_counter-3-inner (node:0086)` 或 `floor1-bar_counter-4-inner (node:0076)`，外侧使用对应 `*-external` 点。

**obligation 与约束。** 三条链均满足 `prepare → release → accept → deliver`；交接窗口覆盖全部 release/accept 区间且同一时刻只能服务一笔订单；注册服务窗口内连续服务覆盖率必须达到合同阈值。工作站、交接窗口、外侧通道、托盘和订单令牌写入 P4 账本。变体可替换三笔订单、工作站和目标桌，不改变服务窗口、顺序和交接关系。

**milestone 与成功。** `team_spawn_valid`、`service_window_open`、`order_stream_ready`、`handoff_1_valid`、`handoff_2_valid`、`handoff_3_valid`、`continuous_service_coverage_valid`、`episode_closeout`。三笔订单必须全部交付、每笔有成对 release/accept、无重叠锁；临时暂存导致的服务裸露超过阈值即失败。

**难度来源。** 多订单连续服务增加依赖深度和通信需求；共享交接窗口和通道提高 contention；工作站和调度选择提高 branching_load；服务覆盖率使未到达 obligation 按零计分。

### T4：跨楼层托盘配送与同步交接

**故事与路线。** 二层 `floor2-dining_table-1` 和 `floor2-dining_table-2` 在同一营业窗口各需要一托盘饮品。A 在一层完成制备，具备已验证 S 能力的机器人通过地面楼梯运输，M 在吧台内外侧完成交接与空托盘回收；两台机器人在楼梯入口同步完成大托盘交接。T4 不使用空中机器人，G1 和 lite3 不得进入该任务的实际执行 roster。

**obligation 与约束。** 两个托盘分别满足 `prepare → inner_release → synchronized_handoff → stair_entry_acquire → stair_transit → stair_exit_release → floor2_accept → deliver`；路线必须经过 `edge:0048` 和 `edge:0052` 对应的楼梯链。楼梯入口、交接窗口、二层通道和托盘按容量排他；二层目标和一层 manifest 的一致性必须先通过 runtime oracle。变体可替换托盘、订单和二层目标，但不改变两托盘、楼梯和同步关系。

**milestone 与成功。** `team_spawn_valid`、`floor1_production_ready`、`inner_handoff_valid`、`stair_entry_acquired`、`joint_crossfloor_handoff_valid`、`floor2_delivery_1_valid`、`floor2_delivery_2_valid`、`service_closeout`。两个托盘必须完成制备、同步交接、楼梯运输和二层放置；双方在同步窗口均在位；楼梯能力、楼层节点、资源锁和 terminal predicates 均有外部证据。

**难度来源。** 能力稀缺、楼梯危险、P3-J 同步、P3-S 营业窗口、P4 排他、多条前置链和通信时效共同形成 Tier 4；未知的二层绑定属于待验证前置，不得被当作已认证路线。

条件与任务 Tier 正交。通信条件采用 city_02 风格的 `C0`（无损）、`C1`（延迟）、`C2`（丢包与重试）和 `C3`（压力下延迟、丢包、乱序）；命名条件为：

| condition | 作用 | 固定规则 |
|---|---|---|
| `nominal_no_battery` | 控制条件 | 不启用电量；通信使用 C0；保留能力、订单和资源账本。 |
| `communication_stress` | 通信条件 | 运行 C0–C3；只改变传输、队列、重试和等待，不改变任务合同。 |
| `availability_loss` | D4 条件 | 在预注册时刻使一个执行功能暂时或持久失效，记录通知、受影响工作和重分配。 |
| `resource_contention` | P4 诊断条件 | 增加订单重叠和共享工作台/交接窗口竞争，不改变资源容量或 Tier。 |

paired branch 必须从共同 pre-factor checkpoint digest 启动，共享任务合同、地图 revision、semantic binding digest、实际三机 roster、订单版本和随机种子摘要。电量不是默认难度输入；如需加入必须另行注册为配对条件。

## 5. Checkpoint 与阶段点

checkpoint 在 `0.02 s` 逻辑 tick 上保存，支持 restore、fork、branch 和 replay。checkpoint 是不可变评估点，不是额外导航点；milestone 必须由外部 evaluator 或事件账本确认，不能由 controller 自报替代。阶段图为：

```text
initialize_team
        |
material_preparation
        |
production
        |
handoff
       / \
      /   \
 delivery  crossfloor_transport (T4)
      |             |
      +-------------+
             |
      service_closeout
```

冻结 checkpoint：

| checkpoint | 触发边界 | 适用任务 | 用途 |
|---|---|---|---|
| `cp_00_spawn_valid` | `team_spawn_valid` 之后 | T1–T4 | 三机 roster、三类起点、能力和地图一致性 |
| `cp_01_material_ready` | `materials_bound_and_ready` 之后 | T1–T4 | 原料、杯子、托盘、订单和库存绑定 |
| `cp_02_pre_handoff` | 首个 release 之前 | T1–T4 | paired condition 的共同起点 |
| `cp_03_handoff_complete` | 所有必需 release/accept 完成 | T1–T4 | P2 交接审计 |
| `cp_04_delivery_complete` | 所有注册桌位满足 | T1–T4 | 一层/二层交付、连续服务和楼梯结果审计 |
| `cp_05_closeout` | 所有 mandatory obligations 关闭 | T1–T4 | 终止证据和 episode 关闭 |

每个 checkpoint 至少记录：实际三台机器人及其配置、`task_id`、`episode_id`、`repeat_id`、`condition_id`、`phase_id`、三机位姿与模式、能力和 controller digest、active obligations/milestones、工作台/交接窗口/楼梯/柜台/托盘/充电资源锁、通信队列、event state、route certificate、订单和库存版本、地图 revision、semantic binding digest、随机种子摘要及 checkpoint digest。nominal、communication stress、resource contention 和 availability loss 分支只能从共同 `cp_02_pre_handoff` digest 开始。

## 6. 指标与可解释性

正式结果沿用论文 D1–D6 的边界：

- **D1 Task organization**：总池选择、三机起点绑定、M/A/S/R 角色分配、能力检查、P1/P2/P3/P4 约束和不可完成任务的安全拒绝。
- **D2 Communication adaptation**：C0–C3 下的订单、交接和连续服务成功保持率，以及消息交付、延迟、丢失和重试记录。
- **D3 Temporal coordination**：P2 release/accept、P3-J 联合动作、P3-S 连续服务和 P4 资源排他分别计分；未到达 obligation 按零处理。
- **D4 Dynamic reconfiguration**：availability loss 后的受影响工作闭包、通知、重分配、功能恢复、服务保持和终止原因。
- **D5 Cross-scenario adaptation**：当前 bar 没有经过认证的跨场景 common anchor，报告 `NA`。
- **D6 Coordination efficiency**：仅在成功门控通过的 nominal run 中报告通信量、路径距离、makespan 和仿真时间；失败运行保留在分母和 failure ledger 中。

诊断指标包括：订单和物料绑定正确率、制备成功率、品类/数量保持率、release/accept 完整度、首次交接和交付时间、楼梯进入等待、楼梯运输成功率、联合同步窗口、工作台和交接窗口排队、连续服务覆盖率、资源互斥违反、订单版本冲突、重复事件抑制、通信延迟/丢包/重试、safe-stop 延迟、路径距离、makespan 以及 checkpoint/replay 等价性。

当前文档只定义 Isaac-free 逻辑轨道，不宣称物理执行认证。`runtime_oracle_bound=false`、route/stair witness 未完成、robot validation 未完成、checkpoint runtime readback 未完成；在 semantic binding、三机能力绑定、楼梯和放置 witness、runtime oracle、checkpoint readback 及必要的跨场景配对目录全部完成前，`paper_score_eligible=false`。
