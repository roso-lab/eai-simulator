# candy_shop 任务设计定稿（Isaac-free 逻辑轨道）

版本：`candy_shop.final.v1`。本文件冻结糖果店任务的逻辑语义、任务族和评测协议，逻辑时钟固定为 `0.02 s`。它不替代 Isaac Sim 的碰撞、传感器、控制器或真机证据；当前 `robot_validated=false`、`paper_score_eligible=false`，状态为运行时绑定待完成。

## 1. 地图绑定与机器人池

地图产品为 `usd/scene/Production and logistics/candy_shop/semantic_map/current`，场景键为 `production_candy_shop`。地图 revision 为 `412b1f18f44c4dab81a99e321618e361`，semantic binding digest 为 `f9844182d9990edc0045724919e6b2a79e874c646c558dae516b1570d0737f3f`。ground topology 为 61 nodes / 63 edges，aerial topology 为 8 nodes / 7 edges。manifest 目前只声明 `1F`，但二层货架语义点和 z>0 节点存在，跨层一致性必须由 runtime adapter 验证。

三个 roster 是同一任务族 `candy_restock_v1` 的 variants；组合登记如下，不能把组合名称当作物理认证：

| variant | 地面机器人 1 | 地面机器人 2 | 空中机器人 |
|---|---|---|---|
| `candy_roster_go2_b2_pegasus` | Go2+D1 | B2+Z1 | Pegasus |
| `candy_roster_m20_carter_pegasus` | M20+D1 | Carter+D1 | Pegasus |
| `candy_roster_go2_b2_iris` | Go2+D1 | B2+Z1 | Iris |

三个 variant 共用冻结起点：

| 任务标签 | 拓扑节点 | 默认角色 |
|---|---|---|
| `start_gate_external` | ground `node:0008` | gate 操作、首个跨层前置 |
| `start_handoff_external` | ground `node:0002` | 一层接收、柜台配送 |
| `start_aerial` | aerial `node:0001` | 天井路线、空中接力 |

能力按角色登记：Go2、B2、M20 具备地面导航和楼梯候选能力；Carter 具备地面导航与机械臂操作，楼梯能力需单独验证；D1/Z1 具备 `pick_place` 候选能力；Pegasus/Iris 具备空中导航和空中路线候选能力。`gate_open` 是任务所需能力，具体机器人是否通过 gate 物理 witness 仍待 runtime 验证。

冻结语义点及绑定包括：`floor1-gate-inner`→ground `node:0007`、`floor1-gate-external`→ground `node:0008`、`floor1-handoff_point-inner`→ground `node:0061`、`floor1-handoff_point-external`→ground `node:0002`；`floor1-shelf`→ground `node:0012`；`floor1-counter-north/east1/east2/east3-{inner,external}` 分别按 semantic-points.json 的 ground/aerial bindings 使用；四个二层货架为 `floor2-shelf-north`（ground `node:0084` / aerial `node:0003`）、`east`（`node:0082` / `node:0005`）、`west`（`node:0050` / `node:0004`）、`south`（`node:0085` / `node:0006`）。`edge:0064`（`node:0024→node:0059`）为要求 `stair_capable` 的楼梯边；`edge:0023`、`edge:0024` 为坡道边；空中拓扑是楼梯替代路线候选。

## 2. 基础补货与订单配货

一楼柜台生成补货或订单需求，需求绑定糖果品类、数量、订单 ID 和目标柜台。任务轨道依次允许机器人：从 `start_gate_external` 接近并打开 gate；由外部谓词确认通行；在二层候选货架查找、取出并核验糖果；选择楼梯、坡道（需独立物理 witness）或天井路线；必要时在 `floor1-handoff_point-inner/external` 完成交接；将正确品类和数量放到目标柜台；更新库存和订单外部状态。

`floor2-shelf-*` 是候选库存点，抵达货架不等于取货完成；临时放到 `floor1-shelf` 不等于订单完成。普通单批补货不自动登记论文 P1/P2，只有多品类齐套后必须交给下游接收者时，才登记 P1/P2。柜台内外侧点的到达、放置和订单关闭均由 evaluator 的外部谓词确认。

## 3. Gate、跨层路线与不可完成协议

Gate 是可由机器人打开的共享物理状态，状态序列固定为：`gate_approach → gate_open_command → gate_open_verified → gate_transit → gate_close_or_release`。`gate_open_command` 后必须收到 `gate_open_verified`；打开后保持开启，直到显式关闭或释放。paired branch 从已开 gate checkpoint 启动时必须共享同一 gate 状态。gate 不是静态障碍，不能仅因 gate 存在登记不可完成；gate 可打开也不代表柜台端点一定可达。

跨层协议为楼梯 `stair_entry_acquire → stair_transit → stair_exit_release`，楼梯容量和 `stair_capable` 必须记入资源账本；坡道必须有独立物理 witness；天井路线必须同时提供合法飞行、载荷、交接和一层接收证据。

正式注册以下不可完成任务。每项都必须有独立能力/时间窗/可达性证书，状态为 `team-state-infeasible`，并声明 `not-deployable`：

| task_id | 合同级空可行集原因 | 处理 |
|---|---|---|
| `candy_infeasible_no_crossfloor_route` | 所有 roster 均无通过楼梯、坡道或合法天井路线的完整证书 | 不派发；锁定资源并 safe-stop |
| `candy_infeasible_stair_window` | 唯一可行楼梯窗口与订单截止时间不相交，且无合法替代路线 | 不派发；保持 gate/楼梯安全状态并 safe-stop |
| `candy_infeasible_kit_capability` | 齐套所需品类/数量的取放能力集合为空 | 不派发；冻结物品所有权并 safe-stop |
| `candy_infeasible_counter_endpoint` | 目标柜台端点不可达或无可验证放置 witness | 不派发；停止继续搬运并 safe-stop |

D1 只有在合同级证书证明可行集为空时才计为正确不可行识别；solver timeout、运行时异常或缺少 binding 只能标为 `feasibility-unverified`。没有 gate 操作能力时，也必须在具体 roster 合同中证明能力集合为空，不能把场景资产本身作为失败原因。

## 4. 四级难度

| 难度 | 任务组成 | 主要测量维度 |
|---|---|---|
| T1 | `candy_delivery_t1`：单批糖果补货，绑定需求、开 gate、单货架取货、单柜台交付 | gate 前置、基础 P2、路径和物料身份 |
| T2 | `candy_delivery_t2`：多品类订单齐套，多个货架分支、核验、齐套后交接 | P1、P1P2、P2、物品所有权和齐套一致性 |
| T3 | `candy_service_t3`：两批补货同时发生，楼梯容量为 1，天井路线作为合法替代 | P4 资源竞争、路线分支、楼梯/飞行能力 |
| T4 | `candy_peak_t4`：三订单高峰齐套、gate 时序、混合跨层路线、通信压力 | 通信延迟/丢失、订单版本、重分配、资源账本和协作鲁棒性 |

四档任务均在三个 roster variants 上运行。通信条件为 `C0`（无损）、`C1`（延迟）、`C2`（丢包与重试）、`C3`（压力下延迟/丢包/乱序）；只改变通信传输、队列和重试，不改变地图、订单、起点和任务语义。电量不是默认条件；availability loss 或电量应作为独立 D4/配对条件注册。难度只由合同需求决定，不由 condition factor、solver timeout 或运行结果决定。

## 5. Checkpoint 与阶段点

逻辑时钟固定为 `0.02 s`。checkpoint 支持 restore、fork、branch 和 replay，是评估点而非额外导航点；milestone 必须由外部 evaluator 或事件账本确认。阶段点冻结为：`cp_00_spawn_valid`、`cp_01_request_bound`、`cp_02_gate_open_verified`、`cp_03_items_verified`、`cp_04_route_selected`、`cp_05_handoff_complete`、`cp_06_counter_delivery`、`cp_07_inventory_order_closeout`、`cp_08_closeout`。

每个 checkpoint 至少记录 variant、task ID、episode/repeat/condition；三台机器人的位姿、模式和能力 digest；gate 状态及最后一次 gate event；active obligations；楼梯、handoff、柜台资源账本；通信队列；route certificate；semantic map digest；checkpoint digest。paired condition 只能从共同 pre-factor checkpoint 开始，保证 gate 状态、订单版本和资源锁一致。

## 6. 指标与可解释性

报告 gate 开门成功率、开门延迟与保持时间、订单/补货绑定正确率、糖果品类和数量保持率、货架取货成功率、楼梯/坡道/天井路线选择、楼梯排队等待、handoff 延迟与 release/accept 完整度、柜台最终放置成功率、订单版本冲突、重复触发抑制、通信延迟/丢包/重试、资源互斥违反、safe-stop 延迟、路径距离、makespan 和 checkpoint/replay 等价性。

论文 D1–D6 映射如下：D1 检查初始角色、能力、gate、路线及不可完成声明；D2 报告 C0–C3 成功保持率；D3 诊断 P2、P1P2 与 P4 obligation；D4 记录 gate、取货、楼梯、飞行或通信功能丢失后的工作保持；D5 当前无认证跨场景 pair，报告 `NA`；D6 在成功门控下报告通信量和物理时间。

本文件只定义 Isaac-free 逻辑轨道，不宣称物理执行认证。只有完成 semantic binding、robot validation、route/gate witness、runtime oracle、checkpoint readback 和必要的跨场景配对目录后，才可将 `paper_score_eligible` 改为 `true`；当前保持 `false`。
