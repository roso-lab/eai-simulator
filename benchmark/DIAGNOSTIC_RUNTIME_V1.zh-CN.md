# 异构多机协作诊断逻辑仿真器 v1

`benchmark/hetero_diagnostic_runtime_v1.py` 是不启动 Isaac Sim 的可复用逻辑运行时。它把场景拓扑、机器人池、任务前置关系、资源占用、通信条件和被测算法分开，所有状态在论文规定的 `0.02 s` 离散时钟上推进。

运行时维护十类可恢复状态：`world`、`clock`、`controller`、`skill`、`task`、`algorithm`、`transport`、`locks`、`events` 和 `rng`。`checkpoint()` 可返回内存 receipt，也可以通过 `checkpoint_store_v3` 写入不可覆盖的 artifact；`restore_checkpoint()` 会校验外部 digest、metadata 和 participant readback。`fork()`/`branch()` 从同一 snapshot 创建独立分支，`replay()` 从同一 checkpoint 运行两个副本并比较完整事件 digest。

通信条件使用共享信道：C0 为零延迟无丢包，C1 为固定延迟，C2 为带宽和丢包压力，C3 为更严格的队列、带宽和丢包压力。资源账本记录容量、FIFO 等待、服务区间和争用次数；`ResourceSpec(recharges=True)` 或 `recover_*` 资源名会在服务释放后恢复地面机器人的电量。电量是可选的机器人状态，未设置 `battery_capacity_m` 的机器人不受能量约束。

city_01 适配器位于 `benchmark/scenarios/city_01.py`：

```bash
PYTHONPATH=. python -m benchmark.run_diagnostic \
  --scenario city_01 --tier 4 --communication C2 \
  --output artifacts/city01-t4-c2.json
```

这条命令使用通用运行时，覆盖区域的 ground/aerial 语义锚点会绑定为同一 `area_i` obligation；tier 2 以后加入三项空中事故观测，tier 3/4 加入观测前置的地面事故集合。若需要与原有 city_01 专用逻辑轨道逐项对照，可使用 `--engine compatibility`。该轨道仍明确标记 `isaac_sim_started=false`、`physical_evidence=false` 和 `paper_score_eligible=false`。

其他场景通过 JSON `EpisodeSpec` 或 Python `ScenarioAdapter` 接入，算法实现 `AlgorithmAdapter` 的 `initialize/decide/snapshot/restore` 四个方法即可替换 EMOS、Greedy baseline 或其他被测算法。通用运行时的 D1–D6 字段是资格和证据状态，不会把单次 CPU 逻辑运行冒充论文正式分数；正式 D2/D4/D5 仍需要成对条件、可回放 checkpoint、availability 分支和认证跨场景目录。

该轨道验证任务图、资源、通信、分配、回放和分支逻辑，不提供碰撞、传感器、控制器或真实 Isaac 物理资格。
