# 通用任务运行时交接

## 稳定边界

`task/contracts.py` 和 `task/runner.py` 不导入 Isaac、Torch、ROS 或外部 benchmark
包。调用方负责创建 EAI session、绑定实时 robot roster，并注入 scene provider、
executor、可选 replay 和 evaluator。

`TaskRunner.run()` 是兼容的一次性路径；`run_orchestrated()` 是可选的有界 DAG
路径。后者使用 `OrchestrationLimits`、`OrchestrationState`、显式 replan callback
和 executor 返回的 `ExecutionUpdate`。retryable failure 不会自动重试；planner
必须重新提交 assignment。terminal failure 阻塞其 descendants，但独立 ready
任务仍可继续。batch callback 只聚合 ready set，不允许 runner 并发推进 Isaac。

## 证据边界

`DependencyOutcome.COMPLETED` 只解锁 DAG，不代表物理任务成功。物理 predicate、
milestone 和最终 verdict 始终由外部 evaluator 持有。缺 executor 时，即使传入
evaluator 也固定返回 `N/A / physical_evidence_required`；缺 evaluator 同样 N/A。

runner fail closed 检查 task/robot identity、inactive robot、重复 task assignment、
assigned/unassigned 重叠、声明的 robot capability 和 feedback identity。live scene
中的 object、asset、controller 与 skill resource 是不透明外部依赖，由 executor
解析并以显式失败报告。

## 场景与任务输入

仓库当前不发布 `task/config/` 任务。测试只用 inline generic fixture。任何临时
场景诊断输入只能放到项目忽略的 `tmp/`，不能作为正式 task、测评成功或跨场景
ready 声明。历史 scenario-specific bridge 只是诊断适配器，不是当前通用运行时
发布范围。

## 地面导航 Skill

`algorithm/skills/navigation/ground_navigation.py` 提供人工拓扑节点选路和航向
PID Provider；`task/executors/ground_navigation.py` 在调用者线程读取真实位姿、
发送底盘速度并负责超时、异常和终态停车。导航不依赖全局栅格、局部代价地图、
DWA、OBB 或碰撞查询。接口、节点弯道表达规则和证据边界见
`algorithm/skills/navigation/GROUND_NAVIGATION.zh-CN.md`。

## 最小验证

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m pytest -q task/tests algorithm/common_coordination/tests
```
