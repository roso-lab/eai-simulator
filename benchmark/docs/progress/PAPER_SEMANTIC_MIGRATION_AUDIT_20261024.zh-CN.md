# 最新论文语义迁移审计（2026-10-24）

身份常量和合同 artifact 已切换到 `0b006d4…`，但目录语义不能通过改字符串完成。对当前 `benchmark/task_catalog.json` 的机检结果如下：

- 当前目录：30 个 anchor、9 个 common-anchor group；
- 最新论文要求：12 个 domain–primitive common cells、六个 source→target 方向；
- 已覆盖：production/P1、P2、P4；service/P1、P2、P3；living_public/P2、P3、P4；
- 缺少：production/P3、service/P4、living_public/P1。

因此当前状态是 `semantic_migration_pending`。缺失的 3 个 cell 需要语义地图线和出题人底稿提供真实 common-anchor 绑定，不能由工程自动复制或猜测。机器收据见 [PAPER_SEMANTIC_MIGRATION_AUDIT_20261024.json](/home/pupil/HeteroCoord/artifacts/protocol/PAPER_SEMANTIC_MIGRATION_AUDIT_20261024.json)。

进一步回溯权威论文历史后，确认三项高层语义来自提交
`04f354ccca801e3cb986aa23999e9b3f72b554b2`：production/P3 是长件或重件协同搬运
（P3-J）与持续产线供给（P3-S），service/P4 是电梯、充电站等共享设施的排他使用，
living/public P1 是并行布置或搜索。该提交同时把 D5 定义为 P1--P4 × 六个有向
source→target 方向的 24 个 panel。上述内容足以冻结 cell intent 和 D5 目录形状，
但论文树没有给出可执行 pair registry 所需的 common-anchor ID、逐域成员、义务图 ID、
P3 子型签名、能力对应和 certificate ID；因此它不能单独把现有 9 组目录提升为 12 组，
也不能把 `semantic_migration_pending` 改成已验证。

在这 3 个 cell、D5 六方向目录和对应 audit 条款完成前，三路 GPU 运行器保持拒绝真实执行；离线探针可以继续作为运行器结构验收，但不升级为物理/论文资格证据。
