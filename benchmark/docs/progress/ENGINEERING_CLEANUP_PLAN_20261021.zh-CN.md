# 工程整理与分批提交计划

日期：2026-10-21

本记录对应结构整理阶段。它只登记归属、依赖和提交边界，不把未验收代码或运行产物
推入远端，也不改变任何运行逻辑。

## 当前基线

- 工作分支：`HeteroCoord`
- 远端：`origin/HeteroCoord`
- 已同步提交：`f6b9ca3d8cde3eac185d85f91e7113bdf3fd5336`
- 论文基准：`1f329494e755523692f5f96c8e0889c6cec15a82`
- `origin/main`（`8c56dde1`）与当前工程没有共同祖先，不能作为本工作树的直接快进推送目标。

## 未跟踪文件的归属分组

| 批次 | 目录/内容 | 当前处理 | 进入条件 |
| --- | --- | --- | --- |
| A | `benchmark/*_v3.py`、`benchmark/fixtures/`、对应 v3 测试 | 保留本地 | 逐模块所有权确认、完整回归、论文基准检查 |
| B | `tools/benchmark_branch/` 的 vectorized、recovery、shutdown watchdog 及测试 | 保留本地 | 修复 timeout sticky 终态、observer 时序、cleanup failure、fresh import 后形成完整依赖闭包 |
| C | `tools/project_runtime.py`、`tools/run_tests.py`、`tools/validation/project_local_*`、`task/tests/test_navigation_measurement.py` | 保留本地 | 项目边界与 GPU 运行入口复核；不得把 CPU 诊断升级为正式资格 |
| D | `requirements-dev.txt` 与 `tools/physics_replay/validation/cache_benchmark.py` | 保留本地 | 明确依赖用途和安装影响后单独提交 |
| E | `artifacts/` | 不提交整目录 | 仅在单个收据被明确登记、可重算且获批准时单独选取 |

tracked dirty 文件（包括 `README.md`、`BENCHMARK.md`、runtime、navigation、旧
`benchmark_branch` 文件等）混合了前序用户修改和阶段实现，当前不整体提交。每个批次必须
先按文件内 hunk 复核归属，避免把用户已有修改带入阶段提交。

## `tmp/` 待用户确认的对象

以下对象已完成其临时使命，但本轮只列出，不删除：

1. `/home/pupil/HeteroCoord/tmp/tool-replay-optimization`：工具回放优化 worktree，
   当前提交 `c51e86d756210e9f76623db25fbf090d282b6234`，约 249 MB。
2. `/home/pupil/HeteroCoord/tmp/tool-vectorized-isolation`：工具向量化隔离 worktree，
   当前提交 `90451fddd0b5b70c17df7dee4e1f363091fdb92b`，约 249 MB。
3. `/home/pupil/HeteroCoord/tmp/CVPR2026_Template`：旧论文模板副本，约 36 MB；它不是
   当前 Overleaf 镜像的工作目录，也不应作为最新论文基准。

删除前需确认其中没有未保存的用户材料。确认后再逐个精确删除，并清理相应 worktree
登记；本轮不执行删除。

## 当前 R7 阻断

独立审查发现：watchdog 在 shutdown 已超时后，如果 shutdown 函数或 deferred observer
随后抛异常，可能把更严格的 `shutdown_timeout` 覆盖成 `unknown-record`。该终态优先级
必须保持 sticky；修复完成并补测试前，R7 不形成可推送提交。工具 worktree 的旧版本还
不能作为主工程 R7 的完成依据，后续修改只进入 `/home/pupil/HeteroCoord`。

## 推送顺序

1. 结构文档（已完成并推送：`f6b9ca3d`）。
2. 项目边界/场景生成小闭包（前一提交 `71769d7f` 已完成）。
3. R7 完整依赖闭包，修复并验收后单独提交。
4. v3 评价器与目录/统计模块，按模块所有权分批提交。
5. GPU G1–G6 收据和正式资格门，只有真实 GPU 证据齐全后才进入发布分支。

所有阶段提交都推送到 `origin/HeteroCoord`；不会把本地 dirty 工作树整体提交，也不会
修改论文正文。

## 阶段反思

本阶段完成了可逆的结构整理和安全推送，但没有把“文件存在”误当作“代码可发布”：
R7 的超时终态覆盖缺口、mixed dirty tracked 文件和临时 worktree 都被保留为明确边界。
下一阶段应先修复并独立验收 R7 的 fail-closed 语义，再讨论任何运行代码的提交；真实
Isaac/GPU 运行仍按 GPU-first 计划执行，并在每次结束后清理遥测残留。
