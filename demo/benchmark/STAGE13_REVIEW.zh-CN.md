# 阶段13审核记录：物理证据后端与业务见证（2026-09-19）

本阶段按场景串行运行、按模块并行审查。主线未覆盖其他会话对 `modern_house_nvd` 的修改；代理只写独立 handoff，未修改场景源文件或 ROS 配置。

## 已完成并有证据的工作

- PhysX SDK 研究确认 `query_prim` 能提供 rigid-body/collider property callback，`request_convex_collision_representation` 属于异步 cooking 请求；同一 path 的多个 child shape 必须按 callback ordinal 保留。研究交接在阶段13团队工作区的 `handoffs/occupancy_backend.md`。
- 新增 [`goal_native_shape_query.py`](goal_native_shape_query.py)，把 stage cache、物理步、配置版本、当前冻结状态、有限超时、finished callback、foreign stage/path、非 `VALID`、重复 shape 和 cooking acceptance-only 记录成 fail-closed 报告。它不修改 `goal_native_occupancy`，也不把 property/cooking 结果宣称为 live solver 的完整占用。
- 注入式测试与原生占用回归共 `60 passed`。测试运行在 `env_isaaclab`，通过 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 隔离系统 ROS pytest 插件；未启动 Kit/ROS。
- Factory F-VERT-01 在 `env_isaaclab`、Isaac Sim 5.1、CPU 物理下运行了 20 步接触校准和 650 步 first-tread 探针。四足地面接触校准有效，但机器人从入口没有推进到 surface entry，630 个样本的最大平面位移约为 `(0.0011, 0.0089) m`，没有首级接触，终态 `step_budget`。原始 JSONL 和摘要在 `tmp/goal_stage13_stairs/`。
- City 只读审查确认 `guide_people`、`control_traffic`、`report_incident` 尚未接入 participant API 和真实 episode witness；人员/车辆完整 roster、角色区间、服务回执、通信/成员变化的 outcome effect 仍缺失。纯 Python 协议测试 `329 passed`，不能替代物理业务证据。
- ZCode 对 `House_v5/close_m20_2.png` 的新一轮实际像素审核确认机器人位于门框外的混凝土门槛/露台侧，室内地面在更深处且不同层；可见姿态无明显悬浮、穿地或倒地。该结果支持 House 语义 blocker，但不证明三维边界或业务路径。

## 当前仍阻塞的门禁

1. Iris 的 `convexDecomposition`、Carter 的 Xform/caster 碰撞几何尚未在同一暂停物理步完成 property/cooked shape 覆盖、变换标定和 live solver 对照，因此四场景三维 occupancy 继续 `blocked`。
2. Factory 楼梯题面保持开放；当前失败是垂直探针没有到达楼梯，不是楼梯被关闭或被判定为不可通行。需要先修正/验证基线路线跟随，再采入口、首级、第二级和上下行证据；首级公开高度约 `0.18 m` 与源测量约 `0.210 m` 的差异也必须由作者解决。
3. House 仍需证明地面机器人在门内的公开 initial-region、门内外边界和连续住户路径。静置姿态和客厅背景不能替代区域语义。
4. City 保持 preparation draft；必须增加 city-specific witness schema/adapter 和真实 session 采样，再接服务入口、角色覆盖、人员/车辆轨迹、回执和 treatment pair。

## 反思与下一步

本阶段验证了“校准成功”和“任务成功”必须分离：地面支持接触可以闭合而楼梯通行仍未知；property query API 可用而 occupancy 仍未知；离线城市协议测试通过而城市服务仍不可计分。下一阶段应先修 Factory 基线命令/路线接线，再在一个暂停 Isaac session 内对 Iris/Carter 做最小 cooked/property 微诊断，最后按 House、Warehouse、City 的业务 witness 契约逐项接入。任何缺失证据都保留 `physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。
