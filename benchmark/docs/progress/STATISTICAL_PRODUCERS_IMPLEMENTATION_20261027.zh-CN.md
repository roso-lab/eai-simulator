# D1–D6 statistical-report 生产器实现收尾（2026-10-27）

## 阶段结论

上一阶段（[设计审计收尾](STATISTICAL_PRODUCERS_DESIGN_CLOSEOUT_20261027.zh-CN.md)）确认的两个 P0 结构缺口已全部实现并通过双路验收：

1. **P0-1 已闭合**（提交 `5de13a3d`）：发布清单的 statistical report set 不再硬编码"全局恰好 6 份（D1–D6 各一、score_id 全局唯一）"，改为按 `result_key_digest` 唯一绑定，可表达论文固定六被测系统 × D1–D6 六维的主结果矩阵。
2. **P0-2 已闭合**（提交 `81ac6de0`）：新增 `benchmark/statistical_report_producer_v1.py` 权威生产器，逐 D1–D6 调用冻结 reducer，装配 `heterocoord.statistical-report.v1` 报告、binding manifest 与 report-set，其输出可直接被 `paper_result_index_v1` 与 `paper_release_manifest_v1` 消费。

正式资格门继续全部关闭：`statistical=false`、`physical=false`、`difficulty=false`、`rankable=false`、`paper_score_eligible=false`。本阶段纯 CPU 合同工作，未启动 GPU/Isaac/CUDA，未生成任何论文 observed 结果。

## P0-1：report-set 按 result key 绑定

改动集中在 `paper_release_manifest_v1.py` 的 `_collect_bound_evidence` 与其测试：

- report-set 条目改为 `{result_key_digest, report_ref}`，条目内 digest 重复被拒。
- binding 之间 `result_key_digest` 唯一（防伪造：digest 由 `result_key_digest(identity)` 重算，caller 自报值无效）。
- report-set 与 binding 的 result-key 集合必须**完全相等**；逐条 `report_ref` 相等。
- D1–D6 全局各至少一份覆盖；`score_id` 枚举限定 D1–D6（越界如 D7 被拒）。逐系统完备性仍由 required-result directory 与索引层负责，发布层不做重复检查（代码内有职责边界注释）。
- 同一报告字节身份不得绑定多个 result key、全部 ref 字节校验、`_validate_statistical_report` 调用、12 角色 dependencies、formal gates 恒 false 均原样保留。
- schema 字符串保持 `heterocoord.statistical-report-set.v1` 不升版：该形态从未产出真实工件，旧形态（score_id 条目）在 build 与 verify 两条路径均以明确错误 fail-closed（有锁定测试）。
- `statistical_evidence_v3.py` 内存层的"恰好 6 份"明确不改：那是 release_v3 旧诊断链，与论文发布清单链相互独立。

## P0-2：权威生产器

`benchmark/statistical_report_producer_v1.py`（831 行）+ 31 个测试（777 行）。关键裁决与代码事实：

- **只调用权威 reducer**：D1 → `organization_report_from_payload`；D2/D4 → `analyze_dimension`；D3 → `d3_relation_report`；D5/D6 → `FixedCatalogV3.analyze`。生产器内无任何重采样/加权/区间构造等统计逻辑（独立复查逐处核验）。
- **scorebook digest 双口径**：release 侧算法 `sha256(canonical_json_bytes(scorebook).rstrip(b"\n"))` 与 reducer 内嵌 `default_scorebook_v3().digest()` 在默认 scorebook 上结构性恒等（canonical JSON 恰含一个尾部换行）；生产器按 release 侧口径写入，且两口径不一致时显式 fail-closed。
- **na 处置**：索引层 `_validate_statistical_report` 只接受 valid/range/unstable/incomplete，na 被拒（有直证测试）。生产器遇 reducer status="na"（如 D2 零正参考率的有限样本区间）fail-closed 报错；NA 语义由 required-result directory 的 missing/NA 表达，不伪造成报告。
- **support 13 字段逐维诚实映射**：只从 reducer 输出的权威计数推导；reducer 未发布的计数记为"absent 0"（无权威数可抄），绝不把未知写成观测到的零。逐维度映射表在模块 docstring。
- **range 溯源边界**：索引层对 observed_range 状态硬白名单 `interval_method="identification_range"`（`_validate_estimate`），reducer 权威 method 串保留在 report_ref 绑定的报告字节里可溯，生产器不越权改写（有事实锁定测试防未来误改）。
- **fail-closed 全集**：identity 无效/自报 digest、未注册 implementation/condition、result key 未注册进 required-result directory、paper_commit 不符、input.json digest 不符、ledger 字节 sha 不符、请求内重复 result key、覆盖已有文件、scope 含 synthetic/diagnostic 词汇、metrics.csv/results_pipeline 路径——逐条独立错误消息，两阶段"先全验后全写"，失败无部分工件。
- 写入纪律：O_EXCL 无覆盖、canonical JSON、fsync；CLI 成功输出 canonical JSON、任何失败统一 exit 2（含非预期 OSError）。

端到端闭环测试：2 系统 × 6 维 = 12 个 result key 真实请求 → 生产器 → `build_result_rows` 12 行 → 发布清单 build+verify 往返（36 项 bound_evidence），同 score_id 跨系统并存。

## 验收证据（agent-team 双路 + 提交边界）

- 受影响集（producer + release manifest + result index）：80 passed，exit 0（系统 python3）。
- 相邻集（release_v3/provenance/analysis/relations/fixed_catalog/organization/results_pipeline）：89 passed，exit 0。
- 提交边界全量 `pytest benchmark`（env_isaaclab 解释器，torch/gurobipy/scipy 齐备）：**1026 passed / 0 failed / 0 skipped**，23.08s，exit 0。注：系统 python3 缺 torch 会在 `test_eai_lifecycle_contract.py` 收集中断，已用干净参照树证实为环境性、与本改动无关。
- `py_compile`（cfile 定向 /tmp，无 pyc 入库）与 `git diff --check` 全净。
- 独立双角度复审两轮均 ACCEPT-with-notes，无 P0/P1；P2 清单处置见下。

## P2 处置与开放边界

已修：D1 support 映射 docstring 措辞校准（events 日志确实被评分消费，缺的是输出层计数）；CLI 非 EEXIST OSError 统一 exit 2；range interval_method 白名单事实锁定测试。

仅记录不扩代码：

- **多 key 请求中途写失败回滚后会残留空目录**（无工件字节，仅外观）。
- **跨 implementation_id 复用同一 event-ledger 字节当前三层（producer/index/release）一致允许**：roster 真相同时内容寻址复用自洽，但两个不同被测系统绑定同一原始 ledger 字节属合理性疑点，建议在 required-result directory 审计环节人工复核或未来显式规则化。
- `statistical_evidence_v3.py` 内存层"恰好 6 份"（release_v3 旧链）与发布层新矩阵语义并存，如未来旧链也要吃矩阵需单独设计。

## 未完成、未声称完成

- 未生成真实 evaluator terminal material，未运行任何真实 case；生产器等待真实语义地图与运行时证据输入。
- 未改变任何 formal gate；diagnostic/synthetic/measured_unqualified 结果不会被本链升级为论文 observed。
- 六系统中四个（FMC-ATA、BT capabilities、Gosrich、HARL: HAPPO）的适配器/资格登记仍是框架缺口（见 [383acc1 审计](../../../../../Documents/Codex/agent-team/workspaces/heterocoord-goal-continuation-20261027/handoffs/paper-alignment-383acc1-audit.md)），TeamWeaver 与 EMOS 为零号已接通。

## 提交

- `5de13a3d` fix(benchmark): bind release report set by result key digest
- `81ac6de0` feat(benchmark): add statistical report producer v1
- 本文档提交见同分支后续 docs 提交；分支：`codex/stat-report-producers-v1`（基线 `origin/HeteroCoord@f0b760ae` + 设计收尾 `aa23769b`）。
