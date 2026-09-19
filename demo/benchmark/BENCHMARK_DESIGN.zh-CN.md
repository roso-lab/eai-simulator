# 三场景 Benchmark：出题设计与实现边界

本指南区分**领域目标、已有执行原语、发布题面、实际运行证据**。2026-09-17
按当前编译器、运行器、领域适配器和维护场景核对出题边界。本轮在同一最终运行器上
完成 Warehouse v8 和 Modern House v6 的完整 Isaac 运动学参考 episode，并重建发布
题包；这只建立导航、空中观察窗口、运动学对象所有权、联合编队和失败关闭证据，
不表示完整任务已被真实感知、物理搬运或爬楼验证。历史运行只适用于当时冻结的源码、
任务和资产；元数据校验、节点完成计数或一条开发轨迹都不能替代业务与物理验收。

## 当前发布包实际测什么

当前出题输入为 Factory 的 `scenarios/inspection_dispatch_01.json`、Warehouse NVD 的
`scenarios/business_logistics_01.json` 和 Modern House NVD 的
`scenarios/home_recovery_01.json`，路径分别位于各自 demo。`tmp/` 是瞬态证据目录；
只有下文列出的、与当前源码 provenance 匹配的报告可用于本轮审查，其他阶段包仍是
历史候选。navigation smoke 也不能作为完整业务题。
编译器输出 `domain_requirements`、`factor_manifest` 和私有
`domain_contract`/`factor_policy`；使用旧包前需重新编译、绑定 hash 并验收。

| 题包 | 实际节点/模型控制 | 当前可测边界 | 不可据此宣称 |
| --- | --- | --- | --- |
| Factory F-INS-01 | 4 个节点；模型只控制 `route_confirmation` | 识别消息条件下的工位派遣、因果记录及派遣后实测到达；前 3 阶段由 benchmark 管理 | 模型自主分解全任务、隔离危险、设备复位或恢复生产 |
| Warehouse business candidate | 10 个场景节点；扫描、双侧到达、联合/单机转移、dock 复核与回场 | 订单履约的可生成骨架、运动学所有权与路径/通信原语 | SKU/订单行、真实道具、dock 回执和物理搬运 |
| Modern House business candidate | 11 个场景节点；入口扫描、家具联合整理、小物暂存、两台机器人撤离、最终复核 | 居家整理的可生成骨架、运动学所有权与路径原语 | 住户安全净空、真实道具身份和物理搬运 |

### 2026-09-17 当前源码参考执行

| 报告 | 已建立的运行证据 | 仍失败关闭的业务证据 |
| --- | --- | --- |
| `tmp/benchmark_release/warehouse_business_iris_kinematic_full_v8_20260917.json` | 5594 步、111.88 s 仿真时间；10/10 节点；2 个 RGB 窗口；2 次空中路线准入；地面逐步跟踪和根状态健康；联合箱体 126 个样本，最大间距 3.4279 m，小于 3.6727 m；三条 delivery 与最终放置谓词通过 | 两条订单身份和 dock receipt 缺实时 adapter；`business_task_success=false` |
| `tmp/benchmark_release/modern_house_business_iris_kinematic_full_v6_20260917.json` | 9505 步、190.10 s 仿真时间；11/11 节点；3 个 RGB 窗口；3 次空中路线准入；地面逐步跟踪和根状态健康；家具协作 116 个样本，最大间距 3.5407 m，小于 3.7262 m；两条 delivery 与最终放置谓词通过 | 小物身份和住户连续动线缺实时 adapter；`business_task_success=false` |

两个报告均由 `scene_public_greedy_v2` 仅消费 participant 可见事件生成，使用按米制
地面/空中路径、悬停、等待和 13 个恢复窗口计算的自动步数预算。M20 的策略控制在
近航点保留 `minimum_distance_scale=0.75`；单机运输以实测中心距离进入声明的
`interaction_standoff` 后在同一控制步执行原有 pickup 校验，避免把稳定到栅格中心
误当作业务条件。失败的 Warehouse v6/v7 报告保留为开发反例：前者在二次取货的
中间航点耗尽 12 次重规划，后者暴露清空路线后多等一个物理步的阈值竞态。

三个场景的交付目的分别是**把检验信息转成正确工位派遣**、**按指定订单完成集货与
出库交接**、**整理家具和指定物品后恢复入口到生活区的动线**。扫描、导航、搬运和
等待服务于这些交付；同一动作名称不代表相同业务题。后两项的业务谓词有代码，
实时领域数据与原生资产执行证据仍待接入。

Warehouse 生成器保留 10 节点。House 在 S8 增加复核机器人撤离节点，现为 11 节点；
两名地面成员分别前往独立待机区，复核目标也移离家具停放区。v4 的旧十节点包保留。
骨架在扫描、双侧到达、双机代理转移、单机代理转移、复核和等待上复用原语，
依赖顺序与目标区域有所区别。S8 已实现订单身份、dock 回执和生活动线的**评分代码**，
仍缺实时语义检测/领域服务采样与原生资产执行证据。
因此它们是明确标注 candidate 的领域任务骨架，尚不足以证明完整领域推理 benchmark。
仅把 transport/navigate 改成业务名字不计新增能力。

S13 的作者侧输入见 [`TASK_DESIGN_REVIEW.zh-CN.md`](TASK_DESIGN_REVIEW.zh-CN.md)。被测模型
实际收到的是每个发布包的 `release/public_task.json`、`release/participant_api.json`、
`factor_manifest` 和运行时按时到达的 observation/lifecycle events；它可以在公开能力
和依赖允许时分配、导航、重分配、等待或消费消息。模型不会收到 hidden identity、未来
干预日程、候补真值或参考决策。Warehouse/House 当前公开的是固定业务 DAG 轨道，模型
做的是团队与动作协调；自主订单选择、未知物品搜索和业务分解仍不计分。

## 审查 API 与冻结兼容性

`semantic_contract.py` 提供三个可独立使用的纯 Python 接口；`factor_matrix.py` 和
`factor_experiment.py` 另外冻结可比较因子及其执行范围：

- `build_semantic_design_catalog(scene_key)`：出题人侧 v2 catalog，含逐目标角色、
  交付物、已有原语检查和待接入领域检查，以及因子、生命周期、通信支持状态。
- `objective_semantics_for(scene_key, objective)`：按 **节点 ID + skill + target**
  精确映射；错绑目标或未知节点抛错，不把同 skill 的节点统一套上整场任务目的。
  `approach -> destination` 只返回导航 smoke 的交付物。
- `review_semantic_design(public_task_or_scenario, participant_api=...)`：只读审查
  缺失领域节点、重复整场语义、原生道具/代理差异、动作宣告与实际应用差异等。
  输出修正映射及 findings；不会执行任务或评分，也不会给出 release-ready。
- `build_public_factor_manifest(scene_key)`：给被测模型公开因子名称、等级、证据轨道
  和单因子比较规则；不包含本轮选中的等级或干预时刻。
- `compile_factor_pair(...)`：出题人用同一冻结输入编译一个控制/变体对。通信与团队
  连续性可进入协议组件执行；订单、动线和垂直因子若没有对应 live fixture，会被明确
  标成 `fixture_pending`，不能因为有等级名称而计分。
- `run_communication_trial(...)`：按选定通信等级运行消息队列并返回私有 replay
  证据；其结果只计入 protocol correctness，`execution_validated` 永远为 `false`。

`semantic_for_scene()` / `build_semantic_task_contract()` 的原有序列化形状与默认
内容保留，因为 ScenarioSpec 的嵌入式 JSON schema 不允许新增字段，且现有发布
hash/运输执行正在冻结。旧 `validate_semantic_task_contract()` 的 `validated`
现在明确限定为 `validation_scope=metadata_only`，旧版本返回
`legacy_declarations_unverified`，绝不代表支持了全部事件或通信条件。

新 catalog 不改变旧 ScenarioSpec 的嵌入式 schema；`scene_challenge._public_task`
以及 Factory 编译器现在按 **节点 ID + skill + target** 调用 `objective_semantics_for()`，将精确角色、
交付物、验收证据和 `catalog_compiler_integrated_domain_pending` 写入 v4 public task。
未知节点只允许得到明确的 scene-mission fallback，不能继承完整领域交付物。catalog
仍拒绝把 `pending` 篡改为已实现的支持声明；它本身也不是运行证据。

## 逐场景目标、角色、交付物

以下交付物只使用当前原语能产出的证据语言。`role` 表示职责，不等于 `skill`
或指定机器人。括号内的技能用于绑定；实际人员选择必须受公开能力和动作范围约束。

### Factory：证据驱动的工位派遣

| 节点 | 角色 | 当前交付物 | 不能推断 |
| --- | --- | --- | --- |
| `aerial_view` | 工位俯视证据生产者 | 带时间、来源的俯视帧/解码记录 | 任意异常检测、危险分类 |
| `ground_approach` | 近景观察位定位者 | `ground_checkpoint` 到达记录 | 观察对象已识别 |
| `ground_view` | 近景证据生产者 | 观察窗口内的近景帧与来源 | 等待即完成业务巡检 |
| `route_confirmation` | 基于证据的工位派遣者 | 消息决策、候选工位选择、决策后到达 | 设备恢复、生产线安全放行 |

Factory 的 dedicated marker/dispatch 回放确有像素、来源、消息时效、动作及实测到达
检查代码；历史标签实验不等于当前版本的完整感知验证。`evaluate_dispatch_arrival()` 会单列 `used_information` 与
`success_by_fallback`：碰巧选中正确站点也可使 `dispatch_success` 为真，不能把
这种结果计为模型成功使用视觉信息。业务载体是原场景箱体上的库存标签和 assembly/dock
候选工位，测的是证据到派遣的因果链，不是任意工业异常识别。`F-TRAFFIC-01` 的共享通道
调度和 `F-COV-01` 的工位持续观察是辅助任务族。楼梯用于上层工位可达性的独立候选
探针，见下文；危险隔离、设备复位和恢复生产不在当前评分范围。

### Warehouse：订单转移骨架

| 节点 | 角色 | 当前交付物 | 待接入的领域验收 |
| --- | --- | --- | --- |
| `rack_overview_scan` | 货架证据生产者 | 货架扫描帧 | SKU/库位识别及拣选前消费 |
| `aisle_left_access` | 取货侧定位者 | 主侧作业位到达 | 订单对应取货通道 |
| `aisle_right_access` | 接收侧定位者 | 接收侧到达 | 同一订单的接收区域绑定 |
| `oversized_crate_transfer` | 大件联合转移团队 | 双成员代理所有权和交付链 | 正确订单行/数量；真实负载需要 |
| `priority_carton_stage` | 集货执行者 | 纸箱代理入集货区 | 优先级、数量和集货核对 |
| `dock_lane_check` | dock 接近检查者 | dock 区域到达 | 实际占用和交接权限 |
| `priority_carton_dispatch` | 出库转移执行者 | 同一纸箱代理从集货到 dock 的链 | 派送前新鲜 dock 放行消息 |
| `outbound_inventory_scan` | 派送后证据生产者 | 派送后的 dock 扫描帧 | 出库身份/数量复核 |
| `dock_confirmation_hold` | 交接窗口保持者 | 定时保持记录 | 收货确认或超时拒收 |
| `aisle_closeout` | 货架返回定位者 | 返回扫描区的到达记录 | 载荷及机器人全部退出受保护通道 |

业务意义是让指定订单物品经过货架、集货区和出库 dock，保持身份、数量及交接链一致，
并在作业后退出通道。现有图是 **dispatch 后 scan**，因此不能写成“先复核 dock 再放行”。`priority`
只是命名，没有订单集合、截止期排序或选择另一订单的决策规则。所谓大件/普通
纸箱在 v4 是两个 `primitive_box`；S9 生成器已改用 `wooden_crate_a` 和 `cardbox_c`，
当前维护业务场景也声明这两种符合仓储用途的 USD 道具，按审核尺寸和估计质量生成实例。
声明原生 `asset_key` 不等于资产已实例化、SKU 已识别或已被物理搬运。
质量仍不参与 kinematic 运载控制，不能声称
60kg 木箱因此验证了两台机器人的物理承载能力。

### Modern House：家具/物品转移骨架

| 节点 | 角色 | 当前交付物 | 待接入的领域验收 |
| --- | --- | --- | --- |
| `room_entry_scan` | 入口证据生产者 | 整理前入口帧 | 门厅阻塞/净空基线 |
| `furniture_side_access` | 家具主侧定位者 | 家具侧到达 | 目标家具和可接近面绑定 |
| `doorway_access_check` | 门口辅助侧定位者 | 辅助作业位到达 | 入口到生活区连续路径基线 |
| `coordinated_furniture_reposition` | 家具联合转移团队 | 家具代理双成员交付链 | 完整占地入允许区域、不侵入动线 |
| `furniture_clearance_scan` | 移动后证据生产者 | 停放区帧 | 最窄通道净空与入住阈值比较 |
| `keepsake_relocation` | 已知小物暂存者 | 小物代理暂存交付链 | 身份识别及观察到交付关联 |
| `room_entry_verification` | 房间复核位定位者 | 最终复核位到达 | 重走入口到生活区完整路径 |
| `room_condition_scan` | 整理后证据生产者 | 最终房间帧 | 家具、物品、净空的语义比对 |
| `placement_confirmation_hold` | 放置观察窗口保持者 | 定时保持记录 | 物体稳定和持续无侵占 |
| `reviewer_standby` | 复核机器人撤离者 | 独立待机区到达 | 最终生活动线未被机器人占据 |
| `hall_release` | 作业机器人撤离者 | 另一独立待机区到达 | 收尾后的完整路径净空 |

业务意义是整理扶手椅和指定书籍，使入口到生活区的通行路径恢复可用，并让机器人
撤到动线之外。当前道具为场景相符的 `mh_armchair`、`mh_bookstack`；历史节点名
`keepsake_relocation` 不意味着未知贵重物品搜索。表格按职责列出节点，实际依赖以题包
DAG 为准，最终 `room_condition_scan` 在两台地面机器人撤离后执行。

v4 的 `hall_release` 返回入口扫描区，可能仍占住入口；S8 改为两个独立待机区，
并要求最终验收发生在所有收尾动作结束之后。新布局已通过二维初始放置、连通性与
执行计划静态门禁；这不代替原生家具和真实轨迹的最终净空验收。
`keepsake_relocation` 操作的是已知位置的代理，不存在找物/身份识别决策。
S9 生成器改为 `mh_armchair`、`mh_bookstack`，根据真实占地调整家具位置。
三次观察采用 0.85m 高度和独立家具复核位，避开体素产品中阻塞旧 2.5m 路线的几何。
生成器对顺序见证的无人机路线、搬运后的物体包络执行连续 swept-AABB 检查。
入口扫描位从 `(17.0, 1.5)` 移到入口侧 `(15.5, 1.5)`，避免扫描完成后的 camera-only
Iris 驻留包络侵入家具联合运输通道；该位置变化已由生成器、维护场景和 QA sidecar
共同冻结。
相机视野、无人机实际跟踪、并发机器人间净空和居民安全仍需要运行证据。
最终扫描在两台地面机器人撤离后执行；确认等待位也在生活动线外。

## 因子、团队变化与通信：支持清单

支持状态描述代码，不描述某次运行结果。每个启用的因子仍需实例配置、真实生效的
干预、观测记录和有消费者的验收谓词，不能仅因列出三个 levels 就算覆盖。

| 条件/因子 | 当前真实边界 | 待接入 |
| --- | --- | --- |
| 目标信息不对称 | Factory decoded marker 消息影响站点选择 | 其他场景的订单/小物识别消息与因果消费 |
| 共享资源、路径净空 | scheduler 资源预约、地图/路线门禁 | 分层因子实例集；资源 token 不等于整个通道的物理占用 |
| 订单优先级 | 名称与固定 DAG | 订单表、截止期、可选择订单、优先级效用 |
| 载荷/团队需求 | 明确 kinematic opt-in；恰好两人联合所有权 | 质量不影响执行；不能据此声称容量、抓取或受力协作 |
| 居家净空/居民安全 | 独立谓词检查前后路径，冻结净宽 0.8m、占据图 hash、所有物体保守占地和地面机器人足迹 | 真实采样适配器；人的接触、跌倒、头部净空等不属于该二维指标 |
| `member_exit` | ground-only 且 scene-neutral/F-COV；已有初始 fleet 成员接替；有运行时绑定时先关闭退出成员控制门禁并回读，再发布事件；释放和恢复记录 | 三场景实际停放/碰撞及故障参考 episode；混合无人机模板、F-INS dispatch 不接收此故障 |
| `member_join` | 协议会话接收实际 join 后更新当前成员，初始题包 hash 不变；`MultiRobotDirectEnv` 将预加载 reserve 固定在停放位，回读后解除控制门禁 | 三场景 reserve 停放位、加入路线与物理参考 episode |
| `capability_change` | 协议会话要求能力版本递增；仅 `navigation_interface` 已接入 controller 热路径门禁并精确读回，其他物理能力变化被拒绝 | 三场景能力事件计划、sensor/末端执行器变化适配及物理参考 episode |
| immediate | F-INS shared 消息链 | 通用场景领域消息消费 |
| delayed | F-INS 时间门禁；通用协议有逐接收者/信道确定性队列 | 实时领域观测生产适配器；不是无线电测量 |
| dropped | 通用协议逐信道/接收者选择性丢弃和私有账本 | 真实信道模型与实验标定 |
| stale | 过期投递/消费拒绝及拒绝记录重放 | 实时领域观测生产适配器 |
| bandwidth_limited | UTF-8 字节窗口、FIFO 分片服务、条件变化及完整重放 | 非物理网络吞吐；有排队字节时的带宽修改显式拒绝 |

退出搬运中的当前释放是 object 实际 XY 加配置的 spawn Z，并保留 carry Z 记录，
不是测量支撑面的自由落体或接触动力学。不得把“at measured carrier pose”扩写为
完整 XYZ 物理掉落；该限制应保留在对应运输验收中。

## 被测模型到底收到什么、能决定什么

三个业务轨道给被测模型的是以下**四类输入**，不是整个作者场景或 evaluator 目录。其中
`factor_manifest` 是 `public_task.json` 内的字段，不要求另有同名文件：

1. `public_task.json`：场景、公开 fleet/能力、目标区、固定 DAG/依赖、资源、逐节点
   业务角色、交付物和物理限制；`business_rules.task_skeleton` 给出可再生成的职责骨架。
   Warehouse/House 的对象使用公开 opaque 标签，身份绑定留在隐藏侧。
2. `participant_api.json`：动作和事件 envelope、动作作用范围、当前运行时支持状态。
3. `factor_manifest`：可比较的业务因子和等级，以及“单次只改变一个因子”的规则。
4. 运行中按时到达的 observation/lifecycle 事件：模型只能消费已经公开的事件，并以
   当前轨道允许的动作响应，不能从事件词汇表推断所有事件都已接入 Isaac。

公开包提供 fleet、目标区、固定 DAG/依赖、资源、技能需求、角色说明和动作协议。
真值身份、未来干预/投递时刻、参考指派与评测器 fixture 必须留在隐藏侧。
公开候选站点与标签到站点的规则是问题约束，不是泄漏当前真值。

Factory 只让模型控制 `route_confirmation` 的 `navigate_goal/wait/consume_message`，
不能把前三阶段的自动成功记成模型规划成果。Warehouse/House 的 public task
允许 generic Isaac runtime 实际接入的 allocate/reassign/navigate_goal/consume_message。
相机观测先进入确定性通信队列，只有已送达且被目标后继显式消费的观测才可放行依赖节点；
同一物理消息可由其声明的多个后继各消费一次，不重复计算传输字节。
`build_scene_runtime_contract(..., protocol_components=True)` 单独声明无运动的协议模式，
由 `InterventionSession` 应用五类动作，包括等待截止时间与真实投递消息的消费。
`wait` **技能节点**可运行，不等于 generic Isaac 中模型 `wait` **动作**已经生效。
协议注册/能力变化及动作接受不会产生物理到达、订单回执或任务完成。

benchmark 管理场景和资产版本、初始机器人及声明能力、固定子任务与依赖、对象的业务
绑定、目标区、阈值、预算、资源预约、基础运动控制、证据采集和私有评分；Factory 的
前三个取证阶段也由 benchmark 管理。模型输出是合法的 `participant_action` envelope，
可附解释文字，但解释不进入机器得分。模型不能重写任务 DAG、承诺未知载荷能力、
用自报姿态或“成功”替代采样，也不能读取未来事件来规划。

隐藏评测器保管 object→SKU/物品身份真值、真实标签、未来故障/消息日程、候补池、
参考指派，以及核验 detector、dock service 和 access-path 记录所需的独立真值。
公开验收要求和评分方法，让模型知道要达成什么；本轮答案和独立真值留在隐藏侧。
可选 baseline 不是标准答案，也不能作为额外真值通道。

当前模板的观察者通常只有一个无人机；单机 transport 要求的机械臂接口
通常只有 M20+Z1；joint_transport 恰好选两名地面成员时往往只有一个团队组合。
固定目标与前置 DAG 又减少了选择。因此必须单独报告**可行候选数、模型实际
控制的节点、动作是否改变执行、是否存在至少两个业务上不同的合法选择**。
不能把多节点自动执行包装成模型自主任务分解或异构团队优化。

## 机器验收：现有判据与缺口

`scene_challenge.evaluate_objectives()` 保留原始 scheduler 完成计数，用于诊断和部分分。
`evaluate_challenge_report()` 对完整 Warehouse/House 模板调用
`domain_evaluator.evaluate_domain_evidence()`，整题 `task_success` 同时要求节点完成和
业务谓词通过。`scheduler_task_success`、`business_task_success`、
`domain_predicate_fraction` 分开输出。场景包装器的 `design_validated` 仅描述骨架；
`scene_semantics.validated` 现在取实际领域验收结果。导航 smoke 和 Factory F-INS
保留各自小范围，不能因为它们通过便声称完整业务任务完成。

`build_domain_contract()` 从作者侧实例编译隐藏绑定，公开 `domain_requirements`
只给订单行/item code/数量、消费者 objective 和净空要求，不给 object→身份真值。
记录必须带契约 hash。旧 `success=true`、`resident_safe=true`、全部节点 succeeded、
缺帧来源、错误所有权或缺少最终对象位姿均不能使新谓词通过。

| 业务交付 | 评测器中的机器谓词 | 当前尚缺 |
| --- | --- | --- |
| 订单身份/数量 | 私有对象集合与独立 detection IDs 一致；frame SHA/来源/时间有效；检测位置与实测对象位置一致；消费早于动作 | 原生对象上的 SKU 感知、真实领域检测消息；模型自主选择拣选对象 |
| 对象交付 | 全对象所有权账本不冲突；pickup→release 顺序、团队人数、执行窗口、目标区、post-step 位姿及最终所有权 | 原生物理抓取、载荷动力学；恢复分支需独立扩展此领域契约 |
| dock 交接 | 最终派送完成后，隐藏 dock service 的最新未过期 ready 状态、同一对象回执、当前 dock 观测及确认窗口 | live dock service；**派送前**放行仍需改动当前 DAG 和动作协议 |
| 居家通行 | 前后观测与移动/撤离时序绑定；路径连接指定入口/生活区；冻结占据图与所有实测实体/地面机器人足迹；全线净宽至少 0.8m | live 前后采样；非二维安全条件；独立待机区的实测到达与最终足迹 |
| 小物身份 | 私有身份→检测/帧→动作前消费→同一物体交付→最终暂存区 | 模型找物/选择动作、真实视觉身份；已知代理搬运不计搜索正确率 |

`domain_adapters.SceneDomainEvidenceAdapter` 是可注入的采集边界。运行器先用
`bind_runtime` 绑定 Isaac 场景、实际 `ObservationStore`、占据图和公开题包；每个真实
相机帧写入 store 后立即调用 `process_frame`，detector 只能返回 claim，由公共边界绑定
source observation 并整批校验后写入。回合结束再调用 `collect` 接收
`identity_checks`、`domain_service_events` 和 `access_paths`。绑定上下文不包含隐藏
domain contract 或 object→身份真值，帧 detector 也拿不到 `base_env`。默认没有真实
rack/SKU detector、dock service 或家居动线采样器；
未接入时 `domain_evidence.adapter_status` 保持 `pending_live_scene_adapter`。
即使 scheduler 全部完成或进程退出码为零，也不能据此通过业务评分。
`live_claims_recorded` 只表示记录已收集，仍须独立领域谓词验收；collector 输出异常
时原子回滚该批记录并标记 `collector_error`。帧处理错误是粘性的，末端 collector
不能用已留下的部分 detection 恢复业务证据。

`reference_domain_providers.py` 给出两场景的严格参考接线。Warehouse 将 SKU detector、
dock service 和 Isaac 对象 pose backend 组合成同源回执；Modern House 只在指定入口/终态
scan 帧采样路径，并要求 `hall_release` 的可信节点完成事件先发生、占据图 hash 全程一致。
这些类不内置识别权重或伪造 service 状态；没有实际 backend 时仍不能生成业务证据。

以上纯测试使用明确标识的 synthetic fixture。评测器输入应由受控 harness 保管，
不可让被测模型提交自己的姿态、帧 hash 或 dock service 日志。源码/资产 provenance
验证和真实执行仍是独立发布门禁；新谓词不会自动把 `execution_validated` 设为真。

仍需要另外实现的完整业务闭环如下：

| 业务交付 | 可机器化的条件 | 缺失时 |
| --- | --- | --- |
| 工位派遣 | 图像/消息来源可回放；若使用消息，消费早于提交且未过期；真值规则选择正确站点；提交后位姿进入公开 radius；fallback 另计 | unknown 或明确失败，不赋予信息使用得分 |
| 生产恢复 | 真正异常状态→隔离动作→复核→恢复放行，不能用 F-INS 的正确派遣替代 | 未实现，不赋予生产恢复得分 |
| 派送前 dock 放行 | `publish <= consume <= dispatch_start < valid_until`；消息对应当前 dock，状态允许交接 | 必须更改现有“派送后 scan”的时序 |
| 自主业务计划 | 模型选择订单/物品/业务备选方案，动作真正改变运行，而非只给既定对象换载具 | 当前候选仅支持固定 DAG 分配，不能计为自主业务分解 |
| 团队恢复 | 实际退出事件→中断节点→已在 fleet 的合格替补→释放/恢复链→完成；事件时序及资源不重叠 | unknown；不得声称 join/capability-change 鲁棒性 |

阈值必须是冻结配置或实测校准结果；不能在看到答案后调节。新增字段、事件与
谓词要同时贯通公开题面、私有绑定、执行采样及评分消费，并配成功/缺证/伪证反例。

## 无人机与楼梯：基础运动和验证边界

无人机是 benchmark 管理的简易运动基线：二维 A* 航点加世界高度目标，使用连续
**swept-AABB + 保守体素**检查每段机体包络、实际位置命令及控制步间实测位移；阻塞时
`plan_local_aerial_detour()` 尝试有界局部绕行，并复用同一门禁。检查包含当前任务物体
的保守包围盒；未知、无可行绕行或越界不能当作自由空间。它不提供完整三维规划、
动态障碍预测、并发机器人避碰或飞行安全证明，也不作为异构多机协作能力得分。
运动基线失败应与模型的业务决策失败分别报告。

Warehouse/Modern House 默认使用 0.6 m/s 有界运动学空中模式，以隔离 provider 控制器
收敛误差与任务规划因子；Factory 保持其独立 CF2X 运行路径。运动学模式的每个命令段和
实测步间位移仍需通过上述门禁，最终扫描仍要求实测悬停和相机帧。它不证明飞行动力学、
执行器限制或抗扰动；`--aerial-motion-mode controller` 仅作为独立飞控诊断。
NVD 正式模板中的 Iris 只挂 camera，不用 LiDAR 假装具备额外感知能力；voxel、任务
对象 AABB、有限局部绕行和逐步 tracking 是 benchmark 管理的简易避障基础设施。

三个场景的配置均声明各自的 `drone_voxels_0.25m.npz`，加载时还保守合入地面碰撞
代理。Factory 新增 `build_factory_voxel_product.py`，将启用碰撞的原生 USD Mesh
世界 AABB 栅格化，sidecar 记录根 USD、地图及产品 hash 和生成参数；这不是把二维
地图当作三维证据。配置声明和本地产品不证明几何完整、provider 发布或飞行通过。
缺失、无效产品或元数据校验失败仍拒绝准入，历史飞行记录不能豁免当前门禁。

楼梯保留为体现形态差异的**显式垂直任务入口**：`F-VERT-01`/vertical metadata、
`main_stair` 和 `vertical_task` 候选探针允许单独检查上层工位可达性。普通
F-INS/F-TRAFFIC/F-COV 地面任务及 mixed DAG 中的普通地面节点仍避开未验证垂直表面，
不能借开放入口取消它们的碰撞和支撑约束。

入口开放或 `admitted_for_probe=true` 仅表示可进入候选协议；当前准入声明保持
`execution_validated=false`。探针由 benchmark 管理控制器，不是模型已能通过通用
participant API 控制爬楼。当前 `scenarios/vertical_stair_01.json` 和独立
`vertical_challenge.py` 可声明/编译候选题包；原 `challenge.py` 仍是派遣专用。
OpenUSD 静态测量已对根层和 67 个依赖层做 hash 绑定，识别出两段 flight；编译器可
通过私有 `--stair-measurement` 输入选择 ground→mezzanine 的 `flight_02`，并把报告、
根层和依赖集合 hash 写入隐藏评测配置。踏步约 0.15m 高、0.40m 深、2.60m 宽；
`flight_02` 两端都有 authored horizontal support candidate。公开候选的接近、入口和
终点仍保持未测量声明，因为静态 polygon 不证明 PhysX 支撑、连通性或机器人可行走；
`release_gate.executable=false`、`release_gate.scoreable=false`。

F-VERT-01 是三场景业务轨道之外的单机基础设施诊断：模型只能提交给定机器人的
`navigate_goal` 路线意图或有界 `wait`，不能提交关节指令、改写位姿或声称成功。
当前只验证意图格式，`executed_action_types=[]`，Isaac 意图执行 adapter 尚未接入。
此候选提供题面/API，尚无运行事件输入，也未提供业务轨道的 `factor_manifest`；
生命周期和通信条件仅为词汇且未启用。隐藏侧保留支撑校准、参考轨迹和独立物理
验收入口，缺失记录保持 unknown。编译题包不意味着部署可执行、可计分的爬楼任务。

探针先以零指令采集校准数据，在 `env.step` 后记录接触；接触力换算使用
`physics_dt`，轨迹和事件使用控制步 `step_dt`。无校准支撑/接触证据时停止推进。
只有绑定当前场景、配置和源码的支撑面、完整 WXYZ、连续健康停留及实测 XYZ 到达
证据才可能支持执行验收；目标高度、teleport、包围盒上沿和机器人形态都不能代替这些
证据。本次不宣称爬楼通过，也不改变任何物理成功阈值。

## 下一步集成与 LLM 再生成

`tools.scenario_generation.common.manifest()` 已附带 `business_task_authoring`。
该作者输入由 `demo.benchmark.authoring.build_authoring_contract()` 生成，包含三个
场景各自的任务族、逐节点业务角色、道具、因子支持清单、允许变化及必须保留的规则。
LLM 产出仍是 ScenarioSpec JSON；Warehouse/House 通过
`validate_authored_business_scenario()` 验证，缺角色、错绑技能、用方盒冒充业务道具、
没有领域验收或三维路线阻塞均拒绝。Factory 使用自己的派遣编译器。
这是一套**固定业务 DAG 的受约束再生成入口**；自主选择订单、物品或业务分解仍是
独立扩展，不能把固定 DAG 的分配得分标作自主规划得分。

出题应以实际交付为中心：工厂要解决工位证据与处置去向；仓库要解决正确订单行、
集货与接收确认；家居要恢复指定生活动线并妥善安置物品。搬运、扫描、等待只是达成
这些交付的手段。加入/退出以交班或电量耗尽等业务理由发生，通信变化以隔断、覆盖
或链路拥塞为条件；预先冻结的配对实验只改变一个因子，不能同时更换布局、机器人
预算和物体身份后仍把差异归因于通信。两机器人节点必须另有单机不可行或能力互补
依据，否则只能评测遵守团队约束，不能证明协作必要性。

1. 发布前调用语义审查。完整业务题拒绝 smoke、错绑目标、重复总任务语义和无
   领域谓词；smoke 可单独发布，但题名和交付物必须限定导航链路。
2. 先落实订单/动线的身份、区域、时序和度量，再增加不同条件及合法替代方案。
   LLM 生成的条件只能选已接入执行与评分的能力；未来能力留在独立设计 catalog。
3. 对齐 public allowed actions、API implemented/pending、实际 dispatch 分支；
   加入模型动作无效/被忽略、过期消息、伪完成报告等反例。协议回放不当作 Isaac 实测。
4. 业务分数、原语完成度、干预恢复和效率分别报告；通过元数据不自动通过 release QA。
5. 每阶段先列待办，再核验“输入是否足够、模型是否真的有选择、证据是否有独立来源”。
   保留失败和反例；领域适配、垂直执行或动态实体缺口未关闭时仍标记 pending，
   不靠增加任务叙述、节点数或协议事件名称提升完成状态。

纯验证命令（无 Isaac）：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q -p no:cacheprovider \
  source/EAI/test/test_benchmark_semantic_contract.py \
  source/EAI/test/test_benchmark_domain_evaluator.py \
  source/EAI/test/test_benchmark_dynamic_participant.py \
  source/EAI/test/test_benchmark_challenge_contract.py \
  source/EAI/test/test_scene_challenge_reuse.py
```
