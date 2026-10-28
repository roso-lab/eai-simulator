# GPU 三路等价：原生共享槽位进展（2026-10-24）

## 当前结论

真实 GPU C 路仍不放行，正式资格门全部保持 `false`。本阶段没有启动 Isaac、CUDA 或 GPU，也没有操作用户正在使用的 ToDesk。

已完成的是共享槽位协议和失败边界，而不是原生 Factory 资格：

- `FactoryNativeSlotBridge` 要求 12 个参与者、单次 batched `env.step`、inactive sibling hold/freeze、generation fencing 和终止收据；
- 新增 `factory_native_slot_runtime.py`，把 A 路六个权威 checkpoint component 严格投影到 12 个参与者；
- participant native state 现在带来源 projection/context digest；decoder 还必须把 native state 反投影为原始 participant projection 并逐位核对，不能只携带摘要而丢弃语义；
- `translation_validated` 与 `restore_readback_verified` 分阶段：import 时后者必须为 false，只有真实 restore + capture 一致后才在恢复收据中变为 true；
- decoder 完成后再次核对 checkpoint 目录摘要，关闭解码期间 TOCTOU；
- mutable buffer 必须与登记 live object 共享实际 NumPy/Torch storage，且不同 slot 不得重叠；
- isolation admission 改为 live probe：从实际资产文件计算 SHA-256、核对逐槽 collision/sensor scope 不相交，并对每个槽逐一真实 reset，验证所有兄弟槽完全不变后恢复目标槽；不再接受布尔值自报；
- inactive restore 会尝试所有参与者并汇总错误；任意 `open_slot` callback 已被禁止，槽生命周期只由 runtime 自身持有，消除 callback 私有资源不可观察的回滚缺口。

## 验收证据

- 原生槽位 runtime + bridge 聚焦：`40 passed`；
- 本轮相关扩展回归：`139 passed, 2 skipped`；
- 扩至整个 `tools/physics_replay/tests` 时：`247 passed, 12 skipped, 14 failed`；14 项均为当前通用 Python 环境缺失可选依赖/入口遮蔽（CLI 直接执行时 `types.py` 遮蔽标准库、未安装 `demo.fire_rescue`、无 `torch`），未归因于本轮 native-slot 改动，也未通过修改环境掩盖；
- `py_compile`、`git diff --check` 通过；
- 最新离线证据：`artifacts/project_runtime/gpu-three-way-offline-20261024-13/offline_concordance.json`；SHA-256 `6ec33ed9742e82c7184871064dc48dfff26914bf61c7d0015c4a1c6163ce043c`；A–B、A–C、B–C 均通过，`qualification_status=diagnostic_only`，所有正式门为 `false`。

首次独审基线为 `P0/P1/P2=0/4/2`，报告位于团队工作区 `handoffs/factory-native-slot-incremental-review.md`。第一次修后独审为 `0/3/1`，报告位于 `handoffs/factory-native-slot-postfix-review.md`。第二次增量独审为 `0/2/1`，报告位于 `handoffs/factory-native-slot-contract-review-2.md`：generic projection authority 已关闭；其指出的非对称 reset 与 callback 虚假回滚反例已再修，尚待与 production registration 一并终审。collision/sensor 原生来源绑定与 production registration 仍是 P1。

## 尚未完成

当前 runner 使用的 `EAI-Factory-Z1-v0.json` 是 version 1；Factory V2 的 `num_envs=1` guard 不是该最小切片的直接阻断。真实阻断是启动器尚未注册 concrete runtime：

1. 12 个参与者尚未绑定真实 EAI/Isaac 环境索引 state owner；
2. evaluator、queue、typed lock、skill、milestone、logical clock 和 RNG 仍是单环境/进程级实现，尚无逐槽 owner；
3. 静态资产身份、collision/sensor filtering、independent reset、inactive sibling 非干扰和 clean terminal shutdown 尚无原生测量收据；
4. 最新论文语义迁移仍缺 `production/P3`、`service/P4`、`living_public/P1` 和 D5 六方向目录，`paper_identity.status` 保持 `migrated_identity_semantics_pending`。

因此本阶段只能继续离线实现和审查，不能冻结真实 GPU release，更不能把 CPU double/离线 concordance 升级为物理、进程隔离、native-full-state、paper-score 或 rankable 证据。

## 2026-10-25 增量

- 严格 production builder 与 C 路 launcher hook 已落地；只有 C worker 获得共享槽位标志，A/B 保持原路径。缺任何 owner 时继续 fail-closed。
- `EnvironmentSlotBuffersComponent` 和 evaluator/task/service 逐槽底座已完成：真实 NumPy/Torch 行内恢复、双向 sibling isolation、每槽独立 channel/typed-lock/history/milestone，专项 53 项通过。
- native step 新增单次消费 active-slot 契约：slot 集合必须唯一、有序、在范围内；每次 step 前重新登记，成功或异常均清除，防止 inactive slot 的服务状态误推进。
- per-slot RNG registry 和多环境 target resolution 基础 API 已完成，但尚待 owner graph 绑定真实 consumer、world row、sensor scope 与 checkpoint identity；因此 C 路仍不可执行。
- 论文权威身份与可执行语义迁移已拆成独立门：状态恢复三路仅在 `identity_verified=true`、`diagnostic_execution_permitted=true` 且 `formal_execution_permitted=false` 时可作为 diagnostic probe 运行；D5/common-anchor 迁移仍 pending，仍阻止所有正式资格。相关扩展回归 145 项通过。
- 旧版外部准入曾错误地要求 ToDesk 必须驻留，并把其它 compute application 一律拒绝；这已在 2026-10-25 修正。公开 benchmark 默认使用 `permissive`：ToDesk 是可选的 provenance 记录，其他 GPU 进程只进入 inventory，显存峰值余量不足才拒绝。需要干净 compute inventory 的认证实验显式使用 `strict_exclusive`，并在 manifest 冻结该模式。修正记录见 [Benchmark 准入与整体工程复盘](BENCHMARK_ADMISSION_REVIEW_20261025.zh-CN.md)。本阶段没有启动或关闭 ToDesk，也没有启动 Isaac/GPU。

## 2026-10-25 软件闭环终验

- 生产 owner graph 已把 A checkpoint 的 12 个参与者投影到真实逐槽 owner，并完成 CPU-double 端到端链路：checkpoint 导入、共享槽绑定/恢复、一次 native batched step、evaluator snapshot 与十字段 concordance frame。逻辑时钟从 8 到 9、业务 RNG 连续，inactive slot 的 12 个 participant digest 全部不变。
- 修复 checkpoint envelope 与 live observation 的接口错配：checkpoint capture 继续保留 `native_state + projection_binding`，生产观测只通过单独的 `capture_live_slot()` 读取实时 projection。
- Factory sensor scope 现在读取 live `scene.sensors` 与 `scene.extras` 中实际的 environment-indexed `prim_paths`；非 sensor extra 不能满足准入。
- 冻结合同固定项目 `usd` 根、Factory USD 路径与 SHA-256；worker 环境覆盖任何继承的 `EAI_USD_ROOT`。A/B 在 live session 打开后读取 `env_cfg.scene.world.spawn.usd_path` 并核对路径与摘要，C 在 owner graph 安装前执行同等校验。
- 源码冻结从手写叶子列表升级为确定性的项目源码树闭包。freeze、validate 与 offline receipt 每次重新枚举 619 个条目；fresh-process 审计实际加载 125 个本地 Python 模块，loaded-but-unhashed 为 0。新增、删除或修改生产 `.py` 均使 release 失效。
- 主代理扩大矩阵：264 passed；独立 Sol 修后终审：304 passed，`P0/P1/P2=0/0/0`，软件 ACCEPT。终审报告位于团队工作区 `handoffs/factory-native-final-review-postfix.md`。
- 当前源码的新离线证据：`artifacts/project_runtime/gpu-three-way-offline-20261025-15/offline_concordance.json`，SHA-256 `5bc415fd9d36a2d1d25c80f097705f6a8fadc8348fa7d33e7b7740941c4929b2`；A-B、A-C、B-C 全通过，负向缺字段与 non-finite probe 均 fail-closed，`qualification_status=diagnostic_only`，所有 formal gates 为 false。
- 真实外部准入复核：GPU compute-app inventory 为空，空闲显存约 21944 MiB，磁盘与内存充足；但 `/opt/todesk/bin/ToDesk_Session` 与用户登记 PID 3089908 均不存在。仅 `ToDesk` 主程序和 `ToDesk_Service` 存在，不能替代用户授权的共驻 session。因此当前不得创建新 GPU freeze/release，也不得启动模拟器。ToDesk 只能由用户保持/恢复，工程不得启动或关闭它。

## 2026-10-25 C 路失败可审计性修复

`-17` 真实 GPU 收据只记录了 `route worker shared_session_restore exited 1`，因为编排器此前把 worker 的 stdout/stderr 丢弃到 `DEVNULL`。当前 runner 已为每个 route 保存 `worker.stdout.log`、`worker.stderr.log` 及有限长度 tail，并把它们写入 `run_failure.json` 的 route result；这样下一次经用户放行的 C 路尝试能区分 native registration、asset preflight、owner graph 和 session teardown 故障。该改动只增强基础设施诊断收据，不改变 A/B/C 编排、恢复或对账语义。

本轮 GPU runner、native evaluator、vectorized backend、slot bridge/runtime 相关回归为 `150 passed`；新的离线探针 `artifacts/project_runtime/gpu-three-way-offline-20261025-20/offline_concordance.json` 三路比较通过，仍为 `diagnostic_only`，所有 formal gates 为 `false`。ToDesk 当前不在驻留状态，因此没有重新 freeze 或启动真实 GPU。

本轮还修正了 C 路启动状态收据：在 `open_simulator_session()` 返回前失败时，worker
会写入 `simulator_app_start_attempted`、`native_registration_started`、
`native_registration_failed` 与 `simulator_app_may_have_started`，编排器在
`run_failure.json` 中记录 `startup_stage=native_registration_failed`。这避免
把 `simulator_session_opened=false` 误读成模拟器从未启动，同时继续禁止自动重试。
