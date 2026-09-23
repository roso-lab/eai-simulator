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

## A2 资产布局复验

`tools/assets/prepare_restaurant_benchmark_assets.py` 现在支持在目标 staging
目录先复制四个冻结产品，再把 `--source-usd` 放到目标 overlay 的既有相对引用
位置；不会改写 overlay 或任何产品哈希。冻结引用要求 staging 目录位于同一深度，
例如本机 `/home/pupil/a/b/c/d/products-candidate-v7`、ljy
`/home/ljy/a/b/c/d/products-candidate-v7`。两台机器均报告：

```text
hash_resolver=passed
source_closure_exists=true
product_validation.validated=true
proxy_count=1398
occupied_cells=89051
source_sha256=618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b
```

这证明了干净 staging 到正式 episode 启动前的 composition/preflight 门禁可复现；
尚未启动正式餐厅业务 episode，也不改变餐厅 release gates。

因此本阶段只确认静态/单元回归和评分证据接线无回归，不抬升任何 `execution_validated`、`business_outcomes_validated` 或 `scoreable` 标志。
