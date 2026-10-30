# Subway 工程适配与验收边界

本场景包以逻辑图、语义点和共享诊断 runtime 为执行基础。机器人出生绑定 `assembly_point` 的 aerial `node:0081` 或 ground `node:0052`；多机器人同点出生仍需 runtime 提供独立位姿和安全间隔。CF2X 只登记观察能力，不登记载荷或操作能力。

发现接口只接受绑定语义点到达作为发现证据。发现记录必须包含机器人、domain、语义标签、节点、tick 和观察来源。ground contact、generator started 和 assembly arrival 是独立 evaluator 谓词，不能由机器人自报替代。

generator 的原地操作不是搬运 generator：`generator_pick` 与 `generator_place` 在同一 runtime 交互区域完成，只有 place 成功才生成 `generator_started`。当前地图没有 interaction pose，物理接口保持 pending。

T3 的 `deadline_suspended` 是逻辑状态而不是数值无穷；episode 最大 tick 仍生效。T4 的 `extension_s` 和 `assistance_window_s` 进入 condition digest；窗口超时记录 `rescue_failed_after_ground_contact`。

算法矩阵中的适配器使用确定性 allocation witness，`native_algorithm=false`。成功矩阵不代表真实 EMOS、TeamWeaver、无人机控制器、人员行为或物理交互认证。
