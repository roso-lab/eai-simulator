# Stage 24 回归证据

## 代码版本

- GitLab `benchmark-task`：`8d8f30cc0690e47c8e3a70fa327c239cab376698`。
- 包含 checkpoint/sham fail-closed 加固 `410a755`，以及评分侧车通信证据 episode 绑定与重复账本去重 `8d8f30c`。
- 用户文件 `source/EAI_hmrs/EAI_hmrs/envs/my_env.json` 和根目录协议文档未纳入提交。

## 本机

命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
-m pytest -q source/EAI/test/test_benchmark_*.py
```

结果：`2267 passed in 235.56s`，退出码 0。

## ljy 隔离工作树

使用 git bundle 同步到 `/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e`，确认 detached HEAD 为 `8d8f30c`，未触碰 ljy 原仓未提交内容。使用 `/home/ljy/miniconda3/envs/env_isaaclab/bin/python`，并显式设置只读的 controller 源路径 `EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller`。

结果：`2269 passed in 87.97s`，退出码 0；`pxr` 可导入，未出现缺依赖或失败测试。原始 stdout/stderr/exit/head 保存在团队工作区：
`/home/pupil/Documents/Codex/agent-team/workspaces/benchmark-stage22-comm-contract-20260924/artifacts/stage24-ljy-regression/`。

## 仍未达到发布条件

- 五场景 native remaining-task reader 和完备性证书尚未完成，作者 fixture 继续为 `oracle_status=failed` / `feasibility_label=null`。
- checkpoint native restore/replay provider 尚不存在；当前校验器只保证结构、哈希、编键 RNG sham fixture 的 fail-closed，仍保持 `raw_only` 与 `not_protocol_score=true`。
- 餐厅 A2 本机冻结 overlay 的源闭包路径仍需可复现的可写布局准备；本阶段没有改 hash 或题包语义。
- E2 机间 participant service / 多端点适配器仍停留在已拍板前的接口设计阶段。

因此本阶段只确认静态/单元回归和评分证据接线无回归，不抬升任何 `execution_validated`、`business_outcomes_validated` 或 `scoreable` 标志。

