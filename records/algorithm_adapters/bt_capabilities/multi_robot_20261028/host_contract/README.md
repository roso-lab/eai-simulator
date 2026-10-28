# BT 双 UR5 宿主交接（2026-10-28）

状态：host-ready / physical evidence pending。专属 CPU 测试 23 passed。
本次未启动 Isaac/GPU，未占用 18767 endpoint，未发送伪造执行反馈。

## 文件

- 宿主：`/home/airs/hetrocoord/tools/algorithm_adapters/isaac_bt_multi_robot.py`
- 专属测试：`/home/airs/hetrocoord/tools/algorithm_adapters/tests/test_isaac_bt_multi_robot.py`
- CPU JUnit：`/home/airs/hetrocoord/records/algorithm_adapters/bt_capabilities/multi_robot_20261028/host_contract/tests.xml`
- 离线任务/native payload/preparation receipt：本目录 `prepared/`
- 协作式依赖：`/home/airs/.cache/heterocoord/envs/bt-multi-robot-test-deps`（greenlet 3.2.4，CPython 3.11）。后续主进程也已在 env_isaaclab 安装相同版本。

## 调度命令

在 Isaac Lab Python 环境运行。endpoint 应与主 agent 的 18767 健康检查地址一致；若服务在容器 IP，请替换下方 loopback 地址。原单 UR5 endpoint 不参与本宿主。

```bash
cd /home/airs/hetrocoord
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/home/airs/hetrocoord//home/airs/.cache/heterocoord/envs/bt-multi-robot-test-deps \
/home/airs/miniconda3/envs/env_isaaclab/bin/python \
tools/algorithm_adapters/isaac_bt_multi_robot.py \
--endpoint http://127.0.0.1:18767 \
--output records/algorithm_adapters/bt_capabilities/multi_robot_20261028/host_contract/physical_run_001 \
--execute
```

输出目录必须为新建或空目录。`--source-root` 可显式提供原 BT checkout，否则使用 shared verified adapter 的默认 cache。`--timeout-s` 默认 100 秒/操作（仿真时间）。物理器使用 CPU PhysX；Kit 启动仍必须服从主 agent GPU 调度。

默认不加 `--execute` 只生成离线准备文件。若需要单独原生预检，用 `--native-preflight` 替代 `--execute` 并换一个空输出目录；它只 plan/validate/close，不发 feedback，不启动 Isaac。

## 固定验收范围

`ur5_1` / `ur5_2` 位于同一 SimulationContext、同一 `/World`、num_envs=1，基座坐标分别为 `(0,-1.25,.45)`、`(0,1.25,.45)`。原 native provider 分配 `move_left` 与 `move_right`，依赖汇合到 `inspect_join`。两臂都声明 manipulate/inspect；每任务对两臂都提供声明几何距离成本，宿主不指定竞拍赢家。若原生分配不在固定臂工作空间内，拒绝，不替换。

物理启动前校验原生 graph/tree、完整 DAG prerequisites、task+bid contract hash、raw/adapted assignment 一致性，以及两个初始任务对应不同 robot。原生源由 `build_native_adapter` 完整性封装验证。每个物理批次完成后用 client 原生 feedback_batch 回传真实 ExecutionUpdate；核对叶节点回显，再由 TaskRunner.run_orchestrated + adapter.replan 驱动后续任务。

双臂分别使用未修改的 PickPlaceExecutor + ArmPickPlaceAdapter，同线程 greenlet 在原 advance 回调处让出控制，主宿主每轮只推进一次 PhysX tick。没有线程池并行 Kit 调用。

join 为 native-assigned robot 执行的宿主 ground-truth 查询：持续 200 ticks/1 秒检查两物体释放/恢复、目标误差 <=35mm、漂移 <=5mm、速度 <=20mm/s。它不是 onboard vision，也不含传感器移动。

## 实际运行后产生的证据

- `shared_ticks.jsonl`：每个物理 tick 的仿真时间、wall monotonic 时间、活动任务、两臂实测关节/末端、源物体 pose/velocity、follow proxy pose 与 held object。
- `move_left.json`、`move_right.json`：真实原 executor pick/place 返回值、最小保持抬升、放置误差、attach/release 次数、源属性恢复、资源与代理释放。
- `inspect_join.json`：依赖之后的连续物理观测。
- `native_events.json`：原生 plan、replan、feedback_batch 全量回执，拒绝输出也保留。
- `scene_audit.json`、`clearance.json`：两臂/两工作台逐 tick clearance；保留场景和碰撞边界。
- `receipt.json`：TaskRunner ledger、最终原生树状态、物理并发重叠 tick/时间、输入 hash 与证据 hash。

并发通过要求两任务同时活动、两臂实测关节同 tick 都变化超过 1e-7rad 的 tick 至少 20 个；仅逻辑并行/驻留/完成数不能通过。整体通过还要求三任务实测通过、TaskRunner 三项 completed、原 native BT SUCCEEDED、源输入未变。

语义严格为 **idealized_follow_proxy_pick_place; not contact grasp**。没有机械摩擦接触抓取证据；机械臂关闭自碰撞与重力补偿，保留环境碰撞。逐 tick 检查所有 arm/table 组合；未认证跨臂 mesh collision（基座相隔 2.5m）、全场景接触安全或 benchmark score。CPU 测试只证明宿主协议/调度契约，不是物理结果。第一次 Isaac 实跑兼容性与物理成功待主 agent 验证。

## CPU 复验

```bash
cd /home/airs/hetrocoord
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=/home/airs/.cache/heterocoord/envs/bt-multi-robot-test-deps \
/home/airs/miniconda3/envs/env_isaaclab/bin/python -m pytest -q -p no:cacheprovider \
tools/algorithm_adapters/tests/test_isaac_bt_multi_robot.py \
--junitxml=records/algorithm_adapters/bt_capabilities/multi_robot_20261028/host_contract/tests.xml
```

历史交接说明：后续实跑见上级目录 physical_001/ 和 REPORT.zh-CN.md；这里的 pending 状态仅表示宿主开发交接时状态。文件迁移未改动历史 JUnit 和准备收据。
