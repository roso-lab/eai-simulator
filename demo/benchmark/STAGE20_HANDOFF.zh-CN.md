# 阶段 20 交接：先拿第一条正式 Episode

日期：2026-09-22

本阶段的目标是把“真实运行验证”从全场景同时收敛，调整为先取得一条可评分前的正式证据链。City 或 House 任一场景先完成 participant `GoalSession`、原生 Isaac、业务 evaluator、算法 trace 和 GUI/像素证据，即作为下一阶段的第一条正式 episode；Warehouse 路线几何问题继续独立记账，不再阻塞这条最短路径。这个调整只改变工作顺序，不改变发布门禁：四场景正式集、held-out 审核和 `scoreable` 仍需全部满足后才能发布。

## 本阶段完成

Warehouse 的 `--member-exit-slice` 已接入 `paired_native_probe.py`。新的 CPU Isaac 运行产物为：

- 目录：`tmp/goal-paired-member-exit-20260922-v6/`
- run：`efd555cf49a14018b0e9b9c50f4515ad`
- 报告：`paired_native_report.json`
- 报告 SHA256：`632966d8566dd4372d3dafacbe221b763e907f9e3fda738181e7fe42a9372f26`
- episode：`warehouse-formal-paired-recovery:efd555cf49a14018b0e9b9c50f4515ad`

报告确认：双机 joint 脱离、剩余单机的 `member allocated load exceeds public payload` 失败、失败准入前后箱体同一 PhysX step 零位移、`member_exit`、`member_join`、双机重新获取，以及 `reassignment_latency_s=0.019999999552965164`。该探针仍明确 `formal_route_admission=false`、`execution_validated=false`、`scoreable=false`，因为它没有完成正式路线、目标区域和 dock 业务 evaluator。

新增 `goal_external_adapters.py` 提供两个受控适配器：TeamWeaver 通过真实 `TeamWeaverPipeline` 与 `DynamicMIQPAllocator` 生成团队选择，EMOS 通过真实 `EMOSDiscussionManager` 的 assignment-policy 边界生成团队选择。两者只输出现有 Goal action envelope；不接触私有 PhysX 状态。离线首次动作翻译已通过，原生 City/House episode 尚未完成。

当前 Warehouse 原生适配器诊断也已完成：TeamWeaver 目录 `tmp/goal-paired-teamweaver-20220922-v5/`（实际目录名为 `tmp/goal-paired-teamweaver-20260922-v5/`），run `f2dcb622308243cdb1968b81189b241c`，报告 SHA256 `5a00836196eaea3d796631ff3091ed6179cc14a409631aa56ab171506392b081`；EMOS 目录 `tmp/goal-paired-emos-20260922-v1/`，run `361f39872fbe460bbdb57239f796f69a`，报告 SHA256 `bdfa3e89ea268d09c11213f732ffd939c070587c3331cdcf962ba3a95f2bc747`。两份报告都为 `diagnostic_completed`，同一 GoalSession episode 中记录算法初始化、首次分配、公开 payload 失败和 replan trace；两者均不是正式路线或业务 evaluator 证据。原生 worker 必须把 `algorithm/TeamWeaver`、`algorithm` 放入 `PYTHONPATH`，否则会得到可复现的 `ModuleNotFoundError: TeamWeaver`。

## 反思

约七成投入用于 runner、City 契约重设计和第一次原生证据，这些工作暴露了纸面契约与物理现实的真实差异，投入合理。约三成投入在 Warehouse `protoIndex` 和已知路线几何 blocker 上反复测量，边际收益低；后续限制为一次有明确 SDK/property 负例的审查，不能继续无限扩大。

此前 `/goal` 的“四场景全部通过才停止”适合最终发布门禁，不适合当前开发节奏。现在使用两层出口：阶段出口是 City/House 一条完整正式 episode；发布出口仍是四场景、失败/恢复、视觉、业务和 held-out 证据齐全。所有中间报告继续 fail-closed。

## 下一步顺序

1. 选择 City 或 House 中几何和业务 evaluator 更接近闭环的场景，建立持久 Isaac session 与 `GoalSession` 的同一 episode 绑定。
2. 将 TeamWeaver 和 EMOS 适配器接到该 runner，记录初始化、团队选择、计划、失败观察和重规划 action trace。
3. 写入原生技能 receipt、动态观察、业务 evaluator 结果和 GUI/像素归档；用独立脚本核对 episode ID、源码 hash 和报告 hash。
4. 第一条正式 episode 通过后，再处理另一场景；Warehouse 路线从起点半宽、目标区实测绑定和双机 swept occupancy 三项逐一修复。
5. 每个可复核阶段单独提交到 GitLab `benchmark-task`，提交信息包含运行命令、产物路径、SHA256、测试结果和仍为 pending 的门禁。

复现成员退出诊断：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
-m demo.warehouse_benchmark.paired_native_probe \
--output-root tmp/<new-dir> --device cpu --member-exit-slice

PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
-m demo.warehouse_benchmark.paired_native_probe \
--output-root tmp/<same-dir> --device cpu --member-exit-slice \
--execute --timeout-s 600
```
