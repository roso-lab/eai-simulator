# 可选按需相机工具与 Orsus idle_destroy

相机接口是可选 companion 工具，不改冻结 `public_task` 或核心 `goal_action`。
模型不接入则零请求、零模型帧、零 `camera` 信道 link bytes。首版仅单帧
`request_observation`（可选 `cancel_request`），不做连续订阅，不计图像 token。

默认视觉源为 Orsus RGB（render product 采集）。本仓库同时提供纯 Python
假后端用于静态单测；**原生 Orsus/RTX 路径仍须实机验收**，不得用静态测试冒充。

## 接入

可信宿主创建 `GoalCameraTelemetry`、带 `CameraCaptureBackend` 的
`FakeOnDemandCameraBackend` / `OrsusOnDemandCameraBackend`，以及
`GoalCameraTools(..., backend=...)`，经 `camera_tools=` 注入
`GoalAlgorithmHost` / `FormalGoalEpisodeRunner`。
进程内 adapter 可实现 `initialize_camera_tools(profile)`；没有该方法则不接入。

Backend 协议（owner 线程）：`capture` / `tick` / `close` / `lifecycle_report`。
每个 tools `tick` 推进 backend idle release；`close` 幂等终态化并关闭自有 RP。

调用 envelope：

```json
{
  "schema": "eai.benchmark.goal_camera_tool_call",
  "version": 1,
  "request_id": "cam-req-1",
  "submitted_at_s": 0.0,
  "tool": "request_observation",
  "arguments": {
    "robot_id": "warehouse_ground_1",
    "sensor_id": "camera_rgb_primary",
    "modalities": ["rgb"],
    "deadline_s": 5.0,
    "resolution": {"width": 64, "height": 48},
    "quality": {"encoding": "png", "max_bytes": 200000},
    "freshness": "capture_at_or_after_submit"
  }
}
```

`quality` 仅允许 `encoding` 与 `max_bytes`。`deadline_s` 必须 **严格大于** 当前时间且
不超过宿主 `budgets.simulation_s`。首版不支持 `requested_sim_time_s`（`unsupported_field`）。
不对模型暴露 `payload_ref` / 本地路径 / `file://`。

## 成员与隐藏身份

可信宿主持有完整 **私有** sensor registry（可含 `fixed_hidden_identity` 候补）。
`public_profile` / 公开 `sensors` / `robot_ids` **仅**列初始公开池与已激活成员。
参与者不能枚举或自行注册隐藏 ID。

`GoalCameraTools.register_public_robot` **已禁用**。唯一合法激活路径是
`GoalAlgorithmHost` 在处理已提交的 GoalSession `member_join` 事件时调用：

```text
activate_joined_member(event, session_event_index=N, time_s=now)
  -> profile_delta（仅含该新成员 sensors/预算/profile hash）
  -> adapter.update_camera_tools(delta)（优先）
  -> 或公开 observation(kind=camera_tools_profile_delta)
```

同一 `session_event_index` 幂等。未知 ID 拒绝且不写入 public_profile。

## 异步与终态

- `accepted` ≠ 已投递。只有仍在 awaiting 且未过 deadline 的请求可向模型暴露
  `camera_frame`。timeout / cancelled / failed / inactive_robot / episode_ended
  之后的迟到 transport 帧一律抑制；已计入 CommunicationRuntime 的 offered/link bytes
  **保留**，不回滚、不伪装 drop 原因。
- 每个 accepted 请求恰有一个终态：`delivered` / `timeout` / `cancelled` /
  `failed` / `episode_ended`。
- 请求 `deadline_s` 必须 `> now` 且 **≤** 宿主注入的 `budgets.simulation_s`。
- Runner idle 对 camera transport event、camera deadline、pose event、simulation
  budget 取最小正时间（`CommunicationRuntime.next_event_time_s`），禁止固定 50ms
  忙轮询。
- Episode 结束 / `close()`：先将未完成请求记为 `episode_ended`，再扫描 ledger
  兜底任何非终态行；`close` 幂等且不重复终态。`_episode_finalized` 之后
  submit/cancel/activate 在写 call ledger 之前拒绝。
- Lifecycle housekeeping 失败记为 `camera_lifecycle_error` / `lifecycle_errors`，
  **不得**让本 tick 已形成的 `camera_frame` 消失。backend `close` 区分
  logical closed 与 `cleanup_pending`；重复 close 可重试剩余 owned product，
  全部释放后 `cleanup_complete`；external owner 永不 destroy。

## 预算口径

| 字段 | 单位 |
| --- | --- |
| `quality.max_bytes` | **原始** payload 字节 |
| `max_payload_bytes_per_episode` | **原始** payload 累计 |
| CommunicationRuntime link/offered | **规范 UTF-8 JSON**（含 base64） |

发布前先按原始长度与 episode 剩余 raw 预算校验，再编码；**仅在
CommunicationRuntime 成功接受消息后**写入 cache。失败候选不留 cache。
首版 telemetry **拒绝** `payload_ref`。

## Orsus render lifecycle（idle_destroy）

无未完成请求且 idle timeout 到期后，在后续 `tick` 中 destroy **benchmark 自有**
product。external/shared owner 不得被销毁。遥测分列：
`render_product_active_s`、create/destroy、warmup、first_frame_latency。

**不得**声称“无请求不传输 == GPU 已节省”。

## 归档

`camera_tool_profile.json`、`camera_tools_report.json`、
`camera_communication_replay.json`。`execution_validated` / `scoreable` 保持 false。
