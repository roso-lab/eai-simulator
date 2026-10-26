# EMOS 被测系统接入规范 v1

当前规范已对应 `benchmark/emos.py` 的初始化/计量接口。旧的
`EmosDecisionAdapter` 仍保留 fire-rescue 兼容路径；通用场景使用
`EmosCaseDecisionAdapter`，避免把 room_06 等任务强行转换成 fire-rescue 语义。
这两类适配器都只产出 typed `AlgorithmDecision`，不产出物理成功事实。

## 边界

EMOS 只负责历史重建后的条件分配与决策输出；benchmark/EAI 负责能力执行、锁服务、物理观测、里程碑、oracle、回放和终局。EMOS 输出不能被当作任务成功或物理收据。

## 初始化

```python
request_after_init = adapter.initialize(H_r, o_r)
```

`H_r` 是 evaluator 提供的历史重建映射，`o_r` 是当前公开观测映射，至少含 `robot_positions`，且必须和绑定名册的 robot IDs 完全一致。初始化受 `initialization_budget_calls` 限制，预算耗尽 fail-closed；初始化完成后才允许条件分配。

## 决策输出

`EmosDecisionAdapter(request_after_init)(task_package, robots)` 返回 `AlgorithmDecision`，包含 typed `Assignment`、未分配任务、条件记录和 provenance。它不能写入物理成功字段。

## 模型端点

`model_endpoint(payload)` 是可选的能力运行时计量端点。payload 只含历史、当前观测、场景 ID 和 robot IDs；端点必须返回 mapping，调用次数由初始化预算计量。模型异常或非 mapping 结果直接 fail-closed；默认 fallback 为无外部模型的确定性 EMOS 分配。

## 锁服务

EMOS 不直接取得或解释 typed-lock grant/deny。锁请求、占用账本和占用谓词属于 benchmark evaluator；oracle 的位置占用账本不因 EMOS 的分配输出或锁应答豁免。

## 当前限制

room_06 × liv-t-04 的合同层 CPU dry-run 接口已具备，独立回归覆盖通用 case
适配器的历史/观测绑定、模型端点计量和 roster fail-closed；真实 case dry-run
仍需等待最终语义地图和 evaluator-owned task package。该 dry-run 不产生论文结果。

## 观测调用（on-demand camera）

被测系统（含 EMOS 零号适配器）通过**宿主持有的可选观测伴侣**获取机器人相机画面。观测是计量服务，不是免费感知。初始化签名对齐：`initialize_camera_tools(profile)` 在 `adapter.initialize(H_r, o_r)` 之后、首个 `act(events)` 之前由宿主调用；未实现该钩子视为未 opt-in——零帧、零流量、相机调用被拒（`camera tool interface was not connected`）。可选钩子 `update_camera_tools(delta)` 接收成员加入增量；无钩子的被测系统仍会收到 `observation(kind=camera_tools_profile_delta)` 公开事件。

每回合事件驱动：`act(events)` 返回的动作只允许相机工具调用（`eai.benchmark.goal_camera_tool_call`，tool=request_observation）与 `consume_message`；低层控制被拒。请求与回执（`SkillRequest(camera_ref, mode=capture, deadline, resolution, quality)` → 观测回执）：`accepted ≠ delivered`，每请求恰一终态（delivered/timeout/cancelled/failed/inactive_robot/episode_ended）；拍摄占用 5 仿真拍（登记值 capture_cost_ticks）；quality 仅 encoding 白名单+max_bytes，deadline 严格大于当前且不超仿真预算，`requested_sim_time_s` 返回 unsupported_field；已计入字节保留不回滚，终态后迟到帧抑制；回执只含 sha256 摘要、元数据与内容寻址 artifact 身份，绝不含 payload_b64/payload_ref/本地路径；隐藏候选 ID 不可枚举，唯一激活路径=宿主成员加入事件按会话序号幂等激活（capability_version 严格递增）。消费收据须显式 `consume_message`；收据不是决策依赖的证明。

计量：观测字节进通信账本（CommunicationRuntime 计量、BranchLedger 记账、ObservationArtifactStore 存证）；被拒请求零痕迹。宿主实现：`benchmark.observation_episode.ObservationEpisodeDriver`；执行边界 `algorithm.skills.observation.runtime.CameraObservationRuntime`；注册条目 `eai.observation.camera`（kind=inspection，subscription=False，execution_validated=False，scoreable=False）。视觉源=各机器人 builder 已配置默认相机（`BuilderCameraBackend`，carter 实机已取证）；假后端仅用于静态测试。本期不做：M2 连续订阅、发现类锚点、历史时刻拍摄。
