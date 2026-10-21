# P1/R7 shared-session 崩溃归因阶段反思与 handoff

日期：2026-10-21
论文基准：`1f329494e755523692f5f96c8e0889c6cec15a82`

## 完成内容

`tools/benchmark_branch/vectorized_executor.py` 的 shared-session 异常路径现在只处理已绑定、已进入当前执行波次的分支。场景中的后续任务仍保持 queued/not-run，不会因为同一 shared session 的崩溃被写成 `unknown-record` 或 `pair-invalid`。如果 native slot 在 `bind` 过程中已经分配但随后报错，只接纳该具体当前 job，仍不把影响范围扩展到 pending 队列。

这与论文 `app:shared-session` 的 active/in-flight block 规则一致：崩溃或 physics buffer warning 使当时在飞的 block 失效，随后按原身份进入 isolated rerun；尚未启动的 block 不产生终止记录。

## 验收证据

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  tools/benchmark_branch/tests/test_vectorized.py \
  tools/benchmark_branch/tests/test_r7_shared_watchdog.py
54 passed
```

新增回归覆盖同一 scene 中 active block 与 pending block：前者获得 `unknown-record`，后者没有 episode terminal receipt，并由 manifest 的 queued 数量保留未运行状态。

## 未完成与边界

- 这是合成 backend/CPU 协议测试，不是 Isaac/Kit/GPU 资格证据。
- `shutdown_watchdog` 仍未接入生产 vectorized executor；当前正常分支的 `shutdown_status` 仍是 pending，缺 collector-owned clean/unknown terminal receipt。
- shared-session 仍未完成真实 GPU runner、isolated restore 对照、slot permutation/batch/hold/rollback qualification 和 isolated open-loop audit。
- 所有 formal qualification gates 与 `paper_score_eligible` 继续保持 false。

## 下一阶段 handoff

进入 G2 GPU-first qualification：先补真实 GPU receipt 的身份链、GPU UUID、物理设备一致性、显存/租约、clean shutdown 和 telemetry cleanup；再做同一配置下 uninterrupted、isolated-process restore、shared-session restore 三路对照。未形成 collector-owned 证据前，不得把 shared-session 结果升级为正式论文证据。
