# city_01 任务与语义地图最终定稿

**版本**：city_01_patrol_emergency_v1 / v1
**状态**：设计冻结，等待 runtime oracle 绑定
**场景**：`service_city_01`
**地图 revision**：`b7d43c2a1ffd4109a22853a30bd6f6ad`
**semantic binding digest**：`d2d52468b4d3a3a2940951acc1f51b3eaf813d2e4c398de1856604bc3d076a0e`
**论文设计版本**：`paper_commit 0ffd470fecdf5b4bdad82dbd9c23c439198b43d3`

本文件是可读的最终设计说明；机器可执行定义以同目录的 `city_01_task_manifest.v1.json` 和 `city_01_difficulty_contracts.v1.json` 为准。

## 1. 设计目标与论文对齐

city_01 是一个跨模态城市巡检与突发事故响应场景。地面机器人负责地面巡检、补能和事故集合；空中机器人负责跨模态巡检和事故位置观测。一个 episode 可以包含多个阶段，但每个阶段的 obligation、证据和得分分别记录，不能把不同子任务的完成比例直接相加成总分。

难度不是由“是否开电量”定义，而是由论文的八维合同需求计算：`dependency_depth`、`coordination_width`、`capability_scarcity`、`contention`、`hazard`、`communication_demand`、`branching_load`、`uncertainty`。合同评估使用 `tier = 1 + max(component_bins)`，形成 Tier 1–4。电量、通信压力和 availability loss 是独立的 condition factor，可以在同一难度合同下配对比较。

当前 city_01 的正式关系签名是 **P2 + P4**：事故观测包触发地面响应释放，recover 资源具有容量 1 的互斥占用。Tier 1 用于组织和覆盖基线，登记为 P1 形式；若以后要求巡检工作单元必须并行完成且有强制 deadline，再把该变体单独认证为 P1。当前场景不把“一起到达 accident”称为 joint action，P3-J/P3-S 暂不认证。

## 2. 语义地图冻结内容

### 2.1 机器人池与初始点

| 机器人 | 模态 | 初始语义点 | 绑定节点 | 角色 |
|---|---|---|---|---|
| M20 | ground | `A` | `node:0033` | 巡检、补能、事故响应 |
| Go2 | ground | `B` | `node:0051` | 巡检、补能、事故响应 |
| Scout | ground | `C` | `node:0005` | 巡检、补能、事故响应 |
| Coco | ground | `D` | `node:0028` | 巡检、补能、事故响应 |
| Pegasus-1 | aerial | `start_1` | `node:0009` | 巡检、事故观测 |
| Pegasus-2 | aerial | `start_2` | `node:0028` | 巡检、事故观测 |
| Iris-1 | aerial | `start_3` | `node:0026` | 巡检、事故观测 |
| Iris-2 | aerial | `start_4` | `node:0027` | 巡检、事故观测 |


### 2.2 六个跨模态巡检区域

区域采用固定的 XY 投影规则：半径 **20 m** 内存在任意一个地面或空中有效轨迹交点，即认为该 area 完成。`crossroads_*` 和 `build_*` 是候选 anchor；默认不要求两个模态都分别到达。若要测试“地面与空中各自必须覆盖”的更严格协作条件，应注册为独立 condition，不能改变本版本的默认判定。

| area | 地面 anchor（节点） | 空中 anchor（节点） | 共享语义 |
|---|---|---|---|
| `area_1` | `crossroads_1` (`ground node:0043`) | `build_05` (`aerial node:0015`)、`build_06` (`aerial node:0007`) | — |
| `area_2` | `crossroads_2` (`ground node:0054`) | `build_03` (`aerial node:0021`) | `build_03` 与 `accident_aerial_1` |
| `area_3` | `crossroads_3` (`ground node:0037`) | `build_01` (`aerial node:0004`) | — |
| `area_4` | `crossroads_4` (`ground node:0009`) | `build_04` (`aerial node:0025`) | — |
| `area_5` | `crossroads_5` (`ground node:0025`) | `build_02` (`aerial node:0019`) | — |
| `area_6` | `crossroads_6` (`ground node:0044`) | `build_07` (`aerial node:0023`) | `build_07` 与 `accident_aerial_2` |

因此，地面巡检点为 `crossroads_1..6` 六个点，空中巡检点为 `build_01..07` 七个点。一个物理节点允许多个语义，不拆分共享节点。

### 2.3 补能资源

地图中的 `recover` 是一个语义资源点，在地面拓扑上绑定七个资源实例：

`recover_01..07` → `ground node:0057..0063`。

每个实例的冻结规则为：容量 `1`、服务半径 `1.5 m`、进入服务半径后占用并等待 `8.0 s`、完成等待后释放。只有启用 battery condition 时，等待完成才产生电量恢复；在 nominal 条件下仍保留资源互斥账本，用于隔离“资源占用”与“充电效果”。

### 2.4 事故语义

- 地面集合点：`accident` → `ground node:0053`。
- 空中观测点：
  - `accident_aerial_1` → `aerial node:0021`，与 `build_03` 共用节点；旧拼写 `accidend_1` 保留在 aliases。
  - `accident_aerial_2` → `aerial node:0023`，与 `build_07` 共用节点；旧拼写 `accidend_2` 保留在 aliases。
  - `accident_aerial_3` → `aerial node:0024`；旧拼写 `accidend_3` 保留在 aliases。

事故真值、事故发生时刻和受影响区域由 evaluator 隐藏并以外生事件注入。观测包必须包含 `incident_id`、状态版本和时间戳；默认 freshness window 为 `30 s`，单次观测保持 `2 s`。

### 2.5 拓扑与地图产品

当前地图产品为 ground 62 nodes / 78 edges、aerial 24 nodes / 36 edges。`manifest.json` 已覆盖所有地图产品 SHA-256；地图产品的 `robot_validated` 仍为 `false`，因为当前只有用户路线可行性验证，尚未生成自动机器人证书。该状态不能被任务设计文件伪造为已认证。

## 3. 四档正式任务

四个任务共享机器人池、地图 revision 和 area 定义；每个任务的 obligation 是独立的合同。

| Tier | task_id | 必须完成 | 关系签名 | 默认 condition | 用途 |
|---:|---|---|---|---|---|
| 1 | `city_01_patrol_t1` | 六个 area 的跨模态覆盖 | P1 形式基线 | `nominal_no_battery` | 初始分配、覆盖率、路径和组织基线 |
| 2 | `city_01_patrol_observe_t2` | Tier 1 + 三个空中事故观测包；观测后才释放地面响应 | P2 | `nominal_no_battery` | 测信息前置、跨模态 handoff、响应释放 |
| 3 | `city_01_patrol_recover_t3` | Tier 2 + 至少 3 个地面响应者；recover 容量 1；补能服务账本有效 | P2 + P4 | `battery_homogeneous` | 测补能选择、等待、资源竞争和部分响应保持 |
| 4 | `city_01_patrol_emergency_t4` | 六个 area；三处空中观测；四个地面机器人全部稳定到 `accident`；recover 容量 1；incident report closeout | P2 + P4 | `battery_heterogeneous` | 完整复合任务和最高冻结难度 |

Tier 4 的阶段顺序为：`initialize_team` → `cross_modal_patrol` 与 `incident_onset` 并行可发生 → `aerial_observation` → `ground_rendezvous` → `team_closeout`；`recover_service` 作为巡检/响应期间的资源阶段。地面机器人集合人数由该合同固定为 4，Tier 3 固定为至少 3，避免把成功定义留给运行结果后再调整。

## 4. 条件矩阵

| condition | battery | recover 等待/恢复 | communication | availability |
|---|---|---|---|---|
| `nominal_no_battery` | 关闭 | 互斥账本开；不恢复电量 | C0 | 无 |
| `battery_homogeneous` | ground 统一 120 m，reserve 5 m；空中不启用 | 8 s 后恢复满电 | C0 | 无 |
| `battery_heterogeneous` | M20 150 m、Go2 130 m、Scout 110 m、Coco 90 m；reserve 5 m；空中不启用 | 8 s 后恢复满电 | C0 | 无 |
| `communication_stress` | 关闭 | 互斥账本开；不恢复电量 | C0/C1/C2/C3，固定队列与容量溢出规则 | 无 |
| `availability_loss` | 按注册的电量条件 | 资源互斥与恢复开启 | C0 | 隐藏 capability change；导航、空中观测或通信功能之一失效，30 s 恢复窗口 |

电量预算是抽象的“可行驶距离单位”，不是硬件 Wh 标定。实际 benchmark 使用时，应先按统一路线和机器人运行时校准，再冻结物理量映射。电量条件可以与 Tier 1–4 配对做难度敏感性实验，但不能把 `battery_heterogeneous` 直接称为一个新的难度等级。

当前逻辑轨道的名义速度为地面 `1.0 m/s`、空中 `4.0 m/s`，控制步长为 `0.02 s`。这些速度不随 USD 根缩放自动变化；当前版本只表达模态级速度差异，不表达具体机器人型号的速度差异。地图完成真实尺度校准后，应把每个机器人速度作为冻结的 roster 参数重新登记。否则，放大地图而保留当前速度会按比例增加运动时间，保留电量预算则会改变任务可行性；两者都必须在条件记录中明确。

## 5. 观测、得分与诊断指标

正式报告按论文 D1–D6 分开：

- **D1 initial plan validity**：初始队伍、能力、起点、区域和路线是否满足合同。
- **D2 nominal-to-capacity-stress success retention**：同一任务在 C0 到通信/资源压力条件下的成功保持率；需要有效的 paired condition 扫描。
- **D3 P2/P4 obligation scores**：观测前置、handoff、ground response、recover mutex 和 closeout 的合同 obligation 分数。
- **D4 affected-work preservation**：注册 availability loss 后，受影响工作和未受影响工作保留情况。
- **D5**：当前 city_01 没有认证的跨场景 pair，报告 `NA`。
- **D6 success-gated bits/time efficiency**：只有在合同成功门控通过后，才报告有效信息量/时间效率。

同时记录诊断指标：六区域覆盖率、首次事故观测时间、首次地面响应时间、全员到达时间、recover 排队等待、最低剩余电量、replan latency、handoff 完整度和报告 closeout 延迟。诊断指标不能替代论文正式分数。

## 6. 标准设计与发布流程

1. 冻结场景 USD、semantic map revision 和 digest。
2. 将每个语义点绑定到对应模态的导航节点，并检查所有绑定节点存在。
3. 固定六个 area、XY 半径和覆盖完成规则。
4. 注册 recover 的实例、容量、等待、占用、释放和恢复策略。
5. 写出外部 obligation、milestone、predicate、证据和关系签名。
6. 只使用合同内容计算八维难度组件和 Tier，不把运行结果、solver 结果或 condition 当作难度输入。
7. 将 battery、communication、availability loss 作为独立 paired conditions 注册。
8. 运行 placement/spawn feasibility 和用户路线 witness replay。
9. 运行 oracle ledger replay，审计失败原因、资源账本、时间戳和共享节点语义。
10. 在有效重复实验后，再填充 D1–D6 的正式分数单元；没有 runtime oracle 时保持 `paper_score_eligible=false`。

## 7. Checkpoint 与阶段监测点

当前设计把 **milestone（里程碑）** 和 **checkpoint（检查点）** 分开：

- milestone 是 evaluator 从轨迹、事件和账本中判断的外部谓词，例如 `all_six_area_units_covered`。它回答“阶段性任务是否已经成立”，可以重复观察、去重并写入 obligation ledger。
- checkpoint 是在已登记边界保存的不可变状态快照。它回答“如果需要重放、配对比较或审计，从哪个共同状态开始”。checkpoint 不是成功标记，也不能由机器人自报替代。
- 因此不是每一个监测事件都建立 checkpoint。每个阶段可以有多个 monitoring point，但只在可复现、语义明确的阶段边界创建 checkpoint。

本任务冻结以下 checkpoint：

| checkpoint | 触发边界 | 适用 Tier | 是否可作为配对分支起点 | 用途 |
|---|---|---:|---|---|
| `cp_00_spawn_valid` | `team_spawn_valid` 之后 | 1–4 | 是 | 初始资格、起点、能力和地图一致性 |
| `cp_01_patrol_ready` | 六个 area 全部覆盖之后 | 1–4 | 是 | 巡检完成后的共同状态 |
| `cp_02_pre_incident` | 事故注入之前 | 2–4 | 是 | 事故、通信和 availability 条件的共同 pre-event 状态 |
| `cp_03_observation_complete` | 三个新鲜观测包完成之后 | 2–4 | 否 | P2 观测到地面响应的 handoff 审计 |
| `cp_04_recover_ledger_closed` | recover 占用、等待、释放账本有效之后 | 3–4 | 否 | P4 容量 1 资源审计 |
| `cp_05_ground_rendezvous` | 要求的地面机器人在 accident 稳定之后 | 4 | 否 | 地面响应结果审计 |
| `cp_06_closeout` | incident report 被 evaluator 接受之后 | 4 | 否 | 终止证据和 episode 关闭 |

每个 checkpoint 至少记录：episode/repeat/condition、phase 和 checkpoint ID、地图 revision、semantic binding digest、controller digest、仿真时间、所有机器人位姿与模式、启用时的电量、active obligations/milestones、观测版本、recover occupancy ledger、通信队列、事件注入状态、随机种子摘要和 checkpoint digest。写入必须由外部 oracle 完成，写入后不可变，并且只能从已登记 checkpoint 恢复。

阶段监测点比 checkpoint 更细：每个 `area_i` 覆盖、每个事故观测包、每次 recover acquire/wait/release、每个地面机器人到达 accident 都应产生一条带时间戳的 evidence event。事件去重按 `episode_id + monitor_id + entity_id + state_version`，不能把重复观测累计成多次完成。

对 paired condition 的规则是：nominal、通信压力和 availability loss 分支必须从同一个 `cp_02_pre_incident` digest 开始；如果要比较电量造成的路径差异，则从同一个 `cp_00_spawn_valid` 或 `cp_01_patrol_ready` 开始，并保持任务合同、机器人池、地图 revision 和事件种子一致。`cp_03` 之后的状态已经包含条件影响，不能作为跨条件共同起点。

## 7. 发布门槛

当前已通过：地图 JSON parse、地图产品哈希一致、地图拓扑重开、语义点绑定存在性检查、四个合同 assessment 重算、用户路线可行性确认、checkpoint/monitoring policy 结构校验，以及 registry checkpoint artifact 的真实写入—读取 round-trip。当前仍未通过的门槛是 live checkpoint runtime readback、runtime oracle binding 和 paper-score eligibility；因此本版本是“设计冻结、适配器已验证、运行时待绑定”，不是已经完成论文 benchmark 认证的结果。

机器文件：

- `benchmark/tasks/city_01/city_01_task_manifest.v1.json`
- `benchmark/tasks/city_01/city_01_difficulty_contracts.v1.json`
- `usd/scene/Service and operations/city_01/semantic_map/current/manifest.json`
- `usd/scene/Service and operations/city_01/semantic_map/current/semantic-points.json`
- `usd/scene/Service and operations/city_01/semantic_map/current/topology.json`
- `usd/scene/Service and operations/city_01/semantic_map/current/topology-aerial.json`
