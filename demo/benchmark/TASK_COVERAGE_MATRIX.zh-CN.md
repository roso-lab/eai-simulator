# 四场景任务机制覆盖矩阵

这是任务生成与审核的 authoring registry，不是实验结果。它将 canonical mechanism template、factor condition 和 fixed-DAG/service diagnostic 分开计数。

当前兼容口径仍是 36 个 goal package、28 个 factor pair；另有四场景 × 四机制的 16 个 M0 canonical templates，生成入口为 `generate_canonical_goal_task_packages()`，发布入口为 `write_goal_mechanism_suite()`。旧 36 包继续标记为 `legacy_measurement_unclassified`，M0 模板单独标记为 `classified`，执行和计分仍保持 pending/false。

## M0 机制

| mechanism_id | 协作原语 | 主要测量目标 | 当前状态 |
|---|---|---|---|
| `decomposition` | task decomposition | 任务分解、依赖暴露和顺序编排 | M0 authoring registry 已注册 |
| `role_allocation` | capability matched assignment | 能力匹配、责任分配和责任真空 | M0 authoring registry 已注册 |
| `communication_adaptation` | information delivery/recovery | 延迟、丢包、过期信息下的恢复 | M0 authoring registry 已注册 |
| `role_churn_recovery` | join/exit/capability reassignment | 成员变化后的责任转移和恢复 | M0 authoring registry 已注册 |

后续增加 timing/synchronization、resource contention、recovery/replanning 和 multi-bottleneck calibration。每个模板再生成 baseline、单因子 treatment、counterfactual/control、calibration 和非法干预变体。

## 场景状态

| 场景 | 当前业务骨架 | M0 目标 | 诊断轨道 | 说明 |
|---|---|---|---|---|
| Factory | `factory.m0.*`（旧骨架：`legacy_goal_factory_01`） | 四种 M0 机制 | `F-INS`、`F-TRAFFIC`、`F-COV`、`F-VERT` | 楼梯是执行前提，不自动等于协作机制 |
| Warehouse | `warehouse_nvd.m0.*`（旧骨架：`legacy_goal_warehouse_01`） | 四种 M0 机制 | `W-DAG-01`、`warehouse_smoke` | 联合搬运和 dock 争用需单独标注 |
| Modern House | `modern_house_nvd.m0.*`（旧骨架：`legacy_goal_modern_house_01`） | 四种 M0 机制 | `MH-DAG-01`、`house_smoke` | 家具 clearance 和住户动线是约束 |
| City | `city.m0.*`（旧骨架：`legacy_goal_city_01`） | 四种 M0 机制 | `city_service_probe`、`native_probe` | 服务诊断是否等同固定 DAG 需另行决策 |

四个场景必须使用同一字段和审核规则；业务 outcome 数量、机器人池和 witness 节点数可以不同，但不能因此省略某个机制的目标声明。

## 计数与发布规则

- factor level 是条件，不是独立机制；seed 也不增加 canonical template 数。
- 固定 DAG 和 City service probe 是独立诊断轨道，不计入 goal template 数。
- 每个模板必须声明 `measurement_target`、`reference_solution`、`intervention_contract` 和 `calibration_profile`；M0 还绑定三态 `team_necessity_certificate`。
- `validated`/`disproven` 必须有证据引用，当前 authoring 模板统一为 `unknown`，不得把声明级 witness 提升为物理必要性。
- 未执行的 factor interaction、物理必要性和因果归因保持 `external_pending`、`unknown` 或 `non_identifiable`。
- 发布报告至少同时给出 `canonical_template_count`、`condition_count`、`factor_pair_count`、四场景覆盖和诊断轨数量。
- 旧题先标 `legacy_measurement_unclassified`，不能根据已有 witness 自动声称已经覆盖协作机制。

机器可读版本见 [task_coverage_matrix.json](task_coverage_matrix.json)。字段契约由 `measurement_contract.py` 提供；运行调度、Isaac/GPU 和统计泛化由下游工程师负责。
