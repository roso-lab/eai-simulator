# 框架持续推进阶段收尾与地图到位执行状态（2026-10-27）

## 本阶段结果

本阶段把 `origin/HeteroCoord` 的窄集成提交快进到 `HeteroCoord` 分支，未触碰主工作树的用户脏改动：

- canonical contract source 已纳入 Git：`artifacts/catalog/contracts/room06-extension-20261026/pilot_contracts-v3.json`；
- room_06 × liv-t-04 evaluator seam 已接入通用 receipt/material 层；
- 地图到位后的唯一执行顺序已记录在 `MAP_ARRIVAL_EXECUTION_RUNBOOK_20261027.zh-CN.md`；
- 当前远端 `HeteroCoord` 已推进至 `7de17ad1`。

## 验收证据

ZCode（GLM-5.3/max，`env_isaaclab`）在干净集成 worktree
`/tmp/heterocoord-integration-20261027-kocuLI` 完成固定检查：

| 检查 | 结果 |
|---|---|
| 五个 benchmark focused modules | 首跑因 worktree 外部写入造成 1 个瞬时缺失；文件就位后重跑 `54 passed`，exit 0 |
| room06 evaluator + receipt/oracle reduction | `24 passed`，exit 0 |
| `compileall -q benchmark tools/benchmark_branch` | exit 0 |
| `git diff --check` + status | exit 0，worktree clean |

首跑缺失不是代码失败：`pilot_contracts-v3.json` 在 Step 1 结束约 45 秒后才落盘；文件纳入后单测和完整 Step 1 均重跑通过。该过程性失败按 fail-closed 规则保留在 ZCode 验收记录中。

环境复核：

```text
Python /home/pupil/miniconda3/envs/env_isaaclab/bin/python
torch 2.7.0+cu128, CUDA 12.8, torch.cuda.is_available=True
lark 1.3.1
```

## 地图 smoke 与边界

对已验收快照 `origin/HeteroCoord@7f44a187` 的 room_06 地图做只读 smoke：

- `audit_semantic_map(..., semantic_points=[floor1-bookshelf, floor2-bookshelf-1/2/3])` 返回 `case_ready=true`；
- 12 个语义点、50 个 ground topology 节点、63 条边，save-id/version chain 一致；
- 四个书架点均有导航绑定；
- 用同一 binding、历史证据转正后的 27 台名册重建 manifest，输出与既有 manifest 字节一致：
  `manifest_digest=ded55b75f3d6e3d73924e913984e3300fc0dfb5b040aaffb6841514a7cc5fbbb`；
- `book_batch_01` 与 `heavy_object` 均按 task-generated object binding 绑定，不需要 USD prim 提取。

当前主工作树 `room_06/semantic_map/current` 仍由协作者写入，拓扑为空、书架点缺失；它只能等待最终快照，不能作为正式输入。上述快照和 CPU diagnostic 证据均不升级任何资格门。

## 地图到位后的最短路径

```text
final map audit
→ rebuild case manifest
→ EMOS initialize(H_r, o_r) dry-run
→ evaluator-owned terminal artifact / raw receipt
→ receipt bridge + oracle/obligation reduction
→ fixed panel projection
→ D1–D6 calculation and export/replay
→ CPU closure accepted
→ native GPU A/B/C evidence
```

仍然未完成且不能由 synthetic/CPU 结果替代的项目：真实 evaluator terminal artifact/raw receipt、artifact manifest、D2 C0–C3 完整目录、D3 四类 relation ledger、D4 healthy/loss/recovery closure、D5 固定 family/case/unit 支持量、D6 独立 calibration，以及 GPU A/B/C native restore 证据。正式 gates 继续保持 `false`。

