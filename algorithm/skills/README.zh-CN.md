# EAI 底层 Skill

该目录采用 HeteroCoord 的 `simulator.skill.v1` 分层：高层分配产生
`SkillRequest`，纯 Provider 编译 `SkillPlan`，EAI executor 调用 controller 并推进
仿真，最终物理成败只由外部 evaluator 判断。

地面导航入口为 `algorithm.skills.navigation.GroundNavigationProvider`，采用
人工拓扑节点、有向 XY 最短路、航向 PID 和固定巡航速度，按距离依次切换节点。
`task.executors.ground_navigation.GroundNavigationExecutor` 负责真实 root 位姿、
模拟时间和 `env.step` 速度应用；调用者提供已校验拓扑及世界状态校验回调。
导航不使用全局栅格、局部代价地图、DWA、OBB 或碰撞查询。
详见[地面导航中文说明](navigation/GROUND_NAVIGATION.zh-CN.md)。

无人机搬运入口为 `algorithm.skills.aerial_transport.AerialPickPlaceProvider`，
Skill ID 为 `eai.transport.aerial_pick_place`。人工 XYZ 导航、垂直梯形轨迹与
确认驱动状态机由纯 Provider 编译；`task.executors.aerial_pick_place` 负责位置
控制器、临时世界支撑、真实 FixedJoint 和精确碰撞恢复。仅 iris（`PegasusIris`）
与 pegasus（`PegasusX4`）的位置机型可通过执行器机型门槛；CF2X 及其他机型在
dispatch 即被拒绝。持物停止保留连接和租约。
详见[无人机 Pick/Place 中文说明](aerial_transport/AERIAL_PICK_PLACE.zh-CN.md)。

Pepper 搬运接口为：

```text
eai.transport.composite
  → algorithm.skills.pepper_transport
  → PEPPER_BOX_TRANSPORT_CFG
  → EAI env.step
  → PepperTransportEvidenceEvaluator
```

固定参数为 `object_ref`、`source_ref`、`destination_ref`、`carry_mode`，以及可选的
`horizon_ticks`。未声明字段、非有限/非法预算、非 Pepper robot id 或不支持的
`carry_mode` 均拒绝。Provider 不导入 Isaac Sim、Torch 或 ROS，不调用 controller，
也不把 `planned` / `command_applied` 当作物理成功。

G1 暂不支持，因此不在 registry 中，不应向上层声明 G1 搬运能力。
