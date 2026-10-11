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

当前注册的搬运能力仅支持 Pepper：

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
