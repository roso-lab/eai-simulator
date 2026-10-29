# living_house 论文检测对齐审计

`living_house` 已绑定最新语义地图 revision `4a5247338b1a4297ae6f93dbff12ae51`、semantic binding digest `109ee095c96533c026e60c78fd0fe0ec421689fa147dc9f34cd3816f4f756bc3`，并接入通用 Isaac-free 逻辑 runtime。地图静态审计为 `case_ready=true`，但 `robot_validated=false`；桌面交互、空中交接、门和楼梯仍缺物理 witness，所以 `paper_score_eligible=false`。

## 协作任务

- T1：两点抓取的椅子联合搬运，门 `door_3` 的开启/穿越/关闭，以及 Pegasus 的 Pot→Table 空中任务，测 P3-J、门因果和空地并发。
- T2：T1 加 Pepper→ground robot 的 fruit/bowl release→accept→chair_new，测 P2 所有权连续性、能力约束和重规划。
- T3：T2 的物料改为空中交接并由 Pegasus 放置到 Table，测 P3-X、跨域通信和表面交互代理。
- T4：fruit 的 chair_new→Sink→chair_new 循环、bowl 空中交接、门与交接并发，测复合调度和状态闭合。

椅子三个 cooperative group（`chair_carry`、`chair_transit`、`chair_place`）必须由同两名不同地面机器人共同到位后同步完成。门只有在无负载机器人到达门侧时打开；装载机器人穿越关闭门会产生 `gate_not_open` 失败。楼梯边的 `stair_down_once` 会在 Scout 第一次下楼后从 active capabilities 消费并记录 `capability_lost`。

## 信息边界、失败与条件

被测算法只看到当前公开目标、能力、可达机器人和门/通信状态；未来对象数量与角色配额由 evaluator ledger 保留。桌面 `bowl`、`fruit`、`air_trans` 使用 `Manipulation point1` 作为逻辑 surface interaction pose，不伪造地面导航边。C0–C3 是独立通信因子；C3 的 availability-loss 在 `pot_place` 或所有当前 tier 的空中放置义务完成并报告后移除 Pegasus，保证故障可审计且 episode 仍可解。

硬失败包括门未开穿越、能力/路线不满足、错误对象所有权、报告超时、deadline/horizon 超时。错误分配保留在事件账本中，即使后续重规划成功也计入诊断指标。

## Checkpoint 与指标

checkpoint 使用 `0.02 s/tick`，固定标签为 `cp_00_spawn_valid`、`cp_01_parallel_release`、`cp_02_chair_grasps`、`cp_03_chair_at_door_in`、`cp_04_chair_through_door`、`cp_05_chair_placed`、`cp_06_pot_on_table`、`cp_07_surface_pick`、`cp_08_handoff_accepted`、`cp_09_sink_started`、`cp_10_sink_complete`、`cp_11_closeout`。未启用阶段不会伪造 checkpoint；每个结果都支持 checkpoint/replay/branch，并比较完整事件账本。

矩阵记录 success、hard deadline、makespan、门开启/关闭与等待、cooperative sync gap、handoff 次数、invalid assignment、通信延迟/丢包、availability-loss 和 replay 等价性。当前四级 × C0–C3 × EMOS/TeamWeaver 的逻辑矩阵用于诊断比较；论文分数仍需机器人能力、交互位姿、门/楼梯路线、配对 receipt 和物理证据全部闭合。
