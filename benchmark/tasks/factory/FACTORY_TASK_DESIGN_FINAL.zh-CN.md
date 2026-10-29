# factory 任务设计定稿（Isaac-free 逻辑轨道）

版本：`factory.final.v1`。运行器使用冻结语义地图和 `0.02 s` 逻辑时钟，评测机器人分配、能力约束、报告传输、资源/交接顺序和硬时间。该轨道不替代 Isaac Sim 的碰撞、传感器、控制器或真机证据。

## 地图与机器人池

地图目录为 `usd/scene/Production and logistics/factory/semantic_map/current`，地图 revision 和文件摘要以 `manifest.json` 为准。四个地面机器人从 `start_1..start_4` 出发：Carter 负责事故采样，M20+D1 具备机械臂和按钮/灭火器能力，lite3 与 scout 负责巡检和运输。`Inspection point_1..2`、`Workstation_1..4`、`Machine_1..2` 是巡检点；`Fire alarm`、`2F Fire Extinguisher`、`Transfer point`、`fire`、`help` 是事故协议点。

## 任务协议

T1 只发布八个巡检义务。完成 `Machine_1` 的巡检后，评估器幂等触发 `fire_event`；T2 继续发布事故响应：Carter 到达阻塞点后等待 `help_clear`，并行完成清障、火情采样、按下报警器和灭火器直送。T3 把灭火器改为 `pick → release at Transfer point → receive → deliver`，要求交接双方和物体所有权连续有效。T4 在 T3 上施加 C3 通信压力，报告、通知和重规划均必须经过通信模型。

未来事故义务数量、机器人配额和具体隐藏任务 ID不进入算法公开观察。公开观察只包含当前目标、机器人能力/位置、通信状态和已登记资源状态；事件触发后才公布下一阶段可见目标。

## 四级难度与硬时间

| 难度 | 任务组成 | 关系/指标 |
|---|---|---|
| T1 | 八点巡检 | P1 分工、覆盖率、路径和 makespan |
| T2 | T1 + 火情响应、阻塞解除、直送 | P2 先后、能力可行性、发现者等待 |
| T3 | T2 + 灭火器交接 | P2 交接、物体身份、连续持有 |
| T4 | T3 + C3 | P4 通信延迟/丢包、报告阻塞和重规划 |

每个难度/通信条件的 `hard_deadline_tick` 冻结在 `factory_task_manifest.v1.json#/timing_policy/hard_deadline_ticks`，由评估器统一执行，与被测算法无关。deadline 以内完成才算成功；超出一拍即产生 `hard_deadline_exceeded`，不得用更长 horizon 把失败改写成成功。当前冻结值由十次确定性重复的 P95 加固定冗余得到，并由 `factory_timing_calibration.v1.json` 留存校准证据。

## 检查点与终局

保留 `spawn_valid`、`patrol_ready`、`machine_1_fire_triggered`、`carter_blocked`、`help_clear`、`fire_sample`、`fire_alarm`、`extinguisher_picked`、`handoff_release`、`handoff_accept`、`extinguisher_delivered` 和 `terminal` 检查点。报告送达才改变任务状态；checkpoint restore、fork 和双轨 replay 必须保持事件摘要一致。

核心诊断指标包括任务完成率、硬时间是否满足、首次事故发现时间、发现者等待、响应到达时间、无效能力分配、交接/物体放置成功、通信延迟/丢包/重试、资源竞争、路径距离、makespan 和 replay 等价性。没有独立物理证据、算法源代码门槛和配对证据目录时，`paper_score_eligible` 保持 `false`。
