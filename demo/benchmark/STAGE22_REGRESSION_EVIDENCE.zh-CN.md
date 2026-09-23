# 阶段 22 回归证据

日期：2026-09-24  
提交：`5d62357`（已推送 `origin/benchmark-task`）

## 通过结果

- 本机 `env_isaaclab`：
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test/test_benchmark_*.py`
  **2257 passed，236.52 秒**。
- ljy 隔离工作树 `/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e`，
  系统 Python 3.10：通信/成本/部署/位置/相机/runner/checkpoint/provenance 专项
  **172 passed，1 deselected**；另有不含 provenance 的先前专项 **145 passed**。

## ljy 覆盖边界

ljy 完整 `test_benchmark_*.py` 收集时有 4 个文件因环境缺少 `torch`/`pxr` 无法导入，
不能把这些测试的原生 Isaac/USD 覆盖记为通过。`test_benchmark_provenance.py` 中一个
USD closure 单测同样因缺 `pxr` 排除；其余可运行专项在 Python 3.10 通过，哈希兼容
修复已验证。

## 解释与发布门禁

这些是离线契约和软件回归证据，不是原生业务 episode、native remaining-task oracle
认证、真实三层 Goal transport 接线或 PhysX checkpoint/sham 证据。餐厅 A2 资产闭包、
五场景 native oracle 和真实保存/恢复仍未完成；通信 participant service 仍等待接口
拍板。

```yaml
scoreable: false
execution_validated: false
business_outcomes_validated: false
```

用户文件 `source/EAI_hmrs/EAI_hmrs/envs/my_env.json` 与根目录协议文档未提交。
