# 原算法 Adapter 适配与测试报告

日期：2026-10-28。范围依据 `/home/airs/下载/CVPR2026_Template.pdf` 的 Table 3 / Section J.1；该 PDF 的 SHA-256 和六个源码版本记录在 `algorithm/heterocoord/paper_algorithms.json`。HeteroMRTA 按用户要求排除。

本轮为六个论文结果列提供统一的 `benchmark.native_algorithms.build_native_adapter` 入口，各方法完成程度见下表。适配器转换任务、能力、观测、历史、动作/分配和执行反馈。2026-10-28 用户进一步授权 EMOS/TeamWeaver 的提示词和输出格式约束；此类约束须独立登记实验条件，仍保留原始讨论、求解、网络及权重。边界见 `ADAPTER_IO_POLICY.zh-CN.md`。所有上游 checkout 都在工程外的 `~/.cache/heterocoord/upstream/`，调用前后经过 `verify_source`。

本报告保留分阶段的原始记录；当前分支集成、最终回归与版本边界以 `ADAPTATION_PUBLICATION_20261028.zh-CN.md` 为准。后文较早的 TeamWeaver 语法阻断记录已由 `teamweaver/repair_20261028/` 续作取代，历史失败不删除。

## 验收状态

| 方法 | 原生运行 | 统一入口 | 反馈/仿真 | Isaac/物理 | 当前结论 |
|---|---:|---:|---:|---:|---|
| FMC-ATA | 多机器人多独立任务返回完整原生 schedule；单任务仍暴露上游收尾停滞 | 发布复跑 65 passed，3 个上游限制 xfailed；此前宿主 7 passed | 原生日程队列消费 + 双 UR5 批量执行反馈 | **通过双 UR5 两任务闭环** | 原版单任务停滞保留为上游限制；不支持 coalition/fractional 物理执行和 FMC 内部 feedback/replan |
| BT capabilities | 7 项真实 ROS1 原生运行 | 最终原生专项 66 passed；双臂宿主 24 passed | 原生 auction + Parallel/MemorySequence + DAG 批次反馈 | **通过同场景双 UR5 三任务闭环** | DAG、双臂抓放与依赖汇合通过；完整 MissionControl、接触抓取和论文分数未验收 |
| SADCHER | 默认修正为官方 benchmark 完整 checkpoint，原生 scheduler/matcher/filter 不变 | 最新专项 60 passed；共享入口/完整性另 8 passed | 原生二维 Simulation，3 dispatch / 2 batch，tick 31 完成任务，tick 59 退出；直接原生调用一致 | 单 UR5 保留 idle；双 UR5 仅决策预检，本轮未启动 Isaac | 有界多机器人 DAG 闭环通过；同任务 coalition 执行仍不支持，未宣称全部论文任务验收 |
| HARL:HAPPO | 原始 HAPPO actor/trainer/runner 运行 | 本轮方法目录独立复核 69 passed | 16/16 符号 holdout，v1/v2 actor parity | UR5 契约不兼容，未启动 | 当前双 actor inspect 权重不能重命名为单 UR5 抓放；缺匹配物理契约的权重与持久反馈生命周期 |
| TeamWeaver | 原始 HRCS MIQP / CVXPY / Gurobi；原生 LLMPlanner.replan + Dongli gpt-5.6-luna/low；两处授权语法补丁 | 最新方法目录 236 + 共享集成 21 = 257 passed | 分配器：2机器人/2批/4次派发；语义：真实分解/MIQP/动作生成后2次逻辑执行 | 理想逻辑宿主，未启动 Isaac | 语法阻断已解除；语义单批接入通过，语义多轮持久状态及同任务 coalition 仍未验收；不同目标用例原生 MIQP 不可行已保留 |
| EMOS | Dongli `gpt-5.6-luna / low`；新增 `emos.native-output.v1` 结构化 I/O 条件后返回 `ur5_1 -> move_carton` | 100 passed；结构化 leader/robot 输出均保留原始响应与 hash | 原生讨论/反思未替换；格式错误、未知 ID、歧义和 coalition 拒绝 | **通过 1 个有界 UR5 集成** | 输出契约条件下完成一次原生分配和 follow-proxy 抓放；不等同完整 EMOS stage-two 或 Benchmark 分数 |

“原生决策返回”不等于任务完成；“符号完成”不等于物理成功；物理成功也不等于论文 Benchmark 分数。完整论文统计还需要统一场景、条件、重复、通信/故障条件和独立 evaluator。

## 已验证的物理闭环

FMC-ATA 本轮修复证据见 `records/algorithm_adapters/fmc_ata/repair_20261028/REPORT.zh-CN.md`：原生 FMC-ATA 在两个独立任务上输出完整日程，宿主按每机器人原生日程队列消费，两台 UR5 在同一场景执行并完成 2/2 任务。适配层没有修改原 solver、没有重竞拍、没有补派；反馈仅用于宿主计划消费。单任务原版消息收尾停滞仍保持 blocked/xfail，不被伪装成通过。


BT 本轮新增同场景双 UR5：`records/algorithm_adapters/bt_capabilities/multi_robot_20261028/REPORT.zh-CN.md`。原始竞拍与 BT 控制代码未修改；两项抓放完成后通过依赖汇合检查，3/3 completed、BT SUCCEEDED、进程正常退出。5,053 个物理步中两臂实测同时运动 3,728 步（18.64 秒）；各抬升 0.135054 m、放置误差 0.0000113215 m、attach/release 各 1/1，四组 arm/table 检查均零重叠。原始轨迹和 source hash 已独立复核。第一次 Kit 退出阻塞的记录单独保留，完整验收取修复宿主退出后的 `physical_002/`。范围仍为 follow-proxy 抓放和仿真真值检查，不是接触抓取或 onboard vision。

BT 的证据位于 `records/algorithm_adapters/bt_capabilities/native_20261028/`：原生拍卖得到 `ur5_1 -> move_carton`，Isaac UR5 host 执行抓放，反馈回传原生行为树并得到 `SUCCEEDED`。测得抬升 `0.135033 m`、最终位置误差 `0.000011 m`、attach/release 为 `1/1`、碰撞重叠步数 `0/5496`。执行语义为 `idealized_follow_proxy_pick_place; not contact grasp`，Benchmark 分数不可用。

SADCHER 的 UR5 输入由原始网络选择 idle，报告为 `records/algorithm_adapters/sadcher/ur5_pickplace_native_decision.json`。没有通过调整参数强行派发。

## 分阶段历史测试证据

- 共享回归：`82 passed`，见 `records/algorithm_adapters/final_shared_regression.xml`。
- 注册表与系统契约：`68 passed`，见 `records/algorithm_adapters/registry_regression.xml`。
- 独立方法测试：FMC-ATA `22 passed, 1 xfailed`；SADCHER `21 passed`；HARL `42 passed`；TeamWeaver 更新后 `28 passed`；BT live report `14/14`。EMOS 历史协议测试与 live 测试不可混计；`emos_final_tests.xml` 为先前完整 72 passed。
- 源码完整性：六个上游 checkout 的 commit/tree/hash 见 `records/algorithm_adapters/source_receipts.json`。
- Python `compileall` 和 `git diff --check` 已通过。

本轮输出适配续作：共享入口 `165 passed, 1 skipped`（`final_output_adaptation_regression_20261028.xml`）；宿主参数及失败记录 `12 passed`（`native_physical_receipts_tests_final_20261028.xml`）；TeamWeaver `40 passed`、HARL 方法目录 `69 passed`、SADCHER `35 passed` 均在各自隔离解释器独立复核。初次 SADCHER 复核暴露测试错误地固定断言 CPU，而原始 scheduler 按 CUDA 可用性选设备；仅修正测试并复跑通过，未修改调度器或适配器。首次混用系统 Python 的收集错误保留在 `agent_method_regression_20261028.xml`，不是算法运行失败，也不计入通过。

多个目录存在同名 `test_native_adapter.py`，必须按方法解释器分别运行，或使用 pytest `--import-mode=importlib`；不运行全仓 pytest，因为历史日志和下载源码不属于本轮测试集合。

## 复现入口

统一命令和方法边界见 `algorithm/heterocoord/REPRODUCTION_PLAN.zh-CN.md`、`tools/algorithm_adapters/README.md`。统一 CLI 的退出 0 只表示原生决策返回，退出 1 表示原生失败/范围拒绝，EMOS 缺真实端点时为 `blocked`；不会调用旧的固定分配、启发式或替代 solver。

以下为适配阶段的历史工作树说明，已由发布索引的隔离分支集成记录取代。

当时工作树仍保留用户原有的 stadium、论文、网站和训练等 dirty/untracked 内容；本轮未提交、未推送，也未合并远程分支。开始前已检查 `origin/HeteroCoord`，其相对当前 HEAD 为 `4 ahead / 163 behind`，因此没有整体 merge。

本轮续作重新 fetch：远端由 `769e31d8` 更新至 `c6eca6403d70bb8be3e10b0aea2f9a09033367ec`，增加 6 个提交，涉及 room06 和语义导航；EMOS/TeamWeaver 适配文件无新增差异。当前工作树未整体合并这些提交。

## Dongli 真实 API 追加记录

2026-10-28 用户明确授权读取 `~/.bashrc` 的 Dongli 配置并使用 `gpt-5.6-luna`、`reasoning_effort=low`。只向端点发送运行所需的 Authorization，不将密钥写入源码、配置或报告。此前未读取该配置时的零请求回执保持历史原样；当前不再以缺少凭据阻塞。

- 连通探针：HTTP 200，返回模型 `gpt-5.6-luna`，19 tokens；只计连通性，见 `records/algorithm_adapters/dongli_connectivity_20261028.json`。
- EMOS 双机器人检查例：3 次请求、1794 tokens。两个原生结果均为 `Inspect object one and object two`，无法无歧义映射，保留 `NativeOutputScopeError`，见 `records/algorithm_adapters/emos/live_dongli_20261028.json`。
- EMOS 四机器人 Fire Rescue fixture：20 次请求、25397 tokens。原生反思明确指出场景几何和执行能力信息不足，最终带条件的 idle 描述不能映射为约定的精确任务/idle，状态 not_passed。输入、20 个 API 响应、原讨论历史和源校验均保留于 `records/algorithm_adapters/emos/live_dongli_fire_rescue_20261028T075825Z/`。这是输入不完整的真实失败测试，不是物理任务测试。
- EMOS → UR5 正确 native 坐标输入：2 次请求、1520 tokens，模型返回完整搬运描述而不是预注册任务文本，严格映射拒绝；`integration.json` 明确 `physical_execution_performed=false`、未派发技能，不能算物理失败或成功。证据：`records/algorithm_adapters/emos/live_dongli_ur5_20261028T080837Z/`。此前独立坐标文案不一致的 fixture 保留在 `live_dongli_ur5_20261028/`，并由 `INPUT_LIMITATION.md` 标记不可复用。
- EMOS 输出契约条件：`emos.native-output.v1` 在推理前登记机器人 roster、任务目录、schema hash 和按 speaker 的 prompt hash；真实 Dongli 运行 2 次请求、2827 tokens，leader 返回结构化 `move_carton`，UR5 反馈返回结构化 `yes`，0 次契约拒绝，`fallback_used=false`。证据：`records/algorithm_adapters/emos/live_dongli_ur5_structured_v1_20261028T165508/native_decision.json`。
- EMOS 有界 UR5 宿主：同一 preregistered task/config 重新执行原生 adapter 后才启动 Isaac；物体和目标来自输入，`pick=true`、`place=true`、抬升 `0.135033 m`、最终原点误差 `0.000011 m`、attach/release `1/1`、重叠步数 `0/5496`。证据：`records/algorithm_adapters/emos/live_dongli_ur5_structured_v1_20261028T165508_host2/integration.json`；范围为 `idealized_follow_proxy_pick_place; not contact grasp`，Benchmark 分数不可用。
- TeamWeaver：真实原始 `OpenAIChat.generate` 和 parser 已通过 Dongli 边界调用；`full_semantic_report.json` 保留原始输出，但完整 `LLMPlanner.replan` 未执行。固定源码中 `execution_manager.py:73` 的 `iflen(...)` 是实际 SyntaxError，依赖已部署，不可用替代 planner 掩盖。详见 `records/algorithm_adapters/teamweaver/unified_entry/full_semantic_report.json`。

所有失败均不被降级成启发式成功或事后改输出。不同输入/重复运行的结果分别保留。

## 其余算法本轮新增证据

- FMC-ATA：`records/algorithm_adapters/fmc_ata/ur5_preflight_20261028/REPORT.zh-CN.md`；单机器人/单任务真实求解 10 秒和 30 秒预算均超时，worker 已回收。现有原算法有单任务处理 TODO，但未采集线程栈，不能据此断言精确根因。
- SADCHER：`records/algorithm_adapters/sadcher/physical_preflight/BLOCKED.md`；校正任务 XY 与宿主物体 XY 后，完整模型仍返回 idle，executor 调用数为 0。
- HARL:HAPPO：`records/algorithm_adapters/harl/UR5_PHYSICAL_CONTRACT_GAP_REPORT.md`；原权重的机器人数量、观测、动作和任务语义均不同，真实 CLI 在调用 actor 前拒绝 UR5 输入，原 holdout 仍运行两个 actor。
- TeamWeaver：`records/algorithm_adapters/teamweaver/output_seam_receipt.json`、`syntax_repair_advisory.json`；原 parser 后的格式/ID/工具/依赖/能力/coalition 检查已实现，语法修复仅提供差异建议，未应用或执行。官方当前 main 没有修复版；历史修复提交缺当前增强 planner，未替换锁定版本。完整 EnvironmentInterface、执行工具与 replan 仍未验收。

EMOS 宿主阶段为另一轮真实推理：2 次请求、2783 tokens；与前一 CLI 的 2827 tokens 分开计数。执行反馈由 TaskRunner/evaluator 保存，`native_feedback=null`，未虚构 EMOS stage-one 不具备的外部反馈接口。

EMOS 最终专项复核 `100 passed`（`records/algorithm_adapters/emos/structured_output_tests_final_20261028.xml`）。物理运行后另修正了实验条件标识，使结构化条件的 `prompt_revision` 绑定原提示词、输出 schema 和 speaker 格式约束 hash；旧运行回执保留原样。宿主也新增派发前输入 hash 检查，修改后已通过 12 项失败路径/参数测试；未将这两项后续改动伪装为此前物理运行时的源码版本。
