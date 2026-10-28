# room06 任务对象与初始候选池接线（2026-10-28）

本阶段把已验收的 room06 语义点转换为 `liv-t-04` 的 case 输入。地图和静态 USD 不被修改；任务物体在运行时 session layer 生成，机器人出生位写入 scene deployment state。

## 任务布置草案（尚未验证可执行）

2026-10-28 复核纠正：下面的 24 kg、物体尺寸、咖啡桌源/目标和三台候选池是工程布置草案，不是出题人已裁决的物理参数。fixed package 的哈希一致只证明输入被固定，不能证明负载可行或任务已执行。现有候选池与 24 kg 重物不匹配，不能将此草案直接用于正式实验。

| 逻辑对象 | 初始绑定 | 目标绑定 | 运行时几何 | 观测身份 |
|---|---|---|---|---|
| `book_batch_01`（6 本） | `floor1-bookshelf`，中心 z=0.8351 m | `floor2-bookshelf-1/2/3` | 0.45×0.30×0.24 m 的批次代理刚体 | `/World/envs/env_0/room06_task/book_batch_01` |
| `heavy_object` | `floor1-coffee_table`，中心 z=0.6223 m | `floor2-coffee_table` | 0.80×0.50×0.40 m，24 kg | `/World/envs/env_0/room06_task/heavy_object` |

`book_batch_01` 的六个逻辑书 ID 保留在任务定义中；当前生成器只有一个批次代理刚体，**尚不能证明六本书分别送到三个书架**。六个独立物体、逐本目的地与 observer 汇总仍需实现，不能从代理移动补造六次完成事件。`heavy_object` 的 `minimum_holders=2` 与 V3 联合持有语义一致。物体身份是任务级逻辑 ID，USD prim 路径只作为运行时 readback 绑定。

## 初始候选池

当前输入包暂选 3 台候选实例，未通过本任务的负载设计核对；算法仍负责运行时分配角色：

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

## 本轮续接核验与纠正

- 用户最终纠正测试目标为 **room06**，不是 room2。
- `room06-fixed-package.json` 的 `verify_room06_fixed_package` 成功，摘要 `668fa53459c88893d732e146979281ce8e92839befedee9cc2eeadfe55caaa33`；task setup 摘要已一致。独立只读审查引用旧文档所称的定义摘要缺口已过时，本次实测纠正。
- 受影响集 `209 passed in 0.61s`，退出 0；新文件 `py_compile`、`git diff --check` 均退出 0。
- benchmark 全量 `1075 passed in 33.90s`，退出 0。命令为 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 EAI_USD_ROOT=/home/pupil/HeteroCoord/usd PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q benchmark --tb=short`。
- 过程偏差：提交 `9500ed8b` 已先推送，随后才执行全量；不符合用户“推送前一次全量”的顺序。本次没有掩盖顺序，下次恢复先验收再推送。后续文档纠正不重复运行全量。
- ZCode plugins/MCP 均返回 `Open one ZCode desktop version, or set ZCODE_CLI to a local CLI path.`；测试由主线程执行，不声称 ZCode 复验。agentmemory 工具本轮未提供。

### 实际准入结果（不是 D1 不可行证明）

直接将当前固定包、promoted roster 与 `robot_pool_resume.v1.json` 交给 `bind_room06_transport_runtime`，再对每台每个逻辑物体调用现有 `TransportAdmissionResolver.check`，得到：

| 实例 | 登记 manipulation payload | 书批 | 24 kg 重物单机 |
| --- | --- | --- | --- |
| hc-rt-006 Go2+D1 | 0.5 kg | 缺 mass_kg，拒绝 | 超载，拒绝 |
| hc-rt-020 Iris | 0.425 kg | 缺 mass_kg，拒绝 | 超载，拒绝 |
| hc-rt-021 Pegasus X4 | 2 kg | 缺 mass_kg，拒绝 | 超载，拒绝 |

这不否定用户已接受的 27/27 平台历史资格；它说明此 case 布置与具体技能负载不匹配。三者数值相加仅为 2.925 kg，且现有执行器没有实现联合负载分配，不能把相加计算当联合搬运资格。也不能用这些拒绝替代完整 D1 solver 证明。

### 下一步实施顺序

1. 给用户提交实际布置建议：六个独立书体，每本 0.2 kg，三个目标各两本；保持现有三台池时可考虑 2.2 kg 联合搬运物（大于任意单台、低于 Iris+Pegasus 的数值总和）。这些是待确认的工程参数建议，**不写入已裁决的锚点语义**，不声称联合搬运已验证。另一选择是保留更重物体并重新设计能够承担它的机器人配置。
2. 先实现任务级单书身份/质量和显式联合持有执行服务，再对接真实导航、技能会话、空中/地面持有读回与 observer。普通 arm pick 的单持有租约不可伪装成双持有。
3. 当前 full-task 输入仍缺 runtime_hook_ref / runtime_source_ref，执行 `prepare_full_task` 正确在 simulator 启动前拒绝；本轮没有重复跑 scene-load，也没有启动新的 GPU 任务。
4. 物体部署与机器人出生位真实读回后，再运行完整 room06 case，产出终局收据。导航失败与技能失败保留为任务结果，不要求地图作者补楼梯或预计算路线。

### 生产 source 接口续接（2026-10-28）

新增 [room06_runtime_source.py](/home/pupil/HeteroCoord/source/EAI/EAI/room06_runtime_source.py)。它是严格的 production registration seam：

- 只绑定 `/World/envs/env_0/room06_task/book_batch_01` 与 `heavy_object` 两个 case-owned rigid body；缺 prim 即失败；
- 只接受环境显式注册的 `room06_task_driver.collect_room06`，不从命令、ledger 或对象 basename 推导事件；
- 所有 completed skill-session、`held_object` 快照和 terminal inputs 仍由 driver 交给原 observer collector 校验；没有 driver 时在任务采集前 fail-closed；
- 当前环境仍没有真正的 `room06_task_driver`。因此这一步完成了接口注册和身份边界，尚未宣称任务 source 可运行，也没有启动 GPU full-task。

其 CPU 合约测试与既有 hook 一起通过 66 项。下一项工程实现是把真实导航/逐本取放/联合持有后端注册为该 driver；普通单机 pick/place 和单个批次代理都不能替代它。
