# Benchmark 准入与整体工程复盘（2026-10-25）

## 结论

HeteroCoord 的核心工程已经进入“语义合同与状态恢复软件闭环完成，真实 GPU 证据尚未完成”的阶段。此前把 GPU compute inventory 中的外部进程直接当作默认准入失败，是把严格 qualification 的环境假设错误地下沉到公开 benchmark 运行入口；它不能作为发布版 benchmark 的默认行为。

当前修正后的分层是：

1. **公开 benchmark（默认 `permissive`）**：允许显示服务和其他 GPU 进程共驻；记录 GPU UUID、compute inventory、ToDesk provenance、显存和资源快照；只有模拟器无法启动、显存峰值余量不足、遥测清理失败等实际资源问题才拒绝运行。
2. **严格资格认证（显式 `strict_exclusive`）**：当论文或认证矩阵需要干净 compute inventory 时才启用，并在 manifest 中冻结；发现外部 compute 才 fail-closed。
3. **ToDesk**：是可选的已知共驻显示服务记录。存在时记录路径、PID、显存和授权理由；缺席时状态为 `absent`，不阻断公开 benchmark；工程不启动、不停止、不 kill 它。

## 已完成

- 24 个锚点合同已接线：104 条谓词，`deadline` 与 `identity_consistency` 已实现；专项 `218 passed, 4 skipped`，工具集 `856 passed, 5 skipped`。
- 三路运行器的软件协议已实现：A 不间断、B 隔离进程恢复、C 共享会话槽位恢复；evaluator-owned 状态、checkpoint、RNG、clock、queue、locks、milestones 和 physical frame 均进入对账。
- Factory native owner graph、live sensor scope、inactive slot 隔离和 CPU-double E2E 已完成；源码闭包 619 entries，fresh import 125 个本地模块、遗漏 0；独立终审 `304 passed`，`P0/P1/P2=0/0/0`。
- 离线 concordance 已通过：`artifacts/project_runtime/gpu-three-way-offline-20261025-15/offline_concordance.json`，A-B/A-C/B-C 全通过；状态仍为 `diagnostic_only`，正式门全部 false。
- `-02` CUDA 诊断收据已登记为 Stage 2 diagnostic-only 证据，不重跑、不覆盖。
- GPU admission 已改为显式模式；新增无 ToDesk、ToDesk+外部 compute、strict 拒绝、显存不足四类边界测试。

## 未完成与真实卡点

- 真实 GPU 三路最小切片尚未取得可发布资格；必须先生成新 manifest、冻结命令哈希并完成一次真实运行。
- 语义地图和 identity mapping 仍未完成，因而合同可执行不等于 24 锚点已获得物理资格。
- 真实 Factory native session 的完整 12 participant 收据、GPU checkpoint readback、clean Kit shutdown、telemetry 清理和三路全链路证据仍待首跑。
- 论文正式实验矩阵（paired reference/fact、fault/sham/intervention、留出恢复、统计覆盖）尚未形成可重算数据包；formal gates、`rankable`、`paper_score_eligible` 和认证 flags 继续为 false。
- 当前工作树有大量用户既有脏改动；本轮只修改 GPU admission 相关源码、测试和进度记录，不 reset/clean/checkout/commit/push。

## 这项开发是否值得

值得，但价值不在继续堆叠更多“检测脚本”。真正有研究与发布价值的部分是三条边界：

- 把论文中的任务语义落成独立 oracle，避免被测系统通过锁服务或自报状态改变判定；
- 把恢复点的 evaluator-owned 状态做成可审计、可重放、可对账的证据链；
- 把公开 benchmark 的可运行性和严格资格认证分离，使共享 GPU 环境不会被无关进程无条件阻断，同时保留需要时的 fail-closed 认证模式。

低价值工作应交给 ZCode 或固定 runner 完成：既有命令的回归、JSON/manifest 一致性、`py_compile`、`git diff --check` 和证据汇总。架构、语义绑定、资格边界、真实 GPU 证据的接受与否仍由主代理和 Sol 审查；不能把机械测试结果升级为论文资格。

## 后续顺序

1. 在默认 `permissive` 模式下重新冻结 GPU manifest；记录共驻 inventory（ToDesk 可 present 或 absent）、显存余量、命令和源码哈希。
2. 只在资源足够、遥测清理通过且三路 worker 尚未启动时处理基础设施故障；模拟进程一旦启动，失败只保留收据，不静默重跑。
3. 完成真实 A/B/C 首跑与 concordance 报告；任何一路不一致都保持 diagnostic-only，并如实保留差异。
4. 之后再做严格 qualification matrix 与论文统计证据；不因软件闭环或离线通过提前打开正式门。

## 新冻结包（尚待用户放行执行）

已用 `/home/pupil/miniconda3/envs/env_isaaclab/bin/python` 在真实 GPU 上重新冻结。兼容性修复后又按源哈希规则换新目录，当前有效包为：

- 输出目录：`artifacts/project_runtime/gpu-three-way-concordance-20261025-17/`
- GPU：Quadro RTX 6000，UUID `GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0`
- admission：`permissive`；ToDesk PID `3945212`，622 MiB，显存 free 21196 MiB，峰值预留 16384 MiB + headroom 4096 MiB，`sufficient_for_peak=true`
- 当前 compute inventory 只有 ToDesk；它被记录为 provenance，不是运行依赖
- manifest SHA-256：`a234ed1656caf37e33becce0b5b84c269914598694cdd0b06a7183917f0c8875`
- command SHA-256：`8640b7e4bb49a6fb5a4a284c7d4675ebb112afb58695fd648744c8b20adccfea`
- approval challenge：`ddb839666ff27468adbdc69dfddc6614a0fb68d179b0ef90a52f53d8dcacbb51`
- `qualification_status` 仍为 diagnostic-only，formal gates 全部 false

第一次用系统默认 Python freeze 因缺少 `torch` 在模拟进程启动前失败，已记录于
`artifacts/project_runtime/gpu-three-way-concordance-20261025-16-preflight-failure/run_failure.json`；随后因兼容性修复造成源哈希漂移，`-16/run_failure.json` 记录了启动前失效，按既有长期授权以新目录 `-17` 重冻结，未改变三路编排或恢复逻辑。

`-17` 首跑已启动真实 Isaac/GPU，因 business RNG checkpoint 的 JSON tuple 复原错误而未形成有效 A/B/C concordance；同时发现 worker 入口在异常路径可能以 0 退出，已增加必需收据检查（A 要求 `frames.json`，B 要求 `restore.json`+`frames.json`，C 另要求 `shared_session_receipt.json`），并新增边界测试。该修复发生在模拟进程已启动之后，因此不自动重跑；新的冻结包只准备到待用户逐次放行。

随后修复了 JSON checkpoint 对 Python `random.Random` 嵌套 tuple 的递归还原，并通过 51 项 focused runner/launcher/RNG 回归。当前待放行包为 `artifacts/project_runtime/gpu-three-way-concordance-20261025-19/`：manifest SHA-256 `13c6f0bbbfe23695636e4cc39c2079a7ef7742818d215ef83f530b34881f0e5b`，command SHA-256 `25945da04bb55f99e842b47bbb431aab5fcc7bd859e8a87f9cb33df6bc6f3071`，approval challenge `f8b4b463841df858e29b9f8ae53539f5dc77a621e3302c9501e0ba304cba3c23`。该包仍使用 `permissive` admission，ToDesk 622 MiB，共有约 21.3 GiB free；formal gates 全部 false。由于之前已经启动过模拟进程，下一次真实重跑必须由用户逐次放行。
