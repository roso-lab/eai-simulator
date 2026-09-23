# 阶段 23：五场景 native remaining-task oracle 审查

日期：2026-09-24  
详细证据：`/home/pupil/Documents/Codex/agent-team/workspaces/benchmark-stage22-comm-contract-20260924/handoffs/native-oracle-research.md`

当前五个场景都没有达到正式 `native_remaining_task` / `verified` 条件。现有有限状态
solver、可信状态合同和 fail-closed 标签规则已经正确：证据不足只返回
`oracle_status=failed` 与空标签，不产生第三种答案。

场景缺口如下：

- Factory：已有物体、楼梯和占用读回，但缺生产异常状态、服务记录和维护交接回执 reader。
- Warehouse：已有 object/attachment/occupancy 读回，但缺订单数量、集货归属、dock receipt
  和 closeout 的完整终局绑定；“木箱已到 dock、只差回执”必须是独立状态。
- Modern House：已有物体和占用诊断，但缺稳定停放、整条路径净空和最终撤离的完整终局 reader。
- City：已有人员 ACK、到达、车辆轨迹、报告回执和连续通路诊断，但尚未接入正式剩余任务
  状态 provider 和替代路径完备性证书。
- Restaurant：已有单 Carter 到达→观察→relay→receipt 闭环，但八机候选池、A2 资产闭包
  和多团队映射未准入，仍为 `authoring_only`。

下一批实现应按以下顺序：

1. 为每个场景冻结 evaluator-only model、predicate bindings、outcome→terminal predicates、
   source versions、entity IDs 和预注册 measurement points；不改变题面或 witness。
2. 接入只读 `state_provider` 与独立 source readers；每个 reader 必须绑定可信时间、实体
   版本和证据 hash，不能读取模型 plan 或作者 witness 推导真值。
3. 为 Warehouse dock-before-receipt、House chair-before-books/path、City
   people-arrived-before-report、Restaurant both-observed-before-relay 增加中间态 fixture，
   验证能力退出后按剩余合法路径判定。
4. native reader、模型完备性证书和所有预注册点全绿前，保持 `oracle_status=failed`，不进入
   IRR/FIR、学习或动态恢复分母。

所有发布标志保持：

```yaml
scoreable: false
execution_validated: false
business_outcomes_validated: false
```
