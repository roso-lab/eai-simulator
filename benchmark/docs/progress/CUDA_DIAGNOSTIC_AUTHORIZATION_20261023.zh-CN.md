# CUDA Diagnostic 单次执行授权包

日期：2026-10-23  
项目：`/home/pupil/HeteroCoord`  
状态：用户已于 2026-10-23 明确授权，并说明后续无需逐次授权；执行仍受本文单次、失败即停与证据边界约束

## 目的与证据边界

本次只验证 supervised native source-collector 的 CUDA admission、runtime/device identity、GPU lease 生命周期、session close 与 telemetry cleanup。它不是物理资格、history qualification、branch qualification、closed-loop audit 或统计 widening 实验；即使成功，以下七个 gate 仍必须为 `false`：

```text
physical_checkpoint_qualified=false
history_initialization_qualified=false
branch_isolation_qualified=false
closed_loop_audit_qualified=false
statistical_widening_qualified=false
paper_score_eligible=false
rankable=false
```

## 冻结配置

- 场景：沿用 source-collector 默认单场景配置，不扩展场景矩阵。
- checkpoint：单个已登记 checkpoint，沿用 collector 的固定 checkpoint sidecar。
- 设备：`cuda:0`；执行前记录 runtime GPU UUID，并要求 child/supervisor UUID 一致。
- 执行次数：一次；禁止重试、并行、矩阵和自动恢复。
- 输出目录：`artifacts/project_runtime/source-collector-cuda-20261023-02/`，必须是不存在的新目录。
  `-01` 已因同 GPU 上存在并发 ZCode 渲染 compute app 判为污染证据并原样保留。
- 解释器：`/home/pupil/miniconda3/envs/env_isaaclab/bin/python`。
- 解释器一致性：`tools.project_runtime exec` 会把内层裸 `python` 替换为外层
  `sys.executable`；已审计 `tools/project_runtime.py:237-247`，因此不会意外落到基础环境。
- 允许写入：仅上述新 artifact 目录；不修改源码、测试、论文或既有 artifact。

## 执行前检查

1. 确认 `env_isaaclab` 可导入 `isaacsim`、`torch`、`pxr`，并记录版本。
2. 重新读取 GPU 0 的 UUID、总显存、使用量、计算应用和 compute mode；selected GPU 的 compute-app
   baseline 必须为空，运行后 compute-app inventory 也必须为空。若存在任何计算进程、清单读取失败或
   malformed row、UUID 无法读取、设备不是 `cuda:0` 或环境身份不一致，立即停止，并且不得创建输出目录。
3. 确认运行前不存在 `omni.telemetry.transmitter` 进程。当前 cleanup 使用 baseline-preserving
   PID inventory，只清理本次新增 PID；若已有其他会话的 transmitter，必须停止本次执行，不能
   把外部进程当成本任务残留清理。
4. 确认目标输出目录不存在且父目录可写；不得覆盖任何历史运行。
5. 确认源码与测试版本仍匹配 admission-guard 验收哈希；若发生漂移，先重新测试，不得执行。
6. 确认本次授权只覆盖下面这一条命令；任何参数、场景或设备变化都需要重新授权。

当前冻结哈希：

```text
tools/validation/run_project_probe.py
04d21e91cd57d5deae13a7a42da98e83ace063735538823d0eca5f9107268634

tools/benchmark_branch/tests/test_native_source_probe.py
1da664e1a522d79791e0c20dc736886120707dcfd35edb137e631ef3d05de3b5

# supporting runtime/cleanup implementation
tools/project_runtime.py
20cc960191594eaee227835593bd6f0ad0e67e80d4471bbca9f3c767658811e8

tools/benchmark_branch/eai_vectorized_backend.py
6a57a1d376177e89a5de04017b8b3dcad31d8764113c1e9b4246be4919656ba6

tools/benchmark_branch/tests/test_eai_vectorized_backend.py
63a2f7e11fb8f1fd8f66935e1598f56f3688a2fa27f1883a3bca60b2d47dde68
```

## 唯一允许命令

授权前可先使用同一参数追加 `--dry-run` 做无副作用解析；它只打印最终 child command，不创建输出目录、不采集 GPU、不启动 native。实测已通过。

```bash
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.project_runtime exec -- \
  python -m tools.validation.run_project_probe \
  source-collector \
  --device cuda:0 \
  --output artifacts/project_runtime/source-collector-cuda-20261023-02
```

## 通过条件

只有同时满足以下条件，才可将 artifact 标记为“diagnostic CUDA admission passed”：进程返回码为 0；child 与 supervisor receipt 完整；requested/actual device 均为 `cuda:0` 且 GPU UUID 一致；selected-GPU compute baseline 与 post-run inventory 均为空；lease acquire/release 均为真；session close 完整；telemetry residue 为空；source/checkpoint/collection/runtime/cleanup identity 可离线重算；七个 formal gate 全部仍为 `false`。

## 失败与停止条件

任一预检、身份、lease、session close、telemetry cleanup、seal 或 gate 检查失败，立即停止，不重试，保留失败 artifact，并把本次运行标记为 diagnostic failure。失败 artifact 不得删除或覆盖；后续修复必须产生新的授权包和新的输出目录。

## 授权记录

用户已明确回复“以后不用授权，可以直接使用”，解除本项目后续 CUDA/native 诊断的逐次授权暂停点。
这不会解除本文的资源隔离、身份一致性、失败即停、不得重试及 formal gate 不升级要求；涉及新设备、
扩大矩阵、覆盖既有 artifact 或影响其他会话的操作仍需重新评估范围。
