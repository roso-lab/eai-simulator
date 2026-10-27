# 主线收敛与脏工作树审计（2026-10-28）

## 收敛结果

- 本地 `HeteroCoord` 已收敛到 `origin/HeteroCoord@18f0acc0`，工作树干净，后续主线开发只在该分支进行。
- 本次“唯一主线”处置的逐项分支判定见 `MAINLINE_UNIQUE_BASELINE_20261028.zh-CN.md`。
- 收敛前的本地提交链保存在标签 `backup/HeteroCoord-pre-convergence-20261028`。
- 收敛前主工作树的跟踪改动和未跟踪文件保存在 `stash@{0}`（消息：`pre-mainline-convergence-20261028-root-worktree`）。恢复点不作为发布分支，也不进入资格证据。

## GPU 脏改动分类

GPU 脏改动主要来自以下两类内容：

1. `artifacts/project_runtime/gpu-three-way-*`、`artifacts/project_runtime/gpu-room06-vertical-*` 以及相关 `run_failure.json`、检查点和 manifest。这些是历史诊断运行输出，包含失败尝试和资源状态，不能直接提交为源码，也不能升级为正式资格证据。
2. 主工作树或临时 GPU 工作树中的 `tools/validation/gpu_three_way_concordance.py`、`three_way_task_runtime.py`、`three_way_evaluator.py` 及 `factory_native_*` 草稿。它们依赖一组尚未在主线形成稳定接口的运行时模块，当前没有完整受影响测试和生产接缝，因此登记为待审工程草稿，不伪装成已完成实现。

room06 GPU 工作树 `/tmp/heterocoord-room06-real-gpu-MTFYKn` 的 `source/EAI/EAI/room06_runtime_hook.py` 和 `tools/validation/room06_livt04_gpu_probe.py` 与 `origin/HeteroCoord` 对应文件字节一致；其 `simulator.py` 差异来自旧版注册接缝，主线已有更新实现。该工作树的 GPU 目录和运行证据保留待审计，不再作为合并源。

ToDesk 进程是用户授权的共驻显示服务，未被关闭或终止；本次主线收敛没有运行 Isaac/Omniverse，也没有发现活动仿真进程或 telemetry transmitter。

## 其他工作树结论

- `oracle-predicates-binding`、`teamweaver/contract-adapter-20261027`、`codex/room06-livt04-integration`、`codex/room06-real-gpu-vertical-20261027` 的有效内容已由主线后续提交吸收或更新；旧提交不再作为新的合并源。
- `codex/stat-report-producers-v1`、`codex/d4-dual-first-event-dac7b94`、`codex/paper-alignment-dac7b94` 的分支提交与主线文件存在版本差异，但主线包含其后续版本，不能把旧文件回拷覆盖主线。
- `/home/pupil/eai-simulator-worktrees/semantic-map-tool-release`（`tool`）仍有未提交的 vectorized/recovery/process 代码和交接文档；这些改动尚未完成独立验收，保留原工作树，禁止无审查覆盖。
- `/home/pupil/eai-simulator-worktrees/paper-difficulty-dac7b94` 仍有未提交的 difficulty/composite/scorebook 相关修改；该工作树属于活动写入面，待其所有者交接后再审查。

## 后续准入

主线下一步只接受两类输入：

1. 已提交、可定位到具体文件并通过受影响测试的工程改动；
2. 明确标记为 diagnostic-only 的运行证据，放入 artifacts 并绑定 manifest/失败原因。

未提交的 GPU、语义地图或 difficulty 工作树在完成交接和独立审查前，不得直接合并、推送或改变正式资格门；`physical_qualified`、`rankable`、`paper_score_eligible` 继续保持 `false`。
