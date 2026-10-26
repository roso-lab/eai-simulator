# Goal 持续推进阶段 3：地图到位即可执行的框架收口（2026-10-27）

## 本阶段结论

Goal 保持 `active`。远端 `HeteroCoord` 已推进到 `bea02eb8`；本阶段完成了结果计算链的固定诊断和一个会阻断全量回归的历史 fixture 迁移。工程侧的“数据进入后如何得到论文指标”接缝已经可重放，但尚未把诊断/CPU 夹具升级为论文正式数据。

## 已完成并验收

### 结果计算链

ZCode（GLM-5.3 / max，`env_isaaclab`）按固定任务运行：

- `test_production_results_v1.py`：3 passed；
- `test_results_pipeline_v3.py`：14 passed；
- `test_paper_panel_v1.py`：26 passed；
- 四个结果模块 `compileall`：exit 0；
- 临时端到端导出含 `report.json`、`paper_panel_package.json`、`paper_projection.json`、`output_manifest.json` 等 10 个文件；六个维度均为 `valid`，但 scope 是 `measured_unqualified`，三项正式 gate 仍为 `false`。

临时 pytest 输出目录会被后续外部 pytest 轮换，不能作为仓库证据；以上只证明字节寻址、panel projection、D1–D6、export 和 verify/replay 的代码接缝可用。

### 目录/论文身份迁移

P4 历史映射 fixture 原来仍使用旧论文 SHA `1f329494...`，会在语义断言前统一失败。已迁移到当前冻结 SHA `0b006d4efcd25d5160d4b0a6e75cddd9e5265e44`，同步刷新 package、mapping source digests 和冻结 receipt，且不改变任何资格字段。专项回归：

```text
benchmark/tests/test_catalog_mapping_v3.py
benchmark/tests/test_task_catalog.py
29 passed
```

## 明日地图交付后的唯一生产顺序

1. **最终地图只读审计**：确认协作者写入已结束；校验 save-id/version chain、所有 manifest file bytes、semantic point 命名唯一且有导航绑定、topology 节点/边和 liv-t-04 的 12 个语义点。接受快照只能作诊断参考，不能复用其 digest。
2. **重建 case manifest**：绑定最终 map/topology digest、当前 canonical 31-anchor/contract artifact、promoted 27/27 roster、`book_batch_01` 与 `heavy_object` 任务级身份；记录新 manifest digest。
3. **EMOS CPU dry-run**：调用 `initialize(H_r, o_r)`，检查预算、完整名册和 decision provenance；不把 dry-run 当作物理成功。
4. **真实 evaluator terminal artifact**：从 task runtime 读取 milestones、task events、zone intervals、逻辑 held-object intervals、identity records、integrity 和 D1/D4/D5/D6 facts；缺事件、坏哈希、未知身份均 fail-closed。不得从进程退出、锁服务 grant/deny 或 caller score 推断科学事实。
5. **单 case CPU 闭环**：terminal artifact → receipt bridge/producer → oracle/obligation reduction → fixed panel projection → D1–D6 → export → verify/replay。此步通过后才有资格安排 native CUDA 运行。
6. **真实 GPU A/B/C**：直接按冻结命令运行不间断、隔离进程恢复、共享会话槽位恢复；核对 requested/actual device 与 UUID、checkpoint identity、终局 receipt 和三路 concordance。ToDesk/显示服务不进入 benchmark 语义准入，也不因其存在中断实验。

## 仍未完成的硬缺口

- 最终完整语义地图尚未交付；
- room_06 × liv-t-04 真实 terminal artifact/raw receipt 尚未产生；
- D2 C0–C3、D3 四类 relation ledger、D4 healthy/loss/recovery、D5 每 family ≥30 cases、D6 独立 calibration 目录尚未闭合；
- native GPU A/B/C restore/concordance 证据尚未形成。

这些缺口不能用 synthetic、CPU fixture、历史机器人资格收据或 route-readback 结果替代。正式资格门继续保持：

```text
rankable=false
paper_score_eligible=false
physical_qualified=false
process_isolation_qualified=false
native_full_state_qualified=false
```

## 变更边界与审计

- 未修改论文正文；
- 未 reset/clean/checkout 主工作树，也未触碰协作者地图写入；
- fixture 迁移为独立提交 `bea02eb8`，来源和派生哈希可由 `git show` 复核；
- GPU/Isaac/Kit 本阶段未启动；
- 下一次真正运行必须以最终地图审计通过和真实 evaluator 证据落盘为前置条件。
