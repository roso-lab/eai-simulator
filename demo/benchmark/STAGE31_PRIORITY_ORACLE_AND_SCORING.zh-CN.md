# 阶段 31：P0 门禁优先级与评分接入审计

日期：2026-09-24  
基线：`7652a24`（`benchmark-task` 远端同提交）

本阶段先处理“当前团队是否存在合法完成路径”这条地基，再扩大正式评分和物理验证。题面仍只给总体目标与可验证里程碑；被测模型自行拆题、选人、排时序。本文是工程交接和验收清单，不是新题包。

## 已确认的边界

- 正式可行性真值只有 `deployable` 与 `not_deployable`。
- `unverified` 只能表示平台证据失败；应归档为 `oracle_status=failed`、`feasibility_label=null`，不能进入模型或统计分母。
- 剩余任务判断是从可信当前状态出发是否存在任一合法完成路径，不能按作者 witness 是否还包含某能力来代替。
- `not_deployable` 求助是有效终止，但不等于可行任务完成；可行样本误拒仍须计入诊断。
- `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false` 在本阶段全程保持。

## 分阶段任务

| 优先级 | 工作包 | 状态 | 出口证据 |
|---|---|---|---|
| P0-A | GoalSession/runner 部署门禁与二元 oracle | 已接入，需收尾 | 初始、成员退出、能力变化、里程碑点均先测量；求助终止且不补资源 |
| P0-B | 五场景 native remaining-task reader 与替代路径证书 | 阻塞 | Factory/Warehouse/House/City/Restaurant 每个预注册点均有可信状态、实体版本、证据引用；否则平台失败 |
| P0-C | 资产准入 | 餐厅 ready；Factory 阻塞 | 冻结 resolver/hash 与真实启动前检查通过；不替换资产、不放宽校验 |
| P1-A | 评分侧车准入一致性 | 进行中 | 只有完整 verified epoch 进入 IRR/FIR 原始计数；计划拒绝仍按模型自提交事实归档 |
| P1-B | checkpoint/sham/RNG | 设计已有，原生回放待做 | 变化前快照、真实 restore、sham 分支隔离、编键随机对拍 |
| P1-C | 三层通信正式 episode 接线 | 离线契约已有 | backhaul/p2p/access 三池、AoI、JP/IID/BP、C90/C50 包围区间与 bootstrap CI |
| P2 | p2p participant service 与云—边—端多端点适配器 | 设计待拍板 | 先冻结接口文档，再实现 publish/consume；不静默升级旧参与者 |

## 并行和串行安排

1. P1-A 可与 P0-B 的只读盘点并行；评分适配器不得把未验证 reader 的结果当成零错误。
2. P0-B 的正式动态回归必须等待对应场景 reader 和替代路径证书；作者 fixture 或 witness 不能充当 native 证据。
3. P1-B、P1-C 只在门禁契约稳定后接入正式 episode；它们的失败分别归入回放/通信基础设施类别。
4. P2 必须先产出云—边—端接口设计文档，再写 participant service；不得与未冻结接口同时改 Goal action contract。

## 当前阻塞与测试策略

- 本机完整轻量回归为 `2973 passed, 1 failed`，唯一失败是 Factory 冻结 USD SHA256 不匹配；ljy 隔离树为 `2812 passed, 9 skipped, 2 failed`，失败同属 Factory 资产缺口。
- 资产缺口不得通过修改 fixture、overlay、题包或 hash 消除；应由资产维护方提供匹配冻结版本后重跑。
- 每个阶段先写交接文档，再提交源码；推送前核对主线、远端和运行中代理的未推提交，禁止 force push。

## 阶段出口

本阶段只有在评分侧车 malformed/inconsistent oracle 回归通过、五场景 reader 缺口和 Factory 资产阻塞被明确记录后才可进入下一阶段。任何基础设施失败都单独报告，不冒充模型决策失败。
