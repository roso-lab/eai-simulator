# subway 任务设计定稿（Isaac-free 逻辑轨道）

版本：`subway.final.v1`。任务族为地下车站空地协同探索、人员搜寻与发电机启动。文档描述逻辑 benchmark 合同，不替代 Isaac Sim 的碰撞、传感器、控制器、交互实体或真机证据。

地图当前只有一个 manifest level：`platform`。拓扑包含多个 z 高程和楼梯边，因此本文把它表述为“单一 `platform` level 内的多高程地下通路”，不把它直接写成已经认证的独立多楼层地图。

## 1. 地图绑定与机器人池

地图产品为 `usd/scene/Service and operations/subway/semantic_map/current`。

| 字段 | 冻结值 |
|---|---|
| 场景键 | `service_subway` |
| 地图 revision | `80f8d348edb1463da6215d2d332e85e9` |
| semantic binding digest | `64787a2b0204c3c355c2805ad7374d2aba238747c979a4af057af2108b6b5eac` |
| ground topology | 67 nodes / 69 edges |
| aerial topology | 68 nodes / 72 edges |
| manifest level | `platform` |
| 逻辑时钟 | `0.02 s/tick` |
| robot validation | `false` |
| paper score eligibility | `false` |

所有机器人出生在 `assembly_point`：

| 角色 | 语义点 | 节点 |
|---|---|---|
| 空中出生 | `assembly_point` | aerial `node:0081` |
| 地面出生 | `assembly_point` | ground `node:0052` |

同一点出生的多台机器人必须由 runtime 绑定独立初始位姿、朝向和安全间隔。出生在集合点只表示初始位置，不表示任何人员或 generator 已被发现。

语义目标为：

- `trapped_people-1` 至 `trapped_people-13`；
- `generator-1`、`generator-2`；
- `door-1-inner`、`door-1-external`，作为路线或门状态的候选语义点。

人员点和 generator 点的 ground/aerial 绑定以 `semantic-points.json` 为准。ground 拓扑中以下 5 条边声明 `traversal_mode=stairs` 且要求 `stair_capable`：`edge:0002`、`edge:0008`、`edge:0010`、`edge:0017`、`edge:0031`。空中拓扑不提供 ground 楼梯能力；无人机可达不能替代地面机器人或人员的地面路线证据。

总候选机器人池为：

| 类别 | 候选 | 逻辑能力候选 |
|---|---|---|
| 空中搜索 | `Pegasus`、`Iris`、`CF2X` | aerial navigation、语义点搜索、定位、观察 |
| 地面确认/引导 | `M20`、`Go2`、`lite3` | ground navigation、目标确认、人员引导候选 |
| 地面原地操作 | `Carter+D1`、`M20+D1`、`B2+Z1` | `manipulator`、`pickplace` 候选 |

runtime 每个 episode 从总池选择 3–5 台。推荐登记以下 roster variants：

| variant | 候选机器人 | 主要用途 |
|---|---|---|
| `subway_roster_pegasus_m20_lite3` | Pegasus、M20、lite3 | T1/T2 基础搜索与确认 |
| `subway_roster_pegasus_m20d1_carterd1` | Pegasus、M20+D1、Carter+D1 | T2/T3 地面操作候选 |
| `subway_roster_iris_b2z1_go2` | Iris、B2+Z1、Go2 | 异构搜索、确认和操作变体 |
| `subway_roster_pegasus_iris_m20d1_b2z1` | Pegasus、Iris、M20+D1、B2+Z1 | T4 默认高峰变体 |
| `subway_roster_cf2x_m20_lite3` | CF2X、M20、lite3 | CF2X 观察、地面确认变体 |
| `subway_roster_pegasus_cf2x_m20d1_b2z1` | Pegasus、CF2X、M20+D1、B2+Z1 | T4 双空中观察变体 |

型号只提供候选能力。CF2X 是观察型无人机，不承担抓取、载荷运输、generator 原地 pick/place 或人员引导。`stair_capable` 必须逐 episode 通过 runtime capability digest 和 route witness 验证；未验证时记为 `feasibility-unverified`，不得直接派发跨高程引导。未被选中的候选不绑定起点，也不产生电量、可用性或任务状态。

## 2. 基础地下探索与搜寻

探索是语义导航点触发的事件协议。无人机或地面机器人到达某个绑定语义导航点，即视为发现该点对应的人员或物体：

```text
arrive_at_bound_semantic_node
→ discovery_event
→ semantic_identity_revealed
→ shared_position_report
```

发现事件至少记录机器人 ID、domain、语义标签、绑定节点、逻辑时间和观察来源。仅经过附近未绑定节点不能算发现。地面机器人也可以自主探索，并以同样规则触发发现；无人机发现后，地面机器人只能在 discovery event 产生后获得该目标的具体位置和语义身份。

人员状态为：

```text
hidden → discovered → ground_reached → assisted → guided → assembly_complete
```

- `hidden`：尚未有机器人到达其绑定语义点；
- `discovered`：无人机或地面机器人触发发现；
- `ground_reached`：地面机器人到达该人员的 ground 绑定点；
- `assisted`：地面机器人已能提供救助或补给；
- `guided`：已开始向 `assembly_point` 引导；
- `assembly_complete`：人员到达 `assembly_point`，由外部 evaluator 确认。

地面机器人到达人员点后即可提供救助和补给，该人员不再受原始等待倒计时约束；这不等于任务自动完成，仍须完成引导并得到 `assembly_complete`。无人机发现不等于地面救助。

基础流程为：搜索并发现目标、报告具体位置和语义、地面机器人确认、必要时启动 generator、在等待规则内执行引导、将人员送至集合点，并由 evaluator 更新人员、generator 和任务账本。

## 3. 事件协议

### 人员发现、确认与引导

空地接力必须按以下顺序闭合：

```text
search_observe
→ target_report
→ ground_handoff
→ target_confirm
→ assistance_or_supply
→ evacuation_guide
→ assembly_point_arrival
```

`ground_handoff` 必须包含目标 ID、位置、观察时间、来源机器人和剩余义务；接收地面机器人必须确认并继续执行。地面机器人自主发现时可以省略跨域 handoff，但仍要记录 discovery、确认和后续引导事件。

### Generator 原地启动

T3/T4 均固定要求发现并启动 `generator-1` 和 `generator-2`。generator 不是被搬运物体，操作在其语义点的 runtime 交互区域原地完成：

```text
generator_discovered
→ generator_pick
→ generator_place
→ generator_started
```

只有 `generator_place` 成功后才生成 `generator_started`。到达 generator、发现 generator 或完成 `pick` 都不能改变人员等待规则。具体控制实体、交互姿态、放置容差和物理成功谓词尚未绑定，不能把静态 generator 语义点写成已认证可操作设施。

generator 的作用对象是尚未被地面机器人抵达的人员：

```text
affected_people = {
  p | state(p) ∈ {hidden, discovered}
       且 ground_reached(p) = false
}
```

已经 `ground_reached` 的人员不再因 generator 启动而重新获得等待时间；他们仍须完成引导和集合点到达。

### 等待与救助窗口

T3 的两台 generator 都启动后，受影响人员进入 `deadline_suspended`，不再因等待时间过期失败，但 episode 仍有最大逻辑时长，且人员必须完成确认、救助、引导和集合点到达。

T4 的两台 generator 都启动后，各自只为绑定区域内、尚未 `ground_reached` 的人员增加固定 `extension_s`。等待时钟不暂停，超时人员义务失败。地面机器人到达人员后，人员进入固定的 `assistance_window_s`；如果未在窗口内及时开始并完成引导到 `assembly_point`，记录 `rescue_failed_after_ground_contact`，该人员救助失败。generator 不能刷新或延长已经开始的 assistance window。

## 4. 四级难度

人员目标数量由 runtime 在 `trapped_people-1..13` 候选中选择；generator 数量固定为 2。

| 难度 | 任务组成 | 主要测量维度 |
|---|---|---|
| T1 `subway_search_t1` | 无人机搜寻或地面自主探索；地面机器人确认人员；不要求引导到集合点 | 语义点发现、目标身份、空地位置报告和基础 P2 |
| T2 `subway_guide_t2` | T1 + 地面机器人确认后引导人员到 `assembly_point` | P2 handoff、地面确认、路线选择、集合点终端谓词 |
| T3 `subway_generator_t3` | T2 + 发现并原地启动两个 generator；启动后受影响人员进入 `deadline_suspended` | generator 双目标闭合、原地 pickplace、责任映射、人员救助保持 |
| T4 `subway_rescue_t4` | T3 + 有限等待扩展、ground contact 后 `assistance_window_s`、多目标并行和时间压力 | P1/P2/P3-S/P4、并行调度、楼梯 witness、逾期失败、重分配鲁棒性 |

执行规模：T1/T2 最小为 1 架无人机 + 1 台地面机器人；T3 最小为 1 架无人机 + 1 台具备操作 witness 的地面机器人；T4 下限为 1 架无人机 + 2 台地面机器人，其中至少 1 台具备 `manipulator/pickplace`，至少 1 台通过 `stair_capable` witness。T4 默认高峰 variant 使用 2 架无人机 + 2 台地面机器人；单无人机 T4 作为低冗余变体单独记录。

等待参数 `base_wait_deadline_s`、`extension_s`、`assistance_window_s` 和 episode 最大时长由 runtime condition 注册并进入 checkpoint digest，不在本文冻结具体数值。

## 5. Checkpoint 与阶段点

逻辑时钟固定为 `0.02 s/tick`。checkpoint 支持 restore、fork、branch 和 replay，是不可变评估状态，不是额外导航点。paired condition 必须从同一注册 checkpoint 启动，共享地图 revision、semantic binding digest、目标集合、实际 roster、随机种子和等待参数。

阶段点为：

`cp_00_spawn_valid` → `cp_01_search_started` → `cp_02_target_observed` → `cp_03_ground_handoff_complete` → `cp_04_target_confirmed` → `cp_05_generators_started` → `cp_06_assistance_or_guide_started` → `cp_07_assembly_arrival` → `cp_08_closeout`

checkpoint 至少记录：

- 实际机器人、domain、出生节点、位姿、模式和能力 digest；
- task、episode、repeat、condition 和随机种子摘要；
- 目标集合、人员状态、等待 deadline、`deadline_suspended` 和 `assistance_window` 状态；
- discovery event、target report、ground handoff 和确认事件；
- 两个 generator 的发现、pick、place、started 状态及 runtime binding digest；
- ground route certificate、楼梯 witness、assembly point 状态；
- active obligations、milestones、资源锁、通信队列和 event state；
- semantic map digest 与 checkpoint digest。

## 6. 指标与可解释性

核心诊断指标包括：

- 语义点搜索覆盖率和首次发现时间；
- 目标身份、位置和观察来源报告正确率；
- 空地 handoff 完整度、延迟、丢包和重试；
- 地面确认成功率与 `ground_reached` 延迟；
- generator 发现率、原地 pick 成功率、place 成功率和启动完成率；
- `deadline_suspended` 进入率、T4 固定扩展时间和受影响人员数量；
- assistance window 启动、完成、超时和 `rescue_failed_after_ground_contact` 次数；
- assembly point 到达率、人员逾期率和最终救助成功率；
- 楼梯进入等待、route witness 通过率和 stair capability 误派发数；
- availability loss 后的重分配时间、资源互斥违反和 safe-stop 延迟；
- 路径距离、通信量、makespan、checkpoint/replay 等价性。

论文维度映射如下：

- **D1**：初始角色、语义搜索能力、地面确认能力、pickplace 能力、楼梯 witness、等待合同和不可完成声明；
- **D2**：C0–C3 条件下发现报告、handoff 和重试保持率；
- **D3**：P2 责任接续、P3-S 楼梯服务、generator 原地操作和 P4 等待/资源竞争；
- **D4**：无人机、地面确认、pickplace、楼梯或通信能力丢失后的重分配与服务保持；
- **D5**：当前没有认证跨场景配对，报告 `NA`；
- **D6**：在成功门控下报告通信量、路径距离、makespan 和逻辑物理时间。

当前状态仍为 Isaac-free 逻辑轨道：`runtime_oracle_bound=false`、`route_witness_pending=true`、`robot_validated=false`、`physical_qualified=false`、`paper_score_eligible=false`。缺少 runtime binding、地图未验证、solver timeout 或运行时异常只能标记为 `feasibility-unverified`，不能直接注册为 team-state-infeasible。
