# FMC-ATA 适配修复与双 UR5 验收

日期：2026-10-28。结论：**原版 FMC-ATA 的多机器人完整日程已接入宿主执行层并通过双 UR5 有界验收；原算法源码未修改。**

## 修复内容

- 保留原始 `solver_fmc_distributed_asy.FMC_ATA`，只修复 adapter 输入、原生日程消费、反馈校验和宿主退出收据。
- 适配器现在校验 runtime roster 与任务包一致、任务/机器人 ID 唯一、原生输出覆盖完整任务、分配和日程身份一致。
- 新增 `NativeFMCScheduleSession`：原生求解只调用一次，保存每个机器人的原生日程；每轮只公开各机器人的队首任务。
- 真实 `ExecutionUpdate` 完成后推进队列；反馈批次先整体校验身份、重复项和成功/失败码，再原子提交。失败停止宿主计划，不重试、不重竞拍、不伪造成功。
- `replan` 仅消费原生日程剩余队列，不再次调用 FMC-ATA，也不声称原生算法发生了动态重规划。

## 测试与实跑

- 当时 FMC adapter 回归：**23 passed，3 xfailed**。3 个 xfail 是原版单任务停滞、部分 schedule 等已确认上游限制，不是通过伪造结果掩盖。
- 宿主契约测试：**7 passed**。
- 双 UR5 实跑：`physical_001/receipt.json`，**2/2 completed、原生完整 schedule、进程正常退出、输入完整性通过**。
- 原生决策分配：`move_left -> ur5_1`、`move_right -> ur5_2`。
- 同场景共享物理时钟：4,853 个物理步；两项任务同时活动 4,453 步，两臂实测同时运动 3,728 步（18.64 秒）。详细数值以 receipt 为准。
- 两项任务均 `pick=true`、`place=true`、抬升约 0.135054 m、最终误差约 0.0000113 m、attach/release=1/1。
- 物理语义为 `idealized_follow_proxy_pick_place; not contact grasp`，符合当前理想化逻辑验收设定。

证据目录：

- `physical_001/receipt.json`
- `physical_001/native_decision.json`
- `physical_001/move_left.json`
- `physical_001/move_right.json`
- `physical_001/shared_ticks.jsonl`
- `adapter_tests_final2.xml`
- `host_tests.xml`

## 未掩盖的上游限制

单任务输入仍可能触发原版 FMC-ATA 的消息收尾停滞：原生分配和日程已经形成，但 task 的 phase-II 完成标志未置位，mailer 在空消息队列等待。诊断记录见 `single_stalled_state.json` 与 `single_stack.log`。这是原版消息去重/收尾问题，adapter 不直接修改它。

该次有界执行覆盖的范围是：独立任务、单能力、单机器人单任务、完整原生 schedule、静态 host 消费和理想化执行。任务依赖、分数可分割的物理技能、自动失败重竞拍、FMC 内部 execution feedback/replan 尚未声称支持。宿主反馈属于执行计划消费，不会被伪装成送回 FMC-ATA 的原生反馈。

## 发布整理复核

`records/algorithm_adapters/publication_20261028/fmc_ata.xml` 为合并后完整方法目录复跑：65 passed、3 xfailed。早期超时、失败和 xfail 记录保留原样；单个成功物理用例不能证明所有输入可靠。此次发布未重跑物理仿真。
