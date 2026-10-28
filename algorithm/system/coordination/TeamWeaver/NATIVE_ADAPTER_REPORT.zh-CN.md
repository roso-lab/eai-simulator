# TeamWeaver 原生适配与授权兼容修复

2026-10-28 用户授权修复课题组算法的语法与兼容问题。修复保留原生任务分解、MIQP、
动作生成与 parser；历史 `eai_adapter` 的自定义算法不替代原生实现。

## 两个明确入口

```python
from benchmark.native_algorithms import build_native_adapter

# 外部提供 DAG；原生 HRCS 分配，可接 TaskRunner.run_orchestrated 的反馈重规划。
allocator = build_native_adapter("teamweaver")

# 原生 LLM 分解 -> 原生阶段组织 -> 原生 MIQP -> 原生 LLM 动作生成/parser。
# 当前是一张世界快照的一批规划；可以由 EAI 执行该批动作。
semantic = build_native_adapter(
    "teamweaver", scope="full_semantic", source_compatibility=True,
    output_seam=True, timeout_s=240,
)
```

`supplied_decomposition` 保持原始 HRCS 源文件，支持外部 DAG 完成反馈；失败、未知/重复反馈及
owner 矛盾均拒绝。原生每机器人恰好分配一项约束保持不变，coalition 不截断。
同一批同类任务的原生 utility 共享目标状态，因此无法表达的不同目标输入拒绝。

`full_semantic` 输出条件标记 `full_semantic_snapshot`。`replan` 不会偷偷新建原生 planner
以假装恢复已有阶段；当前明确拒绝语义跨批持久重规划和带完成历史的快照调用。
这不等于原生算法不具备多轮能力，而是该持久宿主连接尚未完成。
语义入口目前要求两个 active 机器人，保留原生两角色设定；不宣称任意团队规模或能力已验收。

## 源码修复和 I/O 微调

源 commit：`edf4619e4730860b96eebaac39620df4bbdc486a`。
`source_compat.py` 将完整 TeamWeaver 包复制到临时目录，仅修两处拼写错误：

- `planner/HRCS/plan_module/execution_manager.py:73`：`iflen(...)` → `if len(...)`。
- `planner/miqp_planner/optimizer/adaptive_optimizer_v2.py:381`：同上。

补丁严格校验 before/after SHA，记录完整 diff，未知字节拒绝应用。215 个 Python 文件均能编译。
原始缓存 checkout 保持不变，统一来源校验仍有效。运行条件声明“原生代码 + 两处语法补丁”，
不把修复后 planner 称为逐字节未改。可设置 `source_compatibility=False` 复现原始语法阻断。

`semantic_runtime.py` 提供 EAI 世界图与 OpenAI 兼容 API 边界。工具语法参数直接来自原生
skill class 的常量 property；规划用工具拒绝调用 Habitat motor 执行接口，不伪造 Habitat 仿真。
真实执行由 EAI host 提供。

输出契约 `heterocoord.teamweaver.output_seam.v2` 保留原始 Action 行，允许模型额外输出
`Agent_<uid>_Task: <task_id>`。这个标注由模型选择，adapter 只检查是否与原动作、目标、能力和
DAG 对应；没有标注的同目标同类型多任务仍拒绝。未知 ID、重复标注、错误能力、同任务 coalition、
Wait 伪称完成均拒绝。该提示词条件单独记录，不能与旧提示词成绩混算。

原生语义内部的失败/fallback 也必须保留：即使 LLM 返回合法动作，只要原生 MIQP 不可行或
内部报错，adapter 就拒绝派发。

## 与用户指定旧适配的关系

`/home/airs/eai-simulator/algorithm/TeamWeaver` 当前只残留 pyc。git 历史
`62d6c33^:algorithm/TeamWeaver/` 可恢复源码；核心 pipeline、dynamic_miqp、semantic_decomposer、
capability_tracker 与本工程 `eai_adapter` 仅导入路径不同，task_models 字节相同。
参考其任务标识、状态及反馈边界，但未使用其自定义分解、可靠性更新、任务追加或 Hungarian fallback。

## 实跑与复现

本机解释器：`~/.cache/heterocoord/envs/teamweaver-native/bin/python`；语义子进程使用
`~/.cache/heterocoord/envs/teamweaver-semantic/bin/python`，包含实际 Habitat/PARTNR 依赖。
Dongli 环境变量从用户环境继承，密钥不写入工程。实跑模型为 `gpt-5.6-luna`，推理 `low`。

```bash
cd /home/airs/hetrocoord
# 原生分配器 + 两批 DAG 理想逻辑执行
PYTHONPATH=.:source/EAI /home/airs/.cache/heterocoord/envs/teamweaver-native/bin/python \
  -m tools.algorithm_adapters.teamweaver_logical --output /tmp/teamweaver-logical.json

# 原生完整语义规划 + 一批理想逻辑执行；输出必须为新文件
PYTHONPATH=.:source/EAI DONGLI_MODEL=gpt-5.6-luna DONGLI_REASONING_EFFORT=low \
  /home/airs/.cache/heterocoord/envs/teamweaver-native/bin/python \
  -m tools.algorithm_adapters.teamweaver_semantic \
  --task records/algorithm_adapters/teamweaver/unified_entry/teamweaver_semantic_task.json \
  --output /tmp/teamweaver-semantic-new.json
```

已运行：分配器 2 机器人、2 批、4 次派发全部完成；语义规划实际调用原生
`LLMPlanner.replan`，经 3 次真实 API 请求、原生 MIQP Optimal、原生 parser，再执行 2 项逻辑导航。
另有不同目标用例得到原生 `infeasible_or_unbounded`，保留失败，不替换求解器。
结果是有界理想逻辑执行，不是 Isaac 或全论文 Benchmark 验收。

完整计数、失败尝试、测试和哈希见
`records/algorithm_adapters/teamweaver/repair_20261028/REPORT.zh-CN.md`。
