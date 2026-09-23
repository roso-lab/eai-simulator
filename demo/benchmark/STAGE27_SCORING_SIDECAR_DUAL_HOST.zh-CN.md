# 阶段 27：评分侧车通信证据接入与双机复验

评分适配器现可读取 episode 根目录的 `communication_analysis.json`，验证分析 schema、版本和有限实验状态，并保存 SHA-256。该字段仍是 raw-only 诊断证据，不生成六维分数，也不改变 `scoreable`、`execution_validated` 或 `business_outcomes_validated`。

本机 `env_isaaclab`（禁用 ROS pytest 自动插件）通过：

```text
test_benchmark_goal_scoring_adapter.py
test_benchmark_communication_analysis.py
test_benchmark_communication_pools.py
42 passed
```

ljy 新建隔离工作树 `/home/ljy/eai-simulator-worktrees/stage27-scoring`，通过 git bundle 同步到同一提交 `15d06ae`，同样通过：

```text
评分侧车、通信分析、通信池：42 passed
remaining oracle、deployment、checkpoint、USD closure：55 passed
```

同步时首次检出遇到 Git LFS 资产下载 EOF；重新以 `GIT_LFS_SKIP_SMUDGE=1` 创建隔离树后完成测试。该资产下载问题属于环境基础设施记录，不影响本批纯 Python 验证，也没有触碰 ljy 原工作区或 `stage26-usd-compat` 的未提交修复。

本阶段仍不宣称真实三层 Goal episode 已接线；participant p2p service、native remaining-task 读回和物理 checkpoint/sham 仍是后续门禁。
