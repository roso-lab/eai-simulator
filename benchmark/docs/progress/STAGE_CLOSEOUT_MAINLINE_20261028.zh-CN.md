# 主线整合阶段收尾（2026-10-28）

## 已完成

- 已将远端 `origin/HeteroCoord@1b81f702` 之上的主线与 ljy 语义地图工具整合提交推进到
  `origin/HeteroCoord@66e12790`。
- `PickPlaceExecutor` 现在对声明 `idealized_semantics = True` 的 Arm/G1/Pepper
  adapter 只执行语义搬运：导航到任务语义点且 transport admission 通过后，生成逻辑持有/
  释放反馈。旧几何、IK、工具轴、可达性与 controller lifecycle 不再进入 D1/D5 成功条件。
- 各 adapter 的原 `start()` 保留为显式物理诊断路径；诊断失败只能记录，不能改写理想化任务结果。
- `algorithm/skills/pick_place/contracts.py` 已把该边界写入 adapter 合同；transport 回归新增
  几何拒绝但任务成功的反例，防止旧准入回流。

## 验收证据

| 范围 | 结果 |
|---|---|
| `task/tests/test_transport.py` | 8 passed，退出码 0 |
| `benchmark` 全量（推送边界） | 1047 passed，退出码 0 |
| `benchmark/tests/test_room06_runtime_preflight_v1.py` | 13 passed，退出码 0 |
| `compileall`（pick/place adapter 与合同） | 通过 |

`task/tests` 仍有 6 个既有 navigation measurement API 不一致失败；它们与本次改动无关，
并已被 benchmark 全量中现有收集边界隔离。直接运行
`source/EAI/test/test_idealized_manipulation_runtime.py` 还需要把
`source/EAI` 加入 `PYTHONPATH`；纠正路径后该文件仍有既有 fixture/API 漂移，未把这些失败
伪装成理想化搬运回归失败。

## 工作树边界

主工作树 `/home/pupil/HeteroCoord` 仍有其他会话的地图、GPU 与未跟踪实验产物，未执行
`reset`、`clean`、覆盖式 checkout 或批量提交。为避免覆盖这些写入，本阶段在隔离 worktree
完成并把已验收提交直接 fast-forward 推送到远端主分支；待其他写入者收尾后，再用普通
`git pull --ff-only` 同步本地工作树。

正式资格门与认证 flags 继续保持 `false`；本阶段没有启动 GPU 或声称物理资格。
