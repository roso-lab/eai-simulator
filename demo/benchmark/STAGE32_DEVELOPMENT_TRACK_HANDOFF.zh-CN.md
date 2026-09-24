# 阶段 32：快速并行开发与严格发布隔离

日期：2026-09-24  
基线：`eb05edf`（`benchmark-task` 远端同提交）

## 模式切换结论

按 09-24 拍板，本阶段起改为"两条轨道"：

- **开发轨道**：允许 `development_unverified` 资产、合成状态、symbolic fixture 和 contract-only 后端，继续完成 reader、oracle、评分侧车、通信、checkpoint 的契约开发与非发布 smoke 验证。所有结果强制携带开发态标记并归档为 raw/diagnostic/contract 证据。
- **发布轨道**：只使用冻结资产和正式 provider；Factory 冻结哈希不匹配时保持 blocked，不改 hash、fixture、题包或 overlay 语义。

冻结哈希不匹配只阻塞 Factory 正式 native episode、正式业务结果、scoreable 发布和正式 asset admission；不阻塞任何契约开发。获得冻结资产后只需重跑受影响的 Factory admission 与正式 episode。

## 硬约束（全程有效）

1. 不修改冻结题包、见证计划、reach_m、overlay 语义或正式冻结 hash。
2. 不提交或覆盖 `source/EAI_hmrs/EAI_hmrs/envs/my_env.json` 与根目录协议 markdown（用户文件）。
3. `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false` 全程保持。
4. `unverified` 只能是平台证据状态，不能传给被测模型，也不能进入正式统计。
5. 基础设施失败、资产缺失和模型执行失败分开归档。
6. 不把作者 witness 当作唯一合法路径；remaining-task oracle 判断从可信当前状态是否存在任一合法完成方案。
7. 不瞬移、不放宽 reach_m、不伪造 attachment、不改业务评分。

## 开发态标记契约（本阶段新增）

`demo/benchmark/development_track_marker.py`：

- `asset_status`：`frozen_verified` | `development_unverified`（后者必须携带至少一个 `infrastructure_blocker`）。
- `evidence_class`：`raw` | `diagnostic` | `contract`。
- `release_eligible` 恒 `False`：运行标记只能收紧发布，正式资格由独立发布审核决定。
- `factory_hash_mismatch_marker()` 固定携带 `factory_fixture_hash_mismatch` blocker。
- `merge_development_markers()` 聚合出 run 级隔离边界：任一场景 unverified 或有 blocker，整个 run 留在开发轨道。
- `stamp_evidence_record()` 给证据记录盖章：`formal_statistics` 恒 `False`，防止开发态证据被误当正式成绩。

评分侧车 `goal_scoring_adapter.py` 新增 `development_track_observations()`：

- 未声明标记 → `status=missing`（fail-closed，不因未声明而放行）。
- 标记格式错误/篡改 → `status=invalid_marker`，质量失败原因入档。
- `build_scoring_adapter_metadata(..., development_markers=[...])` 可选注入；三个发布标志不因此改变。

## 并行轨道分工

| 轨道 | Owner | 文件范围（均为新文件） | 出口 |
|---|---|---|---|
| A. House/City native reader | 工程代理 1 | `goal_reader_house.py`、`goal_reader_city.py` + 各自测试 | 合成后端全绿、能力不再必要仍 deployable、唯一能力退出经完整求解 not_deployable、读回失败 fail-closed |
| B. Warehouse/Factory reader contract + 替代路径证书 | 工程代理 2 | `goal_alternative_path_certificate.py`、`goal_reader_warehouse.py`、`goal_reader_factory.py` + 测试 | 证书 schema + "已到 dock 未交接+抓取退出仍 deployable"；Factory 只做 contract-only 且全程携带 hash-mismatch blocker |
| C. 评分侧车 + 开发态标记 + 台账 | 主任务 | `development_track_marker.py`、`goal_scoring_adapter.py`、`test_benchmark_development_track_marker.py`、侧车测试 | 标记契约 11 测试 + 侧车集成测试全绿（已完成） |
| D. 通信容量契约实验 + checkpoint 分支 | 工程代理 3 | `communication_capacity_experiments.py`、`goal_checkpoint_branch.py` + 测试 | JP/IID/BP 配对、三池 C90/C50、bootstrap 只在有效样本内；变化前快照纪律 + 编键分支对拍 + sham 隔离 |
| E. 餐厅八机准入审计 | 审计代理 | `STAGE32_RESTAURANT_ADMISSION_AUDIT.zh-CN.md`（只读审计） | 八机准入矩阵 + authoring_only→native 差距清单 |

并行纪律：不同代理不同时修改同一核心文件；代理不运行 git 变更命令，由主任务统一审查、分批提交；先跑目标测试再决定是否全量。

## 阶段出口（沿用阶段 31，按轨道拆分）

- 阶段 1（快速契约收口）：五轨道目标测试全绿，不要求 Factory 正式资产存在。
- 阶段 2（双机并行验证）：本机 + ljy 隔离树跑同一组目标测试；资产缺失只记 infrastructure blocker。
- 阶段 3（场景 native 接入）：House/City 先接 native clock/readback，Warehouse/Factory 接业务回执与替代路径，餐厅独立 native admission。
- 阶段 4（正式发布前验证）：冻结资产、native reader、替代路径证书、正式 episode 全过前，三个发布标志保持 false。

## 回归证据

- 被限流中断的聚焦回归重跑：评分侧车 + oracle registry + deployment cases + deployment + communication + checkpoint 共 `184 passed`。
- 开发态标记契约与侧车集成：`test_benchmark_development_track_marker.py` 11 passed；`test_benchmark_goal_scoring_adapter.py` 36 passed（含 2 个新增 development_track 测试）。
- 合并聚焦集：`197 passed`。

各轨道测试结果与缺口见文末"轨道结果汇总"（代理返回后更新）。

## 轨道结果汇总

聚焦回归（本批全部新增+关联既有测试）：`308 passed`。

### 轨道A：House/City reader（子代理两次限流失败后由主任务接管）

- 新增 `goal_reader_house.py`、`goal_reader_city.py`，测试 `test_benchmark_goal_reader_house.py`、`test_benchmark_goal_reader_city.py`（26 passed）。
- 两个场景各有：冻结注册（合成 scope `synthetic_contract_development`，native scope 必须携带 `native:` 前缀证据否则拒绝）、状态合同、合成状态 provider（native 桥接点=host 注入 probes）、独立 source readers（从 native samples 测量，绝不读 state 的 true_facts）、开发案例查询（结果盖 development marker）。
- 关键语义已锁进测试：书堆/椅子完成后搬运成员退出仍 deployable（能力不再必要）；唯一搬运退出且未完成经完整 BFS 才 not_deployable；City 地面引导与空中服务双替代路径+双向失效对照；地面退出后空中替代救不了交通谓词（求解器整合全部终局谓词）；reader 读回缺谓词/说谎/实体版本错配一律 fail-closed。
- 缺口（native 接入时）：`time_s` 换 native clock、readers 换 host 原生探针、注册换 `native_remaining_task` scope+native 证据。

### 轨道B：替代路径证书 + Warehouse/Factory reader（同上由主任务接管）

- 新增 `goal_alternative_path_certificate.py`：证书冻结路径类别/覆盖测量点/独立复核状态；`gate_not_deployable_result()` 把无有效证书的 not_deployable 降级为 failed/null；deployable 不需要证书（搜索本身展示了一条合法路径）；证书永不产生标签。
- `goal_reader_warehouse.py`（10+11 测试）：两订单对象身份、dock 稳定放置、回执、closeout、通道；**"已到 dock 未交接"中间态 + 抓取退出仍 deployable** 已锁进测试；单机强载荷/双机轻载荷组合（2 dolly）+纸箱（1 dolly）替代路径与资源不足对照。
- `goal_reader_factory.py`（contract-only）：异常独立观察（observation_record）、实际服务到达（physical_completion）、维护交接回执（business_receipt）三类 reader；固定服务车无虚构搬运要求（测试断言无 carry 槽）；全程携带 `factory_fixture_hash_mismatch` marker。
- 测试：证书 10 + warehouse 10 + factory 11 = 31 passed。

### 轨道D：通信容量契约实验 + checkpoint 分支（子代理失败后由主任务接管）

- 新增 `communication_capacity_experiments.py`：通过 `set_conditions(pool=...)` 正规机制注入时变延迟/丢包（每次干预进 ledger）；JP=固定 vs 抖动（精确同均值，TTL 分离分布效应）；BP=IID vs 突发（每十块精确同丢包率）；三池容量扫描走 `summarize_capacity_curve` 得 C90/C50 包围区间，bootstrap 只在有效样本内；`bytes_by_direction()` 方向缺失 → 分解 null 不猜测。
- 新增 `goal_checkpoint_branch.py`：变化前快照纪律（checkpoint 时间/序列必须严格早于变化点，违反即 invalid）；分支 RNG 用共享比较流键（同 seed 同键同流，异键异流）；sham 控制永远 diagnostic 隔离，合并即抛错；strict 比较资格=同 seed+同流键+流摘要一致。native PhysX restore 验证仍是待过的门（blocker `native_restore_verification_pending`）。
- 测试：容量 12 + 分支 8 = 20 passed（含方向 fail-closed、参考点完美、低容量退化）。

### 轨道E：餐厅八机准入审计（子代理失败后由主任务接管）

- 产出 `STAGE32_RESTAURANT_ADMISSION_AUDIT.zh-CN.md`：八机逐机证据矩阵（仅 Carter 有原生证据，出处 `goal_driven_tasks.py:1250-1273` docstring）、authoring_only→native admission 十项差距清单（含"回执已提交后 relay 能力退出仍 deployable"对照态设计）、单 Carter 闭环不可外推边界（代码显式声明 `goal_restaurant_episode.py:1-8`）、建议准入顺序（carter_2→iris_1→m20_1→go2/lite3）。

### 子代理基础设施事件记录

本批四个工程子代理全部因 harness 模型请求失败（"Model request failed"，与上午 codex 会话 429 同期）中断且无部分产出，按"不重复启动同一任务、主任务接管"纪律由主任务顺序完成全部轨道。阶段审核代理（gpt-6-astra）在收尾阶段重新发起，审核报告落地 `tmp/stage32_directional_audit_gpt6astra.md`。
