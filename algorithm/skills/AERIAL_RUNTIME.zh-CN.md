# 空中导航运行时

`AerialSkillRuntime` 只负责调用者显式选择的空中导航。它读取冻结拓扑和实时位姿，通过宿主共享物理循环发送 `goal_position`。局部取放不属于该运行时；需要抓放时，调用者创建并注册 `DronePickPlaceAdapter`，分别执行 `pick` 和 `place`，中间可自行导航。

无人机适配器复用 `PhysxAerialTransportBridge`、实时 PhysX 位姿读取、FixedJoint、支撑物和碰撞恢复工具。持物期间连接和物体租约保留；导航调用者必须继续运行宿主物理循环并避免覆盖适配器要求的控制器写入。停止未知或清理失败时由适配器保留资源，不能根据布尔失败自动释放。

导航运行时和局部取放共享同一 `ResourceManager` 时，调用者应在 pick 成功后协调机器人运动租约，在 place 前提交最新底盘位姿、时间和世界状态版本。详见 [机器人接入指南](pick_place/ADDING_ROBOTS.zh-CN.md)。
