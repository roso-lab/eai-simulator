# 阶段 27：完整轻量回归结果

当前提交 `de2ee69` 使用 `env_isaaclab`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 和四个仓库 source 路径运行：

```text
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
python -m pytest -q source/EAI/test
```

结果：`2969 passed, 1 failed`，耗时约 6 分 58 秒。

唯一失败为 `test_factory_evaluator_fixture.py::test_inspection_fixture_matches_factory_scene_and_two_views`，原因是当前外部 `usd/scene/factory/factory.usd` 与冻结 evaluator fixture 的 `usd_sha256` 不一致。该 fixture 属于外部资产版本门禁；本批没有修改 USD、fixture 或哈希，也没有把它归因于模型决策。其余 benchmark、通信、评分侧车、oracle、checkpoint、餐厅资产和工具测试均通过。

首次运行未加入 `source/EAI_env_diy` 时在收集阶段缺少 `EAI_env_diy`；补齐仓库 source 路径后才得到上述完整结果。ROS pytest 插件自动加载仍需禁用，因为环境缺少 `lark`。

因此本阶段完成轻量代码回归，但仍不能宣称正式 Isaac episode、Factory 资产版本门禁或 scoreable 发布已完成；三个发布标志继续为 `false`。

ljy 端在带有既有场景运行资产的隔离树 `/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e` 上同步同一提交后得到 `2805 passed, 9 skipped, 2 failed`。两项失败均为 Factory USD 文件缺失；其余测试通过。另一个从 git bundle 新建的纯代码树因缺少多个 `.gitignore` 资产产生大量连锁失败，未作为代码回归结论使用。
