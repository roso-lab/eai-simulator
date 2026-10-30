# warehouse 任务设计定稿（Isaac-free 逻辑轨道）

版本：`warehouse.final.v1`。任务以订单驱动，在 `0.02 s` 逻辑时钟上评估多楼层搜索、语义域匹配、拣选、重货协作、workspace 汇聚和充电约束。该轨道不替代 Isaac Sim 的碰撞、传感器、控制器或真机证据。

## 1. 地图绑定与机器人池

地图为 `usd/scene/Production and logistics/warehouse/semantic_map/current`，revision 为 `4174a7c1c4ca456ea39569e066a0de35`，semantic binding digest 为 `3401830b10261f695da0e0aa5453dc75cffe34d6c7f96bd97f074ce289a12b37`。ground topology 为 317 nodes/449 edges，aerial topology 为 157 nodes/212 edges；`robot_validated=false`。manifest 仍只声明 `1F`，但任务启用 `floor2`、`floor3` 语义，所有跨层和 z>0 解释等待 runtime 一致性验证。

候选机器人总池为 Go2+D1、Carter+D1、M20、B2+Z1、Pegasus 和 Iris。每个 episode 允许 2–4 台，由订单的域需求和能力可行性选择。默认起点均为 `workspace` 的绑定节点：ground 使用 `node:0341`、`node:0344`，aerial 使用 `node:0161`、`node:0164`。

货架能力完全由语义点的 `navigation_bindings` 决定：source domains 与 workspace domains 和机器人能力的交集形成可执行集合。仅 ground 或仅 aerial 的点分别限制对应域；同时绑定两域的点允许两类机器人。`floor2-shelf1..6`、`floor1-south-goods-1..3` 是 ground/aerial 双域候选，不能依据 `low/high` 标签硬编码域。`floor3-east`、`floor3-west`、`floor3-south` 是正式搜索区域语义点。

## 2. 订单驱动的拣选与汇聚

订单至少冻结 `order_id`、`material_id`、数量、`weight_class`、`requires_joint_carry`、`required_participants`、来源区域、来源语义点、`workspace_version`、是否需要搜索和截止时间。基础流程为：`order_bind → region_observe → shelf_resolved → acquire → weight_verify → joint_sync（重货）→ transport → workspace_place → inventory_update → order_closeout`。

已知来源货架可直接访问；搜索订单必须先到 floor2 或 floor3 区域观察点，再由外部 evaluator 揭示具体货架。到达语义点不等于取货成功，必须保持物资 ID、数量、订单版本和所有权。workspace 的 ground/aerial 绑定也必须参与最终放置验证。

## 3. 事件协议与不可完成任务

重货由两台满足重量和载荷能力的机器人在同步窗口内共同持有、运输和放置；一台失效后只有存在满足域、能力、资源和截止时间约束的重分配时才可继续。workspace 充电资源容量为 1。

正式注册四类不可完成任务：`warehouse_infeasible_no_domain_capability`、`warehouse_infeasible_workspace_domain_unreachable`、`warehouse_infeasible_insufficient_inventory`、`warehouse_infeasible_deadline_under_search_or_reallocation`。每项状态为 `team-state-infeasible`，须有独立 domain/reachability/inventory/time-window certificate，声明 `not-deployable`、禁止派发并 safe-stop。地图未验证、runtime binding 缺失和 solver timeout 只能标为 `feasibility-unverified`。

## 4. 四档难度与条件

| 难度 | task_id | 任务组成 | 主要测量维度 |
|---|---|---|---|
| T1 | `warehouse_known_pick_t1` | 已知货架轻货单订单 | P1/P2、对象身份和 workspace 放置 |
| T2 | `warehouse_search_pick_t2` | floor2/floor3 区域搜索后定位货架，多订单拣选 | 搜索揭示、订单版本和 P4 |
| T3 | `warehouse_joint_batch_t3` | 多订单含重货双机协作，workspace 到达即刻充满 | P3-J、资源锁、空地并行和 `battery_hard` |
| T4 | `warehouse_peak_multifloor_t4` | 多楼层搜索、轻重货混合、空地并行、通信压力、截止时间和等待充电 | P3-J/P3-S/P4、重分配和 `battery_very_hard` |

T1/T2 默认 `nominal_no_battery`。T3 的 `battery_hard` 在低电量回到 workspace 并完成占位后立即充满；T4 的 `battery_very_hard` 占用容量 1 的 workspace 充电资源并等待固定 `charge_wait_s` 后才充满。电量耗尽立即 safe-stop，受影响订单失败。条件只改变通信、电量、可用性或资源压力，不改变订单语义和任务 ID；paired branch 从同一注册 checkpoint 启动。

## 5. Checkpoint 与阶段点

checkpoint 支持 restore、fork、branch 和 replay，是评估点而非额外导航点。冻结阶段点为 `cp_00_spawn_valid`、`cp_01_order_bound`、`cp_02_region_observed`、`cp_03_shelf_resolved`、`cp_04_items_verified`、`cp_05_joint_carry_ready`、`cp_06_workspace_delivery`、`cp_07_charge_complete`、`cp_08_order_closeout`。记录实际 roster、机器人位姿/模式/能力 digest、订单和 workspace 版本、搜索结果、物资所有权、active obligations、资源锁、通信队列、电量参数与状态、route certificate、semantic map digest 和 checkpoint digest。

## 6. 指标与发布门槛

报告订单绑定正确率、区域观察与货架解析延迟、ground/aerial 取货成功率、物资身份保持、重货同步窗口、workspace 放置成功率、资源互斥、动态重分配、通信延迟/丢包/重试、低电量与充电次数、充电等待、电量耗尽、safe-stop 延迟、路径距离、makespan 和 checkpoint/replay 等价性。D1 评估域能力和不可完成拒绝；D2 评估 C0–C3；D3 评估 P2/P3-J/P3-S/P4；D4 评估可用性损失后的保持；D5 当前为 `NA`；D6 报告通信、路径、充电与物理时间。当前仍为 Isaac-free 逻辑轨道，`physical_qualified=false`、`runtime_oracle_bound=false`、`paper_score_eligible=false`。
