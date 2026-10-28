# D1–D6 statistical-report 生产器设计阶段收尾（2026-10-27）

## 阶段结论

本阶段完成了对最新工程基线 `origin/HeteroCoord@f0b760ae` 的只读审计，目标是确认真实 evaluator 收据能否沿论文公式进入 `heterocoord.statistical-report.v1`、结果索引和发布清单。结论是：D1–D6 的 CPU reducer 和结果索引校验已经存在，但**权威 statistical-report producer 尚未实现**，因此当前工程仍不能把未来真实运行数据自动生产为论文结果。

正式资格门继续关闭：`statistical=false`、`physical=false`、`difficulty=false`、`rankable=false`、`paper_score_eligible=false`。

## 已验证事实

- `benchmark/scorebook_v3.py` 已冻结 D1–D6 的公式、支持层级、缺失规则、估计器族和论文身份 `383acc19657a0fd7ebd881a5ac2bb0e03f8c38c8`。
- `benchmark/analysis_v3.py` 已提供 D1/D2/D4/D5/D6 分析入口；D3 由 `benchmark/relations_v3.py` 的 relation ledger 入口提供。
- reducer 已保留未知、方法失败、基础设施未知、固定目录和重复键，不允许静默删行或重加权。
- `benchmark/paper_result_index_v1.py` 已对报告执行内容寻址、身份、支持计数、估计状态、event-ledger digest 和 formal-gate 校验。
- `benchmark/production_results_v1.py` 仍输出 `measured_unqualified`/诊断结果；它不是 statistical-report producer，不能直接回填论文 observed cell。

## P0 结构缺口

### 1. 缺少权威报告生产入口

仓库中没有一个按 D1–D6 reducer 统一装配以下字段的生产模块：

`paper_commit`、`scorebook_digest`、完整 result identity、`input_digest`、`estimate`、`support`、`event_ledger_digest` 和 `formal_gates`。

下一阶段应新增独立 `benchmark/statistical_report_producer_v1.py`，只消费 evaluator-owned 输入和冻结 required-result directory；不得从 `metrics.csv`、诊断点值、锁应答或调用方自报分数推断结果。

### 2. 发布清单把报告集错误限制为 6 份

`benchmark/paper_release_manifest_v1.py` 当前要求 report set 全局恰好包含 D1–D6 各一份。这与论文固定六个被测系统的主结果矩阵不一致：至少需要每个 result key 一份报告，通常为 `6 systems × 6 dimensions = 36` 份；同一 `score_id` 在不同 implementation/condition 下必须允许并存。

下一阶段应把 report-set 条目改为按 `result_key_digest` 唯一绑定，并要求：

- report set 与 statistical binding 的 result-key 集完全相等；
- 每个 binding 只能对应一个 report 文件；
- D1–D6 六个 score ID 均有覆盖，但不再要求 score ID 全局唯一；
- identity、condition、implementation 和 report bytes 继续逐项 fail-closed 校验。

## 下一阶段最小实现切片

1. 修正 release report-set schema 和验证逻辑，先补六系统/重复 `score_id` 反例测试。
2. 新增 `statistical_report_producer_v1.py`：
   - D1：调用 `organization_report_from_payload`；
   - D2/D4：调用 `analyze_dimension`；
   - D3：调用 `d3_relation_report`，保留 obligation ledger；
   - D5/D6：要求 `FixedCatalogV3`，调用冻结目录分析；
   - 统一转换 `valid/range/incomplete/unstable/na`，不把 diagnostic/synthetic 自动升级为 paper candidate。
3. producer 必须接收冻结 identity、support、input/artifact refs 和 event-ledger bytes；缺任一项直接 fail-closed。
4. 生成 statistical-report binding/report-set，并让 paper result index 与 release manifest 消费同一组 result keys。
5. 先跑改动文件 focused/adjacent 测试；形成提交/推送边界时才运行一次全量 benchmark。

## 本阶段未完成、未声称完成

- 未新增 producer 源码或报告 artifact。
- 未生成真实 evaluator terminal material。
- 未运行 Isaac/CUDA/GPU，也未将 CPU、synthetic 或 diagnostic 结果升级为论文数据。
- 未改变任何 formal gate。

## 证据与后续交接

- 基线：`origin/HeteroCoord@f0b760ae1f0c1b6c404f5c22ef490753fc9e506e`。
- TeamWeaver 已接入并由独立复核通过；本阶段不重复合并。
- 统计生产器只读审计任务：`STATISTICAL-PRODUCERS-DESIGN-V1`；其 handoff 应补充逐 D1–D6 字段映射后再开始实现。
- 语义地图、真实机器人池运行时证据和 evaluator terminal receipt 仍是进入真实论文结果的外部前置条件。
