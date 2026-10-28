# room06 × liv-t-04 运行时搬运接线收口（2026-10-28）

## 本轮结论

主线 `HeteroCoord` 已补齐 room06 case-scoped transport resolver 接口。固定合同包
中的任务生成物提供物体身份与显式 `mass_kg`（未声明质量的书批不会从数量或尺寸猜测）；
promoted runtime roster 提供实例身份，质量/负载能力仍只从对应 source resume 读取。
合同包的 canonical SHA-256 在绑定时再次校验，篡改或身份不完整均在模拟器启动前
fail-closed。

本轮接线包含：

- `TransportAdmissionResolver` / `TransportOrchestrationExecutor` 支持 case-selected
  `allowed_robot_ids`；完整 27 台名册可以作为输入，但只有合同 `plan` 选中的实例进入
  resolver，未选实例的命令拒绝；
- `benchmark/room06_transport_runtime_v1.py` 将已验证的 room06 fixed package、promoted
  roster 和 source resume 绑定为 `Room06TransportBinding`；executor ledger 只记录运行时
  provenance，不冒充 evaluator-owned receipt material；
- `bind_orchestration_ledger_provenance()` 将 `TaskRunner` 返回的 orchestration ledger
  以 schema/version/digest/event-count 引用写入 receipt streams 的
  `identity_records.orchestration_ledger`。它只绑定调度 provenance，不把 ledger 事件解释
  成里程碑、持有、success 或任何论文事实；ledger 篡改和已有引用冲突均 fail-closed；
- `room06_gpu_receipt_seam_v1.build_terminal_payload()` 与 GPU probe 的
  `run_full_task()` 接受同一份显式 orchestration result，将上述 provenance 传入
  map-ready terminal payload；不提供时保持旧行为，绝不从 GPU 状态推断 ledger；
- `benchmark/tests/test_room06_transport_runtime_v1.py` 覆盖选中实例边界、source resume
  负载权威性、executor ledger → receipt identity 接缝和 package/ledger digest 篡改拒绝。

## GPU 脏工作树处置

`/tmp/heterocoord-room06-real-gpu-MTFYKn`（`codex/room06-real-gpu-vertical-20261027`）
的 HEAD `e5a95666` 是当前主线的祖先，落后 `origin/HeteroCoord`，没有领先提交。该树的
未提交内容是旧基线副本、诊断运行 artifacts、地图临时锁和一份历史文本差异；主线已经
包含较新的 GPU hook/probe、map-ready handoff、room06 USD 与诊断记录。因此它不能直接
推送：工作树不干净且把旧接口/运行证据混在一起，整树合并会回退主线或把 diagnostic-only
产物误当源码。ToDesk 不属于此次阻塞，也未被终止。

GPU 诊断结果仍保留在对应 `artifacts/project_runtime/` run manifest 中；它证明了真实
CUDA 场景加载/有限步进，不证明 liv-t-04 完成、机器人资格或论文样本。正式门
`physical_qualified`、`rankable`、`paper_score_eligible` 继续为 `false`。

## 验收与下一步

受影响测试：

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
task/tests/test_transport.py task/tests/test_transport_runtime.py \
benchmark/tests/test_room06_transport_runtime_v1.py
```

结果：受影响 room06/transport/receipt 集 151 passed；`git diff --check` 通过。
`env_isaaclab` 当前提供 `torch 2.7.0+cu128`
与 `lark 1.3.1`。

推送前仍按主线规则跑一次 benchmark 全量。语义地图 preflight 仍需真实跨层拓扑边与
transition node；地图到位后再将 binding 接到 observer/skill-session source，运行
首个真实 room06 case，并依次生成 terminal receipt、oracle/obligation 与论文结果数据。
