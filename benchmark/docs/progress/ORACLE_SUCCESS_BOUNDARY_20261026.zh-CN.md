# Oracle 成功条件生产入口校验

## 实现

`receipt_adapter_v3.normalize_receipts` 在存在 case manifest 时，于 case 观测 ID 校验后，
执行 `_validate_oracle_success_consistency`。生产入口强制提供 case manifest，因而此检查
先于固定面板、计算与导出执行。未绑定 case 的历史诊断归约未升级为生产路径。

24 锚点从 `DRAFT_BINDING_REGISTRY` 的 `safety_success/success_condition` 分类读取；
六试点按绑定 V2 为 `liv-p3-01/pallet_requires_two_carriers` 登记安全条件。
不依赖 verdict.evidence 是否携带分类字段，因为缺事件 verdict 可能没有这些字段。
检查发生在 D3 同源去重之前，防止服务类同源谓词覆盖安全谓词身份。

- 安全违规、截止期未达，以及完整观测下的 success-condition unreached，均不得配 success=true。
- 证据缺失或不变量不足不能作为成功或失败点估计通过；保留原收据，入口报错。
- 记录明确的失败允许继续归约，不中断仿真实验、不抹去失败证据。
- 同一收据中 D6.success 必须与 facts.success 一致。
- 普通时序/资源谓词和诊断级重叠不被此校验重新定义为成功条件。

## 验收证据

ZCode GLM-5.3/max 会话 `sess_5d472acd-6ba2-47af-b83e-66080f87dd87` 执行：

```bash
python -m pytest -q benchmark/tests/test_oracle_success_boundary.py benchmark/tests/test_receipt_occupant_binding.py benchmark/tests/test_results_pipeline_v3.py benchmark/tests/test_paper_panel_v1.py benchmark/tests/test_provenance_hardening_v3.py
```

结果 **51 passed in 0.29s，EXIT=0**。新增 9 项使用真实 oracle，不 mock 判定结果，
覆盖承托失败、完整/不完整流的缺事件原因、截止拍/超一拍、D6 成功冲突、诊断重叠。
本次并非全仓回归或完整生产纵向切片验收。

## 未完成与交接

这是一致性拒绝边界，不是完整的任务成功判定器：通过检查不证明所有终局成功条件满足。
D3 仍消费 facts.obligations；需要登记 predicate→obligation 映射、同源消费与完整性，
不能按谓词数量直接冒充论文的义务分母。完整 artifact→receipt→panel→export→replay
仍需重新验收；旧合成夹具的 zone/entity 绑定不能改写为机器人身份以绕过校验。
真实终局收据、独立校准及运行时资格仍缺，正式资格门全部 false，Goal active。

反思：依据注册表分类，比依据正例 verdict 的元数据更可靠；否则缺事件路径会漏掉
安全或截止期门。D3 去重与任务成功判断必须分别消费证据，不能用同一条去重后摘要代替。
