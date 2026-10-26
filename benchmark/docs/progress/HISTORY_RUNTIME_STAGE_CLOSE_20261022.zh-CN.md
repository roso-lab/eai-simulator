# History Runtime CPU 阶段性收尾（2026-10-22）

## 结论

已完成并验证上一会话遗留的最后一个已知运行时缺口：一次性 history run 的路径拒绝现在会在打开任何应用 context 之前拒绝符号链接和错误类型路径，避免空链/断链绕过“禁止复用已有 run”的检查并把 JSONL 或 seal 写到 run 根目录之外。

本阶段可收尾为：**CPU `protocol_synthetic` 诊断 seam 通过协调者验收；正式/物理资格仍未建立。** 独立 reviewer 在原会话末轮因安全策略中断，未能对本次补丁给出新的 Accept 签字，因此不把本阶段写成“独立复审通过”。

## 来源与上下文

- 目标会话：[codex://threads/01a162f3-f88e-7222-bb55-1022da304fbd](codex://threads/01a162f3-f88e-7222-bb55-1022da304fbd)
- 项目：`/home/pupil/HeteroCoord`
- 团队工作区：`/home/pupil/Documents/Codex/agent-team/workspaces/heterocoord-goal-runtime-20261022`
- 纸面/协议基线：`paper_state_v3.py`；正式 qualification gates 继续全部为 `false`

## 本阶段完成的工作

### 1. 路径安全拒绝

在 `tools/benchmark_branch/history_runtime.py` 增加 `_reject_unsafe_run_paths()`，并将其置于 `HistoryBlockRuntime.run()` 的任何 backend `open_context()` 调用之前。检查范围包括：

- 11 个 canonical JSONL ledgers；
- `checksums.sha256`；
- `branches/` 与 `evidence/` 根目录；
- 两个 artifact 根目录下的嵌套符号链接。

拒绝条件包括现存符号链接、断链符号链接，以及应该是 regular file/directory 却被替换成其他类型的路径。该检查是 run-integrity guard，不是操作系统安全边界。

### 2. 负测覆盖

在 `tools/benchmark_branch/tests/test_history_runtime.py` 增加现存目标链与断链两组测试：

- 11 个 canonical ledger × 2；
- seal × 2；
- `branches` / `evidence` × 2。

每个测试都确认 backend 没有打开任何 context，且外部目标未被创建或追加。

### 3. 前序修复保持有效

上一阶段已经完成并在本轮回归中复核：

- initializer 非 JSON-stable 响应（整数/字符串键冲突、tuple）被保留为 `malformed_response`，不执行 branch；
- normal close 的 child 非零退出会进入 `cleanup_failed`，timeout 强杀不误报 cleanup failure；
- 11 个 canonical ledger、seal、branch/evidence artifact 的常规预存状态会被拒绝；
- run timeout 在 terminal 返回前完成 child terminate/kill/join；
- completed/failed 计数按 terminal `completed is True` 统计；
- terminal `failure_detail` 被限制为有界文本，nested/private payload 不进入持久化结果。

## 验证证据

### 命令与结果

```text
PYTHONPATH=/home/pupil/HeteroCoord PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -q tools/benchmark_branch/tests/test_history_runtime.py
71 passed in 7.92s
```

```text
PYTHONPATH=/home/pupil/HeteroCoord PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -q \
  tools/benchmark_branch/tests/test_history_runtime.py \
  tools/benchmark_branch/tests/test_paper_state_v3.py \
  tools/benchmark_branch/tests/test_shared_session.py \
  tools/benchmark_branch/tests/test_session_backend.py
169 passed in 11.21s
```

```text
python -m py_compile tools/benchmark_branch/history_runtime.py \
  tools/benchmark_branch/history_process.py \
  tools/benchmark_branch/tests/test_history_runtime.py
git diff --check -- tools/benchmark_branch/history_runtime.py \
  tools/benchmark_branch/history_process.py \
  tools/benchmark_branch/tests/test_history_runtime.py \
  tools/benchmark_branch/executor.py
```

以上两项检查均退出码 0、无输出。

### 当前文件指纹

```text
4f6c0aaa189e0ae2e065142ff34f810fd09c70e26c4db57aedb5b5cd82e07f4e  tools/benchmark_branch/history_runtime.py
ea3f71aaedede10c1b9dee074147cc56dbc20f9931145bfb7a20108a7b5f3596  tools/benchmark_branch/history_process.py
8903c04e14d9c62ed27bd30c3f359fa4dfe4ad02fea41fd88cc8685e970f639b  tools/benchmark_branch/tests/test_history_runtime.py
```

## 尚未完成与边界

以下事项没有被本阶段偷偷升级为“已完成”：

- evaluator-owned source-run producer；
- native fresh shared-slot path；
- physical checkpoint restore/hold 与真实 Isaac/GPU 验证；
- external namespace reset、formal differential qualification、paper eligibility；
- 独立 reviewer 对本次 symlink 修复的最终 Accept。

P3 只读设计审计已经给出 source producer → fresh shared-slot 的后续顺序；其中 shared E2E 仍依赖 assign-all / batch-run 的运行时相位拆分。下一步应先实现并独立验证 source producer，再按该设计进入 fresh shared-slot；不要把当前 CPU seam 当成物理资格证明。

## 工作区记录

- 共享决策与经验已更新：`DECISIONS.md`、`RETROSPECTIVE.md`。
- 当前原会话的 R2 reviewer handoff 保留 Reject 证据；本文件记录的是其后协调者修复与回归结果。
- 未执行 commit、push、GPU/Isaac 运行或论文正文修改。
