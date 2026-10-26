# Goal 持续推进阶段 2：最终地图接入前的闭环就绪审查（2026-10-27）

## 结论

Goal 继续保持 `active`。工程已经具备“输入审计 → case manifest → evaluator terminal receipt → receipt bridge → oracle/obligation → D1–D6 → fixed panel → export/replay”的代码接缝；当前不能宣称论文正式数据已经产生，原因是最终语义地图和真实 evaluator terminal receipt 尚未同时到位。

## 本轮冻结与审计

- 已抓取 `origin/HeteroCoord` 最新远端状态。主工作树存在协作者/用户脏改动，当前不做整树 merge、reset、clean 或覆盖式 pull。
- 接受快照 `origin/HeteroCoord@7f44a187` 仅作为诊断参考：`living_room_06`、12 个语义点、50 个 ground topology 节点、63 条边、save-id 链一致，`floor1-bookshelf` 与三个 `floor2-bookshelf-*` 均存在。
- 当前工作树 `room_06/semantic_map/current` 的 `save_id=d471be5423da493e8c19d7b4a60d85c3` 仍是协作者未完成写入：拓扑为空、书架语义点缺失。因此禁止用它重建正式 manifest，也禁止启动真实 GPU。
- 接受快照配合历史证据转正后的 27 台名册，使用 `artifacts/robot-runtime-historical-20261027-r3/robot_runtime_roster.promoted.v1.json`（digest `33d92468c48bd1631fdc034d2d4fb01858294bce573308b1b7dd6542062836f4`）重新构建 manifest，与保存的 `artifacts/case-room06-livt04-20261027/case-manifest.json` 字节一致：manifest digest `ded55b75f3d6e3d73924e913984e3300fc0dfb5b040aaffb6841514a7cc5fbbb`。
- 该复现只证明 builder/审计器确定性和历史名册输入一致性；接受快照和 synthetic/CPU receipt 均不升级 physical、rankable 或 paper-score gate。
- ZCode 在 `env_isaaclab` 完成最终地图接入前专项回归：审计/物体绑定 `30 passed`，论文计算链（production results、results pipeline、paper panel、current paper binding）`52 passed`，五个生产模块 `compileall_exit=0`；未启动 GPU/Isaac/Kit，源码、测试、catalog、地图和 artifacts 均未修改。

## 地图交接后立即执行的顺序

1. 读取最终 `room_06/current`，核对 `save_id` 版本链、semantic/topology digest、12 个语义点、50/63 拓扑和 liv-t-04 所需书架点；任何失败留证并停止后续绑定。
2. 用同一 `binding.json` 和 promoted roster 重建 `case-manifest.json`，禁止复用旧 digest；记录新 map/manifest digest。
3. 先做 CPU 纵向闭环：EMOS `initialize(H_r,o_r)` dry-run → 真实 evaluator-owned terminal receipt material（milestone/event/zone/held-object/identity streams、D1/D4/D5/D6 facts）→ content-addressed artifact → oracle/obligation reduction → fixed panel → D1–D6 export → replay verify。
4. 对真实 receipt 逐字段检查：终局状态、缺事件 fail-closed、联合持有区间、截止期、身份集合、义务 ledger、artifact bytes 与 manifest digest；不允许以进程退出码或 EMOS 决策代替 evaluator 事实。
5. CPU 闭环通过后，才冻结 native CUDA manifest 和命令哈希，执行 A/B/C 三路真实 GPU；三路证据完成前所有正式 gate 继续为 `false`。

## 仍然是硬缺口

- 协作者最终语义地图尚未交付到 `current`。
- 真实 evaluator 尚未为 `room_06 × liv-t-04` 产生 terminal artifact/receipt；当前 receipt → D1–D6 仅为工程验收/诊断链。
- 论文固定统计目录尚未达到正式支持量：D2 C0–C3、D3 四种关系、D4 paired closure、D5 每 family 至少 30 cases、D6 独立 calibration。
- GPU 三路 native restore 的真实收据尚未形成；既有失败保留为诊断，不静默重跑或升级。

## Gate 状态

```text
physical_qualified = false
rankable = false
paper_score_eligible = false
process_isolation_qualified = false
native_full_state_qualified = false
```

本阶段不修改论文正文，不删除 `.write.lock`，不把接受快照冒充最终地图，也不把 CPU/mock/synthetic 结果升级为论文正式结果。
