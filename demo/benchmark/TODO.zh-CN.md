# Benchmark 任务生成与审核阶段清单

> 交付状态总览与文档地图见 [`DELIVERY.zh-CN.md`](DELIVERY.zh-CN.md)。
> 本清单是阶段 1–18 的权威勾选记录，按时间顺序追加，不回填。

本清单区分“题目契约已完成”和“完整 benchmark 已部署”。任何阶段只有在其出口证据齐全时才能勾选；静态声明、模型自报成功和节点调度完成不能替代运行证据。

## 当前推进：阶段18，可靠技能接线与原生业务后端

本阶段沿用阶段17的失败证据，不把新增 adapter、服务 ACK 或离线测试当成真实业务完成。
三条源码写集分别交给现有代理，主会话负责接口审查、串行 Isaac 验证和发布审核。

- [x] Warehouse：冻结 body-frame `base_velocity` 接口，记录真实 16D 控制器输出、机器人与物体各自位移，完成单机固定点升降、输送、释放与 dock 收据复验。阶段18 v4：物体位移 1.976m、机器人 2.008m，承载期间物体底面最低 0.0687m；释放后 dock ready。
- [ ] Warehouse：完成双机固定点承载、退出安全持有和重新获取见证；单机地面输送不代替悬空承载或联合负载验证。阶段18 v9 的 Carter `caster_swivel_right` cooking 阻断已在 v10 解除：根因是 `caster_swivel_left/right` 与 `caster_frame_base` 三个**空 Xform** 被施加了 `PhysicsMeshCollisionAPI`+`convexHull`（无网格可烹饪），本地修复为移除这三个 prim 的碰撞 API（刚体/关节保留，轮子解析 Cylinder 碰撞不受影响，未添加代理形状），collider inventory 73→70，形状闭合门禁通过。**新阻断**：v10 推进至 `settled_before_attachment` 时 M20/Carter 与木箱的实测 collider AABB 相互接触（M20 距箱心 0.785 m、公开 reach 0.8 m；Carter 0.735 m、reach 0.6 m），属已记录的联合队形几何问题，旋转/纵列队形需实测验收；修复资产尚未发布 provider，需维护者按 provider 流程发布。
- [x] House：提供可直接绑定当前 Isaac session 的身份读回和路径后端，复核像素摘要、观测时刻、physics step、对象来源和 occupancy provenance。阶段18 v3 在482/602步均得到两对象身份及有效blocked通路读回；原始帧已保存并重算摘要。
- [ ] House：保留 before/after 实际快照与连续楼面证据；动态机器人堵路归参与者策略，检查器如实记录。**2026-09-19 进展**：干预 probe（v4–v17，STAGE18 归档）首次端到端完成十事件链——双 M20 attach + 抬升 0.0371 m + **搬运实测 0.67 m**（House 首个双机搬运证据）；剩余：椅在 x≈5.647 硬停（占据图准入 [7,13.5] 但实测受阻，疑地图/USD 不一致待复核）、ground_1 撤离净空 0.841<1.077；M20 策略仅前向（纯 yaw 失效）已实测归档。
- [x] City：阶段18 v8 完成一次 CPU PhysX 服务诊断；`guide_people`、`control_traffic` 的 hold/release/divert、`report_incident` 均有 episode-owned runtime readback。人员轨迹约301个样本，三种交通控制各约256个轨迹/安全样本，事故报告后连续 access 约26个样本；证据状态为 `candidate_witness=evidence_complete`。
- [ ] City：把诊断区域与动态实体固定进**运行时**正式 episode（作者侧声明绑定已完成：prepare 私有记录现携带 `episode_binding`——4 人 roster 逐人绑定集合候选、集合容量、危险区模板、安全进入控制点、交通反应窗口、回执端点，并由 `goal_city_intake` 对照打包语义图机检）；服务探针报告仍为 `formal_episode_binding=false`、`full_city_service=pending_formal_episode_geometry`，须由 Isaac 阶段的实测准入（native region reader / measured clear）补齐后才能提升为正式城市业务完成。**2026-09-19 首跑**：正式探针 prepare 机检通过（roster/集合区/容量全部建立，`author_frozen_episode_binding`），execute 在首次服务提交的 admission 谓词处 fail-closed（`service route, authority or capacity not admitted`，service_backend.py:169），待逐谓词校准（见 STAGE18 City 首跑记录）。
- [x] City：任务生成侧完成两份语义不同的声明级业务见证（`_city()` builder，空中主导与交通管制主导两条因果链，均过 `review_goal_business_witness` 城市分支与跨形态异质证书）、复用 communication_quality 的具体因子 treatment（immediate vs delayed 单因子对已隔离）、完整四层题包（`build_goal_task_package("city")` 可 build/verify/freeze）与 goal_suite 城市审计（`authoring/city_goal_bundle_review.json` + `city_factor_pair.json`，draft 仍禁止晋升、计分仍受 release gate 限制）。执行与因子效果验证仍为 external_pending。
- [x] 汇总当前源码下的 focused tests、实际场景报告与独立审查；本轮 `env_isaaclab` 聚焦回归为 `222 passed`，Stage18 evidence gate 读盘结果为 House/Warehouse/City 均 `diagnostic_only`，统一 `release_status=pending`、`scoreable=false`。

阶段18检查点：早期 City 协议回归为 131 项；本轮 v8 服务运行时增强、v9 Warehouse paired shape/probe 和 evidence gate 已重新执行，聚焦回归实际为 `222 passed`，并以归档报告中的 blocked/diagnostic 状态为准。
测试在 `env_isaaclab` 使用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；这些结果不改变
`scoreable=false`。后续修改必须重新验收。

Warehouse 实证：`tmp/warehouse_stage18_native_witness_20260919_v4/`，CPU PhysX，
276 个输送样本、240 个升降样本；固定点误差约 5.5μm。升降只调整可靠连接器目标，
不写物体或机器人位姿。该结果是单机技能微诊断，不代表联合承载、完整订单或因子成对运行。

阶段18独立复查：v4 升降回程有约1.153mm锚点残差，主线已改用不可变初始锚点与
绝对提升量，变姿态/双约束回程测试通过，待新PhysX报告。双机M20+Carter直线并排
队形约0.90×1.87m，当前声明1.3×1.65m缺少实际队形证明；不能把载荷匹配当作
携物路线可行性。保持联合承载pending，不扩大通道或缩小机器人以迎合测试。

Warehouse paired v9 实证：`tmp/warehouse_stage18_paired_native_20260919_v9/` 使用
`env_isaaclab`、CPU PhysX、真实 `QUERY_RIGID_BODY_WITH_COLLIDERS` 查询。原生
`rigid_body`/`finished` 回调已到达，但 Carter
`/World/envs/env_0/carter_1/Carter/caster_swivel_right` 的 deferred convex
表示解析失败（`RESULT_ERROR_INVALID_PARSING`）。v9 同时归档了 73 条实际 USD
collision inventory；该 prim 带 `PhysicsMeshCollisionAPI`、
`physics:approximation=convexHull`，但没有返回可 cooking 的 convex representation。
报告仍为 `status=blocked`，未开始双机承载、路线或退出恢复动作。该 blocker 属于
资产/PhysX shape 闭包，不是双机业务成功或失败；修复源碰撞资产后必须重新生成
probe 并串行运行。

House 实证：`tmp/house_native_backend_stage18_20260919_v3/`，CPU PhysX，
两张320×240 RGBA原始帧及PNG、两组唯一观测ID和同物理步native读回。身份是公开
全向理想场景传感器结果，不是RGB识别；前后无业务干预，不能证明恢复任务成功。

## 阶段17：真实服务与业务证据

本阶段暂不处理楼梯/RL；保留楼梯公开能力与待替换运动模型的边界。City、House、
Warehouse 分离写集，Isaac 场景由主代理串行运行。当前 goal Warehouse 只有木箱和
纸箱，不再将历史 TODO 的 service car 当成该任务的必需道具。

- [x] House 接入原生 PhysX 正反向连续净空扫描，纳入闲置机器人、家具和物品；明确支撑采样与连续楼面证明的区别。
- [x] 修复 headless 场景查询未启用、原生 Float3/回调对象适配、垂直接触容差和无需自体排除的实例命中处理。
- [x] 区分作者缺陷与策略结果：机器人临时阻挡通路不阻止题目作者审核；只有初始布局不合理、强制点位/约束冲突或不存在可行解时，才属于出题侧问题。
- [x] House 连续楼面覆盖与 CPU PhysX 通路检查器完成诊断；CUDA v5 的动态障碍查询因 DirectGPU CPU 位姿可能过期，不作为实时净空证据。
- [x] House 物体落位修复并完成 CPU PhysX 静置见证；书堆底部约 0.25m，质量约 2kg。最终动态通路仍按独立检查器判定，不要求参与者采用诊断撤离动作。
- [x] City 原生机器人张量位姿→incident request→episode-owned receiver receipt 微诊断完成；真实回执链成立，但完整人车/危险区业务仍 pending。
- [ ] Warehouse 原生固定点输送未通过：约 1766 个输送步后物体位移仅 0.00075m，报告明确 blocked。dock 接收器和承载/释放契约已收紧并有回归测试，仍需修复可靠技能后端后重新执行；固定点拖动不等同于悬空承载。
- [x] 完成当前源码与阶段证据的联合回归、阶段复盘及题包发布审核；统一 release manifest 仍为 `release_status=pending`，Warehouse 技能后端、完整 City/House 业务后端等缺失项继续阻止 scoreable。
- [x] 新增阶段证据汇总门禁：从 City/House/Warehouse 归档报告独立重算 `diagnostic_only`、`blocked` 和发布状态；真实报告汇总为 City/House diagnostic、Warehouse blocked，仍保持 `scoreable=false`。

阶段检查：House 已修复 headless 空查询误读问题，并拒绝用 DirectGPU 的过期 CPU
位姿证明动态净空。CPU 撤离 v4 仅验证检查器响应：该次书堆埋入楼板，不能证明正确
部署后的业务成功。物体生成高度已修复并完成短静置读回；诊断中的指定撤离动作不进入题面或作者门禁。
参考接口和额外审核文档不能替代实际宿主接线；下一检查点以场景执行产物为准。

阶段17真实见证记录（2026-09-19）：City 报告
`tmp/city_benchmark/native_probe_stage17_20260919_v1/native_probe_report.json`，
`diagnostic_completed=true`、`full_city_service=pending`；House 报告
`tmp/goal_physical_stage17_house_floor_20260919_v1/physical_report.json`，
`diagnostic_completed=true`；Warehouse 报告
`tmp/warehouse_stage17_native_witness_20260919_v1/warehouse_witness_report.json`，
`status=blocked`，不可提升正式任务执行或 scoreable。

当前所有权：本会话负责任务生成、题包审核、冻结机制和场景接入契约，并按用户最新授权统筹子代理、后端接入诊断和真实部署核验。场景资产、完整业务后端、实验回放和评分模型仍按维护团队写集交接；城市业务服务由侧边会话维护。阶段11已完成配置与参考后端验收，下一阶段转入实际配置应用与可信接线；历史外部交接条件不因主线诊断通过而改为已完成。

## 阶段 1：出题与信息边界

- [x] Factory、Warehouse NVD、Modern House NVD 各有独立业务语义、场景常识道具和任务 DAG。
- [x] Warehouse 覆盖订单核对、大件联合转移、集货、出库 dock 交接和复核。
- [x] Modern House 覆盖住户回家前的入口检查、家具调整、私人物品暂存、机器人撤离和生活动线复核。
- [x] Factory 覆盖基于多视角证据的工位派遣；楼梯另设 `F-VERT-01` 运动诊断入口。
- [x] 给被测模型的输入固定为 `public_task.json`、`participant_api.json` 和逐时刻可见事件；隐藏评测器、未来事件和真值不公开。
- [x] 三场景公开包包含受限 `task_skeleton`，供出题 LLM 在已审查角色、道具和因子范围内再生成。
- [x] LLM 候选生成器接入与 NVD 正式编译器相同的作者语义审核；错误场景道具、业务角色和因果骨架在候选池阶段即被拒绝。
- [x] Factory 任务审核按 `factory_dispatch_v1`、共享通道、覆盖恢复和垂直探针分别标记轨道；F-INS-01 不再套用长流程异构搬运 profile。
- [x] 题包 release gate 增加 `authoring_status` 与 `execution_status`，区分任务设计已审核和外部执行仍待完成；作者审核报告保留在 evaluator 侧，不泄漏到 public task。

阶段反思：固定 DAG 能评估感知证据消费、分配、改派和恢复，但不等价于评估自由任务分解。要测自由分解，需要新 track 和新的隐藏语义验收，不能通过改提示词宣称完成。

## 阶段 2：运动和协议基础设施

- [x] 地面路线使用按机器人 footprint 膨胀的占据图和连续路线准入。
- [x] 无人机使用连续 swept-AABB、保守三维 voxel 和有限局部绕行；它只是运动基线。
- [x] `scene_public_greedy_v2` 与发布 baseline manifest 身份一致；自动预算按米制路径、悬停、等待和恢复窗口计算。
- [x] M20 近目标速度下限、单机运输交互半径和同控制步 pickup 已用失败 episode 与单元测试校正。
- [x] `member_exit` 有运行时释放与恢复记录；`member_join`、`capability_change` 有协议事件和资格重算契约。
- [x] `member_join`、`capability_change` 已有 Isaac 回调提交边界；回调必须返回实际 articulation/entity 与 controller/version 证据，缺失时保持 unknown。
- [x] 预加载实体生命周期 callback 基础已实现：reserve 激活、controller declaration/version readback、失败后物理状态 unknown；不提供动态 spawn 或伪回滚。
- [x] ScenarioSpec 支持互斥的 initial/reserve 物理清单；`public_task.initial_fleet` 只公开初始成员，reserve 身份保持隐藏。
- [x] 作者私有见证按 lifecycle 初始名单计算；已加入的预加载 reserve 只进入私有资格投影，不泄露至初始公开题包；Warehouse/House 替补资格有隔离测试。
- [x] 新增作者私有 lifecycle plan 门禁：对三场景检查 reserve 停放 footprint、静态连通性、加入 rendezvous 路线、退出替补和能力变化时序；结果绑定当前地图 hash，明确保持 `physical_execution_validated=false`。
- [x] 通信组件支持即时、延迟、丢包、带宽受限和过期消息，并保留私有投递账本。
- [x] Warehouse/House 真实 participant 循环已接入通信队列：只发布实际送达观测，显式消费后才放行依赖节点；同一观测可由多个声明后继分别消费。
- [x] 成对 replay 检查两侧干预时刻、有效决策窗口、干预前后可见状态 hash 和事后可行性；相同结果或窗口外干预失败关闭。
- [x] 在 `MultiRobotDirectEnv` 实现预加载 reserve 停放/控制激活及 `navigation_interface` 命令门禁；`member_exit` 在公开事件前关闭退出者门禁并回读，严格 callback 提供该开关的 controller reconfigure/readback。其他物理能力变更仍需独立场景适配，不把动态 spawn 作为本版本要求。
- [x] 维护场景私有计划编译器已为 Factory coverage、Warehouse logistics、Modern House recovery 生成 map-hash-bound reserve parking、join rendezvous、member exit replacement 和 navigation capability-change 事件计划；编译失败会阻止沿用失效点位。
- [ ] 在最终 USD/地图 revision 上完成三场景 reserve 停放位的动态占位、加入路线和能力变化事件验收（当前仅有私有离线计划）。
- [ ] 为三个维护场景生成并执行带 reserve inventory 的物理生命周期 episode。
- [ ] 在 Isaac 中实际运行通信/生命周期成对 trial，证明协议差异改变了被测模型可见信息或团队候选。

阶段反思：通信条件现在真实约束 participant 可见观测和 DAG 放行，但尚无三场景成对 Isaac 参考运行。reserve 已能从 `initial_fleet` 隐藏，私有见证可计算 join 后资格，且共享环境已有控制热路径绑定。Warehouse 地图 hash `9d8b2b329575cd29146d1471e9805cdc08398f6a31d95705d8a1c6d36dc59f6c` 上的 Carter 候选 `(-8.425, -9.975)` 通过 footprint、静态连通性和到扫描区的 inflated-grid 路径；House 当前地图 hash `bb5d56db76c879a929564adf892bcbfb3c4fca73e94e21e7550ccc63be3a7f94` 上的候选 `(19.075, 5.025)` 重新通过同一门禁。Factory coverage 候选 `(-3.45, -8.6)` 也已通过当前地图门禁。上述点位均未通过任务路线动态占位、USD 碰撞和 Isaac 激活验收，不能作为已部署 reserve。发布分数必须分别报告协议正确性、因子效果和物理执行。

## 阶段 3：场景领域证据

- [x] Warehouse 隐藏评测要求订单身份、观察消费、物体所有权、实测交付和 dock 服务回执。
- [x] Modern House 隐藏评测要求物品身份、家具/物品交付、撤离后的 before/after 连续通路和净空。
- [x] Factory 派遣使用相机/标记证据和正确工位到达，不接受模型自报 marker 或成功。
- [x] 当前源码下 Warehouse v8 的 3 条 delivery、House v6 的 2 条 delivery 及两场景最终放置谓词通过。
- [x] 实时领域运行时桥已接通 `bind_runtime → process_frame → collect`；detector claim 在真实帧发布后原子进入同一 `ObservationStore`，帧处理错误会粘性失败。
- [x] Warehouse 参考 provider 已约束 RGB identity、dock state/receipt、确认帧、现场对象 pose、service source 与时序；只接受注入的真实 backend。
- [x] Modern House 参考 provider 已在指定 before/after scan 帧采样通路，并要求 after 位于 `hall_release` 完成事件之后且 occupancy hash 不漂移。
- [x] 接入 Warehouse 场景专属实时证据适配器入口：货架身份 detector、dock service 和 object pose backend 缺失时显式保持 pending。
- [ ] 提供 Warehouse 固定 revision 上的真实货架身份 detector、dock service 和 object pose backend，并完成 Isaac 参考 episode。
- [x] 接入 Modern House 场景专属实时证据适配器入口：住户物品 detector、before/after access-path backend 和 `hall_release` 因果门缺失时显式保持 pending。
- [ ] 提供 Modern House 固定 revision 上的真实物品 detector 与 access-path backend，并完成 Isaac 参考 episode。
- [ ] 在固定场景 revision 上形成每场景独立的未曝光 held-out 任务、布局和事件计划；冻结机制见阶段 8，已有公开模板不算盲测数据。

阶段反思：参考 provider 和运行时事件链已完成，仍需为具体场景提供真实 detector、dock controller、pose sampler 和 path backend。`pending_live_scene_adapter` 时，任务骨架和评测谓词是完整设计，但实例不能作为正式业务得分发布。缺失证据的结果保持 `unknown` 或业务失败，不能由调度完成补齐。

## 本轮推进记录（2026-09-18，`env_isaaclab`）

- [x] 在 Isaac Sim 5.1 headless、`env_isaaclab` 和 Factory CPU fallback 下运行 Go2 平地健康探针；真实 PhysX 射线连续命中 `/World/Indoor/GroundPlane/CollisionPlane`，输出 26 个 post-step 样本并绑定场景、控制器和 USD 层指纹。
- [x] 探针在稳定窗口因基座净空参考未形成而以 `settled_reference_unknown` 停止；输出没有 summary，`flat_diagnostic_completed=false`，因此只证明射线查询链路，不证明平地健康通过或楼梯能力。
- [x] 复核 Go2/B2 历史楼梯接触诊断：可审计记录分别以 `support_unknown`、`contact_calibration_rejected` 或 `step_budget` 结束，均为 `execution_validated=false`，没有可晋升的楼梯参考轨迹。
- [x] 重新编译统一发布清单：四个候选包均为 `release_status=pending`、`execution_validated=false`、`scoreable=false`；`task_design` 和 `package_integrity` 通过，其余发布阶段保持 pending。
- [x] 在 `env_isaaclab` 中运行纯 Python 专项回归：Factory 垂直证据/编译器 `36 passed`，生命周期、通信与发布清单 `126 passed`。ROS2 `launch_testing` 自动插件因该环境缺少 `lark` 无法自动收集，已用 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 并显式设置仓库 `PYTHONPATH` 完成上述回归。

阶段反思：本轮把平地支撑查询和楼梯尝试的“运行过”与“可验收”明确分开。当前真正缺口仍是 calibrated foot-contact、`flight_02` 的 PhysX 支撑/连通性、独立参考爬楼轨迹、三场景领域后端、生命周期/通信 Isaac 成对回放和不可变 provider revision；发布状态不得因导航 smoke 或射线命中而提升。

## 阶段 4：楼梯与物理协作

- [x] `F-VERT-01` 显式开放 `main_stair`，并阻止普通二维任务借用楼层高度或包围盒捷径。
- [x] 候选执行器在 `env.step` 后采样，区分 physics/control 时间，并在支撑或健康异常时停止。
- [x] 支撑契约拆分校准/运行阈值：校准至少三足稳定接触，运行一足可保留测得支撑，至少两足才累计垂直进展；零足间隙最多 80 ms 且不累计高度。
- [x] 使用 OpenUSD 对 Factory 根层及 67 个依赖层做 hash 绑定并测得两段楼梯；`flight_02` 是 ground→mezzanine 候选，踏步约 0.15m 高、0.40m 深、2.60m 宽。
- [x] `F-VERT-01` 编译器可选接收 hash-bound 静态测量，在隐藏评测配置中显式选择 `flight_02` 并绑定根层、依赖层集合和测量报告 hash；公开包仍不泄露 flight 编号。
- [x] 新增 Factory 私有 PhysX evidence adapter：接收 post-step 根状态、静态碰撞身份 raycast、逐足 contact buffer 和校准 support；raycast-only、静态几何或缺 Isaac runtime 均保持 `pending/unknown`。
- [ ] 用 PhysX raycast/contact 验证 `flight_02` 起终支撑面与连通性。
- [ ] 校准足端/接触真值，生成独立参考轨迹并完成 Go2 健康驻留验收。
- [ ] 对 Warehouse 联合运输、dock 交接和 House 家具/物品操作完成物理参考 episode；运动学所有权只保留为协调基线。

阶段反思：楼梯入口已经开放，爬楼能力仍未验证。无人机避障和楼梯探针属于基础运动诊断，异构性结论必须来自跨形态角色之间的业务依赖、信息传递和恢复。

## 阶段 5：资产和统一发布

- [x] 四层题包做内容 hash 绑定并隔离 public、participant、baseline、hidden evaluator。
- [x] `python -m demo.benchmark.release_manifest` 统一审计三个场景和 Factory 楼梯诊断。
- [x] 重建并从实际磁盘审计 Factory 业务、Factory 楼梯、Warehouse 和 Modern House 四个当前四层包及统一 manifest；全部保持 `release_status=pending`、`execution_validated=false`、`benchmark_ready=false` 和 `scoreable=false`。
- [x] Warehouse/Modern House NVD 产品写入 `eai.nvd-products.provenance.v1`：绑定 provider 的完整 USD/MDL/纹理闭包、bench USD、PNG/YAML/NPZ、生成参数和共享坐标系；两个 demo 的 `--check-assets` 已强制执行该门禁，并保持 `local_candidate`、不冒充 provider release。
- [x] 当前源码下完成 Warehouse v8 与 Modern House v6 完整运动学参考 episode；分别为 10/10、11/11 节点且 provenance 有效。
- [x] 生成统一候选 manifest v3，将 Factory 私有 `flight_02` 静态绑定纳入四层审计；四包仍全部 pending/不可执行/不可计分，楼梯 blocker 已收敛为 PhysX 支撑连通性、接触校准、参考实爬和意图执行 adapter。
- [x] 统一 release manifest 现在编译并记录三场景 map-hash-bound 私有 lifecycle plans；任一地图或碰撞产品变化都会阻止 stale reserve plan 进入审计结果。
- [x] 本轮验证完成：三场景 `--check-assets` 全部通过；维护场景与 Factory/场景专项回归共 `941 passed`，挂载/物理配置静态校验另有 `72 passed`。这些结果只证明离线契约、资产闭包和协议基础设施，不能提升 `release_status`。
- [ ] 将 Factory/NVD 场景、原生道具、占据图和 voxel 产品发布到不可变 provider revision，并在题包中绑定实际内容 hash。
- [ ] 在固定 revision 上跑完真实感知、领域适配、参考解、通信/生命周期变体和物理回归。
- [ ] 仅当统一清单没有 blocker 且 reference execution 通过时，把 `release_status` 从 `pending` 提升为 `ready`。

当前结论：出题契约、任务骨架、离线准入和两套 NVD 运动学参考 episode 可供评审与
participant 集成；它们没有使业务得分通过。完整 benchmark 发布仍被资产发布、实时
领域适配、三场景 reserve 停放/事件实例与 Isaac 生命周期参考运行、通信/生命周期成对 replay、楼梯实测和物理协作参考
episode 阻断。

## 阶段 6：目标驱动主轨道（2026-09-18）

- [x] 新增 `eai.skills.v1` 技能本体：导航、空中导航、楼梯、观察、取放、交接、联合搬运、relay 和同步均有参数、前置、效果、失败码、证据类型、取消语义和已验证机器人类型字段。
- [x] 新增 `goal_driven_team_planning_v1` 公共题面契约；`required_outcomes` 只能表达业务结果，不再把固定 DAG 改名后泄漏给 agent。
- [x] 新增 v2 participant API：团队选择、agent 自定义计划、改派、skill dispatch、取消和消息消费；计划校验能力匹配、联盟人数、无环依赖和当前证据引用。
- [x] v2 API 移除 `phase_ready.eligible_agent_ids`、生命周期 `affected_node_ids`、参考分配和消息消费者预绑定；旧 v1 API 保持兼容。
- [x] 为 Factory、Warehouse NVD、Modern House NVD 生成场景原生的最小目标题包；每包至少有两个能力匹配且不同的作者私有 witness。
- [x] 目标题包接入独立 release manifest，明确 `authoring_status=passed`、`execution_status=external_pending`、`scoreable=false`。
- [x] witness 去重改为 ID 无关的语义签名；只修改 `plan_id`、task ID 或任务列表顺序不能伪造第二种合法分解。
- [x] 三场景生成作者私有异构证书，记录跨形态依赖和移除 aerial/ground 后失效的业务结果；证书明确不声称物理必要性。
- [x] 通信和团队连续性所有 level 都生成具体隐藏 treatment，并由 `validate_goal_factor_pair()` 检查目标 treatment 改变、非目标 treatment 与其余冻结输入不变。
- [x] Factory、Warehouse、House 均采用 4 个公开候选、最多 3 个激活成员；关键楼梯或操作能力至少有一个备份候选，使退出和能力变化具备理论改派空间。
- [x] 公开预算增加模型 token 数值；公开区域的 access mode、机器人 mobility、接口和 skill 能力均进入计划准入审核。
- [x] 为三个场景生成 author-only `goal_authoring_brief.json`，限定 LLM 可用对象/区域/能力、业务结果主题、因子轴、禁止公开字段和准入条件，不提供参考解。
- [x] 增加 37 个纯 Python 契约和审核反例测试，覆盖信息泄漏、非法 skill、空间/接口越权、循环依赖、联盟不足、终检顺序、语义 witness、多形态反事实、未来事件、不可见证据、改派绑定、隐藏 treatment 和单因子冻结。

阶段 6 只完成题包结构和声明级审核，不能代表数值物理约束审核完成。可靠技能主轨道的补充工作见阶段 7。
以下工作是 goal-driven 题包进入正式计分集前的外部交接门禁：

- [ ] 运行时团队接入 plan ledger，并记录 agent 自定义 task 到业务 outcome 的执行证据链。
- [ ] 评价团队实现隐藏 outcome predicates，场景团队提供 detector、dock/通路 backend，运行时团队执行成员变化回放。
- [ ] 执行团队在最终 USD/map revision 上完成三场景物理参考 episode，以及通信、退出、加入和能力变化的因子效果验证。
- [ ] 发布负责人仅在上述运行证据和 held-out 审核完成后，将 goal-driven 包从 pending 推进到正式 scoreable 集。

阶段反思：主轨道现在能证明“题目给的是业务目标，计划由 agent 生成”，并能在作者侧证明至少两种语义不同的能力匹配计划、跨形态声明依赖和单因子输入隔离。它不能证明这些计划在 Isaac 中可执行，也不能把固定 witness 或反事实证书当成物理必要性的证据。楼梯开放、无人机避障和地面导航属于基础运行门禁；最终异构分数必须来自隐藏业务结果、通信/成员因子、团队决策和运行证据，不能由计划文本或调度完成事件单独替代。

## 阶段 7：可靠技能下的物理约束出题与审核

用户确认：导航、固定点抓取和观察在合法前提内可靠执行；主轨道评价载荷、空间、资源竞争、信息使用和团队决策。自主抓取/VLA 与随机抓取成功率不作为本轨道发布门禁。

- [x] A：版本化公开可靠技能契约；机器人有效载荷/尺寸/连接接口、物体质量/尺寸/固定点、路线净空与资源容量齐全，标明参数来源。
- [x] B：按成员分担载荷、固定点接口、携物/联合队形空间和计划时窗审核；超载、穿窄门、重复占用和所有权错误有反例。
- [x] C：重审三场景计划，去除预设“双人必需”的结论；合作人数由物体与团队数值约束决定，业务物体交接连续。
- [x] D：枚举可行搬运组合以及成员移除/能力变化后的局部可行性；固定 witness 失效不再被解释成整题必需异构。
- [x] E：升级 LLM authoring brief 与发布说明，交付新题包和审计报告，完成 env_isaaclab 定向回归。

并行分工：约束模块、三场景实例、独立能力反例由不同子代理承担；主代理负责接口、信息边界、集成与最终审核。现代家居场景 USD、运行时回放和评分模型由其他开发者维护。

A/B 验收反思：专项约束测试 65 项通过，主线第一次集成回归 182 项通过。数值准入已覆盖逐成员载荷、携物净空、共享资源、物体持有和机器人区域连续性；首个动作起点仍需运行时绑定，静止占地、转弯几何和真实遮挡未验证。

D 验收反思：独立能力测试 17 项通过；木箱和扶手椅在当前参数下最少两名搬运成员，双 M20 组合也可行。这只能支持局部成员选择诊断，不能据此声称整题必须异构或退出后一定能恢复。

独立审核追加项已完成：拒绝过期观察作为当前 dispatch 证据、重复消息身份和公开层嵌套隐藏材料；非零时刻计划需要完整位置快照；已有持物在失去 pick 后仍允许合法释放；业务 witness 必要条件拒绝“只有导航和观察但自称已完成订单”的作者证明。读取过期消息作为历史信息仍允许，不等于当前事实有效。

C 验收反思：三场景六份计划通过数值和业务必要条件审核。仓库木箱、纸箱均先集货再取得 dock 接收证据；家居复核依赖地面撤离；工厂在现场检查及实际返回后读取维护收据。任务 ID、成员和合法路线可以替换，审核不要求重现固定答案。静态计划中的收据读取仍不能证明收据已实际发生。

E 最终验收（2026-09-18）：`env_isaaclab` 下 212 项定向回归通过；生成并读回审核三场景 × 九种条件共 27 个题包，21 组单因子对照通过。每个题包包含作者专用 `authoring/review.json`，记录数值审核、业务必要条件和未验证边界。输出位于 `tmp/goal_reliable_v2_20260918_8iggiclh/`，总报告为该目录的 `audit_report.json`；运行输出不作为维护源码提交。最终复核还封闭了 API 嵌套声明和 envelope，并对缺少注册审核规则的新增业务 outcome 拒绝准入。

实际执行的最终回归命令：

```bash
/home/pupil/miniconda3/bin/conda run -n env_isaaclab env PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_goal_capability_audit.py \
  source/EAI/test/test_benchmark_goal_business_review.py \
  source/EAI/test/test_benchmark_release_manifest.py \
  source/EAI/test/test_benchmark_challenge_contract.py \
  source/EAI/test/test_benchmark_authoring.py --tb=short
/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab env PYTHONPATH=. python tmp/audit_goal_reliable_v2.py
git diff --check
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 避免 Conda Python 自动载入系统 ROS 的 pytest 插件。本轮未启动 ROS/Isaac。场景几何/初始位置/可见性绑定、静止机器人占地、后端安全中断、真实业务收据、因子生效和计分验证仍由其他模块完成，因此保持 `execution_status=external_pending`、`scoreable=false`。

## 后续场景接入：城市（侧边会话负责实现）

- [x] 接收并审核城市 prepare 草稿中的对象/区域/路线标识、公开地图、业务结果、可靠技能题面与 API；校验其列出的本地源文件内容。
- [x] 将城市注册为第四个任务来源，以独立候选类型纳入统一报告；服务扩展提案和能力缺口可审核，不自动激活服务。
- [ ] 城市场景团队完成尺寸/净空/路线的物理绑定、受影响人员名单、危险区、集合容量和动态实体绑定。
- [ ] 在城市服务接口及参数确定后，由任务生成侧补充完整业务结果审核、两份独立见证及具体因子 treatment，再执行完整题包和单因子冻结门槛。
- [ ] 城市执行与评价团队交付实际服务回执、业务证据和参考运行；城市尚不纳入三个已审完整业务族或正式计分集。

协作边界（阶段18更新）：按用户新增授权，由城市代理推进城市包的业务服务接线，与城市侧边会话保持单一文件所有者。
主会话负责任务接入、审核和验收，不改城市 USD 原始资产；公共约束和参与者协议不按三个已有场景写死。
结构说明（2026-09-19）：原 `demo/city/` 场景运行时已合并为 `demo/city_benchmark/scene/` 子包，
城市侧边会话结束后的所有权收拢；历史证据中的 `demo/city` 路径对应合并前源码。

验收反思要求：每步区分实验定义参数与场景测量值、局部必要条件与全任务可行性、作者审核与后端契约符合性。缺少场景绑定时保持 pending；不以自主抓取未验证阻挡理想技能主轨道。

## 阶段 8：统一接入、内容冻结和可重复审核

- [x] A：注册城市为第四个任务候选来源，读取真实 prepare 包并校验全部产物与源 hash；过期协议、缺文件或身份错误拒绝接入。
- [x] B：建立人员引导、后续交通管制、事件报告的版本化服务扩展提案，明确参数、可见实体、回执与启用条件；草稿不会自动授予机器人能力。
- [x] C：冻结任务身份、数值布局、预算、私有事件计划及题包内容；同布局/同任务的因子变体不跨 development/test，当前公开模板只作为开发或待审核保留集。
- [x] D：把三场景九条件、单因子对照、磁盘读回、城市候选和冻结审核收敛到维护的 CLI；输出作者报告与统一发布门槛。
- [x] E：完成定向回归、真实城市产物接入与全矩阵审核，更新交接文档和剩余条件。

出口边界：四场景注册不等于四个可计分题包。城市缺少完整业务见证、人员容量/危险区绑定和动态实体服务闭环时，须在同一报告中显示 blocked。冻结已公开模板不构成新的 held-out 泛化证据。

A 验收反思：城市接入专项 60 项通过；真实准备包的 10 个产物和 24 个本地源文件字节绑定通过，
复制后和统一输出目录改名后均可重新审核。这只验证所列文件，不认证 provider revision 或完整
USD 依赖闭包；接入结果仍是准备稿。

B 验收反思：服务提案专项 52 项通过，覆盖未观察人员、过期/未投递引用、事故车与后续车辆混淆、
现场角色缺证据及提案伪激活。服务参数与城市现有适配器对应，但实际服务调用、现场角色持续
占用、路径/集合容量和独立业务回执仍需城市团队绑定。

C 验收反思：冻结专项 45 项通过。同任务身份、同公开世界或同声明空间布局会形成传递闭合组，
重命名题号、调整文字/载荷和变更因子标签不能将同布局拆到 development/test。当前每场景只有
一个独立声明布局，统一产物显式标为 reserved；冻结实现不声称已完成盲测集建设。

D 验收反思：统一 `goal_suite` 提供 list/audit/verify，使用新目录原子发布并拒绝覆盖旧版本。
独立复核发现目录符号链接可绕过文件清单，将 evaluator 暴露给 release；已修复目录/悬空
符号链接、外部报告链接及根路径别名的审核漏洞，16 项入口测试通过。已有 `validated=true`
不能代替当前文件、语义审核和完整条件覆盖的读回验证。

E 最终验收（2026-09-18）：主线 11 个明确指定测试文件共 380 项通过；边界修复后再跑统一
入口专项 16 项通过（包含原 11 项及新 5 项反例，不叠加报告为独立用例总数）。城市场景六文件
21 项通过；城市 `test_foundation.py` 首次集成的 4 项旧观察接口失败已由城市维护会话修复。
后续 `authoring_12` 交接的城市、foundation、接入、服务提案和统一入口联合回归为 173 项通过。
本会话未修改城市源码，也未降低共享契约来兼容旧调用。

阶段 8 首次统一输出为 `tmp/goal_suite_20260918_stage8_v2/`：27 个完整作者题包、21 组因子对照、
1 个城市准备候选；各完整业务族仍为 1 个独立声明布局。完整生成和独立磁盘读回均启用
`--verify-sources`，总报告保留 `held_out_validated=false`、`execution_validated=false`、
`scoreable=false`。更早输出绑定旧版源码，不应用作最新交付。使用说明和交接责任见
[GOAL_SUITE.zh-CN.md](GOAL_SUITE.zh-CN.md)。

本轮实际命令（仓库根目录；未启动 Isaac/ROS）：

```bash
/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q \
  source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_goal_capability_audit.py \
  source/EAI/test/test_benchmark_goal_business_review.py \
  source/EAI/test/test_benchmark_release_manifest.py \
  source/EAI/test/test_benchmark_challenge_contract.py \
  source/EAI/test/test_benchmark_authoring.py \
  source/EAI/test/test_benchmark_goal_suite.py \
  source/EAI/test/test_benchmark_goal_freeze.py \
  source/EAI/test/test_benchmark_goal_city_intake.py \
  source/EAI/test/test_benchmark_goal_service_catalog.py --tb=short

# 最后一次边界修复后的专项复核。
conda run -n env_isaaclab env PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q source/EAI/test/test_benchmark_goal_suite.py

/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.city_benchmark.prepare --output-dir tmp/city_benchmark/main_goal_intake_20260918
/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite audit \
  --city-package tmp/city_benchmark/main_goal_intake_20260918 \
  --verify-sources --output-root tmp/goal_suite_20260918_stage8_v2
/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite verify \
  --output-root tmp/goal_suite_20260918_stage8_v2 --verify-sources
git diff --check
```

复跑生成命令需选择尚不存在的新目录。阶段 8 的任务生成侧工程已完成；城市完整业务准入、
未曝光独立数据集、物理后端符合性、因子执行与正式评分仍按上文责任保留待办，不因本轮结项勾选。

## 阶段 9：主会话统筹的执行接入与首次部署

用户已授权主会话持续分配子任务、审核集成并在代码门槛通过后启动 Isaac/ROS 做真实验证。
Isaac 使用 `env_isaaclab`；ROS 进程使用系统环境。主会话统一安排 GPU 运行，避免并行仿真
争用资源；城市场景、家居 USD 和评价模型仍按各自写集协作。

- [x] A：接收城市修正版，关闭旧接口失败，审核时序、全集、通路证据及公开截止期；允许合法的非最短 ETA 方案。
- [x] B：实现独立 goal v2 有状态计划账本：选队、提交/修订、依赖与时窗、派发/取消和当前事件准入；后端完成必须有任务绑定的回执。
- [x] C：在固定输入和内容 hash 下运行 Warehouse 的短程真实控制导航，验证 Isaac 生命周期和位置读回；逐步 applied-command、支撑/碰撞等未归档证据明确保留 pending。
- [x] D：建立真实导航报告的独立只读验收，并将一次技能派发、实际运行和后端回执连接；不将短程诊断当作整题物理绑定或业务成功。
- [x] E：完成专项反例、联调和实际部署记录，更新统一题包及交接文档，逐项记录阶段反思与下一轮阻塞条件。

并行写集：计划账本子代理仅新增 `goal_session.py` 及其测试；部署审核子代理仅新增
`navigation_deployment_review.py` 及其测试；城市侧仅维护 `demo/city_benchmark`。主会话负责
实际启动、适配整合、清单与最终审核。公开 schema 的修改需先同步各团队。

出口要求：协议被接纳、真实控制执行、场景业务通过和正式可计分是四个不同结论。不得用
导航 smoke、代理自报成功、作者固定见证或单次仿真替代完整业务、因子效果和测试集验收。

A 验收反思：主会话复现的四类误判已由城市维护者修复，`authoring_13` 的 10 个产物与24个
源文件核验通过；城市、foundation、接入、服务提案和统一入口共 225 项通过。题目不再把最短
ETA 的局部策略设成答案，时限和计时锚点向参与者公开，隐藏全集仍保持私有。业务骨架依然
只接受未来可信后端提供的证据摘要，不认证观测来源或物理实现。

B 验收反思：`GoalSession` 专项 56 项通过，包含重复派发、伪完成、取消未确认、跨计划残留
资源、成员/能力失效、过期消息、状态快照和依赖完成。信任宿主负责调用和回执真实性；账本
不接受参与者上报的物理真值，也不从计划效果自动更新位置/所有权。

C/D 验收反思：在 Quadro RTX 6000、`env_isaaclab`、`cuda:0`、headless 下完成两次 Warehouse
Carter 短导航。首轮为现有导航入口，第二轮由 `goal_navigation_probe` 在启动前提交计划并派发，
通过 `open_simulator_session` 所属运行时执行，再审核本次回执。第二轮 254 个控制步、5.08 秒
仿真时间、58.83 秒进程时间，退出码 0；第 253 步实测目标误差约 0.49768 m，小于 0.5 m目标半径。
账本成功回执绑定本次报告、启动请求和审核内容 hash。未启动外部 ROS。

现有运行时没有 applied-command 逐步归档；导航观察通过不等于控制因果、地面支撑、接触力或
碰撞安全认证。因此 `physical_control_validated=false`。诊断使用独立 region/route ID，尺寸
净空仍是公开实验声明，不能借此宣称原仓库订单题的抽象区域已完成 USD 绑定。

E 验收：账本、部署审核、probe、goal-driven、constraints 和 provenance 六文件主线联测
263 项通过。导航 probe 7 项包含换输入拒绝启动、旧目录/旧报告不可复用及后端失败保持 pending。
城市交接联测225项与该组有不同范围，不累加声称无重叠总数。真实部署结果位于
`tmp/goal_navigation_probe_20260918/run/summary.json`，独立读回为 `navigation_observed`。
统一生成/读回输出更新为 `tmp/goal_suite_20260918_stage9_city13/`，仍是27题包、21组对照、
1城市准备候选，所有整题 execution/held-out/scoreable 结论保持false。
入口与信任边界见 [GOAL_DEPLOYMENT.zh-CN.md](GOAL_DEPLOYMENT.zh-CN.md)。

阶段9实际部署与回归命令（仓库根目录）：

```bash
/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_navigation_probe \
  --output-root tmp/goal_navigation_probe_20260918 --execute --device cuda:0
/home/pupil/miniconda3/bin/conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q source/EAI/test/test_benchmark_goal_session.py \
  source/EAI/test/test_benchmark_navigation_deployment_review.py \
  source/EAI/test/test_benchmark_goal_navigation_probe.py \
  source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_provenance.py --tb=short
```

第二次运行应先按部署文档准备新目录。下一阶段仍需处理控制指令证据、业务区域/对象绑定及
联合操作后端；当前诊断没有代替这些工作。

## 阶段 10：控制步证据与场景绑定清单

- [x] A：为三场景公共对象、区域、路线和机器人配置生成作者专用绑定清单；逐项区分实验声明、已有场景候选和真实测量，禁止仅凭名称或文件 hash 认证物理绑定。
- [x] B：在共享运行时记录每步提交给 `env.step` 的命令和该步后的根状态，核对步数、时间、机器人身份与数值有效性；不称为执行器力矩或接触证据。
- [x] C：独立部署审核验证新证据，旧报告缺字段仍保持 pending；反例覆盖错步、错时、错机器人及与位置读回冲突。
- [x] D：冻结代码后再次运行真实 Warehouse 诊断，核验账本回执和命令/位置证据一致；没有后台场景篡改或旧报告复用。
- [x] E：更新验证记录、统一交接与下一批明确可部署的场景绑定缺口。

主会话负责 `runtime.py` 的最小接入与实际启动。控制证据子代理负责纯记录器和独立审核器；
绑定子代理负责只读清单及源内容核验。城市和家居场景源码/资产继续由其所有者维护。

B/C 验收反思：主线九文件联合回归 388 项通过，覆盖每步采样、控制张量突变、步进异常、
非有限状态、缺步/错时、foreign robot、移动 body 与 articulation 根状态区分、加载源码绑定、
位置和 yaw 冲突，以及原账本和数值约束。新增记录器只认证命令提交和根状态读回的文件一致性，
不能认证控制因果或接触物理。记录在位姿覆盖前完成；失败记录锁存，不通过删掉失败样本恢复有效。

D 验收反思：新目录 `tmp/goal_navigation_probe_20260918_stage10/` 在 `env_isaaclab`、
Quadro RTX 6000、`cuda:0`、headless 下完成一次实际运行；进程退出 0、60.39 秒墙钟，
254 步 × 0.02 秒 = 5.08 秒仿真。逐步记录有 254 组 Carter 命令、Carter 与挂载机械臂根状态；
审核交叉比较 54 个位置/yaw 样本，没有 pending。第 253 步到达，误差 0.4976769 m，半径
0.5 m；账本在 5.08 秒接受绑定本次报告的终态回执。运行前后源/输入 hash 一致，未使用旧报告，
未启动外部 ROS。整题业务、物理控制与 scoreable 仍为 false。

三场景绑定报告位于 `tmp/goal_scene_bindings_20260918_stage10/`，重新构建并逐文件读回通过。
清单覆盖 75 个公开项（工厂 20、仓库 26、家居 29），所列源文件均存在；10 项局部 USD/栅格
检查通过不代表任何完整题目已绑定。Warehouse 木箱/纸箱在两套作者配置中分别为 12/60 kg、
3/8 kg；House 扶手椅/书堆分别为 14/28 kg、2/2.5 kg，几何尺寸也不一致。Factory 服务车尚无
已审查候选，楼梯仍需连续支撑绑定。现有机器人类型候选不能替代独立实例及完整能力配置。
详细范围与命令见 [GOAL_SCENE_BINDING.zh-CN.md](GOAL_SCENE_BINDING.zh-CN.md)。

A/E 验收反思：绑定专项 33 项通过，覆盖过期/缺失来源、改名误绑定、伪造测量、删除对象、
局部几何/单点栅格不越权、禁覆盖与 CLI 读回。测试仅修改隔离副本，未改生产源文件或资产。
阶段10交付时未修改实跑所绑定的生产代码；阶段11新增生产文件后，该导航证据仅对历史冻结来源有效。下一批交接必须消除
题面与部署配置差异；不能通过给旧场景实体改名字来完成绑定。

阶段10主线实际命令（均从仓库根目录）：

```bash
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q source/EAI/test/test_benchmark_control_step_evidence.py \
  source/EAI/test/test_benchmark_runtime_control_evidence.py \
  source/EAI/test/test_benchmark_navigation_deployment_review.py \
  source/EAI/test/test_benchmark_goal_navigation_probe.py \
  source/EAI/test/test_benchmark_goal_session.py source/EAI/test/test_benchmark_provenance.py \
  source/EAI/test/test_benchmark_goal_driven.py source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_runtime_lifecycle.py --tb=short
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q source/EAI/test/test_benchmark_goal_scene_binding.py --tb=short

conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.warehouse_benchmark.main --check-assets
conda run --no-capture-output -n env_isaaclab env PYTHONPATH=. \
  python -m demo.benchmark.goal_navigation_probe --output-root tmp/goal_navigation_probe_20260918_stage10
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_navigation_probe \
  --output-root tmp/goal_navigation_probe_20260918_stage10 --execute --device cuda:0
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_scene_binding audit --output-dir tmp/goal_scene_bindings_20260918_stage10
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite verify --output-root tmp/goal_suite_20260918_stage9_city13 --verify-sources
git diff --check
```

三场景清单的独立读回另调用 `validate_goal_scene_binding_inventory`；导航部署的独立子代理
重新调用审计器并重放账本，结果与保存的回执一致。统一题包再次读回为 27 包、21 对照、
1 城市候选，held-out 与 scoreable 仍为 false。所有新目录命令复跑时必须换用尚不存在的目录。

## 下一批任务：从候选清单转为目标题目部署契约

- [x] 任务生成主线：为四场景基准题编译独立对象/配置映射，保持冻结公开参数，拒绝静默接入旧固定 DAG 的冲突参数。输出仅为配置；各 treatment 的实际部署一致性仍需下游读回。
- [ ] 场景团队与主线审核：确认原生道具的组合后几何、初始区域、固定抓取点、物体身份和观测位置；质量可为实验定义，但必须与实际配置一致。
- [ ] 场景团队：绑定区域接受边界、双向共享路线、机体/携物/联合队形净空及支撑；Factory 开放楼梯需要连续的入口—梯段—平台连接，不能只使用顶端 AABB。
- [ ] 机器人/运行时团队：为每名候选建立独立实例与附件配置，核验可靠技能的合法调用效果、失败和安全中断；不新增 VLA 或自主抓取算法要求。
- [ ] 任务生成主线与后端：将逐项绑定证据纳入作者审核产物，与业务回执、通信和成员变化事件接入逐步联调。
- [ ] 城市维护会话与主线：将已交付的城市服务后端接入可信宿主，补齐完整业务见证和具体 treatment；当前配置接入 authoring_17，继续作为准备稿。

这些是尚未完成的明确交接项，不在阶段10诊断结果中自动勾选。实验回放、因子效果判定、
评价模型、不可变资产发布和独立 held-out 数据仍由对应团队负责。

## 阶段 11：四场景部署配置与可靠技能参考后端

- [x] A：工厂、仓库、家居生成显式对象参数部署指令、区域/通路与独立出生位候选；保留无法通过地图或几何检查的阻塞项。
- [x] B：城市 authoring_16 在阶段11进入同一部署配置入口，阶段12更新到authoring_17；绑定地图语义、区域/通路和机器人出生位候选；侧边会话交付业务服务，主线记录历史在岗区间等待接线要求。
- [x] C：共用编译器建立公开成员到独立运行实例的一对一映射、物体参数与来源一致性审核；配置读回拒绝实体、控制命名空间和后端状态键复用。
- [x] D：接入有状态可靠技能参考后端，验证导航/固定点操作/联合搬运的条件、资源、取消及成员失效；记录可追踪回执，明确区别参考状态效果与真实物理部署。
- [x] E：四场景产物生成和独立重验，专项反例、集成测试、流程复盘与交接文档更新。主线12文件468项通过，独立审查重建四份配置、重跑四份参考诊断后无剩余阻断问题。

子代理统一为用户指定的 `gpt-5.6-sol/high`：Raman 负责三室内场景适配，Euler 负责城市配置适配，
McClintock 负责独立审核；主线负责共享编译器、参考后端、集成与验收。城市服务不由 Euler 重复实现。
本阶段不会把理想技能参考执行标成 PhysX 验证，也不会用尚未测量的空间数值解锁真实部署。

阶段11产物：`tmp/goal_deployments_20260918_stage11/` 与 `tmp/goal_reference_20260918_stage11/`。
具体命令、参数、回执范围与测试记录见 [四场景配置与参考执行](GOAL_DEPLOYMENT_CONFIGURATION.zh-CN.md)。
工厂/仓库/家居分别配置4名成员和2件任务对象；城市3名成员、41个区域、42条公开路线。
所有配置与参考诊断均保持 physical/business/scoreable=false。

阶段反思：独立审查发现途经区域占用遗漏、参考后端代确认外部取消、同名重入沿用旧位置三个
问题，均先复现再修复，并补充回归。等待期间还必须重查证据有效期。城市连续栅格检查发现
端点全空但中间穿障碍的路线，说明点检查不能代替通路验证。当前所有结论止于已检查范围。

## 阶段12：配置实际应用与可信运行接线（执行中，2026-09-18）

原清单的一条同时包含实体创建、物理能力和业务绑定，无法以一次静置运行验收。
现按证据粒度拆开推进；下面已完成项均不使任务可计分。

- [x] 建立串行原生诊断入口、父子进程锁、请求/源文件/实际设备/仿真时钟审核；父进程中断时保留串行排他并清理子进程。
- [x] 第一轮四景真实 Isaac GUI 各执行240控制步：Factory/Warehouse/House各4个独立实例，City为3个；独立状态键、PhysX位姿和质量、时间推进均有读回。Factory使用已有明确的CPU物理回退，其他三景使用cuda:0。
- [x] Warehouse和House各两个真实原生道具应用冻结质量、尺寸及固定点，并读取PhysX质量/位姿；独立对象审核器复核数值。该项不证明碰撞cooking、持有、接触或业务区域成立。
- [x] 四景采集实际PhysX离散支撑/净空射线，记录命中实体；空域、连续净空、动态占用未知处保持阻塞。
- [x] 城市侧交付authoring_17及CityEpisodeEvidenceHost：已投递观察、连续角色占用、人车读回、服务回执有可信host接口；离线接口通过不代表已完成城市业务执行。
- [x] ZCode最终四景代表性截图复核：明确发送并审核Factory_v7楼梯入口、Warehouse_v8任务道具、City_v5团队总览及House_v5的M20_2/Iris近景。House近景确认可见脚轮未埋地，但M20_2位于门外台面；Iris/客厅背景在室内，不能宣称所有机器人已在室内。旧图只保留各自版本结论，不外推为新图已看过。
- [x] House任务对象改为室内客厅候选、纠正地面出生高度并实际复验：House_v3/v4低高度导致脚轮穿入z=.25楼板；v4倾角勉强低于阈值不构成修复。v5两M20根高改为.77、Carter为.45；两M20最终脚轮中心约.340、原生半径.09，与.250地板吻合，最大倾角约.006rad。该项仅为配置/静置诊断。
- [ ] House完整语义准入：室内道具/Iris已有像素支持，地面机器人门内外与公开初始区域关系、连续住户路径和真实业务仍待绑定；不能将入口外台面当作室内客厅。
- [x] 固定点真实joint后端与单物体微诊断：闭合源码warehouse_v4完成440步连接/短程跟随/释放及三类拒绝；独立审核重算位姿，最大连接anchor误差约3.41微米。范围仅为单个3kg物体理想连接，不证明抬升、双机承载或业务成功；旧目录不重签。
- [ ] 当前占用与三维安全查询：适配器已完成完整清单、未加载子树、点实例protoIndex和内部旋翼joint归属的fail-closed修复；真实占用仍因Iris convexDecomposition、Carter Xform等未支持几何阻塞，故无人机平移查询未放行。下一后端需PhysX cooked/property形状回调、完整清单及坐标/缩放标定，不能改用公开profile外形放行。

## 阶段13：物理形状读回与业务见证闭合（执行中，2026-09-19）

- [x] 建立独立 PhysX property/cooked shape 回调适配器：记录 stage cache、物理步、配置版本、有限超时、finished callback、foreign stage/path、非 `VALID`、重复 shape 和 cooking acceptance-only；注入式测试与原生占用回归共60项通过。该模块不放行 occupancy。
- [x] 完成 PhysX SDK 只读研究交接：确认 `query_prim`、`request_convex_collision_representation`、取消和 callback 字段的实际边界；同一 path 的多个 shape 保留 ordinal。研究报告见阶段13工作区 `handoffs/occupancy_backend.md`。
- [ ] 将适配器接入单一暂停 Isaac session，优先覆盖 Iris convex decomposition 与 Carter Xform/caster；必须完成嵌套变换、非均匀缩放、单位、同物理步和完整 shape coverage 标定，失败继续 blocked。
- [ ] Factory F-VERT-01：楼梯保持开放。真实 probe 已校准地面四足支持，但没有从入口推进到 surface entry，最大平面位移约9mm；需修正/验证路线跟随基线后重做入口、首级、第二级和上下行，不能把这次失败解释为楼梯封堵。
- [ ] House 初始区域和业务语义：ZCode新一轮实际像素审核仍显示M20_2位于门框外混凝土门槛/露台侧；复核门内外边界、地面机器人出生位和连续住户路径，不能用静置姿态或视觉背景代替室内准入。
- [ ] City 业务见证：`guide_people`、`control_traffic`、`report_incident` 尚未接入 participant API/真实 episode；需补完整隐藏人车 roster、角色区间、服务回执、动态轨迹、容量/危险区/救援通路和通信/成员变化 treatment witness。
- [ ] 继续按四场景串行做真实物理和视觉复核；截图必须实际发送给 ZCode，像素结论不能替代碰撞、通路或业务证据。

阶段13交接和反思见[阶段13审核记录](STAGE13_REVIEW.zh-CN.md)。本阶段所有产物仍保持 `physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`；实验回放、因子统计和评价模型仍由其他开发者负责。

## 阶段14：基线纠正与四场景业务见证审查（已完成本阶段审查，2026-09-19）

- [x] 复现并纠正 Factory F-VERT-01 micro-probe 的低速命令死区：`pre_edge`、`first_tread`、`second_tread` 统一使用 `0.55 m/s` 公共导航基线；新增回归约束，防止合法但无运动的 `0.10 m/s` 命令再次被当作路线失败。
- [x] 在 `env_isaaclab`、Isaac Sim 5.1、CPU 物理下重跑 Factory `first_tread`：真实根位姿从入口推进到 `route_index=2`，接触读回命中 `SM_StairsSmall` 碰撞路径；首段随后以 `support_unknown` fail closed。楼梯保持开放，物理成功仍未宣称。
- [x] 追加 Factory 控制诊断：`0.25 m/s` 在入口地面路线进入低速控制死区；临时 B2 副本在公共二维路线准入处因缺少 `b2_1` 路线停止。两项均不作为楼梯或 B2 物理失败结论，原始输出保留在 `tmp/goal_stage14_stairs/`。
- [x] City 三项业务服务完成 participant/API/receipt 边界审查；177 项城市服务、intake、deployment focused tests 通过。真实 episode-owned roster、角色区间、动态轨迹、外部回执和通信/成员变化效果仍阻塞。
- [x] Modern House 门边界完成静态与实际图像审查；ZCode 确认可见 M20_2 支撑足在门框外混凝土/门槛侧，单帧不能证明连续住户路径。initial-region、门内边界与业务路径仍阻塞。
- [x] Warehouse 宽路线/服务车/联合携物/成员退出/安全取消完成任务契约审查；56 项 focused runtime/recovery tests 通过。连续 3-D 净空、服务车 episode 状态、负载/双机形成、物理重新获取和 safe-hold witness 仍阻塞。
- [ ] 接入单一暂停 Isaac session 的 cooked/property shape 后端，覆盖 Iris convex decomposition、Carter Xform/caster、嵌套变换、非均匀缩放、单位和完整 shape coverage。
- [ ] 修正 Factory 首段控制/几何/足端接触链，串行重跑入口、首级、第二级、上行和下行，并生成可独立复核的 support-relative traversal evidence。
- [ ] 为 House 绑定门外/门槛/门内区域和连续 resident path 的真实几何与 PhysX 净空，完成门内出生位和整条住户路径 episode witness。
- [ ] 为 Warehouse 接入服务车与动态占用、联合负载/固定点/双机携物形成、成员退出再获取和安全取消的真实 witness。
- [ ] 为 City 接入完整 episode-owned 服务源，并至少完成稳定条件与一个通信/成员变化 treatment 的成对业务见证。

阶段14审核、证据路径和反思见[阶段14审核记录](STAGE14_REVIEW.zh-CN.md)。阶段14可完成的代码与契约项已闭合，但上述物理和业务 blocker 未满足 release gate；四场景仍保持 `physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。

## 阶段15：机器狗 RL 楼梯基础能力审查（执行中，工作代号 `rl`）

- [ ] 审查 Go2、Lite3、B2 的 RL 模型、命令顺序/坐标系、默认姿态、速度限制、模型资产和地形训练假设。
- [ ] 用现有证据区分 RL/命令适配、场景几何与 PhysX cooking、路线注册、接触采样和任务逻辑问题；不把单次首级失败直接归因给模型，也不关闭或绕行楼梯。
- [ ] 在 `env_isaaclab` 下按场景串行复现可行的最小试验；缺少 Lite3 模型、权重或训练配置时保留明确的外部输入 blocker。
- [ ] 输出 Go2/Lite3/B2 的逐机结论、复现命令、证据 hash、推荐修复顺序和对任务生成侧的边界建议。

审查代理只写阶段14工作区的 `handoffs/rl_stair_review.md`，不修改任务契约、场景 JSON 或生产运行时；主会话在核验报告后决定是否进入基础技能后端修复阶段。

第一轮证据位于 `tmp/goal_physical_stage12/{factory_v1,warehouse_v4,house_v1,city_v1}`，
包含request、launch、runtime_manifest、process_result、physical_report、独立review和object_review。
相机代码更新后这些是历史版本证据，不能回填字段后冒充新版本复验。
仓库v1的USD ABI启动失败、v2的USD API失败和工厂v1的视觉失败均保留。
以下跨模块整体验收项仍需逐项完成：

- [x] 主线与机器人运行时：四场景已建立独立实体并读回控制状态键、质量/位姿和时间，分别为4/4/4/3台。House高度修订后的闭合批次为Factory_v7、Warehouse_v8、House_v5、City_v5；完整profile、工具安装和业务动作仍pending。
- [ ] 室内场景维护者与主线：将对象/区域候选放入组合场景检查；修复被阻塞的仓库宽路线，确认服务车道具，绑定目标区域与地面出生位；保持公开参数冻结。
- [ ] 工厂场景维护者与主线：楼梯题面保持开放；已核对黄色构件为静态无碰撞RampShort，距中心线至少.911m，不能称为安全门。源几何499条中心竖向采样有连续踏面/平台，但实际首级约.210m与公开.18m声明有差异；真实cooking、连续净空、足端接触和上下楼仍未验证。不得静默修改冻结参数或关闭楼梯绕过此项。
- [ ] 运动后端维护者与主线：绑定四场景三维空域、无人机出生位和简易避障；提供包括闲置机器人、携物包络及动态人车的当前占用证据。
- [ ] 可靠技能后端维护者与主线：单物体固定点连接/跟随/释放已接入并独立复核；双机联合搬运、成员退出后的持有状态恢复、安全取消和正式业务接线仍待完成，不增加VLA或自主抓取算法考核。
- [ ] 城市侧边会话与主线：接线field_role_interval及观察/人车轨迹/服务回执采集器，补全业务见证与treatment后审核共享服务接口；不使用请求ack代替业务成功。
- [x] 主线审核：四场景配置应用与独立机器人读回证据已输出；所有归档保持physical/business/scoreable=false，并明确记录占用、城市业务和连续通路阻塞。历史源版本证据与新批次分别保留。实验回放、因子统计和评价模型交对应开发者。

本轮证据矩阵、修订反思和下一后端交接见[阶段12审核记录](STAGE12_REVIEW.zh-CN.md)。
原始闭合审核为`tmp/goal_stage12_close_audit.json`；题包为`tmp/goal_suite_stage12_final`，
配置为`tmp/goal_deployments_stage12_close`。诊断交接不等于上列未勾选的整体验收门禁完成。

## 阶段15：四场景串行诊断与业务见证复核（2026-09-19）

- [x] Warehouse 新一轮 `env_isaaclab/cuda:0` 240步诊断完成并结构化读回：Iris、两台 M20、Carter、12kg 木箱和3kg纸箱的实例/参数/根状态可追踪；对象 cooking、固定点执行、联合负载、服务车动态路线、成员退出重新获取和 dock 业务仍未验证。
- [x] Modern House 新一轮 `env_isaaclab/cuda:0` 240步诊断完成；四个机器人、14kg扶手椅和2kg书堆可读回；结合阶段14实际像素和本轮 `96 passed` focused 测试，门内 initial-region、门槛边界、连续 resident path 与三维净空仍 blocked。
- [x] City `authoring_17` 新一轮 `env_isaaclab/cuda:0` 240步三机器人诊断完成；城市区域/路线/动态占用仍 blocked。`guide_people`、`control_traffic`、`report_incident` 的服务接口、角色区间和回执边界完成静态审查，本轮 focused 测试 `198 passed`；真实 episode witness 和成对 treatment 仍未完成。
- [x] Feynman 单机器人 Factory probe：通过 `vertical_task.py` 调用 `simulator.py/open_simulator_session`，单 Go2 从一楼 `[1.5,-0.35,0.4]` 向二楼 `[1.5,-7.5,1.57]` 上行；`route_index=2` 且足端命中 `SM_StairsSmall/Section0`，第820步 `support_unknown`，未到达二楼。楼梯保持开放，不能把该结果解释为楼梯关闭或任务逻辑失败。
- [x] F-VERT-01 独立首级/第二级 probe：Go2 的 `first_tread` 在第353步到达 `[1.5,-3.33]`，`second_tread` 在第353步到达 `[1.5,-3.73]`，随后分别以 `support_contact_gap_timeout` fail closed；两份原始 JSONL 均绑定 SHA-256，仍未形成连续梯段、平台停留或下行证据。
- [x] 补齐 Lite3 canonical USD 足端映射 `FL_FOOT/FR_FOOT/HL_FOOT/HR_FOOT`，并在 `env_isaaclab` 重跑首级 probe：校准、入口和首级目标均可读回，第332步以支撑间隙停止；相关垂直 focused 回归为 `79 passed`。
- [x] 复核 B2 的二维 route blocker：确认是楼梯排除区与常规 tracking reserve 对 `0.70m` footprint 的重复膨胀；垂直接近段改用 `0.0m` reserve 后 B2 接触采样和校准成功，剩余结果限于运动基线未在预算内到达楼梯入口。
- [x] 代理服务 429 的恢复、主会话接管、Warehouse/House/City/Feynman 交接、阶段15审查与反思已登记；不把代理错误当作物理结论。
- [ ] 接入单一暂停 Isaac session 的 PhysX cooked/property shape 后端，覆盖 Iris `convexDecomposition`、Carter `Xform/caster`、嵌套变换、非均匀缩放、单位和完整 shape coverage。
- [ ] Factory：在同一公开路线和单机器人契约下补足连续上行、二楼停留及下行的 support-relative evidence；B2 仍需单独记录到达楼梯入口后的运动证据。暂不处理 RL 模型修复。
- [ ] Modern House：用真实门外/门槛/门内几何和连续净空绑定 initial-region 与 resident path，再验证固定点家具操作和安全取消。
- [ ] Warehouse：接入服务车 episode 状态、携物 swept envelope、固定点/双机承载形成、成员退出重新获取和 safe-hold/clear-route 业务 witness。
- [ ] City：把三项服务接入 public participant skill 与 episode-owned 人车 roster，完成稳定条件和一个通信或成员变化 treatment 的完整业务见证；请求 ack 不得替代服务 receipt。

阶段15证据和反思见[阶段15审核记录](STAGE15_REVIEW.zh-CN.md)。本阶段没有解除正式 release gate；四场景仍保持 `physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。实验回放、因子统计、评价模型及 RL 基础技能修复仍由其他所有者负责。

## 阶段16：非楼梯任务生成与审核收敛（2026-09-19）

本阶段按用户决定暂不处理楼梯 RL/运动模型；楼梯仍作为公开的基础能力入口保留，连续支撑和上下行证据继续由后续运动后端负责。本阶段只收敛任务生成、公开技能和审核边界。

- [x] City 将 `guide_people`、`control_following_traffic`、`report_incident` 登记为 participant-facing public skills；每项包含参数、能力组合、前置、效果、失败码、证据、取消和超时语义。
- [x] City participant coverage 将三项服务报告为 `registered_runtime_pending`；公共技能入口与真实服务运行时分开，未把 ack、静态轨迹或本地 adapter proposal 当作业务成功。
- [x] 同步共享可靠技能时长表、City intake、服务目录和旧 adapter proposal；active public skill 与 runtime-pending proposal 可以通过明确映射共存，避免注册表和场景约束字段失配。
- [x] 完成 Modern House 任务审核：家庭业务目标、扶手椅/书堆、固定点负载、家具携物包络、resident path、团队退出和通信/成员因子均有题面及隐藏证据要求；审核文档见 `handoffs/house_task_review.md`。
- [x] 完成 Warehouse 任务审核：货架识别、木箱/纸箱集货、dock receipt、库存 closeout、逐成员载荷、双机队形、服务车、成员退出恢复和通信/成员因子均有题面及隐藏证据要求；审核文档见 `handoffs/warehouse_task_review.md`。
- [x] 运行 City/目标任务/服务目录/约束/可靠技能 focused 回归，修复新技能带来的共享时长和 intake 旧断言；所有离线审核通过。
- [x] 保持四场景 release gate fail closed：`physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。
- [x] City 完成 v8 候选服务诊断：五项服务操作均产生真实角色/轨迹/回执/连续通路读回，并保留 `scoreable=false` 与非正式绑定状态。
- [ ] City 完成正式 episode-owned 人员/车辆 roster、危险区和集合容量、角色区间、服务 authority、动态轨迹、独立 receipt，并完成至少一个稳定条件与一个通信或成员变化 treatment 的成对见证；v8 诊断不能替代该门禁。
- [ ] Modern House 绑定门外/门槛/门内区域、连续 resident path、动态占用和家具/书堆固定点操作，完成成员退出和最终净空业务证据。
- [ ] Warehouse 接入 Iris/Carter cooked/property shape、携物 swept envelope、联合固定点/双机承载、service car、成员退出再获取和 safe-hold/clear-route/dock receipt 证据。
- [ ] 在真实场景证据完成后重新生成四场景题包并做磁盘读回、单因子冻结和 held-out 审核；此前不提升 scoreable。

阶段16审核与反思见[阶段16审核记录](STAGE16_REVIEW.zh-CN.md)。本阶段证明的是“题面、participant API、约束和隐藏证据要求可以一致表达”，没有证明 City 服务或 House/Warehouse 业务在 Isaac 中已经完成。
