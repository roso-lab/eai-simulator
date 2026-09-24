# 阶段 30：Factory 资产准入与双主机回归证据

日期：2026-09-24  
提交：`158246c`  
远端：`origin/benchmark-task`

## Factory 资产准入

冻结 evaluator fixture 要求 USD SHA256：

```text
783f24fff7e9bf6a3761aaf71957ffe848bd42cab5fc3a6d0cd87c2112026130
```

当前证据：

| 来源 | 状态 | SHA256/原因 |
|---|---|---|
| 本机工作树 `usd/scene/factory/factory.usd` | blocked | `93e731d942b419b4332ce233a1e8c28ea8d02c9fdf157df81f6bf128430479d5` |
| 本机 HF `v0.1.0-beta.1` cache | blocked | `098902013c85d0fc6a909a8a09a747e2eb2cc8b7bd2d8f160ce27ffd628d5c73` |
| ljy 原工作区 Factory USD | blocked | `93e731d942b419b4332ce233a1e8c28ea8d02c9fdf157df81f6bf128430479d5` |
| ljy 新隔离回归树 | blocked | `factory_usd_missing` |

使用命令：

```bash
python tools/assets/check_factory_fixture_admission.py \
  --asset usd/scene/factory/factory.usd \
  --fixture demo/factory_benchmark/evaluator/inspection_01.json \
  --candidate <provider-or-cache-candidate>
```

命令只读、报告 `mutated=false`，不复制或替换资产。没有找到可信匹配版本，因此不修改
fixture、USD、冻结哈希或题包；该项是基础设施/资产版本阻断，不是模型失败。

## 回归结果

本机命令（`env_isaaclab`，禁用 pytest 自动插件）：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test
```

结果：`2973 passed, 1 failed`。唯一失败是 Factory evaluator fixture 的冻结 USD 哈希不匹配。

ljy 在保留 NVD/City 运行资产的隔离树
`/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e`运行同一套命令，
结果：`2812 passed, 9 skipped, 2 failed`。两项失败均来自 Factory USD 不存在；其余失败项
未出现。该树的聚焦评分/通信/oracle/checkpoint 集合为 `67 passed`，餐厅 A2 candidate
检查为 `status=ready, hash_resolver=passed, source_closure_exists=true`。

所有结果只作为 raw/基础设施证据，继续保持：

```yaml
execution_validated: false
business_outcomes_validated: false
scoreable: false
```

下一步仍需获得冻结 Factory 资产或由资产维护方发布匹配 provider revision，之后才能
重跑 Factory 相关原生验证；不能用现有错误版本冒充通过。

