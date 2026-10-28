# 原算法输入输出适配边界

适用范围：论文主表 FMC-ATA、BT capabilities、SADCHER、HARL:HAPPO、TeamWeaver、EMOS；不包含 HeteroMRTA。
2026-10-28 用户明确允许 EMOS/TeamWeaver 的提示词与输出出口约束。
同日用户进一步明确授权课题组 TeamWeaver 的源码语法及不影响算法效果的兼容修复。
当前仅应用两处固定哈希的 `iflen` → `if len` 补丁，运行于临时副本，逐文件记录原始/修复哈希和差异。
此条件应标记为原生算法加兼容补丁，不能仍标记为“逐字节未改的完整 planner”。
原生 HRCS 求解器、任务效用、目标与约束保持原文件；历史自定义 Hungarian fallback 不作为原生运行。

## 允许的适配

| 算法 | 输入转换 | 输出转换 | 必须保持的算法行为 |
|---|---|---|---|
| FMC-ATA | 任务、机器人、能力、位置、时间单位与 ID | 原始分配比例、owner、调度顺序与时间 | 原生异步消息、效用计算与求解；不取 argmax 代替 fractional allocation |
| BT capabilities | 原生 ROS 竞拍请求、任务与能力字段 | 竞拍结果、行为树状态和真实执行反馈 | 原生竞拍与行为树；不伪造成功或自动替换获胜机器人 |
| SADCHER | 任务图、显式技能维度、二维坐标与运行状态 | 原生 scheduler、matcher、filter 的输出及 idle | 网络结构、完整 checkpoint、调度与 coalition；不能强制派发或截取 coalition 成员 |
| HARL:HAPPO | 与训练契约一致的观测、可行动作、agent 顺序、循环状态 | 原始离散动作到明确登记的技能/任务 ID | 原生 actor、trainer 与 checkpoint；不能把 inspect 策略仅靠重命名当作抓放策略 |
| TeamWeaver | 原生 world graph、工具描述、任务和执行反馈 | 原生 action/parser 协议；可明确约束合法工具名、实体 ID 与输出格式 | 原生任务分解、HRCS/MIQP、阶段推进与恢复流程 |
| EMOS | 真实 robot resume、场景描述与任务目录 | 在原生输出协议中加入任务 ID 格式约束并解析 | 原生讨论、机器人反思与重新分配流程 |

模型提示词的格式约束也可能影响模型选择，因此必须作为独立实验条件登记。
记录原始源码 commit、适配器 hash、输出契约版本、实际提示词 hash、模型/推理设置及原始响应。
不能把带输出约束的新结果追溯填写为旧条件下的成功。

## 不允许由 adapter 完成的决策

- 算法不分配时，自动补齐任务或强制选择机器人。
- 用另一套启发式、匹配器、求解器或 LLM 代替原算法。
- 隐藏 native 失败、超时、未知动作、无法表达的 coalition，或只挑成功重复。
- 根据评测答案修改提示词，或在看到输出后追加专门对应本次结果的任务绑定。
- 只为获得派发而改变能力、任务语义、checkpoint 或训练时动作含义。

未知输出应保留原文和拒绝原因。格式约束须在推理前确定，不提供分配答案。
原始源代码语法/逻辑错误与 I/O 适配是不同问题，不能静默修正后仍声称源码未改。

## 验收分层

1. 接口回归：能发现非法 ID、状态、维度和完整性问题。
2. 原生运行：确实调用固定版本的原始实现；有真实输出、权重与调用证据。
3. 仿真集成：原生输出派发技能，物理宿主产生可验证的结果；原算法支持时回传反馈。
4. Benchmark 验收：统一场景与任务、预登记条件、重复与失败计数、独立 evaluator。

第 1/2 层不能当作第 3/4 层通过。单臂抓放宿主目前采用
`idealized_follow_proxy_pick_place; not contact grasp`，只证明该有界执行条件。

状态与证据索引见同目录 `NATIVE_ALGORITHM_ADAPTATION_REPORT_20261028.zh-CN.md`。
