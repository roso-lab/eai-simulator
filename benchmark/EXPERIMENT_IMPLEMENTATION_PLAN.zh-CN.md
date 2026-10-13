# EMOS 论文实验主干实施方案（按步骤执行）

版本：experiment.mainline.v1
当前状态：P0 运行架构已完成；P1 任务语义和外部物理评测待用户审定。

当前工作范围（用户最新明确）：先确定各场景的任务大纲、三大类归属及合同对应关系；具体语义地图由另一位开发者部署。地图就绪后，再按已审定任务需求选点、绑定机器人/物体并真实布置。当前不逐场景冻结坐标、路线、交互范围、精确时限或物理判据。地图开发与任务大纲可并行，以抽象空间/对象需求清单衔接。

## 1. 最终目标

建立一条可重复、可审计、可直接产出 D1–D6 指标的实验链：

```text
论文任务语义
  → 30 个 task anchors
  → simulator.py 场景绑定
  → EMOS decision / assignment
  → Skill Adapter
  → Fleet Runtime
  → TaskExecutor / env.step
  → 外部物理 EvidenceEvaluator
  → fact/reference/sham/intervention artifacts
  → ReleaseIndex
  → MetricReducer
  → D1–D6 论文表格和图
```

边界必须保持稳定：

- EMOS 负责任务组织、角色分配和必要重配置，不负责宣布物理成功；
- Skill 只把固定字段翻译成仿真器动作，不负责物理成功判断；
- Fleet Runtime 负责身份、消息、锁、资源 ownership、safe-stop 和审计；
- tool replay/GPU 层负责 checkpoint、GPU 设备和资源准入；
- 外部 evaluator 负责物理 milestone 和 terminal verdict；
- ReleaseIndex 负责证据是否满足发布门槛；
- MetricReducer 负责统计聚合，不把缺失证据变成 0。

## 2. 当前基线

仓库已经有：

- 30 个 anchor：production、service、living_public 各 10 个；
- P1/P2/P3/P4 relation signature 和 P3-J/P3-S subtype；
- 八维 difficulty vector；
- common-anchor group；
- 8 个 experiment phase、K=3 repeats；
- 四 branch contract、RuntimeTaskBinding、Fleet Runtime、P2 evaluator；
- BranchArtifactStore、ReleaseIndex、MetricReducer；
- benchmark 与 tool scheduler 之间的 BranchResourceGate。

当前 catalog 仍是 planned / not_yet_certified：

- 30 个 anchor 已登记；
- 9 个 common-anchor group；
- 每个 domain 覆盖 difficulty tier 1–4；
- common-anchor certification 尚未完成；
- oracle certification 尚未完成；
- 默认计划是 1,386 个 repeat-level cells，约 3,033 个 branch execution units。

因此，不能把当前 catalog 当作已经完成的物理实验，也不应直接启动全量矩阵。

## 3. 总体阶段

```text
P0 接口冻结
  ↓
P1 30 个任务语义设计与用户审定
  ↓
P2 场景绑定、oracle 和资产认证
  ↓
P3 外部 evaluator 与 milestone 认证
  ↓
P4 GPU / replay / branch 预发布检查
  ↓
P5 小规模 pilot
  ↓
P6 全量 phase 执行
  ↓
P7 ReleaseIndex + MetricReducer
  ↓
P8 论文表格、图和审计包
```

不能跳过 P1 直接冻结 P3 evaluator；evaluator predicate 必须由 obligation graph 和物理 milestone 反推。

## 4. Step 0：冻结实验接口

### 4.1 必须冻结

任务输入使用 EaiTaskPackage；graph 使用 CoordinationTask；EMOS 输出 AlgorithmDecision；Skill 使用：

```text
SkillRequest → SkillPlan → TaskExecutor → ExecutionFeedback
```

外部物理证据使用：

```text
observations → EvidenceRecord → EvaluationReport
```

每个 branch artifact 至少包含：run key、branch role、task binding、decision、execution records、observations、evaluator report、Fleet audit、tool/replay audit 和 ledger hash chain。

### 4.2 验收

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
-m pytest -q algorithm/skills/tests benchmark/tests
```

当前基线为 83 passed。tool scheduler 交付后再单独运行 tools/physics_replay tests；缺少 demo.fire_rescue 的既有测试要作为环境缺口处理，不能用跳过失败伪造完整通过。

## 5. Step 1：用户审定 30 个 anchor 语义

这是第一个必须由用户介入的阶段。

2026-10-11 更新：新定义以 [Common anchor 合同语义登记表](COMMON_ANCHOR_CONTRACTS.zh-CN.md) 为准，当前 11 个合同的高层语义已确认。旧 catalog 合同已被用户废弃；本文前述旧覆盖数、矩阵规模与 common 分组只描述旧草案，不能作为新合同的发布证据，后续迁移需重算。具体场景配对、物理谓词与阈值仍待审定和认证。

### 5.1 common-anchor 的判断原则

common-anchor 只要求抽象语义等价，不要求表面相同。需要比较：

- obligation graph 是否表达相同依赖流程；
- difficulty flow 是否处于同一难度区间；
- capability role 是否满足相同抽象能力约束；
- milestone 是否采用相同测量机制；
- temporal/resource constraint 是否可比；
- terminal value 是否属于同一论文语义维度。

不要求机器人型号、物体、地点、自然语言、路径和坐标相同。

例如 production 的“箱子送到交接区”和 service 的“书本送到服务台”，可以共享：

```text
precedence → release → handoff → accept
载荷移动能力 + 接收确认 + 外部 milestone 认证
```

### 5.2 每个 anchor 必须审查的字段

| 字段 | 审查内容 |
|---|---|
| anchor_id / domain / scene_key | 身份、领域和场景别名 |
| family / typical_or_common / common_anchor_id | 任务族和 common 分组 |
| primary_label / relation_signature / p3_subtype | 论文协调原语 |
| objective / obligations | 表面目标和抽象义务 |
| required_capabilities / roles | 抽象能力与角色 |
| milestones | 外部可测里程碑 |
| temporal_constraints | 顺序、窗口、deadline |
| resource_constraints | lock、容量、排他资源 |
| difficulty_vector | 八维 0–3 分箱 |
| factor_support / terminal_value | 支持的 D1–D6 和终止语义 |
| oracle_status / certification_status | 是否可以进入正式 benchmark |

### 5.3 Step 1 的用户交付

用户先确认：

1. objective 和 obligations；
2. common-anchor 抽象等价关系；
3. P1/P2/P3/P4 primary label；
4. capability constraint；
5. 正式 benchmark 与 diagnostic-only 的边界；
6. 暂缓的 USD/GPU 场景。

在此之前不冻结 30 个物理 evaluator predicate。

### 5.4 场景级长任务与阶段级测量

采用两层任务结构：

- 一个场景可以承载一个长任务 `Mission Bundle`；
- 长任务由多个可组合的 `Contract Stage` 组成；
- 每个阶段拥有独立 obligation graph、milestone、evaluator window、checkpoint 和风险集；
- 阶段级 contract episode 是 D1–D6 的主要测量单位；
- 长任务只额外报告整体完成、失败传播、恢复和累计成本，不替代阶段级 paired evidence。

因此，场景连续性由共享物理环境和阶段间状态传递保证，机制可归因性由阶段边界和独立证据保证。一个阶段可以由不同数量的机器人执行，但 anchor 本身不绑定固定机器人数量。

首个复合合同现在使用 `C-P1P2-01`：可并行前置义务汇合后进行责任交接，主标签为 P2，分关系保存证据。新登记中的 `C-P1-01` 是随后单独确认的纯 P1 合同。新旧同名 ID 必须由修订版本区分。

阶段切分不产生独立统计样本；正式推断仍保持论文的 base-instance 标识、重复层级和配对风险集。阶段评分、完整 episode 的 D6 成本与长任务结果需要分别定义汇总范围。

### 5.5 机制模板、场景任务与物理测量必须分层

任务设计按三层审定，不能用某个场景的表面任务代替 common mechanism：

1. `Mechanism Contract`：场景无关，冻结 obligation graph、完整 relation signature、抽象角色/能力关系、前置条件和 milestone family；
2. `Scene Task Instance`：将机制映射为餐厅、家庭、工厂等具体目标、物体、可见信息和候选机器人，允许表面语义不同；
3. `Evaluator Binding`：将 milestone 绑定到物理状态谓词、阈值、稳定窗口、时钟和证据来源。

餐厅中的 Pepper/G1/Go2 只用于检查机制模板是否可实例化。家庭场景不必复用餐厅角色或物体；如果属于同一 common anchor，只需保持抽象义务图、能力关系和测量机制一致。typical-only 任务则允许采用该领域独有的机制结构。

### 5.6 首个场景任务包审定：餐厅（交付边界已确认，其余逐项审定）

11 个机制合同及合同登记表 §7 的六类事件条件已完成高层讨论；这不代表所有场景适用、物理可执行或统计面板已认证。接下来逐场景审定任务包，沿用场景身份清单但不复用已废弃 catalog 的合同语义。

餐厅候选正常任务：团队在注册服务时段内完成三桌顾客的点单、按订单选择并装盘已有餐品、装载运输、桌边卸载与交付闭环。三桌仅为初始工作量提案，不冻结难度或机器人数量；暂不加入烹饪和清洗。Pepper/G1/Go2 是候选角色例子，实际能力与资产需认证，不能假设某个型号天然支持全部动作。

用户已确认正常义务须覆盖物理交付：餐品由机器人实际放到指定桌面位置，运输机器人仅到达桌边不等于完成交付。若运输机器人不能卸载，由池中具备操作能力的成员完成桌边卸载，并计入其移动、占用及后续工作；不假设顾客自动取走餐品或物体瞬移。

多桌订单允许形成重叠推进机会，所有已登记订单仍属于同一团队任务包。按点单信息到达后的备餐依赖登记 P2 precedence，按实际责任/状态转移登记 P2 handoff；不把依赖订单的备餐强行标为与点单并行。共享装载位仅在真实竞争及准入/占用约束成立时加入 P4。长时间接单本身不自动成为 P3-S。

common 实例从已批准机制中选择并另行认证，例如以各自都需要资源准入及责任转移的跨域任务构造 C-P2P4-01 配对。餐厅完整任务不因具有部分相似阶段而整体自动获得 common 资格。事件先形成单因素可解释条件，再按明确测量需求设计组合，不把全部事件塞进一次执行。阶段评分保留共同 base-instance 归属。

本节只推进语义讨论，不承诺现有资产/技能已支持，也不替代后续 30 场景清单和跨域覆盖审查。

### 5.7 机器人池、简历与空间调度（用户原则已确认，字段方案待实现）

用户明确：机器人池从 `usd/robot` 资产中选择，不限于示例中的 Pepper/G1/Go2 或每类一台；不同初始坐标的机器人实例均可由被测系统调度。每个实例必须有机器人简历，使系统了解其能力。位置通过实际运动步数、耗时等影响执行成本。

区分三个层次：资产库提供可实例化的机器人类型；实验配置固定本轮候选池及各实例的初始配置；被测系统在允许调度的池内选择执行者、分配与重分配工作。同一资产允许多个实例，每个实例有唯一 robot_id、独立位置和状态。已在池中且可调度的空闲机器人被启用不自动算成员加入；成员加入需改变注册的可用成员集合。不要求每个候选成员必须工作，也不由 benchmark 预填“最佳执行者”。

以下为统一数据语义建议，字段名尚未冻结为接口：

| 信息 | 建议字段 | 用途与来源 |
|---|---|---|
| 资产与实例身份 | robot_id, asset_id, asset_version, embodiment_config_id, profile_version | 绑定 `usd/robot` 的实际资产及附加臂、载具、传感器配置；路径/哈希留在工程绑定中，不要求模型解释 USD |
| 能力简历 | capabilities, supported_skill_ids, constraints | 移动、感知、交互、操作和运输能力及实际速度、载荷、可达范围等适用约束；由有效配置及技能支持核验，不凭机器人名称推断 |
| 场景初态 | initial_pose, frame_id, units, initial_availability | 固定出生位置/朝向、坐标系和初始可用性；作为实例配置参与配对 |
| 公开动态状态 | observed_pose, availability, active_obligations, observed_at, source | 表示已获知的位置、忙闲和当前义务，按观测/通信规则更新；如有电量模型再加入相应字段，不伪造实时全局状态 |
| 能力证据 | evidence_ref, validation_status | 追溯控制器/技能可执行范围；仅存在 USD 不代表所有能力已实现，未知参数保持未知 |

结构化简历为统一输入来源，人类可读描述从同一份内容生成。EMOS 使用自己的输入包装消费这份公开资料，其他被测系统可消费相同信息；Skill 仍接收固定动作字段，不承担选人或填充隐藏状态的职责。任务只规定义务和资格，实际机器人身份由被测系统选择。common 配对仍检查抽象能力关系、流程难度与测量语义，不要求具体机器人型号一致。

位置成本从实际执行轨迹取得，不直接把直线距离作为最终成本。建议同时记录：整轮仿真 tick 数及时间、各机器人实际累计路径长度、各机器人运动持续时间/步数，以及等待与操作时间。固定 physics_dt、控制周期/decimation 和运动判据；并行时全局 tick 与机器人累计运动步数分开，轨迹缺失不能补成零。直线距离只能作为规划估计，绕行、速度、载荷和排队均可能改变实际代价。正式指标保持论文 D6 已登记的成本向量与主指标；新增路径/运动步诊断不擅自替换主指标或合成为未审定的总分。

比较系统时固定候选池、配置、初始坐标与信息权限；配对分支共享干预前快照，仅改变已注册处理。池大小、冗余能力和空间分布是实验条件，不能在同一配对比较中随意变化，也不因实际被选择的人数不同而事后重分难度。

2026-10-11 只读核查：`usd/robot` 下实际存在 g1、go2、pepper、carter、scout、m20、b2、lite3、cf2x 等资产目录，但本次未认证可执行能力。现有 `algorithm/system/coordination/emos/resume_provider.py` 已支持环境生成或 `scenario.robot_profiles` 静态简历；静态 `RobotProfileSpec` 目前主要包含类型和能力列表，provider 的位置缺失回退为 `(0, 0)`，正式输入应显式表示未知。`brain/robot_resume.py` 部分描述与能力推断仍依赖工厂固定实例名称，需要迁移到实际配置绑定；不能把这些历史默认描述当作新机器人池的认证依据。当前仅更新设计，不改动协作者运行代码。

### 5.8 餐厅能力冗余与空间布局（配置原则已确认）

用户已确认首个正常配置采用有限冗余、能力部分重叠及位置分散的原则，能力稀缺作为另外的实验条件。具体 USD 型号、数量、坐标和技能证据后续绑定；原则确认不等于具体机器人配置已认证。

| 必要能力组 | 工作范围 | 候选池与位置提案 |
|---|---|---|
| 顾客交互与订单获取 | 获得餐品选择、桌号及必要约束，形成可追踪订单 | 提供多个合格候选，分布在入口与用餐区域；交互如何在仿真中实现仍需场景绑定 |
| 餐品操作 | 按订单取放已有餐品、装盘、装载与桌边卸载 | 提供多个合格候选，分布在厨房与大厅；核验各自对工作台、载具和桌面的可达性，不默认所有操作角色能操作所有位置 |
| 稳定载荷运输 | 将已装载餐品运至目标桌边，配合装卸 | 提供多个合格候选，分布在出餐区与餐厅其他区域；核验载具、载荷、路线和速度适用范围 |

上述为能力组，不是三个固定机器人编队；同一实例可以具备多组能力，也可承担多个阶段。所谓多个候选是冗余配置提案，不等于已经认证退出后仍可行，后者还取决于位置、剩余时间、占用和状态获取成本。

首个正常配置采用部分能力重叠及有限冗余，使系统既需要异构合作，又有真实的选人和调度空间。作为单独注册条件，再构造关键能力稀缺及退出后不可行配置。不能在运行中为使某系统通过而临时补机器人；系统间比较固定候选池和初态。

空间布局应包含有意义的调度权衡：近处合格成员正在承担另一项登记义务，远处合格成员可以更早空闲，或速度/可操作范围不同。忙碌必须源自任务内真实义务，不能凭空制造背景阻塞。到达成本与原任务受影响程度共同决定安排，最近机器人不必总是最优，也不向系统提供 evaluator 的最优分配答案。

协调测量边界：典型任务允许多能力机器人合法承担多个阶段；不能把一次单机器人连续执行算成跨机器人责任交接。用于 common handoff 比较的实例必须通过真实装备、任务对象或环境条件形成必要协作，保留已注册的交接义务。若某池配置可以合法完全绕过该义务，应在发布前调整实例或选择适用合同；运行后不允许因交接失败或未触发而删掉原有分母。

具体桌数、型号、人数和坐标尚未冻结。

### 5.9 餐厅订单的信息获取（信息规则已确认）

用户选择具体订单需现场交互获取：正常任务开局公开待服务桌号/位置、可点餐品目录及公开规则，具体顾客选择通过合法的现场点单交互获取。顾客偏好在实例初始化时已固定，不由模型回答或运行结果临时决定；评测器可持有核验用真值，但不将未获取的订单内容直接注入模型任务提示。

交互成功后向发起者返回固定结构的订单内容，包含订单身份、桌号、餐品种类/数量与已声明约束，并按系统已有架构及注册通信通道传播给需要该信息的决策/执行成员。现场交互的位置、朝向或可达范围及耗时必须公开登记并经技能认证；无需默认引入自由对话、语音识别或满意度评分。基准点单内容可无歧义，避免将语言识别误差混入协作测量。

初始待服务订单全部属于初始工作集：被机器人稍晚询问到不等于新增需求事件。新增需求实验另行登记真实到达时刻与新增义务版本。服务等待从注册的顾客请求/可服务时刻计算，不能通过延迟点单重置等待起点。

系统自由选择访问顺序、交互成员与并行方式；允许在未知具体订单时准备通用物品，但依赖顾客选择的备餐/交付需满足订单信息及相应前置条件。成功要求实际交付的餐品、数量与桌号匹配登记订单，不以机器人自报完成替代。当前仅审定信息获取原则，具体桌数、订单组合、时限和交互接口均待后续确定。

### 5.10 当前审定粒度与语义地图交接（用户明确的流程约定）

最新工作流（2026-10-12 用户指定）：用户逐次提供场景名、亲自观察到的结构/对象及任务想法，主代理据此整理类别、团队流程、合同与 benchmark 测量，再提出有观察依据的可选扩展；有必要才追问关键缺口。详见任务大纲表的“用户观察驱动的逐场景工作流”。30 场景 v0.1 降为备选素材，不作为需要用户逐项接受的默认方案；具体任务语义由逐场景讨论形成。

逐场景任务大纲统一维护在 [30 场景任务大纲审定表](SCENE_TASK_OUTLINES.zh-CN.md)，避免继续在本实施方案中展开每个场景的细节。2026-10-12 的 v0.1 已核对三组各十个实际目录及分类说明，为 30 个场景分别提出宏观主题、A/B 任务候选、合同/测量问题与地图/能力条件，并附 11 合同覆盖反查。全部新候选尚待审定及地图适配；旧 catalog 不作为其任务语义来源。

当前目标是形成 30 个场景的任务大纲及覆盖表，不继续把单个餐厅展开成完整布点方案。每个场景可以包含多个任务或可组合阶段；先明确团队要完成什么、为什么需要协作、用哪个合同测量，再等待地图进行空间实例化。

2026-10-12 用户补充：三大类确定宏观主题，不能用单一任务模板概括同类场景。具体任务结合场景已有结构和对象提出，例如有楼梯/二楼的工厂可考虑巡视与应急响应，储存仓可考虑查找及搬运货箱。当前保留多个带条件的任务候选，区分已知特征与待地图核验项；地图用于验证、选点和细化，也可能要求调整候选任务。正式实例的终端价值分类仍单独核对。

三大类依论文 Table 2 的终端价值语义区分，不能只按场景名称或某个动作归类：

| 类别 | 主要终端价值 | 当前需写清的内容 |
|---|---|---|
| production（生产） | 物料流程完成 material_flow | 所需物料/产品如何完成处理、转运和交付，受哪些产能/时限约束 |
| service（服务） | 可追责的服务连续性 accountable_continuity | 谁或哪个范围得到服务，未完成请求与责任如何持续保留、响应和接续 |
| living_public（生活/公共） | 通行、使用、安全与隐私 access_safety_privacy | 谁在什么权限和安全条件下获得空间/设施使用或协助 |

任务类别、机制合同、典型/common 身份和事件条件分别登记。类别决定要实现的价值，合同决定协作关系，事件改变实验条件；不能用 P1–P4 替代三大类，也不能把每个 service 任务自动标成 P3-S。每个可评分合同按论文登记所属终端价值族；一个场景容纳不同任务不意味着同一结果可重复计入多类。无需把每个场景强行扩成三类任务，也不因本表而把 30 场景自动扩成 90 个独立样本。旧清单中的领域数量只是历史设计，需在新任务覆盖表重新核对。

当前每份任务大纲只需记录：场景身份、任务类别及归类理由、团队目标与主要任务链、必要能力组、适用合同及完整关系签名、典型任务/common 候选的区别、适用事件和对应测量问题、抽象地图/对象需求、待地图核验事项。合同引用先作候选映射，发布前按实际义务图认证；不为满足覆盖数强加不自然任务。

交给地图开发者的是需求，例如顾客交互位、取餐/装盘区、可装卸载区域、餐桌交付面、候选机器人部署区以及有需要时的共享通道，并注明所需可达性、承载/操作面或排他关系；这些不是已经存在的地图点位。地图交付后由任务侧按需求选点，核验机器人与载荷可达性及协作关系是否成立，再确定距离、坐标、时限、数值阈值与 evaluator binding。如果地图不支持义务，显式调整任务或地图需求，不能用虚构对象/瞬移凑齐流程。

餐厅当前确认的是实际桌面交付、可调度机器人池及简历、有限能力冗余/空间分散、现场获取订单。桌数、型号、坐标、精确截止时间不在本阶段冻结。餐厅大纲若归 service，应明确保留订单责任、服务响应和接续义务，而不能仅因场景名叫餐厅就归 service；仅以餐品物流完成为终端价值的任务应按 production 语义审查。当前先完成跨场景大纲，不再逐个追问地图部署细节。

## 6. Step 2：生成统一任务模板

目标是所有任务由统一模板生成，不为每个场景创建私有 runner。

建议每个 anchor 的 authoring 资料为：

```text
task/benchmark_catalog/<anchor_id>/
  task.json
  oracle.json
  milestone.json
  scene_binding.json
  README.zh-CN.md
```

如果文件数量过多，可以保持 catalog 单文件，但字段结构必须等价。

### task.json

至少表达 task graph、obligations、capability constraints、temporal/resource constraints 和 observation schema。

### scene_binding.json

只描述如何绑定 simulator：asset manifest、robot/target/controller binding、GPU requirements；不能改变论文语义。

### oracle.json

oracle 是实验内部参考，不是被测 EMOS 输入。必须区分 algorithm-visible、oracle-only 和 evaluator-only fields，并生成 certificate digest。

## 7. Step 3：落实论文协调原语

### P1：初始组织

- graph 无环；
- required capability 清晰；
- assignment 初始可行性可由 oracle 判断；
- infeasible 可被 oracle 认证；
- 不用物理结果反推 initial plan validity。

### P2：precedence / release / handoff / accept

统一抽象为：

```text
precedence opportunity
  → predecessor reaches release opportunity
  → release receipt
  → receiver eligibility / handoff
  → accept receipt
  → stable acceptance window
```

不同场景可以改变机器人、物体和地点，但必须保留位置/状态、zone、eligibility、receipt、稳定窗口和时间顺序这些测量机制。

### P3-J：联合执行

必须有共同 obligation、joint capability 和同步窗口；普通串行 handoff 不能标为 P3-J。

### P3-S：服务连续性

必须记录覆盖区间、coverage gap、接替者和 uncovered service time。

coverage gap 不能直接等同为“系统没有响应”。事件后必须分别记录：系统是否获得事件、是否形成有效响应/重规划、是否显式安排延后处理、实际动作何时开始、服务何时持续恢复。至少区分 `planned_deferral`、`late_response`、`unhandled_event` 和 `valid_safe_stop`。P3-S 仍按外部 coverage predicate 计算空窗；D4 则分别评估响应和持续恢复。因此，有效重规划仍可能产生 coverage-gap 代价，而未响应不能因为后来偶然恢复被改写为及时响应。

### P4：资源互斥

明确 resource、lock type、acquire/release 时间和冲突 trace；双 owner 必须禁止。

动态重配置事件（member exit/join、capability loss、demand expansion、service failure、safe-stop）作为事件条件，不混入 P1 语义。

## 8. Step 4：milestone 和外部 evaluator

此阶段在 Step 1 审定后编码。

evaluator 只消费公开 observations，例如：

```json
{
  "tick": 12,
  "sim_time_s": 0.24,
  "robots": {},
  "targets": {},
  "external_events": [],
  "resource_trace": [],
  "communication_trace": [],
  "reconfiguration_events": []
}
```

每个 primitive 都需要：

1. 物理 predicate；
2. 时间 predicate；
3. 缺失证据处理。

缺少外部证据时只能是 N/A 或 unknown；executor feedback 不能替代物理成功；CPU fallback 不能作为正式 GPU 结果。

## 9. Step 5：场景和 GPU/replay 预发布

每个场景必须生成：asset manifest、controller digest、skill schema check、actual physics device、GPU UUID、显存预算、scene/branch slot、并行上限、warmup ticks 和 checkpoint readback。

正式 release 的前置条件：

- asset resolution；
- scene binding；
- Skill schema；
- actual physics device 为 GPU；
- checkpoint readback；
- branch isolation；
- Fleet ledger seal；
- tool audit 可验证。

否则标记 infrastructure_invalid 或 pending，不能自动切 CPU 混入论文结果。

## 10. Step 6：pilot 顺序

不要直接运行 1,386 cells。

### Pilot A：P1

选 production、service、living_public 各一个 anchor，各跑 K=3 nominal fact，检查 task package、EMOS、Skill、Fleet、artifact 和 D1 输入。

### Pilot B：P2 common-anchor

选一个 production/service common pair，检查 precedence、release、handoff、accept、receipt、stable window 和 P2 metric observation。

### Pilot C：P4 resource conflict

检查 typed lock、冲突拒绝、release、双 owner 禁止和 P4 evidence。

### Pilot D：四 branch strict-pair

检查 checkpoint digest、四个独立 session、sham calibration，以及 causal replay 缺失时保持 N/A/unsupported。

四个 pilot 全部通过后才能扩展 phase。

## 11. Step 7：按论文 phase 执行

### Phase 0：qualification

只检查资产、controller、Skill、checkpoint、Fleet、replay 和 audit，不报告论文成功率。

### Phase 1：D1

所有 anchor、K=3：initial plan validity、infeasible declaration、capability/role/milestone validity。

### Phase 2：D2

nominal、delay、loss、jitter、burst、capacity crossing；strict pair 共享 checkpoint，communication trace 独立保存。

### Phase 3：D3

按 relation signature 运行 P2 handoff、P3-J、P3-S、P4 mutex。没有 relation support 的指标保持 N/A。

### Phase 4：D4

member exit 为主 panel，其他 dynamic event 作为保留条件；记录 competing risks 和 event time。

### Phase 5：D5

只使用已认证 common-anchor，并同时满足 causal replay、sham calibration、四 branch artifact 和 strict paired gate；否则 N/A/unsupported。

### Phase 6：D6

只统计 typical nominal fact：offered bits、decision trace、Fleet interaction trace、runtime trace、startup/UWI。

### Phase 7：release audit

不启动 episode，只检查 artifact hash、ledger、coverage、risk set、N/A、paired block、evidence hash 和 GPU infrastructure gate。

## 12. Step 8：指标和论文输出

每个 branch 先生成带 evidence hash 的 MetricObservation，再由 ReleaseIndex 审计 artifact、relation、common-anchor、branch identity 和 required evidence。

MetricReducer 再执行：

```text
repeat → base_instance → 等权 aggregate → cluster bootstrap → D1–D6 tables
```

必须保留 N/A、pending、invalid、denominator floor、exclusion funnel、coverage 和 risk set；不能缺失填 0、以 branch 数代替 base instance、把 physical_state_only 当 causal_replay，或把 CPU 结果混入 GPU 正式结果。

## 13. 建议结果目录

```text
results/
  qualification/
  pilot/
  phase1_d1/
  phase2_d2/
  phase3_d3/
  phase4_d4/
  phase5_d5/
  phase6_d6/
  release/
  tables/
  figures/
  audit/
```

每个 phase 至少保存 manifest、run index、artifacts、metrics、coverage、exclusions 和 README。

## 14. 用户审定点

- A：30 个 anchor 的 objective、obligations、capability、common pairing、difficulty 和 milestone family；
- B：正式 benchmark、diagnostic-only 和暂缓场景；
- C：P2/P3/P4 milestone 的测量机制；
- D：D5 common-anchor、causal replay 和 sham tolerance 门槛；
- E：每个场景 GPU 显存、env 数和 scene/branch parallel limits。

## 15. 当前下一步

1. 用户审定 Step 1 的 30 个 anchor 任务语义；
2. 根据审定结果生成 task template 和 semantic certification checklist；
3. 将 selected anchors 绑定到具体 simulator scene；
4. tool 开发者并行交付 ReplayScheduler；
5. 实现第一批 P1/P2/P4 evaluator 输入适配；
6. 完成 Pilot A–D；
7. Pilot 通过后扩展全量 phase；
8. 接入 MetricReducer 输出论文表格。

在用户审定 30 个 anchor 之前，不冻结具体的 30 个物理 evaluator predicate，也不启动全量实验矩阵。
