# 三路等价认证离线验收（2026-10-24）

本记录只证明运行器的结构语义和 fail-closed 对账逻辑，不是 GPU、物理或资格证据。

## 通过项

运行命令：

```text
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m tools.validation.gpu_three_way_concordance --offline --output artifacts/project_runtime/gpu-three-way-offline-20261024-01
```

结果收据：[offline_concordance.json](/home/pupil/HeteroCoord/artifacts/project_runtime/gpu-three-way-offline-20261024-01/offline_concordance.json)

- A 不间断、B JSON 边界后的独立恢复、C 共享槽位恢复的共同尾段逐字段一致；最新收据同时比较 A–B、A–C 与 B–C；
- `physical` 按 `tau_replay=1e-6` 比较，clock、RNG、command、ledger、queue、locks、observation、skill、milestones 等 evaluator-owned 字段严格比较；
- 删除任一 evaluator-owned 字段的负探针被拒绝，未降级为空字典或占位值；
- `qualification_status=diagnostic_only`，所有 formal gates 仍为 `false`。

## 真实运行边界

- `gpu-three-way-concordance-20261024-04` 在模拟器启动后于恢复路径失败，已保留 `run_failure.json`，不适用启动前基础设施自动续跑规则；
- `gpu-three-way-concordance-20261024-05` 只有冻结收据，没有执行；其 runner hash 已因离线探针和论文身份护栏变更而过期，不能直接放行；
- 2026-10-24 最新只读审计时 ToDesk 不在进程/GPU compute inventory；不得自行启动或 kill。只有用户控制的 ToDesk 再次驻留后，才能在新目录重新登记当时的 PID、路径与显存；
- 工程核心 `PAPER_COMMIT/PAPER_SHA`、24 锚点合同和 composite 原语已更新为 `0b006d4…`，但 D5 全方向目录和 common-anchor 覆盖的语义迁移尚未完成；运行器状态仍为 `migrated_identity_semantics_pending`，因此护栏继续拒绝真实执行。

下一步必须先完成论文身份迁移、更新合同/运行器 manifest，并由独立审查确认后，才重新冻结新目录并处理一次真实 GPU 放行。

## 本轮追加收口

- `SharedSessionSlotBridge` 已加入运行时契约；没有注册 slot bridge 时，C 路明确拒绝新建普通 session fallback。
- slot identity（`shared_session_id`、`environment_slot`、`slot_generation`、`slot_token`、`branch_id`）和 state digest 均强校验。
- comparator 对缺字段、非有限值、畸形数组和非 mapping 的 `physical` 输入均返回失败收据，不再抛异常或误判通过。
- 最新离线收据位于 `artifacts/project_runtime/gpu-three-way-offline-20261024-09/`；它冻结 runner、runtime-state、native evaluator、slot bridge、project runtime、source collector 与 simulator 指纹，三对比较全过，仍仅为 `diagnostic_only`。
- 2026-10-24 19:57 冻结新 GPU 目录时 ToDesk 已不在 compute inventory；该启动前准入失败记录见 [gpu-three-way-concordance-20261024-07-preflight-failure.json](/home/pupil/HeteroCoord/artifacts/project_runtime/gpu-three-way-concordance-20261024-07-preflight-failure.json)，未重启或 kill ToDesk。
- `FactoryNativeSlotBridge` 已绑定进 C 路 runner，但真实 Isaac session 仍未注册 `factory_native_slot_runtime`/12 个 participant providers，且 Factory V2 仍拒绝 `num_envs=2`；因此 C 路继续 fail-closed。

## 2026-10-24 增量闭环

- A/B 的 `LiveCheckpointBackend` 现在注册 runtime buffers、显式 simulation clock 和 native evaluator component；新进程恢复必须逐位回读 evaluator state 与时钟。
- native evaluator 由 `open_simulator_session` 在显式诊断环境合同下注册，状态包含任务/重复/checkpoint 身份、typed queue/lock、observation history、skill 与 milestone order/ticks；空 `{}` 自认证被拒绝。
- 执行入口会复算 manifest internal digest、manifest 文件 hash、精确 release command hash、全部 frozen source/simulator/asset hash；任一漂移在子进程启动前拒绝。
- timeout、route 异常和 preflight/tamper 异常均写 `run_failure.json`；route 结束通过 `finally` 清理 `omni.telemetry.transmitter`，并保留 simulator launch/session-opened 分类。ToDesk 从不在清理目标内。
- `env_isaaclab` 增量回归为 `117 passed`；正式资格门仍全为 false。
