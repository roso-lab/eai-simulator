# 阶段39：自主入口与评分原始进展数据

基线 `0dc7bfa`；分支 `codex/benchmark-stage39-autonomous-measurement`。
本阶段执行[发布路线图](RELEASE_ROADMAP.zh-CN.md)的R1/R2首批接线，不宣称完成
真实并发、动态条件认证或正式评分。City入口交接另见本阶段City文档。

## 可信侧进展接口

`FormalGoalEpisodeRunner(..., progress_reader=reader)` 显式启用；不启用时不新增进展
归档或参与者接口。reader由可信宿主安装，不能由参与者动作注册，也不会传入参与者。
runner以关键字调用：

```python
reader(episode_id=episode_id, boundary=boundary,
       trusted_report={"session": session_report})
```

返回完整读数：

```python
{
    "episode_id": episode_id,
    "time_s": 12.0,                 # 本episode内的测量时刻，秒
    "clock_source": "native_simulation",  # 或contract_clock
    "evaluator_ref": "trusted.scene_predicates.v1",
    "predicates": {
        "outcome_id": {"satisfied": None, "evidence_refs": []},
        # 必须覆盖public_task.required_outcomes的全部id，不能加减分母
    },
}
```

每个已判断的true/false必须附非空、无重复的证据引用。未测量为None；已满足可以撤销，
不能默认里程碑只增不减。采样点为initial、skill_completed、idle、execution_stopped、
business_evaluated、terminal；宿主时钟和测量时钟各自单调，来源在episode内固定。
业务验真可能继续推进原生场景，测量时刻因此可能晚于最后一次技能回执；两者分开记录，
不把宿主收件时间标成物理完成时间。

采样只读；reader不得推进模拟、派遣动作或修改业务状态。所有边界都有全量谓词向量，
变化id由记录器计算。重复观察不增加完成量；此版本不计算完成数得分、Q(t)、AUP或RMST，
不填尚未冻结的权重/稳定窗口。边界采样不代表获得了技能内部所有物理变化时刻，不能
直接据此声称连续时间积分准确。termination保留真实记录截止点，不虚构运行到预算H。
固定视窗尾部延续与实际成本截止的正式公式仍需后续冻结契约后计算。

## 归档、复算和失败语义

- `goal_progress_evidence.json`：绑定episode/题面hash、原始向量、双时钟、变化、质量错误
  和终止截止点；所有发布标志false。
- `goal_progress_replay.json`：独立重算派生字段并对比完整报告。
- 评分侧车在manifest启用采样或新归档存在时增加`progress_evidence`扩展；重新执行回放，不能相信
  文件内的validated声明。manifest已启用但归档丢失时仍输出missing，不静默省略。
  原始归档索引记录两个文件的字节数和hash。

错误episode、缺失/额外谓词、非布尔测量、无证据判断、时钟倒退/换源均拒绝。
reader故障进入`infrastructure_evidence_failure`及lifecycle错误，不将已成功的技能
回执改成模型失败；归档保持failed/invalid，缺失值不填零。正确求助保留终止原因与
截止点，不调用业务成功判定，也不把求助视为完成任务。

observed至少要求成功的initial和terminal采样且无reader质量错误。技能终态包含
正常完成、物理取消、未启动任务的本地撤回与传输失败；侧车把其采样/失败尝试的时间和
数量与独立skill_receipts归档核对，删去技能边界后重算摘要仍不能通过这项交叉检查。

摘要与重放只证明**内部一致性**，不认证PhysX/传感器。`native_simulation`是已安装
reader声明的时钟来源，不自动抬升现有评分侧车的物理认证位；reader的实体来源、
原生测量覆盖和场景oracle仍须独立验收。该记录器不把会话业务状态或技能ACK自动
转换成物理里程碑，具体场景必须接外部状态reader。

## 验收记录

首轮本机聚焦：进展数据、runner、scoring adapter三文件共73项通过。
覆盖撤销/未知/重复、跨episode、派生字段篡改、读数错误、业务评估推进测量时钟、
求助早停、参与者隔离和旧runner兼容。原生场景数据与双机最终结果在完成后追加；
此处的contract_clock测试不作为GPU/原生通过证据。

独立审查发现并修复取消/传输终态漏采样、terminal-only误标完整、opt-in丢档静默
省略三处问题；加入三种终态及缺档、缺initial、重算摘要删边界回归。修复后进展、
downlink、scoring三个文件共71项通过；与前述73项有重叠，不相加。

二轮复核补齐回执归档丢失必须显式missing，并将终态采样固定在可信post-skill hook
之后（hook抛错也在finally采样）。新增正常/异常hook与三种终态丢档验证。
最终本机八文件 **173 passed**（13.21秒），日志 `tmp/stage39-validation/local-progress-final.log`。
