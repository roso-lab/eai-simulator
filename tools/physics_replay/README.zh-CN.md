# 物理回放工具

## 从初始场景重建（独立于原生快照）

新增 `reconstruction.py`：任务先从起点记录未中断的原始过程，之后在**独立新进程**
重新构建初始场景、重新运行任务和策略，不在检查点强写部分物理状态。

- `ReconstructionRecorder.append(channels, task_state=..., checkpoint=...)`：
  流式压缩记录每步的全部子步测量、逻辑状态和任务命名检查点，支持数量变化的接触点。
  `close()` 封存；不完整记录不能作为重建基准，不覆盖旧输出。
- 新进程中创建 `ReconstructionVerifier(reference, fingerprint,
  checkpoint="extinguisher_carried")`，每环境步结束后调用 `compare(...)`。
  任何数值、形状、dtype、通道、任务阶段/时间、数据校验和或指纹不匹配都拒绝通过。
- 只有**当前恰好位于已验证的目标检查点**，才能 `release()` 进入实时执行；若继续
  校验原始后续，则不能再把更晚状态当成之前的检查点释放。新分支不冒充原始后续一致。
- 框架不注入记录的动作、位姿或力；调用方需真正重跑算法以推进策略记忆，而不只是
  跳过算法播放旧动作。输入、观测、策略记忆、噪声源及异步结果交付都需由任务纳入。
- 指纹是调用方声明的覆盖范围，不是“保存了全部隐状态”的证明。成功报告仍保留
  `complete_physical_state_proven=false`。成本是重新模拟整个前缀，不是瞬时 F9。

已接入的受控承重验收入口：

```bash
python -m demo.fire_rescue.checks.reconstruct_physics --output tmp/replay/carry_reconstruction_01
```

实际重新计算 ONNX 策略和高斯命令噪声，通过 `PolicyInferenceTrace` 记录真实
输入/输出（不多调用一次策略或 RNG），比较物理子步接触/摩擦/反力全过程。
保留灭火器真实质量和阻尼，用物理固定关节承重而不是视觉跟随。
这只是受控初始夹持的验收任务，**不是完整 `main.py`/EMOS 异步任务的重建实现**。
实时释放命令、配置限制及实测详见 Fire Rescue 的 README 和验收记录。

## 原生回溯保真优化与零容差审计

`--replay-exact` 是**零容差验收模式，不是“PhysX 已实现绝对零误差”的保证**。
它只支持原生 CPU 单环境回溯，在场景创建前启用增强确定性，保存点读回与后续
轨迹都不允许非零误差。验证必须从刚保存的点开始，比较未中断原始轨迹与恢复
后的轨迹；不接受“两次都恢复后再比较”。失败会暂停，后台验证命令会非零退出。
即使有限窗口通过，报告仍注明求解器内部缓存未恢复，不能推断任意未来都一致。

- 策略不再被当作无需恢复的资源直接跳过。策略/噪声对象必须实现
  `capture_replay_state()`、无副作用的 `validate_replay_state(state)` 和
  `restore_replay_state(state)`；确实无状态时可明确声明 `replay_stateless = True`。
  适配器应包含循环记忆、观测历史、归一化器、计数器，以及在线学习所需状态。
- 已适配受限的 EAI 前馈 ONNX 包装器（检查实际图，不默认所有 ONNX 都无状态）
  和标准 Isaac Lab 噪声模型，保存偏置、参数及首次调用的扩展状态。
- 将独立的 Python/NumPy/Torch Generator 注册到 `base_env.replay_random_streams`；
  按源/机器人命名，原位恢复，保持引用关系。相同调用顺序下重现随机序列，
  **不是关闭噪声**。未注册生成器、模块全局状态和外部服务仍不能自动覆盖。
- 其他资源通过 `base_env.replay_state_resources` 接入；持续外力必须把全部
  生成器/计时器注册到 `base_env.replay_external_wrench_sources` 并提供上述协议，
  才允许一起保存当前外力/力矩缓冲及其生成过程状态。
- 每个真实物理子步求解完成后读取刚体运动、实际驱动力、关节反力、接触力与
  接触点/法线/穿透距离、摩擦力及策略动作。漏步、非有限值或接触缓冲不足直接失败，
  不把原来的接触力强写回来。报告指出首次偏离步；尚未实现策略观测/分布逐步验收。
- 严格模式拿灭火器使用理想物理固定关节，不瞬移预对齐、不减质量、不增阻尼，
  也不使用视觉替身。它不是已验证的真实摩擦抓握；存在障碍物视觉代理或未适配
  传感器历史的场景会拒绝严格检查。普通演示仍保留原来的跟随模式。

可运行命令见 `demo/fire_rescue/README.zh-CN.md`，最新实测及限制见
`demo/fire_rescue/checks/native_replay_validation.md`。快照仍只支持当前进程，
新版本组件快照不承诺与旧版本兼容。

`physics_replay` 是 EAI Simulator 的轻量状态录制与回溯工具，位置在
`tools/physics_replay/`。它不创建第二个 Isaac Sim 应用，而是接入已有的
`open_simulator_session()` 和 `env.step()` 循环。

## 快速检查

在仓库根目录运行：

```bash
python tools/physics_replay/cli.py inspect --episode tmp/replay/episode_0001
```

这个命令不启动 Isaac Sim，只检查 `manifest.json`、`index.json` 和分段
`.npz` 文件。`validate` 会启动指定的保存环境，检查当前 Prim 路径是否仍能
映射到相同的 scene object：

```bash
python tools/physics_replay/cli.py validate \
  --episode tmp/replay/episode_0001 \
  --env my_env \
  --device cuda:0
```

`validate` 需要 `env_isaaclab`、Isaac Sim 资产和可用显示/无头配置；它会关闭
ROS2 bridge，避免实时输入改变校验环境。

## Python API

```python
from simulator import SimulatorLaunchConfig, open_simulator_session
from tools.physics_replay import ReplayRecorder, TargetSpec

config = SimulatorLaunchConfig(env="my_env", headless=True)
with open_simulator_session(config) as session:
    targets = (
        TargetSpec(
            "/World/envs/env_0/go2_1/base",
            ("pose", "velocity", "contact_force"),
        ),
    )
    recorder = ReplayRecorder.from_session(
        session,
        targets,
        "tmp/replay/episode_0001",
        sample_every=1,
    )
    try:
        for _ in range(100):
            action = policy(...)
            recorder.before_step(action)
            session.env.step(action)
            recorder.after_step(phase="transport")
    finally:
        recorder.close()
```

`frame[t]` 是 `env.step(action[t])` 之前的状态；恢复到 `t` 后可以替换
`action[t]` 并继续调用 `env.step()`。根位姿、根速度和关节状态属于可写状态。
`joint_target` 记录 articulation 的 `joint_pos_target`/`joint_vel_target`，用于避免
未控关节沿用事实分支的 actuator target。接触力和关节驱动力是派生观测，首版只
记录并在恢复后的下一步重新采样。

要在 Factory Fire Rescue 中验证 command/action/noise tape，可运行：

```bash
EAI_RUN_FIRE_RESCUE_REPLAY=1 conda run --no-capture-output -n env_isaaclab \
  python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --headless --trials 1 --auto-fire-delay 0 \
  --replay-verify-dir tmp/replay/fire_rescue \
  --replay-verify-steps 50 --replay-local-fallback --device cpu
```

验证会生成 `trial_01/verification.json`。`restore.ok=true` 只表示状态写回和
read-back 通过；如果 `passed=false`，应查看 drift 字段。Factory 的移动底盘和
挂载 articulation 仍可能受 PhysX solver cache、actuator 内部 target 或 GPU/CPU
数值顺序影响，工具会保留这个失败结果，而不是把它降级成成功。

交互式 assignment 分支使用同一套 checkpoint 存储，但由
`demo/fire_rescue.main --branch-interactive` 驱动；它要求非 headless Dashboard，
操作步骤和分支状态见 `demo/fire_rescue/README.zh-CN.md`。

## 文件与限制

episode 目录包含版本化 `manifest.json`、`index.json` 和固定帧数的
`segments/*.npz`。数组从 Torch/其他后端复制到 CPU NumPy 后写入，不需要额外
数据库依赖。

首版恢复只保证同一进程、同一场景中的物理可写状态。PhysX 求解器内部缓存、
控制器隐状态、规划器队列、实时 ROS 输入和未统一的随机噪声不会自动恢复。
因此报告会区分 `state_restore`、`action_replay` 和 `causal_replay`，后者需要
后续的 action/communication/noise tape 协议。
