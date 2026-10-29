# room_06 任务设计定稿（逻辑 benchmark）

room_06 使用语义地图 revision `60878828dc38412496b1b322f687a829`，逻辑时钟为 `0.02 s/tick`。任务族是“跨层图书接力与重物联合搬运”：地面机器人从 `floor1-bookshelf` 发起书籍接力，空中机器人承担 `room06_transition_handoff` 阶段，地面机器人在二层书架完成归位；T3/T4 再要求两台具备 `joint_carry` 的二层地面机器人共同完成重物搬运。

当前地图的 ground topology 将 1F 与 2F 分成两个连通分量，尚无可验证的楼梯/跨层边。因此 `room06_transition_handoff` 是逻辑测试代理，contract 中固定标记 `logical_proxy_pending_physical_witness`。这使任务可以用于协作协议、资源竞争和通信诊断，但不构成 Isaac Sim 或真机跨层资格证明。

## 难度与条件

| 档位 | 义务 | 关注点 |
|---|---|---|
| T1 | 1 本书的 pick → aerial handoff → 二层 place | 基本角色匹配、先后约束、报告协议 |
| T2 | 3 本书的串联接力 | 多阶段依赖、能力过滤、阶段性重规划 |
| T3 | T2 + 两台机器人重物联合任务 | `distinct_group`、二层承载能力、两成员同步屏障和容量 1 的联合搬运区 lease |
| T4 | T3 + C3 通信压力与第二次 handoff 后的空中机器人 availability loss | 丢包重试、通知阻塞、失联后的可用性恢复 |

条件 C0–C3 只改变通信传输模型；任务难度由 T1–T4 合同决定。T4/C3 在固定 seed=1 下会产生可重复的丢包和报告重试，并在 `book_2_handoff` 报告送达后移除一个空中机器人。所有未来义务仍由 evaluator ledger 管理，机器人只能看到当前已释放的目标和资源状态。

## Checkpoint 与里程碑

逻辑 checkpoint 使用通用 runtime 的 `checkpoint`、`restore`、`replay`、`fork/branch`。任务阶段点为：

`cp_00_spawn_valid` → `cp_01_books_assigned` → `cp_02_relay_started` → `cp_03_transition_handoff` → `cp_04_books_complete` → `cp_05_joint_carry_started` → `cp_06_joint_carry_complete` → `cp_07_closeout`。

每个阶段由任务状态、资源账本、通信队列和事件日志共同判定；单纯到达语义点不等于义务完成，必须完成报告传输。两台重物机器人使用同一 `distinct_group` 与 `cooperative_group`：两名参与者都到位后，evaluator 只申请一次容量为 1 的 `joint_carry_capacity` lease，并记录同步开始/完成。room06 的排队量不被强行制造；candy_shop 的 `handoff_window` 提供独立的 P4 排队对照。

## 论文指标映射

- D1：检查初始能力、域匹配、目标可达性和逻辑代理的可行性声明。
- D2：比较 C0–C3 的成功保持率、通信延迟、丢包和报告重试。
- D3：记录 relay 先后关系、联合任务 distinct-group 约束、资源占用与等待。
- D4：检查第二次 handoff 报告后的 availability-loss 和剩余机器人重规划。
- D5：当前没有经过认证的跨场景配对，固定为 `NA`。
- D6：物理时间、跨层路线和传感器资格需要独立 Isaac/真机证据，逻辑轨道保持 `pending`。

`paper_score_eligible` 保持 `false`，直到跨层路线 witness、机器人能力验证、独立物理证据和配对条件目录全部闭合。
