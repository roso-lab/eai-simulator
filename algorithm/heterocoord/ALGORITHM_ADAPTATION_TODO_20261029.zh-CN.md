# 被测算法适配后续 TODO

日期：2026-10-29

执行规则：后续修改只在隔离工作树 `/home/airs/.cache/heterocoord/publish/adapters-20261028` 中进行，默认不提交、不推送。只有用户明确指定发布时，才整理提交并推送到 `HeteroCoord`。

总原则：只接入原算法已经具备、且 Benchmark 契约能够表达并由 evaluator 验收的能力。原算法没有的能力不补；契约不能表达的能力保留完整 native output，并记录 `N/A` / `not_evaluable`。

## 当前基线

- 六个正式算法列：FMC-ATA、BT capabilities、SADCHER、HARL:HAPPO、TeamWeaver、EMOS。
- HeteroMRTA 不纳入正式列。
- 上一阶段研究报告：`ALGORITHM_AND_BENCHMARK_SCOPE_REVIEW_20261029.zh-CN.md`。
- 当前所有 adapter 都能在声明范围内调用，但六类算法尚未全部达到可比较的全量 Benchmark 验收。
- 当前远端基线已发布到 `0fa8cdd0`；后续工作默认不改变远端。

## P0：基线冻结与证据规则

状态：已完成基线冻结；后续每一轮代码修改前继续复核。

- [x] 记录六个上游 source commit、论文引用、权重/模型版本和当前 adapter commit。见 `records/algorithm_adapters/baseline_20261029/BASELINE_MANIFEST.json`。
- [x] 为每个算法建立 `native_input_contract`、`native_output_contract`、`adapter_mapping`、`fallback_used`、`scope_rejection`、`evaluator_evidence` 字段。
- [x] 检查测试输入在算法调用期间保持 hash 不变。
- [x] 所有实验结果分开记录 `native_decision`、`native_failure`、`unsupported_input`、`executor_failure`、`evaluator_success` 和 `N/A`。
- [x] 确认新增测试只使用隔离工作树，不覆盖主工作区 dirty 文件。

准入条件：没有 source、输入、输出和证据身份的任务不进入正式适配。

## P1：统一契约与 SADCHER coalition 边界

优先级：最高。原因：当前 `Assignment(task_id, robot_id)` 无法表达 P3-J 的多成员联合义务。

- [x] 盘点 `Assignment`、transport command、TaskRunner、lock、executor、P3-J evaluator 对 coalition 的全部字段需求。
- [x] 设计一个可选的 coalition 表达草案，保留单机器人算法的兼容性；草案阶段不改现有正式契约。见 `COALITION_ADAPTER_CONTRACT_DESIGN_20261029.zh-CN.md`。
- [x] 明确 coalition 的成员集合、同步窗口、任务 ownership、替补、释放和反馈关联方式。
- [x] 证明 P3-J executor 不会把 coalition 截断成单机器人，也不会由 Fleet 自动选择成员。
- [x] 为 SADCHER 编写 native coalition receipt：完整成员、原生 reward、原生 schedule、适配拒绝原因和 `N/A_reason`。
- [x] 已确认契约、executor 和 evaluator 尚未共同表达 SADCHER coalition terminal evidence，因此不实现强行接入；完整 native coalition 保留并保持 `N/A`。

通过条件：

- 原生 coalition 与 adapter receipt 成员完全一致；
- 未发生成员截断、默认成员补齐或 host 重分配；
- evaluator 能独立判断 joint milestone；
- 单机器人算法的既有回归不受影响。

## P2：FMC-ATA 原生能力扩展

优先级：高；只扩展 FMC-ATA 源码已有输入和通信语义。

- [x] 核对 FMC-ATA 原生 dynamic task arrival 语义；当前 EAI native worker 输入仍是静态独立任务，正式动态 benchmark 条件登记为 `not_registered`，不伪造为已验证。
- [x] 已检查 asynchronous message、NCLO budget、convergence threshold 和通信协议的 native worker 传递；边界 receipt 保留 native counters/context，未声称完整 wire trace。
- [x] 设计并运行 adapter 层延迟/丢包和 task-arrival 记录 fixture；证据见 `records/algorithm_adapters/fmc_ata/native_boundary_receipt_20261029.json`，明确标为 adapter boundary only。
- [x] 当前 Benchmark 契约不能表达 FMC-ATA fractional physical dispatch 或 coalition；native schedule 保留，相关指标写 `N/A`。
- [x] 已通过 native failure、fractional allocation、source integrity 和 TaskRunner 边界测试验证宿主不会在 native failure 后 argmax、四舍五入、排序或重新竞拍。
- [x] 单任务停滞、动态到达未注册和通信边界分别记录；它们不混入算法失败率，详见 FMC-ATA boundary receipt。

通过条件：原生 solver、utility、schedule、终止逻辑与直接上游调用一致；所有宿主反馈都能证明只是 schedule consumption，或明确标成 adapter extension。

## P3：BT capabilities / ros_bt_py 能力覆盖

优先级：高；保持原生 capability auction 和行为树状态机。

- [x] 固定 capability repository、bid、auction、tree type 和 child state 的输入回执。
- [x] 分别测试 `MemorySequence`、`Parallel`、failure、continue 和 success 的状态转移。
- [x] 检查 EAI feedback leaf 与原生 BT tick 的边界，避免把 host 状态写成算法内部状态。
- [x] 对复杂 DAG、MissionControl、coalition 和全局最优性分别做能力登记；未注册项保留 `N/A`。
- [x] 只有有外部 evaluator receipt 时才记录物理 milestone；BT `SUCCEEDED` 单独保存。

通过条件：行为树状态、竞拍结果和执行反馈可回放；没有默认 bid、隐式任务修复或将物理成功写入算法 decision。

## P4：HARL:HAPPO checkpoint 与环境契约

优先级：高；这是当前最容易发生“接口能跑但算法不再等价”的部分。

- [x] 为每个当前候选 checkpoint 核验 actor 数量和顺序、observation shape、share observation、action space、action mask、normalization、RNN state、policy head 和 checkpoint keys。
- [x] 对比训练环境与 EAI 宏环境的字段顺序、坐标域、任务语义和 episode horizon；不匹配时 fail-closed。
- [x] 区分官方 checkpoint、重新训练的 self-trained smoke checkpoint 和不兼容 checkpoint。
- [ ] 没有同构官方 checkpoint 时，不映射为 UR5 抓放或 Benchmark 论文结果。
- [x] 验证 `reset -> act -> feedback -> act` 的 recurrent state 生命周期，以及 terminal 后的 hidden state 清理。
- [x] 保留原始 HAPPO action；`Assignment` 仅作为 adapter mapping，并在 provenance 中标明。

通过条件：checkpoint、训练契约和推理契约逐字段一致；否则只报告 adapter smoke 或 `N/A`。

## P5：TeamWeaver 原生语义链与扩展隔离

优先级：中高；TeamWeaver 是课题组算法，允许兼容性语法修复，但不能改算法效果。

- [x] 保留两处授权 `iflen -> if len` 语法补丁，并单独记录 patch hash；不修改锁定上游 checkout。
- [x] 核对原始 LLM prompt、原始 response、parser、任务分解、MIQP 变量、目标函数和硬约束。
- [x] 将 `ExecutionFeedback`、CapabilityTracker、phase scheduler、lease、checkpoint 和 evaluator 明确标为 HeteroCoord extension。
- [x] 测试 parser 成功、parser 拒绝、MIQP 不可行和 fallback 四条路径。
- [x] fallback 使用时保存 native output、拒绝原因和 `fallback_used=true`，不计入 native LLM/MIQP 结果。
- [x] 单独核验多轮语义持久状态、历史重建、依赖边和同任务 coalition；无法表达时拒绝并保留 native result。

通过条件：原生 planner/solver 结果与直接上游调用一致；扩展行为有独立 provenance；失败推理不会被替换成成功分配。

## P6：EMOS discussion 与执行阶段边界

优先级：中高；先保持 stage-one 原生性，再评估 stage-two 是否具备同构环境。

- [x] 核对 robot resume、robot type、capability、position、scene/task description 和 group discussion 输入。
- [x] 保留 leader prompt、robot prompt、原始响应、parser 输出和 response hash。
- [x] 输出格式约束只放在解析后的 adapter seam；不把 evaluator 成功标签注入原始 prompt。
- [x] 明确 `native_feedback` 与 `adapter_feedback_replan` 的 provenance 边界，禁止混写。
- [x] 检查 Habitat-MAS/CrabAgent、低层 action、原论文场景和 success detector；缺失项继续标为 `N/A`。
- [x] parser 无法表达的 coalition、未知 task 或歧义响应会拒绝并留证，不自动填充。

通过条件：原始 discussion/parser 未被替换；物理执行证据与算法输出独立；adapter 扩展不会伪装成 EMOS 原生能力。

## P7：统一执行与 Benchmark 条件矩阵

优先级：中；依赖 P1-P6 的能力结论。

- [x] 为六类算法分别登记可测的 primitive、关系签名、P3 subtype、公开观测和信息权限；统一读取接口为 `primary_capability_matrix()`。
- [x] 建立 primary、heterogeneous cognition、privileged reference 三类条件，禁止混合统计。
- [x] 每个算法的统一条件矩阵要求 native decision-only、输入拒绝和执行/证据边界三类 fixture；实际证据见 `records/algorithm_adapters/FIXTURE_EVIDENCE_MATRIX_20261029.json`，缺失项保持 `pending`，不填零。
- [x] 已在 `CONDITION_SUPPORT_MATRIX_20261029.json` 对 `P1`、`P2`、`P3-J`、`P3-S`、`P4` 分别记录支持、未注册、不适用和 pending 状态。
- [x] 结果表保留 `pending`、`N/A`、`not_evaluable` 和 `unsupported_input`，不填零，并由 release gate 校验。
- [x] 只有共同条件、共同 evaluator、完整 horizon 和独立 receipt 同时满足时，才进入跨算法比较；当前六列均保持 `pending_common_condition_and_receipt`。
- [x] 增加 fixture receipt 自动校验器 `tools/validate_fixture_evidence_matrix.py`，并生成 `records/algorithm_adapters/CONDITION_SUPPORT_MATRIX_20261029.json`；校验器不会把 `pending`、`N/A` 或 `not_evaluable` 转成分数。

通过条件：每个结果列都有清楚的 source-derived implementation、adapter version、condition identity 和 evidence scope。

## P8：最终回归与发布门

优先级：最后执行。

- [x] 各算法分别使用其官方环境或隔离依赖运行专项测试；环境和可选求解器边界见 `records/algorithm_adapters/environment_regression_20261029.md`。
- [x] 运行共享 adapter、输入完整性、source integrity、contract 和 receipt 测试；本轮共享/receipt/source 回归通过。
- [x] 对所有正式六列的 `N/A` 逐项检查 reason；矩阵见 `records/algorithm_adapters/ADAPTER_STATUS_MATRIX_20261029.zh-CN.md`。
- [x] 生成六列适配状态表、源码版本表、测试结果表和未完成边界表；见 `records/algorithm_adapters/ADAPTER_STATUS_MATRIX_20261029.zh-CN.md` 和 baseline manifest。
- [x] 检查主工作区无关 dirty/untracked 内容未被修改；本轮仅在隔离工作树操作。
- [ ] 只有用户明确要求时，才执行 commit、push 或合并。

最终通过条件：每个可报告指标都有匹配的原生能力、统一契约、外部证据和可复现 receipt；其余指标明确标为 `N/A` / `not_evaluable`。

## 暂不做的事情

- 不为算法增加论文中没有的 coalition、自动重试、启发式 repair 或隐式 replan。
- 不把 UR5 follow-proxy、理想化逻辑执行或 BT 状态当作所有算法的共同物理成功指标。
- 不为 HARL 伪造官方 checkpoint 或把宏动作 smoke 当成论文复现。
- 不把 EMOS/TeamWeaver 的 adapter feedback extension 写成原生反馈能力。
- 不把有限运行、队列清空、进程退出或单次成功升级为完整 Benchmark 结论。
