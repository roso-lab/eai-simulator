# PickPlace 任意角度接口说明（已退出公共契约）

当前公共 `eai.manipulation.pick_place` 是固定动作接口。调用方只传机器人、`pick/place`、逻辑物体 ID 和超时时间；世界坐标、抓取姿态、接近方向和物体尺寸不再进入请求。

本文件中旧的 `target_pose`、`grasp_pose_object`、`approach_direction_world` 和笛卡尔抓取字段只可作为底层控制器内部实现参考，不能作为公共 PickPlace 请求字段。若某个机器人需要任意角度或特殊姿态，应把这些参数放入该机器人的内部动作配置或另设专用控制接口，不得扩展 PickPlace 公共 schema。

当前固定动作阶段由适配器决定，例如：

```text
机械臂：当前姿态 → 向下 → 向上/回撤
无人机：当前位置对齐 → 下降 → 上升
Pepper/G1：机器人配置的固定手臂轨迹 → 回收
```

所有阶段都只写机器人自身控制目标，不创建 payload `FixedJoint`、follow proxy，也不读取或修改物体刚体状态。
