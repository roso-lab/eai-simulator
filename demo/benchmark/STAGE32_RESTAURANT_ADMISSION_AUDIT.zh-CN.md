# 阶段 32：餐厅八机团队 native 准入只读审计

日期：2026-09-24  
审计基线：`98daac0`（`benchmark-task` 远端同提交）  
审计性质：只读，未修改任何代码；证据全部来自仓库当前文件。

## 1. 当前准入状态位（代码出处）

| 状态 | 值 | 出处 |
|---|---|---|
| package_kind | `authoring_only` | `demo/benchmark/goal_scene_registry.py:30-37` |
| authoring_status | `passed` | 同上 |
| execution_status | `external_pending` | 同上 |
| scoreable | `False` | 同上 |
| 部署配置 | `bound_native_v2` | `demo/restaurant_benchmark/config.py:29` |
| 准入状态常量 | `authoring_pending_native_candidate_evidence` | `demo/restaurant_benchmark/config.py:56` |
| 部署案例发布状态 | `authoring_only_requires_native_and_positive_certificate` | `demo/benchmark/goal_deployment_cases.py:73` |
| oracle 状态 | `oracle_status=failed`、`feasibility_label=null`、`quality_failure=remaining_task_oracle_not_yet_certified` | `goal_deployment_cases.py:55-57` |

结论：餐厅当前不满足 native admission 的任何正式条件；所有部署案例的正式标签为空，
内部诊断（`goal_deployment_feasibility.py`）只能作为必要条件诊断，不能冒充正式真值。

## 2. 八机候选池准入矩阵

八机池定义在 `demo/benchmark/goal_driven_tasks.py:1250-1273`（`_restaurant()`），
出生锚点由 Restaurant config 的 `BENCHMARK_ROBOT_SPAWN_XYZ_M` 统一生成。池 docstring
原文："Only Carter routes currently have native evidence; the remaining candidates
stay authoring-only until their controller and contact probes are complete."

| 池成员 | 类型 | 公开能力 | 原生证据状态 | 进入 native admission 还缺 |
|---|---|---|---|---|
| restaurant_carter_1 | carter / 地面 | ground_navigation + observe + relay | **已有**：单机 witness 闭环（staging→kitchen→staging→dining→staging 检查环、双区观察、relay、readiness 回执，`goal_restaurant_episode.py:1-8, PLAN_ID=restaurant_native_witness_a_v1`） | 已具备单机事实源；仍缺 remaining-task 注册与替代路径证书（见 §4） |
| restaurant_carter_2 | carter / 地面 | 同上 | **无独立证据**。算法 episode v1 的三机池里有绑定（`goal_restaurant_algorithm_episode.py:65-77`），但该模块是 executor v1，"concurrent driving and pools beyond these three agents are executor v2 work"（同文件 :13）；本审计未找到 carter_2 的原生 episode 证据归档 | 第二 Carter 出生位 spawn 绑定验证、路线 probe、（并行驾驶需 executor v2） |
| restaurant_m20_1 | m20 / 地面 | ground_navigation + observe + relay | 无（docstring 明示 controller/contact probe 未完成） | 控制器加载证据、spawn 绑定、staging↔kitchen/dining 路线 probe、接触/支撑 probe |
| restaurant_m20_2 | m20 / 地面 | 同上 | 无 | 同上 |
| restaurant_go2_1 | go2 / 地面 | ground_navigation + observe + relay | 无 | 同上；腿式控制器在餐厅隐形 Cube 代理碰撞域内的行为需要单独 contact probe |
| restaurant_lite3_1 | lite3 / 地面 | ground_navigation + observe（**无 relay**） | 无 | 同上；题面规则明示"Lite3 是合法导航/观察诱饵但没有 relay 能力"（`_restaurant()` 规则清单），准入矩阵必须保持这一能力差异，不得为凑数补 relay |
| restaurant_iris_1 | iris / 空中 | aerial_navigation + observe + relay | 绑定存在（`goal_restaurant_algorithm_episode.py:72-77`：hover-held 在公开 overview 锚点，relay 可从当前区域执行），原生飞行证据未归档 | hover 保持证据、overview 空中观察证据、（若参与）relay 从 overview 的回执证据 |
| restaurant_iris_2 | iris / 空中 | 同上 | 无 | 同上 |

注意：`goal_restaurant_episode.py:1-8` 明确单机 witness "does not admit the rest of
the authoring pool or promote the Restaurant package to scoreable status"——
单 Carter 闭环的证据边界在代码里就是显式的。

## 3. 单 Carter 闭环的覆盖范围与不可外推边界

已覆盖（`goal_restaurant_episode.py`，witness v1）：

- 单机串行完成两个服务区（kitchen/dining）的到达与地面支撑检查；
- 每次到达/观察/relay/回执都在同一 native session 的真实 `env.step` 后采集并绑定同一 episode；
- 区域支撑采样（四角偏移 + 射线，`REGION_SUPPORT_*` 常量）与 readiness 回执合并语义。

不可外推：

1. **不可推出多机团队已验证**：并行驾驶、协同调度、成员退出/加入的运行时事件链均无证据。
2. **不可推出其他机型可控**：m20/go2/lite3/iris 的控制器与接触行为在餐厅碰撞域内零证据。
3. **不可推出剩余任务真值**：witness 是作者见证路径，不是 remaining-task oracle；按
   `REMAINING_FEASIBILITY_ORACLE.zh-CN.md`，oracle 必须判断"从可信当前状态是否存在任一
   合法完成方案"，作者 witness 不是唯一合法路径。
4. **不可推出正式业务结果**：A2 资产证据（`STAGE23_RESTAURANT_A2_EVIDENCE.zh-CN.md`）
   在 ljy 隔离树通过 resolver/source audit/materialize/preflight，但依赖 ljy 原仓源闭包，
   本机被绝对路径与权限阻塞；正式 episode 未启动，`benchmark_ready=false`。

## 4. authoring_only → native admission 差距清单

按 `REMAINING_FEASIBILITY_ORACLE.zh-CN.md` 餐厅行 + `TRUSTED_STATE_CONTRACT.zh-CN.md` 逐项：

| 要求 | 现状 | 缺口 | 验收条件 |
|---|---|---|---|
| 可信终局事实 reader：两区实际到达与支撑检查 | witness episode 内有到达+支撑采样实现，但未封装为 trusted state source reader | reader 化 + source_versions/entity_ids 绑定 | `goal_trusted_state.py` 契约下的 physical_completion reader，实体版本随量测演进 |
| 当前观察（observation_record） | witness 有双区观察采集 | 同上 reader 化 | observation_record reader，观察摘要与区域绑定 |
| readiness 回执（business_receipt） | `service_backend.evaluate_readiness_receipt` 存在 | 回执作为 business_receipt 事实源接入合同 | 回执 reader + 截止时间绑定（240 s 预算内，见 `goal_restaurant_algorithm_episode.py` 时间窗注释） |
| 对照态：尚未检查/已检查 | 无注册 | 预注册 measurement points | 每区两态各至少一个测量点 |
| 对照态：relay 仍需/已完成 | 无注册 | 预注册 | relay 完成前后各一个测量点；"回执已提交后 relay 能力退出仍 deployable"必须入测 |
| 替代路径：单机串行 vs 多机并行 | 单机串行有 witness；多机无证据 | 转移模型需枚举两类合法路径 | 替代路径完备性证书（本阶段轨道B schema）覆盖两类；lite3 无 relay 的差异必须保留 |
| 时间预算：单机串行合法替代和剩余时间预算 | 每腿 45 s 窗口 + 240 s 总预算已定（executor v1 注释） | oracle 转移模型未建模时间预算 | 剩余时间不足时串行替代不合法的对照态 |
| 实体版本绑定 | 无 | 全缺 | 每测量点 entity_versions 全集绑定 |
| 八机池成员能力版本 | 池声明为公开部署声明，native 证据仅 Carter | §2 矩阵逐机补证 | 每台准入机有控制器/路线/接触（空中为 hover）证据归档 |
| 发布审核 | `oracle_status=failed` | 全部上述项 green 后 | `goal_native_oracle_registry.py` 审计 `oracle_release_ready=true`（此前恒 false） |

## 5. 建议准入顺序

1. **carter_2**（同型号第二锚点）：控制器零边际成本，验证第二出生位与路线；并行驾驶留给 executor v2。
2. **iris_1**（空中 hover 锚点）：与地面语义正交，验证 hover 保持与 overview 观察/relay。
3. **m20_1**（地面+relay 第三型号）：控制器加载 + 路线 + 接触 probe，一次补齐"多型号地面"维度。
4. **go2_1 / lite3_1**（腿式诱饵）：餐厅碰撞域内的腿式接触行为风险最高，放最后并保留 lite3 无 relay 差异。
5. 每台准入后重跑该机参与的对照态测量点；八机全绿后才允许把 `package_kind` 从
   `authoring_only` 改为 `goal_bundle`（这是发布动作，须走发布审核，不属于开发轨道）。

## 6. 开发态/发布态隔离声明

在拿到全部原生证据前，餐厅一切开发输出使用
`demo/benchmark/development_track_marker.py` 的开发态标记：

```python
build_development_marker(
    "restaurant",
    asset_status="development_unverified",   # A2 资产虽冻结，native 准入未完成
    evidence_class="contract",               # 或 "diagnostic"（跑 smoke 时）
    infrastructure_blockers=["restaurant_native_candidate_evidence_pending"],
)
```

- `release_eligible` 恒 `False`；`scoreable/execution_validated/business_outcomes_validated` 恒 `False`。
- `unverified` 只是平台证据状态，不传给被测模型、不进正式统计。
- 单 Carter witness 结果在八机准入完成前只能作为 diagnostic 证据归档，不进入正式分母。
