# 阶段52：餐厅八机公共部署与自主会话入口

日期：2026-09-25；输入基线 `d165ce7`。本批验收公共契约、真实初态绑定和参与者计划准入，
尚未连接原生执行事件泵，不构成完整自主 episode 或发布认证。

## 行为与兼容边界

新增显式 `metric_regions_v2` 部署扩展及 `RESTAURANT-GOAL-NATIVE-02`。
原 `RESTAURANT-GOAL-01` SHA-256 保持
`e34fe98668acece2234fda06140f867840f58c272e11216430151ce86eac9084`。
旧接口默认行为不变；参与者必须显式确认新 companion 的任务和版本身份。

保留完整八机候选池、能力、业务目标和预算；新增八个真实出生区域及44条双向连接，
原六条业务路线不变。共同 staging 使用一个维护型几何中心，不能按机器人分别指向出生点。
地面出生区域是0.50m XY圆盘，空中是0.30m球；它们是新初始位置语义，未放宽导航、
抓取或业务到达阈值。出生区对所有兼容成员开放，不是成员专属预分配。
反向连接共享容量，独立连接不强加全局互斥；路线长度仅为几何下界，未认证原生可达性。

部署来源如实使用本地候选资产的冻结产品、元数据与地图hash，不冒称已发布provider版本。
派生题面仅纠正一条过时证据状态文字；冻结原件不变，也不声称完整业务通过。
有效题面hash：`3fc22fe937dc3ca6f8d9f16b99bc8760e12fd59f4749364c7612a951be430826`；
companion hash：`3f269b649f8c5ee88e4f2076cd65177daf9fa34a7046a07619b97977f2492d98`。

`open_restaurant_autonomous_context` 使用真实 GoalAlgorithmHost 与 AuditedGoalSession。
一次性可信初始化将八机区域同时绑定到初始snapshot、部署判断触发和隐藏评估输入，
避免编号已建立而触发状态仍为空的错位；非初始会话、重复调用和已有动作均拒绝。
审计可离线重放。私有根状态和oracle不进入参与者输入，缺oracle仍为平台failed/null。
求助保持终止事件；参与者只能提交deployable或not_deployable。

## 验证证据

- 新部署契约13项通过；含反向连接冲突、独立连接并发及兼容访问语义。
- 入口/部署/audit组合38项通过；两种参与者自拟计划分别通过准入，未注入见证分工/顺序。
- 受影响核心6模块118项通过。
- 工程组合275项、独立审查的Goal及Restaurant组合352项通过；集合重叠，不相加成样本量。
- `py_compile`、`git diff --check`通过，三份受保护用户文件hash不变。
- 对阶段51不可变原生初态作几何解释，8/8落在新区域内。仅CPU解释，不重新签发epoch，
  不改旧任务身份，不冒充本批新增PhysX执行。证据：`tmp/stage52-validation/`。
- ljy仍`No route to host`；无本批远端或GPU验证。

独立审查及交接位于团队工作区
`stage52-restaurant-deployment-entry-20260925/handoffs/`。

## 下一主线

下一单一实现所有者接增量dispatch/poll/cancel、真实观察、共享通信与物理tick事件泵。
物理完成时刻和回执送达时刻分别保留；不得靠同步执行或即时上行绕过延迟和取消。
之后运行公开策略的完整业务episode并独立验证oracle与评分事实。
当前归档明确 `execution_loop_connected=false`；原生完整业务、oracle和R1–R5仍未完成。
继续停止无关精细几何投入，405条件/315配对仍只是条件展开。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
