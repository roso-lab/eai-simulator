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

## 明确未完成

- 未证明场景能在 Isaac/PhysX 中成功加载并稳定步进；
- 未运行真实机器人、楼梯控制器或抓取/联合持有技能；
- 未产出真实 terminal receipt、production result 或论文统计样本；
- 未将 CPU/静态结果升级为 GPU、物理或论文资格证据。

GPU 恢复后的下一步是冻结一个隔离的 `room_06 × liv-t-04` 最小纵向探针，真实加载场景、驱动 observer bridge 至终局，再把 terminal material 接入现有 receipt/production 链。任何失败都保留原始证据，不静默重跑。
