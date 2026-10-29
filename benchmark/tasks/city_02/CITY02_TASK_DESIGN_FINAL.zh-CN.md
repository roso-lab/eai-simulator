# city_02 任务设计定稿（Isaac-free 逻辑轨道）

版本：`city02.final.v1`。本任务使用 `service_city_02` 的冻结语义地图和拓扑，逻辑时钟固定为 `0.02 s`；它用于算法内部比较，不替代 Isaac Sim 的碰撞、传感器、控制器或真机证据。

## 1. 地图绑定与机器人池

地图产品为 `usd/scene/Service and operations/city_02/semantic_map/current`。本次定稿把拓扑中原来未绑定的四个地面节点显式登记为起点：

| 任务标签 | 拓扑节点 | 机器人 |
|---|---|---|
| `start_1` | `node:0083` | M20 |
| `start_2` | `node:0084` | Carter+D1 |
| `start_3` | `node:0085` | B2+Z1 |
| `start_4` | `node:0086` | lite3 |
| `build_01` | aerial `node:0007` | Pegasus |

`semantic-points.json` 中的 `Inspection point_9` 保留两个合法地面绑定（`node:0069`、`node:0074`）。运行时按当前机器人到绑定点的最短可达路径选点；任一点到达都触发井盖事件。`build1` 统一规范化为 `build_01`。

机械臂能力只赋予 `Carter+D1` 和 `B2+Z1`。M20、lite3、Pegasus 是观察/导航机器人。`Carter+D1` 和 `B2+Z1` 在逻辑任务中分别作为一个带机械臂的地面机器人身份，不拆成两个独立机器人。

## 2. 基础巡检与区域覆盖

地面必须完成 `Inspection point_1..9`，Pegasus 必须完成 `build_01..build_12`。这 21 个点的逐点完成是任务成功条件。

区域覆盖是独立协作指标：一个区域列出的任意地面点或空中点完成，即把该区域标记为已搜索。区域映射和 XY 判定半径已冻结在 `city_02_task_manifest.v1.json`，半径为 40 m；它只用于区域覆盖统计，不替代逐点巡检。这样可以同时测量“是否完成全部航点”和“空地是否复用相近视野”。

## 3. 事件协议

### 井盖事件

地面机器人完成 `Inspection point_9` 后，评估器生成一次 `manhole_event`。事件是幂等的，只能生成一次。后续顺序固定为：

1. 有机械臂的机器人领取 `warning_pick`，从 `Warning sign` 取物；
2. 同一机器人继续执行 `warning_place`，把警示牌放到 `Manhole cover`；
3. 放置必须同时满足对象身份、源点、目标点和机械臂能力约束。

无机械臂发现者在报告送达后进入 `hold`，发送请求并原地等待；`warning_place` 完成后释放。若发现者本身有机械臂，则可以成为同一 continuation group 的执行者并自处理。

### 箱子事件

`Inspection point_1` 或 `build_10` 任一点先完成都会生成一次 `box_event`（OR、幂等）。处理顺序为 `box_pick → box_place`，目标是把 `box` 放进 `Trash can`。无机械臂发现者按与井盖相同的等待/通知协议处理；有机械臂发现者可以自处理。重复的第二次观测只记录为普通巡检完成，不复制搬运任务。

## 4. 四级难度

| 难度 | 任务组成 | 主要测量维度 |
|---|---|---|
| T1 | 21 个巡检点 + XY 区域覆盖 | 异构空地分工、路径代价、区域覆盖率、makespan |
| T2 | T1 + 井盖警示牌 | 能力约束、发现者等待、通知、同机器人连续动作、P2/P2-02 顺序 |
| T3 | T2 + 箱子 OR 触发和清运 | 触发去重、机械臂资源分配、物体所有权、源/目标正确性 |
| T4 | T3 + C3 通讯压力、近同时触发、有限等待 | 通讯延迟/丢包、重试、再分配、协作鲁棒性；能力丢失分支可作为单独 D4 条件注册 |

每一级都在 `C0/C1/C2/C3` 四种通讯条件下运行。条件改变通讯传输，不改变地图、机器人池和任务语义。当前逻辑轨道不启用电量限制，避免把电量模型与本场景的能力交接效应混在同一因果轴；如需能量因子，应作为独立的配对实验条件注册。

## 5. Checkpoint 与阶段点

运行时 checkpoint 可以在任意 `0.02 s` tick 保存，并支持 restore、fork、branch 和双轨 replay。任务报告必须经通讯模型送达后才算完成，C2/C3 的重试和等待因此可观测。

建议在结果中保留以下里程碑：`cp_00 spawn_valid`、`cp_01 patrol_started`、`cp_02 manhole_or_box_discovered`、`cp_03 request_delivered`、`cp_04 capable_responder_arrived`、`cp_05 object_picked`、`cp_06 object_placed`、`cp_07 all_patrol_points_complete`、`cp_08 closeout`。这些是评估点，不是额外的导航点。

## 6. 指标与可解释性

核心指标包括地面/空中逐点巡检率、XY 区域覆盖率、首次发现时间、通知延迟、响应机器人到达时间、发现者等待时间、机械臂利用率、无效能力分配数、取放成功率、对象身份保持、重复触发抑制、通讯延迟/丢包/重试、路径距离、makespan、checkpoint/replay 等价性。

这些结果是诊断数据。没有独立物理证据、算法源代码门槛和跨场景配对目录时，`paper_score_eligible` 保持 `false`。
