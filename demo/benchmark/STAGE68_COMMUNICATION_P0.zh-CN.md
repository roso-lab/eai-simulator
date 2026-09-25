# Stage68 通信指标 P0 修复与阶段交接

日期：2026-09-26
分支：`codex/benchmark-stage57-oracle-integration`
验收基线：`HEAD=0b3746b`（本阶段源码改动尚未提交时的工作树验收）

## 本阶段目标

承接 Stage67 viewer 的通信指标 P0，只修正离线统计层的边界语义，不把通信维度接入正式六维聚合，也不改变任何发布资格。

## 已完成改动

### 1. `SR_comm=Q(H)` 的机器可读代理边界

`_task_performance()` 现在在任务表现输出中同时提供：

- `sr_basis: "Q(H)"`
- `proxy_only: true`
- `global_termination_predicate_available: false`
- `SR_comm_formal: null`
- `SR_comm_status: "offline_terminal_success_proxy"`

因此 `SR_comm` 的数值仍可用于离线 Q(H) 诊断，但不能仅凭 `SR_comm=1.0` 推断正式终止谓词下的 `Y_i=1`。

### 2. retention/C 阈值尾点不外推

`retention_curve()` 与 `capacity_thresholds()` 现在只把最后一个有已知性能的点作为已测试边界。若末尾预注册条件或容量点为 `None`：

- 不再报告 `above_tested_maximum`；
- 返回明确的 `na_trailing_condition_missing` 或 `na_trailing_capacity_missing`；
- 不输出虚假的边界条件/容量。

没有任何已知性能点时分别返回 `na_no_known_performance`。

## 验收证据

使用项目环境 `/home/pupil/miniconda3/envs/env_isaaclab/bin/python`，并关闭 pytest 自动插件：

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  source/EAI/test/test_benchmark_goal_communication_metrics.py \
  source/EAI/test/test_benchmark_goal_survival_analysis.py \
  source/EAI/test/test_benchmark_goal_metric_aggregation.py
```

结果：`72 passed in 0.47s`。

静态检查：

- `git diff --check`：通过；
- `py_compile`（通信模块及 focused test）：通过；
- 阶段反思代理独立复核：通信/生存 focused `38 passed`，聚合/声明成本/证据 `70 passed`；
- 反思报告 SHA256：`69e16d9cea41266da9a03abe1fd76a60e4d3e775b05840102178f55772afe171`。

## 未完成边界（必须保持）

- `build_protocol_metrics_report()` 的通信五实验族仍未完成正式 source-binding；`communication_adaptation`、`dynamic_reconfiguration`、`cross_episode_learning` 继续为 `na_dimension_source_not_wired` 或 raw-only。
- `aggregate_survival()` 虽已有 `base_instance_ids` 支持，正式 reader 契约仍需强制提供并补同一 base instance 多 episode 的 bootstrap 回归；省略 ID 的调用不能当作正式统计证据。
- 学习 R/C 尚未接入正式聚合器，缺少同一 base instance、同首轮历史、同任务机会的可追溯配对证据。
- Stage66 只证明离线归档可复算（`Q(H)=1.0`、`AUP=0.4832`）；部署 epoch、终止记录和通信报告缺失，不能转成正式 scoreable/release 资格。
- `release=false`、`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false` 全部保持不变。

## 协作与失败记录

原计划的 Sol 工程代理 `comm-metrics-p0` 在首次工具调用前因服务端 `429 Too Many Requests / retry exhaustion` 进入 blocked；这不代表代码或测试失败。主代理接管了限定文件范围内的实现与验收。阶段反思由只读 `team_process_reviewer` 完成，未修改源码。

## 下一阶段建议

先冻结通信正式输入矩阵与 source-binding 契约：
`base_instance_id × condition/family × channel_seed × episode/task`，同时严格区分任务表现 M 与 synthetic delivery companion。随后把 survival 的 base-instance 约束和学习 R/C 配对接口接入 reader；在 native provenance、终止谓词和发布门禁齐备前，不提升任何正式维度或发布标志。
