# FMC → UR5 最小接入预检：原生求解超时，未启动 Isaac

2026-10-28。本次只新增 FMC 专属 records；既有 FMC adapter/测试、上游、
`isaac_pick_place.py`、`run.py`、`native_algorithms.py` 均未由本任务编辑。
遵照用户后续指示，没有启动 Isaac、GPU 仿真或调用物理 executor。

## 实际结果

- `task.json` 是一个 `move_carton` 任务、一个 `ur5_1` 机器人；没有虚构额外
  机器人、辅助任务、coalition 或占位任务来推动 solver。
- 现有 `physical_options()` 校验通过，FMC `_encode()` 校验通过。它们只是
  输入/host 契约证据，不是原生决策或仿真成功。
- 用未经修改的 `NativeFMCATAAdapter` 对同一输入各执行一次，wall timeout
  分别为 10 秒和 30 秒。**两次都发生原生超时，未返回 AlgorithmDecision**。
  原始报告是 `native_attempt.json` 和 `native_attempt_30s.json`。
- 两个 worker PID 分别为 `3234257`、`3250370`；适配器报告 killed and reaped，
  后续 PID 存活检查均为 false。没有把第一轮失败覆盖成第二轮结果。
- 调度/技能派发次数为 0，物理反馈数量为 0；没有 physical_execution receipt，
  没有物理成功/失败评分。状态为 `blocked_before_physical_dispatch`。
- 上游 commit 为 `7872a94776cfe21cf58cadf44007382deb650eea`；调用前后独立
  `verify_source()` 回执一致，源目录全部文件哈希一致，FMC adapter/测试哈希一致。

本次直接运行原 adapter，并在前后独立调用共享源校验。没有宣称运行了共享
factory/CLI/Isaac host；此前统一入口记录仍在 `../unified_entry/`。

## 输入和执行语义

UR5 host 位置约束及抓放参数来自 `task.json`：

| 字段 | 值 |
|---|---|
| Robot world XY 投影 | `[0, 0, 0]` |
| FMC task position / host task XY 投影 | `[0.45, 0, 0]` |
| 物体位置 | `[0.45, 0, 0.475]` |
| 放置位置 | `[0.425, 0.01, 0.475]` |
| Approach | 15° |
| 每技能 timeout | 100 s |
| Mode | `cartesian_pose` |
| FMC workload / productivity | 30 / 1 |
| FMC importance / speed | 1 / 1 |
| FMC max_players | 1 |

workload/productivity 是显式 nominal planning duration，不是测得的机械臂操作时间；
speed 是原生 PlayerSimple 的移动参数，固定基座 UR5 host 没有对应的移动技能。
因此即使后续返回单任务分派，也只能验证“分派到现有 UR5 抓放技能”的最小接入，
不能声称 FMC 旅行时间模型或日程时刻在物理 host 中得到验证。

现有 host 对非 BT 算法先调用统一 CLI；仅在原生成功返回恰好一个合法 assignment
后，TaskRunner 才进入 Executor，导入 `AppLauncher` 并调用现有
`unified_pick_place_smoke.run` / `PickPlaceExecutor`。无原生分派就不会补派技能。
如果未来执行，其语义仍为 `idealized_follow_proxy_pick_place; not contact grasp`，
不能称为接触抓取或通用 benchmark 验收。

## 阻断与缺口

1. 当前单任务/单机器人输入两次均未在预算内得到 native decision，无法进入
   TaskRunner 的物理派发。30 秒结果只证明这一有界运行超时，并不证明永远不终止。
2. 上游 `1_simulation_user_static.py:70` 明确标记 `TODO take care of only 1 task
   in system`；`solver_abstract.py` 的 mailer 存在等待消息循环。但本次没有采集
   超时现场线程栈，不能把注释或静态代码当成精确根因证明。未改终止条件或排空消息。
3. 当前 FMC adapter 是静态求解接口，没有原生 execution-feedback/replan 映射；
   host 可以记录 executor feedback，但不能凭空反馈到 FMC 内部状态机。严格意义的
   “算法反馈闭环”仍缺失，不能因为将来物理技能完成就宣称该闭环完成。
4. 本轮未进行 Isaac runtime / asset / GPU 启动验证。这部分由用户统一顺序执行。

## 准备好的命令（未执行）

Isaac host 命令已保存为 `run_host.sh`。只在用户统一调度时执行：

```bash
bash /home/airs/hetrocoord/records/algorithm_adapters/fmc_ata/ur5_preflight_20261028/run_host.sh \
  /home/airs/hetrocoord/records/algorithm_adapters/fmc_ata/ur5_scheduled_run_01
```

必须给全新的绝对路径。脚本把日志写到输出根目录，把共享 host 的证据放入新的
`host/` 子目录，符合其“新目录或空目录”要求。共享 host 将重新执行原生求解一次，
不会复用本轮超时结果，也不会人为构造 assignment。此命令不保证原生成功。

如需先单独用统一 CLI 复核 CPU native：

```bash
bash /home/airs/hetrocoord/records/algorithm_adapters/fmc_ata/ur5_preflight_20261028/run_native_only.sh \
  /home/airs/hetrocoord/records/algorithm_adapters/fmc_ata/ur5_cli_scheduled_01
```

`adapter_config.json` 是当前准备执行的 30 秒配置；`adapter_config_10s.json`
保留首轮配置。两份 shell 均仅通过 `bash -n`，未实际启动。运行时源仍需可用，
共享 host 接口若继续变更，应由统一调度方复核。

## 工作树隔离

`baseline.json` / `summary.json` 保存前后哈希。观测到共享 `isaac_pick_place.py`
在本轮基线之后变化；本任务没有对它执行写入，且未回退该变化。不能声称全部
共享工作树在本轮保持不变；可以确认本任务仅新增本目录 records，并保存了观测
差异。现有 FMC adapter、FMC 测试与上游文件保持一致。
