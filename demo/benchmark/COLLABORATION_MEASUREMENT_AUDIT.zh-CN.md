# Benchmark 任务生成与审核复盘

日期：2026-09-21
审计基线：`codex/factory-vertical-challenge`，HEAD `32caa78`

## 结论

当前 benchmark 已经是一个可交接的**任务生成与审核原型**，但还不能声称已经完成“协作成本测量与物理反事实归因”。题包、参与者 API、隐藏评估、见证计划、通信/成员变化处理和 fail-closed 发布门禁已经形成工程基础；研究规格中真正缺少的是每道题测量什么、允许改变什么、凭什么归因以及如何证明四个场景具有同等覆盖。

任务数量偏少的判断成立，但要先区分数量和机制覆盖：默认 goal 套件是 4 个场景 × 9 个条件 = 36 个 package，包含 5 个 `communication_quality` 等级和 4 个 `team_continuity` 等级；它们复用每个场景的一个主任务 ID（`FACTORY-GOAL-01`、`WAREHOUSE-GOAL-01`、`MODERN_HOUSE-GOAL-01`、`CITY-GOAL-01`）。28 个 factor pair 是单因子条件检查，不是 28 种独立协作机制。当前每个场景仍只有一套固定业务骨架和两份作者 witness，数量增加主要来自条件展开。

只读复核还统计到：四场景分别只有 3、4、4、4 个业务 outcome；witness 计划节点约为 9/10、17/17、11/11、12/13。既有固定 DAG（例如 W-DAG-01、MH-DAG-01、F-INS/F-TRAFFIC/F-COV 及 City service 诊断）不计入这 36 个 goal package。它们应在发布报告中单独列出，不能把诊断 DAG 数量与 goal 机制模板相加来宣称覆盖。

入口一致性问题已在本轮修复：`goal_suite.py` 和
`goal_driven_tasks.generate_goal_task_packages()` 现在都从同一个 goal scene registry
读取四个 `goal_bundle` 场景。覆盖矩阵还会在 suite 生成和读回时校验默认 36/28 计数，避免
不同入口产生不同的“全量任务”。

历史文档仍有旧的 27/21 数量和“City 未准入”的表述（例如 `DELIVERY.zh-CN.md` 与 City README）；它们必须标为历史快照或改为当前 36/28 口径。否则接手者会把过期文档当成发布门禁。

## 已经闭合的部分

- 四场景都有统一的 goal-driven public task、participant API、hidden evaluator 和 release manifest。
- 每个主任务至少有两份语义不同的合法 witness，审核会检查 outcome 覆盖、能力、载荷、时序、资源和跨空地依赖。
- `communication_quality` 和 `team_continuity` 已有具体 treatment、隐藏事件和单因子冻结检查。
- 技能执行证据与业务成功分层；缺证据时使用 `external_pending`、`scoreable=false`。
- `build_goal_heterogeneity_certificate()` 明确区分见证敏感性和完整任务必要性，没有把声明级证书冒充物理证明。
- `oracle.minimum_team_certificate()` 具有能力/载荷覆盖检查，但代码自身声明不证明时序、并发或协同操作必要性。
- `goal_driven_tasks.py` 明确写出 `No optimality claim.`；作者 witness 不是最优 oracle。
- Go2/B2 楼梯模型、技能账本/业务结果分层、算法子进程协议等近期修复应作为运行平台交接输入，不改变 goal 主轨的科学口径。

## 仍未闭合的缺口

### P0：题目没有机器可读的测量目标

`experiment_factor` 只表达因子和等级，不能告诉接手者这道题测量哪一种协作机制、预期失败是什么、适用哪些指标、允许提出什么结论。需要新增 `measurement_target`，至少包含：`mechanism_family`、`collaboration_primitive`、`failure_mechanisms`、`eligible_metrics`、`allowed_claims`、`forbidden_claims`。

旧题不能静默标成已覆盖；应先标记 `legacy_measurement_status=unclassified`，直到完成映射。

### P0：最小团队与异构必要性证书不完整

现有证书的范围是 `fixed_witness_sensitivity_not_task_necessity`，且 `whole_task_necessity_validated=false`、`physical_necessity_validated=false`。它只说明删除某类成员会破坏某个固定 witness，不说明允许重新规划后小团队、同构团队或替代角色仍不可行。

需要记录搜索空间、替代成员范围、允许的重规划规则、证据等级和未搜索部分，并提供 `validated`、`disproven`、`unknown` 三态。搜索不穷尽时必须保持 `unknown`。

### P0：四类干预探针没有统一题包契约

通信和成员变化已有 treatment，但 I-skill、I-comm、I-role、I-timing 尚未形成四场景统一 schema。每个干预必须声明决策窗口、可变字段、冻结字段、预期证据、非法分支和因果状态。干预应位于相关决策之前；失败已经不可逆后再注入，不能作为同等证据。

I-skill 只用于技能后端异常排查或校准。默认移动、导航、抓取控制器保持理想且固定，不能把低层控制差异混入 TeamWeaver/EMOS 的高层协作排名。

### P0：参考解和最优性边界没有逐题结构化

作者 witness 只能证明声明条件下存在可行计划。每题需要声明是 `feasible_reference_only`、有证明的上下界，还是已有最优性证明，并绑定策略版本、参考指标和 `normative=false`。A* 可达、作者计划或业务完成都不能直接叫 oracle。

### P1：H×C 因子覆盖不足

主轨目前完整展开的只有通信质量和团队连续性。任务结构、角色结构、决策结构、团队规模、异构程度、伙伴迁移、并发/争用、deadline、同步屏障和多瓶颈交互没有同等的生成与审核矩阵。未执行的交互项不能写成已验证结论。

### P1：协作成本指标没有绑定事件语义

需要定义等待、冲突、责任真空、角色错配、恢复延迟、重复工作和通信开销的起止事件、计数单位、缺失值规则及权威 evidence source。算法没有采用作者 witness 的分配，不能单独判成角色错配。

### P1：校准题和反例不足

需要无故障、已知故障、无效干预、单因子对照、多重瓶颈、联合干预可挽救、多种机制均可挽救和 `non-identifiable` 样例。归因审核必须允许 `unknown`，不能强迫每次干预产生唯一根因。

### P1：任务去重、机制注册和四场景平衡不足

任务应注册 `task_family_id`、`mechanism_id`、`scene_key`、`template_version`、`factor_signature`、`intervention_signature`、`witness_signature`、`calibration_role`。审核器需检测 ID 重复、结构重复、同一题换描述、场景机制覆盖不平衡和隐藏 witness 复用导致的伪多样性。

### 运行平台边界仍需交接

这不是任务生成缺口，但接手者必须看见：`GoalAlgorithmHost` 和 `ReliableSkillReferenceHost` 已支持由运行器注入同一 GoalSession 账本，并有组合回归；生产路径仍可能使用进程内 adapter/callback，`episode_id` 尚未贯穿 session、skill receipt 和 business evidence。外部调度的 claim/lock/attempt/checkpoint、通信生命周期真实执行、Isaac/GPU 和统计推断由下游工程师负责，不能在 benchmark 任务审核中假装已经完成。

## 推荐的任务数量策略

不要复制 `GOAL-01` 36 次。先建立机制模板，再展开条件：

| 阶段 | 目标 |
|---|---|
| M0 | 4 场景 × 4 个核心机制 = 16 个 canonical templates：decomposition、role allocation、communication adaptation、role churn/recovery |
| M1 | 每场景扩展到 8 个机制模板 = 32 个 canonical templates：增加 timing/synchronization、resource contention、recovery/replanning、multi-bottleneck calibration |
| 每个模板 | baseline、单因子 treatment、counterfactual/control、calibration variant、非法干预 variant |

seed、通信等级和成员事件等级是同一机制模板的条件，不应单独冒充独立科学任务。goal 主轨冻结初始世界、业务目标、资源约束、能力池和外生事件；被测模型自行分解、分配、规划和重规划。只有 fixed-DAG 诊断轨道才冻结 DAG，作者 witness 必须隐藏且不能成为唯一答案。

## TODO 与验收标准

| 优先级 | TODO | 主要产物 | 验收标准 |
|---|---|---|---|
| P0 | 冻结研究口径 | `COLLABORATION_MEASUREMENT_SPEC.zh-CN.md`、`MEASUREMENT_TARGET_SCHEMA.md` | 词表、允许/禁止结论、四场景目标、unknown/non-identifiable 规则明确 |
| P0 | 机器可读测量契约 | `measurement_contract.py`，扩展 goal contract | 非法机制、指标、参考解和干预字段 fail-closed；旧题显式 unclassified |
| P0 | 机制模板与注册表 | `goal_task_templates.py`、`goal_task_registry.py`、`goal_task_generator.py` | M0 至少 16 个 canonical templates；四场景各 4 个；生成入口只读注册表 |
| P0 | 必要性证书 | `goal_necessity.py` | 能记录搜索空间和证据等级；支持 validated/disproven/unknown；不冒充物理必要性 |
| P0 | 四类干预契约 | `interventions.py`、`factor_experiment.py` | I-skill/I-comm/I-role/I-timing 统一窗口、冻结字段、非法分支和 evidence |
| P0 | 逐题参考解元数据 | `reference_solution.py` | feasible、bound、optimality 三种状态和 normative 边界可验证 |
| P1 | H×C 覆盖审核 | `task_coverage_matrix.json`、`goal_suite.py`、`diagnostic_audit.py` | 四场景机制/因子覆盖矩阵、交互项状态、去重报告可复现 |
| P1 | 校准题生成器 | `calibration_tasks.py` | 已知故障、无故障、无效干预、多重瓶颈和 non-identifiable 全部有样例 |
| P1 | 指标语义绑定 | `collaboration_metrics.py` | 每个指标有事件起止、单位、证据来源、缺失值和不可归因状态 |
| P1 | 统一四场景入口与数量报告 | `goal_scene_registry.py`、`goal_driven_tasks.py`、`goal_suite.py`、`release_manifest.py`、旧文档 | 默认生成、suite、handoff preflight、release manifest 都生成同一四场景集合；报告同时给出 `canonical_template_count`、`condition_count`、`factor_pair_count` 和固定 DAG 数量 |
| P1 | 运行账本交接 | `goal_session.py`、algorithm host/receipt 文档 | episode_id、session、receipt、business evidence 同一账本；外部调度器有明确接口 |
| P2 | 交接文档和示例 | `ALGORITHM_HANDOFF.zh-CN.md`、每机制最小 JSON 示例 | 新开发者无需猜测题目能测什么、能改什么和凭什么判定 |

## 子任务分配方案

按横向能力分工，避免四个场景复制四份不一致实现。下列是交接计划；除当前只读复核外，尚未启动这些实现。

| 角色 | 独立职责 | 文件范围 | 依赖 | 完成证据 |
|---|---|---|---|---|
| 主代理 / 架构 owner | 冻结研究口径、字段、轨道边界和 release gate | 研究规格、`goal_driven_contract.py` 的接口边界 | 无 | 规格评审通过，四场景字段表一致 |
| `team_researcher` | 协作机制 taxonomy、H×C 覆盖矩阵、任务去重规则 | `task_coverage_matrix.json`、研究说明 | 研究口径 | 机器可读矩阵和缺口报告 |
| `team_engineer` | 测量目标 schema、验证器、旧题迁移 | `measurement_contract.py`、goal contract | 研究规格 | 正/负契约测试，旧题为 unclassified |
| `team_engineer`（第二阶段） | 模板、注册表、生成器和四场景默认入口 | `goal_task_templates.py`、`goal_task_registry.py`、`goal_task_generator.py`、suite | measurement schema | M0 16 模板、四场景同入口、去重报告 |
| `team_reviewer` | 最小团队/异构必要性证书独立审查 | `goal_necessity.py`、证书测试 | 模板和能力词表 | 三态证据、搜索范围和反例测试 |
| `team_engineer` | I-skill/I-comm/I-role/I-timing 干预契约 | `interventions.py`、`factor_experiment.py` | measurement schema、决策窗口 | 合法/非法窗口测试，冻结字段哈希稳定 |
| `team_process_reviewer` | 参考解、校准题和交接验收审核 | `reference_solution.py`、`calibration_tasks.py`、handoff checklist | 模板、干预契约 | 可行/最优性边界和 non-identifiable 样例通过 |
| `team_reviewer` | 指标语义、覆盖、sidecar 和 release gate 复核 | `collaboration_metrics.py`、`goal_suite.py`、`diagnostic_audit.py` | 前述产物 | 逐题指标绑定、四场景平衡和完整审查报告 |
| 下游运行工程师 | 统一 GoalSession、外部进程、批量幂等、通信生命周期、Isaac/GPU | runtime/调度器 | benchmark schema 冻结 | 真实 execution evidence；不得改写题目语义 |
| 下游统计工程师 | 预注册 split、任务/布局/机制层级统计和泛化 | 统计实验 registry | 运行 evidence | 不把 seed、branch 或 factor level 当独立任务 |

## 依赖顺序和交接门禁

```text
研究规格
  -> measurement schema
  -> 模板/注册表/生成器
  -> 必要性、干预、参考解、校准契约
  -> 四场景覆盖/去重审核
  -> 下游执行与账本
  -> 统计分析
```

在 P0 产物完成前，不应继续把同一业务骨架批量复制成更多题目；否则只会得到更多有 witness 的业务题，仍无法回答“测了什么、为什么失败、能否归因”。发布时必须同时展示：任务数量、canonical template 数、每场景机制覆盖、证书状态、reference status、calibration profile 和未验证项。

## 当前审计限制

用户提供的 `/home/pupil/eai-simulator/sess_0da75db0-0884-4c32-81b0-2075484e1e52.zcode-session` 在当前工作树中不存在，且 2026-09-20 JSONL 没有该 session ID；因此本文件没有把该路径当作已读取的修复证据。结论基于当前源码、现有 benchmark 复盘文档、可恢复的 ZCode handoff 和本次可复现的 36 包生成检查。之前的 Go2/B2 与 City 运行修复若要成为 benchmark 发布证据，仍须由对应运行工程师提供带输入指纹的正式 artifact。

## 本轮并行开发状态

已完成第一轮 P0 基础产物：

- `measurement_contract.py`：严格校验 measurement target、reference solution、四类 intervention 和 calibration profile；支持四场景、确定性规范化和 `unknown`/`non_identifiable`/`external_pending`。
- `test_measurement_contract.py`：23 项离线测试通过。
- `task_coverage_matrix.json` 与 `TASK_COVERAGE_MATRIX.zh-CN.md`：登记 8 个机制族、四场景 M0 目标和 legacy 兼容矩阵；当前 M0 authoring registry 已覆盖 16 个模板。
- `task_coverage.py`：提供不依赖 Isaac 的矩阵 loader/validator，并可独立验证四场景 × 四机制的 M0 registry 和 36/28 兼容计数口径。
- `mechanism_templates.py` 与 `generate_canonical_goal_task_packages()`：为 M0 模板提供独立 measurement contract、三态必要性证书和机制证据钩子。旧题显式保持 `measurement_status=unclassified`，M0 题包为 `classified`，但 execution/release gate 仍 pending/false。

## 2026-09-21 增量验收与反思

本轮只收敛 authoring 入口和来源绑定，没有把旧题提升为已完成的机制测量：

- `generate_goal_task_packages()` 及其 CLI `--scene` 选项改为读取 `goal_scene_registry`，不再维护第二份场景列表。
- `goal_suite` 的 source binding 纳入 `measurement_contract.py`、`task_coverage.py` 和
  `task_coverage_matrix.json`；覆盖契约变化会使冻结 suite 重新生成，而不是继续沿用旧审核结果。
- suite 生成/读回会交叉检查覆盖矩阵与四场景默认 36 个 package、28 个 factor pair。
- 校准 profile 现在 fail-closed 校验全部 role/condition 配对，并要求
  `non_identifiable` 同步声明对应因果状态；不匹配的校准题不能进入 hidden evaluator。
- 新增 `write_goal_mechanism_suite()` / `goal_suite mechanism-audit|mechanism-verify`，独立发布和读回 16 个 M0 canonical templates；每个模板计划含机制证据钩子，不能只靠 metadata 改名制造覆盖。
- 离线回归：`test_benchmark_goal_suite.py`、`test_benchmark_measurement_contract.py`、
  `test_benchmark_task_coverage.py`、`test_benchmark_goal_driven.py`、
  `test_benchmark_mechanism_templates.py` 共 **121 passed**；M0 专项 20 项测试通过。

同一份源代码和固定 `EAI_NVD_ASSET_ROOT=/home/pupil/nvd_assets_sanitized_xr` 下，使用
`demo.benchmark.goal_physical_probe`、CPU PhysX、headless、8 步完成四场景原生初始化与读回：

| 场景 | 结果 | 运行证据目录 |
|---|---|---|
| Factory | `returncode=0`, `diagnostic_completed=true` | `tmp/headless_measurement_20260921_contract_final/factory` |
| Warehouse NVD | `returncode=0`, `diagnostic_completed=true` | `tmp/headless_measurement_20260921_contract_final/warehouse_nvd` |
| Modern House NVD | `returncode=0`, `diagnostic_completed=true` | `tmp/headless_measurement_20260921_contract_final/modern_house_nvd` |
| City | `returncode=0`, `diagnostic_completed=true` | `tmp/headless_measurement_20260921_contract_final/city` |

第一次 Warehouse 执行因准备阶段和执行阶段的 `EAI_NVD_ASSET_ROOT` 不一致，被部署来源哈希
门禁拒绝；重新固定环境后通过。这说明交接命令必须同时冻结资产根路径和源码输入。上述探针
只证明原生配置、启动和读回，不证明业务 outcome、协作机制因果归因或 `scoreable`；四场景
仍保持 `execution_validated=false`、`scoreable=false`。

下一阶段仍需为 16 个 M0 模板补齐 I-skill/I-comm/I-role/I-timing 的正式 paired replay、
参考解状态升级、校准/反例执行证据和协作成本事件绑定；`GoalSession` 统一账本、进程调度、
批量幂等及统计泛化继续由下游负责。当前 16 个模板的团队/异构必要性证书全部是
`unknown`，不能据此声称物理必要性。
