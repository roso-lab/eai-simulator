# TeamWeaver 授权修复与实际运行 — 2026-10-28

修复了原生语法错误、规划工具参数描述与 EAI 接口、任务 ID 输出映射，以及外部 DAG 的反馈重规划。
最终测试 **257 passed：TeamWeaver 目录 236 项 + 共享入口/来源/集成 21 项；0 failed / 0 skipped**。
原生完整语义规划已能进入 EAI 单批理想逻辑执行；语义多轮持久反馈尚未完成。

## 本次变更

- `source_compat.py`：完整复制冻结源码包，仅对两处 `iflen` 语法错误作固定哈希补丁；原始 checkout 不变。
  389 个包文件中 215 个 Python 文件全部编译通过。`source_compatibility=False` 可复现原始错误。
- `semantic_runtime.py`：使用原生 LLMPlanner、PerceptionConnector、阶段组织、MIQP、动作生成和 parser；
  接入真实 Dongli API，所有请求模型统一为用户指定 `gpt-5.6-luna`、`low`。
  工具描述/参数模式来自原生类；不构造或伪造 Habitat motor policy，执行交给 EAI。
- `native_adapter.py`：`full_semantic` 可返回有来源记录的单快照决策；原生内部错误或 MIQP 失败均拒绝派发。
  v2 输出契约允许模型自行添加 Task ID，保留原 Action 与 parser 结果，只校验而不补分配。
  外部 DAG 分配模式增加 executor 反馈 → 完成集合 → 当前观测重求解，拒绝矛盾反馈。
- `teamweaver_logical.py`、`teamweaver_semantic.py`：分别提供分配器两批闭环、语义单批实际执行及独立位置检查。

未改变 HRCS 目标函数、约束、任务 utility 或机器人指派结果。语法补丁、工具模式适配和提示词
变化均分别登记，不能声称完整语义运行逐字节未改，也不能把新提示词结果计入旧条件。

## 实跑证据

| 路径 | 实际结果 | 文件 |
| --- | --- | --- |
| 原生分配器 + 外部 DAG | 2 机器人、2 批、4 次派发全部完成；两次 MIQP Optimal，无 fallback；第二批使用第一批结束后的坐标 | `logical/receipt.json`、`logical/verification.json` |
| 原生完整语义单批决策 | 真实分解、MIQP Optimal、动作生成/parser，2 个合法任务 ID/机器人映射；0 native errors | `semantic_annotated.json` |
| 原生完整语义 + EAI 逻辑执行 | 3 次真实 API 请求；2 个任务派发并完成，两机器人从 [-1,0,0]/[1,0,0] 到 [0,0,0]；2 条 executor feedback | `semantic_logical.json` |
| 全部回归 | 257 passed，39.62 秒 | `final.xml`、`final.log` |

逻辑执行采用理想化位置更新与独立检查，没有启动 Isaac，也没有接触抓取或完整论文任务成功率证据。
runner 的物理 verdict 仍为 N/A。语义单批完成不代表原生阶段的多轮执行反馈已连通。

## 失败尝试完整保留

本轮共有 5 次真实语义尝试，合计 14 次 API 请求。不是 5 次成功任务：

1. `native_semantic_attempt1.json`：两次 API，原生 MIQP Optimal，但未绑定工具 grammar 导致内部动作生成错误并返回 Wait。
   此早期回执的局部 `planning_success=true` 不可靠；已有 `native_error_count=1`。随后修正成功门禁，任何内部错误均拒绝。
2. `native_semantic_attempt2.json`：三次 API、无内部错误；两个相同 Navigate 目标无法映射不同任务 ID，拒绝派发。
3. `semantic_distinct.json`：不同目标的预先定义用例，三次 API；原生 MIQP 报 `infeasible_or_unbounded`，wrapper 返回 Exception。
   虽 LLM 动作可解析，仍拒绝派发；未修改约束或用其他分配器替代。
4. `semantic_annotated.json`：新 v2 输出条件，三次 API，返回合法带 Task ID 决策；仅决策成功，没有执行。
5. `semantic_logical.json`：三次 API，完整原生语义规划后执行 2 项逻辑导航；本轮唯一语义执行验收。

汇总：`live_attempts.json`。失败不是通过重试抹去，也没有把不同目标失败的输入偷偷改为通过输入。
输入不变的同目标用例使用新声明的 ID 输出格式，避免 adapter 擅自选任务。

系统 Python 初查出现 4 项缺依赖/退化失败；原生环境初次全目录检查还有 httpx 缺失及旧测试固定期待 SyntaxError。
已补依赖、保留原始源码关闭补丁的对照测试，并增加修复后的实际 import 测试；最终全目录通过。
`intermediate.xml/log` 保留中间失败，`updated.xml/log` 是此前 218 passed 的阶段结果，不替代最终计数。

## 源码、本地参考与远端

冻结原版：`edf4619e4730860b96eebaac39620df4bbdc486a`；390 个受验证源码/配置文件汇总 hash
`6e43e80bcad2142bfeaf51eac00e81d7962f799386af123005709792ad875953`。
前后相同，见 `source_before.json` / `source_after.json`。临时补丁的 before/after/diff 在每次语义回执中。
本地实现哈希在 `implementation_hashes.json`，依赖在 `native_requirements.lock.txt`。

用户指定的旧路径目前只残留 pyc；从 git 历史核对后，核心适配与本工程旧 `eai_adapter` 基本一致。
复用了输入/反馈设计，未把其自定义分解、可靠性更新或 Hungarian fallback 当作原生算法。
详见 `logical/comparison.md`。

开工核查并 fetch 远端 HeteroCoord：`75a95615` → `e21c543e`，新增地图编辑修复，未涉及 TeamWeaver。
未合并到有其他未提交工作的当前分支，未提交或推送本轮变更。

## 使用与剩余边界

复现命令见 `algorithm/system/coordination/TeamWeaver/NATIVE_ADAPTER_REPORT.zh-CN.md`。
语义运行要求用户环境中的 Dongli 变量；密钥不保存到工程。

- 分配器外部 DAG 的多轮反馈已通过；原生语义阶段/对话的持久多轮宿主仍未实现，`replan` 明确拒绝，绝不重置原生状态冒充继续执行。
- 当前 TaskRunner 单 owner 契约不能执行同任务 coalition，保留完整原输出后拒绝。
- 不同目标的原生 MIQP 不可行用例仍失败；本次不更改算法约束以追求通过。
- 当前语义层保留原生两角色设定，不声称任意团队规模、任意技能或所有论文任务通过。
