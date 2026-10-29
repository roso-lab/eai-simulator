# P1：SADCHER coalition 接入契约设计审查

日期：2026-10-29

结论：当前工程已经有 P3-J 的 canonical deployment、logical object joint holding、room06 joint-carry runtime 和 cooperative executor，但统一 `AlgorithmDecision -> TaskRunner` 路径仍是一任务一机器人。现在不直接修改 `Assignment` 或 TaskRunner；先保留 SADCHER 完整 native coalition，并把 coalition 接入设计为一个兼容扩展。

## 1. 已确认的当前边界

### 1.1 算法输出边界

SADCHER 的 native adapter 会把同一任务的多个机器人保留为多个 `Assignment`，同时在 provenance 中记录：

- `native_assignments`
- `coalitions`
- `robot_index`
- `task_index`
- `predicted_rewards_including_sentinels`
- `task_runner_compatible`

当同一任务出现多个成员时，adapter 抛出 `NativeCoalitionScopeError`，不选择一个成员、不补齐成员、不重新分配任务。当前回执原因是：

```text
benchmark_contract_cannot_represent_native_coalition
```

这表示统一契约缺少表达能力，不表示 SADCHER 缺少 coalition 能力。

### 1.2 统一决策边界

`algorithm/common_coordination/contract.py` 的 `Assignment` 只有：

```text
(task_id, robot_id, source, plan_version)
```

`task/runner.py` 当前会拒绝重复 `task_id`，反馈身份也是 `(task_id, robot_id)`。`TaskRunner._ready_assignments()` 以任务为唯一 active assignment，无法表示一个任务由多个机器人共同持有。

`task/executors/cooperative.py` 也要求一个 batch 内的 task identity 唯一，因此不能把两个同任务 `Assignment` 直接送入 cooperative executor 来伪造联合动作。

### 1.3 已存在但尚未接桥的 coalition 基础设施

| 现有模块 | 已有能力 | 当前缺口 |
|---|---|---|
| `benchmark/canonical_deployment_v3.py` | obligation participant matrix、role links、participant ticks、sync window、min/max participants | 没有消费 `AlgorithmDecision` 的运行桥 |
| `task/logical_objects.py` | `robot_ids` 集合、minimum holders、joint acquire/release、逐 tick membership monitor | 没有 coalition decision 输入类型 |
| `benchmark/room06_task_runtime_v1.py` | joint carry 的成员校验、替补、逐 tick holder safety | 绑定在 room06 专用 runtime |
| `task/executors/cooperative.py` | batch execution、resource lease、反馈完整性和 quiescent receipt | batch identity 不允许同任务多成员 |
| `benchmark/room06_terminal_material_v1.py` | required/observed holders 与 joint safety failure 证据 | 需要由通用 coalition request 触发 |

## 2. 推荐的最小兼容设计

### 2.1 新增独立类型，不改变现有 `Assignment` 语义

建议增加一个独立的 `CoalitionAssignment`，而不是给 `Assignment.robot_id` 塞列表：

```python
@dataclass(frozen=True)
class CoalitionAssignment:
    task_id: str
    robot_ids: tuple[str, ...]
    source: str
    plan_version: str = ""
    role_bindings: Mapping[str, str] = field(default_factory=dict)
```

最低不变量：

- `robot_ids` 非空、唯一、稳定排序；
- `task_id` 只能出现一个 coalition record；
- 所有成员必须属于公开 robot roster；
- 成员能力和角色校验由 adapter/host 依据公开输入完成；
- 不允许由 host 根据任务质量或 evaluator 真值自动增删成员；
- coalition record 不包含物理成功字段；
- 该类型只表示算法的成员选择，不表示已经同步、持有或完成。

`AlgorithmDecision` 可以在兼容版本中新增可选 `coalition_assignments` 字段，默认空元组。现有六类算法的单机输出和旧 JSON 仍保持有效。

### 2.2 反馈身份扩展

联合执行反馈不能继续只依赖 `(task_id, robot_id)`。建议增加：

```text
coalition_id = digest(task_id + sorted(robot_ids) + plan_version)
```

反馈至少携带：

- `coalition_id`
- `task_id`
- `robot_ids`
- `phase`：`admitted` / `synchronized` / `active` / `released` / `failed`
- `holder_observation_digest`
- `evaluator_evidence_ref`

`ExecutionFeedback` 的单机器人字段继续保留，避免影响 FMC-ATA、BT、HARL 等现有条件。联合反馈应通过独立 `CoalitionExecutionFeedback` 或显式 extension schema 接入，不把多个单机成功拼成一个联合成功。

### 2.3 执行桥的职责

新的 coalition bridge 只做以下工作：

1. 验证 native coalition 与公开 roster/能力的对应关系；
2. 编译一个 coalition skill request，包含完整 `robot_ids`；
3. 请求 logical object / cooperative runtime 进行同步、资源和 holder admission；
4. 接收逐 tick 成员证据和终态 receipt；
5. 将算法 decision、executor feedback、evaluator verdict 分开记账。

bridge 不做以下工作：

- 不选择 coalition 成员；
- 不把 coalition 拆成多个独立任务；
- 不把多个独立成功合成 P3-J；
- 不用 single-robot transport command 替代联合请求；
- 不向 SADCHER 注入 evaluator 真值；
- 不在 native failure 后自动重试或换成员。

## 3. 依赖与实现顺序

### C1：契约草案测试

- [x] 新增 `CoalitionAssignment` 的构造、排序、重复成员、未知成员和单成员测试。
- [x] 验证旧 `Assignment`、旧 `AlgorithmDecision` 和现有六类 adapter 测试完全不变。
- [x] 验证同一 task 只能有一个 coalition record，不能同时出现在单机 assignment 和 coalition assignment 中。

### C2：SADCHER 只读转换

- [x] 从 `decision.provenance["coalitions"]` 生成 `CoalitionAssignment`，不调用 executor。
- [x] 对比原生 coalition 成员集合，证明转换无丢失、无新增、无排序语义变化。
- [ ] 继续保留当前 `NativeCoalitionScopeError`，直到 C3-C5 全部通过。

### C3：通用 coalition request

- [x] 定义 `heterocoord.coalition_skill_request.v1` 请求 schema，复用 `minimum_holders` 和同步窗口字段；当前仅生成请求，不触发 runtime。
- [x] 增加 `prepare_coalition_request` 只读 admission bridge，校验任务身份、部署 roster 和完整 native 成员集合。
- [x] 增加独立 `execute_coalition_skill` 入口，向 logical runtime 发起一次完整成员 session；现有单机 `CooperativeBatchExecutor` 未修改。
- [x] 资源租约按 `coalition_id` 和完整成员集合原子计量。
- [x] 新增独立 coalition execution receipt，保留 session evidence，并将 evaluator verdict 标为 `external_only`。
- [x] 提供 receipt 到 evaluator `identity_records` 的只读绑定；不创建 milestone、base-task success 或最终 verdict。
- [x] 对 completed receipt 强制核对独立 `heavy_object` holder intervals；成员缺失、增加或越界均拒绝。

### C4：外部 evaluator 接线

- [x] coalition receipt 只作为 identity provenance 接入，不声明 evaluator verdict。
- [x] completed coalition 必须有完整且独立的 holder evidence；不满足时保持不可验收。
- [x] 增加同步窗口内逐成员持续持有和 release 顺序校验；仅返回 evidence 状态，不生成最终 verdict。

### C4：外部 evaluator 接线

- [ ] 用独立 joint predicate 判定同步窗口、最小成员数、持续持有和释放。
- [ ] 记录 holder evidence、session evidence、resource evidence 和 final milestone evidence。
- [ ] 证明单机串行执行无法通过 P3-J evaluator。

### C5：SADCHER coalition smoke

- [ ] 运行一个原生 SADCHER coalition fixture。
- [ ] 比较 direct upstream decision、adapter decision、coalition request 和 evaluator receipt。
- [ ] 任一成员缺失、自动补成员、成员替换或 evaluator 真值泄漏都判失败。

### C6：正式条件注册

- [ ] 只在 C1-C5 通过后，为 SADCHER 注册 P3-J 条件。
- [ ] 在此之前 SADCHER 的 P3-J 指标保持 `N/A`，原因固定为契约/执行证据缺失。

## 4. 不应修改的现有行为

- `TaskRunner` 当前单机 DAG 语义保持不变。
- FMC-ATA、BT capabilities、HARL、TeamWeaver、EMOS 的单机 `Assignment` 不自动升级为 coalition。
- SADCHER 的 native scheduler、matcher、filter、checkpoint 和 reward 不修改。
- P3-J evaluator 继续拥有 joint milestone 的最终判定权。
- 没有完整 coalition receipt 时，不能把逻辑执行结果写入正式 Benchmark 分数。

## 5. 当前决策

本轮完成的是 P1 影响面审查、兼容设计和独立 `CoalitionAssignment` 草案类型。下一次代码变更从 C1 剩余的容器级冲突测试开始；若发现现有序列化、TaskRunner 或 receipt pipeline 无法兼容，再回到设计阶段，不通过隐式字段或 adapter fallback 绕过契约。
