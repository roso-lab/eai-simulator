# D1–D6 结果计算与输出闭环（2026-10-25）

## 已完成

`benchmark/results_pipeline_v3.py` 接收一个冻结的 normalized results package，先校验论文提交、scorebook、catalog/plan digest、参数绑定和 D1–D6 输入结构，再复用 `metrics_v3.py` 与 `composite_v3.py` 计算结果。计算顺序和论文定义保持分层：D1–D6 各自保留 `valid/na/incomplete/range` 状态，D2 在 composite 中使用 `p0 * S_D2`，D5 对低于 family floor 的 family 做显式排除披露，D6 将失败运行按固定目录规则记为零贡献，缺失项不会被重加权。

每次报告包含：

- D1–D6 point/status/support/reason；
- composite point 与 16 个 profile vertices 的范围；
- paper/scorebook/catalog/plan/input digest 和 estimator source digest；
- `sanity_checks`：分数边界、D2 交付分不超过条件分、composite 位于 profile range、D5 排除披露、support weight 边界；
- `formal_gates` 固定为 `rankable=false`、`paper_score_eligible=false`、`physical_qualified=false`。

输出目录包括 JSON、CSV、Markdown、coverage/provenance、`input_manifest.json`、可重放的 `input_package.json` 和文件级 `output_manifest.json`。`verify_export()` 会重算输入包、核对所有文件摘要，并拒绝路径穿越、反斜杠和 symlink 条目。

## 模拟数据验收

诊断 artifact：

`artifacts/results-pipeline-20261025-run14/`

本轮再次用同一 normalized synthetic package 生成并验证
`artifacts/results-pipeline-20261025-run15/`；`verify_export()` 重算输入包并
核对文件摘要通过，输出与 run14 逐文件一致，说明未来数据接入时计算边界是
确定性的。

固定 synthetic fixture 的结果为：

| 量 | 结果 |
|---|---:|
| D1 | 80.5 |
| D2 | 75.0 |
| D3 | 78.3333 |
| D4 | 80.0 |
| D5 | 91.1111（短 family 显式排除） |
| D6 | 50.0（失败运行计入零分） |
| composite | 73.3241 |
| profile range | 67.2083–83.3148 |

结果计算、独立论文算术、边界反例、未来输入包 CLI、导出重放、篡改拒绝和安全 manifest 检查已通过 focused suite；当前结果管线测试为 `24 passed`，并通过 `py_compile` 与 `git diff --check`。更大的 benchmark 回归仍受旧论文 fixture 与当前环境缺少 `torch` 的前置问题影响，详见本页末尾复核记录。

## 使用边界

该链路已经达到“未来 evaluator 产出同一 normalized schema 后可直接计算并生成可审计结果”的软件标准，但当前 artifact 仍是 `diagnostic_only`。真实物理运行、evaluator/receipt 完整性、正式目录覆盖、D5 certified family、D6 calibration、release/precision gate 完成前，不得把输出升级为论文正式分数、rankable 结果或物理资格。

## 本轮复核记录

- 结果管线 focused suite：`24 passed`；GPU runner/native/backend 相关专项：`53 + 91 + 59 passed`。
- `benchmark/tests` 全量在当前通用 Python 环境下仍有两个既有前置问题：一个生命周期测试缺少 `torch`，另有 22 个 catalog mapping fixture 使用旧论文提交号而被正确拒绝为 stale；这些不是本轮结果管线改动引入的失败。
- 因此本轮接受范围仍限定为 normalized-results diagnostic deployment，未打开任何 formal gate。
