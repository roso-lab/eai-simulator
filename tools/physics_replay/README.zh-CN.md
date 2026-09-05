# 物理回放工具

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
接触力和关节驱动力是派生观测，首版只记录并在恢复后的下一步重新采样。

## 文件与限制

episode 目录包含版本化 `manifest.json`、`index.json` 和固定帧数的
`segments/*.npz`。数组从 Torch/其他后端复制到 CPU NumPy 后写入，不需要额外
数据库依赖。

首版恢复只保证同一进程、同一场景中的物理可写状态。PhysX 求解器内部缓存、
控制器隐状态、规划器队列、实时 ROS 输入和未统一的随机噪声不会自动恢复。
因此报告会区分 `state_restore`、`action_replay` 和 `causal_replay`，后者需要
后续的 action/communication/noise tape 协议。
