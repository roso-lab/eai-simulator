# BT capabilities：DAG 与同场景双 UR5 验收

2026-10-28。结论：**通用 DAG 输入/原生竞拍/执行反馈协议通过；同场景双 UR5 三任务有界物理验收通过。原算法未修改。**

## 算法与适配边界

固定上游 commit `5438f6e8d7599e39ec12104c0a9f0a8f1543838d`，335 个源码/配置文件校验通过；运行前后 source SHA-256 均为
`b130e4665a99f40475df3950cad1a5b5c1ec29722e51169db792e8fdd4a3d621`。
原始 checkout 以只读方式挂载到独立 ROS1 容器。竞拍、投标排序、slot 预留和 BT 控制仍调用原 `ParallelAuctionManager`、`Parallel`、`MemorySequence`。

适配层只做任务/能力/显式成本/依赖输入转换，提供执行器可用 slot，观测依赖反馈，转发原生分配及实测终态。DAG 以原 Parallel 包含每任务的原 MemorySequence，依赖叶只读取已完成/失败反馈；没有替代调度器、默认竞拍成本或人工修正赢家。宿主提供两臂与物体几何距离作为声明成本，不替代完整 MissionControl utility 生成。

通用客户端校验任务、mission、payload hash、树类型、上游源码 hash、机器人身份/能力与反馈身份。多条反馈先全部验证，再一次提交并 tick；失败阻断依赖，机器人忙时等待，终态轮询不重启任务。

## 测试证据

| 集合 | 结果 | 证据 |
|---|---|---|
| 最终维护的 BT 专项 | 66 passed：59 契约 + 7 真实 ROS | `../dag_20261028/final_endpoint_smoke/run.json` |
| 双臂宿主契约与退出回归 | 24 passed | `host_contract/shutdown_regression_tests.xml` |
| 共享 native/source/TaskRunner 回归 | 155 passed、1 skipped | 本轮终端执行；跳过项未计通过 |
| 原生双臂预检 | 原生分配通过，无物理执行 | `preflight/receipt.json` |
| 双臂完整闭环 | 3/3 completed、BT SUCCEEDED、exit 0 | `physical_002/receipt.json` |
| 独立轨迹/来源复核 | passed | `physical_002/independent_audit.json` |

七个真实 ROS 测试包含单任务、串行链、执行失败、无可用投标、非拓扑输入的四节点分叉汇合、单机器人槽位串行复用、非法批次反馈原子拒绝及失败阻断。协议 fixture 使用 protocol_ack，未冒充物理证据。先前包含两项旧实现测试的 68 passed 记录保留，但本报告以维护的原生专项 66 为准。

最终协议和物理运行均使用 endpoint adapter SHA-256
`18b0e354428ab7687a7b2d8a21a9f1e202be1144d2c939ecf804bddb03b662f7`；早期不同 endpoint revision 的记录保留，不混算。

## 同场景实测

一个 SimulationContext、一个环境、两个固定基座 UR5；同一主线程推进 CPU PhysX，dt=0.005 秒。两个未改动的 PickPlaceExecutor 在已有 advance 回调处协作让出控制。原生竞拍派发 `move_left -> ur5_1`、`move_right -> ur5_2`，两条实测完成反馈回传后，才派发 `inspect_join -> ur5_1`。

| 指标 | ur5_1 / move_left | ur5_2 / move_right |
|---|---:|---:|
| 原执行器 pick / place | true / true | true / true |
| 保持阶段最小抬升 | 0.135054 m | 0.135054 m |
| 放置最终误差 | 0.0000113215 m | 0.0000113215 m |
| attach / release | 1 / 1 | 1 / 1 |
| 执行 tick 区间 | 400–4853 | 400–4853 |
| 物体属性、owner、代理与资源释放 | 通过 | 通过 |

全程 5,053 个物理步；两任务活动重叠 4,453 步（22.265 秒），两臂实测关节在同一步均变化超过 1e-7 rad 的重叠为 3,728 步（18.64 秒）。逐步轨迹保存在 `shared_ticks.jsonl`；独立复核重新计算这些数值，而非仅相信宿主汇总。

汇合检查在 tick 4853 后开始，持续 200 步（1 秒）。两物体最大目标误差均为 0.000011465 m，最大漂移 0.000000597 m，最大速度 0.000041437 m/s，释放后均恢复动态属性。该检查是仿真真值查询，不是机器人相机感知。

四个 arm/table 配对均逐步检查 5,053 次，重叠步数为 0，最小分离间隙 0.013436 m。独立复核检查 clearance 收据与步数一致性，没有另行重建 mesh 碰撞检测。

## 失败保留与宿主修复

`physical_001/` 的三个实测任务及原生树完成，但 Kit 默认 close 阻塞，汇总收据未落盘。该次保留原始轨迹、反馈和 `SHUTDOWN_INCOMPLETE.json`，不计完整验收。仅终止了本次独立宿主进程。

随后仅调整新宿主的退出顺序：关闭 trace、清理原执行器和 native mission，先写收据，再按已有单 UR5 宿主方式 `post_quit` 并关闭 Kit。新增回归覆盖 Kit 在 close 时终止 Python 的情形。`physical_002/` 是修复后的新执行，正常退出，输入源码哈希未变；不是事后改写第一次结果。

## 结构与复现

- 通用 I/O 与 ROS 服务：`algorithm/system/coordination/bt_capabilities/`。
- 物理宿主：`tools/algorithm_adapters/isaac_bt_multi_robot.py`；测试：`tools/algorithm_adapters/tests/test_isaac_bt_multi_robot.py`。
- 源码在工程外，依赖 greenlet 3.2.4 安装于 env_isaaclab；不在工程内携带依赖源码。
- 本目录保存物理运行和宿主测试；`../dag_20261028/` 保存协议运行。早期宿主交接资料已移入 `host_contract/`，旧收据路径保留历史原文。

启动独立 ROS endpoint 的命令见 `algorithm/system/coordination/bt_capabilities/NATIVE_ADAPTER_REPORT.md`。已运行时不用重复启动。物理复验：

```bash
cd /home/airs/hetrocoord
PYTHONPATH=.:source/EAI /home/airs/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.algorithm_adapters.isaac_bt_multi_robot \
  --endpoint http://172.17.0.2:18767 \
  --source-root /tmp/heterocoord-algorithms-20261028/bt-capabilities \
  --output records/algorithm_adapters/bt_capabilities/multi_robot_recheck \
  --execute
```

地址按容器实际 IP 调整；output 必须为空或不存在。

## 明确的验收范围

物理语义是 `idealized_follow_proxy_pick_place; not contact grasp`。关闭机械臂自碰撞与重力，保留环境碰撞；未认证跨臂 mesh 碰撞安全。原生竞拍服务参与分配，但完整 MissionControl capability repository、自动失败重竞拍、coalition 执行未实现/验收。

通用 DAG 输入已支持，不表示所有场景、任意技能已通过物理验证。本轮是双 UR5 抓放和汇合真值检查的有界验收，不生成论文 Benchmark 分数。未提交或推送，不改动其他算法及用户原有 dirty 文件。
