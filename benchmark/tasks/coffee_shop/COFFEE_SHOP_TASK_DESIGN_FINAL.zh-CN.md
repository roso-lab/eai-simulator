# coffee_shop 任务设计定稿（Isaac-free 逻辑轨道）

## 1. 地图绑定与机器人池

场景为 `production_coffee_shop`，语义地图 revision 为 `3d4065d3c691418a9971824d2a897c73`，semantic binding digest 为 `e08912cef9334da4e3213d117bfc0f4211934cc50d43e2bdd9ef30598d9b27a9`。ground topology 为 37 nodes / 35 edges，aerial topology 为 0 / 0；逻辑时钟为 `0.02 s`，`robot_validated=false`，`paper_score_eligible=false`。地图只登记一层地面逻辑路线。

冻结语义点包括 `gate-inner/external`、`side_door-inner/external`、`ordering_counter-worker/customer`、`cup_holder`、`coffee_machine`、`milk_frother`、`spice_rack`、`shaker`、`bread_counter`、`rest_area-1/2`。两个休息区各登记容量 4，`shaker` 登记为容量 1 的共享制作资源。每局从总候选池中选择三台执行机器人：Pepper、G1、wheel_manipulator、go2、lite_manipulator、lite_base、micro_duck；`ur5_fixed` 作为固定制作设备候选，不占用地面导航起点。起点分别绑定入口外侧、`ordering_counter-customer` 和 `ordering_counter-worker`；具体能力、速度、操作姿态和 USD 绑定由 runtime adapter 提供。

## 2. 基础迎宾、点单与咖啡制作

每个顾客从 gate 或 side_door 进入统一队列。机器人执行 `approach → open_command → open_verified → customer_entered → close_or_release`，引导事件首版只记录逻辑顾客事件，不假设人物物理跟随。顾客到达点单处后提交菜单名称；evaluator 按 `menu_item → recipe action graph` 释放取杯、取料、设备处理、等待、核验和放置义务。机器人在 `cup_holder`、`coffee_machine`、`milk_frother`、`spice_rack`、`shaker` 和 `bread_counter` 执行动作，动作到达不等于动作完成，必须有外部谓词和报告。

订单制作期间分配 `rest_area-1/2`。休息区按实时容量分配，满员时顾客排队或重新分配。制作完成后，机器人把对应餐品送到顾客休息区，随后记录顾客离店并完成入口关门。订单 ID、顾客 ID、菜单名称、数量、配方版本、目标休息区和交付截止均由 runtime 注入。

## 3. 事件协议与不可完成任务

事件协议为：入口开门与顾客进入、订单绑定、配方释放、取杯/取料、设备处理、配方核验、休息区分配、引导、休息区交付、顾客离店和关门。跨机器人 release/accept、共享 `shaker`、休息区容量和成品责任转移分别作为 P2/P4 诊断对象。

注册四个不可完成任务：`coffee_infeasible_no_manipulator`（无可验证取放能力）、`coffee_infeasible_rest_capacity`（座位容量与不可等待合同冲突）、`coffee_infeasible_recipe_resource`（配方所需设备或固定资源为空）、`coffee_infeasible_dual_entry_conflict`（双入口状态合同无可行调度）。每项都必须提供 `status=team-state-infeasible`、独立 capability/capacity/recipe-resource/entrance-state certificate、`not-deployable`、不派发动作、safe-stop 和 D1 识别规则。地图未验证、缺少 runtime binding 或 solver timeout 只能标为 `feasibility-unverified`。

## 4. 四级难度与条件

| Tier | 任务组成 | 主要测量维度 |
|---|---|---|
| T1 `coffee_service_t1` | 多顾客统一队列、点单、配方制作、休息引导和送餐 | P2、订单身份、基础路线 |
| T2 `coffee_service_t2` | 多步骤订单与操作机器人容量限制 | P1、P2、`shaker` 互斥和排队 |
| T3 `coffee_service_t3` | 座椅数量限制、固定设备等待、满员排队/重分配 | P4、容量账本和服务连续性 |
| T4 `coffee_service_t4` | 双入口高峰、通信压力、成品 freshness window | P1、P2、P4、报废与恢复 |

条件包括 `C0/C1/C2/C3`、`nominal`、`communication_stress`、`rest_capacity_stress`、`recipe_resource_contention`、`freshness_stress` 和 `availability_loss`。条件只改变通信、资源压力、容量、可用性或 `freshness_window_s`，不改变地图、订单语义、起点和任务 ID。T4 中配方完成后超过 runtime 注册的固定 `freshness_window_s` 且无人接收/运输/交付时，成品报废，订单 obligation 失败。paired branch 从共同注册 checkpoint 启动。

## 5. Checkpoint 与阶段点

checkpoint 为 `cp_00_spawn_valid`、`cp_01_customer_arrived`、`cp_02_order_bound`、`cp_03_recipe_released`、`cp_04_recipe_complete`、`cp_05_rest_assigned`、`cp_06_customer_guided`、`cp_07_delivery_complete`、`cp_08_customer_departure`、`cp_09_closeout`。checkpoint 支持 restore、fork、branch 和 replay，是不可变评估状态，不是额外导航点；milestone 必须由 evaluator 或事件账本确认。

至少记录实际三机 roster、角色和能力 digest、顾客/订单状态、配方图版本与当前动作、两个入口门状态、休息区占用、`shaker`/设备资源锁、active obligations、通信队列、freshness timer、报废原因、semantic map digest 和 checkpoint digest。

## 6. 指标与发布门槛

报告迎宾和开关门成功率、点单绑定正确率、配方动作完成率、取杯/取料/设备处理/放置成功率、操作排队、`shaker` 互斥违反、休息区容量等待、引导完成率、休息区送餐成功率、freshness 窗口内处理率、报废次数、订单版本冲突、重复事件抑制、通信延迟/丢包/重试、safe-stop、路径距离、makespan 和 checkpoint/replay 等价性。

D1 评估角色、能力、入口、配方和不可完成识别；D2 评估 C0–C3 下服务保持；D3 评估 P2、配方先后、共享资源和休息容量时序；D4 评估机器人或设备失效后的重分配；D5 当前无认证跨场景配对，报告 `NA`；D6 报告通信量、排队、路径、makespan 和服务时间。

当前仍为 Isaac-free 逻辑轨道，`runtime_oracle_bound=false`、`physical_qualified=false`、`paper_score_eligible=false`。完成 semantic binding、机器人能力验证、配方 evaluator、入口/顾客事件 witness、设备与容量 witness、freshness runtime readback 和 checkpoint readback 后，才可进入正式论文评测。
