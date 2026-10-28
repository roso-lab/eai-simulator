# FMC-ATA 统一入口执行及多任务域审计

2026-10-28。仅修改 FMC-ATA 自有 adapter、测试和本目录记录；没有修改共享
factory、CLI、registry、上游或其他方法，没有提交或推送。

## 结论

`benchmark.native_algorithms.build_native_adapter("fmc_ata", ...)` 和
`python -m tools.algorithm_adapters.run --algorithm fmc_ata ...` 均已真实执行原生
`solver_fmc_distributed_asy.FMC_ATA`，并经过 `EaiTaskPackage -> AlgorithmDecision -> TaskRunner`。
没有共享 factory 错误。源校验回执为 commit
`7872a94776cfe21cf58cadf44007382deb650eea`，全部原生源文件前后哈希一致。

当前支持的实际上是**多个独立任务，每个任务一个 mission、max_players=1**，
而非整个 package 只能有一个任务。4/6 任务以及同一机器人执行多项任务已实测；
无需扩展或改写算法。仅澄清 adapter docstring，并为异常新增
`native_result = evidence` 别名以适配共享 CLI 已有的失败输出协议。

## 已保存的输入输出

本目录顶层 `summary.json` 是第一次固定 7 组、14 次调用的记录：factory 返回
3 次决策、4 次失败；CLI 返回 4 次决策、3 次失败。`cli_run_4_tasks.json` 是更早的
单独成功 smoke，配套 `_input.json`、`_config.json`、`_invocation.json` 已保存。

`with_failure_evidence/summary.json` 是增加异常别名后的第二批固定 7 组、14 次
调用，未覆盖第一批证据：

| 案例 | factory | 真实 CLI |
|---|---|---|
| 2 任务、异构能力 | 返回完整决策 | 返回完整决策 |
| 4 个独立任务 | 返回完整决策 | 原生日程不一致，拒绝 |
| 6 个独立任务 | 返回完整决策 | 原生日程不一致，拒绝 |
| 修改位置、工作量、重要性的 6 任务 | 返回完整决策 | 返回完整决策 |
| NCLO=1 | 原生预算失败 | 原生预算失败 |
| max_players=2 | 输入域拒绝，未求解 | 输入域拒绝，未求解 |
| 单任务/单机器人 | 超时并回收 | 超时并回收 |

第二批共 6 次决策返回、8 次失败，其中 2 次是输入域拒绝而未运行 solver。
两批之间的原生结果差异来自异步执行，未重试择优；factory 与 CLI 是各自独立
的真实调用，不能把某条路径的成功或失败冒充另一条路径的输出。

每个案例目录含 `task.json`、`config.json`、`factory_report.json`、
`cli_report.json`、`cli_invocation.json`。后者保存完整 argv、工作目录、退出码、
stdout/stderr。成功输出含源校验回执及原始分数分配、玩家/任务日程、消息上下文。
第二批 CLI 原生异常保存 `rejected_native_result`，对应**同次失败求解**；工厂报告
用 `native_error_evidence` 保存其自身失败。输入域拒绝没有伪造原生输出。
第一批共享 CLI 未读取 `evidence` 字段，部分失败报告只有错误文本；第一批记录
保留原样，不回填其他运行结果。

## 多任务审计

- adapter `native_adapter.py:128` 遍历整个 `task.graph.tasks`，没有任务数等于 1
  的约束；`solver.add_tasks_list(tasks)` 后仅调用一次 `solver.solve(0)`。
- 上游 `simulation_abstract_components.py` 的 `TaskSimple.missions_list` 和
  `solver_abstract.py` 的 `AllocationSolver.add_tasks_list/solve` 原生支持多任务。
  上游 `solver_fmc_distributed_asy.py:444` 的日程计算及 `:458` 的
  `update_schedule_for_simulation_player` 原生输出有序的 `(task, mission, time)` 列表。
- adapter 遍历每个玩家的原始 schedule，保留顺序，验证全部输入任务恰好出现
  一次、normalized allocation 恰为 1、duration 等于完整 workload/productivity，
  并验证任务侧与玩家侧分配一致。没有额外排序、round、argmax、补派或任务拆分。
- 第二批 6 任务 factory 的 r0 顺序为 `t0,t2,t4`，r1 为 `t1,t3,t5`。
  修改输入后，factory 和 CLI 都得到 r0=`t1,t3,t5`，r1=`t2,t0,t4`；t0 原生
  duration=53。测试逐项比对 Assignment 顺序与原生日程，不按期望场景硬编码分派。
- 原生一个 TaskSimple 可以有多个 mission，但当前 EAI 一个 Assignment 只有
  task_id/robot_id，没有 mission/workload 切片。忠实扩展需要先声明并实现这些
  执行语义；不能擅自把一个 EAI 任务拆成独立任务、把 coalition 选成单机器人，
  或忽略剩余 mission。因此本次没有声称支持 multi-mission、cooperative fractional
  workloads、DAG 或动态反馈重规划。
- 原生静态驱动按任务 phase-II 标志终止，未保证所有玩家日程最终同步；原生
  单任务边界也可能阻塞。本次原样保留，拒绝不一致/不完整日程，超时杀死并回收
  worker，不追加消息排空、重算或策略修补。
- `TaskRunner.run` 保留 assignment 列表执行顺序；跨机器人时间并发、原生 arrival
  times 的物理实现未验证。不能把原生预测时长当作模拟器实测时间。

## 测试与复现

新增 4 个统一入口测试：factory 多任务、真实 CLI 多任务、CLI NCLO 失败传播、
CLI 协作域拒绝。第一次聚焦运行 4 passed；新增异常别名后完整测试 **20 passed、
3 xfailed**（23 collected，无普通失败），见 `with_failure_evidence/pytest_all.xml`。
其中统一 CLI 多任务当次遇到原生日程不一致并 XFAIL；XFAIL 不计 native pass。
预算失败/输入拒绝测试通过仅表示失败传播契约正确。

直接复现已保存的 6 任务修改案例：

```bash
cd /home/airs/hetrocoord
PYTHONDONTWRITEBYTECODE=1 \
/home/airs/.cache/heterocoord/envs/fmc-ata/bin/python -B -m tools.algorithm_adapters.run \
  --algorithm fmc_ata \
  --source-root /tmp/heterocoord-algorithms-20261028/fmc-ata \
  --task records/algorithm_adapters/fmc_ata/unified_entry/with_failure_evidence/changed_6_tasks/task.json \
  --config records/algorithm_adapters/fmc_ata/unified_entry/with_failure_evidence/changed_6_tasks/config.json \
  --report /tmp/fmc-ata-unified-reproduced.json
```

运行测试：

```bash
cd /home/airs/hetrocoord
FMC_ATA_SOURCE_ROOT=/tmp/heterocoord-algorithms-20261028/fmc-ata \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
/home/airs/.cache/heterocoord/envs/fmc-ata/bin/python -m pytest \
  algorithm/system/coordination/fmc_ata/tests/test_native_adapter.py \
  --import-mode=importlib -q -rxX
```

固定批次导出入口为同一测试文件的 `--write-unified-evidence DIRECTORY`。
完整输入、配置、源/adapter/入口哈希均在已保存的 JSON 中。异步运行结果可变化，
复现命令不保证成功。所有返回决策的 TaskRunner verdict 仍为
`N/A / physical_evidence_required`；没有执行 Isaac 或计作物理成功。
