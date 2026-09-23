# 可选位置工具与通信计量

位置接口是可选 companion 工具，不改冻结 `public_task` 或 `participant_api`。
模型可以不接入、按需查询，或订阅公开周期的更新；不接入/不订阅不会自动采样。
当前实现提供通信与正式 runner 接口，尚未在五场景原生入口全部接线，
`execution_validated=false`、`scoreable=false` 保持不变。

## 接入与调用

可信宿主创建 `GoalPoseTelemetry`、`GoalPoseTools`，将后者通过 `pose_tools=`
传入 `FormalGoalEpisodeRunner`。episode ID 和 public task hash 必须一致。
进程内 adapter 可实现 `initialize_tools(profile)` 接收公开接口；没有该方法则不接入。
`SubprocessGoalAdapter` 把接口放在子进程 `context.tool_profile`，子进程可以忽略。

模型在现有 `act(events)` 返回列表中加入以下 envelope：

```json
{
  "schema": "eai.benchmark.goal_pose_tool_call",
  "version": 1,
  "call_id": "pose-query-1",
  "submitted_at_s": 0.0,
  "tool": "query_pose",
  "arguments": {"robot_ids": ["warehouse_ground_1"]}
}
```

`submitted_at_s` 必须是当前 episode 时间；ID 不与其他工具调用或 Goal action 重复。
支持 `query_pose`、`subscribe_pose`、`unsubscribe_pose`，参数都是非空、不重复的
公开机器人 ID 列表。查询/订阅对象必须仍是当前成员；首次公开加入的成员可在收到
`member_join` 后查询。未来隐藏替补不可查询。退出会停止其订阅。

订阅在接入后的当前可信 tick 首次采样，后续按 profile 的 `subscription_period_s`
安排。重复订阅不强制刷新；取消订阅不召回已经发出的消息。当前只提供一个公开基率，
更细频率选择后置；频率不是按模型身份私下配置。

调用本地接收后，下一回合出现 `observation(kind=pose_tool_result)`，包含调用 ID、
接收状态及查询期限。它不包含位姿、链路条件、待投递内容或是否丢包。
位姿只通过 `observation(kind=pose_update)` 到达；接收状态不表示位姿已送达。
查询期限内没有收到新鲜响应，则出现 `pose_query_timeout`，只列出未收到响应的机器人。
丢包和超期延迟产生相同超时信息，模型不能据此获知隐藏链路原因。

## 数据、时钟和消费

可信 `sample(robot_id, time_s)` 返回以下精确字段：

```python
{
    "sampled_at_s": time_s,
    "position_m": [x, y, z],
    "yaw_rad": yaw,             # [-pi, pi]
    "frame_id": "world",
    "source": "physx",         # 符号后端必须为 synthetic
    "quality": {"position_error_m": None},
}
```

坐标与角度必须有限；frame 与 profile 一致。quality 是可信后端提供的有限 JSON，
未知误差用 null，不默认为无误差。只能读取当前真实状态，不能把区域中心当作实测位置。
每次新采样产生新消息，消息的 `data` 还包含发布时间，外层包含 producer、message ID
与 `valid_until_s`。链路内部投递时间保存在评测器通信账本；Goal observation 的事件时间
是宿主把消息公开的时间，二者不可混算。

宿主只能在实际时钟推进后调用 tick；跳过多个周期时只采当前一次，并记录
`missed_periods`，不补造历史轨迹。当前 runner 的采样范围是 **技能边界和实际 idle tick**，
manifest 明确记录 `runner_boundaries; no intra-skill fixed-rate claim`。
这还不是技能执行期间固定频率的原生流。

可选 `backend.advance_idle(requested_time_s)` 必须真实推进自己的时钟并返回：

```python
{"time_s": actual_time_s,
 "runtime_state": {"robot_regions": current_regions, "object_states": current_objects}}
```

runner 使用返回的真实时间，不把请求时间伪装成采样时间；时间必须前进且不超过 episode
预算。没有该接口、但工具仍需等待时，运行报告明确的基础设施错误，不静默写成完成。
实际 idle tick 对模型可见，使模型有机会重试、取消订阅或继续工作。
House/Warehouse 的名义技能时间与 PhysX 时间尚未统一，不能直接贴上 `source=physx`
并声称完成接线；Restaurant 共享物理时钟可作为后续原生接入起点。

模型可使用现有 `consume_message` 引用已收到且未过期的 pose 消息。
查询与猜测消息 ID 后消费放在同一批动作中会被拒绝，因为该坐标尚未交给模型。
无效 action 或过期消费不得污染 Goal 与通信任一账本。已收到的旧值可以留在模型内存，
benchmark 不声称能删除它；消费/引用记录也不证明模型实际依赖该信息。

## 计量及归档范围

- 位姿响应经过 `CommunicationRuntime`，规范 UTF-8 JSON 按收件副本刚性计量，
  受延迟、丢包、限流和 TTL 影响；隐藏 conditions、队列与未来投递时间不会公开。
- 工具调用的 ingress 字节，以及接收/超时控制响应字节，分别记在 `calls`、
  `control_responses`；它们是模型与 benchmark 本地接口交互，不冒充无线下行传输。
- 模型→机器人派遣/取消的真实下行，以及机器人↔机器人链路服务仍待接入。
  进程内部讨论不可观测，不在此口径中，也不表示其成本为零。

正式 runner 额外写出 `pose_tool_profile.json`、`pose_tools_report.json` 和
`pose_communication_replay.json`；profile hash 绑定 manifest。通信报告保持原始 v2 格式，
运行时调用既有 `validate_communication_report` 检查其重放一致性。
工具控制响应与工具调用不混入该无线通信重放账本；原生接线、跨文件归档验真和
五场景健康/降级业务配对仍是后续验收项。

已加入无接入零采样、按需/订阅/取消、无历史补采、丢包/延迟可见性、查询超时、
成员加入/退出、双账本消费、猜测 ID 拒绝，以及正式 runner idle 时钟与归档测试。
这些使用显式 `synthetic` 样本，不构成原生业务运行证据。

本阶段验证：220 项工具/链路/宿主/runner/session 测试通过；完整
`test_benchmark_*.py` 回归 2102 项通过（231.68s）。随后增加采样异常关闭宿主
检查，工具/宿主 33 项通过（3.85s）；未重复整套回归，未运行 Isaac。
