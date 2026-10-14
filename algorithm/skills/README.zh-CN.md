# EAI 技能

`eai.manipulation.pick_place` 是唯一的局部取放公共技能。执行入口为 `PickPlaceExecutor.execute(request) -> bool`，每个请求只完成一次 `pick` 或 `place`；导航、运输路线和何时调用下一次 `place` 由调用者决定。

宿主创建控制器和 `PickPlaceAdapter`，按 `adapter.robot_id` 注册，并注入已有物理循环与仿真时钟。Executor 负责请求校验、世界到采样底盘坐标的统一转换、资源租约、局部状态机、超时取消和安全停止，公开结果始终只有布尔值。机械臂、无人机、Pepper 和 G1 适配器保留各自的接近、抓取、抬升、释放、撤离及实测反馈。

地面和空中导航是独立技能，由调用者显式选择。公共 registry 保留导航、局部操控、检查、协同和外部 I/O 合同，已废弃的完整搬运 skill ID 已移除。

参见[局部 PickPlace 指南](pick_place/README.zh-CN.md)和[机器人接入指南](pick_place/ADDING_ROBOTS.zh-CN.md)。
