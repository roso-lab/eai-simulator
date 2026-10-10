# benchmark_branch 工具审查与下一版开发交接

本文是对提交 1cc505f81d6df6b0c62a5472230c32736e59b63b 合入后的审查结论。
它给 tool 开发者说明：哪些功能已经接受、哪些职责不能越界、下一版还需要补什么。

当前文档对应 `HeteroCoord` 正式分支的兼容提交 `01296b5d`。本次已完成的是
collector 身份兼容和 fail-closed 边界；真实 Isaac session、GPU preflight 自动接线
和 shared-session 认证仍属于后续 tool 开发任务。

## 1. 这次合入已经接受的能力

tools/benchmark_branch/ 作为实验采集基础设施已经保留并接入主分支，原因是它解决了原有轻量 runner 没有覆盖的工程问题：

- 每个 branch 可以进入独立 fresh process，避免 Python/控制器/随机数状态意外串联；
- parent collector 单写者 + bounded queue，worker 不直接并发写公共账本；
- branch_manifest / checkpoints / steps / events / episodes / network / costs / resources / metrics 等 append-only JSONL；
- worker PID、session、heartbeat、first/last tick、异常和恢复尝试都有原始记录；
- checksum、corruption detection、resume/retry、plan/run/validate/summarize/export CLI；
- shared-session/vectorized backend 可作为诊断和后续 GPU backend 的接口原型；
- branch-local ledger mirror 便于单 slot 审计。

这些能力属于 tool/runtime 层，不替代论文 benchmark 的身份、因果 replay 或指标归约。

## 2. benchmark 与 tool 的唯一职责边界

论文语义的权威实现仍在：

- benchmark.protocol.RunKey / BranchSpec：branch 身份、角色、treatment、authority；
- benchmark.batch.ExperimentBatch：cell、checkpoint、manifest digest、严格 paired 要求；
- benchmark.replay.PairedBranchCoordinator：causal replay fidelity、sham calibration、strict pair gate；
- benchmark.artifacts.BranchArtifactStore：证据链 artifact；
- benchmark.release.ReleaseIndex 与 benchmark.reducer.MetricReducer：D1–D6、risk set、repeat/base-instance 归约和论文释放。

tool 只能收集 runtime evidence，并把证据交给上述权威层。tool 的 validate_run() 通过，只表示账本结构和 collector 生命周期完整；不表示物理成功、paired valid 或论文分数有效。

正式 paired 结论必须沿用：

~~~text
ExperimentBatch
  -> restore/checkpoint evidence
  -> PairedBranchCoordinator
  -> sham calibration + causal replay gate
  -> ReleaseIndex / MetricReducer
~~~

## 3. 已修正的兼容性问题

本次协调层已经加入以下兼容修复：

1. BranchJob.repeat_id 改为非空字符串，可直接保存论文中的 r1/r2/r3；
2. retry 只修改 branch_id 和 attempt，保持 base_instance_id 不变；retry 不是新的统计独立实例；
3. 新增 benchmark_bridge.py：
   - branch_job_from_spec(...)
   - branch_jobs_from_batch(...)
   - branch_plan_from_batch(...)
   - validate_jobs_against_batch(...)
4. BranchPlan 支持 explicit_jobs。当 job 来自 ExperimentBatch 时，collector 不再自行重算 branch identity；
5. fact-only cell 仍保留 fact-only，不会被 convenience BranchPlan 强行扩成四个角色；
6. collector validation 的 paired 字段改为诊断含义，固定输出：

~~~json
{
  "collector_pairing_complete": true,
  "strict_pair_claim_status": "unsupported",
  "strict_pair_claim_reason": "benchmark_paired_gate_required",
  "formal_pairing_eligible": false
}
~~~

其中 collector_pairing_complete 只代表结构完整；formal_pairing_eligible 是兼容旧调用方的 deprecated 字段，不能改回 true。
7. summary 使用 collector_status 和 inference_status，不会把 collector 完整误写成 "valid"；
8. adapter 未显式声明 supported_execution_scopes 时只能运行 protocol_synthetic。Dummy adapter 明确拒绝 measured，不能用 CLI 参数把 synthetic 结果伪装成实测。

## 4. 继续开发时必须完成的内容

### 4.1 接入真实 ExperimentBatch

真实 runner 应由 benchmark 先生成 batch，再调用：

~~~python
from tools.benchmark_branch import branch_plan_from_batch

collector_plan = branch_plan_from_batch(
    batch,
    execution_scope="measured",
    run_id="experiment-2026-...",
)
~~~

不得从 BranchPlan(scene_key=..., slots=...) 重新生成正式论文 branch 身份。正式 measured job 的 metadata 至少必须保留：

- benchmark_manifest_digest；
- benchmark_plan_digest；
- benchmark_run_key（包含 base_instance_id / system_id / condition_id / repeat_id / checkpoint_round / branch_role）；
- checkpoint_ref 与 checkpoint digest；
- treatment_id / authority / non_target_budget；
- untreated_history_id；
- strict_pair_required。

如果这些字段缺失，runner 应 fail closed，输出 pending/invalid artifact，而不是猜测补齐。

### 4.2 Scheduler 必须和真实 session 生命周期接通

当前 tools/physics_replay/execution/scheduler.py 已有 scene/branch lease、GPU/host memory 总账、max_parallel_scenes、max_parallel_branches、max_branches_per_scene 和 requested/actual device audit。`BranchExecutor` 已支持显式传入 `SessionBackend + ReplayScheduler`，并在多个 scene group 间并发、在每个 scene 内按 branch 容量并发；它不会自动创建 scheduler。

正式顺序应为：

~~~text
scene admission
  -> create/open simulator session
  -> warm-up
  -> scheduler.preflight(session)
  -> branch admission
  -> run_session_episode / adapter execution
  -> branch release
  -> scene release
~~~

不要在 session 创建前把 branch 当作已通过 physics preflight。requested cuda:0、actual physics device、controller/action device 和 GPU UUID 都要写进 scheduler audit；无法观测 actual device 时必须是 unavailable，不能默认为 CUDA。

当前正式入口仍需要上层 runner 显式编排：

~~~text
ExperimentBatch
  -> branch_plan_from_batch(...)
  -> scene/branch scheduler leases
  -> BranchExecutor / SessionBackend
  -> raw collector ledgers
  -> benchmark replay/evaluator/reducer
~~~

`BranchExecutor` 不会自动创建 `ReplayScheduler`；必须由上层 runner 显式提供真实
session/backend 和设备探针。因此不能仅凭 collector 的 `validation.json` 证明 GPU
admission、物理 session 隔离或外部 evaluator 结论已经通过。

### 4.3 shared-session 不能直接获得 strict paired 资格

shared-session 只有在以下证据全部存在后，才可以申请 measured：

- 每个 vector slot 有独立 controller/task/message queue/RNG/evaluator/checkpoint state；
- 有真实 cross-slot collision、force、queue、controller contamination 检测；
- 每个 slot 有独立 branch-local ledger；
- actual GPU device 和资源预算通过 scheduler preflight；
- 真实 session 的 restore/readback 证据通过；
- 最终仍需 PairedBranchCoordinator 做 causal replay 和 sham calibration。

在这些认证完成前，shared-session 只能标记为 diagnostic 或 pending，不能输出 strict paired valid。

### 4.4 外部 evaluator 仍由 benchmark/task 层提供

collector 可以收集外部 evaluator 返回的 raw evidence，但不能从“进程退出、step 达到 horizon、assignment 完成”推断 physical success。measured episode 必须有外部 evidence refs、evaluator version、evidence hash、milestone predicates、terminal/safety/feasibility predicates。

## 5. 建议的下一版接口

tool 已增加一个 session-aware runner seam；不要把 EMOS、任务 DAG 或 D1–D6 逻辑写入 tool：

~~~python
class SessionBackend:
    def open_scene(self, scene_id, *, scheduler): ...
    def preflight(self, session, *, requested_device): ...
    def open_branch(self, session, job, *, lease): ...
    def run_branch(self, branch_runtime, *, emit): ...
    def close_branch(self, branch_runtime): ...
    def close_scene(self, session): ...
~~~

BranchExecutor 只负责调度和账本，SessionBackend 负责 simulator/session，benchmark runner 负责把 ExperimentBatch 和 BranchSpec 交给它。

## 6. 验收清单

### Collector 基础

~~~bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  tools/benchmark_branch/tests
~~~

必须覆盖：retry identity、字符串 repeat、explicit benchmark jobs、fact-only、checksum corruption、resume、shared-session branch ledger、Dummy measured fail-closed。

### Benchmark 兼容

~~~bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  benchmark/tests tools/benchmark_branch/tests/test_benchmark_bridge.py
~~~

必须证明：bridge 不改变 RunKey、manifest digest、treatment、checkpoint digest；collector 不产生 strict paired valid。

### 代码质量

~~~bash
git diff --check
python -m compileall -q tools/benchmark_branch
~~~

## 7. 明确禁止的行为

- 用 tool 重新生成或覆盖 benchmark 的 base_instance_id、pair_key、repeat_id；
- 用 formal_pairing_eligible=true 代替 benchmark paired gate；
- 用 Dummy/shared synthetic 输出冒充 measured/GPU physics；
- 没有 actual device/preflight 证据就声称 CUDA 运行；
- 把 retry 作为新的 base instance；
- 在 tool 内实现论文 D1–D6 分数或 risk-set 归约；
- 为了通过 collector validation 而删除 failed、right-censored、restore mismatch 或 sham-invalid 记录。
