# 机器人抓放接入

统一入口是 `PickPlaceExecutor.execute(request) -> bool`。宿主显式创建控制器和适配器，用 `executor.register_adapter(adapter)` 注册；适配器通过 `robot_id` 绑定到一个具体机器人实例。

Executor 只管理请求、坐标转换、`IDLE → PICKING → HOLDING → PLACING → IDLE`、资源、超时取消和最终布尔结果。适配器负责机器人专属的接近、抓取、抬升、持物、释放和回收；控制器负责 IK、关节目标、飞控或双臂运动。导航由外部调用者选择，持物期间继续运行宿主物理循环。

UR5/Z1 复用 `ArmPickPlaceAdapter` 与 `IdealizedManipulationRuntime`；PegasusIris/PegasusX4 复用 `DronePickPlaceAdapter` 与 `PhysxAerialTransportBridge`；Pepper 使用 `PepperPickPlaceAdapter` 和 `PEPPER_PICK_PLACE_CFG`；G1 使用 `G1PickPlaceAdapter`、G1LiveRuntime 和现有规划器。底层文件保留，完整搬运入口和型号分支不再作为公共调用路径。

新机器人请按 [ADDING_ROBOTS.zh-CN.md](pick_place/ADDING_ROBOTS.zh-CN.md) 完成控制器绑定、适配器实现、注册和验证。验证必须包含实际抓取和抬升、持续持物、单次释放、撤离、取消、超时和资源保留。
