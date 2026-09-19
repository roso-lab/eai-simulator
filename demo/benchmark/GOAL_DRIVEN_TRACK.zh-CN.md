# 目标驱动主轨道

目标驱动主轨道使用 `goal_driven_team_planning_v1`。它和现有的
`fixed_dag_diagnostic` 是两条不同的评价轨道：后者给出作者固定的节点和依赖，
用于协议、感知消费和运行时联调；前者只给出业务结果和公开能力，由被测 agent
自己组成团队、生成计划、改派和恢复。

## 被测模型收到的内容

`python -m demo.benchmark.goal_driven_tasks --output-root <dir>` 会为三个场景分别
生成以下发布文件：

```text
<dir>/<scene>/release/public_task.json
<dir>/<scene>/release/participant_api.json
<dir>/<scene>/release/baseline_manifest.json
<dir>/<scene>/evaluator/hidden_evaluator.json
<dir>/<scene>/authoring/goal_authoring_brief.json
<dir>/<scene>/authoring/review.json
```

`public_task.json` 只公开 `mission`、`required_outcomes`、`candidate_robot_pool`、
`public_world`、`business_constraints`、`budgets`、当前实验因子规则、技能本体版本和
`execution_contract`。题包和计划契约升级为 v2，技能本体为 `eai.skills.v2`；轨道名称不变。
`required_outcomes` 只能表达业务结果，不包含 skill、机器人、目标点、前置关系或执行顺序。
预算同时明确仿真时间、规划时间、通信字节、模型 token 和最大激活成员数。

## 可靠技能与物理约束

导航、固定点抓取和观察在合法前提与执行不变量内保证效果。抓取使用公开的固定点及连接接口；
不要求被测模型生成关节轨迹、识别抓取姿态或承担随机抓取失败。后端对合法调用未实现约定效果
属于基础设施错误，必须与模型的超载、错误成员选择或资源冲突区分。

机器人公开 `physical_profile`（配置身份、尺寸、有效载荷、固定点可达距离、连接接口和台阶高度）；
物体公开质量、尺寸、初始区域、局部坐标固定点、可选搬运模式与每个点的载荷份额。
每个槽位绑定一个不同成员，逐个满足 `质量 × 份额 ≤ 有效载荷`，并匹配接口及可达距离。
`reach_m` 使用物体局部固定点到原点的距离作为理想连接代理，不代表机械臂逆运动学验证。
候选模式允许单机和多人方案，审核根据数值决定可行组合。Carter 的实验承载连接口不等于机械臂。

路线公开起终区域、通行能力、净宽/净高、台阶高度及共享资源容量。
检查机器人、物体与联合队形的外包尺寸，并在净宽和净高两侧各保留公开安全余量。
相反方向的路线可以共享同一资源 ID；资源容量按并发动作计，一次联合搬运占一个动作单位。
路线在整个动作时窗内保守预留；这不是连续轨迹碰撞检测，也不验证转弯半径或速度。
`pick` 建立物体持有关系，携物导航保持其外包尺寸，`place/handoff` 释放；
`cooperative_carry` 是完整的获取、搬运、释放动作。

本轮数值均为 `benchmark_defined` 实验定义，`scene_binding_status=pending`。
它们不是机器人厂商额定值、真实挂载能力或 USD 实测结果。场景团队仍需绑定几何、固定点、
观察可见性及初始位置；运行时团队需验证后端遵循可靠技能契约。自主抓取/VLA 不属于该门槛。

题面不包含作者 witness、参考分配、参考 DAG、reserve 身份、未来干预时刻、隐藏对象绑定或
消费者预绑定。三场景的对象来自场景常识：Factory 使用生产异常标记、服务车和开放主楼梯，
Warehouse 使用货架、木箱、纸箱、集货区和 dock，Modern House 使用扶手椅、书堆、入口和生活区。

## Agent 输出

`participant_api.json` 的 v2 动作词汇是：

```text
select_team
submit_plan
revise_plan
dispatch_skill
cancel_skill
consume_message
```

计划中的 task ID 由 agent 自定义。每个计划任务必须声明 `skill`、`arguments`、
`prerequisites`、`required_capabilities`、`assigned_agents`、`evidence_refs` 和
`execution={start_s,duration_s}`。时间为绝对仿真秒，区间为左闭右开。API 内嵌完整技能参数定义，
导航必须提供路线 ID，抓取与联合搬运必须提供搬运模式及成员—固定点绑定。校验器会检查：

- skill 是否来自本体，参数是否属于该 skill；
- assigned agents 是否是当前公开成员，且满足能力和 coalition 最小人数；
- prerequisites 是否只引用同一份计划中的 task ID，并且无环；
- 对象、区域和楼梯连接是否属于公开世界；
- evidence 是否来自当前已经送达的事件；
- 逐成员载荷、固定点、携物净空和区域/通道资源是否满足约束；
- 依赖完成时间、动作最短时长、成员/物体互斥和物体交接关系；
- `revise_plan` 是否同时绑定可见事件序列、被替换计划和修订原因。

`member_join`、`member_exit` 和 `capability_change` 事件只提供当前发生的成员与能力变化，
不提供 `eligible_agent_ids`、`affected_node_ids` 或未来计划。通信事件也不预绑定某个消费者或
作者节点。

成员加入和能力变化事件携带完整公开声明（含数值能力）与正整数能力版本。
接入层从已发生事件维护受信 `current_agents`、`joined_agents`、`capability_updates`、
`object_states`、`robot_regions`、`current_time_s` 和可见事件；参与者不能通过计划覆盖这些状态。
非零时刻提交计划必须提供完整物体状态及所选机器人位置，不能重新推断出生位置。
重规划以当前物体位置和持有状态审核，不能假定物体自动回到题面初始位置。
已有连接与新抓取分开审核：仅失去 `pick` 不阻止仍具 `place` 的成员安全放下；若当前载荷或连接
条件已不合法，运行时必须先返回可用的安全状态。过期消息可作为历史读取，不能充当当前行动证据。
`dispatch_skill` 当前是协议准入；运行时仍负责核对提交计划账本、依赖完成、实时资源和技能状态。

因子编排通过 `--factor-id communication_quality|team_continuity` 选择单一变化轴；通信轴的
level 是 `immediate/delayed/dropped/bandwidth_limited/stale`，团队连续性轴的 level 是
`stable/member_exit/member_join/capability_change`。两条轴都冻结场景、对象、候选池、预算和
模型预算。隐藏侧为通信投递和团队事件保存具体 treatment；它们在首次跨形态观察之后、首次业务
状态提交之前触发。成员退出和能力变化按确定性规则选择当前已选的关键能力成员，加入事件使用未在
初始候选池公开的身份。participant 只会在事件实际发生后收到当前事件，不会提前看到 treatment。

`validate_goal_factor_pair()` 比较两份完整题包，要求除目标 level 和对应隐藏 treatment 外，场景、
对象、候选池、业务结果、预算、witness、评价条件和非目标 treatment 完全一致。该审核只证明出题
输入的单变量隔离，返回的 `factor_effect_validated` 和 `execution_validated` 始终为 false。

## 作者审核

每个最小题包都附带两个作者私有 witness。它们只用于证明题目存在多种能力匹配的合法计划，
不作为 participant 的答案，也不要求 agent 复现它们。当前审核检查业务结果和道具属于对应场景、
能力与联盟约束能够满足 witness、至少两个 witness 在忽略 `plan_id` 和自定义 task ID 后仍具有
不同的技能、参数、成员或依赖结构，并且每个业务结果都有独立的隐藏谓词和证据类型。

业务 witness 还必须满足场景的符号必要条件：仓库两件货物先集货、再到 dock 并分别取得收据；
家居物体到达允许区域、地面成员撤离后再复核；工厂具有跨视角现场检查和实际返回维护区后的收据。
检查使用动作含义、物体最终状态和因果关系，不依赖作者自定义 task ID。
只有导航和观察、却自称完成全部订单的计划会被拒绝；现场收据真实性与业务评分由其他模块验证。

隐藏评价包还包含声明级异构证书：从 witness 依赖图中提取跨形态依赖并计算固定计划对成员移除
的敏感性；另独立枚举物体搬运组合以及成员移除、能力变化后的局部能力供给。
局部同构方案合法时如实保留，不将固定计划失效解释为全任务必须异构。
`whole_task_necessity_validated`、`physical_necessity_validated` 均保持 false。
计划合法性与作者标注的 outcome 支持只构成出题审核，不证明隐藏业务结果已实际实现。

`goal_authoring_brief.json` 只供出题侧 LLM 使用。它限定场景允许的对象类别、区域类别、能力词汇、
必须覆盖的业务结果主题、可选因子轴、禁止公开字段和准入条件；不包含参考 DAG、参考分配或任何
witness 内容。生成候选仍必须经过与手写题包相同的契约、语义 witness、异构证书和因子隔离审核。
当前 outcome ID 对应已注册的业务语义；新增业务结果需要先补充相应审核规则，不能仅把它绑定到
现有观察节点就视为通过。城市接入也须注册其业务结果检查。

当前发布状态诚实地保持 `authoring_status=passed`、`execution_status=external_pending` 和
`scoreable=false`。无人机避障和地面导航属于运行时的基础执行条件；异构成绩应由业务结果、
信息消费、团队决策、恢复和隐藏证据共同判断。

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
PYTHONPATH=. python -m demo.benchmark.goal_driven_tasks \
  --scene all --factor-level immediate \
  --output-root tmp/goal_driven_packages
```

这个入口只做任务生成和契约审核，不启动 Isaac，也不宣称场景绑定、可靠技能后端、通信效果或
成员生命周期已经完成验证。完整目标题包目前包含工厂、仓库和家居；城市内容由侧边会话维护，
已通过统一 `goal_suite` 注册和准备包接入审核，但完整业务准入仍为 `blocked`。它不具备完整
业务见证和具体因子 treatment，不能视为第四个完整目标题包。
三场景全条件审核、城市产物校验和冻结流程见 [统一审核入口](GOAL_SUITE.zh-CN.md)。
