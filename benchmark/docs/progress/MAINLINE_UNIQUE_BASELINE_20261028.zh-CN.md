# 主分支唯一基线与未合并修改处置（2026-10-28）

## 当前基线

- 本地 `HeteroCoord` 与 `origin/HeteroCoord` 均为 `7e78ef04`，工作树干净。
- 本轮已执行 `git fetch origin HeteroCoord`；远端没有待拉取提交。
- 后续工程开发、受影响测试和推送统一以 `HeteroCoord` 为基线。其他分支保留为历史审计源，不再作为并行主线。
- `physical_qualified`、`rankable`、`paper_score_eligible` 继续保持 `false`。

## GPU 脏改动的性质

GPU 工作树 `codex/room06-real-gpu-vertical-20261027` 比当前主线落后 36 个提交，且没有领先主线的新提交。其工作树中的未提交内容包括：

1. room06 GPU hook/probe 的旧基线副本；对应功能已由主线的新注册接缝和生产前置代码吸收。
2. `artifacts/project_runtime/gpu-room06-vertical-*`、`gpu-three-way-*` 下的 manifest、checkpoint、`run_failure.json`、stdout/stderr 和诊断报告；这些是 diagnostic-only 运行证据，不能当作源码提交，也不能升级资格。
3. 语义地图导出物、`.write.lock`、临时日志和未完成脚本；它们缺少完整依赖闭合与受影响回归，整树推送会混入重复地图和旧接口。

因此 GPU 工作树不能直接推送，不是因为 ToDesk，也不是因为某个共驻 GPU 进程。ToDesk 未被终止；本次审计没有启动 GPU 或 telemetry transmitter。GPU 证据应按单次 run manifest 和失败原因继续留在 artifacts，待真实运行完成后再单独登记。

## 已审查的分支处置

| 来源 | 当前判断 | 处置 |
|---|---|---|
| `oracle-predicates-binding` | 主线已有后续 oracle/receipt 版本 | 不回拷旧文件 |
| `teamweaver/contract-adapter-20261027` | 主线已有后续适配器提交 `1484495b` | 不重复合并 |
| `codex/room06-livt04-integration` | 主线已吸收 room06 接线及后续修订 | 不重复合并 |
| `codex/room06-real-gpu-vertical-20261027` | 无领先提交，只有旧基线脏改动和诊断产物 | 保留证据，禁止整树合并 |
| `codex/stat-report-producers-v1` | `11b6f03e` 已被主线 `dfd75ecc` 等后续修订 supersede | 以主线版本为准 |
| `codex/d4-dual-first-event-dac7b94` | 主线已有 `a467e60c` 及 `ddb9f688` 修订 | 以主线版本为准 |
| `codex/paper-alignment-*` | room06 fixed package 与论文身份已有更新版本 | 以主线版本为准 |
| `paper-difficulty-dac7b94` | 旧论文 SHA `dac7b942` 的未提交改动；主线已含 `difficulty_contract_v1` 和当前论文身份；主线受影响集复验 `40 passed` | 保留原工作树，禁止覆盖；待需要时按当前 `PAPER_COMMIT` 做小补丁 |
| `semantic-map-tool-release` / `tool` | 基于旧线的大量 vectorized/recovery 改动，包含重复地图和旧运行时接口 | 不整合；使用主线现有 topology authoring/shared-session 代码 |

## 规则与下一步

主线只接受两种输入：

1. 基于 `HeteroCoord` 的已提交代码，带受影响测试证据；
2. 明确标记为 `diagnostic-only` 的运行证据，绑定 manifest、输入摘要和失败原因。

未提交的 GPU、语义地图和旧论文工作树不再作为开发基线。接入完整语义地图后，下一条主线任务是补正式场景的 object/robot resolver，接通 receipt/ledger，再用 room06 × liv-t-04 做首个真实 case；日常只跑受影响集，推送前跑一次全量套件。
