# 机械臂、G1、Pepper 统一抓放运行

基于 HeteroCoord `34456e76`，三类机器人共用
`SkillRequest → 纯 Provider → SkillPlan → CooperativeBinding → handle → 外部 evaluator`。
推荐统一 skill `eai.manipulation.pick_place`，输入 `object_ref`、物体原点的世界米制
`pick_position[3]` 与 `place_position[3]`，可选 `frame="world"`。
Provider 不访问仿真；后端由显式注册的机器人绑定选择。

| 后端 | 执行机制 | 绑定与能力条件 |
| --- | --- | --- |
| arm | 现有 UR5/Z1 柱坐标规划、关节目标、理想附着 | 已安装 IdealizedManipulationRuntime；注册物体、工具偏移 |
| g1 | 影子 USD 规划、真实 articulation 关节驱动、测得掌部姿态门控 | 固定基座；标定一致的 Inspire 资产与物体握持配置 |
| pepper | 原有双臂抓箱与底盘搬运状态机 | PEPPER_BOX_TRANSPORT_TASK_CFG；注册箱体与支撑面；目标符合已配置路线 |

三类后端具有相同的任务运行范围：单任务、并行环境和多机器人团队均使用
`ManipulationSkillRuntime`，后端只提供 command/after_step/cancel，统一宿主每 tick
调用一次 `env.step`。模型的可达范围、物体形状和路线能力仍由各绑定明确校验。
不能把统一接口理解为任意物体/任意坐标均可执行。

## 克隆与团队运行

任务身份使用 `env_0/g1_1`、`env_1/pepper_1` 等；底层场景资产键仍为 `g1_1`、
`pepper_1`。读取保留单行 batch，所有关节/物体写入传真实 `env_ids`。
机械臂和 Pepper 各环境拥有独立状态机；Pepper 既有局部场景配置由视图平移到
对应 clone，调用者的统一请求仍使用包含 clone origin 的世界坐标。

同一 ResourceManager 持有 `robot:<scoped robot id>` 和
`object:<absolute clone prim path>` 租约。同环境争用同一物体会拒绝；不同克隆同名
物体可并行。普通环境步进不会自动启动 task-owned Pepper 控制器。
同一环境中的多个 runtime 自动复用资源管理器，并拒绝不同管理器；租约所有者
必须匹配请求 ID。每次共享物理步进前统一校验未完成任务，失效租约不能借其他
机器人的步进继续执行；尚未解决的机器人任务禁止被新任务覆盖。
取消持物任务继续到已注册放置点；完成要求释放物体且测得机器人/物体停止。
错误和停止预算耗尽交给 CooperativeBatchExecutor 保留/隔离租约。
选择性环境 reset 使对应任务失效，调用方须显式恢复场景并协调旧租约后重试。
统一 runtime 不提供跨进程恢复；旧 Pepper checkpoint 保留原单环境用途。

```python
from algorithm.skills import SkillRequest
from task.executors import ManipulationRobotBinding, ManipulationSkillRuntime
from task.executors.cooperative import CooperativeBatchExecutor

# env 和 stage 由调用方创建，cfg 已加载适当控制器。
# G1 objects 值为 ObjectBinding；arm / Pepper 为绝对/template prim path。
runtime = ManipulationSkillRuntime(env, registered_bindings, stage=stage)
request = SkillRequest('transfer-1', 'eai.manipulation.pick_place', 'env_0/g1_1',
    parameters={'object_ref': 'bottle', 'pick_position': [0.28, -0.46, 1.24],
                'place_position': [0.30, -0.06, 1.24]})
binding = runtime.binding(request)
# 在任务 resolver 中返回 binding；共享同一资源管理器。
executor = CooperativeBatchExecutor(resolve=resolve_registered_task,
    step=runtime.step, neutral_actions=runtime.neutral_actions,
    resources=runtime.resources, max_ticks=6000, max_cancel_ticks=6000,
    expected_scene_id=scene_id)
# 按 task/RUNTIME_GUIDE.zh-CN.md 绑定 run 与 caller-owned scene，再执行 assignment。
```

G1 资产配置入口为 `EAI_assets.robots.g1_inspire_manipulation.calibrated_g1_cfg`，
要求显式解析资产路径并核对标定 SHA；主控制器使用
`EAI.controllers.fixed_manipulation.FixedManipulationControllerCfg`。
实时执行不改写活跃 articulation 的 USD link transforms；原
`G1PickPlaceSkill` 和随机杆/哑铃/线轴演示继续作为暂停 USD 的独立几何验证入口。
旧 skill `eai.manipulation.g1_pick_place` 与 `eai.transport.composite` 可接统一 runtime；
运行时需要显式 object_ref 以锁定物体租约。

## 验证与成功含义

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest algorithm/skills/tests \
  algorithm/skills/pepper_transport/pepper_transport_tests.py task/tests \
  source/EAI/test/test_environment_view.py \
  source/EAI/test/test_idealized_manipulation_runtime.py -q
python tools/validation/g1_shared_manipulation_smoke.py \
  --asset /absolute/g1_29dof_with_inspire_rev_1_0.usd --report /tmp/g1-shared.json
python tools/validation/pepper_shared_manipulation_smoke.py \
  --asset /absolute/pepper.usd --report /tmp/pepper-shared.json
```

2026-10-14 上述完整回归命令：523 项通过。原生运行验收：G1 两个环境、各两台机器人，四项任务均在 1164 个
共享 tick 后完成，最大放置误差约 0.779 mm；Pepper 两个环境、各一台机器人，
两项任务均在 613 个共享 tick 后完成，放置误差约 0.00024 mm。Pepper 场景包含
带重力与碰撞的 8 kg 箱体和支撑桌面，基座由固定根节点的虚拟平面控制器驱动。
机械臂本轮覆盖逐环境状态机与统一调度回归，未新增原生机械臂场景验收。

原生 G1 smoke 配置两个环境、各两台机器人，使用真实关节驱动与测量门控；
为单独验证驱动/调度，测试载荷关闭重力与碰撞。其结果不认证接触抓取或承重。
三类 backend 的物体关联均为理想附着。完成回执用于任务调度；
`physical_success` 由独立 evaluator 确认，缺少证据时为 unknown。

参见 [G1 标定与暂停几何接口](g1_pick_place/G1_PICK_PLACE.zh-CN.md)、
[Pepper 原路线接口](pepper_transport/README.md)、
[机械臂模型约束](IDEALIZED_MANIPULATION.zh-CN.md)。
