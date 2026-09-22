# Go2 / B2 修复复盘（2026-09-21）

本复盘基于会话 `sess_0da75db0-0884-4c32-81b0-2075484e1e52` 的 ZCode 日志、提交
`600eec1` 的源码和当前工作树。会话文件本身不在仓库根目录；对应记录位于
`/home/pupil/.zcode/cli/rollout/model-io-sess_0da75db0-0884-4c32-81b0-2075484e1e52.jsonl`。

## 已确认的有效修复

- Go2 的 Stage-B checkpoint 3999 已收敛到 `GO2_MIXED_STAIRS_CFG`，旧的
  `GO2_MIXED_STAIRS_CONTINUED_CFG` 注册名已删除，scenario、catalog、resolver 和 Env DIY
  入口使用同一个名称。
- B2 已增加 `B2_MIXED_STAIRS_CFG`、232 维观测（含 187 条高度射线）、独立策略文件和
  `b2_factory_stairs.json`。Go2/B2 的高度扫描器分别绑定 `base` / `base_link`，修复了
  统一使用 `/base` 导致 B2 RayCaster 失败的问题。
- 观测和 ONNX 的静态接口相符：两份楼梯策略均为 232 维输入、12 维输出；catalog、资源
  需求和键盘速度命令回归测试通过。会话报告的 Isaac 运行结论可以作为部署线索，但仍需
  把原始报告、场景/资产 hash 和运行配置入库后才是可审计证据。

## 本次审查修复

原 `scenario_spec.validation` 只允许 Go2 在 `F-VERT-01` 使用混合楼梯控制器。于是已经
注册的 `B2_MIXED_STAIRS_CFG` 在同一 F-VERT 轨道上仍会被拒绝为“use the registered default
controller”。现已把 B2 楼梯控制器加入同一条显式白名单，并增加回归测试
`test_fvert_accepts_b2_mixed_stairs_controller`。相关 focused 测试为 38 项通过。

同时发现 `vertical_stair_01.audit.json` 的场景摘要落后于当前已提交的
`vertical_stair_01.json`。已用 `diagnostic_audit --write` 重建 sidecar；审计入口现在可以
重新读回该文件而不报 stale。

另外，业务结果记录现在对重复提交幂等，对 `validated` / `failed` 终态拒绝重写，避免
评价器回写覆盖已经固定的业务结论。

## 仍需交接的缺口

1. **运行器账本基础接线已完成，生产 runner 仍待实测。** `GoalAlgorithmHost` 和
   `ReliableSkillReferenceHost` 现在支持由运行器注入并校验同一个 `GoalSession`；共享模式的
   `_commit()` 直接回写该 ledger，组合回归已覆盖技能终态对算法主轨的可见性。仍需由真实
   Isaac/TeamWeaver/EMOS runner 实例化这条路径，并把 episode identity、外部进程 attempt
   目录和原生运行证据贯穿完整 episode。
2. **外部进程 helper 尚未接入生产入口。** `run_algorithm_subprocess()` 已有 JSON、超时和
   协议分类，但当前非测试调用仍是进程内 `GoalAlgorithmHost` 或 callback/thread batch；
   helper 也没有自动调用 `validate_algorithm_result()`、核对 `episode_id`/`run_root` 并写入
   attempt 目录。交接方必须实现真实 runner，不能把 helper 单测当成隔离完成。
3. **episode 身份没有贯穿账本。** `algorithm_adapter` 的 manifest 会生成 `episode_id`，
   但 `GoalSession`、技能 receipt 和业务 evidence 没有强制持有并核对同一个值。需要给
   session 增加不可变 episode identity，并把它加入 receipt/evidence 校验。
4. **B2 没有独立的 F-VERT 题包文件。** `b2_factory_stairs.json` 是 Env DIY 保存环境，
   不是 `vertical_challenge.py` 使用的 F-VERT 场景包。若要统计 Go2/B2，需生成并审核一个
   B2 版本的 F-VERT candidate，绑定自己的 scenario hash、spawn pose、controller cfg 和
   `runtime_config`。
5. **模型身份尚未进入题包的运行证明。** 题面保存 controller 名称，但还没有把 provider
   revision、模型 SHA-256、控制器源码 hash、观测布局版本和运行时 execution contract
   一起写入 episode manifest。否则同名 `policy.onnx` 被替换后，历史结果无法复现。
6. **当前测试主要是静态接线测试。** 尚缺 Go2/B2 各自的 fake-env 观测顺序/坐标系测试、
   action permutation 测试、height scan 非有限值测试，以及干净环境下的 ONNX provider
   选择测试。真实 Isaac 运行不能由普通 pytest 自动替代，应由下游运行矩阵保存原始日志和
   hash-bound report。
7. **B2 动作应用路径需要运行时复核。** `apply_b2_mixed_stairs_action()` 复用
   `apply_b2_velocity_action()`；后者既设置 position target，又在存在 actuator 参数时
   计算并写入 effort target。需要确认这不是同一步中的重复控制源，并把最终控制模式写入
   execution contract。benchmark 的高层任务接口不应依赖一个未记录的低层控制副作用。
8. **发布门禁仍必须保持 fail-closed。** 即使会话中出现“可上下楼”日志，也只能标记为
   controller smoke 或 deployment evidence；在 Go2/B2 分别完成连续上行、平台停留、下行、
   失败/超时读回、场景绑定和独立支撑证据前，继续保持
   `execution_validated=false`、`scoreable=false`。

## 本轮验证

```text
source/EAI/test/test_factory_vertical_challenge.py
source/EAI/test/test_env_diy_controller_catalog.py
source/EAI/test/test_keyboard_controller_regression.py
38 passed
```

没有启动 Isaac GUI，也没有把 Go2/B2 物理运行声称为 benchmark 业务成功。当前工作树中的
`source/EAI_hmrs/EAI_hmrs/envs/my_env.json` 仍是其他工作，不属于本复盘。
