# 室内理想抓取的执行证据边界

本记录对应 2026-09-23 的室内正式 episode 排查。抓取是 benchmark 提供的
理想基础技能：目标是合理的可视连接，避免虚空抓取，不追求夹爪、IK 或接触力
真实性。负载、承载人数、公开能力等影响多机分配的约束仍须保留。当前
`scoreable=false`；这里的运行结果不证明视觉连接或业务结果通过。

## 路径与门槛

Factory、Warehouse、House 共用 `HouseNativeGoalBackend`。其中 `pick` 与
`cooperative_carry` 都先调用 `_approach_object`，成功才调用
`goal_native_attachment.prepare_attachment`。Factory 当前第一份见证只有观察、
导航、楼梯；以后若执行抓取，同样走此路径。City 的地面引导/空中广播使用独立
后端，Restaurant 也不是这条室内路径。

Warehouse 的 `pick_carton` 和 House 的 `pick_books` 也有接近步骤；当前串行见证
因前置搬运失败而未执行它们。不能因此说单机抓取必然失败或永远看不到 reach
检查：独立执行时能否进入检查取决于该次接近结果。

1.25m 是执行器的二维到达阈值，不是题包中的抓取规则，也不是 0.8m 的替代。
本轮保留它以保持运行可比较；不把它提升成计划准入条件。它也不是物理必要条件：
执行器没有按各自支撑点完成精确就位，且保存了接近开始时的物体中心，物体移动
后该中心可能过时。因此通过门槛不保证可连接，未通过也不能证明理想抓取不可行。
后续理想技能应以合理连接位置为目标，避免把低层控制器缺陷测成模型能力。

`approach_object_not_reached:<object>:<distance>m` 的距离是接近目标点距离；
新的 `skill_results[].execution_diagnostics` 将每个 carrier 的 root pose、目标点、
到判定中心的距离、到最终实时物体中心的距离分别保存。`failure_agent_ids` 指出
未到达者或实际 reach 拒绝者。`reach_checks.prepare_attachment/commit_attachment`
只记录检查实际测到的成员；前一成员拒绝后，未检查成员不能记为通过。
三维 root→固定点距离是理想连接规则，不能称为真实机械臂工作空间测量。

`allow_reverse=False` 在现有 follower 中只关闭停滞后的符号翻转。
`_seek_command` 对身后 waypoint 仍能返回负 vx；走廊符号翻转也可能多次发生。
本轮只如实记录 `stall_sign_flip_applied` 和实际负速度命令
`negative_vx_command_steps`，不修改控制策略。`reverse_applied` 表示发出负 vx
命令，不代表已测得倒车位移。不得用“无 house_vx_sign”推断没有任何负 vx。

## 实测与结论

- Factory v9，episode `339b4ff3c1774e9aa8d5789ce728cd61`：9 步成功，
  未涉及抓取；不构成抓取验证。
- House v14，episode `6c21c617c31847eb85b1af4e1db86079`：第一台到判定中心
  1.044m，第二台 2.744m；第二台距接近目标 2.053m。失败在 approach，
  reach 未运行。两机终态间距约 2.557m，不支持声明足迹下的终态直接互相顶住。
  静态占据图上第二台到 waypoint 的 44 个采样格均空闲，但这不能排除真实静态
  障碍、动态碰撞或控制器问题。仅凭前进命令和不动的位姿，不能选定根因。
- Warehouse v12（ljy），episode `b839c481b16940968a16bb5cb91b8db0`：
  通过接近，真实触发 `current PhysX root-to-fixed-point distance exceeds reach`。
  日志打印的约 `(-10.59,-11.67)` / `(-9.71,-11.73)` 是接近过程采样，
  不是 reach 拒绝瞬间的完整测量，不能据此计算“只差 0.1m”。
- Warehouse 本机诊断 v15，episode `c6cb61ab59764df1bc2b0288558c755c`：
  第一台 `warehouse_ground_1` 的 `/m20_1/base_link` 到 `support_left`
  实测 0.99238m > 0.8m；第二台尚未检查。第一台 root 为
  `[-11.12087,-11.66073,0.52938]`，固定点为
  `[-12.09860,-11.82934,0.55003]`。物体中心由接近开始的
  `[-11.48751,-11.69023]` 移至检查时的 `[-12.01823,-11.54045]`，
  因而冻结物体位置或过时中心不能代表最终连接几何。

House 是当前接近执行器失败；Warehouse 是当前姿态下理想连接检查拒绝。
二者是真实运行记录，但均不证明当前机器人双机搬运在所有合理姿态下不可执行，
也不证明见证中的模型分配错误。物体支撑点由实际物体姿态旋转、平移得到，
测量起点是实际 PhysX 根刚体；“机体离中心近”与该三维测量不同，不是同一距离。
1m 的 authoring region 也不能直接等同于实际货架净宽。

模型仍可提交满足公开约束的抓取/联合搬运计划。保留执行失败回执，分开解释
执行器局限与模型决策；不新增基于本次停滞的计划禁令。此次不改冻结题包、
见证、评分、reach、瞬移或成功状态；不继续投入与多机分配无关的高保真抓取调参。

## 验证范围

最终完整 benchmark 回归为 **2073 passed**，命令为
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test/test_benchmark*.py`。
`git diff --check` 通过。补充测试覆盖
单/双机 approach 阶段拦截、prepare 早期拒绝未测 reach、prepare/commit
实际 reach 拒绝、诊断开关不改变决策/Stage、首成员失败后不伪造后续成员结果。
原生证据位于本地 `tmp/goal-house-episode-20260923-v14-diagnostic/`、
`tmp/goal-warehouse-episode-20260923-diagnostics-v15/`；运行输出不入 Git。
v14/v15 是本轮诊断实现的中间版本证据，后续补充的实时中心字段由单元测试验证，
不能回填成旧 episode 已保存的数据。尚未完成成功抓取的视觉验收。
