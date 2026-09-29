# 第二任务整改完成报告（2026-09-29）

分支 `feature/restaurant-service-order-delivery`，整改区间 928db15 → 4f7b296（12 个提交）。
针对发起人裁定（09-29 10:58 三条硬性要求 + 11:30 两条追加）对 928db15 "架空被测算法分配权"的整改验收记录。评分门全部保持 false，未进入 9×9/405 网格，GOAL-01 冻结哈希零耦合。

## 一、四项裁定与验证结果

### 裁定 1：技能图只定义任务，不定义队伍

任务定义不得预置已满足区域连续性的 agents；候选池=公开机器人池；队伍选择与任务分配完全由被测算法输出。

**落地**：measured 回合把任务骨架（队伍剥离的 structure_plan，仅保留 prerequisites→区域供挂接前航向提示）交给后端；EMOS 适配器在公开池上按槽位开真讨论，`carrier_bindings` 按算法输出重绑 `agent_id`；后端不接收任何种子队伍。

**验证**：五任务真 LLM 回合中 EMOS 反复给出与 witness 不同的组合（餐厅订单观察给 carter/m20 而非 pepper、回执 relay 给 m20、order_b 给无人机、两轮 factory 分配各异）——分配权实测在算法侧。

### 裁定 2：EMOS 必须真分配；不可用如实记录并中止；失败是测量结果

**落地**：EMOS 引擎逐结果标记产生路径（`last_result_source` ∈ llm/policy/fallback/local_fallback），适配器只接受 llm 来源的分配。LLM 响应但分配不可解析→`allocation_invalid`（算法测量失败，启发式回退被丢弃不执行）；调用未应答→`unavailable` 并中止；漏派/错派记录为测量结果，全程无 raise。回执归档双来源字段：`discussion_source`（讨论边界：llm/policy/unavailable）与 `allocation_source`（最终分配：real_llm/deterministic_fallback/unavailable）。

**验证**（`tmp/second_mission_measured/*-emos-llm2/`，5/5）：

| 任务 | allocation | discussion | error | 回执 |
| --- | --- | --- | --- | --- |
| RESTAURANT-SERVICE-01 | real_llm | llm | None | 12 回执 11 成（carter 完整 A 餐链+返厨；B 餐 pick 34.97m 物理回执） |
| CITY-EXIT-01 | real_llm | llm | None | 派无人机做地面观察→constraint_violation:region_access 回执 |
| MODERN_HOUSE-DELIVERY-01 | real_llm | llm | None | 换 m20_2 取放三级成；place 7.29m 物理回执 |
| WAREHOUSE-INBOUND-01 | real_llm | llm | None | 派无人机做地面观察→constraint_violation:region_access 回执 |
| FACTORY-FIRE-01 | real_llm | llm | None | LLM 派 m20_1 去按钮 3.95m 未达物理回执 |

（第一轮 `*-emos-llm/` 同形态 5/5 证据保留；LLM 业务成功不是整改验收条件——viewer 裁定。）

### 裁定 3：准入门只验结构，错配下沉为执行层约束违规

**落地**：`validate_goal_plan/validate_goal_action` 新增 `admission="interface"`——只验 schema 良构、robot/skill 引用存在、任务自身目录词汇、动作在六动作词汇表；能力/机动/区域/团队预算/最少人数移出准入，在 dispatch 执行层落 `constraint_violation:capability/mobility/region_access/agent_count` 单任务失败回执（任务组织维度的测量点）。结构网补齐：承载模式存在、槽数匹配、绑定集合==assigned_agents、绑定点==模式槽（两模式同控，堵死"派 A 执行 B"）；审计账本记录并按 `plan_admission` 回放（旧档案兼容 full）。

**验证**：错配计划过 interface 准入、执行层落违规回执（city/house/warehouse 实测复现）；结构坏计划（未知 robot/skill、目录外 skill、绑定错人）两模式均拒；full 模式零回归（既有 GOAL-01 全套件绿）。

### 裁定 4：技能 agent 参数化 + 多解可执行验收

**落地**：技能后端审计零实例 ID 绑定（机型查表导航、承载只看约束、Pepper 记录类任意实例、socket 角色与派单顺序解耦）；修复基线引入的预设集结路线机型记账 NameError 回退。14 个合法分配入 `second_mission_allocation_matrix.py`（interface 准入 + 执行层合同双检全过、语义签名互异、池容量缺口如实上报）；Isaac 实测入口 `second_mission_episode --allocation <id>`（交接计划自动串行窗口归一）。

**验证**（`tmp/second_mission_matrix/`，7 发有效）：

| 任务 | 替代分配 | 结果 |
| --- | --- | --- |
| FACTORY-FIRE-01 | 换承载者 m20_2 | **9/9 全链**（吸附-搬运-放置-会合） |
| MODERN_HOUSE-DELIVERY-01 | 换承载者 m20_2 | **9/9 全链**（取放+签收+双撤收） |
| CITY-EXIT-01 | 换引导者 carter | **7/7 全链**（guide_people+人数核销） |
| WAREHOUSE-INBOUND-01 | 换纸箱承载者 | **14/14 全链**（木箱 cooperative_carry+纸箱移交+撤收+闭区） |
| WAREHOUSE-INBOUND-01 | 承载角色对调 | 5/6（接近差 1.04m 物理容差） |
| RESTAURANT-SERVICE-01 | 分摊送餐 | carter 全链成；m20 体型阻挡厨房通道 |
| RESTAURANT-SERVICE-01 | 交换送餐者（构造行） | pepper 全链成；m20 同通道体型阻挡 |

**全部失败均为物理差异（体型膨胀半径/接近容差/到点失败），零 harness 拒绝**——"合法选择之间的差异只能来自物理优劣"实测成立。

## 二、验证清单

- 离线测试：247 passed（新增准入 6、参数化 7、矩阵 11、真 LLM 来源三态 3；含 full 模式零回归与 handoff preflight 45 包）
- viewer 审校：总判定"需调整"的 6 项修正全部落地（含 B 漏做的绑定结构校验由主线程补齐）
- 真 LLM 完整回合：5/5 双来源归档（alloc=real_llm + discussion=llm + error=None）
- 多解 Isaac 抽测：7 发，4 例全链可执行 + 3 例物理差异回执
- 提交推送：928db15→4f7b296 共 12 提交，`git ls-remote` 核实远端一致

## 三、已知限制（如实申报）

1. 部署初态朝向仍按 witness 计划构建——各替代分配共享同一冻结初态（可比性好），考场初态语义上偏 witness；
2. city solo 变体过合同双检、仅可在 measured（interface）路径执行（witness 全准入的静态区域连续性检查拒绝其单机排布）；
3. 餐厅池仅 1 台 pepper，"换记单者"轴不可实现（池容量缺口，已入 `POOL_CAPACITY_NOTES`）；
4. 真 LLM 分配"先成功一轮、后续轮失败"目前记为 real_llm+漏派测量，无独立终态枚举。

## 四、解锁条件

本报告与全部证据随分支合入主线后，第二任务采集（Q7，45 基础模板×9 条件档）解锁。
