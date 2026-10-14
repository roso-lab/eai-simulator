# Pepper transport runtime

The existing provider compiles `simulator.skill.v1` transport requests. The
legacy executor reports commands applied, and the evaluator reports physical
evidence separately.

The public skill id is `eai.transport.composite`, implemented only for Pepper.
It carries a configured box with bilateral ideal attachment. Required parameters
are `object_ref`, `source_ref`, `destination_ref`, and
`carry_mode="bilateral_ideal_attachment"`; optional `horizon_ticks` defaults to
1200 and must be a positive integer no greater than 5000. References must be
distinct and match the live controller binding. Undeclared parameters, unsupported
versions, robot identifiers and carry modes are rejected.

```python
from algorithm.skills import SkillRequest
from algorithm.skills.pepper_transport import PepperTransportProvider

request = SkillRequest(
    request_id="move-box-1",
    skill_id="eai.transport.composite",
    robot_id="pepper_1",
    parameters={
        "object_ref": "box",
        "source_ref": "source_table",
        "destination_ref": "destination_table",
        "carry_mode": "bilateral_ideal_attachment",
        "horizon_ticks": 1200,
    },
)
plan = PepperTransportProvider().plan(request)
# These example references must be replaced with the caller's configured names.
# A planned command alone is not a completed transport or physical success.
```

The pure provider emits one `pepper_transport_advance` command, with
`provider_id`, `provider_version`, `schema_version`, `request_sha256` and
`command_route` metadata. `PepperTransportExecutorAdapter.execute(plan, env=env)`
is the legacy bounded execution route for an existing caller-owned environment.
It advances that environment and reports `command_applied`; use the cooperative
handle below for shared task stepping and resource ownership.

For task orchestration, use `PepperTransportHandle` with
`CooperativeBatchExecutor` and `validate_transport_binding`. Select
`PEPPER_BOX_TRANSPORT_TASK_CFG` before opening the caller-owned scene; it starts
gated idle. The caller supplies physical box/support assets, the Pepper fixed
root configuration and pre-initialization robot/box/support collision filters.
The runtime does not build a task scene, substitute targets, or step Isaac.

Completion requires both controller state machines to finish, box release, and
stable measured base/arm/box speeds. Cancellation finishes safe placement on the
existing route, then returns failure. It is not an emergency brake. A controller
error or expired stop budget retains ownership and stops further shared stepping.

`PepperTransportCheckpoint` covers the active controller and handle in the same
session, integrated through `task.checkpoint.SkillRuntimeComponent`. Register all
active lease owners, restore physical state first, reconcile the ideal attachment,
and stop the run on any restore failure. It cannot resurrect released resources
or recover a running task in another process. Full integration and validation
boundaries are documented in [the runtime guide](../../../task/RUNTIME_GUIDE.zh-CN.md).

The separate [G1 skill](../g1_pick_place/G1_PICK_PLACE.zh-CN.md) accepts two world
positions and runs in a paused USD scene. Its kinematic execution loop cannot be
substituted for this live Pepper handle. See the
[framework integration comparison](../GRASP_SKILLS_INTEGRATION.zh-CN.md).

## 并行环境统一抓放

通过 `task.executors.manipulation.ManipulationSkillRuntime` 将 Pepper 与机械臂、G1
统一接入任务系统。机器人身份包含 env row，旧 Pepper 控制器每行独立加载并用
局部坐标视图转换 clone origin，底层写入显式限定 env_ids。
共享 executor 与 runtime 必须使用同一 ResourceManager。
统一 XYZ 请求仍需匹配已有 Pepper 箱体和路线绑定。
详见 `algorithm/skills/GRASP_SKILLS_INTEGRATION.zh-CN.md`。
