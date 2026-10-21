# Tool 诊断证据适配层

更新日期：2026-10-21

本适配层把分支工具输出的 `heterocoord.diagnostic_trace_evidence.v1` 转换为 HeteroCoord
项目内部可审计的证据记录。它解决的是“工具诊断结果如何进入项目 ledger”的问题，
不是把工具原型变成正式论文 runner。

## 数据边界

适配器只接受同时满足以下条件的 tool envelope：

- `evidence_scope=diagnostic_only`；
- `rankable=false`；
- `process_failure_scope=shared_session`；
- `formal_measured_qualified=false`；
- `native_full_state_qualified=false`；
- `process_isolation_qualified=false`；
- 含有 `missing_branch_ids`、`extra_branch_ids`、`structural_mismatches`、
  `max_numeric_error`、`tolerance`、`numeric_equivalence` 和 `exact_equivalence`；
- 包含 candidate/reference 路径、experiment identity 和 shutdown status；
- envelope digest 与 experiment identity digest 可以重新计算并匹配。

任何缺失、正向资格标志、摘要不匹配或错误故障域都会被拒绝。适配器不会从 JSON
推断物理成功，不会把 observable restore、numeric equivalence 或 symbolic witness
转换成 native full-state qualification。

## 与项目 ledger 的关系

`DiagnosticEvidenceV3.append_to_ledger()` 同时检查两个身份来源：调用方显式提供的
`ledger_identity` 必须逐字段匹配当前项目 `RunKey`，envelope 自身的
`experiment_identity` 也必须把 base/system/condition/repeat/checkpoint/branch role
逐字段绑定到同一个 `RunKey`；提供时还会核对 untreated history。调用方不能再用一个
与 ledger 相符的外部 identity 掩盖 envelope 自身错配。通过后只追加一种明确的事件：

```text
diagnostic_trace_comparison
```

该事件仍属于 branch-local ledger，并保留原始比较信息。它不会生成 `MetricResultV3`，
不会改变 D1–D6 的 support、weight、coverage、identification range 或 release status。
因此它可以支持 P3 的“差异证据可追溯”，但不能支持正式排名。

## 当前证据解释

原 tool 的真实 Isaac 证据目前只能表述为：

- shared-session observable diagnostic；
- fresh-process numeric equivalence under a registered tolerance；
- exact equivalence 未成立；
- Kit shutdown timeout 的诊断主体已写出，但不是 clean pass。

以下字段在正式 task driver、native hidden-state 覆盖、strict isolated replay、clean
shutdown、物理边界起点技能验证以及正式 catalog/calibration/baseline 证据完成前，继续保持
`false`：

```text
formal_measured_qualified
native_full_state_qualified
process_isolation_qualified
```

## 验证

适配层定向测试：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
-m pytest -q \
benchmark/tests/test_diagnostic_evidence_v3.py \
benchmark/tests/test_artifact_verifier_v3.py \
benchmark/tests/test_release_v3.py
```

当前结果：适配器/fixture 定向测试 `58 passed`；选定的项目运行边界回归为 `793 passed in 5.28s`。

2026-10-21 的独立验收曾构造出 reference envelope 追加到 fact ledger 的反例；修复后
同一反例以 `DiagnosticEvidenceError` 拒绝。`from_tool_mapping()` 现在也直接验证
evidence、experiment identity 和 provenance digest，不能作为文件读取路径之外的摘要绕过入口。
验证结果保存为深度不可变结构，直接构造的未验证对象不能写入 ledger 或输出为已验证 envelope；
tool v1 的 ASCII canonical JSON、非 ASCII 文本和整数数值表示均有跨仓库回归覆盖。

这组测试证明的是 envelope 校验、摘要完整性、ledger 接线和 fail-closed 语义；
不证明真实 Isaac 物理资格。
