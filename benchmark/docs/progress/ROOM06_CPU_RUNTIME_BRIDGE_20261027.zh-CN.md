# room_06 × liv-t-04 CPU 运行时接线阶段收尾（2026-10-27）

## 结论

`room_06 × liv-t-04` 已完成真实 Isaac 运行前所需的 CPU 侧接线：发布场景可通过逻辑路径和固定摘要解析，EAI/Isaac 场景目录可以选择该场景，Go2 楼梯控制器拥有场景自有的单网格 raycast 代理，评价器只从 observer-owned 的已完成技能会话与完整联合持有快照建立证据。

本阶段没有启动 Isaac Sim、Kit、SimulationApp 或任何 CUDA 任务。真实 GPU 场景加载、机器人/技能执行、终局 receipt 与 production seam 仍待 GPU 可用后完成；所有正式资格门保持 `false`。

## 已完成实现

1. 场景资产解析：
   - 公共逻辑路径：`scene/room_06/room_06.usda`；
   - 冻结 SHA-256：`e67affcbd6d67941feb8bc7728d6be4cd74ad63d414a8b65db771337b76e300a`；
   - 缺文件或摘要变化均 fail-closed；发布代码不写入本机绝对仓库路径。
2. 场景注册：
   - `room_06` 已进入资产需求、场景资源目录、EAI 选择目录和 Isaac 交互环境构建器；
   - 加载时去活 USD 内嵌 `PhysicsScene`，由 InteractiveScene 持有唯一运行时物理场景；
   - 合并场景自有 CollisionAPI 网格为不可见、三角化的 raycast 代理，禁止复用其他场景的碰撞代理。
3. 观测桥：
   - 里程碑只接受 schema 完整且状态为 `completed` 的 observer-owned 技能会话事件；
   - 命令、锁服务 grant/deny 和非完成状态不能推进里程碑；
   - 联合搬运只接受覆盖冻结名册的 `held_object` 快照，未分配机器人不能贡献事件或联合持有证据；
   - 单机持有、持有中断、缺采样、未知机器人、同会话篡改均 fail-closed；
   - 同一已接受会话的迟到重放保持幂等。

## 只读 USD 事实

使用 `env_isaaclab` 的 USD Python 绑定进行纯 CPU 检查：场景可打开，defaultPrim 为 `/World`，Z-up，`metersPerUnit=1.0`，共 329 个 prim、1 个内嵌 PhysicsScene、78 个 CollisionAPI prim；23 个纹理依赖全部可解析，无 unresolved dependency。该检查不等同于 Isaac/PhysX 运行资格。

## 受影响测试

ZCode GLM-5.3/max 在 `CUDA_VISIBLE_DEVICES=''` 下执行：

```bash
EAI_USD_ROOT=/home/pupil/HeteroCoord/usd \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  benchmark/tests/test_room06_observation_bridge_v1.py \
  benchmark/tests/test_room06_task_runtime_v1.py \
  benchmark/tests/test_room06_terminal_material_v1.py \
  source/EAI/test/test_room06_runtime_scene.py
```

结果：退出码 `0`，`120 passed / 0 failed / 0 errors / 0 skipped`。此前直接导入 Kit 依赖导致的 `carb` 环境假失败已改为对当前 worktree 构建器做 AST 契约检查；没有通过切换到错误 editable install 掩盖问题。

推送边界全量同时暴露了一个派生工件接缝：本阶段向 `env_builder.py` 加入 room_06 后，`benchmark/observation_camera_registration_patch.v1.json` 的相机注册内容仍与实时提取一致，但 `generated_from.sha256` 还是旧源摘要。已将它更新为当前构建器摘要 `55e20d025d15e35dea5c19370e94bb539d427e281dfc4750029a1469bbb93ae8`；唯一失败项复验为 `1 passed`。该修复不改变机器人相机注册值。

## 明确未完成

- 未证明场景能在 Isaac/PhysX 中成功加载并稳定步进；
- 未运行真实机器人、楼梯控制器或抓取/联合持有技能；
- 未产出真实 terminal receipt、production result 或论文统计样本；
- 未将 CPU/静态结果升级为 GPU、物理或论文资格证据。

## 真实 GPU 场景诊断（2026-10-27）

用户重新开放 GPU 后，在隔离 worktree
`/tmp/heterocoord-room06-real-gpu-MTFYKn` 上按 `run-manifest-r4.json` 执行了
一次新的 `run-05-scene-load-diagnostic`。执行前 compute-app inventory 为空，
可用显存约 22037 MiB；ToDesk/显示共驻不作为阻断，也未触碰 ToDesk；清理命令只针对
`omni.telemetry.transmitter`。

ZCode GLM-5.3/max 只读复验最新生命周期修复：`py_compile` 退出 0，
`test_room06_livt04_gpu_probe.py` 为 **22 passed in 0.17s**，退出 0。

真实 CUDA 运行结果：Isaac/PhysX 在 `cuda:0` 启动，场景构建了 77 个 collision mesh
（raycast proxy 41416 点、29917 三角形），完成 64 步有限读回；
`report.json` 已在 simulator context 内、native close 前封存。关键结果：

- `status=diagnostic_only`，`schema=heterocoord.room06-scene-load-diagnostic.v1`；
- UUID `GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0` 精确匹配；
- `/World/Room06`、`physics_dt=0.005`、`simulation_time=0.3299999926`；
- 无 task/terminal/receipt/qualification 字段，四个正式门仍全为 `false`；
- 运行后 telemetry 清理完成，磁盘约 508 GiB 可用、内存约 232 GiB 可用，compute inventory 为空。

`run-04` 的原始失败没有覆盖：其 `run_failure.json` 记录 simulator 已启动、场景与
CUDA 物理已建立，但 native close 抢先终止了 context 外 report 写入；修复是把 report
写入移入 context 内，重试输出目录为 `run-05-scene-load-diagnostic`。

因此，当前已证明的是“真实 GPU 场景加载与有限步进诊断”，不是 `liv-t-04` 成功、
机器人/技能资格或论文统计数据。完整任务仍受真实跨层 topology edge 与生产
`room06_runtime_hook_source` 注册缺口约束；缺口不能由诊断结果补造。

GPU 恢复后的下一步是把真实 observer/skill-session source 注册到生产 runner，
然后再冻结并执行最小任务纵向探针；任何失败继续保留原始证据，不静默重跑。
