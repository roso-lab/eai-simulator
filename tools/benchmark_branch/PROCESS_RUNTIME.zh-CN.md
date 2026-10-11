# 隔离进程运行与依赖固定

`ProcessSupervisor` 是外层进程执行边界。每个 worker 在自己的 Python/Isaac
进程内运行既有 runtime factory/CLI，进程间不传递 Kit、USD、Torch 或 session 对象。
它不创建正式任务、封存 branch evidence，也不把退出码 0 当作物理成功。

```python
from pathlib import Path
from tools.benchmark_branch.process import GpuAdmissionPolicy, ProcessSupervisor

# 同一 GPU 的所有调用方必须使用同一个 shared_directory 与固定策略。
policy = GpuAdmissionPolicy(Path("/absolute/shared/gpu-uuid"),
                            slots=2, capacity_mb_per_slot=6000, request_mb=6000)
supervisor = ProcessSupervisor("tmp/isolated-attempts", admission_policy=policy,
                               terminate_grace_s=10)
receipt = supervisor.run(
    ["/absolute/env/bin/python", "-m", "tools.benchmark_branch", "run",
     "--config", "/absolute/caller-config.json", "--output", "/absolute/new-run-dir"],
    cwd="/absolute/eai-simulator", timeout_s=300,
)
```

实际 CLI 参数以 `python -m tools.benchmark_branch --help` 及子命令 help 为准；
上例是组合入口示意。调用方负责每次 branch 输出目录独立，不能将同一个外部 output
传给多个 worker。`EAI_PROCESS_ATTEMPT_DIR` 与 `EAI_PROCESS_ARTIFACT_DIR` 提供本次
唯一目录；自有 worker 可将 branch 原始产物直接写入 artifact 目录。

每次调用先创建唯一 attempt 目录，保存 stdout/stderr，终止后写
`process_receipt.json`。argv 不经 shell。到期对进程组发 TERM，再按宽限期升级 KILL；
正常主进程退出也检查残留同组子进程。只有确认整个组终止才释放父进程持有的准入锁；
未确认终止的 slot 保留，禁止按“发出 kill”推断已停止。

POSIX flock 准入固定 shared_directory 内的 slot 数和每 slot 预算；配置不一致、
请求超额或 slot 不足都会拒绝。该预算是保守的调用方政策，不是显存硬隔离，且不
约束未使用本准入目录的其他程序。应结合既有 `CudaResourceProbe` 的真实 UUID、
可用显存/主机内存测量设定容量，并单独保留系统余量。容量数不从一次成功自动外推。

准入 FD 同时由 supervisor 与直接 child 持有：supervisor 异常退出时，仍运行的
child 保留锁。worker 必须保留继承 FD，且不自行 daemonize 到新进程组。关闭所有
未知 FD 或脱离进程组的运行时不在此保证内，需要单独的身份/隔离机制。硬超时仍是
操作系统终止协议；不可中断的内核状态可能使进程组终止未确认，receipt 会如实标记。

异常退出后禁止覆盖/拼接原 attempt。检查进程及其组确实结束、检查原始未封存
记录，修复原因后用新 attempt 显式重跑。没有终态 receipt 的目录是中断证据；
它不是可自动 resume 的成功记录。恢复正式任务需要上游 checkpoint/任务协议。

## Runtime 依赖指纹

现有 runtime_factory 配置可以增加 `runtime_dependencies`：

```json
{
  "runtime_factory": "my_runtime:create_runtime",
  "runtime_dependencies": {
    "controllers": ["/absolute/controller.py"],
    "models": [{"path": "/absolute/model.bin", "sha256": "<64 hex characters>"}],
    "assets": ["/absolute/robot.usd"],
    "external_inputs": ["/absolute/map.yaml", "/absolute/map.png"]
  }
}
```

载入时固定真实绝对路径、类型、SHA-256；目录以排序的相对文件路径及文件摘要递归
计算，目录内 symlink 拒绝。resume 在调用 factory 前核验声明和全部已声明文件；
缺失、内容变化、分组/路径/声明变化均要求新运行。未声明的依赖不在覆盖内。
未添加此字段的旧配置维持原 factory-file pin 行为，不能据此声称资产已全部固定。

## 分层检查

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  task/tests algorithm/skills/tests algorithm/skills/pepper_transport/pepper_transport_tests.py \
  tools/benchmark_branch/tests tools/physics_replay/tests/test_live.py
```

纯回归包括真实轻量子进程的超时、残留子进程、父崩溃、1/2/4 slot 竞争和依赖变化；
它不运行 Isaac。Isaac 场景、GPU 并发容量、故障恢复和视觉证据应由临时诊断逐项记录，
禁止将这些诊断或其 evaluator 当作正式任务。
