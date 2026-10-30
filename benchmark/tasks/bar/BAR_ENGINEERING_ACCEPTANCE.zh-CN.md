# Bar 工程适配与论文验收边界

本次以 GitLab `4959e104` 和论文 `/home/pupil/CVPR2026_Template` 的 `overleaf-old/main@0ffd470f` 为输入。保留原任务的 T1 单订单、T2 双订单顺序交付、T3 三订单连续服务、T4 二层双托盘及楼梯入口同步交接；没有把 T4 改成两台机器人全程共同搬上楼。

## 任务阶段和技能的边界

公开 `eai.manipulation.pick_place` 2.0.0 只接受单机器人 `pick/place`、`object_ref` 和可选 `timeout_s`。任务级 `prepare` 是工作站服务；`release` 是生产者在内侧准备交接并保留所有权；`accept` 通过共享 `task.runtime.logical_objects.LogicalObjectRuntime.receive` 原子更换持有者。此接口属于宿主对象协议，不向公开 PickPlace 塞入 `receive`、坐标、语义点或多机参数。

Bar runtime 通过五字段逻辑对象定义共享同一账本，使用 graph-controller 的到点和会话证据。内外吧台是同一交接窗口的两个固定操作位，两个机器人分别在各自的操作位验证；不增加虚构导航边。原料获取、制备、交接和目标桌放置保持订单 ID 连续，单机最多持有一个对象。工作台/冰箱为容量一资源，交接租约覆盖准备接收至接收确认，楼梯租约覆盖入口至运输报告；充电容量一、按实际服务次数计数。

## 论文关系证据

- P2：每订单的 acquire/prepare/release-ready/accept/place、所有权、接收端和先后顺序。未到达 obligation 记零，已完成的交接不因后续失败被抹除。
- P3-J：T4 的每个楼梯入口动作有两个不同的能力合格参与者、订单 ID、到达 tick、同步窗口、连续 25 tick 的逻辑支持样本和不可分割的联合完成事件；随后由原载荷持有者运输。逻辑支持不宣称真实接触力。
- P3-S：T3/T4 的外部服务从 service_cover 到服务关闭逐 tick 采样，以无缺口 RLE 区间存储，包含端点。仅允许事先登记的补能/故障重分配间隙，并检查最长间隙和覆盖率；任务自报暂停不会结束外部需求。
- P4：工作台、冰箱、交接窗口、楼梯和充电点的请求/占用/等待/释放均留痕。未进入资源不等于完成互斥任务。补能回路保留原任务与持物状态，关闭 episode 前要求资源和队列释放。

## 参数与难度

难度任务和语义目标遵循原文。G1/lite3 不加入 T4 的实际三机组合，因此矩阵排除这些组合，并非把它们记为被测算法失败。候选能力仍保留 manifest 的逻辑声明；携臂楼梯能力不因此获得物理认证。`1F` 是地图连续支持层标识，`floor2-*` 的高度和两条 stairs 边是逻辑跨层路径依据；不把层名作为额外障碍。

原文字段没有给出连续耗电速率、初始电量、同步窗口或服务阈值。实现将其集中登记为**待独立校准的逻辑候选参数**：百分制容量 100、初始 60、连续消耗 0.5 百分点/s，安全阈值保留原 manifest 的 15；困难模式占位后一个控制 tick 充满，非常困难模式 4 s。等待和执行均计耗电，出发准入还核对当前路段和回充所需电量，拒绝预算不足的派发并记录 energy_admission_deferred，实际充电时不重复耗电，排队仍耗电。同步窗口 1500 tick，服务覆盖率下限 0.5、允许的单段补能/重分配间隙上限 90 s。候选值用于联调，不能冒充作者最终冻结的论文阈值，也未按某个被测算法的表现调 deadline。

协调状态包和到点报告都经过共享信道：状态包未交付时不调用被测系统的新决策，报告未交付时不关闭任务；丢包按有界重试处理。C0–C3 的容量严格递减，并保留已登记的延迟、丢包、重试机制；后二者属于外生干扰诊断。正式 D2 仍需独立校准容量和固定队列/协议。T3/T4 的 communication/resource/fault 条件继承本 tier 的电量模式，避免无意去掉难度要求。

## Checkpoint、分支与结果

运行生成真实状态快照：cp_00_spawn_valid、cp_01_material_ready、cp_02_pre_handoff、cp_03_handoff_complete、cp_04_delivery_complete、cp_05_closeout。包含对象、机器人、任务、资源、通信、能量、服务和联合动作状态。`fork/replay` 是本地确定性调试，包含算法状态。`fresh_branch` 校验外部固定 checkpoint digest，只恢复评价器状态，使用新算法对象并从公开观测重新 initialize；不恢复旧算法私有状态。此 API 尚不认证独立进程隔离。

`availability_loss` 在第一单放置确认后隐藏触发，持续移除一台空手机器人的 handoff_receive 功能，保留它的其他真实能力。故障只禁用一个执行功能，不同时删除制备、装载和接收三项能力。受影响工作在事件时冻结，发送状态消息，发布服务替补义务，让算法选择新的服务者和执行分工。保留失败行，不能把通知或重分配计数直接当 D4 得分。事件前另存 cp_fault_pre_event。resource_contention 条件登记一笔外部背景订单在前 20 s 占用工作台，正常订单必须经过同一 FIFO 资源账本等待；它不增加本 episode 的目标订单数量、不改变容量。

命令：

```bash
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m benchmark.run_bar_matrix --seeds 17,18,19 --workers 4
```

输出 JSON/CSV 汇总及 `.evidence.jsonl.gz` 完整事件、对象会话、P2/P3/P4 obligation、服务区间、联合动作样本和 checkpoint digest。可用 `BarScenarioAdapter.runtime(..., algorithm=实例)` 接入任意实现 initialize/decide/snapshot/restore 的被测算法。

现有 `EmosBarAlgorithm` / `TeamWeaverBarAlgorithm` 仅保留兼容名称，它们使用同一个确定性可行性策略，输出明确标注 `native_algorithm=false`、`algorithm_scope=deterministic_contract_witness`；不可作为真实 EMOS/TeamWeaver 比较结果。

D1 缺初始完整计划与正式不可行样本集；D2 缺容量校准/正式配对目录；D3 有逻辑 obligation 数据但缺认证支持集；D4 有持续故障和受影响工作数据但缺正式配对评价；D5 无认证跨场景适配单元，NA；D6 有序列化 bits 和逻辑时间但缺独立 B0/T0。所有 `D*_eligible` 和 `paper_score_eligible` 保持 false。机器人与路径的物理证据仅涉及本体控制，不要求恢复已取消的任务物体刚体仿真。
