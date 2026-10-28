# SADCHER 适配检查与修复 — 2026-10-28

已修复 adapter 默认权重和状态/反馈入口问题，原生算法、匹配器与权重文件未改。
最终 **68 passed（SADCHER 60 + 共享入口/完整性 8），0 failed，0 skipped**。
本轮验收范围是原生二维仿真中的多机器人 DAG 闭环，未启动 Isaac。

## 修复

1. 旧默认 model_0 权重与当前网络有 16 个 missing、84 个 unexpected、86 个 shape mismatch。
   默认改用上游 benchmark 已选用的 `hyperparam_2_8t3r3s/best_checkpoint.pt`，116/116 完整加载。
   显式参数或环境变量指定不兼容权重时仍报错，绝不在失败后自动替换。
2. 校验运行时机器人 ID、坐标和 float32 可表示性；IN_PROGRESS 要求有效 active owner、
   已完成前置、技能覆盖与原生到达判断；DONE 不允许非零剩余时间。
3. 重规划检查完整任务集合、反馈身份、重复反馈、失败与完成矛盾、最近派发 owner；
   live 状态与 runner 状态双向一致。完成状态写入时同步清零剩余时间。
4. 检查原生输出索引及机器人可用性。保留完整奖励、哨兵、coalition 和 checkpoint 来源。
   上游在任务仍 IN_PROGRESS 时输出 exit 的情况保留在异常决策中，拒绝将其当作完成。
5. 旧 disabled 入口的说明和默认权重记录同步更新；没有启用旧 fallback。

没有改网络、输入归一化规则、技能顺序、奖励、matcher、两个 filter 或 idle 决策。

## 实际运行结果

| 运行 | 结果 | 证据 |
| --- | --- | --- |
| 初始专用环境专项 | 35 passed | `baseline.xml` / `baseline.log` |
| 最终专项及共享回归 | 68 passed，135 条 PuLP 弃用警告 | `final.xml` / `final.log` |
| 默认配置独立 CLI | 完整加载，返回原生决策 | `default_cli.json` |
| TaskRunner + 持久化原生 Simulation | 3 机器人、3 任务，2 批/3 dispatch；tick 31 完成任务 | `final_evidence/unified_entry/run_orchestrated_native_feedback.json` |
| 同一仿真原生退出阶段 | tick 59 全部到达出口；两轮派发和退出阶段的 features/rewards/assignments 均与直接原生一致 | 同上 |
| 独立上游仿真 | 全任务与退出完成，未触及 worst-case timeout | `final_evidence/original_simulation_rollout.json` |
| 单 UR5 输入 | 原生 idle，0 dispatch；保留真实结果 | `final_evidence/ur5_pickplace_native_decision.json` |
| 双 UR5 输入预检 | move_left → ur5_1，ur5_2 idle；只是决策，未执行物理任务 | `two_ur5.preflight.json` / `two_ur5.task.json` |

新增状态清零测试的首次运行得到真实 native coalition，不能被当前单 owner runner 派发。
该测试已改为检查保留的 coalition 异常与清零后的状态，没有改算法或调整输入来强行派发。
首次结果 `1 failed, 59 passed` 保存在 `intermediate_guard_tests.xml/log`；最终测试通过。

## 完整性及远端核对

冻结源码 commit：`a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae`。
57 个源码/配置文件的汇总 hash 在修复前后完全一致：
`a1ab0f3a925758897639bed75b2da1e65bf26ca8052b3b8c13cbd13bedf7f60d`。
见 `source_before.json`、`source_after.json`，完整权重 hash 和汇总见 `summary.json`。

远端 `origin/HeteroCoord` 从本地旧引用 `07a5bb66` 更新至 `75a95615`，已 fetch。
新增提交包含 room06 逻辑执行、EMOS、D1 和地图编辑更新，没有 SADCHER 文件更新。
当前 `codex/cartesian-pick-place` 工作区有大量未提交改动；本轮未合并远端或改动这些并行工作。
因此这里的结果不代表已验证远端 room06 新执行链。

## 尚未覆盖

- 支持多个机器人执行不同任务及 DAG 依赖协作；同一个任务要求多个机器人共同执行时，
  当前 TaskRunner 单 owner 契约无法表达，保留完整原生 coalition 后明确拒绝。
- 单机器人 idle 是原生策略结果，不在出口强制改为任务分配。
- 上游 premature exit 被检测并拒绝，没有修改算法以恢复推进。
- 仅三个固定二值技能、共面几何、显式时长与原生单位速度；不是任意异构平台的全部任务覆盖。
- 无 Isaac 或接触抓取验收；无论文全任务成功率结论。

复现命令与输入契约见 `algorithm/system/coordination/sadcher/README.md`。
