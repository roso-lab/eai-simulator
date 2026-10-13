# HeteroCoord Skill 开发需求（simulator.skill.v1）

## 1. 任务定位

Skill 是仿真系统中的“动作翻译层”。被测模型或 EMOS 输出一份我们预先规定的任务部署字段，Skill 将这些字段翻译为 `simulator.py`、EAI controller、ROS/OmniGraph bridge 能够执行的动作。

```text
被测模型固定输出
    ↓
SkillRequest
    ↓
Skill Provider / Adapter
    ↓
SkillPlan + SkillCommand
    ↓
TaskExecutor / bridge
    ↓
simulator.py + controller
```

Skill 不是新的任务规划器，也不是物理评测器。它可以进行复杂的动作编译、轨迹展开、坐标转换、单位转换、时序组合和 controller 映射，但不能把动作计划直接写成物理成功。

## 2. 开发者必须先使用的合同

现有通用数据结构位于：

```text
algorithm/skills/contracts.py
```

仿真器动作的唯一机器可读 schema 位于：

```text
algorithm/skills/simulator_schema.py
```

当前版本：

```text
simulator.skill.v1
```

不要在 provider 中重新发明字段名、单位、坐标系或 command route。应先使用：

```python
from algorithm.skills.simulator_schema import (
    simulator_skill_schema,
    validate_simulator_skill_request,
)
```

## 3. 固定输入字段

所有 SkillRequest 都必须有：

```python
SkillRequest(
    request_id="branch/repeat/task/0",
    skill_id="eai.navigation.goal_position",
    robot_id="robot_1",
    target_ref="dock_A",          # 可选，按 skill schema 决定
    target_pose=(x, y, z),         # 可选，按 skill schema 决定
    parameters={...},              # 只能使用 schema 已声明的键
)
```

固定上下文包括：

| 字段 | 说明 |
| --- | --- |
| `request_id` | branch 内本次翻译请求的唯一身份 |
| `skill_id` / `skill_version` | 使用哪个固定 Skill schema |
| `robot_id` | 动作执行者，必须属于当前 session roster |
| `task_id` | 上游 assignment 对应的任务身份，由 executor/ledger 关联 |
| `target_ref` | 场景中的目标、物体或区域引用 |
| `target_pose` | 几何目标，使用 schema 声明的 frame 和单位 |
| `parameters` | 该 Skill 的固定参数集合，未声明字段必须拒绝 |
| `simulator_snapshot` | 当前 branch 可见的仿真状态（如 provider 需要） |
| `timing` / `budget` | tick、deadline、动作时长等预算 |
| `branch_context` | `RunKey`、branch role、checkpoint round 等 provenance |

缺少必填字段时返回 `invalid_request`，不能静默猜测或填入未经声明的默认目标。

## 4. 当前已确认的 simulator/controller 动作面

所有 schema 均严格检查版本、向量维数、声明的 frame；pose 四元数必须非零，
joint_names 必须唯一且与位置向量等长。通用 goal executor 仅在 controller 明确
声明 `yaw_command_name` 时发送 `[N,1]` 航向命令，并与 XYZ 同 tick 执行；否则在
任何写入前拒绝带 `target_yaw` 的计划。空中持续技能使用
[AerialSkillRuntime](AERIAL_RUNTIME.zh-CN.md)；当前 XYZ 绑定不支持航向执行。

| 动作 | 输入 | 输出 command | 执行路径 |
| --- | --- | --- | --- |
| 移动目标 | `target_pose=(x,y,z)`、`frame=world`、可选 `target_yaw` | `goal_position`、可选 `goal_yaw` | `base_env.set_command(robot, "goal_position", tensor[N,3])` |
| 底盘速度 | `velocity=[vx,vy,wz]`、`horizon_ticks` | `velocity` | `env.step({robot: tensor[N,3]})` → `compute_action_from_command` → `apply_action` |
| 转子速度 | `rotor_velocity=[u1,...,uK]` | `rotor_velocity` | 直接进入 controller action，K 由 `action_dim` 决定 |
| 机械臂位姿 | `frame_id`、`position[3]`、`orientation_xyzw[4]` | `manipulator_pose` | UR5/Z1 ROS/OmniGraph `target_pose` |
| 机械臂关节 | `joint_names`、`joint_positions` | `joint_position` | UR5/Z1 `joint_command`，按 joint name 对齐 |
| 夹爪 | `gripper_position`、可选 velocity | `gripper_position` | articulation joint target，按软限位裁剪 |

单位要求：

```text
position        m
linear velocity m/s
angular velocity rad/s
yaw             rad
joint position  rad
time            simulation tick / s，必须显式声明
```

## 5. 六类 Skill 的开发要求

### 5.1 Navigation

固定输入：

```text
target_ref 或 target_pose
frame
target_yaw（可选）
horizon_ticks
mode = goal | velocity
```

输出：

```text
goal_position + goal_yaw
或
velocity=[vx, vy, wz]
```

`goal_position` 使用 world frame 的三维目标。`velocity` 必须通过 `env.step()` 的 robot action 传给 controller，不能只写入 `base_env.set_command()`。

当前已完成：

```text
eai.navigation.goal_position
```

当前需要继续补齐：

```text
eai.navigation.velocity
eai.navigation.rotor_velocity
```

### 5.2 Manipulation

固定输入：

```text
mode = pose | joint | gripper
frame_id = world | base_link
position
orientation_xyzw
joint_names
joint_positions
gripper_position
```

输出：

```text
manipulator_pose
joint_position
gripper_position
```

要求：

- 位姿四元数必须 finite，并在发送前归一化；
- UR5 joint command 必须按固定 joint name 对齐；
- gripper command 必须经过 articulation joint limit；
- 不同机械臂 model 的 joint schema 必须通过 model manifest 声明，不能靠位置顺序猜测。

### 5.3 Transport

Transport 是组合 Skill，不是一个单独的 controller action。

固定输入：

```text
object_ref
source_ref
destination_ref
carry_mode
team / robot_ids
handoff_ref（可选）
```

输出是有序 command sequence，例如：

```text
navigation → grasp/close → navigation → handoff → release
```

Skill 负责生成顺序和参数；TaskExecutor/Fleet Runtime 负责执行、锁、handoff 和资源释放。

### 5.4 Inspection

固定输入：

```text
observation_ref
sensor_profile
target_ref
scan_height_m（可选）
scan_yaw_rad（可选）
coverage_requirement（可选）
dwell_ticks（可选）
```

输出：

```text
navigation / pose command
sensor_trigger
```

`sensor_trigger` 只是请求传感器采样，不是“观察成功”。覆盖率、数据质量和目标识别由外部 evaluator 判断。

### 5.5 Coordination

Coordination Skill 将协作阶段转换为 Fleet Runtime 可记录的 typed event，必要时附带低层动作。

固定输入：

```text
robot_ids
phase
sync_tick
handoff_ref（可选）
resource_ref（可选）
```

输出：

```text
handoff_request
handoff_accept
resource_release
```

这些 command 只表示请求、接受或释放事件，不表示 P2 milestone 已由物理 evaluator 认证。

### 5.6 External I/O

固定输入：

```text
interface_id
message_type
payload
endpoint（可选）
timeout_ticks（可选）
sequence（可选）
```

输出：

```text
ros_twist
nav_goal
joint_state
```

适配 Nav2、ROS cmd_vel、UR5/Z1 OmniGraph 等外部接口，但不要在 Skill provider 内启动 ROS 节点或 Isaac App；bridge/runtime 是执行所有者。

## 6. SkillPlan 输出要求

```python
SkillPlan(
    request_id="...",
    skill_id="...",
    robot_id="...",
    commands=(
        SkillCommand(
            command_type="goal_position",
            payload={
                "target_pose": [1.0, 2.0, 0.5],
                "frame": "world",
                "horizon_ticks": 10,
            },
        ),
    ),
    status="planned",
    metadata={
        "schema_version": "simulator.skill.v1",
        "provider_id": "...",
        "provider_version": "...",
        "request_digest": "...",
        "observation_digest": "...",
        "command_route": "...",
        "expected_external_outcome": "...",
    },
)
```

`expected_external_outcome` 是预期结果描述，不是成功证明。真正的 success/failure/unknown 由外部 `EvidenceEvaluator` 返回。

## 7. 错误语义

至少区分：

```text
invalid_request
unsupported_capability
infeasible
degraded
controller_rejected
execution_failure
milestone_unverified
```

错误必须保留：

```text
request_id
task_id
robot_id
skill_id / schema_version
branch/run_key
provider_version
reason
```

## 8. 当前 executor 实现状态

已完成：

- goal navigation adapter；
- goal position command executor；
- velocity command 通过 `env.step()` 进入 controller 的修复；
- schema validation 和 provenance。

尚未完成，需要协作者实现：

- manipulation pose/joint/gripper executor bridge；
- transport composite executor；
- inspection sensor trigger bridge；
- coordination event → Fleet Runtime bridge；
- external_io → ROS/Nav2/OmniGraph bridge；
- rotor velocity action dimension 和 controller-specific validation。

不要把尚未实现的 route 伪装成已完成。没有对应 bridge 时应返回 `unsupported` 或 `invalid_plan`，并保留 artifact/ledger 证据。

## 9. 测试要求

测试放在：

```text
algorithm/skills/tests/
benchmark/tests/
```

最低验收标准：

- schema 中所有必填字段缺失时 fail closed；
- 未声明参数键被拒绝；
- NaN、Inf、错误维度、错误 frame、错误 joint name 被拒绝；
- 相同固定输入产生稳定的 JSON-safe plan；
- `goal_position` 写入正确的 `[N,3]` tensor；
- `velocity` 通过 `env.step` action 进入 controller；
- rotor action 维度与 controller `action_dim` 一致；
- UR5/Z1 pose、joint、gripper 字段与 OmniGraph helper 一致；
- transport command 顺序稳定；
- coordination event 可进入 Fleet audit；
- inspection trigger 不会伪造 evaluator success；
- evaluator 缺失时结果保持 `unknown` / `N/A`，不能变成 success。

## 10. 允许修改的范围

允许修改：

```text
algorithm/skills/
benchmark/adapters.py
benchmark/eai_adapter.py
algorithm/skills/tests/
benchmark/tests/
```

需要先协调再修改：

```text
benchmark/fleet.py
benchmark/runtime.py
source/EAI/EAI/controllers/
simulator.py
```

不允许在 Skill 任务中修改：

```text
EMOS 的任务分配语义
30 个场景 task catalog
外部 evaluator 的物理 predicate
D1--D6 指标公式
replay fidelity 语义
USD 资产和 checkpoint
```

## 11. 交付内容

每个 provider 交付：

1. provider 实现和版本号；
2. 固定输入字段示例；
3. 输出 `SkillPlan` 示例；
4. command route 说明；
5. 错误矩阵；
6. 单元测试和 executor bridge 测试；
7. 不支持项列表；
8. provenance 字段说明。
