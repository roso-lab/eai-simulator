# Subway 论文对齐审计

subway 采用 `subway_search_rescue_v1` 任务族和 `0.02 s` 逻辑时钟。当前结果仅是 Isaac-free 诊断，`robot_validated=false`、`physical_qualified=false`、`paper_score_eligible=false`。

## 任务与关系

- T1 只要求语义点发现、位置报告和地面确认，主关系为 P2。
- T2 增加地面引导到 `assembly_point`，验证确认到引导的先后关系。
- T3 固定启动两个 generator；原地 `pick → place` 成功后，尚未被地面机器人抵达的人员进入 `deadline_suspended`。
- T4 对尚未 ground contact 的人员增加固定 `extension_s`，ground contact 后还必须在 `assistance_window_s` 内完成集合点引导。

所有 discovery 都由绑定语义导航点到达触发。空中发现只发布位置和身份；地面机器人必须实际到达 ground 绑定点才产生救助接触。generator 只作用于尚未 ground contact 的人员。

## 机器人池补充

`CF2X` 是新增的空中观察候选，适合 discovery、位置报告和空中路线诊断；当前不登记 payload、pick/place、generator 操作或人员引导能力。使用 CF2X 的 roster 仍须满足对应 tier 的地面确认、操作和楼梯 witness 合同。

## 证据边界

ground 的 5 条 stairs 边要求 `stair_capable`；逐 episode witness 未完成时只能标记 `feasibility-unverified`。`generator-1/2` 当前没有独立 interaction pose，因此 pick/place 是待 runtime 绑定的逻辑操作协议。集合点出生不代表任务目标已发现。

## D1–D6

- D1：检查空地角色、语义点发现能力、ground confirmation、generator pickplace 和 stair witness。
- D2：比较 C0–C3 下 discovery report、handoff 和重试保持率。
- D3：诊断 P2 handoff、generator 原地操作、P3-S 引导时序与 P4 时间/资源竞争。
- D4：检查空中搜索、地面确认、操作或通信能力丢失后的重分配。
- D5：当前无认证跨场景配对，报告 `NA`。
- D6：报告通信量、路径距离、makespan 和逻辑时间；物理时间仍待独立校准。

paired condition 必须从同一 checkpoint 启动，共享地图 digest、目标集合、实际 roster、等待参数和随机种子摘要。
