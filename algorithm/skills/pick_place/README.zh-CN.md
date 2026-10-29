# PickPlace 固定动作接口

`eai.manipulation.pick_place` 只执行一次固定的 pick 或 place 动作。导航由调用方单独完成，PickPlace 不接收世界坐标，也不负责路线规划。

```python
from algorithm.skills.contracts import SkillRequest

request = SkillRequest(
    request_id="pick-001",
    skill_id="eai.manipulation.pick_place",
    robot_id="arm_1",
    parameters={
        "operation": "pick",      # 或 place
        "object_ref": "box_1",
        "timeout_s": 30.0,
    },
)
result = executor.execute(request)  # bool
```

请求只允许 `operation`、`object_ref` 和可选的 `timeout_s`。`target_pose`、`target_ref`、底盘位姿、世界状态版本、仿真时间、语义点、`robot_ids` 以及抓取姿态字段都属于其他职责，会被 PickPlace schema 拒绝。

典型流程是：

```text
导航到物体源点 → PickPlace.pick
导航到物体目标点 → PickPlace.place
```

`PickPlaceExecutor` 保留 `IDLE → PICKING → HOLDING → PLACING → IDLE` 状态机。`HOLDING` 是逻辑状态和资源租约，不表示物体被物理抓住。Executor 通过 `HoldingRegistry` 提供 `holding_snapshot()`、`holding_object(robot_id)` 和 `holder_of(object_ref)` 查询。pick 和 place 都必须携带 `object_ref`；place 会校验它与当前 holding 完全一致。

适配器从机器人当前测量状态和内部固定动作模板生成控制器目标：机械臂执行向下/向上或回撤， 无人机执行对齐后的下降/上升，Pepper 执行双臂固定动作，G1 执行校准手臂和手指轨迹。所有后端都不创建 `FixedJoint`、follow proxy，也不写入物体位姿、速度、碰撞或重力。

PickPlace 一次只服务一个机器人。多机器人共同 holding 使用独立的协同运输接口；普通 PickPlace 对需要多个 holding 者的逻辑物体返回能力错误。

代码边界固定为：本目录只放公共契约和 provider；生命周期编排在
`task/executors/pick_place.py`，机器人适配器在
`task/executors/pick_place_adapters/`，动作轨迹控制器在
`task/controllers/pick_place/` 或 `task/controllers/g1_pick_place/`，共享逻辑
对象账本在 `task/runtime/logical_objects/`，无人机实时位姿读取在
`source/EAI/EAI/physics/aerial_pose_reader.py`。
