# Stage 27：五场景 native remaining-task oracle 完备性审计

本阶段新增 `goal_native_oracle_registry.py`，作为评测器侧的只读总闸。它不生成剩余任务
可行性标签、不读取被测模型的 plan，也不创建注册、状态或见证计划。它只接受已经冻结的
evaluator-only registration、trusted state contract 和 release review，检查这些输入是否足以
支持五个场景的正式 native remaining-task 统计。

固定场景集合为：`factory`、`warehouse_nvd`、`modern_house_nvd`、`city`、`restaurant`。
调用 `audit_native_oracle_registry(...)` 时，可以传入三份按 `scene_key` 索引的映射/列表，
也可以传入 bundled records：

```python
report = audit_native_oracle_registry([
    {
        "scene_key": "factory",
        "registration": registration,
        "state_contract": state_contract,
        "release_review": release_review,
    },
])
```

审计内容包括：

- 五场景覆盖、重复场景和重复 measurement point；
- registration 与 trusted state contract 的 canonical SHA-256 封存值；
- registration、contract、release review 之间的 task/registration/contract 哈希绑定；
- `validation.status=verified`、`validation.scope=native_remaining_task` 和非空证据引用；
- 每个冻结 measurement point 是否都有 `oracle_status=verified`、
  `feasibility_label=deployable|not_deployable`、`validation_scope=native_remaining_task` 和
  非空证据引用；
- release review 是否明确报告 `oracle_release_ready=true`，以及 contract 的三个发布标志
  是否仍为 `false`。

报告是 JSON-ready 的 evaluator-only 对象，包含 `missing_scenes`、`duplicate_scenes`、
`duplicate_measurements`、逐场景 `invalid` / `failed` / `missing_measurement_ids`，以及最终
`oracle_release_ready`。缺场景、哈希漂移、authoring-only scope、空证据和失败/未验证
measurement 都 fail-closed。`unverified` 只会作为证据错误归档，永远不会变成正式的第三种
任务真值。该模块不会抬升 `execution_validated`、`business_outcomes_validated` 或
`scoreable`。

当前测试：

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 conda run --no-capture-output -n env_isaaclab \
  python -m pytest -q source/EAI/test/test_benchmark_goal_native_oracle_registry.py
6 passed
```

这是注册完备性门禁，不是五场景 native reader 或模型完备性证书本身。正式发布前仍须由
各场景提供可信状态 reader、冻结测量点及独立合法路径证书，并将它们接入该审计器。
