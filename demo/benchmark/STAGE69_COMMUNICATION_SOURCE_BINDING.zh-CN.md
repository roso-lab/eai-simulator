# Stage69 通信五实验族 source-binding 契约

日期：2026-09-26
基线：`6a6dd61`
状态：raw-only 验收；未接入正式六维聚合器

## 目标

在计算任何通信适应分数之前，先冻结并校验通信实验的输入矩阵与来源归属。Stage69 新增 `goal_communication_source_binding.py`，它只负责验证，不计算跨实例统计、保持率阈值或综合分。

## 事前 expected-cell manifest

expected manifest 必须与 observed rows 分开提供，并在执行前冻结。其主键为：

```text
task_id × base_instance_id × family × condition_id × channel_seed
```

manifest 绑定任务、合同摘要、评价器、预注册任务表现指标 M、排除消息清单、archive 与 shared transport 的 schema/version/runtime ref，以及每个 condition spec 的摘要。五个规范 family 为：

- `fixed_delay`
- `iid_loss`
- `capacity`
- `jitter`
- `burst_loss`

observed rows 不能扩展 manifest。未观测 expected cell、额外 observed cell、重复主键及 episode 跨 cell 重用分别进入 `missing`、`unexpected`、`duplicate` 或 `rejected` 账本，缺失不会填零或删除。

## Native communication 专属来源门禁

通用 contract/episode/evaluator/termination source 不足以证明真实通信链路。每行还必须绑定：

- expected manifest 与 condition spec 摘要；
- shared communication report 与 progress source 摘要；
- native archive/evidence ref、archive 摘要、schema/version；
- shared transport report schema/version/runtime ref；
- replay 已验证状态；
- episode 启动端点/池绑定摘要；
- 可信 `host_event` 或 `native_receipt` 终止锚。

这些字段全部通过也只得到 `accepted_raw_only`，状态不命名为 `formal_eligible`，更不代表业务成功、正式 Y_i 或发布资格。validator 不回显 observed raw payload；只保留规范 JSON 摘要和 archive ref，避免被拒输入中伪造的 `release=true` 被误读为 validator 自身结论。

## 任务表现与传输伴随量隔离

manifest 为整个矩阵选择一个预注册任务表现 M：`SR_comm`、`MCR_comm`、`AUP_comm` 或 `e_step`。synthetic delivery 只能留在 `transport_companion`，不能回填任务 M。

- M 缺失但 companion 存在：`diagnostic_only / na_task_performance`；
- M 存在但 transport、cost 或 native provenance 缺失：rejected；
- `SR_comm=Q(H)`：保留 `proxy_only` 原始诊断，`qualified_metric_value=null`；
- 合法零消息运行可保留 `B_offer=0/B_link=0`；通信报告缺失仍是 N/A，两者不合并。

capacity manifest 只接受 v2：明确 `bits_per_second`、真实 unlimited reference 或正有限容量，并拒绝 legacy v1 的 byte/s 语义混入。

## 验收证据

相关回归命令覆盖 source binding、任务通信指标、聚合器、通信 analysis、capacity v1/v2 和独立复算：

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  source/EAI/test/test_benchmark_goal_communication_source_binding.py \
  source/EAI/test/test_benchmark_goal_communication_metrics.py \
  source/EAI/test/test_benchmark_goal_metric_aggregation.py \
  source/EAI/test/test_benchmark_communication_analysis.py \
  source/EAI/test/test_benchmark_communication_capacity.py \
  source/EAI/test/test_benchmark_communication_capacity_v2.py \
  source/EAI/test/test_benchmark_goal_independent_recomputation.py
```

结果：`122 passed in 2.64s`。

随后扩大到全部 `communication` 命名测试（runtime、pools、publication treatment、archive、host、tool）并保留聚合器与独立复算，结果为 `332 passed in 3.65s`。

附加检查：

- `git diff --check`：通过；
- `py_compile`：通过；
- 保护文件未纳入修改范围。

## 保留边界

- `build_protocol_metrics_report()` 未调用本 validator；
- `communication_adaptation`、`dynamic_reconfiguration`、`cross_episode_learning` 继续为 `na_dimension_source_not_wired`；
- 没有新增 family 权重、指标阈值或综合评分公式；
- 没有生成真实 native episode 或补写缺失 archive；
- `release=false`、`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。

## 下一步

下一阶段可为真实 runner/archive 增加 expected manifest 和 source-binding producer，使一个事前合法的通信矩阵能够生成上述原始行。只有当真实 native 来源闭包、正式全局终止谓词及统计聚合契约同时完成后，才讨论把通信维度从 N/A 升级；Stage69 本身不提供升级依据。
