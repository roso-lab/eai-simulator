# room06 任务对象与初始候选池接线（2026-10-28）

本阶段把已验收的 room06 语义点转换为 `liv-t-04` 的 case 输入。地图和静态 USD 不被修改；任务物体在运行时 session layer 生成，机器人出生位写入 scene deployment state。

## 已冻结的任务布置

| 逻辑对象 | 初始绑定 | 目标绑定 | 运行时几何 | 观测身份 |
|---|---|---|---|---|
| `book_batch_01`（6 本） | `floor1-bookshelf`，中心 z=0.8351 m | `floor2-bookshelf-1/2/3` | 0.45×0.30×0.24 m 的批次代理刚体 | `/World/envs/env_0/room06_task/book_batch_01` |
| `heavy_object` | `floor1-coffee_table`，中心 z=0.6223 m | `floor2-coffee_table` | 0.80×0.50×0.40 m，24 kg | `/World/envs/env_0/room06_task/heavy_object` |

`book_batch_01` 的六个逻辑书 ID 保留在任务定义中；第一阶段物理运行使用一个批次代理刚体，完成事件仍必须逐本记录并由 observer 汇总到批次身份。`heavy_object` 的 `minimum_holders=2` 与 V3 联合持有语义一致。物体身份是任务级逻辑 ID，USD prim 路径只作为运行时 readback 绑定。

## 初始候选池

本 case 冻结 3 台候选实例，算法仍负责运行时分配角色：

| 实例 | 初始位置 | 地图绑定 | 预期能力角色 |
|---|---|---|---|
| `hc-rt-006` Go2+D1 | `[3.47318, 3.77619, 0.42]` | ground `node:0001`（一楼书架） | 一楼取书、具备跨层移动的 courier |
| `hc-rt-020` Iris | `[2.42079, 1.32777, 1.50]` | aerial `node:0005`（一楼吧台右侧） | 图书接力与联合持有候选 |
| `hc-rt-021` Pegasus X4 | `[5.07086, 2.39855, 1.50]` | aerial `node:0007`（一楼阅读桌） | 图书接力与联合持有候选 |

三台实例的历史 runtime qualification 已在 27 台名册中登记；场景部署仍保持 `runtime_qualified/physical_qualified/rankable/paper_score_eligible=false`。出生位是 case state，不改变名册默认初始状态摘要，也不替被测系统预先选择最终执行联盟。

## 产物

- 任务定义：[task-setup.json](/home/pupil/HeteroCoord/artifacts/project_runtime/gpu-room06-vertical-20261027/inputs/task-setup.json)
- 机器人部署：[deployment-state.json](/home/pupil/HeteroCoord/artifacts/project_runtime/gpu-room06-vertical-20261027/inputs/deployment-state.json)
- 任务物体布置：[task-object-placement.json](/home/pupil/HeteroCoord/artifacts/project_runtime/gpu-room06-vertical-20261027/inputs/task-object-placement.json)
- CPU 规划/Isaac 配置：[room06_task_objects.py](/home/pupil/HeteroCoord/source/EAI_assets/EAI_assets/scene/room06_task_objects.py)

## 当前边界

CPU preflight 已通过：任务对象定义摘要、地图 save_id、四个书架目标和 3 台候选实例均一致。GPU probe 现在会在 Isaac 创建环境前调用 `build_room06_task_object_configs()`，把两个任务刚体放进 session layer；仍需要一个真正注册到 Isaac session 的 runtime source 来执行导航/取放/联合持有，并从 `held_object` 读回事件。失败保留原始收据，正式资格门继续关闭。

## 当前运行结果

- 受影响 CPU 集：`76 passed`，包含任务对象计划、room06 preflight、任务运行时和 fixed-package 回归。
- 真实 GPU 场景加载：`run-08-scene-load-after-env-fix/report.json`，CUDA UUID 与主线 USD 摘要一致，64 步有限进展成功。
- `run-07` 在模拟进程启动前暴露主线缺少 `EAI.hmrs_env.slot_state`；已恢复依赖并按基础设施重试规则使用新目录继续。该失败不计入任务结果。
- GPU 报告仍明确标记为 scene-load diagnostic：没有任务物体、技能事件、联合持有快照或 terminal receipt，正式门全部关闭。
