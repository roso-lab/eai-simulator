# 阶段14审核记录：基线修正与四场景任务见证（2026-09-19）

## 阶段出口

本阶段完成了任务生成与审核侧可以独立完成的修正和审查。四场景仍未进入正式计分集：`physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。

## Factory F-VERT-01

阶段13的 `first_tread` 运行使用 `0.10 m/s` micro-probe cap。相同 `env_isaaclab`、Isaac Sim 5.1、CPU 物理条件下，Go2 的命令流虽然合法，但 630 个运动样本的平面位移只有约 9mm。阶段14将 `pre_edge`、`first_tread`、`second_tread` 的 micro-probe cap 与公开导航基线统一为 `0.55 m/s`，并加入测试约束，避免把 Go2 低速死区误判成楼梯封闭。

静态专项测试为 `78 passed`。新的真实运行 `tmp/goal_stage14_stairs/first_tread_speed055.jsonl` 在 20 步校准后实际推进到 `route_index=2`，从入口前进到 `y≈-2.70`，接触读回出现真实楼梯路径 `/World/Indoor/SM_StairsSmall/SM_StairsSmall/Section0`。随后根高度从约 `0.33m` 降至 `0.21m`，支撑接触超时，终态 `support_unknown`，`execution_validated=false`。这证明命令基线修正有效，也证明完整首级通行仍未成立；楼梯继续保持公开开放，不能改为绕行或关闭。

本次新证据没有证明楼梯不可通行。下一步应把首段的足端姿态、踏步接触法向/力、横向漂移和碰撞形状覆盖绑定到同一物理步，并在修正后的路径控制下串行重跑入口、首级、第二级、上行和下行。

追加的控制诊断进一步缩小了问题范围：将 `first_tread` 降到 `0.25 m/s` 后，Go2 在约 `y=-1.06` 处进入低速控制死区并保持原地，不能作为楼梯几何结论；临时替换为 B2 的副本则在公共二维路线准入阶段因缺少 `b2_1` 路线而停止，也不能作为 B2 物理失败。因而当前需要由运行时/技能后端提供一个具有明确航向和首级通过策略的参考执行，任务生成侧不应通过关闭楼梯、增加绕行或修改公开楼梯语义来规避该问题。

## Modern House NVD

`close_m20_2.png` 已通过 ZCode 真实图片附件复核。像素事实是：M20_2 的可见支撑足在门框外灰色混凝土/门槛侧；室内深色地面、木质门槛和室外地面存在分界；精确高差和连续地面未知；单帧无法证明入口到生活区的连续住户路径。静态 audit 也明确当前只有 2-D 初始放置，没有路径或 3-D 净空证据。House initial-region、门内外边界、连续住户路径和业务结果继续 blocked。

## City

`guide_people`、`control_traffic`、`report_incident` 的 participant request、trusted receipt、时窗、角色区间和 fail-closed 审核已有接口；城市测试、intake 和 deployment focused tests 共 `177 passed`。当前仍缺真实 episode-owned 隐藏人车 roster、角色时间区间、测量轨迹、容量/危险区、救援通路、外部服务端 receipt，以及通信和成员变化后的业务效果见证。请求 ack 不得替代业务成功，City 继续 blocked。

## Warehouse NVD

Warehouse scenario 已有订单、货架、60kg 木箱、8kg 纸箱、集货区和 dock 的场景化任务骨架；`joint_transport`、成员退出释放/恢复、路由重建和安全取消的协议测试通过，相关 focused tests `56 passed`。但当前 author audit 仍是 2-D initial placement；场景没有接入服务车的 episode roster/轨迹，联合搬运没有真实负载/固定点/双机形成包络见证，连续携物净空、成员退出后的物理重新获取和安全取消后的 safe-hold/clear-route 仍缺。Warehouse 继续 blocked。

## 反思与修正

1. 阶段13把低速 micro-probe 的失败当作主要路线问题；阶段14用同一场景的 `0.55 m/s` 实测对照纠正了这个归因。今后必须同时记录命令、根位姿、route index、接触路径和终态。
2. 任务生成侧已经把可靠导航、空中观察、固定点抓取和释放当作基础后端；评价对象继续聚焦负载/净空/成员数/通信/恢复，避免引入 VLA 抓取考核。
3. 业务 adapter 单元测试只能证明证据边界，不能证明场景业务发生。所有场景的正式 release gate 继续要求 episode-owned 运行见证。
4. 视觉检查只用于确认像素事实；House 的图像不能替代门边界、连续路径或 PhysX 证据。
