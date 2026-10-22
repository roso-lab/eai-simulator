# 推荐的向量化共享会话架构

`shared_session`（共享会话）的推荐主路径统一为 `vectorized_session`（向量化会话）。一个场景只打开一个模拟器会话，在会话内分配多个环境槽位；一次批量推进同时处理活动槽位。只读静态资产可以共享，所有可变实验状态仍按槽位归属。

## 分支如何接入被测系统

真实后端实现 `VectorizedBackend` 契约中的以下接口：

| 接口 | 用途 |
| --- | --- |
| `open_scene(scene_id, num_envs, scheduler)` | 打开一组向量化环境 |
| `isolation_config(session)` | 声明静态资产摘要、碰撞/传感器过滤、独立重置、非活动槽位冻结 |
| `open_slot(..., checkpoint, channel)` | 在指定槽位恢复一个分支，并返回 `next_step` 和基于实际 `capture_slot` 的 `state_digest` |
| `capture_slot` / `restore_slot` | 读写十二类版本化参与者状态 |
| `mutable_buffers` | 枚举每个槽位的原生可变缓冲区，用于别名和重叠检查 |
| `compute_actions` / `step_batch` | 先按槽位生成动作，再用一次批量模拟器调用推进 |
| `close_slot` / `close_scene` | 释放槽位和场景 |

十二类状态参与者是 `world`（世界）、`clock`（时钟）、`controller`（控制器）、`skill`（技能）、`task`（任务）、`algorithm`（算法）、`transport`（通信）、`locks`（锁）、`events`（事件）、`rng`（随机数生成器）、`checkpoint`（检查点管理）、`evaluator`（评估器）。每类都必须提供非空 `version`（版本）和映射形式的 `state`（状态）。

适配器生成的检查点状态必须是 `{"participants": {...}, "next_step": 整数}`，检查点摘要必须绑定完整状态。工具在恢复后自行读取参与者状态，与冻结检查点逐项等价比较；后端只返回成功标志或复述检查点摘要不能通过。完整检查点会持久化到基础检查点账本，离线验证再次检查其摘要和初始读回绑定。初始治疗动作应在恢复验收之后由 `compute_actions` 执行，不能在 `open_slot` 提前修改基线状态。

每个槽位都有 `(shared_session_id, environment_slot, slot_generation, slot_token, branch_id)`。动作、批量返回值、步骤、观测、事件和账本行都必须携带同一个槽位令牌。槽位释放后，旧代次的动作、消息和快照全部失效；回溯会恢复参与者、私有消息队列、随机状态和模拟器时钟。

## 并行、恢复和失败边界

采集器先取得场景租约，再取得每个分支租约，随后固定一个批次。所有槽位先恢复，再调用一次 `step_batch`；某一分支的方法终止不会中断其他槽位。批次期间检查非活动槽位的状态摘要，关闭槽位前检查未完成的步骤输出。任何隔离证据不确定、原生批处理异常或场景清理异常都会使整个共享会话进入隔离区，未完成分支记为基础设施失败，并保留未确认的调度容量。

运行时检查覆盖 Python 容器、NumPy 重叠视图和连续 Torch 缓冲区。它不能证明原生物理内核没有执行未登记的跨槽位写入，因此必须再做 `vectorized_qualification.py` 的差分认证：在新进程中逐分支运行参考轨迹，与向量化候选轨迹逐步比较状态摘要、动作、实际动作和观测。缺少事件的随机数使用 `(pair_seed, stream, event_id)` 键控，不会推动后续事件的随机序列。

共享进程仍是共同故障域。Python 进程崩溃、CUDA 上下文损坏或原生模拟器崩溃可能同时影响所有槽位；`process_failure_scope` 会记录为 `shared_session`。这项设计节约场景启动和静态资产复制成本，但不能声称拥有独立进程的崩溃隔离。

### 关闭观察与 unknown-record

生产采集器在关闭场景后为每个已绑定槽位写入独立的 shutdown receipt。后端必须实现 collector-owned 的 `collect_shutdown_observation(session, identity)`，返回由后端实际读回的终止状态和完整 shared-session equivalence receipt。普通 callable、调用者传入的 receipt 或复制后的字典都不能充当观察能力。

后端未提供观察器、场景关闭失败、身份链不一致、快照读回不一致或归一化偏差超过 manifest 容差时，receipt 记录为 `unknown-record` 或 `pair-invalid`；不得把 `pending` 或 caller 自报升级为 `clean`。即使观察成功，当前论文修订和独立差分认证尚未完成，所有正式资格门仍保持关闭。

当前检查模式会执行深拷贝、状态摘要和跨槽位缓冲区比较，存在开销，尚未测量真实 GPU 的显存或吞吐收益。`timeout_s`（超时）在批量调用之间协作检查，不能强行中断阻塞的原生内核。多个场景当前依次调度；同一场景内才是批量并行。独立进程路径保留为差分参考和需要进程故障隔离的后备，旧共享回调仅保留合成测试兼容，推荐路径统一为本契约。

## 证据等级和论文使用

`protocol_synthetic`（协议合成）可使用 NumPy 参考后端验证接口、回溯和日志。`measured_diagnostic`（实测诊断）可以接入真实后端，但仍要求恢复登记、外部评估器证据和设备检查。当前工具拒绝把向量化 `shared_session` 直接标记为正式 `measured`：必须先完成真实模拟器的“独立进程参考 vs 向量化批量”差分报告，并在论文协议中新增向量化隔离认证条款。现有论文中“并行只能在独立模拟器进程中执行”的表述需要同步修订，不能仅修改工具字段。

示例：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m tools.benchmark_branch.main run \
  --config tools/benchmark_branch/examples/vectorized_reference.json \
  --output /tmp/vectorized-reference-run
```

该示例是协议合成后端，不代表 Isaac 或 GPU 的正式实验结果。

`python -m tools.benchmark_branch.vectorized_qualification --reference reference.json --candidate candidate.json --output comparison.json` 可比较外部采集的轨迹。每个文件是 `{branch_id: [{"state": 状态摘要, "row": {"simulator_step": 整数, "action": ..., "applied_action": ..., "observation": ...}}]}`。报告仅认证所提供轨迹的相等性，`provenance_verified=false` 表示不验证文件是否真的来自独立进程；完整实验认证还需要冻结代码、设备、场景、种子和真实进程执行记录。

## 文献依据与协议修订建议（2026-10-20）

- [Isaac Lab（2025）](https://arxiv.org/abs/2511.04831) 已采用 GPU 原生并行物理、传感器和模块化环境；单纯使用向量化环境本身不宜作为新颖性主张。
- [GPUSimBench（2026-07，论文标注 IROS 2026）](https://arxiv.org/abs/2607.13059) 将吞吐/显存、物理一致性、运行间和环境间确定性分别评估，报告 GPU 批量执行可能引入差异。它支持在本工具中把“批量执行成功”和“隔离等价性认证”分开的设计决定，并不证明本项目已达到等价。
- [Isaac Lab 官方场景接口](https://isaac-sim.github.io/IsaacLab/v2.3.2/source/api/lab/isaaclab.scene.html) 单独提供克隆和碰撞过滤配置。真实驱动须依据实际安装版本核验过滤生效，配置布尔值只是声明。

建议论文贡献表述聚焦“在共享模拟器中实现可审计的反事实分支隔离、完整状态回溯、代次隔离和差分认证协议”。这是研究定位建议，尚未完成全面新颖性审查或原生物理实验。

当前论文仓库 `bb82b7bdb1352f1633bc405f41595a4affc79567` 的 `sec/X_suppl.tex:383` 仅允许独立模拟器进程并行。建议新增协议版本，允许经过预登记认证的向量化后端：固定模拟器/设备/资产/种子和数值容差，比较单分支新进程、批量、槽位置换、不同批量规模、邻居动作扰动及局部回溯的结果。离散事件、消息归属、治疗时刻必须严格一致，连续物理量如需容差应事先登记并报告偏差；本版本比较器仅支持严格相等。记录共享会话故障域，并将无资格运行标为诊断。本文档是提案，没有修改论文原稿。
