# 阶段17任务生成与审核报告

日期：2026-09-19

本阶段的职责是把场景业务目标、对象物理参数、机器人公开能力、生命周期/通信因子和运行证据连接成可执行题目契约。它不把抓取算法、VLA 视觉抓取或底层导航控制当作被测能力；这些由可靠技能后端提供。被测模型需要决定谁执行、是否需要一个或两个机器人、如何处理负载、通路、通信和成员变化，并留下可审计的决策与事件链。

## 交给被测模型的内容

每个任务实例向 participant 暴露四类数据：

1. `public_task.json`：场景、业务目标、公开对象身份与参数、区域和路线语义、初始机器人团队、机器人公开能力、子任务 DAG、完成规则、时间/通信接口和公开因子规则。
2. `participant_api.json`：可提交的分配、改派、导航目标、固定点操作、联合承载声明、消息和确认操作的 schema。接口只允许公开能力范围内的操作。
3. 逐时刻公开事件：当前观察、送达的消息、成员加入/退出、能力变化和动作回执。未送达消息、未来干预、隐藏真值和 evaluator 条件不进入 participant 输入。
4. 当前实验条件：例如通信延迟、丢包、带宽、成员退出或能力变化的公开规则。具体未来触发时刻和隐藏验收阈值仍由编排器/evaluator 持有。

隐藏层保存对象真值、实际质量和碰撞绑定、目标 AABB、真实服务状态、事件时序、任务因果条件、失败归因和评分证据。被测模型的输出是团队决策和接口调用序列，而不是一段替代底层技能的控制代码。

## 作者审核边界

作者侧必须验证初始对象和机器人落位、对象质量/尺寸/固定点、区域与通路绑定、公开能力和物理参数的一致性，以及在不预先规定参与者路线的前提下存在至少一个可执行方案。任务中临时机器人停留造成的拥堵属于策略结果；只有作者把机器人强制放在不合理初始位置、把强制点安排在互相冲突的位置，或任务不存在可行团队方案时，才是生成侧缺陷。House 的诊断撤离位置因此不会写进 public task。

机器人导航、固定点抓取和释放由可靠技能后端执行并提供物理读回。Benchmark 仍需检查 footprint、负载能力、单机/双机资格、联合成员状态、狭窄通道和 dock 外形容纳；它不评价 VLA 是否找到了抓取点，也不把“创建了一个接头”当作搬运完成。

## 本轮场景结果

### City

原生 CPU PhysX 探针读取 `go2_1` articulation tensor pose，真实位置进入诊断区域，完成 observation delivery、incident request、episode-owned `city_dispatch` receiver 和独立 receipt。报告为 `diagnostic_completed=true` 且 source hash 未变化。该证据只证明服务请求/接收协议接线；完整的人员引导、交通控制、危险区响应和多角色业务仍为 pending，不能计分。

### Modern House

连续住户通路检查器使用正反向 swept volume、家具/物品/机器人动态障碍、连续楼面覆盖和实时支撑采样。已修复对象生成高度漏算楼面的问题：House 楼面为 0.25m，书堆底部读回约 0.25m，质量约 2kg。当前机器人堵住通路时报告 blocked，只表示当前动态状态；它不要求参与者采用预设撤离动作。DirectGPU 下 CPU 动态位姿可能过期，相关查询保持 unknown；显式 CPU 作者诊断才可用于同步查询。

### Warehouse

已将 dock 接收门禁从“中心接近”提高为：完整旋转物体外形处于 dock AABB、身份/质量与冻结对象一致、接头实际移除、释放后产生新物理步、没有遗留启用约束，并且与 ready dock state 和当前观测帧绑定。service car 按任务可选，goal 任务只包含木箱和纸箱。

真实 CPU 探针创建了固定点接头，但约 1766 个输送步后纸箱位移仅约 0.000752m，未形成可信运输，报告为 blocked。这个结果定位到可靠技能后端/控制接线，不能转化为任务成功，也不能通过拖动或中心接近伪造 dock 回执。联合承载和 dock 业务只有在真实物理输送、释放和接收链闭合后才能进入正式计分。

## 发布结论

当前统一 release manifest 仍为 `release_status=pending`、`benchmark_ready=false`、`scoreable=false`。阶段17的任务骨架、公开/参与者/隐藏层边界、冻结哈希、City 微回执、House 落位检查和 Warehouse 证据门禁可以供接口联调与后端修复使用。正式发布仍需要 Warehouse 可靠承载后端、完整 City 业务服务、House 真实物品 detector/access-path 后端，以及固定 revision 上的完整参考 episode。所有未闭合项保持显式 pending 或 blocked，不由调度完成、协议测试或模型自报成功补齐。

验证命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 conda run -n env_isaaclab pytest -q \
  demo/city_benchmark/tests \
  demo/warehouse_benchmark/test_dock_receiver.py \
  demo/warehouse_benchmark/test_live_witness.py \
  demo/warehouse_benchmark/test_domain_adapter.py \
  source/EAI/test/test_benchmark_goal_indoor_deployment.py \
  source/EAI/test/test_benchmark_house_live_path.py \
  source/EAI/test/test_benchmark_goal_physical_probe.py
```

结果：`214 passed`。另有 `compileall` 和目标范围 `git diff --check` 通过。Isaac 运行均在 `env_isaaclab` 中按场景串行完成；本阶段没有启动 ROS。

阶段证据汇总器 `stage17_evidence_gate.py` 对三份真实报告的结果为：City
`diagnostic_only`、House `diagnostic_only`、Warehouse `blocked`；汇总结果仍为
`release_status=pending`、`scoreable=false`。Warehouse 的 blocker 被展开为报告状态
blocked、输送位移不足和缺少释放后 dock 回执。该汇总器只读报告并 fail closed，不能把
调度退出码或单独的接头读回提升为业务成功。
