# HeteroCoord 后续任务规划与阶段性收尾

日期：2026-10-22  
工程目录：`/home/pupil/HeteroCoord`  
论文目录：`/home/pupil/CVPR2026_Template`

## 0. 执行结论

后续工作以 Overleaf 提交
`72b6a3b975ae83743dc1d447b99edd728cb1dcdc`
为唯一论文基线。该提交补入了此前遗漏的
`sec/supp_system_state.tex`，因此它比旧进度文档中使用的 `1f32949` 更完整。旧的
`CURRENT_PAPER_ALIGNMENT.zh-CN.md` 只保留作历史记录，不能再用作当前协议依据。

工程目前已经完成论文身份绑定、D1--D6 统计接口、若干 oracle/checker 合同、branch
恢复合同，以及 CPU `protocol_synthetic` history continuation 的诊断闭环。最近的
history-runtime 阶段可收尾为“合同和诊断 seam 通过”；它没有建立真实 physical
checkpoint、Isaac/PhysX restore、GPU shared-session 或论文分数资格。

正式执行顺序必须是：

```text
论文契约冻结
  -> 任务/机器人/资源/技能细粒度合同
  -> canonical oracle 与物理技能验证
  -> evaluator-owned source producer
  -> isolated fresh continuation
  -> native fresh shared-slot
  -> GPU 三路 qualification
  -> isolated closed-loop audit
  -> catalog / calibration / baseline
  -> paired pilot
  -> fault / sham / intervention / held-out recovery
  -> release gate、D1--D6 结果与论文回填
```

在 `source producer`、真实 checkpoint/history、shared-session qualification 和 audit
完成前，不运行正式 GPU 大矩阵，不把 CPU/mock/vectorized 结果写入论文结果表。

## 1. 论文科学逻辑与工程映射

论文要回答三个问题：团队是否能够协调、协调何时失效、失效由哪种协调机制或部署因素
造成。评价对象不是一个单一的任务成功率，而是带有关系结构、动态事件和证据状态的
base instance。

| 科学对象 | 论文约束 | 工程承载 | 当前判断 |
| --- | --- | --- | --- |
| 运行域 | production、service、living/public space | `benchmark/task_catalog.json`、scene/asset catalog | 高层锚点已列出，细粒度认证未完成 |
| 协调原语 | P1 parallel、P2 precedence/handoff、P3 coupled service/action、P4 exclusion；P3-J/P3-S 分型 | obligation graph、relation ledger、oracle | 合同和统计入口存在，真实物理 witness 未闭合 |
| 任务身份 | 完整 obligation graph、relation signature、P3 subtype、capability correspondence、milestone semantics | task package、robot resume、resource contract | 30 anchors 仍为 `planned` / `not_yet_certified` |
| 难度 | 八维 difficulty vector 和 weakest-link tier 在运行前冻结 | catalog difficulty fields、coverage manifest | 字段存在；尚未完成独立 feasibility/skill validation |
| 执行时间 | simulator tick 固定为 0.02 s | runner manifest、ledger tick | 合同已固定，真实运行 receipt 未形成 |
| branch 起点 | 同一 evaluator-owned physical checkpoint 与 permitted history | `paper_state_v3.py`、checkpoint store、history runtime | CPU contract seam 完成；source producer/physical restore 未接通 |
| fresh initialization | condition 分配在初始化预算结束后；无 condition 泄漏；每 branch 私有 context/memory/tool/RNG | `paper_state_v3.py`、`history_runtime.py` | synthetic CPU 已验证；真实 submitted system 仍未资格化 |
| shared session | slot 私有物理/机器人/controller/skill/lock/queue/message/brain/clock 状态；slot permutation 与 batch effects 可审计 | `vectorized.py`、`vectorized_executor.py`、`eai_vectorized_backend.py` | legacy bridge/诊断接口存在；native fresh schema 和 GPU qualification 未完成 |
| 六个维度 | D1--D6 分别计算、分别支持集和不确定性；primary label 只展示，不作 relation estimand 分层 | `protocol_v3.py`、`scorebook_v3.py`、`statistics_v3.py` | 统计接口已接入；正式数据和 support manifest 未完成 |
| 失败归因 | method failure 保留；infrastructure failure 才按规则重跑；unknown/incomplete/NA/pair-invalid 不变成确定失败 | release gate、repair、recovery bridge | 状态机/发布门有实现，正式 run driver 未闭合 |
| 部署校验 | shared crash、physics warning、slot interference 使相关 active block 失效并按原 identity isolated rerun | watchdog、executor、audit | 规则和部分回归已完成，生产接线和真实 receipt 未完成 |

## 2. 当前状态盘点

### 已完成并可作为后续输入

- 代码中的 `PAPER_COMMIT` / `PAPER_SHA` 已绑定 `72b6a3b975ae83743dc1d447b99edd728cb1dcdc`，旧论文身份会被拒绝。
- D1--D6 的统计和发布接口已能表达 relation-specific support、P3 subtype、unknown/incomplete、D2 retention interval、D4 timeline/value、D5/D6 资源诊断。
- `PhysicalCheckpoint`、`PermittedHistory`、`FreshInitializer`、`BranchPaperIdentity` 建立了 evaluator-owned state 与 submitted-system fresh state 的边界。
- CPU history runtime 已覆盖 fresh context、初始化屏障、condition 延迟分配、进程连续性、malformed initializer、terminal schema、timeout kill/reap、cleanup failure、预存 ledger/seal/artifact 拒绝及符号链接路径拒绝。
- canonical bounded deployment oracle/checker、branch executor、checkpoint store、recovery bridge 和 shared-session/vectorized 诊断入口已存在。
- 最近 history-runtime 阶段：history tests 71 passed；四文件回归 169 passed；`py_compile` 与 diff-check 通过。该证据只支持 CPU synthetic seam。

### 明确未完成

- evaluator-owned source-run producer 及 source checkpoint/history 的真实生产接线。
- 真实物理 checkpoint restore/hold、isolated fresh continuation、native fresh shared-slot。
- isolated/shared 三路状态恢复一致性、GPU runner identity manifest、Isaac/driver/container/device/physics/controller/source digest。
- isolated open-loop audit、shared crash/physics warning 的生产归因和 `eta_exec` widening。
- 30 个 scene anchor 的 obligation/resource/robot pool/skill/oracle certification。
- fixed catalog、baseline、calibration、paired pilot，以及 P4/P5 fault/sham/intervention/held-out recovery batches。
- formal release 和论文结果表。

### Formal gates（当前全部保持 false）

```text
physical_checkpoint_qualified = false
history_initialization_qualified = false
branch_isolation_qualified = false
closed_loop_audit_qualified = false
statistical_widening_qualified = false
paper_score_eligible = false
rankable = false
```

## 3. 分阶段任务计划

下面的 owner 是责任角色，不代表必须立即创建新代理。每阶段都要留下可重算的 manifest、
receipt 和测试命令；没有这些证据只能保持 `pending` 或 `diagnostic`。

## 3A. 本轮 Goal 阶段性收尾（2026-10-22）

本轮已完成 ZCode 连接修复与 agent-team 协作验收。ZCode 使用 GLM-5.3/max、build
模式，插件诊断为空；documents/pdf/presentations/spreadsheets/image-search/
agentmemory MCP 均已连接。ZCode 执行的只读回归为 89 + 71 + 61 = 221 个测试，
并通过 `compileall`；执行前后工作树均为 572 条既有未提交变更。该证据仅覆盖合同、
synthetic/runtime 和诊断 seam，不改变任何 physical/GPU qualification gate。

主代理新增 evaluator-owned source-run 合同层：
`tools/benchmark_branch/source_run.py` 及其 5 个 focused tests。它只接受
evaluator factory 产生的 checkpoint/state/observation/history，拒绝 submitted system、
condition、outcome 和 private state，并明确标记为 isolated-process diagnostic receipt。
主代理回归（source-run + paper-state + P0/P2 + history runtime）为 **156 passed**。

6 个 pilot candidate 的只读一致性检查也已完成：anchor_id 唯一、全部存在于 30-anchor
catalog，domain/relation/P3 subtype 一致，coverage 声明与实际集合一致，论文 SHA 与
manifest 一致；30 个 anchor 仍全部为 `planned/not_yet_certified`，formal gates 全部
为 false。原计划由 ZCode 写入 audit artifact，但其审计脚本触发权限请求并在会话超时后
被取消；因此该结论由主代理只读复核，不能计为 ZCode 产出的独立 artifact。

阶段性结论：S0 已冻结，S1 进入“候选字段扩展”阶段，S2 已有 diagnostic contract
producer 但尚未接入真实 evaluator/Isaac source。下一检查点是为 6 个候选补齐 capability
evidence、role/obligation contract、milestone predicates、formal temporal/resource
constraints、skill pre/postconditions、physical-feasibility witness 与 oracle 状态，
然后再接 isolated source bundle；在此之前不得启动 GPU 大矩阵或声称论文分数资格。

本轮新增 `tools/benchmark_branch/pilot_contract.py` 及 3 个 focused tests，并生成
`artifacts/catalog/contracts/72b6a3b975ae83743dc1d447b99edd728cb1dcdc/pilot_contracts.json`。
合同集显式包含 obligation、milestone、capability、resource、skill、physical witness、
oracle 与 certification 块；所有证据依赖字段仍为 `pending`，6/6 保持
`candidate_contract_only`。主代理回归为 159 passed；ZCode 独立复跑 focused tests 为
3 passed，并通过项目 validator 核验 artifact，确认所有 certification flags 仍为 false。

随后新增 `capability_evidence.py`，把 robot resume 的能力信息投影为
`evidence_inventory_only`。主代理修正了一个过时测试假设：当前 resume 文件实际有 28
条记录，且其 coverage 字段同样声明 28（此前文档曾提到 30 个 expanded resume，不能
继续硬编码）。修正后本地 focused tests 为 10 passed；ZCode 首次运行捕获该漂移（1 failed），
随后只读复核 artifact，确认 6 个候选各有 28 条 inventory rows、共 392 条 evidence
entry，`runtime_qualified != false` 为 0。该 inventory 仍不提供 robot-instance binding
或 physical qualification。

本轮又将 temporal/resource 字符串投影为 19 个形式化谓词草案，写入每个
`resource_contract.formal_predicates`。这些条目统一使用 `UNBOUND(...)` 表达式和
`draft_pending_validation` 状态；在 oracle 作者绑定具体语义、单位、时间窗和 ledger
字段前都不可执行、不可认证。ZCode 复核 `test_pilot_contract.py` 为 3 passed，并逐项确认
6 个合同、19 个谓词和所有 certification flags。

谓词草案现已进一步显式声明 `input_fields`、`units`、`time_window` 和
`binding_status`。这些字段让 oracle 绑定有稳定的输入接口：precedence 使用
`milestone_ledger`，mutex 使用 `resource_ledger`，continuity 使用 observation 与
milestone ledger；时间窗和单位仍由后续独立验证填实。ZCode 逐项核验 19/19 字段完整，
且 6 个合同的 certification flags 仍全部为 false。

本轮为每个 predicate 增加 `binding_spec` 与 `unbound_parameters`：明确所需数量、事件对、
以及尚未绑定的阈值和身份映射。19/19 谓词均具备这些字段，仍保持
`draft_pending_validation`；这为后续 oracle 实现提供接口，但不构成语义验证或物理认证。

2026-10-23 又完成了一项 S1 结构修正：ZCode 只读审计发现 19 个谓词中有 4 个
`event_pair_candidate.candidate_labels=[]`，其中 3 个是 resource predicate，另 1 个是
`requests_cannot_overlap_counter` 这一 temporal mutex。主代理没有伪造 milestone 事件对，
而是在 `pilot_contract.py` 增加独立的 `binding_candidates` seam：resource 使用
`resource_ledger`（`resource_id_candidate`、`capacity_constraint_candidate` 留空），
temporal mutex 使用 `interval_ledger`（`interval_source` 留空）；二者均为
`draft_pending_validation` 且要求 oracle review。artifact 已重新生成，focused regression
为 **11 passed**，ZCode 按同一只读计划复核通过；6 个合同的 certification flags 仍全部为
false。该修正明确了资源容量/区间约束不能由 milestone token overlap 代替，但仍不构成
oracle 绑定或物理资格化。

同日又补齐了 S1 的三类合同接缝：`role_obligation_binding` 保留候选角色-义务对为空，
`skill_contract` 声明所需能力但不绑定具体 skill，`oracle_contract` 统一为
`heterocoord-oracle-input.v1` 并要求 evaluator receipt。三者均保持
`draft_pending_validation`；主代理与 ZCode focused 回归均为 **11 passed**，6 个合同的
认证标志仍为 false。下一步必须由 case author/evaluator 提供真实映射和可重算回执，不能
从名称或能力字段自动推断认证。

S2 进一步加入 source bundle 可重算检查：同一 `source_run_id` 必须重现相同的
checkpoint/history/initializer/observation digest 四元组；任何摘要漂移都必须拒绝。当前
测试覆盖稳定重算与漂移拒绝，仍只证明 evaluator-factory diagnostic contract，不能替代
真实物理 source adapter 或 qualification receipt。

### 本轮增量收尾：S3 cleanup receipt identity audit（2026-10-23）

`ProcessCleanupReceipt` 已完成从生命周期封条到重试/重放身份封条的闭环：receipt 现在绑定
`run_id`、`source_run_id`、`checkpoint_digest`，恢复时验证字段和 seal，使用时通过
`assert_identity` 做三元组比较，失配立即 fail-closed。ZCode 独立验收为 **74 passed**，目标文件
`compileall` 为 `EXIT=0`，没有资格字段或 gate 变化。

该项的后续工程顺序固定为：

1. 用 sealed source bundle + initialization outcome + cleanup receipt 建立 retry/replay audit
   manifest，验证同一 run identity 的失败重试可重放、不同 source/checkpoint identity 必须拒绝；
2. 在不改变 receipt schema 边界的前提下，接入真实 collector-owned initialization/cleanup
   receipt；
3. 只有真实 isolated process、物理 hold/readback 和独立 branch evidence 均通过后，才评估
   `history_initialization_qualified` 或 `branch_isolation_qualified`；
4. 之后才进入 native shared-slot、GPU qualification、closed-loop audit、calibration/baseline
   和 paired pilot。

当前仍禁止把 CPU/mock/vectorized 证据写入论文结果表，也禁止提升
`physical_checkpoint_qualified`、`history_initialization_qualified`、`branch_isolation_qualified`、
`closed_loop_audit_qualified`、`statistical_widening_qualified`、`paper_score_eligible` 或
`rankable`。

### S3 retry/replay identity manifest（已完成）

`ReplayIdentityManifest` 已将四个 context 的 sealed initialization outcome 与 cleanup receipt
按 context 精确配对，并以 manifest-level digest 封装。它可用于后续失败重试和重放时的 identity
审计，但不承载任何资格信号。主代理与 ZCode 均通过 75-test history suite 和 compileall。

下一步必须把该 manifest 作为真实 collector 接入的只读输入：collector 重新生成 receipt 后，
先验证 source/run/checkpoint 三元组，再验证 context 集合和 outcome/cleanup digest；任何身份
漂移、缺失或新增 context 都拒绝重放。完成这一步前，仍不得将 diagnostic manifest 转换为
physical/history/branch qualification。

`assert_matches_ledger_rows()` 已先在 synthetic ledger 上实现这一审计路径：它允许 ledger
顺序变化，但拒绝 context 集合变化和 sealed digest 漂移。下一阶段可直接将真实 collector
生成的两类 receipt ledger 接到同一入口，保持 schema 和 fail-closed 语义不变。

runtime 现在会在 isolated cleanup 完成后自动落账 `replay_identity_manifest` event；因此真实
collector 接入点已从“手动调用审计工具”推进到“运行生命周期自动产出审计索引”。仍需替换
synthetic source/cleanup producer 为真实 evaluator-owned collector，并保留 fake backend 的
能力缺失边界。

### S0：论文版本与科学契约冻结

**目标**：让所有实现、进度文档和实验 manifest 使用同一论文身份和同一条款解释。

**任务**

1. 将 `CURRENT_PAPER_ALIGNMENT.zh-CN.md`、旧 P0--P7/G0--G6 说明中的当前基线统一到
   `72b6a3b975ae83743dc1d447b99edd728cb1dcdc`，保留旧 SHA 的历史说明但不再作为输入。
2. 从 `sec/5_evaluation_protocol.tex`、`sec/supp_system_state.tex`、`sec/X_suppl.tex`、
   `sec/supp_design_details.tex` 生成一份 machine-readable protocol manifest：tick、
   relation、P3 subtype、checkpoint、history、condition assignment、unknown、rerun、
   shared-session 和 D1--D6 规则均必须有版本字段。
3. 将本规划作为 progress README 的首要入口，建立“论文条款 -> 代码 -> receipt -> gate”的追踪表。

**Owner**：protocol/evaluator owner。  
**输出**：`artifacts/protocol/<paper_sha>/protocol_manifest.json`、对齐文档更新。  
**验收**：旧 SHA 被拒绝；manifest digest 能进入 result identity；代码、文档、测试中的当前 SHA 一致。

### S1：细粒度 task package、robot pool、resource 与 skill contract

**目标**：把 30 个高层 anchors 变成可执行、可验证、可审计的 canonical case。

**任务**

1. 每个 anchor 补齐 obligation graph、P1/P2/P3/P4 relation signature、P3-J/P3-S、
   role/capability correspondence、milestone、tick horizon、resource capacity/lock、
   hazard、communication demand、difficulty vector 和 weakest-link tier。
2. 为每条 robot--obligation edge 注册 robot resume、实际 runtime/controller/camera/skill
   状态和 capability evidence；不能从机器人名称推断能力。
3. 注册 spawn state、scene asset digest、skill pre/postcondition、failure/timeout rule、
   physical witness 和 oracle input；区分 `not_yet_certified`、`certified`、`invalid`。
4. 从 30 个 anchors 选少量跨 domain、跨 relation、含 P3/P4 的 pilot anchors，先做最小
   可认证子目录，禁止先把全目录标成可运行。

**Owner**：task catalog + robot/skill validation owner。  
**输出**：`benchmark/task_catalog.json`、`benchmark/robot_resume/`、skill validation receipts、
   `artifacts/catalog/<catalog_digest>/`。  
**验收**：每个 pilot case 通过独立 infrastructure、feasibility、skill validation；
   未验证 oracle 不得证明 infeasible；catalog 变更产生新 digest。

### S2：evaluator-owned source-run producer

**目标**：实际产生论文规定的 evaluator-owned physical checkpoint 和 permitted history。

**任务**

1. 在隔离进程运行 source system，保存 evaluator-owned world、robot、controller、skill、
   transport、lock/queue 和 evaluator state；不保存 submitted algorithm/model/private state。
2. 在注册事件/first-passage tick 生成 checkpoint，写入物理 readback、remaining feasibility、
   source ledger、event keys 和 permitted history digest。
3. 由 collector 生成 source-run receipt、checkpoint manifest、history manifest 和
   `BranchPaperIdentity`，拒绝 caller 自报或 outcome-dependent checkpoint。
4. 将 source bundle 接入当前 isolated history runtime，先使用 synthetic backend，再接真实
   EAI/Isaac backend；两者 receipt schema 相同但 qualification 标志不同。

**Owner**：source-run/evaluator owner。  
**输出**：`tools/benchmark_branch/source_run.py`（或等价模块）、source bundle schema、
   `artifacts/source_runs/<run_id>/`。  
**验收**：同一 source run 可重算 checkpoint/history digest；替换 caller-provided state、
   condition、context 或 RNG 后无法伪造有效 receipt；source run 与 continuation 身份分离。

### S3：isolated fresh continuation

**目标**：在真实 isolated process 中完成“同一物理起点 + permitted history + fresh submitted
system initialization”的第一条正式可审计路径。

**任务**

1. continuation 启动前只接收 evaluator-owned bundle、condition-blind initialization request、
   current observation 和 private initialization stream。
2. 初始化预算结束后才随机分配 condition；初始化期间禁止 condition label、未来 outcome、
   其他 branch context 和共享 writable state 泄漏。
3. 保存独立 memory/retrieval/tool/model-service context、host-time budget、terminal status、
   malformed/timeout/method failure，并在关闭前完成 child reap 和 cleanup receipt。
4. 对 fresh continuation 做 discrete observation、milestone、ledger 和 identity 的可重放检查。

**Owner**：isolated runtime owner。  
**输出**：isolated continuation runner、branch ledger、private namespace manifest、failure receipt。  
**验收**：`history_initialization_qualified` 仅在真实初始化和 collector-owned receipt 均通过后置 true；
   method failure 保留，不被转换为 infrastructure failure。

### S4：native fresh shared-slot

**目标**：增加论文所需的 fresh shared-slot 执行模型，同时保留 legacy vectorized bind/restore 路径。

**任务**

1. 新增独立 fresh shared-slot schema：slot、branch identity、checkpoint/history、private
   context、private RNG、brain hold、generation、batch 和 close state 必须显式登记。
2. 不修改旧的 `VectorizedSession.bind/restore` 合同；在 `eai_vectorized_backend.py` 中增加
   native fresh bridge，明确 legacy/diagnostic/formal 三种 execution mode。
3. 接入 slot permutation、batch composition、slot reuse、neighbour load、rollback、brain hold、
   lock/queue/message/clock 私有性检查；同一 block 的所有 uncached branch 进入同一 batch。
4. 实现 shared crash/physics warning 的 active/in-flight block 失效、queued/not-started 保持
   queued/not-run、原 identity/random key isolated rerun。

**Owner**：shared-session runtime owner。  
**输出**：fresh shared-slot schema、native backend、executor/watchdog receipt、slot ledger。  
**验收**：CPU synthetic 只作为状态机回归；不得把它当作 GPU qualification；legacy tests 全部保持通过。

### S5：GPU 三路 qualification

**目标**：确认同一 registered block 的 uninterrupted、isolated restore、shared restore 在真实
   Isaac/PhysX/GPU 上具有论文允许的一致性。

**任务**

1. runner 由 collector 读取实际 `cuda:*` device、GPU UUID、Isaac/driver/container/source/
   asset/controller/physics digests、slot map、batch range、tolerances、memory/lease 和 clean shutdown。
2. 对同一 scene、source bundle、repeat key 运行三路路径；覆盖 slot permutation、batch size、
   neighbour condition/load、slot reuse、brain hold、concurrent session。
3. 比较 evaluator-owned physical state、branch id/random key、logical clock、queue/lock、skill
   outcome、milestone order 和 scored ledger；连续状态只能在注册的 `tau_replay`/`eta_exec` 内放宽。
4. 任一路径不一致、slot 非私有、physics buffer warning 或 shared crash 都使 shared config 保持
   不合格，并生成 isolated rerun 清单。

**Owner**：GPU/Isaac runner owner。  
**输出**：`artifacts/qualification/<config_digest>/`、GPU identity manifest、三路差异报告。  
**验收**：`physical_checkpoint_qualified`、`branch_isolation_qualified` 只能由 collector-owned
   GPU receipt 驱动；单场景成功或 requested device 字段不足以通过。

### S6：isolated closed-loop audit 与 statistical widening

**目标**：验证 shared-session 中 brain 获得的输入与 isolated execution 一致，并把抽样不一致纳入
   统计不确定性。

**任务**

1. 按注册 hash 独立抽取 shared blocks，在 isolated process 重放 logged brain decisions。
2. 比较离散 observation、连续 observation（注册 tolerance 内）、skill outcome、milestone 和
   scored ledger；记录 `n_dis`、`n_aud`、`N_blk`、audit seed、coverage 和 affected blocks。
3. 计算论文规定的 hypergeometric upper bound，并将最坏端点 widening 纳入 D1--D6；超过
   `eta_exec` 时撤销该配置资格并 rerun affected blocks。
4. 审计 active shared crash、physics warning、method failure、unknown 和 pair-invalid 的归因
   是否保持原规则。

**Owner**：independent audit/statistics owner。  
**输出**：audit manifest、discordance report、widening receipt、isolated rerun list。  
**验收**：`closed_loop_audit_qualified` 与 `statistical_widening_qualified` 均只能由可重算
   audit receipt 驱动；无 audit 的 shared 结果不得进入 paired evidence。

### S7：canonical catalog、calibration 与 baseline

**目标**：建立固定 roster、固定系统、固定资源和固定 oracle，形成正式比较的最小数据集。

**任务**

1. 锁定已通过 S1/S2/S5/S6 的 pilot catalog，记录 release digest 和覆盖矩阵。
2. 完成 nominal baseline、通信容量 calibration、execution-time/throughput calibration、
   resource/efficiency calibration；禁止用结果反推 difficulty tier。
3. 确认每个 D1--D6 的 support、repeat、condition、uncertainty 和 validity status；primary label
   只用于展示，不重新作为 relation estimand 的分层变量。
4. 生成第一版 release bundle，但在 formal gates 全部通过前保持不可排名。

**Owner**：catalog/statistics/release owner。  
**输出**：baseline bundle、calibration manifest、support matrix、release candidate。  
**验收**：所有 case 都能解析到相同 paper/catalog/source/config digests；unknown/incomplete/NA/
   pair-invalid 保留原状态；无完整 support 时返回范围或 NA。

### S8：正式 paired pilot

**目标**：在最小认证目录上验证完整 fact/reference/sham/intervention block 的端到端闭环。

**任务**

1. 每个 branch 从同一 checkpoint 恢复，使用相同 registered exogenous-event keys 和 repeat key；
   policy randomness 保持独立。
2. 运行一个完整 `(instance, system, repeat)` block，记录 source、continuation、shared-session、
   isolated execution 身份，不把 source run 替换为 outcome-dependent branch。
3. 对 paired support、condition assignment、milestone、unknown、pair-invalid、sham mismatch
   和 artifact digest 做 release 前检查。

**Owner**：experiment coordinator + evaluator owner。  
**输出**：pilot result bundle、pair coverage report、failure adjudication report。  
**验收**：pilot 通过后才允许扩大样本；任何 pair-invalid/unknown 都保留原始记录并按协议返回
   `NA` 或区间，不生成排名。

### S9：fault、sham、intervention 与 held-out recovery

**目标**：回答论文关于失效机制、选择性下降和修复效果的科学问题。

**任务**

1. 在固定 system、task、team size、observation 和 skill fidelity 下运行 nominal/fault/sham，
   不以 retraining、换任务或换机器人能力替代 disruption effect。
2. 对 registered fact failures 运行单因素/联合 intervention；sham 与 intervention 必须共享
   endpoint、horizon、eligibility 和外生随机流。
3. 使用 held-out cases 检查方法是否能跨场景恢复；固定 event、checkpoint、affected set 和
   terminal rule，保留 controller/method failure。
4. 分别生成 D1--D6 的 point/range/NA/unstable 结果，不能以主标签汇总掩盖 relation-specific
   support 或 P3 subtype 差异。

**Owner**：experiment/statistics owner。  
**输出**：fault/sham/intervention/held-out bundles、D1--D6 tables、diagnostic panels。  
**验收**：所有结果能从 release manifest 重算；缺少 sham、audit、固定 fault 或 held-out coverage
   时标为 unsupported，不进入主结论。

### S10：release gate、论文回填与阶段收尾

**目标**：完成可审计发布包，再把结果回填论文；若门未通过则明确停在诊断状态。

**任务**

1. 运行 `benchmark/release_v3.py` 及 artifact verification，检查 source commit、paper SHA、
   composition digest、physical evidence、statistical evidence、receipt digest 和 score status。
2. 生成完整 manifest、checksums、环境身份、GPU/telemetry cleanup receipt、测试报告和 rerun 清单。
3. 只有 `paper_score_eligible=true` 且 `rankable=true` 时才生成排名/主表；否则生成带原因的
   `NA`、range 或 diagnostic report。
4. 论文正文回填必须单独登记 commit，不能把工程 progress 文档或 CPU diagnostics 当作论文证据。

**Owner**：release owner + paper owner。  
**输出**：`artifacts/release/<release_digest>/`、D1--D6 发布表、论文回填清单、阶段 close note。  
**验收**：release gate 可在干净环境重算；任一证据缺失都 fail closed。

## 4. 立即执行的第一批任务

建议下一工作批次只做以下四项，完成后再进入 shared/GPU 工作：

1. **统一论文身份**：更新所有进度索引和对齐文档到 `72b6a3b`，建立 S0 manifest。
2. **冻结 pilot contract**：从 30 anchors 选少量 P1/P2/P3-J/P3-S/P4、跨三个 domain 的 case，
   补齐 robot capability、role、obligation、resource、tick、milestone、oracle 字段。
3. **实现 source producer**：在 evaluator-owned 进程中真实生成 checkpoint/history/source receipt，
   并接入已完成的 isolated history runtime。
4. **做独立审查**：检查 source producer 是否可能接收 caller 自报的 checkpoint、condition、
   private context 或 RNG；检查 source/continuation/branch identity 是否可互相替代。

第一批的停止条件是：任何 pilot case 的 feasibility、skill 或 oracle 证据缺失；source receipt
无法在独立进程重算；或代码仍把 `algorithm` 作为物理 checkpoint 的必需 participant。遇到这些
情况先修合同，不进入 S4/S5。

## 5. 关键风险、禁止跳步与恢复规则

| 风险 | 触发条件 | 处理 |
| --- | --- | --- |
| 论文版本漂移 | manifest、代码或 receipt 出现旧 SHA | 立即停止该批次，重新生成 identity；旧 artifact 不自动升级 |
| 假性 physical qualification | 只有 CPU/mock、caller requested device 或单场景成功 | 保持 diagnostic，补真实 GPU collector receipt |
| source/checkpoint 泄漏 | checkpoint 含 algorithm/model/private state，或 branch 从 outcome-dependent state 派生 | 丢弃 bundle，修 source producer；不做事后清洗 |
| condition 泄漏 | 初始化期间可读取 condition、未来 outcome 或其他 branch namespace | branch 标为 method/infrastructure invalid，按合同重新生成 |
| shared-session 干扰 | slot permutation、batch load、queue/lock/brain hold 不一致 | shared config 失去资格，active block isolated rerun |
| shared crash/physics warning | session failure 影响 active blocks | active/in-flight 全部失效；queued/not-started 保持 queued/not-run |
| unknown 被当作失败 | malformed、timeout、incomplete、pair-invalid 被汇总为 0 | 保留状态，按 support 生成 NA 或区间 |
| 结果反推难度 | 用系统成功率修改 difficulty vector/tier | 违反科学契约；恢复冻结的 generation-time catalog |
| telemetry/关闭不完整 | native Kit 关闭无独立 marker 或仍有 telemetry process | 只保留 diagnostic；补 supervisor marker 和 cleanup receipt |

## 6. 推荐验证命令与产物

以下命令用于阶段性回归；真实 GPU 命令必须由对应 runner manifest 生成，不能把手工
`requested_device=cuda` 当成证据。

```bash
# 论文身份与合同层
cd /home/pupil/HeteroCoord
PYTHONPATH=. pytest -q \
  benchmark/tests/test_current_paper_binding_v3.py \
  tools/benchmark_branch/tests/test_paper_state_v3.py \
  tools/benchmark_branch/tests/test_paper_p0_p2_contract.py

# history runtime CPU diagnostic seam
PYTHONPATH=. pytest -q tools/benchmark_branch/tests/test_history_runtime.py

# vectorized / recovery / release 回归
PYTHONPATH=. pytest -q \
  tools/benchmark_branch/tests/test_vectorized.py \
  tools/benchmark_branch/tests/test_vectorized_qualification_contract.py \
  tools/benchmark_branch/tests/test_eai_vectorized_backend.py \
  benchmark/tests/test_repair_v3.py

python -m compileall -q benchmark tools/benchmark_branch
```

阶段产物至少包括：

- `protocol_manifest.json`、`catalog_digest`、`source_run_manifest`、`checkpoint_manifest`、
  `history_manifest`；
- branch/slot ledger、identity manifest、GPU/Isaac/driver/device/source digests；
- qualification、closed-loop audit、widening、rerun、telemetry cleanup receipt；
- D1--D6 support/uncertainty/status manifest、release bundle、checksums 和可重算命令。

## 7. 本阶段收尾

## 7A. Goal 模式执行记录（2026-10-22）

已启用 agent-team 工作区
`/home/pupil/Documents/Codex/agent-team/workspaces/heterocoord-goal-zcode-20261022`，并
修复/验证 ZCode 桥接。ZCode `GLM-5.3 / max` 在只读约束下完成了四步回归，证据交接见
`handoffs/zcode-regression-20261022.md`，原始日志位于该工作区的 `artifacts/`：

- 论文绑定、`paper_state`、P0--P2 合同：89 passed；
- history runtime synthetic seam：71 passed；
- vectorized qualification/backend 与 repair：61 passed；
- `compileall`：退出码 0；
- 合计 221 个测试，0 failure、0 error、0 skip；执行前后工程脏树计数均为 572，未启动 Isaac/Kit/GPU。

该批次只验证合同/诊断回归，不改变任何 formal gate。S0 同时生成了可重算的协议合同清单：
[protocol_manifest.json](/home/pupil/HeteroCoord/artifacts/protocol/72b6a3b975ae83743dc1d447b99edd728cb1dcdc/protocol_manifest.json)。
其状态是 `contract_only_local_mirror_synced`：已记录远端论文基线，并已将本地论文镜像
快进到 `72b6a3b`，现已包含 `sec/supp_system_state.tex`。该 manifest 不宣称物理
checkpoint、GPU shared-session 或论文分数资格。

S1 已完成“候选选择”这一步，但没有改变 catalog 的认证状态：当前候选集见
[pilot-candidates-20261022.json](/home/pupil/HeteroCoord/artifacts/catalog/pilot-candidates-20261022.json)，
共 6 个 anchor，覆盖 production/service/living_public 及 P1/P2/P3-J/P3-S/P4。下一步仍须逐项补齐
robot capability、resource/skill contract、feasibility、physical skill validation 和 oracle certification。

本规划完成后，当前工作可以按以下状态对外说明：

> 论文协议已按 Overleaf `72b6a3b` 对齐；工程已完成统计/合同接口和 CPU history-runtime
> 诊断闭环；正式物理、GPU shared-session、closed-loop audit、catalog certification 和
> paper-score release 尚未完成。下一步优先冻结细粒度 pilot contract 并实现 evaluator-owned
> source-run producer，然后再推进 isolated/shared/GPU qualification。

该结论既保留了已完成工作的可复用证据，也明确阻止把诊断结果提前升级为论文科学结论。

S2 receipt persistence 已补齐：collector 可将 `SourceRunReceipt.row()` 落盘，再通过
`SourceRunReceipt.from_row()` 独立恢复并验证 seal；未知字段、篡改和物理资格自授权均拒绝。
当前只证明 diagnostic receipt 的跨进程可重读边界，尚未接入真实 source adapter。

Source bundle manifest 也已固定为 `source-run-manifest.v1`：只允许 schema、sealed receipt
和 source identity，明确排除私有 state；manifest identity 漂移会被拒绝。真实 adapter
接入时应复用此 manifest，而不是自定义旁路格式。
该 manifest 已升级为 `source-run-manifest.v2` 并增加 `manifest_digest`，覆盖 schema、receipt
和 source identity；后续 collector 必须先验 manifest seal，再使用其中的 receipt。

## 2026-10-23 回归状态更新

ZCode full diagnostic regression 已完成：89 + 71 + 61 个既有回归、`compileall`、以及新增的 21 个 source-run/pilot-contract/capability-evidence focused tests 全部通过，合计 **242 passed**。该批次没有产生源码或论文写入；后续计划不变：先完成真实 evaluator source adapter 和 physical receipt，再进入 S3 isolated fresh continuation、S4 native fresh shared-slot、S5 GPU qualification；在 collector-owned evidence 到位前，不运行正式 GPU 大矩阵、不回填论文结果、不提升 formal gate。

### S2 当前增量

`EvaluatorSourceAdapter` 已作为真实 evaluator collector 的最小输入接口加入
`tools/benchmark_branch/source_run.py`。后续真实 adapter 必须实现 `collect(source_run_id)`，
仅返回既定 source payload；它不能携带 condition、submitted/private state、outcome 或自行
设置 physical qualification。接入顺序固定为：adapter collector → source bundle digest →
receipt/manifest seal → isolated continuation；在真实 collector receipt 到位前仍保持
diagnostic-only。
当前 adapter 增量主代理为 25 passed，ZCode 已复核整组 24 passed、source-run 单文件 16
passed 与 compileall 通过。主代理随后重跑完整四组回归，合计 246 passed，compileall 通过；
这只证明 adapter 改动未破坏既有诊断 seam，不改变真实 physical/GPU qualification 前置条件。

S3 当前新增 `SourceRunBundle.fresh_initialization_request(...)`，作为真实 collector 到
isolated continuation 的下一接缝。它只创建 condition-blind request，不执行 submitted
system 初始化，也不改变 physical qualification gate。S3 focused suite 经主代理与 ZCode
均为 27 passed；主代理兼容性全回归为 248 passed。下一步是把该 request 交给真实 isolated
process runner，并由 collector 生成初始化 outcome/cleanup receipt。
### S3 receipt persistence 增量

`InitializationOutcome.row()` / `from_row()` 已固定 collector-owned JSON row、schema 与
digest seal；恢复时拒绝字段漂移、seal 篡改、错误布尔类型和 physical self-authorization。
ZCode 独立只读复核相关四组 focused tests 为 **104 passed**，`compileall` 退出码 0；主代理
兼容性回归仍为 **248 passed**。该接口只解决跨进程诊断 receipt 的可重读性，不能替代真实
isolated runner、物理 tick/hold 证据或 cleanup receipt。

下一执行顺序保持：

1. 将 sealed source bundle 接入真实 isolated process runner，并生成 collector-owned
   initialization outcome 与 cleanup receipt；
2. 用同一 identity/digest 做 branch/history 回放与失败重试审计；
3. 只有真实 checkpoint/history/branch isolation 证据齐备后，才重新评估相应 gate。
### S3 isolated history integration 增量

`HistoryBlockRuntime` 现在将初始化 outcome 通过 `InitializationOutcome.row()` 写入
checkpoint ledger；新增测试把 `EvaluatorOwnedSourceProducer` 产生的 sealed source bundle
交给 `IsolatedHistoryBackend`，执行 4 个 spawned CPU contexts，并对 ledger 中 4 条 sealed
rows 做 `from_row()` 恢复。主代理与 ZCode 均完成 **167 passed**，compileall 通过；兼容性全
回归升至 **249 passed**。

这只证明 source-bundle → isolated CPU history runtime 的集成和 receipt persistence，不是
真实物理初始化。后续必须继续补齐真实 collector-owned cleanup receipt、真实环境身份和失败重试
审计，完成前保持所有 qualification gates 为 false。
### S3 cleanup receipt 增量

`IsolatedHistoryBackend` 现在提供 sealed `ProcessCleanupReceipt`，运行时 finally 将其写入
`events` ledger 的 `context_cleanup` 事件。新增测试覆盖 4 个 context 的回执恢复、篡改拒绝、
`process_exit_code` 类型和 `cleanup_status` 与 `closed/reaped` 的一致性。主代理与 ZCode 均为
**74 passed**，compileall 通过；全量兼容回归升至 **251 passed**。

该回执仅用于证明进程关闭/reap 的生命周期事实，不能作为物理或历史资格证据。下一步是把
cleanup receipt 与真实 collector 的初始化失败/重试 identity 绑定，并保持所有正式 gate 为
false。
### S3 cleanup receipt identity 增量

cleanup receipt 已绑定运行、source-run 和 checkpoint 三层身份：`run_id`、`source_run_id`、
`checkpoint_digest`。runtime 负责从当前 store/source 注入，恢复端拒绝缺失、空值或 seal
不匹配。ZCode 74 passed、compileall 通过，兼容回归保持 251 passed。

下一步可在不改变 receipt schema 边界的前提下，将这些 identity 用于真实 collector 的失败
重试索引与 branch/history 回放审计；本地 CPU receipt 仍不构成物理 qualification。

### S3 manifest failure-path 验收补充（2026-10-23）

失败路径已由 ZCode 独立复核：history runtime **76 passed**，`compileall` **EXIT=0**。manifest 构建失败会留下 `cleanup_failed` 与原始 `cleanup_errors`，完成 store finalize 后继续抛出 RuntimeError，不生成伪 manifest，也不改变任何 qualification/paper-score 字段。

后续顺序：

1. `s3-runtime-manifest-failure-path` 已完成并关闭；
2. `s4-real-collector-receipts`：接入真实 evaluator-owned initialization/cleanup collector；
3. `s4-physical-checkpoint-qualification`：完成 Isaac/PhysX hold/readback、tick 与环境身份 receipt；
4. `s5-history-branch-qualification`：完成真实 isolated continuation、branch identity、native shared-slot 与 GPU qualification；
5. `s6-closed-loop-and-statistics`：完成 audit、fault/sham/intervention/held-out recovery、calibration/baseline、paired pilot，再审查 D1--D6 和论文结果表。

禁止把 CPU/mock/vectorized 证据当作物理资格，禁止提前启动 GPU 大矩阵或回填论文分数。

### S4 collector receipt handoff 增量（2026-10-23）

`CollectorReceiptBundle` 已完成并由 ZCode 复核（**78 passed**、compileall `EXIT=0`）。真实
collector 现在有明确的交接对象：先验证 source-run manifest，再从 sealed checkpoint/cleanup
ledgers 构建 replay manifest，最后生成 bundle seal。任何 source/checkpoint identity 漂移、
未知字段或摘要篡改均拒绝恢复。

下一步不是扩展 synthetic 字段，而是实现真实 evaluator-owned collector，使其实际产生同一
bundle；随后在 Isaac/PhysX 环境中补充 physical hold/readback 与 device/driver/container/source
digest receipt。当前所有 formal gates 继续为 false。

### S4 真实 collector 环境前提与接线任务（2026-10-23）

只读预检确认当前 base 环境没有 `isaacsim`、`omni.isaac.lab`、`torch`；主代理进一步确认
Conda 环境 `env_isaaclab` 存在且可发现 `isaacsim`、`torch`、`pxr`。已有物理入口位于
`tools/physics_replay/live.py` 与 `simulator.py`，但缺少到 `EvaluatorSourceAdapter` 的真实
接线。下一次执行必须切换到 `env_isaaclab`：启动 evaluator-owned isolated session，调用
`LiveCheckpointBackend.capture()`，组装 observation/permitted history/initializer，随后通过
`SourceRunBundle` 与 `CollectorReceiptBundle` 落账，并生成 readback/tick/device/source digest
及 cleanup receipt。证据齐备前不得提升 gate。

### S4 LiveCheckpoint source adapter 已完成（2026-10-23）

`LiveCheckpointEvaluatorSourceAdapter` 已固定 capture、sealed store read、restore/readback 与
projection 的顺序和失败语义；主代理与 ZCode 均验证 focused suite **99 passed**，compileall
`EXIT=0`，`env_isaaclab` 纯导入成功。collection receipt 只能是
`diagnostic_readback_only`，source receipt 的 `physical_qualified` 仍硬封为 false。

下一工作批次改为真实 session 接线，而不是继续扩展 synthetic schema：

1. 在 `env_isaaclab` 中用最小 native fixture 建立 evaluator-owned `open_simulator_session`，并把
   `LiveCheckpointBackend` 实例交给 adapter；先做单 step、单环境、单 checkpoint 的受控 smoke；
2. 实现并验收五类真实 projection：physical/runtime snapshot → paper-state `state`、live
   observation、permitted history、initializer contract、node identity；禁止读取 condition、未来
   outcome、submitted/private state 或 branch namespace；
3. 同一运行落盘 `SourceRunBundle`、`LiveCheckpointCollectionReceipt`、初始化/cleanup sealed rows
   与 `CollectorReceiptBundle`，确保 source/checkpoint/run identity 可从账本重算；
4. 将 session close、Kit/telemetry 退出和 GPU 进程检查纳入 cleanup receipt；任何 readback、identity
   或 cleanup 失败都只保留 diagnostic failure，不产生 physical qualification；
5. 先完成单场景证据与可重跑 manifest，再决定是否进入 physical hold/tick 资格测试；在此之前不跑
   GPU 大矩阵、不生成排名、不回填论文结果。

### S4 native smoke 启动前审计结果（2026-10-23）

CUDA/GPU 与项目 provenance 预检已通过，但已有 native replay tests 不能直接作为新 collector
smoke：三个 worker 仍为 CPU，且绕过 source/cleanup/telemetry receipt 链。执行顺序调整为：

1. 完成 sealed physics snapshot 到默认 paper-state schema 的 evaluator-owned projection；
2. 新增专用单 checkpoint CUDA diagnostic worker，显式记录 requested/runtime device 与 GPU identity；
3. 在 worker 内接入 `LiveCheckpointEvaluatorSourceAdapter`，持久化 source manifest 与 collection
   receipt，并在 finally 记录 session/trace/telemetry cleanup；
4. CPU/mock 回归与 `env_isaaclab` 纯导入通过后，由 ZCode 执行一次受控 native smoke；
5. smoke 成功仍只标记 diagnostic，待独立 physical hold/tick 与 cleanup 资格 receipt 到位后再评估 gate。

### S4 受监督原生 source seam 已完成；后续计划重排（2026-10-23）

项目自有 `EAI-Factory-Z1-v0` 已通过一次受监督 CPU diagnostic 运行。首个运行暴露并保留了
Kit 关闭导致 child close receipt 未落盘的问题；supervisor-side marker finalization 修复经主代理与
ZCode 各 **33 tests**、compileall 以及一次新目录原生重跑验证。修复后运行的 close receipt、
source/collection seals、checkpoint sidecar 和空 telemetry 均通过独立复核。

后续不再继续扩展 diagnostic source schema，按以下依赖顺序推进：

1. `s5-physical-hold-readback-protocol`：定义并实现真实 hold/readback/tick、环境身份、device 与
   cleanup 资格 receipt，先做静态设计审查和 fail-closed tests；
2. `s5-minimal-shared-resource-history-branch`：设计评审通过后，才运行最小 GPU/shared-resource、
   fresh history initialization 和 isolated branch continuation，明确 lease、namespace、seed、tick
   与 retry identity；
3. `s6-closed-loop-audit`：补齐控制回路、故障注入、sham/intervention、held-out recovery 和
   cleanup/failure audit；
4. `s6-statistical-pilot`：先做 calibration/baseline 与 paired pilot，再决定样本量和 widening；
5. `s7-paper-eligibility-review`：只有前述 receipts 与统计协议全部通过后，才评估论文分数、排序与
   结果表更新。

当前七个 formal gates 继续全部为 false。不得从本次 CPU diagnostic 运行推断物理资格，也不得
自动启动 CUDA 或回填论文正文。

### S5 physical hold/readback/tick 合同（2026-10-23）

ZCode 对论文补充材料和当前 native seam 完成了一轮只读审计，确认论文要求 frozen physical
checkpoint、controller/queue/lock 状态、初始化期间 physical time held fixed、结果观察前注册
eligibility、K=3 同 checkpoint 重复以及 sealed permitted history。当前 `-02` CPU artifact 只有
单次 restore/readback，不能证明 hold 窗口内 tick 不变、trajectory 级验证、GPU runtime 或 K=3
重复。

主代理新增 `PhysicalHoldReadbackReceipt`（`heterocoord.physical_hold_readback.v1`）作为下一步
合同边界，包含 checkpoint/source/collection/runtime/cleanup identities、pre/post state digest、
tick trace、单调 host-time hold 区间与采样次数、逐通道误差与 tolerance，并强制 requested/actual device 一致、hold 前后
tick/state 不变、readback 与 telemetry cleanup 成功、`physical_checkpoint_qualified=false`。
它只是 diagnostic review receipt，不是资格授权。

主代理与 ZCode 对最终语义修订输入均得到 **23 passed**，compileall exit 0；修订后不再存在
`hold_window_ticks`，因此不会把 host 等待时间误写成物理时间推进。本阶段只完成合同，尚未实现
受控 native producer，也未启动 CUDA。

### S5 native hold producer 已闭环（2026-10-23）

受监督 source collector 现已实际产生 `heterocoord.physical_hold_readback.v1`：child 在 checkpoint
restore/readback 后仅调用 `read_physics()` 做 4 次观测，不调用 `env.step()`；supervisor 在完整
session-close marker 与空 telemetry residue 到位后才终结 sealed receipt。实现同时修正了两个
跨对象缺陷：`SourceRunBundle.validate_manifest_row()` 的返回值按 `SourceRunReceipt` 使用，且
readback tolerance/pass 状态从真实 restore report 写入 hold evidence，不再由 supervisor 猜测。

主代理与 ZCode 对相同输入均得到 **35 passed**，compileall 和 scoped `git diff --check` 均为
exit 0。随后只执行一次新的 CPU 原生诊断：
`artifacts/project_runtime/source-collector-cpu-20261023-03/`。该运行 return code 0、supervisor
pass、session close 完整、telemetry residue 为空、无 GPU lease；checkpoint/start/end/post tick
均为 8，4 次 hold 观测覆盖 5,617,120 ns 主机单调时间，最大 readback error
`2.9808951866712727e-07 < 1e-05`。source、checkpoint、collection、runtime、hold、cleanup seal
及交叉身份均可离线重算。

这仍是 CPU diagnostic，不是物理资格。七个正式 gate 继续全部为 false。

### 后续实验规划：按论文因果链推进

1. **S5 minimal shared-resource history/branch 合同（当前 P0）**：先定义 sealed receipt 和失败语义，
   将同一 qualified-candidate checkpoint、K=3 condition-blind 重复、fresh private initialization、
   permitted history、branch namespace/seed/tick、GPU lease/device/runtime、cleanup/retry identity
   绑定在一个可重算 manifest 中。先做 CPU/mock 负向测试；不得由 receipt 自授权 history、branch
   或 physical gate。
2. **S5 单次 CUDA 诊断（需单独授权）**：只有合同、fail-closed tests、resource admission 与
   cleanup 审查全部通过后，才申请执行一个场景、一个 checkpoint、最小 continue/hold pair；
   不运行大矩阵，不把单次成功当作资格，不自动启动 GPU。
3. **S6 closed-loop audit**：在 fresh history/branch identity 真实成立后，验证 action→physics→
   observation 闭环，补齐 fault、sham、intervention、held-out recovery、失败重试与清理账本；
   任一 provenance/eligibility/trajectory 缺口均 fail closed。
4. **S6 statistical pilot**：先冻结 primary metric、paired unit、exclusion、seed 和 stopping rule，完成
   calibration/baseline，再做小规模 paired pilot；报告 effect size、uncertainty 与失败率，未完成
   preregistration/K=3/独立重复前不得 widening。
5. **S7 paper eligibility review**：逐项审查 physical/history/branch/closed-loop/statistical 五级证据，
   只有全部 receipt 与统计协议通过后才允许 `paper_score_eligible=true`；在此之前不改论文正文、
   不写结果表、不生成排名。

### S5 shared-resource/history/branch 合同已完成；转入账本汇聚

合同实现与独立验收已完成：主代理与 ZCode 对同一输入均得到 **40 passed**，`compileall` 和
scoped `git diff --check` 均为 exit 0。合同固定 K=3 × 四条件、12 个 fresh context、初始化屏障后
分配、repeat 内共享/repeat 间不同的 exogenous stream、私有 init/policy stream、branch-local
writable namespace、唯一 attempt/retry identity，以及 runtime/device/lease/eligibility/cleanup seal；
七个正式 gate 继续硬封为 false，未启动 native/GPU/CUDA。

下一 P0 不修改 `HistoryBlockRuntime` 的单次 repeat 语义，而增加 collector-owned 汇聚器：读取三个
已 finalize 的独立 store，从 initialization outcome、branch manifest、replay identity manifest 和
context cleanup rows 重算 K=3 总收据。汇聚器必须拒绝缺失/重复 context、role 不全、屏障前分配、
跨 repeat exogenous stream 复用、私有流或 namespace 复用、attempt/retry identity 冲突，以及
runtime/cleanup seal 漂移。先完成 CPU/mock 正反测试与 ZCode 独立复测；它仍不授予任何 formal gate。

### S5 K=3 账本汇聚已完成；下一步为单独授权的 CUDA diagnostic

collector-owned 汇聚器已实现并通过主代理/ZCode 各 **92 tests**。它只接受 r1/r2/r3 三个
finalized sealed store，并从现有账本重算 12 个 context 的 lineage、barrier/assignment、随机流、
namespace、attempt/retry、cleanup 与 replay identities；缺失、重复、seal 或 lineage 漂移均 fail
closed。CPU 多进程验证不授予 physical/history/branch qualification，七个 gate 仍全部为 false。

下一任务是单独授权的最小 CUDA diagnostic：一个场景、一个 checkpoint、最小 hold/continue 路径，
记录实际 GPU/device/lease/runtime/cleanup receipt。执行前再次核对 GPU 空闲与 telemetry cleanup；
只允许一次运行、禁止矩阵和自动重试。单次通过仍只产生 diagnostic artifact，之后才能决定是否
进入 closed-loop audit；未获明确授权前不执行。

### S5 CUDA admission guard 已收口

source-collector supervisor 现已 fail-closed：要求 child/runtime 与 supervisor GPU UUID 一致、
lease acquisition/release 真实记录、session close 完整且 telemetry 清空。主代理/ZCode 各 36 tests
通过，compileall/diff check 通过，未启动 GPU。下一步是等待用户对冻结的单场景、单 checkpoint、
`cuda:0`、一次执行授权；授权包位于
`benchmark/docs/progress/CUDA_DIAGNOSTIC_AUTHORIZATION_20261023.zh-CN.md`。
