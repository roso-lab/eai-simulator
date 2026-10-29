# candy_shop 论文检测对齐审计

`candy_shop` 当前是 Isaac-free 逻辑轨道，地图 revision 为
`412b1f18f44c4dab81a99e321618e361`，时钟为 `0.02 s/tick`。它验证任务发布、能力匹配、物料所有权、跨机器人交接、容量 1 资源和通信/可用性故障，不能替代碰撞、抓取、传感器或真机证据；因此 `paper_score_eligible=false`。

## 任务与协作机制

| 档位 | 必需义务 | 论文关系/协作证据 |
|---|---|---|
| T1 | 开门、单订单 ground pick/place | 能力约束、前置关系和终端对象放置 |
| T2 | 两订单的 loader release → receiver accept → place | P2 物料交接、对象身份和接收方互补能力 |
| T3 | aerial inventory scan、ground resource access、两订单服务 | P2/P4；`handoff_window` 容量 1 排队和阶段性重规划 |
| T4 | T3 + 三订单 + C3 或 availability-loss 分支 | P2/P4、丢包重试、空中观察功能失效后的重规划 |

成功条件由 evaluator ledger 判定：订单对象必须保持 `object_id`、source、destination 和 held-by；`pick → release → accept → place` 顺序必须成立；到达语义点或 controller 自报不能关闭订单。门、交接窗口和对象状态在资源账本中闭合后才允许 episode 成功。

## 公开信息边界

manifest 只声明支撑层 `1F`。地图中的 `floor2-shelf-*` 是内部语义绑定，public goal 使用 `shelf_1..3` 等 opaque alias；被测系统看到目标身份与位置、当前资源容量/占用/队列，不看到 `floor2-` 文本、未来订单数量或预设机器人配额。地面机器人能否通过 stairs/ramp 由 topology 与 capability 共同决定，不能由别名推断楼层。

## 条件、checkpoint 与指标

`nominal`、`resource_contention`、`communication_stress` 和 `availability_loss` 只改变配对条件，不改变 T1–T4 合同。C3 保留 report transport：丢包、重试和 `report_wait` 会阻塞任务重规划；availability-loss 在 `inventory_scan_2` 报告闭合后移除 `pegasus_1`。

checkpoint 阶段为 `spawn_valid → gate_open_verified → inventory_observed → handoff_complete → order_closeout`，每个点保存机器人状态、active obligations、资源账本、通信队列、对象状态和地图 digest，支持 restore/replay/branch。

记录 `success`、hard deadline、makespan、对象放置、资源等待/竞争、通信延迟/丢包、availability-loss 和 replay 等价性。D1/D3/D4 仅作为逻辑诊断字段；D2、D5、D6 以及 paper score 需要配对目录、跨场景/物理校准和独立 evidence 后才可升级。
