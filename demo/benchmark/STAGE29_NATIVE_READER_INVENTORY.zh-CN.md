# 阶段 29：五场景 native remaining-task reader 盘点

日期：2026-09-24  
审查证据：`/home/pupil/Documents/Codex/agent-team/workspaces/stage29-native-reader-inventory-20260924/handoffs/stage29-native-reader-inventory.md`

## 结论

五个场景目前都没有完成 `GroundedRemainingTaskOracle` 所需的 native reader、实体版本
绑定和替代路径完备性证书。`goal_native_oracle_registry.py` 现在是结构与发布总闸，不能被
解释为五个场景已经有真实二元真值。所有缺口继续写为 `oracle_status=failed`、
`feasibility_label=null`，不进入 IRR/FIR 或其他正式分母。

| 场景 | 已有可信事实 | 仍缺的正式 reader/证书 |
|---|---|---|
| Factory | 物体、占用和部分静置/楼梯诊断 | Goal native backend、异常观察、服务状态、维护交接、替代路径 |
| Warehouse | attachment 失败和部分对象/通道诊断 | 库存/集货、dock receipt、closeout，以及“已到 dock 只差回执”中间态 |
| Modern House | PhysX 对象、导航和 attachment 诊断 | 稳定放置、整条路径净空、撤离、`time_s`/实体版本绑定 |
| City | 人员、车辆、报告和连续通路的局部 native 证据 | 完整 runtime state/provider、实体版本和合法替代路径证书 |
| Restaurant | 单 Carter 到达、双区观察、relay/readiness receipt | 八机候选池准入、团队状态、替代路径与正式 oracle provider |

现有静态 probe、作者 witness、scene binding、occupancy 和 attachment failure 只能作为
诊断证据；它们不等价于剩余任务可行性，也不能把模型失败或执行器失败转换为部署判断。

## 回归

本机轻量全量命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test
```

结果：`2973 passed, 1 failed`。唯一失败是
`test_factory_evaluator_fixture.py::test_inspection_fixture_matches_factory_scene_and_two_views`，
原因是当前外部 Factory USD SHA256 为
`93e731d942b419b4332ce233a1e8c28ea8d02c9fdf157df81f6bf128430479d5`，而冻结 evaluator
fixture 要求另一版本。未修改 USD、fixture、题包或哈希；该项归类为资产版本准入缺口。

本批重点聚焦回归仍为 `116 passed`，任务分配画像提交为 `8f588e7` 并已推送
`origin/benchmark-task`。三个发布标志继续保持 false。

新增只读命令 `tools/assets/check_factory_fixture_admission.py`，可以同时核对工作树资产
和 provider cache 候选，输出实际 SHA256、匹配候选与 `mutated=false`。本机工作树为
`93e731d9…`，HF `v0.1.0-beta.1` cache 为 `09890201…`，均不匹配冻结值
`783f24ff…`；因此当前结论仍是 `blocked:factory_usd_hash_mismatch`，没有可信版本可供
替换。

## 下一步

1. 先解决 Factory 受信 provider/资产版本准入或形成可复现阻断报告；不放宽哈希。
2. 按 Warehouse → House → City → Restaurant → Factory 的事实源可用性，逐场景冻结
   `predicate_bindings`、终局谓词和 measurement points。
3. 只在 reader、替代路径完备性和 native 证据全绿后，运行双机动态部署回归。
