# G2 持续推进反思与当前状态（2026-10-24）

本记录承接三路 GPU 等价认证 goal。它只记录工程状态与停止边界，不是论文正文，也不授予任何正式资格。

## 已完成

- 分支 A 的 6 个试点、19 条 oracle 谓词已合并；C-P2-02 离线 witness replay 的 3 个共同锚点均为 `satisfied`。
- Oracle 阶段通过独立 Sol 复审：46 passed、4 skipped，未发现 P0/P1/P2；formal gates 仍为 `false`。
- `source-collector-cuda-20261023-02` 已登记为 diagnostic-only，历史收据未覆盖。
- `-04` 已留下真实 Isaac/GPU A 路 frames；B/C 在模拟器启动后因错误调用 `verify_all()` 失败，失败收据保留，未静默重跑；`-05` 仅冻结未执行。
- 三路对账器现对 evaluator-owned 字段缺失、物理 shape 不一致、精确字段不一致和帧数截断 fail-closed；离线 focused 回归当前通过。

## 当前未完成

- 最新 Overleaf 远端为项目 `6ab4c7c4e984abb8da9c8113` 的 `0b006d4efcd25d5160d4b0a6e75cddd9e5265e44`，而工程文档/任务目录/scorebook 仍以 `72b6a3b975ae83743dc1d447b99edd728cb1dcdc` 为基线；必须先完成身份和差异映射，不能冻结旧身份的 G2 manifest。
- G2 尚无可接受真实收据：B 路跨进程恢复尚未覆盖完整 evaluator-owned 状态，C 路仍是新建普通 session，不是真实 shared-session slot restore。
- native concrete `NativeSlotStateBridge` 尚未接入；当前 `LiveCheckpointBackend` 只有有限 command/RNG 等状态，不能声称覆盖 world/clock/controller/skill/task/algorithm/queue/locks/events/RNG/checkpoint/evaluator 全集。
- G3 isolated audit、G4 paired pilot、G5 P4 认证目录/校准、G6 P5 归因与留出恢复均未开始。

## 卡点与停止规则

1. 最新论文身份漂移是 G0 阻塞；不能以 72b6a3b 生成新的正式实验身份。
2. shared-session 语义缺少生产级 slot bridge；不能把普通 session 改名为 shared-session。
3. checkpoint/evaluator 状态覆盖不足；缺字段必须拒绝，不能用空字典或占位值补齐。
4. `-04` 已在模拟进程启动后失败；后续逻辑失败必须保留证据并暂停，不适用“启动前基础设施修复可自动续跑”规则。
5. ToDesk 仅作为同条件共驻显示服务白名单，不能放宽三路身份、显存余量、telemetry 清理或外部 compute 检查。

## 流程反思

之前流程把授权和运行请求放得过早，且在 native bridge、论文身份和 evaluator-state contract 尚未验收前冻结/尝试 GPU，造成用户频繁介入但信息增益有限。改进后的顺序是：论文身份审计 → 离线结构闭环 → 独立 adversarial review → 新目录 manifest/hash 冻结 → 一次用户放行 → 真实 GPU slice；模拟器启动后的逻辑失败只停不重跑。这样把权限请求压缩到真正不可逆的边界，同时保留每次失败的可审计收据。

## 资格边界

本阶段所有 `physical_qualified`、`process_isolation_qualified`、`native_full_state_qualified`、`paper_score_eligible`、`rankable` 及相关 formal gates 均保持 `false`。

