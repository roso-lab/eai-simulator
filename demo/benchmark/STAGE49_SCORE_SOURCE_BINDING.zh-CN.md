# 阶段49：计划与成本来源绑定

输入基线 `f4089cd`，分支 `codex/benchmark-stage49-score-source-binding`。
本阶段修复评分读取边界：部署证据拒绝外来任务档案后，计划、动作、时钟、激活和通信
成本仍有可能读取同目录的外来文件。来源错误属于平台证据故障，不是参与者决策失败。
不改变评分公式、任务分解自由、冻结题包、见证或执行技能。

## 最小契约

正式 runner 在原始档案和 session report 写完后、成本侧车生成前，写入固定文件
`goal_cost_source_bindings.json`，记录 episode、public task digest、session wrapper hash
以及固定白名单内原始文件的 SHA256。评分消费者只核验，不自动补建或重新封存绑定。
这是同一可信生产边界内的来源与完整性记录，不是外部签名或原生业务认证。

正式成本读取传入 expected episode 和 task digest；session wrapper、已有可信任务
绑定和文件指纹必须一致。直接传入 report 不能绕过该检查。错误源所派生的事实为
null，并报告缺证原因；其他来源有效的事实可保留。真实观测到的零与未观测分开。
共享通信 transport 仍为权威源，不能在它失败后改取工具镜像来补出成本，不能重复计费。

旧调用的无身份、仅 episode 身份读取保留显式 legacy 诊断模式，不声称 task-bound。
旧原生档案缺少冻结绑定时，正式读法不得将今天的文件重新封存成历史证明；原文件不改。
raw cost version 2 与映射 v5 标明新读法；旧归档诊断值不能混作正式来源已认证的数据。

## 验收记录

- 已用真实 runner 复现来源错误：修改激活值为99和替换另一独立合法通信档案后，
  旧读取器仍使用外来数值；外来 session 仍可显示计划 observed。复现为修复前的行为，
  不将新绑定文件尚不存在造成的测试失败另算独立缺陷。
- 工程最终focused检查131通过；主代理真实runner集成与餐厅consumer三模式首轮21通过。
- 联合首轮470通过/1失败：阶段48夹具没有生产新manifest，故正式plan读取按缺证拒绝。
  按真实producer顺序补写manifest后保留原“oracle failed不遮蔽有效plan”的断言。
  首次稳定联合471通过。独审后补未知字段/文件名、bool版本和正式档缺元数据防降级；
  最终29文件476 passed /52.29s，测试与全部benchmark Python输入hash跑前后不变。
  命令 `python3 tmp/stage49-validation/run_joint.py`，清单/log/hash均在该目录。
- Stage46真实原生档案只读兼容：30个文件不变；旧模式的5项成本值保持，正式读法因
  缺历史绑定为null，plan不显示observed。进展、通信分析/容量和oracle registry四子块
  不变，failed oracle继续排除。没有补造历史证明或覆写旧侧车。
- 餐厅consumer按formal双身份复算新runner成本；cost v1保留旧字段对比且明确legacy。
  formal档删掉manifest和顶层source_binding后仍拒绝，不降级读取；未知mode拒绝，
  显式v2 legacy生产模式仍可诊断读取。三项受保护用户文件hash未变。
- 独立终审通过：7文件136 passed /23.31s、diff-check与4个实现文件py_compile通过。
  审查见团队工作区 `stage49-score-source-binding-20260925/handoffs/final-review.md`，
  SHA256 `ce07139b034625d35c851f9bd6252b7bc57551ec5f7b3f606d4f5814033f7eee`。
- 本批不需要 GPU；ljy SSH 返回 `No route to host`，不宣称双机通过。

## 发布边界与后续

保持 `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
来源绑定不认证原生时钟、计算成本、可行性 oracle 或业务完成。R1–R5仍未完成。
独立全局审查建议本批身份切片验收后转回City初始oracle的有界资格验证；声明前成本
截点随后接真实稳定档案，不继续评分硬化代替自主执行接线。写集独立时可并行，
来源绑定不是开展oracle研究的额外技术前置门禁。

正标签需要一个隐藏、完整的合法完成方案：可信初态满足前提，已审定技能转换的
后置条件覆盖全部硬终局，资源与时限相容。不能要求同episode未来业务已经成功后
才准许初始判断。负标签则需支持域完整且排除所有合法替代方案；当前路线匹配和
单条路线提案均不能单独给出整题真值。证书不公开给模型、不约束模型选人或时序。

下一批只核定一个完整候选方案所需的技能保证；若仍缺停止研究的身体包络或其他
基础设施证据，记录具体缺口并结束该候选的资格工作。再比较其他场景现有完整业务
路径和接线成本，选取下一候选，不预先假定某场景已可发布。City业务仍为3/4、
初始oracle failed/null；餐厅完整八机自主入口仍待接通，不再重启无界资产精化。
