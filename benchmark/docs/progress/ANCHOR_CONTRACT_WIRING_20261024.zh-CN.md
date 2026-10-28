# 24 锚点合同接线阶段记录（2026-10-24）

## 结论

语义权威采用 `benchmark/docs/anchor_drafts/` 与
`BINDING_DECISION_SHEET_20261024.zh-CN.md` 的“全按推荐”拍板结果。合同投影已经生成 24 个新锚点、104 条谓词；六个既有试点仍为 19 条谓词，逐项语义字段比较无漂移。所有合同谓词保持 `bound_pending_replay`，不授予认证；formal gates 与 certification flags 继续为 false。

独立深审曾纠正最初的“104 条均可执行”误判：当时 39/104 缺少 judge，另有跨锚点复用造成的 zone/window/object 漂移。该缺口现已关闭：`DRAFT_BINDING_REGISTRY[(anchor_id, predicate_id)]` 精确覆盖 104/104，每条均可由有效输入到达 SATISFIED/VIOLATED，缺事件或不完整 evaluator receipt 则 fail-closed。生成物中的 104 条 candidate 均为 `executable`，仅 `identity_mapping.scenario_entity_ids` 继续等待语义地图；`bound_pending_replay` 仍不等于认证完成。

## 已接入

- `deadline`：终局里程碑 tick ≤ 登记 horizon，恰好截止拍通过，超一拍违反；缺失终局里程碑按 evaluator-owned fail-closed cause code 处理，并标注为任务成功条件而非违约扣分。
- `identity_consistency`：支持 `equal`、`member` 与 `set_equal`，缺身份、缺集合或集合类型不合法均 fail-closed；`liv-p2-02` 同时校验药品包身份与接收人授权集合，`liv-p2-03` 校验派单批次集合及登记角色映射。
- 24 锚点分类计数：precedence 43、non_overlapping 24、deadline 21、may_overlap 7、continuous_coverage 3、within_window 2、contact_count 2、identity_consistency 2；无 unresolved operator。
- 安全类保持五处：`prod-t-01`、`prod-t-03`、`svc-p3-01`、`liv-p3-01`、`liv-t-03`。24-anchor artifact 中四条新安全谓词路由为 `task_unsuccessful / Y_omega / D2,D6`；安全失败不打断实验。其余 72 条义务谓词路由 D3，21 条 deadline 路由任务未完成，7 条 overlap 仅诊断；全部 `experiment_interrupt=false`。
- 所有 tick 与 interval 边界要求精确非负整数（显式拒绝 `bool`、浮点和非法区间）。对 evaluator-private ledger 采用单调完整性规则：正见证可直接判定；补回漏记可能反转的 absence-dependent 结论必须返回 `evaluator_record_missing`，不得误打 D3 或 `Y_omega`。
- 生成器与 artifact 保留 `cf839ec8` / `0b6890c0` oracle 来源链，身份映射的场景实体 ID 留 pending。

## 验收证据

- `tools/benchmark_branch/tests/test_oracle_predicates.py` + `test_pilot_contract.py`：218 passed, 4 skipped。
- `tools/benchmark_branch/tests` 全量：856 passed, 5 skipped。
- 合同/目录交叉专项：214 passed, 4 skipped。
- `compileall`、scoped `git diff --check`、合同重生成与 validator：通过；新鲜重生成与登记 artifact 字节级一致。
- 生成 artifact：`artifacts/catalog/contracts/24-anchor-drafts-20261024/pilot_contracts.json`。
- 同源不双计已落轨：draft 谓词登记 `source_ledger`、`dedupe_group`、`dedupe_key` 和 `counting_policy`；dock/scan 多视角共享 evaluator-private occupancy source，validator 拒绝元数据篡改，oracle reducer 保留该组最坏判定并记录被折叠 predicate IDs。六试点自身的 19 条语义保持不变。
- 冻结指纹：oracle `48161a8143db905e72ad3963c4f7701e6d204052417e91b65f7cc8d2ce95a346`；oracle tests `68de61e9adfb6a3570e53a5e47794ef09c60e0c0be965b33bacabd017505bbfe`；artifact `8f3ba3b967a3cb7ebe8762261f355d20360dc2ae08e5271a6ea6401a0a6d2a6e`。
- 独立 Sol 审查最终 ACCEPT，开放 `P0/P1/P2 = 0/0/0`。团队交接：[anchor_oracle_contracts.md](/home/pupil/Documents/Codex/agent-team/workspaces/anchor-contract-wiring-20261024/handoffs/anchor_oracle_contracts.md)；独立审查：[anchor_contract_review.md](/home/pupil/Documents/Codex/agent-team/workspaces/anchor-contract-wiring-20261024/handoffs/anchor_contract_review.md)。
- ZCode GLM-5.3/max 最终只读复验：四项冻结 SHA 一致；定向 `218 passed, 4 skipped`；`tools/benchmark_branch/tests` 全量 `856 passed, 5 skipped`；artifact 为 24 anchors / 104 predicates，认证数 0，104 条 `scenario_entity_ids` 均为空且 pending；四任务文件 scoped `git diff --check` exit 0。复验未修改源码、测试、artifact 或文档，未触碰 GPU/Isaac。

## 未关闭项

`env_isaaclab` 下 `benchmark/tests` 为 505 passed、22 failed；22 项均集中于既有 `test_catalog_mapping_v3.py` 的 frozen fixture 仍绑定旧论文 commit。本阶段未越权改写旧 fixture，故不能宣称“全仓回归全绿”；论文身份迁移线需单独关闭。16 处场景/语义地图缺口仍转交语义地图线。GPU 三路运行本阶段未启动。
