# Fire Rescue Demo

纯机器人火灾救援实验示例。该 Demo 不注册 Gym 环境、不启动实验总控，也不保存实验记录。

## 启动命令

```bash
cd eai-simulator
conda activate env_isaaclab

python -m demo.fire_rescue.main \
  --env=EAI-Factory-v0 \
  --device=cuda:0 \
  --trials=1 \
  --trial-hazard-ids=1 \
  --auto-fire-delay=0 \
  --emos-llm-preset=zhipu-glm4-flash
```

非 headless 模式启动后，终端会提示打开：

```text
http://127.0.0.1:8767/
```

Dashboard 数据接口使用端口 `8767`，Isaac Sim 实时图像 WebSocket 使用端口 `8766`。

## 环境加载流程

外部调用仍使用 `--env=<名称>`，但名称只对应 JSON 文件，不再对应 Gym registry：

```text
--env=EAI-Factory-v0
        │
        ▼
source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-v0.json
        │
        ▼
simulator.py
        │
        ├── EAI.hmrs_env.env_diy.storage.load_task()
        ├── EAI.hmrs_env.env_diy.flow.interactive_selection_from_dict()
        └── EAI_hmrs.env_builder.build_interactive_env_cfg_from_selection()
        │
        ▼
EAI.hmrs_env.MultiRobotDirectEnv
```

`simulator.py` 不再检查注册 Gym 环境，也不再调用 `load_cfg_from_registry()` 或 `gym.make()`。

## Factory JSON 要求

`source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-v0.json` 声明 Factory 场景和四台机器人：

- `carter_1`：Carter 差速底盘，Orsus 数据采集。
- `m20_1`：M20 + UR5。
- `m20_2`：M20 + UR5。
- `scout_1`：Scout + UR5，负责救援通道按钮。

附件使用实验最小配置：

- 只有 `carter_1` 挂载 Orsus；
- `m20_1`、`m20_2` 和 `scout_1` 只挂载任务所需的 UR5；
- Fire Rescue 不使用 Nav2，JSON 中不挂载 `ros`，启动时也不加载 Isaac ROS Bridge。

机器人实例名由 EAI builder 按类型和出现顺序生成。Fire Rescue 不修改通用 JSON 命名规则，直接适配 EAI 默认生成的 `scout_1`。

JSON 中可以选择性提供 `spawn_pose`：

```json
{
  "spawn_pose": {
    "position": [1.0, 2.0, 0.5],
    "rotation": [1.0, 0.0, 0.0, 0.0]
  }
}
```

不提供 `spawn_pose` 时，EAI builder 使用场景默认排列位置。

本实验需要固定初始布局，因此通过 `SimulatorLaunchConfig.env_cfg_hook` 在创建环境前写入出生位置。实验位置定义在 `config.py` 的 `ROBOT_SPAWN_POSES`，不会污染通用 EAI JSON 环境。

## Simulator 接口

`main.py` 创建 `SimulatorLaunchConfig`，然后调用：

```python
with open_simulator_session(launch_config) as sim:
    run_robot_baseline_experiment(sim, demo_config)
```

`SimulatorSession` 向实验提供：

- `simulation_app`：Isaac Sim 应用。
- `env`：EAI 多机器人环境。
- `base_env`：解包后的 `MultiRobotDirectEnv`。
- `env_cfg`：由 JSON 动态构建的配置。
- `possible_agents`：JSON 环境生成的机器人实例名。
- `device`、`num_envs` 和外部环境名称。

Fire Rescue 只通过该会话使用仿真环境，不自行创建 Isaac Sim 或 Gym 环境。

## 算法接入

### EMOS

任务分配使用独立算法包：

```python
from algorithm.emos.engine import EMOSDiscussionManager
```

`scenario.py` 定义火源任务、机器人能力、任务约束和默认分配规则。EMOS 根据当前机器人位置和能力完成多机器人讨论与任务分配。

### Global Planner

路径规划使用独立算法包：

```python
from algorithm.global_planner.session import GlobalNavSession
```

`runtime/algorithm_adapter.py` 只负责 EAI 仿真位姿、控制器张量和规划器之间的适配。规划、路径跟踪和速度指令计算保留在 `algorithm/global_planner` 内。

Fire Rescue 默认使用 `GlobalNavSession.plan_batch()` 对同时派发的机器人执行时空冲突规划。控制循环在 `env.step()` 前还会根据机器人半径、当前位置和短时速度预测执行安全停车；检测到冲突时，低优先级机器人让行，进入硬安全距离时双方停车并异步成组重规划。火源附近使用按机器人区分的停靠点，并按挂载 UR5 后的有效包络保留间距；hazard 1 的 Scout 停靠点同时避开 M20 灭火器运输通道，防止已完成任务的 Scout 阻塞最终运送。机器人进入待命或安全停车时会同时消除底盘残余速度，避免到点后继续滑移。

机械臂动作与底盘导航按阶段衔接：M20 的 UR5 先执行目标上方接近、下降和夹持，进入夹持阶段后才允许灭火器附着；随后竖起肩部、收拢肘部，将灭火器抬到底盘上方的紧凑携带位置，并在运输期间持续保持该关节目标。机械臂完成抬升后底盘才解除保持，先沿取件区通道退出，再规划前往火源。随手臂移动的灭火器视觉代理不参与物理碰撞。Scout 按压救援通道按钮期间保持底盘静止，动作结束后解除保持，并用一条连续路径先离开按钮区域、再前往火源集结，途中不在固定后退点停下切换任务。

## 实验流程

```text
main.py
  └── experiment.py
        ├── 创建 EMOS manager
        ├── 选择火源编号
        └── runtime/experiment_loop.py
              ├── 启动 Dashboard 与图像流
              ├── 创建火源
              ├── 触发 EMOS 任务分配
              ├── 调用 global_planner 规划路径
              ├── 控制导航、UR5 和障碍救援
              └── 检查任务成功条件
```

火源通常位于障碍物或不可直接到达区域，因此最终火源任务规划到火源周围 `3 m` 内的可达位置，而不是强制到达火源中心。

## 文件结构

```text
demo/fire_rescue/
├── README.md                 # 本说明
├── main.py                   # CLI 和 SimulatorSession 启动入口
├── experiment.py             # 多轮实验编排和 env_cfg hook
├── config.py                 # Demo 参数、任务目标和实验出生位置
├── scenario.py               # EMOS 场景、机器人能力和任务解释
├── algorithm_paths.py        # 保证使用仓库内 algorithm 包
├── llm_compat.py             # OpenAI 兼容修复
│
├── assets/
│   ├── factory_map.yaml      # 路径规划地图元数据
│   └── factory_map.png       # 占据栅格地图
│
├── dashboard/
│   ├── index.html            # 纯机器人监控页面
│   ├── design-tokens.css     # 页面样式
│   ├── server.py             # 8767 数据与静态资源服务
│   └── stream.py             # 8766 Isaac Sim 图像流
│
└── runtime/
    ├── experiment_loop.py    # 逐帧实验主循环
    ├── navigation.py         # 机器人任务队列和导航执行
    ├── algorithm_adapter.py  # EAI 与 global_planner 适配
    ├── ur5.py                # M20/Scout UR5 执行逻辑
    ├── obstacle_rescue.py    # 动态障碍救援流程
    ├── rescue_llm.py         # 救援机器人选择
    ├── mission_success.py    # 统一成功条件
    ├── sim_helpers.py        # 位姿和灯光辅助函数
    ├── fire.py               # 火源可视标记
    ├── input.py              # 键盘输入
    ├── settings.py           # 导航、火源、UR5 和端口常量
    └── llm_presets.py        # LLM preset 配置
```

## 结束条件

实验在以下情况结束：

- 灭火器任务完成；
- 救援通道按钮任务完成；
- 必要的动态障碍救援完成；
- 成功判定要求的机器人进入火源 `3 m` 邻域；
- 达到实验内部任务超时；
- 用户关闭 Isaac Sim。

CLI 不提供 `--max-steps`，不会因为固定环境步数提前结束。

## Headless

可以增加：

```bash
--headless
```

Headless 模式不会启动 Dashboard 和 Isaac Sim 图像流。
