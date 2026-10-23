# P4/P5 证据闭合推进记录（2026-10-21）

## 论文与运行边界

本记录绑定 Overleaf Git 最新提交
`75ceff6f55202b6b1a31b2e2e0f6de56023940fd`。所有探针均通过工程内
`tools.project_runtime` 启动，EAI 来源为 `/home/pupil/HeteroCoord/source/`。
本记录不修改论文正文，也不把单场景诊断升级为正式论文实验、认证目录或排名结果。

## 本轮实际推进

重新生成了项目内 acceptance 工件：

`artifacts/p0-p5/acceptance-75ceff6-audit/`

工程运行时 doctor 确认 EAI、EAI_assets、EAI_hmrs 均从项目本地 source 导入。
`benchmark.verify_p0_p5` 退出码为 0，但 `formal_readiness.ready=false`，13 个 blocker
仍完整保留：

```text
asset_digest
split_digest
evaluator_digest
checkpoint_provider_digest
calibration_coordinator_id
capacities_bps
queue_limit_bytes
baseline_manifest_digest
common_start_directory_digest
d5_reference_directory_digest
certified_catalog
qualified_baselines
independent_calibration_uncertainty
```

随后执行了两次新的项目内 Factory Carter 路由探针。两次任务主体都完成：

- 三个有序节点均被实际访问；
- endpoint error 为 `0.11898842755412287 m`；
- final speed 为 `0.0005664952706775568 m/s`；
- `stop_confirmed=true`、`quarantined=false`；
- 本地 source inventory 共 92 项，运行期间未变化；
- 未发现 project 外 USD layer。

对应证据目录：

- `artifacts/project_runtime/navigation_next_20261021/`
- `artifacts/project_runtime/navigation_next_20261021_r2/`

## 发现并修复的门禁问题

原监督器只检查子进程退出码、`status=completed` 和任务自身 `passed=true`，没有要求
`session_close_observed=true`。因此一个任务已经完成但 Kit/原生会话没有完成干净收尾的
receipt 会被错误判为 supervisor 通过。

已修改：

- `tools/validation/run_project_probe.py`
- `benchmark/tests/test_node_route_evidence.py`

现在 supervisor 只有在以下条件全部成立时才返回通过：

1. 子进程退出码为 0；
2. receipt 的任务状态和 measured result 通过；
3. receipt 明确记录 `session_close_observed=true`。

若主体任务完成但收尾未被观察到，监督器返回 `session_close_not_observed`，并保留原始
receipt、native log 和 supervisor receipt，不删除或覆盖失败证据。

## 新鲜运行结论

`navigation_next_20261021_r2` 的任务主体通过，但 `session_close_observed=false`，因此
修复后的 supervisor 退出码为 1，理由为 `session_close_not_observed`。这不是测试假失败，
而是当前真实 native/Kit 收尾证据不足的可复现结果。

这项结果支持：

- 本地 EAI 任务执行与独立 measured evaluator 可以工作；
- supervisor 现在不会把干净关闭缺失误报为成功；
- 当前证据仍只能作为 candidate/diagnostic integration evidence。

它不能支持：

- skill physical qualification；
- strict replay 或完整 native state restore；
- certified catalog case；
- qualified baseline；
- D1–D6 正式论文分数。

## 验收证据

```text
project runtime doctor：exit 0
benchmark.verify_p0_p5（75ceff6 fresh acceptance）：exit 0，formal_ready=false
focused node-route/supervisor tests：12 passed
benchmark 全量：478 passed
navigation_next_20261021：任务通过，但旧门禁误判为 supervisor pass
navigation_next_20261021_r2：任务 receipt 完成，supervisor exit 1，session_close_not_observed
git diff --check：exit 0
```

## 阶段反思

本阶段最重要的纠正是把“任务结果完成”与“原生会话干净结束”分成两个独立状态。
论文和 Tool 协议都要求不能把 exit 0、JSON 已生成或任务方法状态替代 clean shutdown；
本轮新鲜 native 运行正好暴露了该边界。修复后，证据链更严格，但 formal readiness
没有因此减少，因为收尾证据仍不是完整物理资格。

## 下一步最小工作

1. 保留上述失败 receipt，不能重写为成功。
2. 由独立审计确认一个完整任务合同所需的 catalog/oracle/skill/physical/common-start
   字段是否齐全；缺字段必须进入 candidate package 的明确缺口清单。
3. 若要推进 P4，先补齐一个机器可读细粒度合同及独立 checker，再取得可观察的 clean
   shutdown 和 physical evidence；不从现有导航成功轨迹反推认证。
4. 若要推进 P5，再把真实事件注入、sham、paired branches 和留出集恢复绑定到同一
   candidate case，仍保持五项正式资格门为 false，直到完整证据链通过。
