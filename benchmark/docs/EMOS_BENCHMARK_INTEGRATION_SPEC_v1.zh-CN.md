# EMOS 被测系统适配器附录 v1（零号实现）

本附录只描述 EMOS 作为被测系统的**特有实现**。接入合同的统一语义（输入输出类型、
初始化生命周期、计量、fail-closed 边界、观测服务、序列化 envelope）一律以
[《被测系统接入规范 v1》](TESTED_SYSTEM_INTEGRATION_SPEC_v1.zh-CN.md) 为准；
该规范对所有被测系统（EMOS、TeamWeaver 及后续第三方）生效，EMOS 不是接口的
定义者，只是第一个实现者。

## 适配器形态（`benchmark/emos.py`）

- `EmosDecisionAdapter`：fire-rescue 兼容路径，保留给旧入口；不得用于把 room_06
  等新场景强行转换成 fire-rescue 语义。
- `EmosCaseDecisionAdapter`：通用 case 适配器，签名对齐统一协议
  `initialize(H_r, o_r)` → `(task_package, robots) -> AlgorithmDecision`。
  两类适配器都只产出 typed `AlgorithmDecision`，不产出物理成功事实。

## 边界

EMOS 只负责历史重建后的条件分配与决策输出；benchmark/EAI 负责能力执行、锁服务、
物理观测、里程碑、oracle、回放和终局。EMOS 输出不能被当作任务成功或物理收据。

## EMOS 特有约定

- **初始化预算**：`initialization_budget_calls` 默认 1，便于新集成先在最小预算下
  验证 fail-closed；耗尽即拒绝。
- **历史重建后的条件分配**：EMOS 在 `initialize` 完成历史重建后才允许分配；
  `o_r.robot_positions` 必须与绑定名册 robot IDs 完全一致，否则初始化拒绝。
- **模型端点**：`model_endpoint(payload)` 可选；payload 只含历史、当前观测、场景
  ID 和 robot IDs；必须返回 mapping，调用次数受初始化预算计量；模型异常或非
  mapping 结果直接 fail-closed；默认 fallback 为无外部模型的确定性 EMOS 分配。
- **锁服务**：EMOS 不直接取得或解释 typed-lock grant/deny；oracle 的位置占用
  账本不因 EMOS 的分配输出或锁应答豁免。

## 观测 opt-in

EMOS 零号适配器按通用规范的观测伴侣协议 opt-in 相机观测（`initialize_camera_tools`
钩子 + 事件驱动 `act(events)`，动作白名单 `eai.benchmark.goal_camera_tool_call` 与
`consume_message`）；计量、终态、回执与存证语义见通用规范第 5 节，此处不重复定义。

## 当前限制

room_06 × liv-t-04 的合同层 CPU dry-run 接口已具备，独立回归覆盖通用 case 适配器
的历史/观测绑定、模型端点计量和 roster fail-closed；真实 case dry-run 仍需等待
最终语义地图和 evaluator-owned task package。该 dry-run 不产生论文结果。
