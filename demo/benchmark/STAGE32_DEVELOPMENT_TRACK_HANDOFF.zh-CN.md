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

（待并行代理返回后填写）
