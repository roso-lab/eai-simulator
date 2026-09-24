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

### 业务证据绑定

`verify_restaurant_business_v2` 接收已回放的任务表、技能回执、v2 readiness、原生
记录、可信机器人/区域绑定和共享通信账本。业务端点 `service_recipient` 必填；
不能用送达无关接收者代替开业端点送达，publication也不能冒充delivery。

`trusted_task_regions_from_replayed_audit(journal, replay)` 从成功回放的每次完成
调用后提取同一完成时刻的可信状态提交，不用最终区域快照替代历史状态。调用者
必须先实际运行独立回放，并使用可信request/native来源；自报的replay结果不是认证。
原生观察、到达、中继和回执绑定实际task/action/agent/receipt，时间必须落在派遣
及任务窗口内。接触传感器路径须属于绑定的机器人prim，观察沿用现有原生支撑谓词。

允许复查、重试、选择非最新但有效观察、早期失败后成功；允许在当前服务区中继，
不要求回出生点；`relay.region_id`保持公开契约中的可选语义，消息标签不要求作者
见证中的固定字符串。多段导航由会话重放检查结构，原生到达记录绑定最后一段；
这不证明每一段的实际物理轨迹。已有接触/支撑谓词主要覆盖Carter，其他本体的
原生接线与对应物理证据仍属于准入缺口，不能据此宣称八机全覆盖。

联合测试真实创建GoalSession、提交不同于作者展开的计划、派遣/完成并独立回放，
再将回放产物送入业务验真。首次失败发现的是测试窗口短于公开技能最小时长；修正
夹具时序与因果依赖后通过，没有修改题包或放宽准入。

覆盖检查本机/ljy各12项通过，提交 `df91015` 已推送。
审计提交 `24d4e0b` 已推送，ljy六文件152项通过。工程聚焦130项、主审四文件52项
与其有重叠，不相加。业务/审计/新旧服务组合76项通过。
所有摘要/回放均证明对应归档的一致性，不独立认证传感器或PhysX事实。
`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。

### 最终双机验收

业务实现提交 `1d8c455`。同一组13个测试文件，本机 **273 passed / 0 skipped**
（27.77秒），ljy **273 passed / 0 skipped**（10.42秒）。前述小集合是同一批的
阶段检查，不能与273相加。最终列表如下，均在 `source/EAI/test/`：

```text
test_benchmark_goal_session_audit.py
test_benchmark_goal_episode_runner.py
test_benchmark_goal_session.py
test_benchmark_goal_scoring_adapter.py
test_benchmark_goal_execution_downlink.py
test_benchmark_goal_deployment_runtime_integration.py
test_benchmark_restaurant_autonomous_admission.py
test_benchmark_goal_restaurant_business_v2.py
test_benchmark_restaurant_service_v2.py
test_restaurant_service_backend.py
test_benchmark_goal_restaurant_episode.py
test_restaurant_algorithm_episode.py
test_restaurant_native_transport.py
```

本机命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
xargs -a tmp/stage37-validation/test-files.txt \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q -rs
```

ljy用git bundle同步至新隔离树
`/home/ljy/eai-simulator-worktrees/benchmark-stage37-autonomous-admission`，测试提交
`1d8c455`；不依赖GitLab凭据，原工作区的 `goal_house_episode.py` 修改保留。
远端使用同一测试列表及 `env_isaaclab` Python，额外显式绑定已有资产：

```bash
EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller
EAI_USD_ROOT=/home/ljy/eai-simulator/usd
RESTAURANT_PRODUCTS_ROOT=/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e/tmp/restaurant_benchmark/products-candidate-v7
```

最终日志在本机 `tmp/stage37-validation/local-final.log` 与 `ljy-final.log`；
这些运行产物不提交。双机重算覆盖报告摘要均为
`fe65c9662c88a537d155b955c49a9225c298d3c727acdf32bac4ac36c8a5e065`，题包和v3公开
任务哈希也一致。`git diff --check`通过，冻结题包/见证/资产及用户文件未纳入提交。
本阶段没有启动GPU/Isaac；RTX5080可用性已核实，但已知full8原生缺口尚未补齐，
不能用一次旧三机诊断冒充其验证。

### 下一阶段验收出口

随后由同一所有者整合自主request、runner、backend和原生归档验真；先以受控后端
验证并发、执行中事件和取消，再在ljy运行对应原生验证。串行后端无法执行合法
并发方案时应报平台限制，不能改成参与者错误，也不能悄悄将其串行化后出分。

先前固定出生点中继、固定message_kind和单段route要求属于旧适配器的实现边界，
不能无声进入新业务成功定义。这是本次复查后实际修正的测量偏差；未来应在搬用旧
验真器之前先列明身份、时序和业务谓词，避免把见证细节当作唯一合法方案。
