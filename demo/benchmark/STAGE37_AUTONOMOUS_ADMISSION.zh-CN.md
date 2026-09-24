# 阶段37：自主计划审计与原生执行准入

基线 `aa68af9`；开发分支 `codex/benchmark-stage37-autonomous-admission`。
本阶段承接阶段36，不改变冻结题包、见证、资产哈希、作业半径或发布标志。

## 范围和反思结论

优先解决会改变被测能力含义的问题：参与者实际拆题能否独立回放、复查和替代
顺序能否通过业务校验、平台缺实现是否会被误算为模型失误。底层技能的精细物理
优化和通用恢复框架不作为这些工作的前置条件。完整自主原生接线仍必须如实补齐，
不能把原有固定 mission 分配器改名后作为新入口。

三条并行工作：

1. `goal_session_audit.py`：可信侧记录实际顶层调用，包括被拒动作、计划修订、
   可信状态和技能回执；独立 GoalSession 重放，不重建作者计划。runner 显式 opt-in。
2. `goal_restaurant_business_v2.py`：v2 readiness 记录绑定实际派遣、回执、原生观察
   和送达开业端点的通信事实；允许重复观察、重试、合法改序。与旧验真器分离。
3. `goal_restaurant_autonomous_admission.py`：从原始完整餐厅题包和现有后端源码
   重算静态覆盖报告；不缩小候选池，不接受参与者自报 coverage。

## 覆盖报告的边界

运行：

```bash
python -m demo.benchmark.goal_restaurant_autonomous_admission \
  --output tmp/stage37-validation/restaurant-autonomous-admission.json
```

当前退出码 `2` 表示预期的 `platform_blocked`，不是模型拒绝或模型执行失败。
报告完整保留8台候选；绑定原始题包哈希、显式启用部署门禁的v3题面哈希、源码哈希。
验真重算本地维护源，不能只修改候选池或覆盖位再自算摘要获得通过。

覆盖矩阵只统计声明技能和公开对应路线，不添加不存在的空中路线，也不凭区域中心
猜出生点。已注册 Carter 路线不等于已经通过物理证明；`observe` 仅记录已实现的
厨房/用餐区检查范围，不宣称所有参数和区域均支持。Iris 原生绑定只有驻留中继。
所有缺实现按平台缺口处理，不能合成为 `member_exit`、`capability_change`。

当前明确缺口包括五台成员无原生绑定、已有 Carter 的厨房↔用餐区直达路线未实现、
Iris 飞行/观察驱动未实现、自主入口与v2通信业务接线、并发事件泵，以及匹配新版
题面的可信剩余可行性 oracle。`oracle_status=failed`、`feasibility_label=null`
只是平台状态，不进入模型部署二元统计。

这是一个默认餐厅条件的必要准入检查，不是81个餐厅变体或405个条件的原生认证。
函数 `require_restaurant_autonomous_coverage()` 供新自主入口在调用参与者前使用；
本批不将它接到旧三机诊断入口，也不宣称尚未实现的新入口已受门禁保护。

## 验收记录与下阶段

### 实际计划审计

`FormalGoalEpisodeRunner(..., session_audit=True)` 显式启用，默认不新增文件或字段。
可信侧输出 `goal_session_audit.json` 与 `goal_session_audit_replay.json`，评分侧车
的原始文件索引收录两者的字节数和哈希。它们不进入参与者事件流。
`AuditedGoalSession` 使用组合代理，只记录顶层调用，避免内部 `emit` 重复记账。
独立 `replay_goal_session_audit(journal, public_task, participant_api)` 逐次比对结果
或异常，并比对最终报告；允许被拒计划、失败执行、合法修订，不要求所有尝试成功。
校验错误仍输出结果和评分侧车，记录为基础设施错误；不会把审计失败变成模型失误。
审计模式不抬升业务验证标志，旧模式兼容行为保留。

覆盖检查本机/ljy各12项通过，提交 `df91015` 已推送。
审计工程聚焦回归130项通过；主审另跑audit/runner/downlink/动态部署四文件52项通过，
这些集合有重叠，不相加。餐厅业务验真仍在收拢；最终双机结果随后追加。
所有摘要/回放均证明对应归档的一致性，不独立认证传感器或PhysX事实。
`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。

随后由同一所有者整合自主request、runner、backend和原生归档验真；先以受控后端
验证并发、执行中事件和取消，再在ljy运行对应原生验证。串行后端无法执行合法
并发方案时应报平台限制，不能改成参与者错误，也不能悄悄将其串行化后出分。
