# 四场景任务与算法接口交接验收（2026-09-20）

本轮完成任务生成、作者审核、四场景一致性及高层接入的离线交接。导航/移动/操作使用固定
默认技能契约；TeamWeaver、EMOS 是被测系统。未启动 Isaac、未实现多进程/多机器调度，
未提交 Git 或发布 provider 资产。接手步骤见 [ALGORITHM_HANDOFF.zh-CN.md](ALGORITHM_HANDOFF.zh-CN.md)。

## 实际产物

本机新生成目录为 `tmp/benchmark_delivery_20260920_jm6i7tcf/`：

| 文件/目录 | 已验证内容 |
| --- | --- |
| `suite/audit_report.json` | 36 个完整题包、28 对单因子对照；四场景各九条件、七对照；源码冻结验证通过 |
| `handoff/handoff.json` | 四份基准题包实读、作者/API/见证复审、文件清单和哈希重算；四场景均为 GoalSession 主轨 |
| `handoff-cli-verification.json` | 独立 CLI 读回交接矩阵的结果 |
| `verification.json` | 四场景仅从 release 加载公开文件，完成 adapter 团队选择；同时记录 36/28 与未启动 Isaac |

这些是临时运行输出，不作为源码入库。交接矩阵的所有 `execution_status=external_pending`、
`scoreable=false`；suite 的 `held_out_validated=false`。它们分别表示运行、计分和独立
未曝光布局尚待验收，不否定作者侧生成/审核已通过，也不会被作者通过标志覆盖。

新增的一键预检 `demo.benchmark.handoff_preflight` 已通过：在新目录生成并读回 36/28
矩阵，验证四个公开 release 目录均只有 `public_task.json`、`participant_api.json`、
`baseline_manifest.json`，并对四个场景各完成一次公开团队选择。该预检不启动 Isaac、ROS
或外部调度器，失败会清理部分输出。

生成通过 Python API `write_goal_suite(..., verify_sources=True)`、`write_four_scene_handoff`
执行，并分别用 `verify_goal_suite`、`verify_four_scene_handoff` 实读复核。随后实际运行：

```bash
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.benchmark.goal_suite verify \
  --output-root tmp/benchmark_delivery_20260920_jm6i7tcf/suite --verify-sources
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.benchmark.handoff_manifest \
  --verify tmp/benchmark_delivery_20260920_jm6i7tcf/handoff
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.benchmark.diagnostic_audit
```

三个命令 exit 0；最后一个命令独立验证 Factory 五份、Warehouse/House 各一份维护 sidecar。
新版本源码变化后原冻结包可能不再通过 `--verify-sources`，应保留旧证据并重新生成。

## 最终测试

此前 2026-09-20 定向回归为 **398 passed**。加入一键预检和 goal 轨道 manifest
fail-fast 后新增 3 项；当前交接相关回归合计 **401 passed**（原 398 + 预检 2 +
轨道 manifest 1），exit 0。

完整命令为：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  source/EAI/test/test_benchmark_algorithm_adapter.py \
  source/EAI/test/test_benchmark_handoff_manifest.py \
  source/EAI/test/test_benchmark_goal_algorithm_adapter.py \
  source/EAI/test/test_benchmark_goal_session.py \
  source/EAI/test/test_benchmark_goal_suite.py \
  demo/city_benchmark/tests/test_formal_binding.py \
  source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_freeze.py \
  source/EAI/test/test_benchmark_reliable_skill_backend.py \
  source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_participant_entrypoints.py \
  source/EAI/test/test_benchmark_diagnostic_audit.py
  source/EAI/test/test_benchmark_handoff_preflight.py
git diff --check
```

覆盖四场景接口、公开事件增量、非法低层动作/伪造完成、半批失败不重放、可信回执、固定
控制 manifest、goal/fixed-DAG 轨道 manifest 互拒、比较输入隔离、指标方向、缺测/重复结果、题包篡改/私有文件泄漏、父路径
符号链接、生成期间源码变化拒绝及暂存清理、City 可选 preparation 不晋升等边界。

过程中发现旧 reference backend 测试仍让 Carter 承载被当前约束禁止的支撑点，已将测试
更新为权威任务见证使用的双 M20；没有放宽准入规则。新增回执测试亦修正为现有的公开
`action_result` schema，未引入第二种事件协议。修复后的完整回归结果以上述最终命令为准。

## 团队审查与限制

复用既有工程代理实现 GoalAlgorithmHost，并让其独立只读审查主代理的 suite/handoff 改动。
它报告了生成中源码漂移及父 symlink 两项缺陷，主代理已修复并以反例测试验收。
审查代理后续遇到服务 429，未取得独立的修复后签核；不把该失败写成“独立审查全绿”。

当前没有统一完成四景 Isaac 后端符合性验证、真实算法业务闭环、未曝光布局构建或正式统计
实验。固定控制声明不是物理理想化证明；普通 episode bootstrap 也不能把同一布局多分支
当作独立任务。上述后续步骤及外部调度边界均已在接入指南中列明。
