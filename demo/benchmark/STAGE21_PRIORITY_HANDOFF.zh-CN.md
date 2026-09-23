# 阶段 21：评分证据接入与能力轴优先级交接

日期：2026-09-23。代码基线：`125612f` 加本阶段接线改动，目标分支为
`benchmark-task`。本文是阶段交接记录，不改变冻结题包、见证计划、`reach_m`、
评分协议原文件或任何发布标志。

## 本阶段已完成

评分侧车新增 `cost_evidence` 原始归档。`goal_cost_evidence.py` 只读取已经存在的
可信宿主记录：GoalSession 的接收时钟和已接受动作、`team_usage.json` 中明确标记为
proxy 的激活成本，以及通过 `validated=true` 通信回放证明的 `bytes_served`。规划和
重规划计算时间没有可信来源时保持 `null`；没有通信回放时通信字节保持 `null`。
归档同时写出来源、有效性和 raw-only 标志，始终保持：

```yaml
execution_validated: false
business_outcomes_validated: false
scoreable: false
```

`FormalGoalEpisodeRunner` 会写出独立的 `cost_evidence.json`，评分元数据同时嵌入同一
份内容并将其加入归档索引。该接线不把动作数当作物理耗时，不把缺失通信账本当作零，
也不窥探被测模型进程内部通信。

## 按能力轴的主线优先级

1. **P0 部署可行性与剩余任务 oracle**：完成五场景 native reader、硬终局谓词和
   预注册动态处理点的二元 `deployable/not_deployable` 校验；`oracle_status=failed`
   只记平台缺口，不能进入模型分母。
2. **P0 真实时钟、通信和高层成本**：把现有云—边—端池、位姿按需工具、观察、指令、
   机间消息接入 GoalSession 事件源。模型可以不订阅位置；只有真实产生并送达的消息才
   计入字节与 AoI。当前 `cost_evidence` 是接线前的 raw-only 地基。
3. **P0 checkpoint/sham**：在干预发生前保存环境、参与者可恢复状态、可见历史、调度器、
   通信队列、时钟和编键随机流；先通过无处理 sham，再做 fact/reference/intervention。
4. **P1 经验学习**：在上述门禁后冻结 learning opportunity registry，做共同首轮的
   R/C 配对；`eligible/ceiling/unknown` 分母先于任何结果冻结。
5. **P1 任务设计增补**：沿用“总体目标 + 可验证里程碑，由模型自行拆题”的题面，分别
   设计规模响应、失效恢复、持续服务、必要机间协商和成本校准题。不得把作者 witness
   的子任务或顺序泄露给模型，也不得把低层理想化抓取失败当作高层能力错误。

## 当前高层审查结论

`CAPABILITY_DESIGN_AUDIT.zh-CN.md` 已确认五场景可提供分配调度、顺序依赖和部分联盟
协作基础，但规模响应、经验学习、持续主动配合和正式通信降级证据仍不完整。餐厅当前
适合做低耦合成本校准，资产 source closure 完成前不进入学习或正式计分；Factory、
Warehouse、House、City 的原生执行缺口分别归为平台/资产/低层证据，不冒充模型失败。

## 下一阶段出口

- 本机与 ljy 隔离工作树完成同一提交的评分、通信、部署和 raw-cost 回归；
- `cost_evidence` 的真实通信来源至少有一条经 replay 验证的三层事件链；
- 五场景 native feasibility reader 与动态失效点达到 `oracle_status=verified`，或明确
 记录不能进入正式统计的场景；
- checkpoint/sham 和真实时钟仍未通过时，继续保持三个发布标志为 false。

## 双机复验

本机 env_isaaclab 专项：`44 passed`（cost/scoring/runner，含 malformed-source
fail-closed）。ljy 隔离工作树 `/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e`
通过 git bundle 同步到 `36941ad` 后，使用其 `python3` 完成同一部署、通信、评分和
raw-cost 专项：`81 passed`。ljy 原仓未提交内容未触碰；远端没有可用的
`env_isaaclab` 路径，因此没有声称其运行 Isaac 原生 episode。

阶段收尾时本机完整 `source/EAI/test/test_benchmark_*.py` 回归为 **2248 passed，
246.55 秒**。该回归仍是轻量/契约和假后端测试；没有把它解释为五场景原生业务完成，
也没有改变任何 release gate。
