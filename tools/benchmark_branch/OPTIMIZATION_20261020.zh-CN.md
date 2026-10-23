# 分支执行与回放工具优化：主系统接入说明

本次工具实现依据 Overleaf 项目 `6ab4c7c4e984abb8da9c8113` 的
`fc04465e034c9e25402cb52c6b574e3b44e5b797`。相对主系统先前冻结的 `07dcd40`，
这次论文更新只修改图 3，回放和重试条款未改变。工具保留物理执行、论文配对、
统计发布的分层边界；资源调度完成不能作为物理成功或因果恢复认证。

## 已改变的运行行为

1. `max_workers` 成为 session collector 的全局分支上限，不再随驻留场景数相乘。
   场景线程数也限制为请求数、计划场景数与 scheduler 场景容量的最小值。
2. 分支正常结束或异常结束，都在 runtime 关闭后释放 branch lease；关闭失败只调用
   一次清理，保留 lease 并停止 scheduler 的新准入，唤醒等待者，避免继续运行或永久等待。
   成功关闭整个 scene 后才允许释放这些保留 lease。scene 关闭失败时保留 scene lease，
   manifest 和 scheduler snapshot 如实记录仍占用的分支数。恢复需要调用方确认运行时
   清理完成后重建 scheduler，不能通过超时强制清空账本。
   open 调用在分配部分 native 资源后抛错或返回空句柄，也按归属未确认处理：停止新准入，
   branch 等整个 scene 成功关闭才释放；没有 scene 句柄时保留 scene 容量供外部清理。
3. 同一对象被错误地返回给两个活动分支时，拒绝第二个分支；不能让第二个分支关闭
   第一个分支仍在使用的对象。检测覆盖同一 collector 内的不同场景。
4. 实测运行的 `resume` 先检查封存历史和冻结重跑规则；没有任务可运行时不再重新
   捕获 checkpoint、启动 bootstrap 场景。方法失败继续作为结果保留。
5. 回放 reader 默认最多保留两个分段、64 MiB 解压数组，采用 LRU。超大分段按需读取
   但不留存；`clear_cache()` 可主动释放缓存。显式完整性检查重新读取磁盘，文件被改动
   或删除不能被旧缓存掩盖。这里限制的是缓存数组字节，不是进程 RSS 或单次解压峰值。

## 论文约束如何落实

论文 `sec/X_suppl.tex:339` 要求新的 fresh-process retry 使用新 base-instance 身份，
不能塞进原配对；`:1442` 允许预注册的基础设施重跑恢复原 checkpoint 和随机流，
保留原始记录，并明确方法失败不得重跑。

| 已封存结果 | 工具行为 |
| --- | --- |
| completed | 保留，不重新执行 |
| method timeout、malformed action、budget overrun、missing response 等 | 保留方法结果，不追加替代 attempt |
| 已有方法失败，后来又出现 evaluator/cleanup 故障 | 仍保留方法失败，附加基础设施问题 |
| 明确基础设施故障，命中冻结原因列表，有完整恢复登记且未超上限 | 允许同身份追加 attempt，原行保持不变 |
| 无登记、原因不明的 crashed、缺终态的未知运行 | 留待上游审查，不自动重试 |
| 新进程从头重建、固定技能的新 controller 子流 | 由主系统先登记新的执行方案；不靠 collector 自动改身份 |
| protocol_synthetic | 保留既有协议测试的便利重试；不能当实测配对证据 |

规则适用于 `measured` 与 `measured_diagnostic`。旧实测计划没有冻结恢复登记时不会
自动升级；应建立有完整登记的新运行。`BranchJob.with_attempt()` 只是身份变换函数，
本身不授权任何科学重跑。

## 主系统需要提供的最小接线

正式 job 在创建 run 前冻结 `checkpoint_digest` 和下面的 metadata。digest 来自
实际快照和状态读回；示例字符串只是字段说明，不能直接作为实验数据使用。

```json
{
  "registered_recovery": {
    "schema": "heterocoord.registered_recovery.v1",
    "mode": "same_checkpoint_random_stream",
    "registration_id": "upstream-registered-rule-id",
    "checkpoint_digest": "same-as-frozen-job-checkpoint",
    "random_stream_digest": "digest-of-registered-random-state",
    "participant_state_digests": {
      "world": "snapshot-state-digest",
      "clock": "snapshot-state-digest",
      "controller": "snapshot-state-digest",
      "skill": "snapshot-state-digest",
      "task": "snapshot-state-digest",
      "algorithm": "snapshot-state-digest",
      "transport": "snapshot-state-digest",
      "locks": "snapshot-state-digest",
      "events": "snapshot-state-digest",
      "rng": "snapshot-state-digest"
    },
    "infrastructure_reasons": ["preflight_rejected", "registered-simulator-error"],
    "max_attempts": 2
  }
}
```

`max_attempts` 包含原始 attempt 0。本恢复 schema 要求 world、clock、controller、skill、
task、algorithm、transport、locks、events、rng 全部 10 类参与者，允许额外登记参与者；
缺任何一类即拒绝。实际状态内容、版本和完整性仍由主系统恢复注册表提供并验收。

允许重跑的 episode 必须有明确 `status`（`infrastructure_invalid`、`simulator_error`
或 `evaluator_error`）及登记过的 `infrastructure_reason`。只填 `completed=false`
或者通用 `crashed` 不够。

恢复后的 branch driver 在第一条 step 前发出一条 checkpoint receipt，包含：

- `restore_passed: true` 和 `readback_digest`；
- `recovery_registration_id` 和 `recovery_mode`；
- 现场恢复后计算的 `random_stream_digest` 与 `participant_state_digests`。

它们必须逐项匹配冻结登记。失败凭据保留，禁止继续记录执行步骤。离线 `validate`
重新检查 attempt 前因、身份、冻结规则与 checkpoint/random-stream/participant 读回；
初始恢复凭据须唯一，后续普通 checkpoint 诊断行不计入恢复凭据数量。
即使记录被重新封存，也不能把方法失败后的重试当作合法基础设施恢复。
这些 hash 和字段绑定证明一致性，不证明提供者诚实，更不代替 sham、终态一致性或
PhysX 隐状态认证。主系统恢复注册表若因部分恢复而被隔离，driver 不得发成功凭据。

## 与主会话开发的边界

- 当前主系统负责论文目录、共同起点、公共历史、参与者完整性、通信状态、sham 和统计。
- 工具负责执行容量、生命周期、冻结重跑规则的执行、原始证据留存和磁盘回放效率。
- `EaiSessionBackend` 继续遵守主线程串行限制。真实并行需要隔离进程/环境 backend；
  本次没有把 `max_workers` 配置冒充 GPU 并行。
- 不修改主系统 `benchmark/`、`task/`、`source/`、`simulator.py` 或正在开发的
  `tools/validation/project_local_*`。主系统可接入本工具提交后运行自己的真实诊断。
- 本包不含历史 `demo.fire_rescue`；涉及该外部 CLI 的七项测试在缺包时明确 skip，
  重建核心测试继续执行。历史文档里的 demo 命令不是当前包的独立可运行入口。

## 验证入口

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tools/benchmark_branch/tests tools/physics_replay/tests
python -m tools.physics_replay.validation.cache_benchmark \
  --output /absolute/new-cache-report.json
```

cache benchmark 比较同一 reader 的“保留所有分段”配置与默认有界配置，检查逐帧及
终态摘要一致，并报告缓存字节和耗时。缓存减少意味着再次访问旧段可能重新解压；
不能把减少内存自动说成提高吞吐。它不启动 Isaac，也不提供物理保真或 GPU 容量认证。

本轮验证（2026-10-20）：工具套件 315 passed / 14 skipped；将本 worktree 的 tools
优先放入模块搜索路径后，主系统 benchmark 与 task checkpoint 测试 250 passed，
测试期间输入源码指纹未变化。14 项 skip 为环境/可选集成项，其中 7 项依赖外部 demo。
64 分段、512 帧、数组宽度 4096 的缓存对照中，留存解压数组从 25,180,160 降到
786,880 字节（减少 96.875%），状态摘要一致；读取时间约 0.050 秒增至 0.142 秒。
本轮没有启动真实 Isaac/GPU 实验，不能据此认定物理回溯保真或 GPU 并行能力。
