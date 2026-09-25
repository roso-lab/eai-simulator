# 阶段 65：协议批次 0/1/2A 落地

日期：2026-09-25  
基线：`2a1b1d1` → `cd97171`  
依据：[STAGE64_PROTOCOL_CALIBRATION](STAGE64_PROTOCOL_CALIBRATION.zh-CN.md) 批次计划 + viewer 合同 v4 预审（tmp/contract_v4_design_precheck_viewer.md）  
约束：branch 工具全部功能（含快照恢复+sham）按用户指示搁置——本阶段所有产出均不依赖恢复/分支能力。

## 批次 1：协议进展曲线（8ce704d + a0d4918）

- `goal_progress_curve.py`：Q(t)/MCR/AUP/PGI/nPGI 离线计算器。等权 v1 冻结策略
  （`protocol-v1-equal`，协议对必需里程碑等权原则的推广，**待用户追认**）；
  最早满足去重（anti_double_count）、hold-last 到 H、精确阶梯积分、
  零执行窗 nPGI 显式 N/A、零进展 unit 量标记 `undefined_zero_progress`。
- 12 测试；真实餐厅归档：Q(H)=1.0、AUP=0.4832、nPGI=0.6596（t_exec=188.02）。
- `goal_independent_recomputation.py` 集成：给定 kind→milestone 映射时，
  进展事实族在同一归档上直接产出曲线；无映射显式不含曲线（不是零进展）。

## 批次 0：oracle 词汇对齐 + 部署五率（428fbb7）

- `goal_protocol_deployment.py`：真值词 `team_state_infeasible`（读取层双读旧档
  `not_deployable`，新旧档案分类与五率逐字段相等有测试证明）；协议五率
  （detection/false_non_deployable/false_deployable/pre_dispatch_safe_stop/
  assessment_latency 右删失），每率含分子/分母/Wilson CI 界；
  约束失败四标签——`no_committed_plan`/`required_milestone_unmet` 可判定，
  cap/role 显式 `not_applicable_until_contract_v4` 占位。
- 侧车新增 `protocol_deployment` 节（revision v7）；13 新测试 +
  全部 goal 套件 2169 passed；ljy 聚焦 92 passed。

## 批次 2A：合同 v4 schema + 显式迁移器（cd97171）

- `goal_contract_v4.py`（26 测试）：v3/v4 分离（互读 fail-closed）；
  `contract_milestones` 与 `required_outcomes` 一一对应并存；精确字段集 +
  aggregation 四值枚举 + 权重校验 + canonical 封印。
- **迁移器零默认语义**（比 viewer 预审更严）：weight/activation/stability/
  monotonicity/anti_double_count 五字段必须逐 outcome 作者显式提供，
  缺失即失败并列清单——`provisional_fields` 恒空，防止 evidence_expectations
  冒充里程碑语义污染 Q(t) 分母与 CS 判定。
- Q(t) 接口端到端（等权/显式权两例）；CS 门三态
  （not_applicable_until_contract_v4 / no_committed_plan / deferred_to_2B）。

## 双机验证

ljy（bundle 流 + LFS skip）：批次 0/1 聚焦 92 passed。

## 下一步（批次 2B，依赖清单已由 2A 交接）

1. committed_plan 证据提取器（从 GoalSession 计划/派发日志派生，不扩冻结面）；
2. CS 三维判定器（cap/role/mile 读 v4 合同+计划+外部里程碑证据）；
3. `predicate_ref` 绑定 trusted evaluator 注册表；
4. 非 episode-start 激活规则的外生求值缺口。
