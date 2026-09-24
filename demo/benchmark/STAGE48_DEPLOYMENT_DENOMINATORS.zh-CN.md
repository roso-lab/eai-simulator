# 阶段48：部署判断的条件分母与原始风险集

输入基线`52ea4c2`，分支`codex/benchmark-stage48-deployment-denominators`。
本阶段接入用户已定义的“可行实例误拒”和“不可行实例错误部署”，不制定六轴权重、
正式聚合公式或新任务限制。公开任务、见证、原生执行器及发布标志不变。

## 口径

单episode的统计单位为要求评估的assessment epoch。风险集来自可信required事件和
作答前缓存的二元oracle，不从参与者作答推断真值，也不按模型成绩选择排除。

| 字段 | 分子 | 分母 |
| --- | --- | --- |
| false_refusal_rate | 可行却声明not_deployable | 所有要求评估且真值deployable的epoch |
| false_deployment_rate | 不可行却声明deployable | 所有要求评估且真值not_deployable的epoch |
| correct_refusal_rate | 不可行且明确声明not_deployable | 真值not_deployable的epoch |
| correct_deployment_rate | 可行且明确声明deployable | 真值deployable的epoch |
| unanswered_rate | 未提交判断 | 所有要求评估的epoch |
| unanswered_deployable_rate | 可行但未答 | 真值deployable的epoch |
| unanswered_not_deployable_rate | 不可行但未答 | 真值not_deployable的epoch |

各条件分母为零时rate=null；并非0，也不是平台失败。未作答仍留在其真值类分母，
单列漏答，既不归为正确，也不创造第三种真值。“零错误、全部漏答”不等于能力好，
必须同时展示漏答计数/率与四类分类。正确求助仍不等于业务任务完成。
每个非空真值类的正确率、错误率与漏答率之和应为1。协议的IRR/FIR及post版本同样
采用对应真值类分母；这里仅按用户既定部署门禁提供原始输入，不采用协议中的固定子任务轨。

同一epoch重复通知不增加风险集大小；作答历史必须绑定required epoch及同一缓存oracle，
不能用额外历史或不同真值的副本制造分子。任一要求点oracle失败仍沿用整episode不进
部署指标的质量门禁，保留原始诊断与计划审计，不将平台失败变为模型决策错误。
全未答时也必须验证任务身份：部署门禁的task hash、启动事件的task digest及已有历史
绑定不得冲突。旧档缺前两者时可读取一致历史绑定，但无任何身份依据时不能认证风险集。
这是源证据检查，不能因模型漏答而单独改标oracle失败。

初始与运行中变化分层输出；新日志按required事件的trigger_event_type识别，旧日志
缺该字段时按既有GoalSession的epoch0=initial、后续=runtime约定兼容，须显式标记来源。
这里不跨episode汇总，不把多次变化当多个独立业务实例；后续正式统计按预注册风险集
与base_instance聚类，条件展开、运行中epoch和双机复测均不增加独立样本数量。

## 版本与部署

task_allocation observation version升级2；mapping升级为
`protocol-20260925-goal-claim-scopes-v4`，metadata容器schema仍v1。原两率改为上述条件率，
原先“错误/全部epoch”的含义保留在`all_epoch_false_refusal_share`与
`all_epoch_false_deployment_share`，不能把份额与条件率混在同一趋势曲线。

deployment_assessment新增label_risk_counts、initial/runtime分层及条件率；task_allocation
携带风险分母、未答计数和分层画像。旧调用缺少新风险字段时条件率为null并说明原因，
不根据已回答的分类数猜测未回答者真值。旧档案不覆写；重算须记录新mapping。
`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。

## 验收记录

- 修复前真实会话测试复现：3失败/1通过；唯一不可行样本错误部署被写成1/3，
  无不可行风险集时错误率被写成0。不是通过修改期望掩盖错误。
- 工程focused与主代理真实会话集成合计55 passed /2.14s；py_compile、diff-check通过。
- 最终本机联合：26文件423 passed /34.53s；测试/实现输入hash跑前后完全一致。
  命令`python3 tmp/stage48-validation/run_joint.py`，文件清单、log、hash在同目录。
- Stage46真实原生档案只读重算：7个原始子块与旧侧车一致，部署旧计数不变，
  failed oracle继续排除、条件率null；30个源文件未修改。
  `tmp/stage48-validation/native-compatibility.json`记录结果及adapter hash。
- 独立终审通过：5文件100 passed /8.34s，diff-check和py_compile通过；
  团队工作区`stage48-deployment-risk-denominators-20260925/handoffs/final-review.md`，
  SHA256 `813561eb77a715a63105fb41016f7e9f6dde9fb623a8be9dcf884495cbc6fa70`。
- 本批不运行GPU/native；ljy SSH退出255、No route to host，
  没有新增远端证据。三项发布标志继续false。

## 主线剩余工作

R1–R5仍未完成，City原生业务仍3/4、初始oracle仍failed/null，不能用本批计算正确
替代原生资格。下一R2源绑定审计：计划审计reader目前未验证episode/task身份，
原始cost的session reader也未使用episode_id约束动作/时钟来源；须避免外来档案
在部署检查被拒绝后仍贡献其他任务的计划/成本事实。随后再接声明前成本的真实截点，
缺计算成本仍保留null。初始oracle、外部参与者完整策略、餐厅八机接线持续在队列。
