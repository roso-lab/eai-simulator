# 固定面板义务状态归约修正

## 权威与实现

`PREDICATE_BINDING_20261023.zh-CN.md` 修订 V2 第 8 条规定：有完整观测而未达成的
`unreached` 义务计 0；评估记录缺失不能据此计 0。`protocol_v3.py` 与
`receipt_adapter_v3._obligation_rows` 已采用这一语义，但 `paper_panel_v1` 曾把
`unreached` 错转为 unknown。这会使同一收据的普通归约与论文面板得出不同结果。

现固定面板映射为 completed=1、violated/unreached=0；unknown/incomplete 为未知。
不修改原收据或 evaluator 进程状态。新增 20 个参数组合覆盖四种关系和五种义务状态，
并验算固定权重面板：十二个等权观测中的一个已知零得到 11/12 的确定点，
一个未知得到 [11/12, 1]，不删除样本或重分配权重。

## 验收与边界

ZCode 会话 `sess_5d472acd-6ba2-47af-b83e-66080f87dd87` 已报告分文件结果：
occupant binding 6 passed、results pipeline 7 passed、paper panel 26 passed、
provenance hardening 3 passed。随后同一会话直接执行以下命令（无管道），
得到 **42 passed in 0.29s，EXIT=0**：

```bash
python -m pytest -q benchmark/tests/test_receipt_occupant_binding.py benchmark/tests/test_results_pipeline_v3.py benchmark/tests/test_paper_panel_v1.py benchmark/tests/test_provenance_hardening_v3.py
```

旧版合并测试 22 passed 发生在新增 20 项之前，不是当前版本结果。
当前源码 SHA-256 为 `f873d7c9bb927d3278909baac7a595b38204b3d1435c9302cbac7f8ef5dcbc4f`；
测试文件为 `64cfd4bf7356e17cf6603a0bd888cd3f7601b85b2a28b08afdbf37513ddb6768`。

本修正不代表完整 oracle→义务映射已完成：现有适配器仍消费 facts.success 和
facts.obligations，oracle summary 尚不足以强制验证它们；绑定关系及完整生产纵向切片
仍待实现与验收。真实 evaluator 收据、独立校准、运行时资格证据仍缺。
正式资格门全部 false，Goal 保持 active。

## 反思与交接

不能将所有未达成统一称为未知，也不能用摘要附件声称完成科学归约。
后续先把判定原因、计分角色与义务映射绑定到生产入口，再验收完整 export/replay；
不要为绕过输入检查而改写合成收据中的任务身份或区域语义。
