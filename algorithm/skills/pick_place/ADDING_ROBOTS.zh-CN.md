# 接入新的 PickPlace 机器人

本文说明如何把一个新的机器人接入统一的局部取放技能。统一入口只执行一次 `pick` 或一次 `place`，由调用者决定是否导航以及何时提交下一次操作。执行完成后，`PickPlaceExecutor.execute()` 只返回 `bool`。

## 分工和公共文件

宿主在创建场景时创建控制器和适配器，然后用 `adapter.robot_id` 注册适配器：

```python
controller = DemoArmController(scene, robot_path="/World/demo_arm")
adapter = DemoArmPickPlaceAdapter(
    robot_id="env_0/demo_arm",
    controller=controller,
    object_bindings={"box": "/World/envs/env_0/box"},
)
executor.register_adapter(adapter)
```

公共状态机位于 `algorithm/skills/pick_place/contracts.py`，执行器位于 `task/executors/pick_place.py`。接入新机器人通常不需要修改这两个文件、`simulator_schema.py` 或公共资源管理器。只有新增公共请求字段或新的跨机器人语义时才修改合同，并同时补充合同测试。

职责固定如下：

* **Executor** 校验请求、把世界坐标转换到底盘坐标、管理 `IDLE → PICKING → HOLDING → PLACING → IDLE`、占用 robot/object 资源、处理超时和取消、请求安全停止，并最终返回 `True` 或 `False`。
* **适配器** 处理本机器人的接近、抓取、抬升、释放、撤离和回收，应用安装外参和抓取偏移，读取实测持物状态，并实现后端协议。
* **控制器** 只负责 IK、关节目标、飞行推力或其他实际运动。控制器必须由宿主的物理循环驱动，适配器不能创建或推进仿真。

同类机器人应优先复用现有适配器，通过配置传入关节、安装位置、工具和能力范围。只有动作阶段、反馈证据或挂接方式确实不同，才新增适配器。公共状态机不能加入机器人型号分支。

## 后端协议

适配器需要有唯一的 `robot_id`，并实现以下方法：

| 方法 | 职责 |
| --- | --- |
| `resource_key(object_ref)` | 把别名解析成稳定的 `object:/...` 资源键；不能创建动作或修改物理状态。 |
| `validate(command)` | 做无副作用的能力、目标、物体身份和当前持物检查；不可达、姿态不支持或物体不一致时抛出异常。 |
| `start(command)` | 启动单次局部操作；命令被接受不代表成功。锁存操作开始时的目标和物体引用。 |
| `before_step()` | 在宿主物理步前写入本适配器的控制目标。只写自己的控制器。 |
| `after_step()` | 在宿主物理步后读取实测位姿、关节、速度和挂接结果，更新内部阶段。 |
| `feedback()` | 返回内部 `BackendFeedback`，包含 `running/completed/failed/stopped`、抓取状态、`ready/stopped/clean` 和原因。 |
| `request_stop(reason)` | 请求安全停止并确认本次轨迹不再推进。已持物时保留连接、物体和必要的保持目标。 |

`feedback()` 的内容只供 Executor 使用；调用者永远只看到布尔值。成功的 pick 必须确认实际抓取、抬升并进入稳定持物，成功的 place 必须确认释放、撤离/回收以及物体稳定。不能把收到控制命令、规划完成或到达名义目标当作成功。

## 坐标和工具约定

所有距离用米，角度用弧度。请求中的四元数顺序是 `xyzw`，合同和适配器内部使用 `wxyz`。`target_pose` 是物体原点的世界位置：pick 是当前实测原点，place 是最终期望原点。它不是桌面、抓取点或腕部位置。可选 `grasp_offset_object_m` 表示物体原点到抓取点的物体局部 XYZ 偏移，适配器再叠加安装外参和末端工具偏移。

Executor 使用调用者采样的完整底盘位姿计算：

```text
p_base = Rᵀ (p_world - t)
```

适配器从 `command.target_position_world_m` 取得锁存的世界目标，从 `command.target_position_base_m` 取得同一采样的局部目标。底盘在外部导航期间可以移动；目标不会随底盘漂移。可选 `orientation_xyzw` 也会转换到采样底盘坐标系。适配器不支持该姿态时必须拒绝请求。

安装外参属于适配器/控制器绑定，不属于调用者重复计算。抓取点、物体尺寸、末端偏移和释放补偿由适配器根据机器人工具和实测状态处理。控制器负责把适配器给出的目标变成关节、推力或双臂运动。

## 宿主循环和注册

宿主应让每个物理 tick 按以下顺序执行一次：

```text
adapter.before_step() → env.step(...) → adapter.after_step()
```

Executor 的 `advance` 回调必须调用宿主已有的物理推进；Executor 和适配器都不应自行调用 `env.step`。同一宿主会话的 Executor、导航和其他技能共享一个 `ResourceManager`。注册示例：

```python
executor = PickPlaceExecutor(
    advance=lambda: env.step(action_buffer),
    simulation_time=lambda: float(env.sim.current_time_step_index) * dt,
    resources=shared_resources,
)
executor.register_adapter(adapter)
```

`robot_id` 是唯一索引，例如 `env_0/demo_arm`。同一个机器人不能注册两个适配器。pick 会持有 `robot:<robot_id>` 和物体资源直到 place 清理完成；运动租约只覆盖当前操作，便于导航调用者在 pick 成功后接管底盘或飞控。导航调用者必须尊重物体租约和适配器要求的持物保持控制。

## HOLDING、失败和取消

pick 返回 `True` 后，Executor 停留在 `HOLDING`，不会自动导航或自动调用 place。调用者可以导航，也可以原地放置；place 请求必须重新提交最新底盘位姿、世界状态版本和仿真时间。持物期间宿主物理循环仍需继续运行，以便适配器维持关节目标、FixedJoint 或实测物体跟随。

失败、取消、超时或反馈未知时，适配器必须按实测状态处理资源：已确认持物就保留连接和物体租约，停止未确认或持物未知时进入故障并保留租约，确认空手且临时状态清理后才允许 Executor 释放资源。布尔 `False` 不能触发自动释放。场景重置后由宿主重新协调现场状态并创建新的会话执行器。

## 最小接入案例

下面的类名是虚构的最小示例；真实实现仍需把每一步接到控制器和物理反馈：

```python
class DemoArmPickPlaceAdapter:
    robot_id = "env_0/demo_arm"

    def resource_key(self, object_ref):
        return "object:" + self.object_bindings[object_ref]

    def validate(self, command):
        # 检查目标、能力、物体实测位置和当前持物状态；失败时抛异常。
        ...

    def start(self, command): ...
    def before_step(self): ...
    def after_step(self): ...
    def feedback(self): ...
    def request_stop(self, reason): ...

controller = DemoArmController(...)
adapter = DemoArmPickPlaceAdapter(
    robot_id="env_0/demo_arm",
    controller=controller,
    object_bindings={"box": "/World/envs/env_0/box"},
)
executor.register_adapter(adapter)

picked = executor.execute(pick_request)
# 外部自行导航或保持原位
placed = executor.execute(place_request)
```

## 必须验证的证据

接入完成前至少要覆盖：注册和资源租约、世界到局部坐标（包含平移、旋转和倾斜底盘）、目标姿态支持边界、非法输入和重复请求；实际抓取、抬升、持续持物、单次释放、撤离/回收和释放后稳定；不可达、反馈错误、超时、取消及持物失败时的资源保留。Isaac 集成验证应记录控制器、适配器、资产哈希和场景限制，并在每个机器人上重复多轮 pick/place。只检查“命令已接受”不能算通过。
