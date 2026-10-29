# living_house 家庭场景任务设计（逻辑仿真定稿 v1）

## 1. 评测边界

本场景使用 `living_house` 语义地图 revision `4a5247338b1a4297ae6f93dbff12ae51`，语义绑定摘要为 `109ee095c96533c026e60c78fd0fe0ec421689fa147dc9f34cd3816f4f756bc3`。当前地图 `robot_validated=false`，因此本文件定义的是 Isaac-free 的逻辑诊断轨道：它验证拓扑可达性、任务因果、能力约束、对象状态、门时序、通信和回放，不能替代真实机器人上的碰撞、抓取稳定性和三维传感器证据。

`bowl`、`fruit`、`air_trans` 的原始 ground 绑定是桌面/高位操作面节点。它们不应被解释为 Pepper 必须沿地面导航到达的普通航点。逻辑轨道使用 `Manipulation point1` 作为 Pepper 机械臂的显式 `surface interaction pose`，同时保留原始语义绑定；每个事件记录 `source_semantic_label` 和 `interaction_pose`。启用论文正式分数前，必须补充机械臂操作位和空中交接的物理 witness。

## 2. 初始编队与能力

| 机器人 | 初始语义点 | 域 | 评测能力 |
|---|---|---|---|
| Pegasus | `start_1` | aerial | `pot_pick`、`aerial_payload`、`aerial_handoff` |
| Pepper | `Manipulation point1` | ground | `arm_manipulation`、`surface_transfer`、`indoor_transport` |
| M20+D1 | `start_2` | ground | `chair_grasp`、`chair_carry`、`outdoor_transport`、`handoff_receive`、`manipulator`、上下楼梯 |
| Scout+D1 | `start_3` | ground | `chair_grasp`、`chair_carry`、`outdoor_transport`、一次性 `stair_down_once` |
| Go2 | `start_4` | ground | `chair_grasp`、`chair_carry`、`outdoor_transport`、`handoff_receive` |

Carter+D1 不属于正常实验池。它与 M20+D1 一起保留在之前错误实验团队的独立 D1 不可行案例 `D1_infeasible_wrong_team_table` 中。该案例只有桌子联合搬运下楼任务，要求两个不同持有者同时具备 `table_carry` 和 `stair_down`。Carter+D1 可以搬桌子但不具备楼梯能力，M20+D1 同时具备两项能力，因此可行持有者集合只有 1 台而要求为 2 台（`1 < 2`）。正确输出是合同级 `team-state-infeasible / not-deployable_safe-stop`，不能把运行超时当作不可行证明。

D1 的桌子是一个逻辑载荷：源位使用 `Manipulation point1` 的桌面交互代理，目的位为 `chair_new`，路线证据为 `door_in_3 → door_out_3 → edge:0005 → chair_new`。该代理不新增语义地图拓扑边；它只用于检验错误团队在能力声明阶段能否识别“联合搬运并下楼”的不可行性。

能力是评测器的协议事实。被测系统可以把任务错误地派给 Pepper 或无机械臂机器人；运行时会拒绝该提议并记录 `invalid_assignment`，不会替算法修正，也不会把错误从结果中删除。

## 3. 任务对象与公开顺序

每一集同时发布椅子任务和 Pot/桌面物体任务。后续任务只有在前置事件完成后才进入公开目标列表，避免把完整的未来任务数量泄露给被测系统。

独立 D1 不属于上述正常集：它只发布桌子联合搬运下楼任务，用于验证被测系统能否在执行前识别错误团队的能力级不可行性。

### 椅子协作搬运

1. 两名不同的地面机器人分别到达 `chair_left`、`chair_right` 并完成双点抓取。
2. 两名机器人以同一个 `chair_carry` cooperative group 到达 `door_in_3`。两人都到达后才能完成该阶段。
3. `door_in_3` 与 `door_out_3` 之间的门只有在无负载机器人位于任一侧时才会打开；它不要求固定的 `door_operator` 角色，也不为每次开关建立 checkpoint。
4. 两名搬运者共同到达 `door_out_3`；门未打开时，底层运动 skill 不提供跨门边的运动，搬运者原地等待。门通过后立即关闭。
5. 从 `door_out_3` 到 `chair_new` 的唯一 ground 路线经过 `edge:0005` 楼梯。M20+D1 具备上下楼梯，Scout+D1 只具备一次性下楼能力；下楼后其 `stair_down_once` 能力被运行时消费并记录 `capability_lost`，它不能再上楼。原两名搬运者共同到达 `chair_new` 并放置椅子。

门不是永久全局状态，也不能因为另一机器人曾经打开过门就跳过开门事件。门状态和开关 tick 会进入 checkpoint、replay 和事件账本。

### Pegasus 空中取物

Pegasus 到 `Pot` 拾取 Pot，再将其放到 aerial 绑定的 `Table`。这条任务独立于椅子任务，用来测量空地分工和并发调度。

### Pepper 桌面取物与交接

Pepper 只能在 `surface interaction pose` 完成桌面物体取放。它不具有 `outdoor_transport`，因此不能把物体从室内直接带到 `chair_new`。

- 地面交接：Pepper 将 `fruit` 放到 `trans1`、`bowl` 放到 `trans2`；具有 `handoff_receive` 和 `outdoor_transport` 的地面机器人接收后从室内运到 `chair_new`。
- 空中交接：Pepper 将物体放到 `air_trans`；Pegasus 在 `air grasping point` 接收，并将物体放到 `Table`。此路线依赖 surface-to-aerial 的逻辑交接 witness。
- 每次交接必须按 `pick → release → accept → place` 完成，所有权改变由运行时记录。没有 release 的 receive、没有持有权的 place、或错误对象均失败。

### Sink 循环

T4 中 `fruit` 在地面路线送到 `chair_new` 后，必须由同一具备 `manipulator` 的机器人执行：

`chair_new pick → Sink place → Sink pick → chair_new place`。

中间放置使用 `place_intermediate`，最终放置才检查对象声明的目的地。这样不会把“到 Sink”与“回到 chair_new”合并成一个无法审计的动作。

## 4. 难度与独立实验因子

难度由任务依赖和跨域交接增加；通信条件 `C0`–`C3` 是独立因子，不把通信损失混入难度定义。

| 难度 | 任务组合 | 关系/指标重点 |
|---|---|---|
| T1 | 椅子双点协作、开门穿门、`chair_new` 放置；Pegasus 搬 Pot | P3-J 同步、门因果、空地并发 |
| T2 | T1 + Pepper 地面交接 fruit/bowl + 地面室外运输 | P2 handoff、能力约束、所有权连续性 |
| T3 | T2 的物体路线改为空中交接并放置到 Table | P3-X 跨域接力、通信和 surface witness |
| T4 | fruit 地面交接并完成 Sink 循环，bowl 空中交接；C2 默认，C3 可作为配对压力条件 | 复合调度、资源竞争、重规划、回放 |

建议配对运行同一 tier 的 `C0/C1/C2/C3`，只改变通信和可用性条件，不改变地图、初始对象状态、机器人初始点或任务账本。

## 5. Checkpoint 与里程碑

Checkpoint 是评估器状态快照，不是额外导航点。每个快照保存地图 revision/digest、机器人位姿和状态、任务账本、对象位置与所有权、门状态、通信队列、协作组成员、资源锁、事件摘要和 digest。

固定标签为（门的打开/关闭是事件账本，不是单独 checkpoint）：

`cp_00_spawn_valid` → `cp_01_parallel_release` → `cp_02_chair_grasps` → `cp_03_chair_at_door_in` → `cp_04_chair_through_door` → `cp_05_chair_placed` → `cp_06_pot_on_table` → `cp_07_surface_pick` → `cp_08_handoff_accepted` → `cp_09_sink_started` → `cp_10_sink_complete` → `cp_11_closeout`。

未启用的任务阶段不生成伪 checkpoint。`checkpoint/restore/fork/branch/replay` 必须从同一共同前置状态开始；replay 比较完整事件账本和结果摘要，不能只比较最终 success。

## 6. 约束、失败和记分

时间使用注册的 `0.02 s` 控制时钟。地图运动速度目前是逻辑轨道约定值：地面 Pepper `0.75 m/s`、其他地面机器人 `1.0 m/s`、Pegasus `4.0 m/s`。这些数值是逻辑测试参数，不是对真机速度的声明。

当前先用 10 个 deterministic seeds 做拓扑校准；加入楼梯方向能力和门等待后，T1–T4 默认条件的完成时间分别为约 26.78 s、69.96 s、23.44 s 和 79.66–92.70 s。候选 deadline 按 `quantize_to_0.02(max(P95+5 s, P95×1.15))` 生成，当前候选 tick 为 T1=1589、T2=4022、T3=1422、T4=5304，写入 timing calibration 文件；在 robot validation、交互 witness 和配对收集完成前，`paper_score_eligible=false`，候选值不能当作论文最终阈值。

硬失败包括：门未打开就穿越、不可达目标、能力不满足、对象未持有就放置、对象所有权错误、重复/错误拾取、通信报告超时、硬 deadline 超时和 horizon 超时。错误分配本身保留为诊断证据；如果系统随后重新规划并成功，episode 可以成功，但 `invalid_assignment`、重试次数和等待时间仍计入指标。

主指标：

- `success`、任务完成率、`makespan_s`、deadline 是否满足；
- 协作同步间隔（两名搬运者到达屏障的 tick 差）；
- 门打开等待、门竞争和关闭时序；
- handoff latency、owner-change 次数、对象运输距离；
- `invalid_assignment`、重规划次数、报告重试和通信丢失/延迟；
- `resource_wait_ticks`、协作组等待、checkpoint/replay 等价性。

这些结果首先用于诊断和算法比较。只有通过地图机器人验证、surface/door/跨域交接 witness、paired-condition receipt、物理证据和独立时间校准后，才进入论文分数。

## 7. 标准运行流程

1. 读取 manifest 和 semantic map，验证 revision、binding digest、拓扑节点和初始点。
2. 对每个 seed/通信条件从 `cp_00` 创建 episode；记录任务、机器人能力和对象初始位置。
3. 运行被测算法 goal mode，逐 tick 处理分配、移动、交接、门状态、报告和隐藏阶段释放。
4. 在固定 checkpoint 记录状态；需要比较时从同一 checkpoint fork 两个分支。
5. 收集完整事件账本和指标，执行 replay equivalence 检查。
6. 对至少 10 个 deterministic seeds 统计 P50/P95/max；根据校准规则更新 timing artifact，人工审阅后再冻结 benchmark deadline。

实现入口：`benchmark/scenarios/living_house.py`；基线：`benchmark/living_house_algorithms.py`；任务契约：`living_house_task_manifest.v1.json` 与 `living_house_difficulty_contracts.v1.json`。
