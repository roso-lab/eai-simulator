# Replay/GPU Scheduler Tool 开发交接文档

当前 collector 已提供 CLI runtime factory、主线程 EAI backend 和文件级 evaluator
证据校验，接线见 [运行集成合同](../benchmark_branch/RUNTIME_INTEGRATION.zh-CN.md)。
这些机制不内置场景任务，也不替代 shared-session 隔离认证或外部严格配对验收。

版本：replay.scheduler.v1

目标：为正式 benchmark 提供可审计的 GPU 资源准入、scene/branch 并行控制和独立 replay session 生命周期。

这份文档只描述 tools/physics_replay/ 的底层能力。论文实验的 branch 语义、任务流程和指标仍由 benchmark/ 主干负责。

当前实现状态：scheduler 的 scene/branch lease、容量限制、统一显存账本、device
preflight、stale lease 回收和 audit 已落地并有 fake-backend 测试。`BranchExecutor`
现在支持显式注入一个 `SessionBackend` 和一个 `ReplayScheduler`，按
scene admission → session warm-up/preflight → branch admission → branch release 的顺序
执行；它不会从 CLI 配置自动创建 scheduler。正式 runner 仍需提供真实 session、设备
探针和 evaluator，不能把 collector 的完成状态当成 GPU 预检或物理成功。

## 1. 职责边界

### tool 开发者负责

- checkpoint capture/restore/fork 以及 restore fidelity 报告；
- requested device 与 actual physics device 的验证；
- GPU 显存、主机内存和 worker 进程的资源采样；
- scene slot、全局 branch slot、单 scene branch slot 的准入和释放；
- 超容量时排队或显式拒绝，不得隐式 oversubscribe GPU；
- warm-up、session 创建后的 preflight 和正式计时边界；
- branch worker 异常、进程崩溃后的 lease/session 清理；
- 每个 scene/branch 的设备、资源、容量和生命周期 audit；
- 为 benchmark 提供一个不会共享可变 physics session 的窄接口。

### benchmark 主干负责

- fact/reference/sham/intervention 四类 branch 的论文语义；
- RunKey、BranchSpec、paired checkpoint、sham calibration 和 strict paired gate；
- EMOS assignment、Skill、TaskExecutor、P2/P3/P4 milestone 和外部 evaluator；
- BranchArtifactStore、ReleaseIndex、D1–D6 和 MetricReducer；
- 30 个场景的任务语义、obligation graph 和 common-anchor 认证；
- 决定某个 tool report 是否允许进入论文 release。

正式数据流应保持为：

~~~text
ExperimentBatch / BranchSpec
  -> tools.benchmark_branch.branch_plan_from_batch
  -> ReplayScheduler scene/branch admission
  -> isolated SessionBackend / BranchExecutor
  -> raw ledgers and audit
  -> benchmark replay + evaluator + MetricReducer / ReleaseIndex
~~~

不要在 tool 中新增第二套 task graph、EMOS planner、metric registry 或 physical success evaluator。tool 只报告事实和资源证据。

## 2. 正式 GPU 规则

正式 benchmark 默认 GPU-first。场景发布前必须完成 GPU preflight；CPU 只允许用于开发诊断、资产检查和失败定位。

如果 actual_physics_device 不是 cuda:<id>：

- tool 仍可保留诊断 artifact；
- 该运行必须标记为 infrastructure_invalid 或 pending；
- 不得被伪装成正式 GPU 论文结果；
- 不得仅因为 requested device 写成 cuda:0 就判定 GPU 运行成功。

## 3. 资源与调度数据结构

建议把实现放在 tools/physics_replay/execution/scheduler.py。合同保持依赖轻、可 JSON 序列化，不要把 Isaac 类型放进合同层。

~~~python
@dataclass(frozen=True)
class ReplayCapacity:
    max_parallel_scenes: int
    max_parallel_branches: int
    max_branches_per_scene: int = 4
    gpu_memory_budget_mb: int | None = None
    host_memory_budget_mb: int | None = None

@dataclass(frozen=True)
class ResourceReservation:
    gpu_memory_mb: int
    host_memory_mb: int = 0

@dataclass(frozen=True)
class DeviceEvidence:
    requested_device: str
    actual_physics_device: str
    controller_device: str
    device_validation: str       # passed / failed / unavailable
    gpu_uuid: str = ""
    gpu_memory_budget_mb: int | None = None
    diagnostics: tuple[str, ...] = ()

@dataclass(frozen=True)
class ReplayRequest:
    scene_id: str
    branch_key: str              # RunKey.as_string()，tool 不解析论文语义
    reservation: ResourceReservation
    requested_device: str
    warmup_ticks: int = 0

@dataclass(frozen=True)
class SceneLease:
    lease_id: str
    scene_id: str
    slot: int
    capacity: ReplayCapacity

@dataclass(frozen=True)
class BranchLease:
    lease_id: str
    scene_id: str
    branch_key: str
    slot: int
    device: DeviceEvidence

@dataclass(frozen=True)
class CapacitySnapshot:
    active_scenes: int
    active_branches: int
    active_branches_by_scene: Mapping[str, int]
    queued_requests: int
    gpu_uuid: str
    gpu_free_memory_mb: int | None
    events_digest: str

@dataclass(frozen=True)
class ReplayAudit:
    event: str
    lease_id: str
    scene_id: str
    branch_key: str = ""
    requested_device: str = ""
    actual_physics_device: str = ""
    warmup_ticks: int = 0
    episode_time_s: float | None = None
    diagnostics: tuple[str, ...] = ()
~~~

所有 lease 必须携带 scene_id 和 branch_key。tool 不需要知道 branch role，但不能把不同 branch_key 的 mutable session 合并到同一个 worker。

## 4. Scheduler 最小接口

~~~python
class ReplayScheduler:
    def __init__(self, capacity, *, resource_probe, device_probe, audit_sink=None): ...

    def preflight(self, request: ReplayRequest, session: Any) -> DeviceEvidence: ...

    def acquire_scene(
        self, scene_id: str, *, reservation: ResourceReservation,
        requested_device: str, wait: bool = True, timeout_s: float | None = None,
    ) -> SceneLease: ...

    def acquire_branch(
        self, scene: SceneLease, *, branch_key: str,
        reservation: ResourceReservation, requested_device: str,
        wait: bool = True, timeout_s: float | None = None,
    ) -> BranchLease: ...

    def release_branch(self, lease: BranchLease, *, status: str = "completed") -> None: ...
    def release_scene(self, lease: SceneLease, *, status: str = "completed") -> None: ...
    def snapshot(self) -> CapacitySnapshot: ...
    def recover_stale(self, *, live_pid_check: Callable[[int], bool]) -> tuple[str, ...]: ...
~~~

推荐提供上下文管理器：

~~~python
with scheduler.scene_slot(...) as scene_lease:
    with scheduler.branch_slot(scene_lease, branch_key=...) as branch_lease:
        # benchmark worker 在这里创建/使用独立 session
        ...
~~~

上下文退出必须幂等释放。worker 抛出异常时必须记录 branch_abort，释放 branch lease，并在 scene 没有其他 branch 时释放 scene lease。不能因为异常留下锁、GPU 占用或 running 状态。

## 5. 调度规则

三个容量旋钮必须同时生效：

- max_parallel_scenes：不同 scene session 的总上限；
- max_parallel_branches：所有 scene 合计的 branch 上限；
- max_branches_per_scene：单个 scene 内 branch 上限。

四个 branch 逻辑上必须全部执行；容量不足时只能排队或明确拒绝，不能减少成两个 branch，也不能让四个 branch 共享一个 mutable physics session。tool 不决定四个 branch 的执行顺序，顺序由 benchmark/branch_execution.py 或更高层 runner 决定。

建议调度顺序：

~~~text
scene admission
  → session factory creates isolated session
  → warm-up / device preflight
  → branch admission
  → benchmark worker executes one branch
  → branch release
  → scene release
~~~

warm-up tick 不计入 episode execution time。正式计时起点必须在 warm-up 和 device preflight 通过之后，并在 audit 中明确记录。

## 6. Device probe 与 GPU gate

device_probe 不能只读取 session.device。它必须尽可能核验：

- physics backend 实际 device；
- controller/action device；
- GPU UUID；
- GPU free/used memory；
- scene/env 数量；
- 是否启用了 CPU fallback；
- GPU 与 scene 兼容性。

如果 actual device 无法确认，返回 unavailable，不要猜测为通过。建议的通过记录：

~~~python
DeviceEvidence(
    requested_device="cuda:0",
    actual_physics_device="cuda:0",
    controller_device="cuda:0",
    device_validation="passed",
    gpu_uuid="...",
)
~~~

## 7. Checkpoint/fork 与现有 replay coordinator 的关系

现有 ReplayCoordinator 和 benchmark/replay_backend.py 继续负责 checkpoint restore、component state、action/noise tape 和 fidelity report。Scheduler 不重写这些逻辑，只负责资源准入和 session 生命周期。

每个 branch 必须通过独立的 session/coordinator factory 创建：

~~~python
def coordinator_factory(branch):
    # 创建独立 simulator session、FleetRuntime、ReplayCoordinator
    ...
~~~

tool 不得从已处理 branch 复制 treatment 后状态，也不得在同一个 mutable ReplayCoordinator 上并发调用两个 branch。benchmark 的 paired gate 仍然检查四个 branch 是否共享同一 checkpoint digest，以及是否达到 causal_replay。

## 8. Audit 与 artifact

每次 scene/branch 至少记录：

~~~text
scene_id
branch_key
lease_id
requested_device
actual_physics_device
controller_device
gpu_uuid
gpu_memory_budget_mb
max_parallel_scenes
max_parallel_branches
max_branches_per_scene
warmup_ticks
episode_start_time
episode_end_time
release_status
crash_or_abort_reason
~~~

建议事件类型：

~~~text
scene_queued
scene_acquired
scene_preflight_passed
scene_preflight_failed
branch_queued
branch_acquired
branch_started
branch_completed
branch_abort
branch_released
scene_released
stale_lease_recovered
gpu_admission_rejected
~~~

tool audit 是资源与设备证据，不是 evaluator evidence，也不直接写 D1–D6 数值。

## 9. 崩溃恢复与 stale lease

- 正常退出、异常退出和 session factory 失败都必须释放 lease；
- 进程级 worker 必须记录 owner PID、启动时间和 lease id；
- recover_stale 只能在明确确认 PID 已不存在后回收 lease；
- 不能因为超时就强制删除仍存活进程的 lease；
- 回收必须产生 stale_lease_recovered audit；
- recovery 后新的 scene/branch 才能重新准入。

## 10. 测试验收

测试放在 tools/physics_replay/tests/，至少覆盖：

1. max_parallel_scenes 限制不同 scene 的并行数；
2. max_parallel_branches 限制全局 branch 数；
3. max_branches_per_scene 限制单 scene branch 数；
4. 超容量请求排队或显式拒绝；
5. scene/branch lease 重复释放幂等；
6. worker 异常后没有 active lease 或孤儿 session；
7. requested device 是 CUDA 但 actual physics device 是 CPU 时，preflight 失败；
8. warm-up 不计入 episode 时间；
9. 每个 branch 得到独立 session/coordinator，不能共享 mutable 对象；
10. GPU UUID、显存、容量和 abort 信息进入 audit；
11. stale lease 只回收已死亡 PID；
12. tool 不生成 physical success、D1–D6 或 paired valid 结论。

## 11. 允许修改范围

允许修改：

- tools/physics_replay/；
- tools/physics_replay/tests/；
- 必要时 benchmark/replay_backend.py 的适配字段；
- 必要时 benchmark/branch_execution.py 的最小调用适配，但先向主代理报告。

不要修改：

- EMOS assignment 或 planner；
- 30 个场景任务语义；
- 外部 evaluator predicate；
- D1–D6 metric registry/reducer 公式；
- simulator.py 的 Isaac application composition root；
- benchmark/replay.py 的 paired/fidelity 语义，除非先提出契约变更。

## 12. 完成标准

开发完成后必须交付：

- scheduler 实现路径；
- 所有 tool tests 结果；
- 一个 Isaac-independent fake worker 并行 smoke；
- 一个 GPU preflight 报告样例（真实 GPU 或明确 unavailable）；
- audit JSON 样例；
- 明确说明哪些 fidelity 仍是 physical_state_only、哪些条件才能报告 causal_replay。

显式接线已经完成，但没有真实 Isaac session、GPU UUID/preflight 和外部 evaluator
证据的运行仍必须按 `protocol_synthetic`、`pending` 或 `infrastructure_invalid` 记录；
不得把它写成 `GPU_valid` 或 `strict_paired_valid`。
