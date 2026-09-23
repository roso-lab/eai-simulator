# 阶段 23：餐厅资产准入 A2 证据

日期：2026-09-24  
详细交接：`/home/pupil/Documents/Codex/agent-team/workspaces/benchmark-stage22-comm-contract-20260924/handoffs/restaurant-a2-runner.md`

## 冻结输入

v7 候选四个产品文件的哈希与冻结值一致；`Restaurant_Only.usda` 源哈希为
`618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b`。本批没有修改
overlay、resolver、产品字节或用户文件。

## 本机

- resolver/hash：通过。
- source closure：失败。overlay 指向绝对路径
  `/home/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda`，本机不存在；
  materialize 尝试因父目录权限返回 `PermissionError`。
- formal Goal preflight：在 composition gate 失败，未启动 Isaac episode。
- source audit：authoring/effective closure 证据通过，但 `benchmark_ready=false`、
  `execution_validated=false`、`scoreable=false`。

## ljy 隔离树

- resolver、source audit、materialize（`copied=false`）通过，源字节与冻结哈希一致。
- prepare-only formal preflight 在显式只读 `EAI_CONTROLLER_ROOT` 下通过，产品验证和
  manifest 绑定通过；未启动正式 episode。
- 该结果依赖 ljy 原仓 `/home/ljy/eai-simulator` 的源闭包，隔离工作树本身不是
  自包含、可迁移的资产闭包；未设置 controller root 时会明确失败。

## 结论

A2 已产生可复现的双机差异证据，但尚未达到正式 episode 启动准入。本机绝对路径和
权限问题必须由资产部署环境解决；不能改写冻结 overlay、放宽哈希或把平台失败计为
模型错误。发布标志继续保持：

```yaml
scoreable: false
execution_validated: false
business_outcomes_validated: false
```
