# 真实 GPU 三路最小切片失败收据（2026-10-25）

## 运行身份

- 输出目录：`artifacts/project_runtime/gpu-three-way-concordance-20261025-22/`
- manifest SHA-256：`8cd385e8a31d60b28050d5c0d4b171efbf7d25805dab1037bed89713f7b75651`
- command SHA-256：`8dee9c0cf22502b033fd961cb22db4947b03b5c5e1c4c5960e1bcae9dc255291`
- approval challenge：`dea750dc6baba9cc1a3f96f16199034e82f835c887f9a1e87e88934bdb825e2b`
- GPU：Quadro RTX 6000，UUID `GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0`
- 准入：公开 `permissive`；compute inventory 为空；ToDesk absent，仅作可选 provenance；冻结时显存余量满足峰值预留。

## 结果

A 路已经启动真实 Isaac/Kit、打开 Factory session、核对 live USD，并写入了真实
checkpoint：

`artifacts/project_runtime/gpu-three-way-concordance-20261025-22/shared_checkpoint/`

随后在 A 路第 9 个逻辑步的 evaluator command 校验处失败，B/C 尚未启动。因此没有
concordance 或 qualification 报告，`qualification_status` 不能升级，所有 formal gates
继续为 `false`。完整失败收据为：

`artifacts/project_runtime/gpu-three-way-concordance-20261025-22/run_failure.json`

worker stdout/stderr：

- `uninterrupted.worker.stdout.log`
- `uninterrupted.worker.stderr.log`

## 根因定位

失败 traceback：

`RuntimeError: live controller command differs from the frozen task schedule`

checkpoint 中第 1–8 步的 live command 均为 `[[0.0, 0.0, 0.0]]`，并且 evaluator 的
服务、锁、RNG 和 checkpoint milestone 已写入。第 9 步计划命令是 Python literal
`0.12`；CUDA 环境中的 `torch.float32` 读回后是
`0.11999999731779099`。当前 runtime 对 command 使用逐位 Python list 比较，导致
同一登记命令的 float32 表示差异被判为逻辑违约。

这属于真实运行后暴露的 command canonicalization 缺口，不能把本次 A 路收据当作三路
等价通过，也不能把它归类为启动前基础设施失败。

同一 runtime 在不启动模拟器的 float32 synthetic probe 中复现相同边界：第 1–8 步
通过，第 9 步稳定抛出同一异常。该 probe 只确认比较边界，不构成 GPU 或资格证据。

## 处理边界

该失败发生在模拟进程启动之后。按三路运行规则，本轮保留所有证据并暂停；修复
command canonicalization 后，是否启动新的冻结目录与真实重跑需用户决定。不得静默重跑、
覆盖 `-22` 或打开任何正式资格门。

## 后续修复（用户放行后）

已在源码中将 task runtime 的命令登记、live 读回和历史校验统一为明确的
`np.float32` canonical representation，并在 runner manifest 增加
`command_contract`（dtype、shape、canonicalization 和 exact comparison policy）。
新增 float32 continuation 边界测试；相关 focused suite 当前 `80 passed`，但该修复
尚未用于真实 GPU 重跑，`-22` 仍是不可升级的失败证据。
