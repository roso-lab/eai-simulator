# HeteroCoord 生产闭环阶段收尾（2026-10-26）

## 阶段结论

本阶段已经把工程从“合同/oracle 可计算”推进到一条可审计的 CPU 生产接缝：

`case manifest → evaluator artifact → sealed raw receipt → oracle/success/obligation reduction → fixed-panel 入口`

当前实现可以验证真实实验数据是否具备进入论文计算层的身份、字节与语义条件，但还不能宣称已经产出论文正式数据。原因不是缺少分数函数，而是三类上游证据尚未同时闭合：完整机器人池历史证据、真实 GPU 三路恢复一致性、以及真实 evaluator terminal receipt / fixed-panel 支持目录。所有正式资格门继续保持 `false`。

## 1. 语义地图与真实 case 输入线

已完成：

- `origin/HeteroCoord@7f44a187` 已包含经出题人验收的 room_06 语义地图修正；12 个语义点有非空唯一命名和导航绑定，50 个拓扑节点、63 条边，save_id 版本链一致。
- 审计器 v2.1 不再要求已取消的 `isaac_binding`，也不检查 `robot_validated`；四层语义集合为空不再自动判败，而是按当前锚点所需谓词输入逐项核对。
- `liv-t-04` 已登记为 room_06 跨层图书接力与重物联合搬运锚点，horizon 为 15000 tick；联合承托输入采用逻辑持有区间，不读取物理接触。
- case object binding v2.1 支持任务脚本生成的 `book_batch` / `heavy_object`，不要求伪造现有 USD prim；映射与 scene digest 一起进入 manifest digest。
- room_06 审计/合同专项最终证据为 35 项通过；map 单项 `case_ready=true` 只表示地图输入满足本锚点要求，不代表完整 case 已可运行。

未完成：

- 尚无 room_06 × liv-t-04 的真实 evaluator terminal artifact/receipt。
- task-generated 对象仍需真实任务布置程序给出 definition digest；如果改用场景现有道具，才需要另行确认 USD prim。
- catalog 扩展、场景键重命名、manifest builder 与远端地图应在独立干净树做一次联合发布回归；不得从当前大脏工作树整树提交。

## 2. Oracle、receipt 与论文计算接缝

已完成：

- `contact_count` 保留兼容名称，但 V3 唯一观测量为 `hold_intervals`，对应 held_object 或联合技能会话账本。
- predicate scoring role 已集中为单一权威函数，receipt material、obligation reducer 与 adapter 共用；`liv-p3-01/pallet_requires_two_carriers` 明确归入 `safety_success`。
- raw receipt 的 envelope hash 现包含 `artifact_manifest_digest`；production readiness/normalization 同时核对 sealed digest、调用方 manifest 和磁盘实际字节。
- `safe_stop` 必须有 certified safety 标记、对应真实 artifact 字节的 certificate digest 且 `success=false`；`team_state_infeasible` 同样要求真实 proof artifact 且 `success=false`。
- fixed-panel package 未进入时，case readiness 无条件报告 `PAPER_FIXED_PANEL_PACKAGE_MISSING`，不会把单个 case 或 raw receipt 表面升级成论文支持。

独立执行证据：ZCode GLM-5.3/max 只读运行，8 个生产模块 `py_compile` exit 0；receipt 专项 `15 passed`；五文件 oracle/receipt/CP2/joint-hold 回归 `58 passed`。测试前后目标文件状态一致，未启动 GPU、CUDA、Isaac 或 Kit。

发布门收口记录：原计划的 Sol 只读复审在会话中止时未收口。本阶段推送改由 ZCode 在用户授权下完成：以最新 `origin/HeteroCoord@7f44a187` 为父，在独立干净工作树复验（生产闭包 9 个测试文件 `98 passed`、机器人池套件、roster 校验、`py_compile`）通过后推送。当前工作树的旧候选提交 `442b3cc7` 仍作废，不得推送。推送范围 = 生产闭包 31 个代码文件（7 修改 + 24 新增）+ 机器人池登记套件与用户裁决记录 + 进度文档。

## 3. 机器人池线

已完成：

- 27 台运行时名册目标已经按装配政策 v3.1 建模，排除 Carter+Z1 与 M20+Z1，不保留 deprecated 尾项。
- 历史集成证据逐台登记后，15 台找到精确可追溯来源并通过统一 receipt validation（收据校验）：`hc-rt-001/004/005/006/007/009/010/012/014/018/020/021/025/026/027`，行状态为 `qualified_historical_integration`。
- 其余 12 台（`hc-rt-002/003/008/011/013/015/016/017/019/022/023/024`）已由用户于 2026-10-26 最终裁决全部通过。注册机制为此扩展了用户裁决路径：收据引用已提交的裁决记录 `benchmark/robot_resume/RUNTIME_RULING_20261026.zh-CN.md`，行状态为 `qualified_user_ruling`，与 15 台的 `qualified_historical_integration` 区分开。
- 机器人历史证据专项为 18 项通过；没有启动新的逐台 Isaac 验证，没有覆盖生产名册。

当前判断：机器人池名册 27/27 达成行级 `runtime_qualified`（15 台 `qualified_historical_integration` + 12 台 `qualified_user_ruling`），但名册 formal gates（正式资格门）仍保持四项 `false`，用户裁决不翻转它们。RP-C 运行器保留给未来新增条目。

## 4. GPU 三路恢复线

已完成：

- A 不间断 / B 隔离进程恢复 / C 共享会话槽位恢复的离线编排、owner graph、比较字段和 fail-closed 收据结构已经实现。
- 最新离线三路收据三对比较通过，但始终标记 `diagnostic_only`；它证明协议和比较器，不证明 Isaac/PhysX/GPU 资格。
- 公开 benchmark 的默认 admission 已纠正为 permissive：ToDesk 只是可选 provenance，其他 GPU 进程进入 inventory；只有 benchmark 自身 lease 或显存余量不足才拒绝。需要干净 compute inventory 的认证运行必须显式选择 `strict_exclusive`。

真实运行现状：`gpu-three-way-concordance-20261025-22` 在模拟进程已经启动后，于 A 路第 9 个逻辑步的 evaluator command canonicalization 处失败；B/C 未启动，没有 concordance/qualification 报告。该失败收据必须保留，不能静默重跑或升级。修复后的真实 A/B/C 仍未完成，因此 physical qualification、rankable 与 paper-score eligibility 全部为 false。

## 5. 距离论文数据还缺什么

要达到“上游数据一到即可直接计算”，仍需依次闭合：

1. 用已审计语义地图、可用机器人子集、合同/oracle、replay/evaluator identity 生成唯一 case manifest；
2. evaluator 产生真实 terminal artifact 和 sealed receipt，包含完整 milestone/event/zone/hold/identity streams；
3. 形成注册的 fixed base-instance × repeat × condition/relation cell panel，而不是把 receipt 行均值当 estimand；
4. 补齐 D4 healthy/loss closure、D5 source/target/sham/causal attempt 与每 family 的统计支持、D6 独立 calibration；
5. 修复 GPU A 路 command canonicalization 后完成真实 A/B/C 三路一致性；
6. 由 physical、statistical、release 与 replay gates 独立复核后，才允许讨论论文正式表格或排名。

补充一条用户裁决（2026-10-26）：EMOS 零号被测系统（zeroth tested system）升格经用户裁决暂缓执行（先不进行），不进入本阶段闭合范围。

## 6. 发布策略

- 阶段代码只以最新 `origin/HeteroCoord` 为父构造选择性提交；不得 pull/rebase/reset/clean 当前用户工作树，也不得改变其既有索引。
- 先在独立干净工作树复验生产闭包和依赖导入，再确认远端未漂移并快进推送到 `HeteroCoord`。
- GPU 失败 artifacts、12 台凭用户裁决（而非新的逐台 Isaac 验证）转正的资格依据和地图并行脏产品不作为“完成结果”混入发布声明；进度文档保留它们的证据等级。
- 所有正式资格门、认证 flags 和论文 eligibility 在完整证据链通过前保持 `false`。

## 阶段推送记录（2026-10-26）

- 父提交：`origin/HeteroCoord@7f44a187`；推送分支：`HeteroCoord`。
- 推送内容分三块：生产闭包代码（31 个代码文件，7 修改 + 24 新增）；机器人池登记套件与用户裁决记录（名册 27/27 行级 `runtime_qualified`）；本组进度文档。
- 推送前在独立干净工作树完成验证：生产闭包回归 9 个测试文件 `98 passed`；机器人池登记套件通过；roster 校验通过；生产模块 `py_compile` exit 0。
- 旧候选提交 `442b3cc7` 作废，未进入推送。
- 正式资格门保持 `false`：本次推送只交付工程闭包与登记状态，不改变任何论文资格声明。
