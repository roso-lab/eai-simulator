# EMOS 被测系统接入规范 v1（未完成草稿）

当前只是接口探索，尚未接通 capability_runtime 计量端点、all-terminal 条件分配屏障，也未移除 fire_rescue 依赖。下面描述不能作为已实现验收声明。按用户队列，历史资格登记和终局账本→receipt_material 接缝先完成，再完善此规范与实现。

探索性的 initialize 改动已撤下，避免把本地调用计数器误当作能力运行时预算。benchmark/emos.py 保留原适配器行为；以下是待实现的接口需求。

## 边界

EMOS 只负责历史重建后的条件分配与决策输出；benchmark/EAI 负责能力执行、锁服务、物理观测、里程碑、oracle、回放和终局。EMOS 输出不能被当作任务成功或物理收据。

## 初始化

```python
request_after_init = adapter.initialize(H_r, o_r)
```

`H_r` 是 evaluator 提供的历史重建映射，`o_r` 是当前公开观测映射，至少含 `robot_positions`，且必须和绑定名册的 robot IDs 完全一致。初始化受 `initialization_budget_calls` 限制，预算耗尽 fail-closed；初始化完成后才允许条件分配。

## 决策输出

`EmosDecisionAdapter(request_after_init)(task_package, robots)` 返回 `AlgorithmDecision`，包含 typed `Assignment`、未分配任务、条件记录和 provenance。它不能写入物理成功字段。

## 模型端点

`model_endpoint(payload)` 是可选的能力运行时计量端点。payload 只含历史、当前观测、场景 ID 和 robot IDs；端点必须返回 mapping，调用次数由初始化预算计量。模型异常或非 mapping 结果直接 fail-closed；默认 fallback 为无外部模型的确定性 EMOS 分配。

## 锁服务

EMOS 不直接取得或解释 typed-lock grant/deny。锁请求、占用账本和占用谓词属于 benchmark evaluator；oracle 的位置占用账本不因 EMOS 的分配输出或锁应答豁免。

## 当前限制

room_06 × liv-t-04 的合同层 CPU 干跑尚未执行。现有 EMOS 三项旧测试通过不覆盖新初始化接口；不能据此声称预算、历史重建、条件盲化或模型计量已满足要求。
