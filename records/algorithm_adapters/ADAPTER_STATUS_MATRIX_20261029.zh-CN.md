# 六类算法适配状态矩阵

日期：2026-10-29

本矩阵只记录当前隔离工作树中可复核的适配范围。`native decision`、逻辑执行、物理 evaluator 和论文复现不混计。

| 算法 | 原生决策接入 | 当前可验收执行 | P3-J coalition | 主要 `N/A` / `not_evaluable` 原因 |
|---|---|---|---|---|
| FMC-ATA | 通过；完整 native schedule 保留 | 双 UR5 独立任务宿主闭环 | `N/A` | fractional / coalition 不可映射到当前单 owner contract |
| BT capabilities | 通过；auction、tree state、feedback 分开保留 | 有界双 UR5 / DAG 条件 | `N/A` | 未注册 coalition；BT `SUCCEEDED` 不等于物理 milestone |
| SADCHER | 通过；官方完整 checkpoint、idle、DAG、coalition 原样保留 | 单 owner 条件；coalition 仅完成 request/evidence seam | `N/A` | 当前仍缺真实 joint runtime + 完整 evaluator terminal receipt |
| HARL:HAPPO | 通过；actor/checkpoint/action/recurrent contract 审计 | `N/A` | `N/A` | 当前 checkpoint 是 self-trained macro smoke，不是官方同构 checkpoint；UR5 contract 不匹配 |
| TeamWeaver | 通过；原 HRCS/MIQP/LLM parser 保留，兼容修复仅语法 | 理想逻辑宿主 | `N/A` | 多轮持久语义和同任务 coalition 尚未完成；Gurobi 为可选环境；native adapter 57 项独立复跑通过 |
| EMOS | 通过；原 discussion/parser/response hash 保留 | 有界 follow-proxy 条件 | `N/A` | Habitat-MAS/CrabAgent stage two、原生低层动作和论文 success detector 未迁移 |

## 源码和版本收据

权威基线为 `records/algorithm_adapters/baseline_20261029/BASELINE_MANIFEST.json`，其中记录六个 source commit、adapter hash、checkpoint hash 和 paper hash。当前改动尚未更新远端版本，也未提交；因此本矩阵不能被解释为已发布版本。

## 回归收据

- FMC-ATA：66 passed，2 xfailed。
- BT capabilities：61 passed，7 skipped。
- HARL:HAPPO：69 passed。
- EMOS：98 passed，使用 Python 3.10 隔离环境。
- TeamWeaver：234 passed，2 skipped；跳过项仅为缺少可选 `gurobipy` 的 MIQP 路径。
- coalition/shared 当前回归：38 passed，1 skipped。

## 未完成边界

1. SADCHER coalition 尚未注册正式 P3-J 条件；当前只完成无损转换、request、资源 lease、session receipt 和独立 holder/time evidence 校验。
2. FMC-ATA 动态到达、通信延迟/丢包和 fractional workload 的正式冻结 fixture 尚未纳入统一比较。
3. BT 的完整 MissionControl、复杂 DAG coalition 和 physical milestone receipt 尚未形成共同条件。
4. HARL 缺少论文同构官方 checkpoint/environment contract，不能报告论文性能或映射为 UR5 抓放。
5. TeamWeaver 的完整多轮语义持久状态、历史重建和同任务 coalition 尚未验收。
6. EMOS 的 Habitat-MAS/CrabAgent stage two、原生低层动作和同构 success detector 尚未迁移。

只有共同条件、共同 evaluator、完整 horizon 和独立 receipt 同时存在时，结果才进入跨算法比较；其他项目保留 `N/A` 或 `not_evaluable`，不填零。
