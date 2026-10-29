# 接入新的 PickPlace 机器人

PickPlace 公共请求只包含 `operation`、`object_ref` 和 `timeout_s`。调用方先用导航到达工作区域，再提交 pick 或 place；适配器从机器人当前测量状态和内部固定动作模板生成控制器目标。

```python
request = SkillRequest(
    request_id="pick-001",
    skill_id="eai.manipulation.pick_place",
    robot_id="env_0/demo_arm",
    parameters={"operation": "pick", "object_ref": "box_1", "timeout_s": 30.0},
)
```

宿主创建控制器和适配器，然后按 `adapter.robot_id` 注册：

```python
adapter = DemoArmPickPlaceAdapter(robot_id="env_0/demo_arm", controller=controller)
executor.register_adapter(adapter)
```

## 分工

- Executor 管理 `IDLE → PICKING → HOLDING → PLACING → IDLE`、超时、取消、逻辑 holding 和资源租约。
- 适配器读取当前机器人状态，选择固定的下降、上升、回撤或返航动作，并返回实测稳定反馈。
- 底层控制器只负责 IK、关节目标、飞行目标或其他机器人运动。适配器不能推进仿真。

适配器需要实现：

| 方法 | 职责 |
| --- | --- |
| `resource_key(object_ref)` | 返回稳定的逻辑物体资源键。 |
| `validate(command)` | 无副作用地检查机器人能力和当前状态。 |
| `start(command)` | 启动固定动作轨迹。 |
| `before_step()` | 在物理步前写入机器人控制目标。 |
| `after_step()` | 在物理步后读取实测位姿、关节和速度。 |
| `feedback()` | 返回 `running/completed/failed/stopped` 和逻辑 `empty/holding`。 |
| `request_stop(reason)` | 停止本次动作并确认控制权已释放。 |

## 物理边界

PickPlace 不接收也不计算世界目标坐标。适配器不得读取或写入 payload 的位姿、速度、碰撞、重力或刚体状态，不得创建 `FixedJoint` 或 follow proxy。`HOLDING` 只表示逻辑任务状态和资源占用。

## 失败和 holding

pick 成功后，调用方自行导航到目标点，再提交带同一 `object_ref` 的 place。place 会校验机器人当前 holding 与请求一致。失败、取消或超时时，holding 状态进入 `uncertain`，资源保留到宿主完成恢复；不能因为返回 `False` 就自动释放资源。

通过 `HoldingRegistry` 可以查询：

```python
executor.holding_snapshot()
executor.holding_object("env_0/demo_arm")
executor.holder_of("box_1")
```

PickPlace 一次只服务一个机器人。多机器人共同 holding 使用独立协同运输接口。
