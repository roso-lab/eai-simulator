# Pepper transport runtime

The existing provider compiles `simulator.skill.v1` transport requests. The
legacy executor reports commands applied, and the evaluator reports physical
evidence separately.

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
