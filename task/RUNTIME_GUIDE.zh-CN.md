# 通用执行与恢复接入

这里组合现有 task 和 skill 接口，不定义正式任务合同、物体语义、业务成功谓词或
实验条件。任务设计方继续提供 `EaiTaskPackage`；运行接入方提供以下适配器。

## 组装入口

```python
from task import ResourceManager, SkillTaskExecutor, TaskRunRequest, TaskRunner

# session 由 simulator.open_simulator_session(...) 的调用方持有。
# task、planner、replan、resolve、observe、evaluator 全部来自调用方。
resources = ResourceManager()
executor = SkillTaskExecutor(
    resolve,
    resources=resources,
    observation_provider=observe,
    expected_scene_id=task.scene_id,
)
runner = TaskRunner(
    scene_provider=lambda scene_id: session,
    observation_provider=observe,
    orchestration_executor=executor,
    evaluator=evaluator,
)
result = runner.run_orchestrated(
    TaskRunRequest(task), planner, replan=replan,
)
```

每个运行/分支建立新的 executor。runner 在打开 scene 或调用 replay 前执行
`executor.bind_run(request)`，拒绝跨运行复用，避免重复请求记录和旧观测泄漏。
session 生命周期仍由调用方管理，运行器不会自动创建或关闭 Isaac。

同一 scene 内需要互斥的执行器必须共享一个 `ResourceManager`。manager 只有进程内
资源记账，不是跨进程锁，也不替代 GPU scheduler。`SkillTaskExecutor.execute_batch` 按顺序执行完整
skill；它没有实现同时推进多机器人 skill，也没有硬超时或抢占式取消。

## Live observation

`observe(session)` 返回 `WorldSnapshot(scene_id, revision, tick, robots, objects)`，
使用关键字参数。机器人使用现有 `RobotState/RobotCapability`；objects 是调用方
定义的 JSON 观测，不在该层规定“箱子”“桌子”等结构。

- `revision` 是采样版本，`tick` 是仿真步号，均为非负整数；同一版本的内容必须相同。
- scene 必须匹配当前任务，revision/tick 不得回退。新的运行或 checkpoint 分支
  使用新 executor/新 runner 调用，不能向正在运行的观测流偷偷注入回溯状态。
- runner 在初始规划前、派遣前和派遣后采样。派遣后必须重新测量并提供更大的
  revision；即使请求在产生物理动作前被拒绝，也应重新采样，tick 此时可以不增长。
  重复读取同一个派遣前样本允许保持 revision，不能拿它充当执行后的新观测。
- sampler 负责正确采样，单纯递增数字不证明测量真实。访问对象返回独立副本，
  planner 不能修改 sampler 内的状态。executor 的独立采样也校验 expected_scene_id。
- replan 收到更新后的 task/robots，原始任务包保持不变；完整观测位于
  `task.metadata['runtime_observation']`。外部 evaluator 同样收到最新 task。
- 此入口用于 `run_orchestrated`；一次性 `run` 保留原有静态输入行为。

## 任务到 skill 的绑定

`resolve(assignment, task, *, scene)` 返回 `BoundSkill`，包含：

| 字段 | 调用方责任 |
| --- | --- |
| `request` | 使用已有 `SkillRequest`，robot 必须与 assignment 一致；每次实际重试使用新 request_id |
| `provider` | 对应 skill provider，只编译 `SkillPlan`，不推进仿真、不修改 request |
| `validate(scene=..., request=...)` | 验证 live 对象、controller、资源和能力绑定；无效时抛异常，不能执行动作 |
| `resources` | 本次操作需独占的规范化资源 ID，例如物体/放置区域；executor 自动补上 `robot:<robot_id>` |
| `execute(plan, scene=...)` | 在 owner thread 内调用 runtime，返回 `ExecutionReceipt` |
| `stop(scene=..., request=...)` | 可选的异常停止回调，只有确实确认停止才返回严格布尔 `True` |

该层不从 description 或任意 metadata 猜测 skill 参数。Pepper provider/executor 已存在，
但其 `command_applied` 返回值不能直接转成 `completed`：接入方必须核实实际控制器阶段、
请求目标以及是否仍持物或运行。未经适配的 runtime 不能靠缺省成功回执接入。

`ExecutionReceipt` 必须包含 request/skill/robot 身份、`DependencyOutcome`、
`quiescent` 和 `FailureCode`。quiescent 指本次执行已不再使用所申请资源，可以安全
移交；函数返回、机器人暂时不动都不足以证明这一点。仍持物的阶段不能释放物体占用；
跨阶段持物应由上层组合 skill 保持一份完整租约，直到安全交接。

completed 必须配 `FailureCode.NONE`；失败必须有失败码。即使 completed 也只是
依赖推进，物理成功仍由独立 evaluator 判断。未接 evaluator 时 verdict 为 N/A。

## 异常与占用恢复

资源申请是全有或全无。冲突返回 retryable_failure，不执行动作；runner 只接受
replan 明确提出的重试。已发出的 request_id 在本 executor 内不得再次执行。

runtime 异常后执行 stop：确认停止才释放资源；无法确认则隔离租约、保留占用，返回
terminal failure。错误类型/身份的回执即使停止成功也保持 terminal，不能自动重试。
`KeyboardInterrupt/SystemExit` 同样尝试停止并处理占用，随后继续抛出。

现场处置确认 quiescent 后，调用方可取 `executor.quarantined_leases` 中的原始租约，
调用 `resources.recover(lease, quiescent=True)`，再显式 `resources.release(lease)`。
recover 只解除隔离，不会停止机器人、自动释放或证明物理安全。伪造/旧租约不能释放
别人的资源。任何隔离租约都阻断后续共享物理步：即使别的机器人有空闲资源，
推进同一 scene 仍会让未确认停止的 controller 运动。调用方不得绕过执行器继续 step。
租约身份和请求去重保留在当前进程；不能通过序列化记录重建已释放或其他进程的租约。

runner ledger 记录观测 digest 和 DAG/反馈；`executor.ledger` 记录请求 digest、
资源申请、开始执行、停止/隔离及释放。调用方需将两者一并纳入实验 artifact。
这些记录描述协议执行，不替代物理原始观测和 evaluator artifact。

## 协作步进

`CooperativeBatchExecutor` 接受同样的 `resolve`，但返回 `CooperativeBinding`：
`execute/stop` 替换为 `start(plan, *, scene)`，返回一个非步进 handle。

```python
from task import CooperativeBatchExecutor, CooperativeBinding

executor = CooperativeBatchExecutor(
    resolve, resources=resources, observation_provider=observe,
    step=lambda session, actions: session.env.step(actions),
    neutral_actions=make_all_robot_neutral_actions,
    expected_scene_id=task.scene_id, max_ticks=1800, max_cancel_ticks=1800,
)
```

handle 的 `command()` 只返回自己机器人的动作，`after_step()` 返回 None 或严格
匹配身份的 `ExecutionReceipt`，`cancel(reason)` 只提出取消，不代表已停止。
每次共享 tick 合并全体动作，只调用一次 step；已结束机器人的动作由 neutral map
补齐。全批次先验证/规划，再获取资源、复验绑定和启动。发生未确认停止时，全场景
停止继续推进，尚在运行的 handle 一并隔离；不会让另一任务的 step 带动故障 controller。

只有共享 `step` 成功返回，才调用该 tick 的 `after_step()`。neutral map 或任一
`command()` 构造失败时整批不 step，下一次有界尝试重新构造动作；step 抛异常时
不能把旧观测累计为稳定停止的采样。失败尝试仍消耗取消预算，持续失败会隔离，
不会因为没有成功物理步而无限重试。step 在部分推进后抛异常时也按未确认处理，
执行器无法推断底层到底推进了多少子步。

在动作构造、物理推进和回执回调返回的边界检查隔离；回调触发的 checkpoint
隔离优先于同一回调返回的完成回执，不能释放占用或继续另一机器人的物理步。
`KeyboardInterrupt/SystemExit` 立即终止正常循环，只尝试不步进的取消并继续抛出原异常；
已启动但未确认结束的租约隔离，尚未启动且未被外部隔离的租约释放。中断本身
不是物理停止证据；调用方应暂停/关闭 session，恢复前必须明确完成现场对账。

在 owner-thread 的 step 回调内可调用 `executor.cancel(reason)`。下一边界开始
取消排空；`max_cancel_ticks` 是取消尝试上限（包含失败/未推进的尝试），耗尽仍未
停止则隔离。安全取消后的调度结果是失败，
不解锁业务依赖；外部 evaluator 可以分别记录箱体已被安全放下。硬墙钟超时需使用
外层进程 supervisor，不能通过 tick 限额打断阻塞的 Python/Kit 调用。

## Pepper 与导航

Pepper 调用方选择 `PEPPER_BOX_TRANSPORT_TASK_CFG`，默认 execution_enabled=False；
builder 已注册该名称。保留原 `PEPPER_BOX_TRANSPORT_CFG` 的演示自动启动行为。
这两个配置都不创建箱体或桌面，任务方需通过 scene/场景 hook 提供刚体、支撑物、
适配的 Pepper 根关节固定配置及初始化前碰撞过滤。禁止为适配器自动注入预设任务。

`algorithm.skills.pepper_transport.runtime` 提供 `validate_transport_binding` 和
`PepperTransportHandle`。必须是原有 Pepper 控制器；请求的 box/source/destination
名称严格匹配当前 controller，箱体与支撑物必须存在于 live scene。只有 idle、
未持物、被 gate 暂停的状态可以启动；不支持用任意新目标替换已加载 controller 的路线。
handle 等待既有状态机完成、箱体释放，底盘、双臂和箱体实测速度连续满足停止条件后
关闭 gate。持箱取消沿已验证的原路线完成放置，再报告取消失败；这不是即时急停，
也不适用于该路线已不可用的紧急处置。后者应暂停 scene/终止隔离进程并人工对账。

导航 `NavigationProvider` 只包装注入的旧规划器；地图、轮廓、版本由调用方明确提供。
接受二维位置或 z=0 的三维位置，不支持高度/终端朝向目标。旧栅格规划器的终点偏差
必须通过 `endpoint_tolerance_m` 显式配置，且不得大于 handle 的到达半径；最终检查
仍针对原始目标。`NavigationHandle` 包装旧 tracker、world pose/velocity 采样与控制器
动作转换，不创建地图或实现新的规划算法。它不提供多机器人避碰保证。

## 同 session checkpoint

`task.checkpoint.SkillRuntimeComponent` 实现现有 replay component 协议，可传入
`LiveCheckpointBackend(..., components=(component,))`。`skills` 以 lease owner
（即 request_id）为键，完整覆盖当前全部租约；每个 adapter 实现
capture_state/validate_state/restore_state。Pepper 提供 `PepperTransportCheckpoint(handle)`。

调用方必须在完成 env.step、暂停进一步推进的 owner-thread 边界执行。该组件记录
scene/依赖指纹、真实租约身份和技能状态；资源占用或 adapter 集合变化即拒绝恢复。
物理 backend 先恢复关节/刚体/目标并读回，然后该组件恢复状态机、持物标志、
controller 缓冲与 handle 计数，调用 `reconcile` 核对持物位置。任意后续 component
或最终读回失败都会调用 `on_restore_failed()`，锁定运行并隔离资源。

这是同一 live handle 的可观测状态回退，不回退 scheduler 的请求历史、调度预算或
观测版本；调用方持续增加观察 revision，原始事件和尝试记录不删减。已经结束的
handle、已经释放/转移的租约和新 session 不允许恢复。跨进程恢复、PhysX 隐藏缓存、
完整因果状态与轨迹等价性仍需任务方的状态覆盖与协议，不能据此声称严格配对。

## 验证与范围

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q task/tests
```

测试覆盖状态刷新、资源争用、请求去重、失败后显式改派、独立 evaluator、跨分支
复用拒绝、错误回执和未确认停止的占用隔离。测试中的任务为通用协议 fixture。
临时 Isaac 场景和结果只保存在 `tmp/`；后续阶段见
[开发清单](DEVELOPMENT_TODO.zh-CN.md)。
