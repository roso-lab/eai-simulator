# B3a 可信状态与硬终局谓词合同

这份合同冻结“评测器看到了什么”，不替代剩余任务可行性求解，也不向被测模型公开作者计划。它把五类场景的可信状态来源、实体版本和硬终局谓词绑定到同一个任务包与 oracle 注册表。

合同必须记录：

- 场景与 public task SHA256；
- oracle registration SHA256、资产清单版本和技能合同版本；
- 每个模型事实对应的可信来源、来源版本、证据类别和实体键；
- 每个公开业务结果对应的硬终局谓词；
- 当前实体版本、预注册测量点和可信时间；
- 物理完成、业务回执、持续服务或观察记录的适用证据。

物理状态、业务回执和持续服务分开记录。对象已经稳定到 dock，只差无需抓取能力的接收回执时，状态合同可以完整表达“搬运谓词已满足、交接谓词仍待完成”；抓取成员退出不能仅凭历史见证把该状态改成不可部署。对象尚未到 dock 且唯一合法搬运能力退出时，剩余任务 oracle 才能在完整转移模型证明后返回 `not_deployable`。

正式标签唯一来自 `goal_remaining_feasibility_oracle.py` 的注册模型求解：

```yaml
feasibility_label: deployable | not_deployable
oracle_status: verified | failed
```

可信状态合同本身不产生标签。缺失读回、版本错配、哈希篡改、未注册中间状态或来源无法证明时，grounding 记为 `failed`，标签为 `null`，该测量点退出部署判断分母并单列为平台质量缺口。

五场景当前状态：Factory、Warehouse、House、City 的原生终局读回和模型完备性仍待认证；Restaurant 明确为 `authoring_only` 且 `asset_blocked`。这些状态不会因为合同结构通过而变成 `native_verified`。

当前实现范围：`goal_trusted_state.py` 只做状态绑定和来源读回校验，保留三类发布标志为 false；`GroundedRemainingTaskOracle` 将来源读回失败传给已有注册可达性求解器。它不读取参与者答案、计划或作者 witness，也不推送子任务、指派或时序。

验收顺序：先冻结合同和测量点，再为每个场景补可信 native reader 与终局谓词完整性证据；所有预注册点达到 `oracle_status=verified` 后，才允许把 B3b 结果接入 IRR/FIR 等正式统计。
