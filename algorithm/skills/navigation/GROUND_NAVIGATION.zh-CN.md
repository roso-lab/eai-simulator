# 统一节点路线导航

导航公共 Skill 只有 `eai.navigation.node_route`。请求必须提供
`target_ref` 和 `world_state_seq`，不接受 `target_pose`、控制器 frame、
`mode` 或 `horizon_ticks`。

Provider 绑定一张冻结拓扑产品，负责目标引用解析、起点匹配、确定性
Dijkstra、节点进度以及 start/continue/hold/replan/stop 生命周期。它只输
出 `navigation_target`：节点 ID、节点序号、拓扑 XYZ 和可选终点航向。
Provider 不读取运动模式、底盘能力或控制器命令。

地面和空中地图是两个独立产品，但 JSON schema 相同。地面
`NavigationProfile(metric="xy")` 在起点匹配、边权、到达和路线长度中忽略
Z；空中 `metric="xyz"` 保留高度语义。路线只使用拓扑节点端点和有向边，
polyline 仅供作者工具、可视化和场景校验。

`NavigationExecutor` 通过两阶段 host tick 与控制器解耦：`prepare()` 读取
Adapter 的实时状态、调用 Provider 并写入抽象目标；宿主推进一次物理
tick；`after_step()` 读取实测位姿/速度并确认节点到达和安全停止。Executor
统一管理 robot lease、world-state 序号、仿真时间、超时、取消、私有证据
及停止未确认时的 quarantine。

`NavigationControllerAdapter` 是接入新机器人或底盘运动模式的唯一扩展点。
现有平面 Adapter 将目标编译为 `base_velocity`，世界位置 Adapter 将目标
编译为 `goal_position`。新增底盘不需要修改 Skill 或 Provider。
