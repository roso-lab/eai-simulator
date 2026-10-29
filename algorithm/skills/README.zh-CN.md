# EAI 技能

`eai.manipulation.pick_place` 是唯一的局部取放公共技能。执行入口为 `PickPlaceExecutor.execute(request) -> bool`，每个请求只完成一次 `pick` 或 `place`；导航、运输路线和何时调用下一次 `place` 由调用者决定。当前 PickPlace 是动作执行模式：机器人完成接近、下降/上升或回撤轨迹，不创建 payload 固定关节，也不移动物体。公共 `HOLDING` 只表示逻辑状态和资源租约。

宿主创建控制器和 `PickPlaceAdapter`，按 `adapter.robot_id` 注册，并注入已有物理循环与仿真时钟。调用方只提供机器人、动作类型、逻辑物体 ID 和超时时间；Executor 负责逻辑 holding、资源租约、局部状态机、超时取消和安全停止，固定动作几何由适配器和底层控制器内部配置。机械臂、无人机、Pepper 和 G1 适配器保留各自的动作轨迹及实测反馈。

地面和空中导航是独立技能，由调用者显式选择。公共 registry 保留导航、局部操控、检查、协同和外部 I/O 合同，已废弃的完整搬运 skill ID 已移除。

参见[局部 PickPlace 指南](pick_place/README.zh-CN.md)和[机器人接入指南](pick_place/ADDING_ROBOTS.zh-CN.md)。
