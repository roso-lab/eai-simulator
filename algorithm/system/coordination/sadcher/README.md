# SADCHER 原生 I/O adapter

统一入口是 `benchmark.native_algorithms.build_native_adapter("sadcher")`。
使用冻结的 `jakbichler/Sadcher@a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae`，
由上游网络、matcher 和两个 filter 决定分配。adapter 不改变模型、奖励、匹配约束或 idle。

## 权重与运行环境

默认权重与上游 `benchmarking/benchmark_schedulers.py` 一致：
`imitation_learning/checkpoints/hyperparam_2_8t3r3s/best_checkpoint.pt`。
该文件完整匹配 116 个 state entries，SHA256 为
`8c391003af62d9b99399563e85790e4af6b65162120c79917077dc02882bb10e`。

旧默认 `8t3r3s_models/model_0/best_checkpoint.pt` 不匹配当前网络结构。
2026-10-28 修正默认选择；显式参数优先于环境变量，环境变量优先于默认。
显式指定旧权重仍拒绝，不自动换权重、不接受部分加载。
`checkpoint_selection` 与完整加载审计随决策记录。

本机隔离环境：`/home/airs/.cache/heterocoord/envs/sadcher/bin/python`，
Python 3.10、torch 2.9.1+cu128、numpy 1.23.5、PuLP 3.3.2。
系统 Python 缺少 PuLP；不要混用 Isaac Python 的 torch 环境。

```bash
cd /home/airs/hetrocoord
PYTHONPATH=.:source/EAI CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 \
  /home/airs/.cache/heterocoord/envs/sadcher/bin/python \
  -m tools.algorithm_adapters.run --algorithm sadcher \
  --task records/algorithm_adapters/sadcher/review_20261028/final_evidence/unified_entry/normal.task.json \
  --report /tmp/sadcher-default-decision.json
```

这是决策调用；退出 0 不代表任务已执行完成。

## 可表示的输入与反馈

- 显式提供三个二值技能的固定顺序、机器人位置、任务位置和正数 `duration_s`、DAG、起终点。所有坐标共面，原生速度为 1 坐标单位/秒。保持上游归一化，不调整坐标或技能顺序来改变派发结果。
- `IN_PROGRESS` 必须有正的剩余时长、已完成的前置条件、active owner，并满足原生 `all_robots_at_task(..., threshold=0.01)` 的到达和技能覆盖条件。前往任务途中使用 `PENDING` 加 `current_task_id`。
- `DONE` 的显式剩余时长必须为零。重规划依据 runner 的完成集合和 executor feedback，拒绝重复、未知身份、失败与完成矛盾及最近一批 owner 不匹配。feedback 是累计记录；更早批次的派发身份由 TaskRunner 校验，adapter 不虚构历史。
- live `native_tasks` 需覆盖完整任务集合；其 DONE 状态必须与重规划集合双向一致。完成任务剩余时长归零。
- inactive 表示暂不可调度，不能拥有未完成任务；外部 unavailable 且无 owner 不推断额外 workload。

## 输出与能力边界

原生 idle、start/end 和完整 coalition 均保留。当前 TaskRunner 每个任务只支持一个 owner，
原生多机器人共同执行一个任务时抛出 `NativeCoalitionScopeError`，完整决策保存在
`exception.decision`，不会截成单机器人分配。多机器人各自执行任务、DAG 依赖协作可走当前闭环。

上游在只有 IN_PROGRESS 实际任务时可能给空闲机器人输出 exit；adapter 保留决策并抛出
`NativePrematureExitError`，宿主不能将此视为任务完成。没有修改原生提前退出逻辑。
这类拒绝和原生 idle 应作为真实实验结果记录。

## 测试与实跑

```bash
cd /home/airs/hetrocoord
PYTHONPATH=.:source/EAI PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 CUDA_VISIBLE_DEVICES='' \
  /home/airs/.cache/heterocoord/envs/sadcher/bin/python -m pytest -q \
  algorithm/system/coordination/sadcher/tests \
  benchmark/tests/test_native_algorithms.py \
  benchmark/tests/test_native_source_integrity.py
```

2026-10-28：SADCHER 专项 60 项 + 共享完整性/入口 8 项，共 68 passed。
持久化上游二维仿真经 TaskRunner 完成 3 机器人/3 任务、2 批执行，tick 31 完成实际任务，
原生退出阶段 tick 59 全部到达出口。两轮派发及退出决策的 features/rewards/assignment
与直接原生调用一致。单 UR5 输入仍 idle；双 UR5 预检只派发左侧任务，右侧 idle。

证据和完整报告：`records/algorithm_adapters/sadcher/review_20261028/REPORT.zh-CN.md`。
这是有界原生二维理想化逻辑闭环；本轮没有启动 Isaac，也不构成论文全部任务或 coalition 执行验收。
