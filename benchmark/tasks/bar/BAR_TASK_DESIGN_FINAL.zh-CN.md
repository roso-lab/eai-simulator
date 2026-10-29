# bar 任务与语义地图最终定稿

**版本**：`bar_drink_delivery_v1` / v1  
**状态**：设计冻结，等待 runtime oracle 绑定  
**场景**：`production_bar`  
**地图 revision**：`754f3e1731ca464a83455132375279a0`  
**semantic binding digest**：`c85daa9af0b9c3d9e1daec654d6e1adedf743583f938e4adf853df6db7e8e999`  
**论文来源**：`/home/ljy/workspace/CVPR2026_Template/main.tex`

本文件是 bar 场景的可读任务定稿说明，参照 `ce6b7ae3` 中 `CITY01_TASK_DESIGN_FINAL.zh-CN.md` 的结构和设计方法。当前版本只交付设计说明，不新增机器可执行 manifest 或 difficulty contract；后续 runtime 接入时应把本文件中的合同字段序列化为机器文件。

## 1. 设计目标与论文对齐

bar 属于 **Production and logistics**。任务的终端价值是饮品、容器、原料、托盘和订单在吧台与餐桌之间完成物料流转。场景也可以承载 service and operations 任务，但本版本的正式任务统一采用生产与配送的终端成功谓词。

论文将协调任务拆成四种原语。bar 中的登记含义如下：

| 原语 | bar 中的合同含义 |
|---|---|
| P1 互补并行工作 | 操作机器人制备饮品，移动机器人准备容器、取冰、清理或预定位交付路线。两个工作单元具有互补能力。 |
| P2 前置与交接 | 制备完成后，生产端必须通过 `release` 将订单或托盘交给配送端；配送端必须产生匹配的 `accept`，之后才可交付。 |
| P3-J 联合动作 | 两台机器人在同一同步窗口共同搬运超大托盘，或共同完成需要两方同时在位的交接动作。 |
| P3-S 连续服务 | 在注册营业窗口内连续处理多个订单，保持服务覆盖率，不得因暂存、重规划或单笔失败让后续服务长期裸露。 |
| P4 共享资源排他 | 冰箱、工作台、吧台内侧交接窗口、楼梯入口、窄通道和充电点按容量 1 或注册容量进行互斥占用。 |

四档正式难度由论文的八个合同需求分量解释：`dependency_depth`、`coordination_width`、`capability_scarcity`、`contention`、`hazard`、`communication_demand`、`branching_load` 和 `uncertainty`。难度由合同冻结前计算，使用 `tier = 1 + max(component_bins)` 的论文定义；运行时成功率、solver 结果和 condition factor 不参与等级判定。

本版本不注册第五档。四个正式任务分别对应 Tier 1–4；通信压力、电量/补能、功能丢失和资源压力均作为独立条件或诊断条件，与任务难度正交。

## 2. 语义地图冻结内容

### 2.1 当前地图事实

bar 语义地图的 `manifest.json` 声明一个 `1F` level，地面拓扑和空中拓扑产品均已列出。当前地图产品状态为 `robot_validated=false`。`semantic-footprints.json` 的实体、区域、交互位姿和路线数组目前为空，不能据此声称场景已经绑定了可操作物品。

地面 `topology.json` 当前包含 **59 个节点、59 条边**。其中两条边的 `traversal_mode` 为 `stairs`，并要求 `stair_capable`：

| 楼梯边 | 起点 | 终点 | 必需能力 |
|---|---|---|---|
| `edge:0048` | `node:0046`（z=0） | `node:0047`（z=1.61） | `stair_capable` |
| `edge:0052` | `node:0050`（z=1.61） | `node:0051`（z=2.0） | `stair_capable` |

虽然 manifest 的 level 列表当前只写有 `1F`，semantic points 中已经存在 `floor2-dining_table-1/2`，拓扑中也存在 z>0 节点。因此跨楼层任务只登记为**待 runtime 一致性验证**的任务：不能把二层语义点解释成已经通过楼层注册和机器人验证的独立楼层。

### 2.2 语义点绑定

| 语义点 | 地图节点 | 任务用途 |
|---|---|---|
| `floor1-storage_area` | `node:0004` | 瓶装原料、杯子和托盘的逻辑库存源点 |
| `refrigerator` | `node:0018` | 冷藏饮品、冰块和易腐原料源点 |
| `workbench-1` | `node:0063` | 制备与装盘工作站 |
| `workbench-2` | `node:0058` | 第二制备工作站和并行分支 |
| `workbench-3` | `node:0069` | 高难度任务的备用制备工作站 |
| `floor1-bar_counter-1-external` | `node:0031` | 一层外侧接收位姿 |
| `floor1-bar_counter-1-inner` | `node:0064` | 一层内侧生产位姿 |
| `floor1-bar_counter-2-external` | `node:0029` | 一层外侧接收位姿 |
| `floor1-bar_counter-2-inner` | `node:0059` | 一层内侧生产位姿 |
| `floor1-bar_counter-3-external` | `node:0028` | 一层外侧接收位姿 |
| `floor1-bar_counter-3-inner` | `node:0086` | 一层内侧生产位姿 |
| `floor1-bar_counter-4-external` | `node:0025` | 一层外侧接收位姿 |
| `floor1-bar_counter-4-inner` | `node:0076` | 一层内侧生产位姿 |
| `floor1-bar_counter-5-external` | `node:0073` | 一层外侧接收位姿 |
| `floor1-bar_counter-5-inner` | `node:0008` | 一层内侧生产位姿 |
| `floor1-dining_table-1` | `node:0033` | 一层配送目标 |
| `floor1-dining_table-2` | `node:0035` | 一层配送目标 |
| `floor1-dining_table-3` | `node:0036` | 一层配送目标 |
| `floor1-dining_table-4` | `node:0034` | 一层配送目标 |
| `floor1-dining_table-5` | `node:0040` | 一层配送目标 |
| `floor1-dining_table-6` | `node:0043` | 一层配送目标 |
| `floor1-charging_point` | `node:0066` | 补能和安全回充资源 |
| `floor2-dining_table-1` | `node:0053` | 跨楼层配送目标，待楼层绑定验证 |
| `floor2-dining_table-2` | `node:0054` | 跨楼层配送目标，待楼层绑定验证 |

当前场景资产清单标明 `interactive_fixtures=false`，且写入关节数量为 0。`order-*`、`cup-*`、`bottle-*`、`ice-*`、`tray-*`、`spill-*` 和 `delivery-*` 只能作为任务合同中的逻辑对象；它们必须在 runtime adapter 中绑定 USD prim、质量、抓取姿态、放置容差和外部成功谓词。

### 2.3 机器人池与能力角色

本版本不虚构具体机器人型号、初始位姿或 controller profile，只冻结能力角色：

| 角色 | 必需能力 | 可承担工作 |
|---|---|---|
| M：移动搬运 | 1F 地面导航、托盘搬运、低位放置 | 一层取送、外侧配送、回收空托盘 |
| A：操作制备 | 抓取、装杯、分拣、有限载荷操作 | 冰箱/储物区取料、工作台制备、内侧交接 |
| S：楼梯运输 | M 的能力加 `stair_capable` 和跨层载荷能力 | 经过楼梯边的二层运输 |
| R：备用重分配 | 覆盖被替换工作单元的能力 | 功能丢失后的接管和重规划 |

具体 roster、初始语义点、能力清单和 controller digest 必须在 runtime adapter 中绑定。没有满足能力字段的机器人不得仅凭名称或到达相邻节点被视为可执行者。

## 3. 四档正式任务

四个任务共享场景身份、语义点命名和地图 digest；每个任务的 obligation、milestone、关系签名和默认 condition 独立冻结。任务变体只能替换订单身份、目标桌、候选工作站、对象实例和初始位姿，不能改变任务的关系图或难度等级。

| Tier | task_id | 必须完成 | 关系签名 | 默认 condition |
|---:|---|---|---|---|
| 1 | `bar_delivery_t1` | 一杯冷藏饮品完成制备、交接和一层交付 | P1 + P2 | `nominal_no_battery` |
| 2 | `bar_delivery_t2` | 两个订单完成并按固定优先级顺序交付 | P1 + P2 + P4 | `nominal_no_battery` |
| 3 | `bar_service_t3` | 三个订单在吧台内外连续交接并维持服务覆盖 | P1 + P2 + P3-S + P4 | `nominal_no_battery` |
| 4 | `bar_crossfloor_t4` | 两个二层托盘完成跨楼层运输、同步交接和交付 | P1 + P2 + P3-J + P3-S + P4 | `resource_contention` |

### 3.1 Tier 1：单订单冷藏饮品配送

**故事。** 开店前，顾客在 `floor1-dining_table-1` 等待一杯冷藏饮品。A 从 `refrigerator` 取出指定饮品，在 `workbench-1` 装杯；M 在完成交接后将饮品送到目标桌。

**obligation 与路线。** `prepare_order_A` 必须先于 `release_order_A`；`release_order_A` 必须先于 `accept_order_A`；`accept_order_A` 必须先于 `deliver_order_A`。推荐语义路线为 `refrigerator (node:0018)` → `workbench-1 (node:0063)` → `floor1-bar_counter-1-external (node:0031)` → `floor1-dining_table-1 (node:0033)`。具体抓取和交接位姿待 runtime 绑定。

**能力与资源。** A 必须具备操作能力，M 必须具备地面搬运能力；杯子和订单令牌只能被一个机器人持有。无联合动作、连续服务或共享资源争用要求。

**外部 milestone。** `team_spawn_valid`、`order_prepared`、`handoff_release_accept_valid`、`delivery_A_accepted`、`episode_closeout`。

**成功条件。** 正确订单与饮品匹配；制备、release、accept、放置事件顺序正确；杯子位于桌 1 的注册容差内；没有未释放锁、碰撞或超时；所有 terminal predicates 由 evaluator 验证。

**难度来源。** 单条前置链、两种互补能力、低协调宽度、无显著争用、低通信需求和低分支负载。该任务是 Tier 1 组织与交接基线。

### 3.2 Tier 2：双订单备料与顺序交付

**故事。** 桌 2 需要冰饮，桌 3 需要无冰饮。A 在冰箱和工作台之间完成两种备料，M 先交付订单 A，再交付订单 B；两笔订单不得串单。

**obligation 与路线。** 两个 `prepare_order` 可以并行或交错，但每个订单内部必须满足 `prepare → release → accept → deliver`。订单 A 的 delivery 必须先于订单 B 的 delivery。候选工作站为 `workbench-1 (node:0063)` 和 `workbench-2 (node:0058)`；目标为 `floor1-dining_table-2 (node:0035)`、`floor1-dining_table-3 (node:0036)`。

**能力与资源。** A 必须完成取冰和装杯；M 负责外侧配送；`refrigerator` 和工作台按容量 1 记录 acquire/release；两个订单的托盘不可互换。

**外部 milestone。** `team_spawn_valid`、`orders_prepared`、`order_A_handoff_valid`、`order_A_delivery_valid`、`order_B_handoff_valid`、`order_B_delivery_valid`、`episode_closeout`。

**成功条件。** 两个订单均完成；交接和交付顺序满足优先级；资源锁没有重叠占用；任一订单失败时不能把另一订单的证据复制为完成；在 horizon 内关闭任务。

**难度来源。** 依赖深度、协调宽度、工作台/冰箱争用和候选工作站分支均高于 Tier 1；通信需求来自订单优先级与交接状态同步。

### 3.3 Tier 3：吧台内外多订单连续交接

**故事。** 高峰前，A 在吧台内侧连续制备三杯不同饮品，M 在吧台外侧按订单顺序送往桌 4、桌 5、桌 6。内外侧之间的交接窗口同一时刻只能服务一笔订单。

**obligation 与路线。** 三条订单链均满足 `prepare → release → accept → deliver`；交接窗口的 P4 互斥覆盖全部 release/accept 区间；服务窗口内必须保持注册的连续服务覆盖率。推荐内侧为 `floor1-bar_counter-3-inner (node:0086)` 或 `floor1-bar_counter-4-inner (node:0076)`，外侧为对应 `*-external` 节点，目标为 `floor1-dining_table-4/5/6 (node:0034/0040/0043)`。

**能力与资源。** A 需要连续制备能力，M 需要顺序配送能力；交接窗口、外侧通道、托盘和订单令牌均写入资源账本；P3-S 的服务覆盖率和 P4 的互斥约束必须分别评估。

**外部 milestone。** `team_spawn_valid`、`service_window_open`、`order_stream_ready`、`handoff_1_valid`、`handoff_2_valid`、`handoff_3_valid`、`continuous_service_coverage_valid`、`episode_closeout`。

**成功条件。** 三笔订单全部交付；每笔均有成对的 release/accept；交接窗口没有重叠占用；连续服务覆盖率达到合同阈值；临时暂存不能使后续订单的服务保持时间超过上限。

**难度来源。** 多订单连续服务提升依赖深度和通信需求；共享交接窗口与外侧通道提升 contention；工作站选择和订单调度提升 branching_load；服务覆盖率使未到达 obligation 按零计分。

### 3.4 Tier 4：跨楼层托盘配送与同步交接

**故事。** 二层桌 1 和桌 2 在同一营业窗口内各需要一托盘饮品。A 负责一层制备，S 通过 `stair_capable` 楼梯路径运输，M 在一层完成交接和空托盘回收；两台配送机器人必须在楼梯口同步完成大托盘交接。

**obligation 与路线。** 两个托盘各自满足 `prepare → inner_release → synchronized_handoff → stair_transport → floor2_accept → deliver`。路线必须经过 `edge:0048` 和 `edge:0052` 对应的楼梯链；目标为 `floor2-dining_table-1 (node:0053)`、`floor2-dining_table-2 (node:0054)`。二层语义点和 manifest level 的一致性必须先由 runtime oracle 验证。

**能力与资源。** S 必须拥有 `stair_capable` 和跨层载荷能力；M 与 S 在同步窗口内共同满足 P3-J；楼梯入口、二层窄通道、交接窗口和托盘均按资源账本排他；空托盘回收不能早于饮品放置。

**外部 milestone。** `team_spawn_valid`、`floor1_production_ready`、`inner_handoff_valid`、`stair_entry_acquired`、`joint_crossfloor_handoff_valid`、`floor2_delivery_1_valid`、`floor2_delivery_2_valid`、`service_closeout`。

**成功条件。** 两个托盘均完成制备、同步交接、楼梯运输和二层放置；楼梯能力被真实检查；同步窗口内双方均在位；楼梯入口和二层通道无互斥违反；二层目标没有被错误投影到一层；所有跨楼层语义均通过 runtime 绑定检查。

**难度来源。** 能力稀缺、跨层危险、P3-J 同步、P3-S 营业窗口、P4 资源排他、多条前置链、通信时效和二层语义不确定性共同形成 Tier 4。

## 4. 不可完成任务正式注册

以下任务的 `status` 固定为 `team-state-infeasible`。不可完成意味着合同级可行集合为空，并且必须提供独立可检查的能力、时间窗或可达性证书。solver timeout、数值失败、缺失证书、地图未绑定或 runtime 未验证只能标为 `feasibility-unverified`，不能冒充不可行。

| task_id | 关系签名 | 不可行类型 | 正确系统行为 |
|---|---|---|---|
| `bar_infeasible_no_stair_capability` | P2 + P3-S + P4 | 要求跨楼层运输，但可用机器人没有 `stair_capable` | 声明 `not-deployable`，不派发二层任务，安全停止 |
| `bar_infeasible_insufficient_joint_manipulators` | P3-J + P4 | 要求至少两台满足抓取和载荷能力的机器人，队伍最多一台满足 | 声明 `not-deployable`，不伪造联合参与，安全停止 |
| `bar_infeasible_workbench_time_window` | P2 + P4 | 容量 1 工作台的串行占用超过固定截止时间，重叠占用又违反互斥 | 声明 `not-deployable`，不重叠抢占工作台，释放锁后停止 |
| `bar_infeasible_unreachable_inner_handoff` | P2 + P4 | 合同固定内侧交接点，但队伍和访问权限无法到达该点 | 声明 `not-deployable`，不伪造 release/accept，安全停止 |

### 4.1 `bar_infeasible_no_stair_capability`

任务要求将托盘交付到 `floor2-dining_table-1`，且合法路径必须经过带 `stair_capable` 要求的 `edge:0048` 和 `edge:0052`。机器人池只有 M 和 A，没有满足楼梯能力的成员。

独立证书必须证明 `{r | stair_capable(r)}` 为空，并保存目标节点、楼梯边 ID、能力 roster digest 和合同版本。系统不能把到达楼梯入口或一层吧台外侧当作交付成功。

### 4.2 `bar_infeasible_insufficient_joint_manipulators`

任务要求超大托盘由两台操作机器人在同一同步窗口共同搬运；队伍中只有一台机器人同时满足 `manipulation=true` 和 `payload_capacity >= W`。

独立证书必须给出 `|{r: manipulation(r) ∧ payload(r)≥W}| < 2`，并固定 P3-J 的最小参与数、载荷阈值和同步窗口。移动机器人不能被临时标记为联合操作员。

### 4.3 `bar_infeasible_workbench_time_window`

三笔订单强制使用容量为 1 的 `workbench-1`，每笔制备占用区间和 P2 最大等待间隔已经冻结，总截止时间早于三段合法串行占用的最短完成时刻。并行占用工作台违反 P4，串行占用违反 deadline。

独立证书必须包含资源容量、三段持续时间区间、precedence/max-lag、截止时间和空可行集证明。系统不能通过重叠锁、跳过 release 或伪造完成时间获得进度。

### 4.4 `bar_infeasible_unreachable_inner_handoff`

任务把交接端点固定为 `floor1-bar_counter-5-inner`（`node:0008`），但队伍只有外侧移动机器人，合同禁止外侧穿越到内侧，也没有可到达内侧的操作机器人或通道。

独立证书必须给出受限导航图中从合法起始节点到 `node:0008` 的可达性为空，并保留内外侧访问权限和交接端点约束。系统应在最近安全节点停止，不得把 `floor1-bar_counter-5-external`（`node:0073`）当作内侧交接替代点。

### 4.5 D1 处理规则

不可完成任务只有在系统首次计划中声明 `not-deployable`、没有派发不满足能力的动作、没有占用任务资源，并产生 evaluator 可验证的 safe-stop 事件时，才进入 D1 的不可行识别信用。尝试执行、错误地声明可部署、提交无法验证的自报证书或在 oracle 状态为 `feasibility-unverified` 时强行拒绝，均不计为正确不可行判断。

## 5. 条件矩阵

条件改变运行环境或可用性，不改变基础任务的难度合同：

| condition | 作用 | 固定规则 |
|---|---|---|
| `nominal_no_battery` | 控制条件 | 不启用电量；保留资源锁和事件账本；通信为 C0。 |
| `communication_stress` | 通信条件 | 使用注册的 C0/C1/C2/C3 容量、队列、延迟和丢失规则；不改变机器人能力和任务合同。 |
| `availability_loss` | D4 条件 | 在隐藏或预注册时刻使一个执行功能暂时或持久失效；记录通知、受影响工作、恢复窗口和重分配。 |
| `resource_contention` | P4 诊断条件 | 提高订单重叠和共享工作台/交接窗口的竞争；不改变容量定义或难度 Tier。 |

paired branch 必须从相同的 pre-factor checkpoint digest 开始，共享任务合同、地图 revision、semantic binding digest、事件种子和允许的历史。条件影响发生后的 checkpoint 不能作为不同条件的共同起点。

## 6. 阶段图与外部里程碑

阶段按以下依赖图注册：

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
delivery  crossfloor_transport (Tier 4)
  |                 |
  |              delivery
  |                 |
  +-----------------+
          |
   service_closeout
```

正式阶段和 milestone 如下：

| phase_id | 前置阶段 | 外部 milestone |
|---|---|---|
| `initialize_team` | 无 | `team_spawn_valid` |
| `material_preparation` | `initialize_team` | `materials_bound_and_ready` |
| `production` | `material_preparation` | `registered_orders_prepared` |
| `handoff` | `production` | `release_accept_pairs_valid` |
| `delivery` | `handoff` | `registered_delivery_targets_satisfied` |
| `crossfloor_transport` | `handoff` | `stairs_and_floor2_delivery_valid`；仅 Tier 4 |
| `service_closeout` | `delivery` 或 `crossfloor_transport` | `all_mandatory_obligations_closed` |

milestone 由 evaluator 根据轨迹、技能返回、资源账本和外部事件判断；机器人自报“完成”不能单独触发 milestone。未到达 obligation 在 D3 中按零处理，资源锁违反、缺失交接端点和不满足能力的派发保留在 failure ledger 中。

## 7. Checkpoint 与阶段监测点

milestone 是可重复观察的外部状态谓词；checkpoint 是用于重放、配对和审计的不可变状态快照。checkpoint 不是成功标记，也不能由 controller 自报替代。

### 7.1 冻结 checkpoint

| checkpoint | 触发边界 | 适用 Tier | 是否可分支 | 用途 |
|---|---|---:|---|---|
| `cp_00_spawn_valid` | `team_spawn_valid` 之后 | 1–4 | 是 | 起点、能力 roster 和地图一致性 |
| `cp_01_material_ready` | `materials_bound_and_ready` 之后 | 1–4 | 是 | 原料、杯子、订单和库存绑定后的共同状态 |
| `cp_02_pre_handoff` | 首个 release 之前 | 1–4 | 是 | 通信、资源压力和 availability 条件的共同起点 |
| `cp_03_handoff_complete` | 所有必需 release/accept 对完成之后 | 1–4 | 否 | P2 交接审计 |
| `cp_04_delivery_complete` | 所有注册目标满足之后 | 1–4 | 否 | 交付、连续服务和跨楼层结果审计 |
| `cp_05_closeout` | `all_mandatory_obligations_closed` 之后 | 1–4 | 否 | 终止证据和 episode 关闭 |

每个 checkpoint 至少记录：`task_id`、`episode_id`、`repeat_id`、`condition_id`、`phase_id`、`checkpoint_id`、地图 revision、semantic binding digest、controller digest、仿真时间、全部机器人位姿与模式、active obligations/milestones、资源锁账本、通信队列、事件注入状态、随机种子摘要、导航/语义地图 digest 和 checkpoint digest。

写入规则固定为：外部 oracle 写入、写入后不可变、内容寻址 digest、不能只接受 controller 自报、只能从已注册 checkpoint 恢复。nominal、communication stress、resource contention 和 availability loss 分支必须从同一个 `cp_02_pre_handoff` digest 开始。

### 7.2 监测点

| monitor_id | 阶段 | 谓词 | 主要证据 |
|---|---|---|---|
| `mp_spawn` | `initialize_team` | `team_spawn_valid` | 初始位姿、能力 roster、地图 digest |
| `mp_materials` | `material_preparation` | `materials_bound_and_ready` | 对象 ID、库存源点、绑定版本 |
| `mp_production` | `production` | `order_i_prepared` | order ID、对象 ID、skill receipt、时间戳 |
| `mp_handoff` | `handoff` | `release_accept_pair_valid` | producer、receiver、resource lock、release/accept 时间 |
| `mp_delivery` | `delivery` | `delivery_target_i_satisfied` | 目标语义点、放置位姿、容差、订单 ID |
| `mp_stairs` | `crossfloor_transport` | `stair_edge_and_floor2_delivery_valid` | 楼梯边 ID、`stair_capable`、楼层节点、运输轨迹 |
| `mp_closeout` | `service_closeout` | `all_mandatory_obligations_closed` | obligation digest、failure ledger、终止事件 |

重复监测事件按 `episode_id + monitor_id + entity_id + state_version` 去重，不得把重复观测、重复 release 或重复放置累计成多次完成。

## 8. 观测、得分与诊断指标

正式报告沿用论文 D1–D6 的边界：

- **D1 Task organization**：初始 roster、能力、角色、P1 分配、P2/P3/P4 约束和不可完成任务安全拒绝。
- **D2 Communication adaptation**：同一任务在 C0–C3 通信条件下的成功保持率、消息交付和队列记录。
- **D3 Temporal coordination**：P2 前置/交接、P3-J 联合动作、P3-S 连续服务和 P4 资源排他分别计分；未到达 obligation 按零。
- **D4 Dynamic reconfiguration**：availability loss 后的受影响工作闭包、功能恢复、重分配、连续服务和终止原因。
- **D5 Cross-scenario adaptation**：当前 bar 版本没有经过认证的 source-target common anchor，报告 `NA`。
- **D6 Coordination efficiency**：在成功门控通过的 nominal runs 中报告通信量和仿真时间；失败运行保留为失败，不删除分母。

诊断指标包括首次制备时间、首次交接时间、交付完成时间、连续服务覆盖率、交接间隙、资源排队等待、互斥违反次数、楼梯入口等待、重规划延迟、安全停止延迟和 failure-ledger cause。诊断指标不能替代正式 D1–D6 分数。

## 9. 标准设计与发布流程

1. 冻结 bar USD、语义地图 revision、manifest digest 和 semantic binding digest。
2. 检查所有语义点、拓扑节点和楼梯边引用存在；保留 manifest 的 `1F` 与 z>0 节点之间的待验证状态。
3. 将订单、杯子、原料、托盘和交付目标绑定为逻辑对象，补齐 USD prim、交互姿态、质量、载荷和容差。
4. 固定四个任务的关系签名、obligation、milestone、成功谓词和默认 condition。
5. 只用基础合同计算八维难度和 Tier；不把 runtime outcome、solver timeout 或 condition 写入难度合同。
6. 为四个不可完成任务生成独立的 capability、time-window 或 reachability certificate；没有证书则只能标记 `feasibility-unverified`。
7. 完成 placement/spawn feasibility、用户路线 witness replay、楼梯能力检查、交接事件检查和资源账本 replay。
8. 注册 checkpoint 和 monitoring policy，验证 checkpoint artifact 的写入、读取和 digest 一致性。
9. 完成 runtime oracle binding、robot validation 和 live checkpoint readback 后，才允许进入 paper-score eligible 流程。

## 10. 发布门槛

当前定稿保留以下状态：

| gate | 当前状态 | 说明 |
|---|---|---|
| 地图 JSON parse | 待执行/待记录 | 需对 manifest、semantic points、footprints 和两个 topology 产品执行解析检查。 |
| 语义节点引用检查 | 待执行/待记录 | 四档任务和四个不可完成任务的节点引用必须全部存在。 |
| route witness replay | 未通过 | 当前地图声明 `robot_validated=false`，尚无自动机器人证书。 |
| semantic binding | 未绑定 | footprints 的实体、区域、交互位姿和路线为空。 |
| runtime oracle bound | `false` | 尚无 live task oracle。 |
| checkpoint runtime readback | `false` | 需要外部 ReplayStateRegistry 和 live simulator receipt。 |
| paper score eligible | `false` | 以上运行时门槛全部通过后才可改为 `true`。 |

设计文档中的“设计冻结”表示合同和故事已经冻结，不表示任务已经完成物理可执行认证，也不表示任何候选系统已经取得论文分数。

## 11. 最小发布清单

- [ ] 四个 Tier 1–4 可完成任务各有固定 task_id、关系签名、obligation、milestone、成功谓词和默认 condition。
- [ ] 四个不可完成任务各有 `team-state-infeasible` 状态、独立证书要求和 safe-stop 行为。
- [ ] 所有语义点、节点和楼梯边引用已经过 JSON 存在性检查。
- [ ] floor2 任务已明确标记为 manifest level 与拓扑 z>0 的待绑定一致性事项。
- [ ] `interactive_fixtures=false`、无关节和 `robot_validated=false` 均在发布说明中保留。
- [ ] checkpoint 与 monitoring point 区分清楚，paired condition 从共同 pre-factor checkpoint 启动。
- [ ] `runtime_oracle_bound=false`、`paper_score_eligible=false` 在 runtime 接入完成前保持不变。
