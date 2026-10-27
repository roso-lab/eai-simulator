# D1–D6 统计证据契约加固收尾（2026-10-27）

## 结论

统计生产链的三个 P0 证据缺口已关闭：producer 不再只绑定摘要字段，不再把未知支持数伪装成零，也不会因某个合法但不可估计的结果键而终止整批生产。此次变更是接口不兼容的 v2 契约升级；既有 v1 statistical input/report/binding/index/release 工件必须从原始测量输入重新生成，不能原地改名冒充 v2。

正式资格门仍全部为 `false`。本阶段使用合成测试夹具验证计算和证据契约，没有把它们升级为论文数据。

## R1：完整输入、账本和实现绑定

- statistical input 冻结完整 reducer payload、完整结果身份、evidence scope、reducer method、payload/input digest 与 event-ledger 引用；
- event ledger 逐 run 绑定 `run_id / instance_id / repeat_id / evidence_digest`，并绑定 authoritative support registry；
- producer/reducer 源文件集合拥有内容摘要，代码变化会使旧输入 fail-closed；
- report 保留原始 reducer output，非有限数使用显式 tagged JSON 编码，不再丢失方法输出；
- index 与 release 重新读取并校验 input、ledger、report 三者的内容寻址关系。

## R2：零、未知与不适用分离

`paired_instances`、`common_support_instances`、事件、竞争事件和删失计数采用三态：

- `observed(value)`：权威登记确实给出计数，包括真实零；
- `unavailable(reason)`：本次证据不能确定；
- `not_applicable(reason)`：该估计器不适用。

`method_failure` 若仍携带 reducer 可消费的已知观测，则保留在 metric-eligible 分母中；它不再被误当作未知数据排除。

## R3：逐键不可估计

权威 reducer 成功运行但返回 `status="na"` 时，该键生成 `entry_state="not_estimable"`、原因、支持数和原始 reducer output；同批其他结果键继续生产。畸形输入、缺文件、身份/摘要不一致、非法 ledger 或 reducer 异常仍使整批 fail-closed。

## 写入事务修复

主审查补充修复了一个写入边界：producer 写完工件后的下游自校验若失败，现在会删除本轮新建的全部 report/binding/report-set 文件，再返回错误。反例测试通过注入下游拒绝确认输出目录不残留半成品。

## 验收证据

- Sol 工程提交：`11b6f03ea141e461ec4180e9d99cb3cae6f07e29`；
- Sol CPU focused：3 个测试文件 `91 passed`，`py_compile` 与 `git diff --check` 退出码均为 0；
- ZCode GLM-5.3/max 独立复验同一提交：`91 passed in 21.60s`，退出码 0；
- 主审查事务修复后，单文件 focused：`42 passed in 22.98s`，退出码 0（比原提交该文件新增 1 个回滚反例）。

集成提交上的三文件 focused 为 `92 passed / 0 failed / 0 errors / 0 skipped`，退出码 0。推送边界只执行了一次仓库全量：`1046 passed / 1 failed / 0 errors / 0 skipped`；唯一失败是 room_06 场景注册改动后，相机注册派生清单仍绑定旧 `env_builder.py` 摘要。清单摘要更新后仅复验该失败项，结果 `1 passed`、退出码 0；未为制造“一次全绿”叙述而重复其余 1046 项。
