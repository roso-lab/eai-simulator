# Stage39 City 自主算法入口

本阶段新增 `demo.benchmark.goal_city_autonomous`，作为显式版本化、默认关闭的 City 原生自主入口。调用方必须传入实现 `GoalAlgorithmAdapter` 的外部适配器；入口只向它提供公开 City task、participant API 和 GoalSession 公开事件。

## 边界

- 入口 schema 为 `eai.benchmark.goal_city_autonomous_entry` v1。
- 默认且当前唯一原生配置为 `bound_native_v1`（`CITY-GOAL-NATIVE-01` / `eai.skills.v3`），与 `prepare_probe(pool="v1")` 一致；完整三机候选池为 `go2_1`、`carter_1`、`iris_1`。入口不会依据作者见证或适配器预告缩池。
- 入口不调用 `build_city_episode_plan()`，不构造或传递作者任务分解、任务时序、见证 plan、隐藏 evaluator 包、native context 或 backend evidence。
- 外部适配器必须先处理公开 `deployment_assessment_required`，自行声明 `deployable` 或 `not_deployable`，再自行选队、生成 plan 和派遣技能。
- 公开题面通过 `enable_deployment_assessment()` 升级为 v3；每次自主运行固定启用 `session_audit=True`，保留完整调用日志与独立 replay。
- `CityNativeGoalBackend` 不再依赖预构造 plan。旧位置参数仍可传入但被明确忽略；执行只绑定 GoalSession trusted report 中当前 pending task、action、plan ID 和状态，因此旧 witness/TeamWeaver/EMOS 入口兼容。

自主入口元数据独立归档到 `goal_city_autonomous_entry.json`；runner 返回值与 `result.json` 保持一致。完整 native probe 还会写入 `goal_city_autonomous_source.json`、`city_acceptance_evidence.json`、`native_evidence_log.json` 与下述 `city_progress_sources.json`。City 层每批 sidecar 写完后会重建 scoring metadata 的 archive index，同时保留 runner 已追加的 `episode_result` 等字段。

每个 episode 只读取和验证一次 adapter manifest，之后向 Host 和各归档提供同一快照；参与者的可选工具方法仍由原适配器处理。完整入口在 prepare 前冻结 benchmark Python 源文件与 City service-probe 既有源码/资产哈希，在执行后重新核对，源码漂移使外层报告 blocked；源记录同时保存前后指纹，不能用结束时的新源码冒充开始时的版本。这是运行期文件一致性检查，不是加载字节或外部 adapter 实现的完整物理认证。

自主 context 在接管预热后的 native scene 时冻结 `clock_epoch_s`。Goal 计划窗口、技能回执、业务期限与 progress `time_s` 均使用 episode 相对时间；底层服务 TTL、请求/观测和原始 native evidence 保留绝对时间。Acceptance envelope 只换算明确的时间字段，不改持续时间或空间数据；progress sources 同时保存相对时间、原生时间与 epoch。旧入口默认 epoch=0，维持既有时钟语义。

## 部署 oracle 与进展证据

默认隐藏评估器只记录当前平台缺口：`oracle_status=failed`、`feasibility_label=null`、`platform_valid=false`。它不会把候选池或合成条件伪造成已验证可行性。正式环境可显式传入独立认证的 `assessment_evaluator`。

原生 context 默认安装 `CityAcceptanceProgressReader`。它只读当前 `native_evidence_log` 的内存记录，经 `build_acceptance_evidence()` 和 `evaluate_city_acceptance()` 计算当下快照，不调用 `await_assembly_arrivals()`、不调用 `ctx["step"]`，并以 `ctx["city"].elapsed - clock_epoch_s` 作为 `native_simulation` 时钟。已满足的 outcome 记 true；未满足或证据不足一律记 unknown，不自动写 false。原生 envelope 中尚待 readback 的 null timestamp 属于合法未完成状态；时间字段完整后 evaluator 若抛出异常，reader 会先保存 source snapshot，再把异常交给 runner 归为 `infrastructure_evidence_failure`，不会以 observed unknown 隐藏故障。每个边界的 envelope/review 独立保存在 `city_progress_sources.json`，progress ref 绑定 sample index/outcome，避免后续最终文件覆盖历史。调用方仍可显式传入其他可信宿主 `progress_reader`；入口从不接受参与者提供的 reader。

## API

```python
from demo.benchmark.goal_city_autonomous import run_city_autonomous_episode

report = run_city_autonomous_episode(
    "tmp/city-autonomous-run",
    external_goal_adapter,
    device="cpu",
    assessment_evaluator=trusted_native_feasibility_reader,  # 可选
    progress_reader=trusted_city_progress_reader,             # 可选
)
```

`run_city_autonomous_session()` 是 CPU 合同测试和受控 trusted backend 的正式 runner 边界；`run_city_autonomous_context()` 将准备好的 City service-probe context 接入真实 `CityNativeGoalBackend` 与 City business evaluator；`run_city_autonomous_episode()` 负责完整 prepare/execute native probe 生命周期。

## 验证覆盖

`source/EAI/test/test_benchmark_goal_city_autonomous.py` 复用既有可控 City native context，使用两个没有接收作者 plan 的外部 adapter 路径：两者分别选择 Go2 与 Carter，并为不同真实人员/assembly 生成合法 `guide_people` plan。两者均经过真实 `FormalGoalEpisodeRunner`、`CityNativeGoalBackend`、City business evaluator、部署门禁和 session-audit replay。测试还覆盖：

- 完整三机池未缩减；
- 默认 oracle 只记录未认证平台缺口；
- 默认只读 progress reader 产生逐边界 source snapshot，初始证据不足保持 unknown；
- `not_deployable` 求助零派遣、零业务成功声明；
- 未提交初始门禁声明时 fail closed 并归档协议错误；
- 新入口源码不引用 witness plan builder；
- 后端 plan 参数已变为兼容性可选项。

修正源码漂移、可变 manifest 和 native epoch 后，本机 12 文件联合回归为
`222 passed in 18.25s`（`tmp/stage39-validation/local-city-final.log`；文件集合见
同目录 `test-files.txt`），覆盖 City、progress、runner、scoring、session audit、
部署动态门禁、下行与通信/位姿工具。新增 epoch 专项验证非零起点的 20 秒窗口、
相对失败回执、业务期限换算以及原始 native 日志不变。独立复核与 ljy 结果按后续
阶段收尾证据归档；此处不将本机受控测试等同于原生认证。

## 尚未完成的原生认证

初次实现交接时未运行 Isaac Sim/GPU；随后 `1504a99` 已完成 ljy 局部原生诊断和双机
222 项回归，详情见 [阶段39验收](STAGE39_ACCEPTANCE.zh-CN.md)。该诊断未认证真实模型的
完整自主任务、三机并发执行、动态成员变更、全池控制器能力、真实 native feasibility oracle
或连续物理进展。发布边界仍为 `execution_validated=false`、
`business_outcomes_validated=false`、`scoreable=false`；某次业务 evaluator 的归档事实
不提升该自主入口的发布状态。
