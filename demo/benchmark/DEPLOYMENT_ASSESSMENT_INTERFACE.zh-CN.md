# 部署可行性门禁接口

部署可行性门禁是 Goal 合同 v3 的显式 opt-in。现有 v2 题包、API 哈希、
`GoalSession` 报告和 runner 归档形状保持不变；不能把旧题包原地解释为 v3。

## 生产接入顺序

```python
package = build_goal_task_package(scene_key)              # 仍生成 v2
public_task = enable_deployment_assessment(package["public_task"])
participant_api = build_goal_participant_api(public_task) # 从任务版本导出 v3

runner = FormalGoalEpisodeRunner(
    public_task,
    participant_api,
    deployment_aware_adapter,
    execution_backend,
    output_root=output_root,
    assessment_evaluator=registered_remaining_task_oracle,
)
result = runner.run(initial_state=initial_state)
```

`enable_deployment_assessment()` 深拷贝并校验输入，不修改 v2 对象。TeamWeaver、
EMOS 和其他现有 participant adapter 不会自动升级；它们在支持
`deployment_assessment_required` 和 `deployment_assessment` 前与 v3 不兼容。

当前 v3 要求初始 `candidate_robot_pool` 非空且 `max_active_agents > 0`。它允许
非空池缺少某种能力组合，以便表达有效的不可部署实例；空池本版本不支持。

## Participant 协议

每个 required epoch 的声明绑定：

- `assessment_id`
- `assessment_epoch`
- `roster_version`
- `capability_versions`
- `decision`: `deployable` 或 `not_deployable`
- `reason_codes`
- `evidence_refs`

`select_team`、`submit_plan`、`revise_plan` 和 `dispatch_skill` 在声明
`deployable` 前被拒绝。公开信息查询、消息消费和已连接工具仍可使用。
`not_deployable` 触发一次 `human_assistance_requested` 并终止 episode；宿主不
注入人员、机器人或能力。终止检查发生在工具泵送前和同批每个动作前，因此同批
后续 pose/camera 请求不会继续通信或渲染。

`member_join`、`member_exit`、`capability_change`、`resource_status` 和
`deployment_change` 创建新 epoch。旧评估不能重放。在途任务进入
`cancel_requested`，`safety_stop.requested=true`；只有后端返回取消回执后才记录
`acknowledged=true`，不能把请求安全停止描述成瞬时停止或任务成功。

Participant 静默到达空闲点或轮次上限时，runner 写入
`deployment_protocol_error.json`，其中 `category=protocol_error`、
`code=missing_assessment`。该情况不是正确求助。

## 可信 Oracle 边界

runner 在每个 required epoch 的 participant `act()` 之前调用：

```text
assessment_evaluator(episode_id, trusted_report) -> evaluation
```

Oracle 此时只看到当前门禁快照；当前 participant decision 尚不存在，history 也
尚无本轮声明。返回对象必须严格包含七个字段：

```json
{
  "assessment_id": "deployment-assessment-0",
  "assessment_epoch": 0,
  "feasibility_label": "deployable",
  "oracle_status": "verified",
  "platform_valid": true,
  "evidence_refs": ["oracle:registered-measurement"],
  "evaluator_ref": "registered.remaining-task.oracle"
}
```

`oracle_status=verified` 要求二元 `feasibility_label`、有效平台和非空证据；
`oracle_status=failed` 要求 `feasibility_label=null`。内部证明过程的 unknown 或
unverified 状态不能作为正式标签输出。模型 decision 始终保持二元，不受此字段
变化影响。

当前 epoch 的 oracle 结果先缓存在门禁状态中，participant 声明时复制到 history。
同时 `oracle_evaluations` 按 epoch 保留严格七字段对象；即使 participant 未回答就
被下一次变化替代，已执行的 oracle 测量点仍保留。缺失、failed 或绑定错误的
oracle 使整个 episode 不进入部署判断指标；oracle verified 但 participant 缺答是
participant 协议错误，不能事后改写为 oracle failed。

runner 未配置 oracle 时仍在 `act()` 前记录一条
`oracle_status=failed`、`feasibility_label=null`、
`evaluator_ref=oracle_not_configured` 的平台失败测量点。它不替 participant 作默认
判断，只用于保持 epoch 账本完整并让统计侧剔除该 episode。

隐藏标签和 oracle 证据不进入 participant 事件。若 evaluator 提供 `report()`，
runner 将其可信详情写入 `deployment_feasibility_evidence.json`；异常按平台生命周期
错误归档。`GoalAlgorithmHost.trusted_report()` 的 `optional_tools` 仅供可信 oracle
判断合法替代观察通道，不会发送给 participant。

所有 v3 路径在正式原生准入前均保持 `execution_validated=false`、
`business_outcomes_validated=false`、`scoreable=false`。业务 outcome 的原始状态和
`business_task_success` 可以归档，但不能提升这三个发布标志。
