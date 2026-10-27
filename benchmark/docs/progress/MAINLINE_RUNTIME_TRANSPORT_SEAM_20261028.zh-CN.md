# 主线 runtime transport 接缝阶段收尾（2026-10-28）

## 主线唯一基线

主工作树 `/home/pupil/HeteroCoord` 以 `HeteroCoord` / `origin/HeteroCoord` 为唯一集成基线。GPU 工作树 `codex/room06-real-gpu-vertical-20261027` 当前落后主线且没有领先提交，因此不具备可推送的工程提交。

## GPU 脏改动判定

该工作树的未提交内容是一次诊断运行的现场状态，不是主线缺失的待合并功能：

- 修改的 `simulator.py`、`room06_runtime_hook.py`、`room06_livt04_gpu_probe.py` 的有效 registration/probe 版本已在主线；
- `artifacts/project_runtime/`、`usd/scene/room_06/`、日志、checkpoint、`run_failure.json` 是运行输出或重复场景副本；
- `ROOM06_CPU_RUNTIME_BRIDGE` 的追加文字是 diagnostic-only 证据，不能升级为 task receipt、机器人资格或论文样本；
- 该分支比主线落后 41 个提交、没有 ahead commit，故整树合并会倒灌旧基线和不可复现现场文件，不能直接推送；
- ToDesk 没有被关闭，也不是拒绝原因；GPU 准入审计应关注真实实验资源，显示服务不属于工程提交。

## 本阶段完成

新增 `task.executors.TransportOrchestrationExecutor`，把现有 `TransportAdmissionResolver` 接入 `TaskRunner.run_orchestrated()`：

1. 从 `Assignment + task.metadata.transport` 编译稳定的 `TransportCommand`；
2. 以场景物体质量和机器人 payload 为唯一准入来源，命令参数不能伪造质量；
3. 超载、缺物体记录、缺 payload、畸形合同、导航失败和缺执行回调均 fail-closed，返回 `ExecutionUpdate`；
4. 准入成功后才调用导航/执行回调，反馈保留 transport command/admission provenance；
5. `transport_command_bound`、`transport_admission`、`transport_navigation`、`transport_started`、`transport_returned/failed` 写入 append-only ledger；
6. 支持 `from_json_documents()`，使正式场景 object/robot resolver 可直接注入，不依赖 Isaac/Kit；
7. 该层只提供调度反馈，不生成 evaluator-owned receipt material，也不打开任何正式资格门。

## 验证

受影响集（`env_isaaclab`）：

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 env_isaaclab/bin/python -m pytest -q \
  task/tests/test_transport.py task/tests/test_transport_runtime.py task/tests/test_runner.py
29 passed in 0.10s
```

覆盖超载短路、等载执行、缺记录、无回调、独立任务继续、显式稳定 request id/伪造质量忽略，以及 runner 的失败传播。

## 未完成与下一接缝

- evaluator-owned terminal receipt 仍需从真实 observer/skill-session source 生成并接入现有 receipt material 链；
- room_06 的真实跨层 topology edge 与物体/机器人语义地图到位后，才能冻结并运行第一个真实 case；
- diagnostic GPU 结果继续保持 `diagnostic-only`，`physical_qualified`、`rankable`、`paper_score_eligible` 及认证 flags 全部为 `false`；
- 推送边界再执行全量回归，日常小步不重复全量套件。
