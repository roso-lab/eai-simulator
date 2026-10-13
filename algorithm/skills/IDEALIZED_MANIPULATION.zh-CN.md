# 理想化机械臂 Skill：柱坐标平面 Pick/Place

理想化机械臂 Skill 的流程是：**柱坐标目标 → 旋转 → 径向移动 → 垂直移动 →
逻辑连接或释放 → 回收**。它服务于单环境（`num_envs=1`）的 UR5 和 Z1
演示，使用机械臂基座局部坐标和简化的平面 DLS IK。它不模拟真实夹持力，也不
承担导航、避障、ROS2 action 或多环境调度。

## 实现位置和运行入口

运行时位于
[`source/EAI/EAI/controllers/idealized_manipulation.py`](../../source/EAI/EAI/controllers/idealized_manipulation.py)，
平面 IK 位于
[`source/EAI/EAI/controllers/ik_controller.py`](../../source/EAI/EAI/controllers/ik_controller.py)。
环境在普通 controller、auxiliary controller 和 `post_apply_action` 之后调用
`IdealizedManipulationRuntime.run_post_apply()`，因此 runtime 是机械臂目标的最后写入者。

十物块 GUI 脚本属于本地诊断入口；它可以从仓库外部运行，但 `/tmp` 下生成的脚本、截图、CSV
和 JSON 都是运行产物，不属于 skill 源码或提交内容。正式代码和接口说明以本文件及上述
controller 模块为准。

## 谁负责什么

| 层次 | 负责内容 |
| --- | --- |
| 调用方 | 提供目标物体 USD 路径、柱坐标目标、回收姿态，并在 `ATTACHED_HOLD` 后移动底盘 |
| `IdealizedManipulationRuntime` | 校验命令、推进阶段、换算基座坐标、管理 follow proxy 和清理 |
| 平面 DLS IK | 在锁定第一关节的前提下求解径向位置、Z 高度和朝下工具姿态 |
| 现有 controller | 执行关节目标和原有执行器限速 |
| 外部 evaluator | 根据真实仿真状态判断演示是否成功 |

runtime 不创建 Isaac Sim 应用，不调用 `env.step()`，不查询 occupancy/costmap，也不自动
规划绕障路线。导航必须在 `ATTACHED_HOLD` 后由调用方接管。

## 坐标和模型规则

公开目标类型为：

```python
from EAI.controllers import CylindricalPose

pose = CylindricalPose(x_m=0.40, z_m=0.35, yaw_rad=1.57)
```

也接受长度为 3 的 `(x, z, yaw)` 序列。`x_m` 是旋转平面内的前伸量，必须非负；`z_m`
是机械臂基座局部高度；`yaw_rad` 绕基座局部 Z 轴旋转，并归一化到 `[-pi, pi)`。

UR5 带固定平面偏置 `b=0.10915 m`，Z1 的偏置为 `b=0`。目标位置由下式转换：

```text
p_base = Rz(yaw) · (x, b, z)
p_world = base_position_world + R_base · p_base
```

基座位姿每个 physics step 从 articulation root 实时读取，因此底盘移动时，回收目标仍然
保持在机械臂基座局部坐标中。目标反解时，横向偏置不足会被拒绝，不会静默退回旧的零偏置
世界 XYZ 接口。

UR5 直接控制 `shoulder_pan_joint` 的 yaw，`shoulder_lift_joint` 到 `wrist_3_joint` 做平面
IK。Z1 直接控制 `joint1` 的 yaw，`joint2` 到 `joint5` 做平面 IK，`joint6` 固定为 `0 rad`。
两种模型都保持工具朝下；yaw 阶段只改变第一关节，不让 IK 用腕关节补偿转向。

## 公开接口

```python
runtime.start_move(
    robot_name, target_pose, *,
    tolerance_m=0.05,
    yaw_tolerance_rad=0.05,
    phase_timeout_steps=300,
)

runtime.start_pick(
    robot_name, object_body_path, grasp_pose, *,
    recovery_pose,
    tolerance_m=0.05,
    yaw_tolerance_rad=0.05,
    settle_duration_s=1.0,
    phase_timeout_steps=300,
)

runtime.start_place(
    robot_name, place_pose, *,
    recovery_pose=None,
    tolerance_m=0.05,
    yaw_tolerance_rad=0.05,
    phase_timeout_steps=300,
)
```

`start_pick` 必须显式传入回收姿态。回收姿态必须满足 `recovery.z > target.z` 且
`recovery.x <= target.x`。`start_place` 默认使用 pick 保存的回收姿态，也可以传入覆盖值。
命令在忙碌、未持物 place、持物时再次 move、非单环境或目标被其他机器人占用时返回
`False` 并记录 `last_error`；格式错误、非法路径和不满足回收约束的参数抛出 `ValueError`。

## 固定阶段顺序

空载 move：

```text
ROTATE → RADIAL_APPROACH → VERTICAL_APPROACH → IDLE
```

pick：

```text
ROTATE → RADIAL_APPROACH → VERTICAL_APPROACH → GRASP
      → LIFT → RETRACT → ROTATE(recovery) → ATTACHED_HOLD
```

place：

```text
ROTATE → RADIAL_APPROACH → VERTICAL_APPROACH → RELEASE
      → LIFT → RETRACT → ROTATE(recovery) → IDLE
```

每个 physics step 只推进一个参考分量。默认参考速度为径向 `0.35 m/s`、垂直 `0.10 m/s`
和 yaw `1.0 rad/s`；关节目标继续使用已加载 controller cfg 的 DLS 参数（默认
`max_delta_rad=0.10`、`dls_lambda=0.02`）和实际软限位。每阶段同时检查平面位置、yaw、工具
朝下姿态和第一关节目标；超过 `phase_timeout_steps` 后进入 `FAILED` 并清理临时对象。

`ATTACHED_HOLD` 是导航接管的唯一条件。进入该状态前，机械臂已经完成上升、径向回收和
回收 yaw；携物期间禁止再次调用普通 `start_move`。

## Follow 搬运和 Z1 夹爪

Skill 只支持 `attachment_mode="follow"`：

1. pick 接受后保存源物体完整 USD 位姿和将临时修改的属性，创建引用原视觉模型的无物理 proxy。
2. 源物体临时关闭碰撞、重力和可见性并清零速度；质量、惯量、质心和材质不修改。
3. `GRASP` 到位后，Z1 夹爪从 `-0.8 rad` 张开目标闭合到 `0 rad`，保持 `1.0 s` 后保存末端与物体的相对位姿并创建逻辑连接。
4. pick 连接完成后夹爪持续闭合，proxy 随末端搬运；源物体和 proxy 都不参与碰撞。
5. `RELEASE` 到位后夹爪重新张开到 `-0.8 rad` 并等待 `1.0 s`，只写回源物体一次，然后恢复属性并删除 proxy。

Z1 的 `gripperStator` 和 `gripperMover` 碰撞在 runtime 期间关闭，reset、close、timeout 或
失败时恢复原属性。Z1 的 `joint6` 始终保持 `0 rad`。UR5 没有夹爪阶段目标。

## 调用方循环

```python
from EAI.controllers import IdealizedManipulationRuntime, ManipulationPhase

runtime = IdealizedManipulationRuntime(env)
runtime.start_pick("robot", "/World/Cube_01", grasp_pose, recovery_pose=recovery)

# 每个 physics step 由环境 post-apply hook 调用
runtime.run_post_apply(env)

if runtime.state("robot") == ManipulationPhase.ATTACHED_HOLD:
    # 现在才允许导航移动底盘；回收姿态仍以机械臂基座局部坐标解释。
    ...
```

调用方应在每个 physics step 提供最新 articulation root 和末端状态，不要写关节状态瞬移，
不要在携物阶段重新捕获带误差的世界姿态，也不要把 `[0, 0, 0]` 当作停止命令。

## 检查与排查

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q \
  source/EAI/test/test_idealized_manipulation_runtime.py

python -m compileall source/EAI/EAI/controllers source/EAI/test
git diff --check
```

重点检查：柱坐标正反转换、UR5/Z1 偏置、阶段顺序、回收安全约束、Z1 夹爪目标、单次释放、
follow proxy 清理和 reset/close/timeout 后的属性恢复。完整 GUI/headless 十物块运行需要
Isaac Sim 5.1、Isaac Lab 2.x、`env_isaaclab`、CUDA GPU 和对应 UR5/Z1 资源；GUI 诊断脚本
不是正式 evaluator，也不能替代这些单元测试。
