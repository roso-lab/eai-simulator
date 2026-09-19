# Benchmark 阶段执行清单

> **历史归档（2026-09-19 标注）**：本清单是 Factory 优先时期（P0–P6）的阶段执行记录。
> 当前权威的阶段清单与勾选状态是 [demo/benchmark/TODO.zh-CN.md](../benchmark/TODO.zh-CN.md)（阶段 1–18），
> 交付状态总览见 [demo/benchmark/DELIVERY.zh-CN.md](../benchmark/DELIVERY.zh-CN.md)。
> 本文件保留为历史证据，条目不再更新。

持续目标：形成 Factory、Warehouse NVD 和 Modern House NVD 的场景化出题、执行与独立验收边界。
当前边界见 [BENCHMARK_DESIGN.zh-CN.md](../benchmark/BENCHMARK_DESIGN.zh-CN.md)，
长期设计依据为 [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)。文档核对日期：2026-09-18。
候选范围为 EAI 当前接入的全部 14 型机器人，不受真机库存限制。
保留真实质量和物理约束；机械臂实现由其他会话负责，本任务仅消费其能力和结果接口。

状态约定：`[ ]` 待完成，`[x]` 只表示该条工作及其明确列出的检查完成。
代码/文档检查、协议回放、历史 Isaac 运行分别记录；勾选不代表整题、当前发布版或
物理能力已通过。下方 P0–P6 与验证记录保留阶段历史，历史数字不因本次文档更新重新
获得当前源码适用性；现状以当前边界指南及以下清单、实现为准。本轮已在
`env_isaaclab` 运行受限 Isaac 探针和 Python 专项回归；结果只更新本轮证据，不重写历史阶段数字。

## 当前出题边界与文档核对（2026-09-17）

- [x] D1 阅读当前编译器、维护任务、authoring/semantic contract、领域 collector、
  participant 生命周期、飞行与垂直执行代码及对应测试断言。反思：声明、准入和协议
  支持不能直接升级为业务或物理成功；复核共享工作区新增的 Factory 体素声明、
  生成器、F-VERT-01 候选场景及独立编译器，仍保留其物理与部署门禁。
- [x] D2 在限定四份文档中统一三场景目的、模型输入/输出和验证边界。反思：固定
  DAG 测分配与协调，不能因为节点多、机器人多就称为自主分解或异构必要性验证。
- [x] D3 用 `rg` 检查“已完成/真实验证”等表述，逐项保留为限定版本的历史记录或
  明确的未验证边界；本次写入操作只涉及指定四份 Markdown。相对链接、代码围栏及
  `git diff --no-index --check /dev/null <文档>` 检查通过。范围快照发现共享工作区
  同时有其他代码新增/修改，已只读复核并同步其文档边界，没有改写这些 Python/JSON。
  反思：文本一致性不等于运行通过，也不能声称整个共享工作区没有其他变化。

| 场景 | 当前任务骨架与业务目的 | 保留的证据缺口 |
| --- | --- | --- |
| Factory | F-INS 的俯视取证、地面接近、近景取证、工位派遣；辅助 F-TRAFFIC 通道调度和 F-COV 工位值守；垂直探针研究上层工位可达性 | 只开放最后派遣节点给模型；没有完整工业异常识别、生产恢复或爬楼验收 |
| Warehouse NVD | 10 节点贯通订单对应的货架扫描、双侧到达、木箱联合集货、纸箱暂存/出库、dock 派送后复核与通道收尾；声明 `wooden_crate_a`、`cardbox_c` | live SKU/订单/库位与 dock 适配器、原生对象执行和物理承载证据待接入；不计自主订单优先级选择 |
| Modern House NVD | 11 节点贯通入口检查、扶手椅整理、指定书籍暂存、机器人撤离、最终生活动线复核；声明 `mh_armchair`、`mh_bookstack` | live 身份和 access-path 采样、物理搬运待接入；不是未知物品搜索或一般住户安全验收 |

三个业务轨道的模型收到四类输入：`public_task.json`（含 `business_rules.task_skeleton`）、
`participant_api.json`、嵌入题面的 `factor_manifest`、按时投递的运行事件。
Factory 可决定消息消费/等待和公开站点派遣；Warehouse/House Isaac 可在既定 DAG 上
分配、改派及提交合法导航目标，运输终点固定。generic `wait/consume_message`
动作仅在无运动协议 harness 应用。输出是 `participant_action`，说明文字不计分。

benchmark 管理场景/资产、初始团队、固定业务分解、对象角色、目标、阈值、预算、
调度和基础运动，以及采集与评分。对象/标签真值、未来故障和消息日程、候补池、
参考指派与 detector/dock/access-path 真值保留在隐藏侧。LLM 可依骨架再生成合法
场景实例，不能从固定 DAG 协调分数推导自主任务分解能力。

无人机是连续 **swept-AABB + 保守体素 + 有界局部绕行**的简易运动基线，不是异构
多机能力项或完整三维规划。三个场景均有体素路径声明；Factory 生成器从 USD 碰撞
Mesh 的世界 AABB 生成产品及 hash 元数据。声明和本地产品不等于几何/发布/飞行验收。
楼梯显式入口开放不等于爬楼通过；准入保持
`execution_validated=false`，缺校准接触/支撑证据时候选探针停止推进。
F-VERT-01 候选场景与独立编译器只提供路线意图契约：静态测量已识别并私有绑定
`flight_02`，但坐标和 PhysX 支撑仍未校准，
`release_gate.executable=false`、`release_gate.scoreable=false`，Isaac 意图 adapter
和独立物理验收 pending；没有实时事件/业务 `factor_manifest`，不启用通信或成员干预。

- [x] F-VERT PhysX 证据边界：新增 Factory 私有适配器，接收 post-step 根状态、
  严格静态碰撞身份的 raycast patch、逐足 contact buffer 和校准 support 记录；
  raycast-only、静态测量、缺 Isaac runtime、接触身份变化和不完整样本均保持
  `pending/unknown`。有效记录只进入 evaluator 私有侧，状态为 `observed` 且独立
  traversal acceptance 仍为 `pending`。专项及既有垂直测试共 `78 passed`；本轮在
  `env_isaaclab` 中启动 Go2 平地健康探针，射线可命中平地，但因稳定净空参考未知
  以 `settled_reference_unknown` fail-closed，没有生成可晋升的健康或楼梯结论。

真实领域适配器未接入时，`domain_evidence.adapter_status` 保持
`pending_live_scene_adapter`；收集到 claims 仍需隐藏评测器验收，异常批次回滚并
标记 `collector_error`。调度完成、代理对象交付或自报成功都不能代替业务结果。
`member_join/capability_change` 当前仅协议级，Isaac 动态实体创建/激活和在途能力
重配置尚未实现。`member_exit` 只适用于已支持的纯地面任务及预加载替补；混合
无人机模板和 F-INS 不接收此故障。通信队列支持不代表已验证真实无线或领域消息链。

以下部署工作仍待完成，本次文档范围内不实施：

- [ ] 接入 Warehouse/House 真实领域采样，检验身份/数量、消息先于动作、dock 回执和
  最终动线，保留缺证与伪证反例。反思：业务分数不能由 scheduler 自动填真。
- [ ] 用 PhysX 核对楼梯可行走表面、接近/入口/终点与接触校准，为 F-VERT-01 候选题包接入
  意图执行与独立验收，再作有界实测。反思：探针由 benchmark 控制，不能计成模型爬楼。
- [ ] 核验 Factory 三维体素产品来源/hash、不可变 provider 发布和当前代码下的飞行。
  反思：历史飞行成功与二维地图都不能代替当前三维准入。
- [ ] 接入 Isaac 动态加入/激活、真实能力变化和在途恢复，再做配对干预。
  反思：每次变更后重算未完成节点资格；零候选是题目可行性问题，不能归咎模型。
- [ ] 验收原生道具执行、物理抓取/搬运及联合承载必要性。
  反思：kinematic 所有权和“两人”约束不证明受力协作或单机不可行。
- [ ] 冻结世界、资产、因子、阈值和动作范围，重新编译题包并分开报告业务、协议、
  恢复和运动基线结果。反思：声明等级、开发轨迹或历史包不代替独立留出评测。

## P0：生成、执行与证据（历史条件绑定验收记录）

- [x] P0-A1：修复候选 provider 的 manifest 引用、配置序列化和用户指令传递；离线模拟模型响应回归。
- [x] P0-A2：保留扫描目标高度，按实际位置判断到达和连续停留；发布准确的原语支持契约。
- [x] P0-B1：机器人、对象及显式共享资源的互斥调度，避免 ready 节点覆盖命令。
- [x] P0-B2：正式 wait、节点 timeout、失败/取消资源释放和按控制模式保持。
- [x] P0-C1：真实执行源码、任务、世界与所用配置指纹；记录器、导入器、汇总一致。
- [x] P0-C2：旧证据不可误绑定当前配置；篡改与缺失指纹回归。
- [x] P0 验收：相关轻量测试通过，Factory 执行验证与限制形成记录。
- [x] P0-C3：新增观测、评测、分配源码与渲染/墙钟条件纳入启动前请求；控制前另存 USD/材质/纹理、控制器资产/已加载源码、地图、实际时间步及环境版本；结束检查文件漂移。记录器/导入器/汇总拒绝过期、篡改或缺失绑定。Factory 真实完整覆盖、批量记录和 E6 导入预览已贯通，未合并 dossier。此前 C1/C2 的覆盖不足以支持这些新增条件。
- [x] 维护场景、evaluator fixture 和 Benchmark 回归加入 Git 白名单，运行输出与生产资产继续忽略。

P0 验收记录：`57 passed`（生成器、扫描控制、调度、批执行证据、能力画像、Factory 契约、任务需求和三 demo 契约的纯测试）；
`compileall` 与 `git diff --check` 通过。完整回归当前有一个既有的 M20/UR5 未缩放物理挂载
限制失败，属于另一会话的机械臂范围，未修改其实现。

## P1：Factory 场景与任务契约（进行中）

- [x] 建立区域、任务道具、观测点和业务语义边界；底层 USD 未声明的 prim 语义保持待核验。
- [x] 任务道具使用 EAI 注册表状态并对缺失资产失败关闭。
- [x] 建立 F-INS-01、F-TRAFFIC-01、F-COV-01 的角色化任务输入/业务结果初始契约。
- [x] 建立 Factory 核心几何 prim 绑定审计、米制/Z-up 校验和 USD 文件 hash；业务语义仍不由几何自动推断。
- [x] 修复 SimpleCar URDF 生成层中无几何 link 的空 visual reference；导入器生成和 `--check` 均会结构化校验，当前资产重新打开无 unresolved reference。
- [x] 建立 `ObservationStore` 的 observation_id、生产者、传感器配置、坐标、有效期和 consumed_by 契约；帧记录明确标记为 `sensor_pose_only`，不把 hover 或相机位姿当作目标定位。
- [ ] 区域、通道、工位和对象继续绑定真实 USD prim、坐标、单位及来源哈希。
- [x] F-INS 的空中及地面视角接入真实 Isaac RGB 帧，形成 frame hash、shape/dtype、observation_id 和 consumed_by 证据；目标识别与定位精度仍未完成。
- [x] 定义 detector 输出契约：检测器来源/版本、目标 ID、估计位置、置信度、时间戳和源 observation 关联；ground truth 保持 evaluator-only。
- [x] 将 F-INS 的候选目标绑定到 Factory USD 的真实箱体 prim，并记录世界包围盒坐标；当前仅是 `visual_reference_only`，不声明业务识别或物理交互。
- [x] 将两个允许视角、目标真值、scenario hash 和 USD hash 绑定到 evaluator-only fixture；运行时不加载真值。
- [ ] 验收真实场景对应关系、无效引用/尺度/楼层负例。

## P2：机器人能力与选择（进行中）

- [x] 配置级能力画像/雷达数据 schema；未知项保留未知，不填造评分。
- [x] 14 型默认 dossier 可导出为无测量分数的能力画像。
- [x] 14 型控制语义覆盖矩阵：差速、平面根速度、Ackermann、足式/轮足、双足和空中适配器均显式列出。
- [x] 14 型配置覆盖矩阵及控制接口适配，区分差速、Ackermann、足式/轮足和空中模式；可通过 `export_capability_profiles.py` 导出审核报告。
- [x] 机械臂证据接入画像 v2：每型包含 UR5、UR5 bare、Z1 的配置级静止挂载状态，并单列不可选择的 Kinova Gen3 候选证据；42 条 Env DIY 矩阵保持 `9 conditional / 9 rejected / 24 unknown`，14 条 Kinova 候选记录为 `4 / 2 / 8`。所有动态操作准入仍阻断，固定底座组件 E7 不迁移到移动配置，Carter 记录保留轮质量分布偏乐观警告，112 个雷达轴仍全部 `unknown`。
- [ ] 配置级导航/传感器微测试、证据范围、原始指标和重复性记录。
- [x] 建立 `configuration_microtest` 协议；v2 补齐来源重算边界：启动请求、报告、实际文件、源码、配置和质量审计需一致，转换器/协议 hash 也绑定；旧 v1 仅字段检查，不再作为 E6 准入。
- [x] SimpleCar+C01 的历史两条手工记录保留为 v1 数据；当前完整 F-COV 2582 步重新验证后，自动导出三条到达与三条完整取帧窗口记录。六条检查来自同一个三成员 episode，不能算六次独立重复或硬件能力分数。
- [x] 生成配置级雷达数据、HTML 审核表，并将已声明的 suitability 约束接入分配前筛选；仍待补充 Factory 实测轴后才能形成测量结论。
- [x] allocation 前置约束已支持逐项匹配 domain、导航/传感器能力、已测雷达轴和最低 evidence tier；仍需补充各配置的 Factory 导航/传感器实测后才能形成更强 suitability 结论。
- [x] 新增 `configuration_smoke.py`：为全部 14 型生成 Factory 单配置 smoke 输入，显式固定 controller、场景、传感器/工具、spawn 条件和协议版本；差速/平面、Ackermann、足式/轮足、双足和空中使用不同原语。
- [x] smoke 输入对缺失场景/控制器资产、附件冲突和未配对空中执行器生成 `blocked`/`not_executable` 原因；`physics_gate=pending` 不被解释为通过。
- [x] 在同一 Factory 布局和冻结协议下完成首批 Carter、Scout、SimpleCar、M20、Go2、MuSHR/Coco Ackermann 配置运行，并为每型保留独立报告和失败原因；通过与失败均不外推为型号能力排名。
- [x] 足式 waypoint 跟踪使用 body-frame `[vx, vy, wz]` 速度投影；此前前向-only 输入也符合策略接口，改动不代表修复接口维度错误。M20 原长路线以 3708 步完成；Go2 原长路线失败仍保留。
- [x] `navigate` 完成直接使用 evaluator 的 `robot_visited_zone`，精确目标点保留到末端路线，避免 CLI tolerance 或栅格中心偏移提前完成。访问是 ever-visited 语义，到达事件记录实际位置。
- [x] 近场协议升为 `legged_navigation_v2`/`biped_navigation_v2`；出生点、控制器、附件与实际输入一致，保存 scenario hash，启动前检查几何、连通性和初始未成功。近场通过不能替代原长路线失败。
- [x] `predicate_v2/` 独立 Isaac 报告：B2 234、Lite3 281、M20 273、Go2 239、G1 175、CF2X 492 步通过；CF2X 含真实 RGB 帧和 0.5 s 悬停。各次报告绑定当时源码，未直接晋升到当前 dossier。
- [x] Pepper 读取移动 `base_link`，按固定根坐标系驱动 XDisp/YDisp 虚拟底座。相同场景修复前 2067 步 deadlock，修复后 1763 步通过，真实位置、完成事件与 provenance 一致。报告保存世界坐标轨迹和终态采样。
- [ ] Go2 原长路线的倒地/碰撞/跟踪健康诊断；终态高度约 0.06 m，不能仅以近场通过归因为地图瓶颈。
- [x] Go2 旧失败的场景位置复核：四条长路线失败的末态中心均位于 main_stair 包络内，六条对照报告的 Factory USD 摘要与当前一致；记录在 `configuration_smoke_20260916/go2_stair_review_20260916.json`。旧报告无姿态轨迹/接触数据，尚不能确定倒地起因，也未证明绕行后恢复。
- [x] 轨迹对规划膨胀边界的跟踪校验：保留 Carter 0.5 m 物理规划圆盘，在楼梯/坡面排除区外增加 0.15 m 跟踪储备；连续同方向栅格压缩为转角航点，中间航点仅在半个地图栅格内切换。每个 0.02 s 物理步以实测中心段计算扫掠圆盘到保守 AABB 的精确二维净距，负值立即失败；不缩小机器人、质量、圆盘或目标到达半径。两条检测驱动派车路线已在独立 Isaac 进程中通过，限制仍见下方验收记录。
- [x] MuSHR 动态单位层物化及 E6 复跑：实际 Kit 会话捕获的层为空，metadata 指向一个缺失 prim 和两个无引用的停用 prim；将真实捕获内容写为相对 USD，同时修改 sublayer 和 metadata 路径。非依赖内容哈希一致，未改单位/质量/惯量/几何。原协议 2953 步通过，依赖完整。
- [x] Iris/Pegasus 正式位置控制配对：各 727 步完成悬停与真实取帧。Iris 首轮因远程材质未获 E6，九项依赖迁入相对路径后复跑通过；已解除生成器的型号限制，直接转子控制器仍被阻断。
- [x] Pegasus antenna 网格优化后仍引用原 3583 面的材质索引，优化网格仅 1199 面。恢复源资产中该无碰撞视觉网格，世界变换和网格外全部内容哈希不变，整机材质子集校验通过；727 步复跑通过，完整日志中告警消失。
- [x] smoke schema 升为 v2，配置 ID 与能力画像共用同一算法；dossier 快照哈希单独保留，错误 ID、旧版记录与非单配置输入会拒绝，避免测量关联错配。
- [x] `record_configuration_microtests.py` 从冻结报告生成并重算 v2 记录；保留所有提供的失败/拒绝尝试，不把成功列表当作失败分母，重复报告不能增加重复次数。
- [x] 单次协议通过改为 `protocol_checks`，保留原始指标并维持雷达未知；不再把一次导航/取帧通过填成雷达满分。分配按配置 ID、布局、目标、观察对象和时窗匹配，避免同型号不同附件互用证据。
- [x] 实际数据负例：不提供测量、目标移动 0.1 m、一个成员移除 camera 时，固定/贪心/优化全部拒绝。改写指标/范围、来源文件漂移、旧记录伪填 E6 有针对性回归。
- [ ] 补其余配置的 v2 转换与同协议重复，再扩展分配候选；有限路线证据仍不证明整场景通行能力，连续雷达轴还需独立协议与失败分母。
- [x] 当前 SimpleCar+C01 同协议复现：独立准入与三方法共四次 episode，各六条记录，按相同配置/目标/条件分为六组、每组四次。`repeatability.json` 保存原始指标；同一布局、seed 和最终分配，不能当作四个独立世界或 24 次机器人可靠性试验。

## P3：三个完整 Factory 任务（进行中）

- [x] 建立第三方评分四层边界：`public_task_spec`、`participant_api`、`hidden_evaluator`、`baseline_solver`。F-INS 编译器将公开文件写到 `release/`，隐藏真值与发布 QA 写到 `evaluator/`；公开包递归拒绝真实标签、隐藏故障时刻、`preferred_agent` 和参考决策。
- [x] 将 Factory task requirements 分为 participant 接口约束与 benchmark 发布 QA。配置级 E6、像素重放、场景闭包和参考路线只保留在隐藏 QA，不再作为第三方系统参加评分的预置证据要求。
- [x] 将 `immediate` / `wait_for_message` 明确迁入 `baseline_solver.py`；兼容入口仍保留，但 manifest 只描述算法和参数，不发布参考分配或路线答案。
- [x] 在 F-INS Isaac 运行器接入可注入 Python participant adapter，默认 baseline 走同一 event/action 边界；schema v3 记录 turn、可见消息和动作，hidden evaluator 只对声明为 `baseline_solver` 的策略重放，第三方策略只做接口、消息时序、决策绑定和业务评分。
- [ ] 将 participant 控制范围从 `route_confirmation` 扩展到任务级 `allocate` / `reassign`；当前公开包明确只启用 `consume_message`、`wait`、`navigate_goal`，不能把已声明但禁用的动作词汇当成已执行能力。
- [ ] 将运行条件拆成公开任务条件与 evaluator-only intervention；隐藏故障发生时只通过 participant event 暴露当下事件，不提前泄漏具体时刻。
- [x] 四层契约第一阶段验收：公开目录递归敏感键扫描无泄漏，artifact hash 交叉绑定及 public/evaluator 目录隔离负例通过；当时 Benchmark/Factory 扩展纯回归 `223 passed`，未启动 Isaac。
- [x] participant adapter schema-v3 已冻结并完成一条新 Isaac 运行：`tmp/factory_benchmark/participant_adapter_v1_shared23_20260916/shared23`。运行完成且证据/业务验证通过，4 个 participant event、3 个动作，11.02 s 决策后导航 Carter 到 dock，67.84 s 到达，误差 0.498537 m，二维扫掠圆盘最小净距 0.204401 m；baseline 只从 participant 可见事件重放。该单次工程运行不是完整矩阵或新的配置 E6 记录，也不建立机械操作、额定 payload、实机抓取力、碰撞安全裕度、抗风或载荷动力学结论。
- [x] 初版 World/Task、Fleet、Assignment 解耦及可解释贪心分配接口。
- [x] F-INS-01、F-TRAFFIC-01、F-COV-01 的初版场景化需求定义，不绑定具体机器人 ID。
- [x] AssignmentPlan 可编译为 V2 `phase_graph` 的 `preferred_agent`/`agents`/`resources`，缺失或非法团队失败关闭。
- [x] Factory 运行报告接入调度等待/吞吐、共享资源占用、机器人轨迹最小间距指标；业务任务的观测与覆盖指标接口已建立。
- [x] 增加 `traffic_01.json` 维护场景：Carter 与 SimpleCar 使用不同控制模式并竞争 `midband` 资源，离线几何/连通性通过。
- [x] 生成器支持 `--task-id` 和 Factory 任务族准入；`F-TRAFFIC-01` 不再被全局 aerial/joint 长流程模板强制污染。
- [x] `F-COV-01` 契约改为 `navigate → wait` 观测窗口，避免给不支持 duration 的导航节点伪造持续覆盖。
- [ ] 固定脚本、贪心与优化分配在相同信息和计算预算下对照。
- [x] 三方法离线分配、DAG 顺序复用和同团队约束：正式 F-COV 六阶段使用三辆 SimpleCar 均可行；有限优化预算耗尽区分 unknown/不可行，代价不解释为路线性能。真实配对执行仍待完成。
- [x] 分配门禁消费来源可核验的配置级 v2 检查：未提供 measurement 时三方法均为 `infeasible`，匹配六条 SimpleCar+C01 检查后均可行；旧 v1 的通过状态不沿用。
- [x] 完成首轮单位成本三方法真实配对：固定/贪心均 2582 步通过；优化分配 6733 步完成所有节点但观测最大空档 55.86 s，超过 30 s，故 episode 验证失败。来源、条件及失败原报告已核验保留。一个工程布局不能给出统计排名，`optimality_proven` 只表示单位成本枚举最优。
- [x] 将场景地图路线代价接入分配，并在相同任务/窗口/预算下完成新配对：三方法均选择各自近场工位，各 2582 步通过，最大观测空档 1.26 s、共享资源冲突 0。此布局三方法分配相同，不宣称优化器普遍优越；旧单位成本失败原样保留。
- [x] v2 转换检查逐帧实际观察位、窗口内采样位置与空中悬停事件；错站点/中途离区/错误时间/错节点/错误高度负例通过。原 admission、固定及贪心各六条记录重新核验通过。记录位置是机器人底座代理，不能称为已校验相机外参或目标可见性。
- [x] 将观察区核验同时接入执行结果：`camera_validation.py` 被运行端和 v2 转换共同调用，错位帧使 episode 验证失败；恢复前的部分窗口保留单独检查。新源码准入以 2582 步完成，三工位各 46 帧/45 s，全部逐帧与采样位置检查通过。
- [x] 真实错位取帧负例：机器人距观察点 1.5 m（允许半径 0.5 m），静止取帧 3 s；151 步完成、获得四帧，但 `camera_window_validation=false`、attempt 为 failed、E6 导入拒绝。源码/资产证据正常，拒绝原因为观察位错误，不是启动或来源失败。
- [ ] F-INS-01：已贯通真实相机窗口、贴标检测/定位、检测驱动派车、六条件信息干预及配置级检测准入；任务内目标变化复核及独立 world 验证仍待完成。
- [x] F-INS 标签工程基线：真实表面贴标、遮挡/缺失负例、实际像素检测、固定多帧融合及下游目标/路线选择已形成可复核结果。相机参数来自渲染产品，真值仅供 evaluator；不替代自然物体识别，也不以 observation_id 数据关联代替决策作用。
- [x] 帧归档基础实现：可选 `--archive-camera-frames` 绑定启动请求，保存未修改数组/PNG 及同一 RGB 渲染产品的 CameraParams/ReferenceTime；像素与预览摘要可复核。纯测试覆盖 RGBA 原样保存、摘要改写、尺寸不匹配、米制坐标与实际矩阵约定。
- [x] F-INS 原始视角实测：`inspection_capture_20260916/initial_views/` 首轮在取帧前因 CF2X 落地停滞而失败；附加渲染图初始化后原生位姿确认已倒置落地。`restore_initial_state/` 在初始化结束后、正式任务前正常 env.reset，恢复声明出生位姿，5640 步完成并归档四帧。未改质量、控制策略参数或物理重力。
- [x] 原观察位负例：同次报告空中目标中心投影在图外，地面三帧中目标在相机后方，两视角均无中心或角点入镜。取帧窗口成功不能认定巡检业务成功；原报告、像素和 projection.json 保留。
- [x] 修正观察位：目标保留地面坐标，新增 aerial_scan 的独立世界高度 scan_height_m；使用 Iris 的原生朝向控制，实际图像与矩阵核验后更新维护场景和独立 fixture。新文件与已验证输入字节相同，原报告保留。投影在图内仍不等于无遮挡识别。
- [x] scan_height_m 从生成 schema、TaskSpec 编译、执行计划贯通到相机窗口核验与配置微测试作用域；拒绝虚构楼层、低于目标/非法高度及非 aerial_scan 使用。默认楼层高度行为保留。
- [x] aligned_views 负例保留：2221 步完成四节点，真实窗口通过但目标仍不在两相机图内；CF2X 首帧机体偏航约 1.65 rad，声明初始值 pi 未被位置策略保持。C01 真实相机 forward=[-1,0,0]、up=[0,1,0]，与安装前向/上向不符。
- [x] 修复 C01 Camera API 的重复轴变换：构造器输入采用 world 相机轴的单位四元数，由原生 API 完成一次 USD 转换；原实现传入已转换四元数而导致画面横置。保留安装位置、实体、质量、控制器和动力学。
- [x] scan_yaw_rad 接入 EAI 已有 Iris/Pegasus goal_yaw；要求位置和偏航连续同时满足 0.5 s，观测与证据绑定测量。CF2X 与直接转子模式明确拒绝此能力要求；不按外形授予朝向控制。
- [x] iris_heading_v2：修复相机后使用 Iris 位置控制器、scan_height_m=2.3、scan_yaw_rad=pi，2267 步完成四节点；位置误差 0.031671 m、偏航误差 0.000472 rad；目标中心及八角点在两相机图内。四条 scoped 配置记录通过来源重算，未合并 dossier 或生成雷达分数。
- [x] 库存标签基线代码：ArUco 标签、覆盖/缺失条件使用相对 USD 覆盖层；原始场景和物理属性保留。在线解码只消费像素、相机参数、公共字典和边长，不消费期望 ID 或目标真值。最初 0.18 m 黑框的纸张支撑有误，已单列失败及修复版本。
- [x] PnP/证据契约：实际像素解码、米制世界坐标换算、主点偏移、非法输入和无标签/擦除负例；未标定 confidence 为 null。独立 evaluator 重新解码归档图像，再从实际标签几何计算误差，篡改解码结果会拒绝。
- [x] 原库存标签四条件矩阵已完成并核验来源：两可见 ID 的空中帧正确解码，定位误差约 4.56/4.69 mm；覆盖和缺失条件没有检测。ID23 地面仅一帧解码且误差 0.331 m，ID17 地面没有检测。`inventory_detection_20260916/matrix.audit.json` 保留全部结果。纸张角点到实际表面间隔最大 0.273 m，场景支撑验收失败；不得认定业务或双视角定位完成。
- [x] 贴标支撑修复：黑框 0.09 m、纸张 0.12 m，按真实网格三角形拟合且整张纸保留边距，独立检查全部墨色/纸张顶点和米制尺寸；拒绝包围盒足够但实际支撑不足、离面、错误尺寸与父级缩放。只调整新标签，不调整箱体或机器人。
- [x] 正式 `run_inventory_matrix.py` 入口冻结源码/任务/条件，保存逐次 attempt 与像素评测；配对资产闭包仅允许标签层不同，保留失败，拒绝覆盖旧目录。
- [x] 支撑、朝向、导航进展和检测方法修复后的 v3 四条件全部真实复跑通过；`inventory_matrix_v3_20260916/matrix.audit.json` 核对来源与配对资产，仅标签层不同。后续检测驱动路线与信息干预未完成，不声明完整巡检业务成功。
- [x] 修复缺失视角误判：此前 evaluator 只比较已采集视角，地面阶段未执行时可能给出 all_views_localized=true。现在使用冻结任务的两路视角要求，缺失任一路即不通过；来源验证与条件成功分别报告。
- [x] 地面观察朝向契约：wait_yaw_rad 经 schema/编译/执行/证据作用域贯通；支持已有差速/平面角速度接口，连续停稳后开始完整取帧时长。朝向漂移、缺失对齐事件、过短窗口和不支持的控制器被拒绝，实际视角验证继续进行。
- [x] 修复慢速末端接近被恢复倒车打断：两个不同目标都在距目标约 0.455 m 循环，轨迹证明车辆先正常接近到 0.186 m，再被“没有 waypoint 完成”计时触发倒车。导航现按距离/朝向误差单调改善重置计时，重复振荡不算进展，保留原到达半径与有界重规划。控制/执行/配置证据/来源/库存场景回归 `103 passed`；同输入真实复跑 2504 步完成全部节点，重规划为零，原 12 次重规划失败保留。
- [x] 同一 oriented_ground 输入在进展修复后完成所有节点，空中一帧与地面三帧都解码 ID17；地面原 SUBPIX+IPPE 定位误差 0.190/0.221/0.250 m，仍按 0.1 m 阈值失败。失败结果保留在 `inventory_progress_20260916`。
- [x] 在该开发帧集比较四种官方角点方法后固定 APRILTAG 角点、ArUco 字典与坐标中位数融合；融合不读取真值或期望 ID。补迭代 PnP 候选解决完全正视 IPPE 的 144 px 残差退化，保留备选解。开发重算中空中/地面融合误差约 0.013/0.064 m；随后按新协议重新冻结并完成四条件实跑，不能称为独立世界留出验证。
- [x] F-INS 决策包 A：`inventory_dispatch.py` 与维护 `inspection_dispatch_01.json` 声明 ID17→assembly_site、ID23→dock_site。冻结两条几何准入路线，真实 ID 留在场景构造/evaluator，策略只获得带时间戳的检测消息；站点到达不声称物料交付。
- [x] F-INS 决策包 B：检测消息选择目标并按真实起点重新规划，同步到达谓词和逐步位置评测，原 scenario/hash 不改。旧楼梯前导航失败保留；绕行后六项均完成所选路线、零重规划。
- [x] F-INS 决策包 C：同任务两种标签 × shared/withheld/delayed 六项完成。无消息走默认站；ID17 默认猜对仍成功，ID23 默认到错站则业务失败。派车者不自动读取 prerequisite 原始帧，禁发和延迟真正改变决策输入，未用收信门禁锁死 DAG。
- [x] F-INS 决策包 D：`inventory_dispatch_matrix_v4_20260916/matrix.audit.json` 重放全部像素/固定融合/消息时刻/公开策略，核验六次实际到达与隐藏 ID、请求/报告/日志摘要及运行闭包；来源验收和业务结果分别保存。一个世界和固定单次决策策略不证明异构必要性。
- [x] F-INS 决策包 E：公开 `immediate` 与 `wait_for_message` 一次提交策略；后者仅在已声明延迟消息仍待送达时保持车辆，收到首条可判定消息或等待预算耗尽后提交。独立 evaluator 逐物理步重放，拒绝本可更早提交却人为等待。同源码、同世界、同 60 s 延迟配对中立即默认到错站，等待 51.04 s 后消费单条空中消息并到达正确站；5 s 延迟的立即策略也只用空中消息成功。该样例否定“延迟必然使任务失败”和“双视角在此任务中必要”，不外推通用传感器充分性。
- [x] F-INS 配置准入：`factory_inventory_detection_v2` 从独立像素重放提取逐视角帧数、识别帧数、固定融合定位误差和实际 marker ID；Iris 空中观察、SimpleCar 地面接近/观察及 Carter 实际目标导航均绑定精确配置、世界和节点 scope。固定/贪心/优化在真实 v4 记录下均可行；删除任一检测记录后均不可行，篡改 scope、marker 或定位指标会因来源重算不一致而拒绝。Carter 导航记录不构成检测或策略能力，单世界记录不填充雷达轴或可靠率。
- [x] F-TRAFFIC-01：共享通道预约、等待/冲突/吞吐指标；维护配对完成一次 5000 步 CPU Isaac smoke（2/2 节点完成、无资源冲突、吞吐 2）。Carter 与 dock 的反向配对在当前碰撞几何入口停滞，保留为配对失败证据，不外推为型号能力结论。
- [x] F-COV-01：三个 SimpleCar 并行驻守 storage/assembly/dock，真实 C01 RGB 帧按 1 秒周期采集；并行 CPU Isaac smoke 验证 3/3 工位、无资源冲突、最大观测年龄约 1.24 s（阈值 30 s）。
- [x] F-COV-01 dropout 变体：导航与观测恢复组、完整窗口、真实帧来源和退出事件绑定；原任务成功时跳过恢复。退出车保留原地、备用成员使用独立观察位。显式 5 Hz 渲染的 CPU Isaac smoke 完成 3820 步，退出后 11.38 s 获得备用首帧，最大帧间/结束间隔 25.36 s；默认渲染 180 s 墙钟超时作为失败记录。该结果只覆盖一个布局及渲染条件。
- [x] 修正团队必要性证书；`reallocation_certificate` 在保持任务需求和成功条件不变时允许重新分配，分别报告单机、同构团队、异构必要性及预算耗尽的 `unknown`。
- [x] 分开报告既定编排的成员数与任务必要性：无依赖阶段允许重新排序，联合搬运的成员绑定不再单独证明异构必要；当前 F-COV 证书为最少 3 个机器人、单机不可行、同构团队可行、异构不是必要条件。
- [ ] 在真实 Isaac 配对执行中复核上述反事实证书，保留同一布局、时间窗和成功条件。
- [ ] 固定布局调通后每任务进行独立 world 工程验证，记录失败。
- [ ] 明确 F-COV 初次建站时限与持续值守区间，再生成对应新协议变体；当前 freshness 从每站首帧开始计帧间/末尾空档，不包含首帧前的建站延迟，不能称为从 episode 起点连续覆盖。

## P4：协作条件与任务扩展

- [ ] 同世界的信息延迟/丢包、角色责任、成员退出配对条件。
- [ ] 扩展感知、物流、通道、覆盖四任务族的场景化变体与多样性统计。
- [ ] 接入已就绪的外部操作后端，明确未支持任务及证据边界。

## P5：统计与因果干预

- [ ] 独立世界/任务族划分、冻结运行配置、配对/聚类区间与失败分类。
- [ ] 将本轮逐次运行请求/资产快照升级为整个研究的条件矩阵和资产版本冻结；运行时 MDL/驱动二进制、完整模拟器状态和原生传感器速率不在当前文件证据契约中。
- [ ] 复用现有 replay 组件，核验任务、控制器、策略、消息/传感器队列状态。
- [ ] 有限观测/分配/预约干预与同噪声前缀验证；不支持的恢复明确拒绝。

## P6：场景复用

- [x] 建立公共四层 challenge 编译器，并完成 Warehouse/Modern House 的契约编译和纯验证。
- [ ] Warehouse 实体/通道/库存任务适配与验证；已有场景化生成、公开能力筛选和短窗口组合稳定性筛查，完整任务执行与真实物理搬运仍未完成。
- [ ] Modern House 房间/门口/家具/观察任务的实时领域与物理验证；已有 11 节点业务骨架、
  原生道具声明和领域谓词，真实身份/动线适配器及物理搬运仍待接入。
- [x] 为两个场景接入公开导航/空中扫描/等待 phase 的 allocation 和 member-exit reassign participant runtime adapter、任务生成器和 task-specific hidden evaluator；公开候选按 domain、导航接口和配置附件筛选。
- [x] 运输任务开放 participant 控制（`scene_transport_allocation_v2`）：`transport_object`/`joint_transport` 进入 controlled_objectives，participant 选定 agent/团队后运行时按实时机器人位姿与对象实际位置重建取放路线（`rebuild_transport_routes`，含接力腿与落地物体）；对象所有权只经运行时 pickup/release 事件变化，携带中改派失败关闭（`carried object` 拒绝 + `participant_reassign_rejected` 审计）；成员退出触发 `forced_release` 在载具位置落地并记录 owner_before/owner_after；运输节点拒绝 `navigate_goal`（交付目标是任务语义）。`execution_contract` 放开 ground-only 任务的 member_exit+transport 组合（空中机队故障注入仍拒绝）。纯回归覆盖路线重建、所有权守卫、退出释放计划与契约负例；运动学搬运边界不变，仍不构成抓取/payload 证据。
- [x] 非 Factory 运行器逐控制步检查 articulation/rigid-object 根状态；NaN/Inf 以 `physics_state_invalid` 失败关闭并输出合法 JSON。Warehouse 隔离表明近距离多挂载组合会失稳，单 Carter+Z1 和三基础机器人短窗口稳定；生成任务收敛为单 M20+Z1、Carter 基础配置及更大初始间距。该筛查不构成 E7、碰撞安全或载荷动力学证据。
- [x] 公共机制消除 Factory 常量依赖，完成跨场景留出协议及文档第一版。

## 验证记录

以下是历史阶段实际记录的命令和结果，本次文档核对未重跑；“当前/本轮”均指该记录
形成时的版本。后续加入的体素、领域与生命周期门禁不能用这些旧运行直接豁免。

早期阶段验证命令：

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs env_isaaclab/bin/python -m pytest -q source/EAI/test/test_benchmark_observations.py source/EAI/test/test_factory_evaluation.py source/EAI/test/test_factory_task_admission.py`（`20 passed`）；
能力画像、分配、观测、Factory 准入和覆盖回归合计 `20 passed`；14 型导出报告校验为 `14/14`，当前所有雷达轴状态均为 `unknown`。
`python -m compileall -q demo/benchmark demo/factory_benchmark tools/scenario_generation source/EAI/EAI/scenario_spec`（通过）；
`git diff --check`（通过）。本轮 F-INS-01 真实 Isaac smoke：`status=executed`，`steps_taken=5607`，
3/3 节点完成，CF2X 产生真实 RGB frame，`ground_view` 消费 observation_id；报告中的
`observation_chain_validated=true`，但 `target_identification_validated=false`、
`target_localization_validated=false`。该 smoke 不构成 E7、目标识别准确率、定位误差或机械操作证据。
维护交通场景已有 5000 步 CPU Isaac 证据；另有 3 个既有 Carter+Z1 未缩放物理 screen 失败，
属于另一会话的机械臂范围，未修改其实现。

增量验收（2026-09-16）：F-INS 场景改为 `aerial_view → ground_approach → ground_view(wait) → route_confirmation`，
新增 `observation_ref` 以区分导航目标和观测对象；evaluator-only fixture 校验真实目标 prim、scenario/USD hash
和两个视角，聚焦回归 `25 passed`。真实 Isaac CPU smoke 报告为 `/tmp/factory_inspection_01_two_view.json`：
`status=executed`、`steps_taken=5706`、4/4 节点完成、空中产生 1 帧、地面窗口产生 3 帧，
`inspection_observation_validation.observation_chain_validated=true`；该证据仍不包含目标识别或定位精度结论。

增量验收（2026-09-16，恢复与分配）：13 个聚焦测试文件合计 `80 passed in 1.19s`，
覆盖 allocation、recovery、scheduling、control、observations、capability、Factory task admission/
evaluation/traffic/scene/fixture/prim bindings。正式 dropout 的默认渲染失败与显式 5 Hz 成功
保存在 `tmp/factory_benchmark/recovery_20260916/`。恢复首帧延迟 11.38 s；共享资源冲突 0，
但资源预约和采样间距不构成碰撞安全裕度。三方法离线分配报告为
`tmp/factory_benchmark/allocation_coverage_20260916.json`，没有 Isaac 算法性能排名结论。

增量验收（2026-09-16，运行证据绑定）：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  source/EAI/test/test_benchmark_provenance.py source/EAI/test/test_scenario_batch_execution.py \
  source/EAI/test/test_scenario_benchmark_protocol.py source/EAI/test/test_runtime_evidence.py \
  source/EAI/test/test_benchmark_demos.py -k 'not maintained_navigation_examples_pass_geometry'
```

结果 `45 passed, 3 deselected in 0.72s`。先运行未筛选版本得到 `38 passed, 3 failed`；
失败均为三 demo 的旧 Carter+Z1 样例被当前未缩放挂载物理门禁拒绝，未绕过门禁或修改机械臂。
`compileall`、`git diff --check` 通过；另检查 31 个相关 Python 文件的尾随空白，均通过。
Git 白名单已核验可见维护场景/fixture/测试，`tmp/` 报告与生产 USD 仍被忽略。

实际 Isaac 两步检查调用 `python -m demo.factory_benchmark.main --scenario
demo/factory_benchmark/scenarios/coverage_01.json --headless --steps 2 --render-period-s 0.2
--wall-timeout-s 60 --report tmp/factory_benchmark/provenance_20260916/short_run.json`，
`status=steps_exhausted`，439 个文件的控制前/结束核验通过；该检查不算任务成功。

随后通过 `batch_execute.run_executor()` 实际执行兼容入口 `execute_episode.py`：同一正式
`coverage_01.json`、9000 步上限、CPU、`transport_mode=none`、渲染周期 0.2 s、
控制墙钟 480 s、子进程超时 660 s，并传入启动前保存的 `--execution-request`。
报告 `tmp/factory_benchmark/provenance_20260916/coverage_full.json`：`status=executed`，
2614 步、6/6 节点完成、3/3 工位观测，最大帧间/结束间隔 1.24 s；控制循环墙钟
99.19 s、总子进程 165.78 s。436 个运行文件的指纹完整且未变化，200 Hz 物理、
50 Hz 控制、5 Hz 渲染。`coverage_attempt.json` 为 completed，
`validated_import_preview.json` 通过 E6 importer，未合并或晋升任何 robot dossier。
`prelaunch_context.json` 保存实际选中含相机配置的 dossier 快照，不使用默认无附件画像替代。

该运行的 request SHA-256 为
`9340bcd8a538ccb2c549894275f25a586aeb2bada887bdbd611d70567cead415`，
runtime SHA-256 为 `d9b84fd07513b6254b1568b96decad51d567b181e9e6791c23aa2c8abb76444f`，
规范 JSON 报告摘要为 `fe55ad28aca58ed4352e9d98865a4a1a0da8192a751f15ffeb84781c23db03a3`。
此结果验证一个布局、一组配置及其证据链；目标可见性/识别、原生传感器发布频率、
机器人可靠性、物理操作及跨布局统计仍未验证，完整 Goal 保持进行中。

配置微测试回归：`test_configuration_microtest.py`、能力画像、14 型覆盖、分配、Factory
准入与评测共 `37 passed in 0.15s`。真实记录和带 SimpleCar 测量的能力导出保存在
`tmp/factory_benchmark/capability_microtests/`；这是被忽略的运行数据，不是维护 fixture。

增量验收（2026-09-16，14 型配置 smoke 输入）：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  source/EAI/test/test_configuration_smoke.py source/EAI/test/test_configuration_microtest.py \
  source/EAI/test/test_capability_profile.py source/EAI/test/test_benchmark_capability_coverage.py \
  source/EAI/test/test_benchmark_allocation.py source/EAI/test/test_factory_task_admission.py \
  source/EAI/test/test_factory_evaluation.py source/EAI/test/test_factory_scene_contract.py \
  source/EAI/test/test_factory_traffic_scenario.py
```

结果 `48 passed in 1.00s`；生成器输出 14 条配置记录，其中 12 条为
`ready_for_isaac_smoke`、Iris/Pegasus 因当前空中执行器只配对 CF2X 而为 `blocked`。
14 条嵌入场景均通过通用 V2 schema/语义校验。该结果只是输入契约和静态资源门禁，
尚未产生这 14 型的 Isaac 执行证据。

增量验收（2026-09-16，首批配置级 Isaac smoke）：

报告目录：`tmp/factory_benchmark/configuration_smoke_20260916/`。

- Carter：`executed`，2473 步，目标成功，`navigation_execution_validated`。
- Scout：调整到其 footprint 可通行的短目标后 `executed`，1074 步，目标成功，`navigation_execution_validated`。
- SimpleCar：移除导航 smoke 的默认相机挂载后 `executed`，2003 步，目标成功，`navigation_execution_validated`；相机/C01 仍由独立传感器微测试负责。
- Coco：`executed`，3228 步，目标成功，`navigation_execution_validated`。
- M20、Go2：资产完整但在 12 次重规划后 deadlock，分别停在 0.769 m、0.578 m 的航点误差处；这是当前路线/控制配置失败证据。
- MuSHR：谓词成功，但运行指纹缺少 USD `metricsAssembler` 依赖，保持 `execution_failed`，不能晋升为 E6。

以上只验证配置级导航和资产闭环；报告仍明确排除额定 payload、抓取力、碰撞安全裕度、
抗风能力、载荷动力学和 E7 physical manipulation。默认相机条件曾使 SimpleCar 在 180 秒
墙钟内仅完成 1741 步，已作为渲染条件失败记录保留，未与机器人控制失败混合。

增量验收（2026-09-16，完成条件、移动底座与空中配对）：

- `predicate_v2/`：Lite3 281、B2 234、M20 273、Go2 239、G1 175、CF2X 492 步通过；Pepper 旧反馈 2067 步 deadlock。
- `moving_body_v3/pepper.report.json`：同一输入修复后 1763 步通过，反馈为 `body:base_link`，轨迹终态步骤与 `steps_taken` 一致。
- `aerial_pairing_v1/iris.report.json`：727 步悬停/取帧完成，但远程材质缺失，E6 拒绝；`pegasus.report.json` 727 步通过，保留 antenna 渲染告警。
- `aerial_local_materials_v2/iris.report.json`：本地相对材质依赖完整，727 步通过。
- `mushr_local_units_v1/mushr_v2.report.json`：动态层物化后 2953 步通过，当前代码/实际文件复核及 E6 导入预览通过，未合并 dossier。
- `suite_paired_v3.json`：14 条输入均为 ready，全部 physics gate 仍为 pending；不把静态生成结果当作 14 型物理验证。
- `pegasus_visual_v2/pegasus.report.json`：修复视觉拓扑后 727 步通过，完整日志无原 geomSubset 告警，当前代码/文件核验和 E6 导入预览通过。
- `suite_schema_v2.json`：配置 ID 统一后的最新 14 型输入；旧 suite 文件保留历史含义，不直接按新 schema 导入。

上述目录均位于 `tmp/factory_benchmark/configuration_smoke_20260916/`。
不同源码或协议版本的报告保留各自启动前指纹，不合并成同条件重复实验。
Iris 下载/迁移/源文件备份在 `tmp/factory_benchmark/aerial_materials/`；MuSHR 会话捕获、
物化/迁移/源文件备份在 `tmp/factory_benchmark/mushr_metrics_inspection/`。
Pegasus 视觉网格修复与源/目标哈希、原始优化资产备份在 `tmp/factory_benchmark/pegasus_visual_repair/`。
MuSHR 原始单位标记未调整：这里只证明依赖迁移不改变运行内容，不声明新单位或硬件能力认证。

本轮实际轻量检查均使用
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q`：

| 测试路径（均在 `source/EAI/test/`） | 结果 |
| --- | --- |
| `test_benchmark_control.py test_scenario_execution.py test_factory_evaluation.py test_benchmark_recovery.py` | 60 passed |
| `test_configuration_smoke.py test_benchmark_capability_coverage.py test_configuration_microtest.py` | 17 passed |
| `test_usd_closure.py test_benchmark_provenance.py` | 28 passed |

当前修改路径的 `python -m compileall -q` 和 `git diff --check` 通过。
下一工作包：配置证据自动转换/核验、同协议重复、任务相关准入及三分配方法真实配对；
Go2 长路线健康继续单独保留，P2/P3 与完整 Goal 仍在进行。

增量验收（2026-09-16，观察位置与路线分配）：

- 首轮单位成本配对及源码/文件复核：`tmp/factory_benchmark/scoped_microtests_20260916/paired_methods/audit.position_verified.json`。固定、贪心各 2582 步/51.62 s 仿真时间通过；优化 6733 步/134.64 s 完成全部节点，却因 dock 55.86 s 空档失败。失败没有被 `status=executed` 覆盖。
- `map_route_length` 使用 Factory 当前膨胀地图和候选出生点；单位为米，不混入未标定的运动速度。当前短路线合计约 5.1213 m；旧优化分配的几何估计约 47.0584 m。独立根导航后可接同位置/同团队 wait；未知中间起点或不支持原语显式拒绝。
- `run_allocation_comparison.py` 在 demo 内提供可复现入口：冻结分配输入、任务、启动请求和方法顺序；保存每方法失败，检查源码/输入漂移及实际运行文件、时间步一致性；不复用旧输出目录。
- 新源码运行输出：`tmp/factory_benchmark/route_cost_comparison_20260916/`。准入完成并导出六条当前记录；三方法真实配对均通过。固定/贪心/优化的控制循环墙钟分别为 97.445/102.917/97.466 s，总进程约 162.26/168.35/162.29 s；仿真均为 51.62 s、2582 步。固定/贪心/优化分别评估 6/9/84 个候选，共用 10000 次上限和相同路线代价表。不同算法评估次数及墙钟不可被称为完全相同计算量。
- 纯回归：microtest、route costs、allocation、capability coverage、Factory evaluation、recovery、control 共 `88 passed`；随后 runner、route costs、microtest、provenance 共 `44 passed`；scenario execution、Factory task admission、configuration smoke、observations、batch execution 共 `46 passed`。这些集合有重叠，不作为独立实验次数。
- `compileall`、`git diff --check` 通过。新模块和测试未被 Git 忽略；实验报告和 HTML 仍为 `tmp/` 运行数据。
- 同协议复现的 `repeated.records.json` 含 24 条记录、`repeatability.json` 明确四次 episode/一个世界；各方法实际分配相同，不能解释为算法排名或型号可靠性。
- 真实负例及 `expected_rejection.json` 位于 `wrong_observation_site/`：151 步、四个真实帧、运行验证与 E6 导入均按预期拒绝。所有配对结束后再次核验源码与启动前冻结版本完全一致。

正式配对入口的等价复现命令（输出路径需使用新目录，测量需匹配当前源码）：

```bash
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.factory_benchmark.run_allocation_comparison \
  --scenario demo/factory_benchmark/scenarios/coverage_01.json \
  --measurements tmp/factory_benchmark/route_cost_comparison_20260916/coverage.records.json \
  --cost-model map_route_length --steps 9000 --device cpu \
  --render-period-s 0.2 --wall-timeout-s 480 --process-timeout-s 630 \
  --output-dir tmp/factory_benchmark/new_route_cost_comparison
```

本轮实际通过同一模块的 `run_comparison()` 执行，启动前另用 `run_executor()` 完成准入，
再由 `derive_records()` 从该次原始 attempt 产生输入测量；没有复用旧源码的准入记录。

历史报告不自动升级：旧 F-INS 地面帧缺少当前逐节点来源字段，旧 dropout 报告缺少当前世界坐标
轨迹，均不能通过新观察区检查或当作当前版本 E6。原报告与其当时的验证结论保留，后续需重新
采样。雷达仍以未知表示未测项，位置代理/有效图像窗口不代表目标识别、光学标定或物理能力。

增量验收（2026-09-16，Factory 巡检观察位、坐标轴与朝向控制）：

全部运行输出位于 `tmp/factory_benchmark/inspection_capture_20260916/`。

| 运行 | 结果与意义 |
| --- | --- |
| `initial_views` | 2105 步 deadlock、无帧；附加渲染图初始化推进物理，CF2X 倒置落地。 |
| `restore_initial_state` | 正式任务前正常 reset，5640 步完成、四帧归档；原观察位两视角均无目标投影入图。 |
| `aligned_views` | 新观察位置及 2.3 m 世界高度，2221 步完成；CF2X 不维持要求朝向、C01 双重坐标转换导致侧向横置，两视角仍失败。 |
| `iris_heading_v2` | 原生 Iris goal_yaw + 修正 C01 相机轴，2267 步/45.34 s 仿真时间完成；控制墙钟 88.323 s，总进程 153.64 s；一帧空中及三帧地面。 |

最后一次运行的相机光轴/上向分别约为空中 `[-1,0,0]/[0,0.014,1]`、
地面 `[0,-1,0]/[0,0,1]`，与声明观察方向相符。独立 evaluator 投影中，
空中目标中心像素约 `(315.43,242.26)`、地面约 `(1207.65,273.16)`，四帧各八角点在图内；
真实图像已人工查看，不把人工查看或投影当作 detector 输出或无遮挡认证。

实际调用 `run_aligned_capture.py` 和 `run_heading_capture.py --output-dir .../iris_heading_v2`
（位于该输出父目录），内部复用 `batch_execute.run_executor()` 与正式 EAI demo。
条件固定为 `steps=9000, device=cpu, transport_mode=none, render_period_s=0.2,
wall_timeout_s=480, process_timeout_s=630, archive_camera_frames=true`；启动请求在运行前冻结。
随后实际运行：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.factory_benchmark.inspect_camera_frames \
  --scenario tmp/factory_benchmark/inspection_capture_20260916/iris_heading_v2/scenario.json \
  --report tmp/factory_benchmark/inspection_capture_20260916/iris_heading_v2/report.json \
  --fixture tmp/factory_benchmark/inspection_capture_20260916/iris_heading_v2/fixture.json \
  --output tmp/factory_benchmark/inspection_capture_20260916/iris_heading_v2/projection.json
```

`derive_records()`、`measurement_for_profile()` 与实际文件来源重核验均通过；
`configuration.records.json` 的四条检查来自同一次 episode，配置为 Iris+camera/navigation_io、
SimpleCar+camera/navigation_io、Carter，不作为四次独立重复。`cf2x_heading_rejection.json`
保留同一朝向要求被 CF2X 原生接口门禁拒绝的输入和原因；这是离线拒绝，不是一次飞行失败。
维护 scenario SHA-256 为 `01c0e1a2ba19906582b20ca96ee0271bea03f0db1a4ec3fbbad7277cc3e2cd4f`，
与真实运行输入完全一致；fixture 同步更新机器人 frame_id 与 scenario hash，目标 prim 和 USD 未变。

实际轻量检查使用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs`
及 `env_isaaclab/bin/python -m pytest -q`，测试路径均在 `source/EAI/test/`：

- 高度第一批：control、configuration_microtest、task_admission、camera_capture、generator、execution 和编译专项，共 `73 passed`。
- 朝向和 C01 修复后：control、configuration_microtest、execution、task_admission、编译专项、simple_car_registration，共 `83 passed`。
- 来源/像素/fixture/生成/分配/smoke/评测共 `69 passed`。
- 维护场景更新后：factory_evaluator_fixture、factory_task_admission、benchmark_demos，排除已有机械臂几何样例后 `15 passed, 3 deselected`。
- 编译专项移入已列入 Git 白名单的 `test_benchmark_control.py` 后，该文件 `21 passed`；原先临时编辑的被忽略 family 测试已恢复，未将其无关内容纳入修改。

以上测试集合有重叠，不累加为独立试验次数。`compileall`、`git diff --check` 通过。
图像归档目录及实验脚本保持运行数据；未更改生产 USD、相机安装位置、机械臂、机器人质量、
惯量、重力或控制器增益。C01 轴修复影响其相机视线，旧数据保留原源码指纹，不升级为当前证据。
本包完成取帧位置/朝向配置与验证；真实遮挡、检测/定位、有效信息对决策的作用和跨世界评测
继续按 P1/P2/P3 推进，完整 Goal 仍进行中。

增量验收（2026-09-16，表面支撑、双视角定位与库存矩阵 v3）：

`tmp/factory_benchmark/inventory_matrix_v3_20260916/matrix.audit.json` 已核验四次
来源和配对资产。每次 2504 步、全部节点完成、零重规划，空中一帧、地面三帧，
完整地面窗口 2 s。地面最大位置误差 0.117772 m（阈值 0.12 m），最大朝向误差
0.017935 rad（阈值 0.05 rad）。只允许标签覆盖层不同，未改变箱体和机器人物理。

| 条件 | 空中固定融合误差 | 地面固定融合误差 | 验收 |
| --- | --- | --- | --- |
| visible17 | 0.013558 m | 0.064165 m | 两视角通过 |
| covered17 | 无检测 | 无检测 | 两视角负例通过 |
| absent17 | 无检测 | 无检测 | 两视角负例通过 |
| visible23 | 0.013479 m | 0.063892 m | 两视角通过 |

visible17 地面中间帧误差 0.141316 m，超出 0.1 m 阈值，原帧及误差完整保留。
验收使用预先固定的三帧坐标中位数，不表示每帧通过。真实标签黑框边长 0.09 m，
纸张 0.12 m，支撑为原箱体 mesh 的第 225 个三角面，保留 1 mm 边距/离面距离。
旧纸张越界、缺失地面视角、SUBPIX 定位超差、末端进展倒车失败均保留原结果。

实际命令：`python -u -m demo.factory_benchmark.run_inventory_matrix --scenario
tmp/factory_benchmark/inventory_supported_20260916/oriented_ground.json --output-dir
tmp/factory_benchmark/inventory_matrix_v3_20260916`，使用 `env_isaaclab` 和仓库 Python 路径。
预算为 CPU/9000 步、5 Hz 渲染、控制墙钟 480 s、进程 630 s、transport none。
控制循环约 97.6–98.3 s，总进程约 162.7–164.6 s。

维护 `inspection_01.json` 与该次实际输入字节相同，SHA-256：
`dd4d863ff15fd5e944bf7099af735cc43e5386b161665deef8a59205482e65e6`。
fixture 同步该摘要。四条 `configuration.records.json` 已由 `derive_records()` 和
`measurement_for_profile()` 核验，只是导航/相机窗口协议，不是检测协议或雷达评分。
转换结束时四条件源码快照仍等于当时当前源码；后续源码更新不升级这些历史报告。

实际回归：marker_detection、factory_inventory_scene、benchmark_control、configuration_microtest、
scenario_execution、benchmark_provenance、scenario_batch_execution 共 `122 passed in 1.52s`；
维护 JSON 更新后的 factory_evaluator_fixture、factory_task_admission 为 `8 passed in 0.30s`。
`compileall`、`git diff --check` 通过。此矩阵尚未使检测影响后续目的地，
`business_task_validated=false`；完整 P3/Goal 继续进行。

增量工作记录（2026-09-16，派车决策与地面路线修复）：

- `inventory_dispatch_routes_v1_20260916`：两个条件都在空中消息发布时提前结束，无正式报告。
  旧空中帧为 2.42 s，而节点已在 2.40 s 完成；对保留的旧四条件报告重新调用消息生产器，
  复现 `dispatch message cannot precede its source frames`。该诊断重算不计新物理运行。
- 修正空中采样事件的物理步；异常在 simulator cleanup 前记录，批运行保存完整日志。
  旧两次无报告结果不补成 E6 或导航失败统计。
- `inventory_dispatch_routes_v2_20260916`：shared17 5091 步、shared23 5524 步，均在
  12 次重规划后 deadlock。独立重放像素、融合、消息、决策和目标谓词通过；目标选择
  正确，但两次派车均未完成。shared17 控制循环约 199.51 s，不能因检测通过标为业务成功。
- `shared17/stair_route_diagnosis.json` 保存实际 USD 三个启用碰撞的楼梯 mesh 包络。
  轨迹停滞在 y≈-2.8217，原路线穿过模板已有 main_stair；这是未验证表面被纳入平面
  路线的缺陷。未采集接触力，不把包络重叠写成经过力测量确认的碰撞分类。
- 统一 F-INS/F-TRAFFIC/F-COV 的地面区域排除，初始/恢复规划、连通门禁与分配路线代价
  共用；拒绝地面出生点/目标进入未验证楼梯/坡面。保留生产 USD/地图和机器人全部物理。
  派车 preflight 独立检查排除框包住当前场景 prim。离线绕行通过，完整六条件实跑进行中。
- 新维护派车 scenario SHA-256：`e571494db6abb7cda3e96f69c40046330a7e97447787e686e762d2e5c320b8ba`。
  v1/v2 与绕行版保持同一任务输入；源码/路径掩码版本分别冻结，旧报告不升级。
- 实际轻量回归先后为 `129 passed`、`131 passed`；加入区域排除后专项 `79 passed`，
  再加入楼梯绕行负例后 `153 passed in 3.36s`。集合有重叠，不能累加为独立试验次数。
- `inventory_dispatch_matrix_v3_20260916` 的首条件发生启动期原生库冲突：进程内场景核验
  提前导入独立 pxr，Kit 输出 C++ wrapper 注册冲突并进入崩溃符号解析。已终止该 executor
  和其 addr2line 子进程，保留完整日志及首 attempt；随后中止 matrix runner，第二条件
  的启动中断和其余未运行条件记录在 `interruption.json`。这些不计为导航/通信策略失败。
- 几何核验拆开生命周期：纯路径 preflight 不导入 pxr，矩阵父进程离线核验实际绑定；
  executor 在 `open_simulator_session` 成功后、任务控制前重复核验。独立 Python 子进程
  回归确认路径 preflight 不加载任何 pxr 模块；修复后专项 `50 passed in 2.94s`。
- `inventory_dispatch_matrix_v4_20260916` 的前三条件已完成后，外层长批次退出码为 143
  （SIGTERM，来源尚未确定）；shared23 当时没有 runtime snapshot 或正式报告。
  该启动记录保留于 `shared23/interrupted_start/`。通过原冻结请求和原协议摘要分段续跑，
  `resume.id23.json` 记录外部续跑脚本和请求验证，原三条完成记录不改写。

增量验收（2026-09-16，完成检测驱动派车的六条件配对）：

最终结果与独立核验为 `tmp/factory_benchmark/inventory_dispatch_matrix_v4_20260916/`
下的 `results.json`、`matrix.audit.json`。六次均完成四节点，零重规划，来源和运行文件
核验通过。此处“所选路线完成”和“正确工位到达”严格分开；两项业务失败未丢弃。

| 条件 | 派车者可用消息 | 实际到达站点 | 步数 | 到达时刻 / s | 业务结果 |
| --- | --- | --- | --- | --- | --- |
| shared17 | 两条 ID17 检测 | assembly_site | 3954 | 79.06 | 成功 |
| withheld17 | 无 | assembly_site | 3954 | 79.06 | 默认猜对，成功 |
| delayed17 | 无 | assembly_site | 3954 | 79.06 | 默认猜对，成功 |
| shared23 | 两条 ID23 检测 | dock_site | 2988 | 59.74 | 成功 |
| withheld23 | 无 | assembly_site | 3954 | 79.06 | 到错工位，失败 |
| delayed23 | 无 | assembly_site | 3954 | 79.06 | 到错工位，失败 |

派车决策均在 12.98 s。共享消息在 2.40/12.96 s 发布；固定延迟条件的可用时刻为
62.40/72.96 s，因此决策时未收到消息。派车者原始帧消费记录均为空。策略一次提交路线，
迟到消息不触发改派；这些结果是该公开策略的表现，不证明延迟使任务本身不可完成。
后续应比较允许等消息/改派的策略，并允许单视角、单机或同构团队重新分配。

两视角固定融合全部通过：空中定位误差 0.013446–0.013532 m，地面 0.063594–0.064245 m。
ID17 地面中间帧仍有 0.131877–0.141330 m 超差，逐帧原始数据保留；固定融合通过不表示
每帧合格。六项决策前采样的机器人位置/朝向前缀完全相同，但原始像素并非完全相同。
这不能当作完整模拟器状态、传感器噪声或控制器隐藏状态的确定性重放证明。

采样的 Carter 中心均未进入排除区域；规划用 0.5 m 圆盘与保守包络的最小间距仍为
装配路线 -0.086956 m、装卸路线 -0.033109 m。到达验收不包含连续碰撞安全，相关跟踪
约束继续列于 P2；不能缩小机器人/机械臂、降低质量或把规划圆盘缩小来消除这项记录。
`route_comparison.png` / `.pdf` 展示旧失败与新实跑轨迹，源报告和地图摘要在配套 JSON 中。

跟踪边界增量验收（2026-09-16）：第一版把中间航点切换从 0.55 m 收紧到半个 0.05 m
地图栅格，但仍逐格跟踪；`inventory_dispatch_tracking_v1_shared17_20260916/` 因 9000 步
耗尽保留为失败，未越界，Carter 最小扫掠净距 0.196147 m。第二版只压缩连续同方向栅格，
保留所有转角及精确业务终点，规划排除区外另留 0.15 m 跟踪储备。相同源码摘要
`25c5d64b9688c26c432eb99cb648fcd52b517f548111ea167bbe295cca2af473` 下的独立运行：

| 条件 | 目标 | 步数 | 到达时刻 / s | Carter 最小扫掠净距 / m | 重规划 | 结果 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| shared17 | assembly_site | 4213 | 84.24 | 0.200238 | 0 | 成功 |
| shared23 | dock_site | 3393 | 67.84 | 0.204401 | 0 | 成功 |

报告分别位于 `inventory_dispatch_tracking_v2_shared17_20260916/` 与
`inventory_dispatch_tracking_v2_shared23_20260916/`。两次来源、运行文件、检测重放与业务到达
均通过矩阵入口审核；运行时负净距会失败关闭。该证据是离散物理步之间中心线段的二维扫掠
圆盘对保守排除 AABB 的校验，不是接触力测量、连续时间/完整三维碰撞证明，也不建立 E7
physical manipulation、额定 payload、机械臂抓取力、抗风能力或载荷动力学结论。

信息策略增量验收（2026-09-16）：`inventory_dispatch_policy_v1.audit.json` 独立核验三次新
episode。冻结源码摘要为 `e9ec4dca94fa950e9372546f860bfe3bf106b3a1f7973ca3bcd7e617e7471c3a`；
60 s 配对的 scenario、非干预请求项和运行闭包一致，仅
`inventory_dispatch_policy=[immediate, wait_for_message]` 不同。

| 条件 | 策略 | 决策时刻 / s | 等待 / s | 决策可用消息 | 目标 | 到达 / s | 业务 |
| --- | --- | ---: | ---: | --- | --- | ---: | --- |
| delayed23 | immediate | 11.02 | 0 | 无 | assembly_site | 84.24 | 失败 |
| waiting23 | wait_for_message | 62.06 | 51.04 | aerial ID23 | dock_site | 119.70 | 成功 |
| short_delayed23 | immediate，5 s 延迟 | 11.02 | 0 | aerial ID23 | dock_site | 67.84 | 成功 |

三次均完成所选路线、零重规划、轨迹边界和来源审核通过。配对是独立新进程，不是同检查点
恢复或同噪声分支；短延迟同时改变延迟量和可用信息，只作诊断。结果说明旧 delayed23
失败属于立即默认提交策略，不是 60 s 延迟下任务不可完成。单条空中消息在这个固定世界已
足够选站，因此后续应把第二视角作为冗余/精度/失败恢复变量，而不是预设必要角色。

CPU/9000 步预算、5 Hz 渲染、control wall 480 s、process timeout 630 s、transport none。
控制循环墙钟为 shared23 的 119.178 s，其余约 151.597–153.370 s；完整子进程分别为
187.75 s 和 217.54–219.48 s。原场景和机器人资产未变化，配对运行闭包仅允许标签层不同。
未修改机械臂，未把派车导航或标签识别升级为 E7、厂家能力或配置雷达分数。

协议 SHA-256：`8a336e9f4c4dffe0db194c3ae46c3598f1bafc453d20758dea34d7705c279090`。
冻结源码集合摘要：`b26c90f44aaddf6560e735fc644d722eaaceb21207100c5bd4762e990e632ba2`。
审核完成时源码仍与六次运行前冻结版本完全一致。三条早期完成记录保留，启动中断及分段
续跑另存，不把中断当成一个有策略结论的独立世界或隐去失败分母。

实际执行命令（均使用 `env_isaaclab/bin/python` 及仓库 Python 路径）：

```bash
python -u -m demo.factory_benchmark.run_inventory_matrix --dispatch \
  --output-dir tmp/factory_benchmark/inventory_dispatch_matrix_v4_20260916
python -u tmp/factory_benchmark/resume_dispatch_matrix.py \
  tmp/factory_benchmark/inventory_dispatch_matrix_v4_20260916 shared23 withheld23 delayed23
python tmp/factory_benchmark/audit_inventory_dispatch_matrix.py \
  tmp/factory_benchmark/inventory_dispatch_matrix_v4_20260916
python tmp/factory_benchmark/plot_dispatch_routes.py
```

续跑和绘图脚本为本次运行数据，摘要已留存；正式维护入口仍为 demo 的 `run_inventory_matrix`。
最终统一审核 `validated=true`、六项全部复核；`compileall`、`git diff --check` 通过，另对
15 个本轮文件运行 `git diff --no-index --check /dev/null <file>`，补查未跟踪源码的空白问题。
静态回归命令仍为 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q ...`；153 项集合覆盖 dispatch、
marker、inventory scene、control、configuration microtest、scenario execution、provenance、batch、
Factory admission/traffic/scene/route costs 和 generator；最后 50 项集合覆盖 dispatch、provenance、
batch、Factory admission 和 route costs，并增加独立进程的 USD 导入顺序检查。

当时拟定的下一工作包顺序：扩展其他配置候选，再比较允许途中改派的策略，随后
扩展独立 world 与任务变化。Factory 的这些门禁和
协议稳定后再迁移 Warehouse/Modern House；P2/P3/P4/P5/P6 与完整 Goal 仍在进行。

配置检测准入增量验收（2026-09-16）：`factory_inventory_detection_v2` 已进入 F-INS 的
`aerial_view` 与 `ground_view` 硬门禁，`route_confirmation` 改为匹配实际动态目标的
`factory_navigation_visit_v2`，不再以未使用的 LiDAR 或未知 `information_service` 雷达轴代替
任务证据。Iris、SimpleCar、Carter 的配置 ID 分别为
`iris@2f3666b172fd700d`、`simple_car@da76cf9f991b505d`、
`carter@97efe7d050ebb157`。三个空中型号的 dossier 均声明当前位置目标扫描适配；带绝对偏航的
F-INS 仍由运行契约限制到成对的 Iris/Pegasus 位置控制器，且配置声明不能替代任务范围 E6。

`inventory_detection_admission_v3_shared17_20260916/` 的 episode 实际完成，但执行期间另一会话
修改 `source/EAI_assets/EAI_assets/robots/kinova_mount.py`，来源门禁以
`source_changed_during_execution` 和 `runtime_file_changed` 拒绝，未生成准入记录。文件稳定后
重新冻结的 `inventory_detection_admission_v4_shared17_20260916/` 为 4213 步，84.24 s 到达
assembly，Carter 最小二维扫掠圆盘净距 0.200238 m，来源、检测重放、动态派车、业务到达与
路线边界均通过。四条 v2 记录由 attempt 重算产生：空中 1/1 帧、定位误差 0.004278 m；地面
3/3 帧、融合误差 0.055958 m；SimpleCar 近场导航和 Carter assembly 导航保留原始到达指标。

真实记录下 fixed/greedy/optimized 均为 `feasible`，单位分配成本 4、不同配置数 3；优化器仅在
该有限候选和单位成本模型下证明最优。删除空中检测记录后完整依赖链不可行；删除地面检测记录
后 `ground_view` 与后续路线不可行。把仍在阈值内的 marker、定位值或 scope 改成其他值，都会
因不能从源 attempt 重算而拒绝。`capability_profiles.json/html` 仍覆盖 14 型，只有上述三配置
带 1/2/1 条 protocol check，所有雷达轴均为 `unknown`。这不是识别可靠率、硬件标定、通用
信息能力或异构必要性结论，也不建立 E7 physical manipulation、额定 payload、机械臂抓取力、
接触安全裕度、抗风能力或载荷动力学结论。

本增量纯回归为 `153 passed`；另有针对配置准入、三空中型号声明和雷达未知状态的最小集合
`57 passed`，`git diff --check` 通过。v4 正向、缺记录负例和来源篡改负例均保存在同一输出目录。

场景复用增量（2026-09-16）：公开 fleet 增加 domain、mobility、配置附件接口与
`rated_payload_kg=null`；公开 greedy 和动作应用层不再允许 CF2X 承担地面任务，也不允许
无配置机械臂接口的机器人承担 `transport_object`。该阶段的 `controlled_objectives` 只包含
导航、空中扫描和等待，phase-ready 使用公开约束计算完整候选集，不发布隐藏
`preferred_agent`；当时运输仍由参考执行器管理（当前已开放运输团队分配，见 P6）。契约、participant、控制和 demo 聚焦回归
`71 passed`。Warehouse 隔离运行中，近距离单 M20+Z1/Carter 组合在第 111 步被新门禁记录为
`physics_state_invalid`；把 Carter 与 M20 初始间距扩大后 140 步根状态检查通过，但任务尚未
完成，因此只作为组合筛查，不作为完整 Warehouse 或物理搬运验收。

场景复用第二增量（2026-09-16）：修复 generic participant 初始化遗漏
`controlled_objectives` 的问题；Warehouse 与 Modern House 运行时均在首个空中扫描
`phase_ready` 上记录由公开候选筛选得到的 CF2X `allocate`，当时运输节点尚未开放 participant
控制（当前范围见 P6 及上方清单）。生成门禁新增共享执行器完整建路检查，拦截了 Modern House 原先“二维连通通过但
`object_recheck` 无路线”的任务。两场景拆为独立业务 DAG：Warehouse 为货架/集货/dock
物流链，Modern House 为入口/家具/房间复核链。新版两场景分别完成 140 步 CPU 短运行，
所有 articulation/rigid-object 根状态有限，状态为 `steps_exhausted`，任务未完成；这些结果
只支持短窗口组合筛查和公开协议接线，不建立 E7、额定 payload、抓取力、碰撞安全裕度、
抗风能力、载荷动力学或长期稳定性结论。
