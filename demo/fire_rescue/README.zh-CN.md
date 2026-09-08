# Fire Rescue Demo

[English](README.md)

## 两路并行、多轮无头检查

```bash
conda activate env_isaaclab
python -m demo.fire_rescue.checks.parallel_replay --workers 2 --rounds 2
```

默认使用示例算法；需要换算法时只加 `--algorithm 你的模块:assign`。输出自动分配到
新的 `tmp/replay/parallel_replay_*/`，查看其中的 `parallel.json`。

每轮同时启动两个**独立的单环境进程**，轮间重新创建场景，不复用动态 USD、物理
求解器或控制器状态。两个任务进程先等待彼此场景就绪，再开始实际 `env.step()`；
报告用实际步进时间区间的交集检查是否确实发生了并行，不把先后串行运行报告为并行。
整个检查始终使用 CPU PhysX 和无头渲染，进程失败、记录缺失或超时均非零退出；超时
清理所属进程组，包括 Isaac/preflight 子进程，而不只结束 Conda 父进程。

每个作业包含两个**独立验收项**：

- **真实任务逻辑**：运行指定算法400个环境步，确认任务分配已执行、任务自己的
  `assignment_applied` 检查点自动保存。此项启用物理夹持配置，但400步是逻辑冒烟
  上限，不代表完成抓取或整场救援；报告另外保留 `mission_success`。
- **物理重建**：在新的进程中运行既有受控承重夹持测试，比较原始与重新执行的
  160环境步／640物理子步，包括实际噪声、策略观测/动作、力和运动，容差为零。

两项都通过**不等于指定算法完整任务的物理回溯已验证**，所以报告保留
`full_task_physical_rewind_verified=false`。重建夹持测试仍是固定命令驱动的独立
测试，不会将它的成功冒充指定任务算法的回溯成功。

不要用 `main --num-envs 2` 代替这个检查：当前任务导航、目标和状态机只读第0个环境，
不能把给两个机器人批次广播命令算成两个独立任务。CLI 现在会明确拒绝，包括
`--rewind --num-envs 2`，不再悄悄改成单环境。共享 ONNX 控制器仍支持固定 batch=1
模型按环境逐条推理，动态 batch 模型保持一次批量推理；这只解决模型维度限制，不会
自动把 Fire Rescue 任务向量化。

## 从初始场景重建的物理验收

如果只是想让自己的算法跑任务，并自动拥有任务检查点和原生恢复，使用下面这一条命令即可：

```bash
python -m demo.fire_rescue.main \
  --algorithm algorithm.my_policy:assign \
  --rewind
```

`--rewind` 会自动设置 CPU PhysX、单环境、单轮、任务自有检查点和原生恢复，不需要再
分别填写 `--task-checkpoints`、`--replay-native`、`--trials 1` 或 `--device cpu`。
默认打开 Isaac Sim 窗口；如果要无界面运行，增加 `--headless`：

```bash
python -m demo.fire_rescue.main \
  --algorithm algorithm.my_policy:assign \
  --rewind --headless
```

任务会自动在 `assignment_applied`、`extinguisher_carried` 等安全节点保存。窗口中仍可
使用 `F5` 保存、`F9` 恢复、`F8` 暂停/继续、`F10` 单步。灭火器抬臂完成后的携带状态
也可以恢复。当前物理保真边界是同一进程原生恢复；它会拒绝不支持状态，但仍不能把
PhysX 求解器内部缓存宣称为已序列化。

若要额外做严格零容差审计，在同一条命令上增加：

```bash
python -m demo.fire_rescue.main \
  --algorithm algorithm.my_policy:assign \
  --rewind --rewind-check --headless
```

审计发现任何偏差会失败退出，而不是掩盖偏差。通常开发时先用 `--rewind`，算法验收
时再用 `--rewind-check`。
这两个选项仍使用同一个原生快照后端，不会切换到下文的新场景重建后端，也不保证
恢复零偏差；已有验证中原生快照恢复出现过实测偏差。

新增一条不同于“写回部分快照”的路线：录制未中断的原始运行，再启动**独立新进程**，
从相同初始场景重新执行到任务检查点，并核对其后续过程。不需要浏览器或手动保存：

```bash
conda activate env_isaaclab
python -m demo.fire_rescue.checks.reconstruct_physics \
  --output tmp/replay/carry_reconstruction_01
```

默认 CPU PhysX、无窗口、seed 0；160 个环境步（640 个物理子步），第 80 个完成步
是验收任务预设的 `extinguisher_carried` 检查点。每次使用新的输出目录。
加 `--gui` 可看两次依次启动的运行；`--scenario locomotion --steps 64
--checkpoint-step 40` 是较小的无负载行走对照。

注意：这是**初始位置受控、真实灭火器通过物理固定关节承重的测试**，不等于完整
伸臂抓取或救援任务。保留原始质量、阻尼，不使用视觉替身。两次新场景都执行同样的
初始布置，但检查点处不写回位置、不注入旧接触力。固定关节锚点改用实时 PhysX
位姿，避免读取滞后的 USD 位姿导致初始约束错位。

比较每个真实求解子步的刚体/关节状态、驱动力、关节反力、接触与摩擦过程；同时
核对实际 ONNX 输入观测/输出、原始命令/实际加噪命令、每环境步前后的 RNG 状态
和任务阶段。高斯动作噪声始终启用（标准差 0.001），不是关闭噪声后再声称一致。
模型、运行配置、已加载代码、文件型 USD 资产指纹不符、原始记录不完整或任何
测量非零偏差都会拒绝通过，命令非零退出。

从已有记录重建到搬运检查点，再切换到**新的实时命令**：

```bash
python -m demo.fire_rescue.checks.reconstruct_physics \
  --mode reconstruct --reference tmp/replay/carry_reconstruction_01/record/journal \
  --output tmp/replay/carry_live_01 --release-at-checkpoint --live-speed 0.1
```

前 80 步全部通过零容差比较后，才把新命令交给真实控制器；不会把这个新分支报告为
“与原始后续完全相同”。配置必须与记录一致，包括 `--gui`。验收看
`comparison.json`，原始日志和数据在 `record/`，重建日志在 `reconstruct/`。
`--mode record` 可只创建原始记录。

**尚未替换完整 `main.py`/F9 的回溯路径**，也未自动覆盖 EMOS/导航异步结果交付、
任意循环策略或传感器历史。可复用的记录/准入接口在
`tools.physics_replay.reconstruction`。这证明的是有限窗口中所测通道一致，不是
任意任务、任意时长、所有隐状态都绝对无偏差。实测结果见
`checks/native_replay_validation.md`。

## 零容差物理回溯审计

```bash
conda activate env_isaaclab
python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --device cpu --trials 1 --headless --auto-fire-delay 0 \
  --task-checkpoints --replay-native --replay-exact --replay-native-test-steps 100 \
  --assignment-policy algorithm.emos.example_policy:assign \
  --checkpoint-dir tmp/replay/fire_rescue_exact
```

任务决定保存点，命令自动对第一个保存点执行“原始未中断续跑 vs 恢复后续跑”，
比较完退出，不需要手动保存，也不代表整场任务成功。`--replay-exact` 将读回和
轨迹容差强制为零，不受普通 `--replay-restore-tolerance` 参数放宽。
任何非零差异或未支持状态都会失败；**当前尚未证明物理回溯零偏差**。
如果保存点读回已经失败，会在继续比较前停止。报告写入打印出的 trial 目录：
`last_restore.json`、`trajectory_verification.json`。
不传 `--replay-native-test-steps` 时仍自动保存任务里程碑，但不会自动执行回溯验证。

该模式逐物理子步测量实际接触/摩擦力和关节反力；拿灭火器使用理想物理固定
关节，保留质量、阻尼和当前相对姿态，不再用无物理代理冒充负载。它不是已经
验证的真实夹爪摩擦抓握，现有障碍物视觉代理和未适配的传感器历史也不支持
严格检查。策略、噪声、持续外力的接入协议见 `tools/physics_replay/README.zh-CN.md`，
实测见 `checks/native_replay_validation.md`；PhysX 求解器缓存仍未纳入快照。

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

普通非 headless 模式（未使用 `--replay-native` 或 `--task-checkpoints`）启动后，终端会提示打开：

```text
http://127.0.0.1:8767/
```

Dashboard 数据接口使用端口 `8767`，Isaac Sim 实时图像 WebSocket 使用端口 `8766`。

## 任务自带检查点与算法接入（实验版）

检查点由任务定义，不由算法或操作人员决定。`task_checkpoints.py` 固定以下逻辑节点：

| 节点 | 触发条件 |
| --- | --- |
| `assignment_applied` | 任务分配已派发，不再等待派发延时。 |
| `extinguisher_carried` | 已抓到灭火器，抬臂序列完成，仍处于携带状态。 |
| `rescue_channel_open` | 任务逻辑确认通道按钮动作完成。 |
| `obstacle_removed` | 任务逻辑确认障碍已清除；没有突发障碍时可能不触发。 |
| `mission_success` | 任务自己的统一成功条件成立。 |

节点成立后在安全的完整环境步边界自动保存，**不暂停、不需要按 F5**。同时成立的
节点共用一个物理快照。后台规划/机械臂操作尚未结束时等待，不降低原有安全校验。
索引记录触发步和实际保存步；若条件在安全保存前已失效，会记为 `missed`，不能把
后来的空闲状态冒充“正在携带”。恢复到早期检查点后，后续节点重新等待触发；恢复到
节点本身不会重复保存它。手动 F5/F9/F8/F10 仍保留，自动模式不主动回溯或失败重试。

无人值守运行示例（使用不调用 LLM 的示例算法）：

```bash
conda activate env_isaaclab
python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --device cpu --trials 1 --auto-fire-delay 0 \
  --task-checkpoints --headless \
  --assignment-policy algorithm.emos.example_policy:assign \
  --checkpoint-dir tmp/replay/fire_rescue_task
```

去掉 `--headless` 即保留 Isaac 原生检查点窗口，不启动浏览器。任务结束后，无界面
模式退出，有界面模式保持暂停供查看/回溯。第一版要求 CPU PhysX、单环境、每进程一轮
（`--trials 1`）；多轮测试应分别启动新进程，避免上轮动态 USD 对象污染下一轮。
原生检查点模式下 headless 也保留灭火器代理和机械臂任务动作，不只存“逻辑已拿取”标记。

**接自己的算法不需要修改 `main.py`、任务判定或 checkpoint。** 在 `algorithm/` 中
实现模块级 `assign(request) -> dict[str, str]`，通过 `--assignment-policy 模块:函数`
选择它。参照 `algorithm/emos/example_policy.py`，输出例如：

```python
{"m20_1": "red", "m20_2": "yellow", "scout_1": "green", "carter_1": "blue"}
```

`AssignmentRequest` 提供任务描述、子任务/能力、机器人位置、任务目标、已完成任务和
救援约束的副本。算法只返回“机器人 → 子任务 id”，目标位置、执行、成功判定和
检查点仍归任务层。这一入口只替换高层任务分配，不替换底层导航/机械臂控制器。
未知 id 和违反 green/yellow 能力限制的结果会报错；算法异常不会
偷偷切回启发式算法，救援重规划也调用所选算法。允许部分分配，但缺失任务不会自动补齐。

默认 `--assignment-policy emos` 保持 EMOS 调用；自定义算法不能同时使用
`--replay-local-fallback`，避免测到不同算法。只加载明确指定且可信的本地模块。
第一版回调要求无状态：不要把可变历史藏在闭包、模块全局变量或模型内部；有状态算法
需要另外实现状态适配，不能宣称隐藏状态已回溯。外部服务结果也未录制。

每轮 `task_checkpoints.json` 记录算法标识、节点与检查点 id、触发/保存步、`missed`、
仍待安全点的 `pending`、未达到的节点及任务结果。任务成功与否以索引中的 `result` 为准，
正常进程退出不等于任务成功。终止时仍不安全就如实留下 `pending`，
不强行保存。物理状态仍是带校验和的 JSON/NPZ。当前不提供跨算法共享快照、自动故障注入
或自动重试；恢复限同一进程，进程结束后的文件用于审计，不能当作跨进程恢复协议。

## 原生检查点控制（实验版，无需浏览器）

```bash
python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --device cpu --trials 1 --auto-fire-delay 0 \
  --replay-native --replay-local-fallback \
  --branch-checkpoint-dir tmp/replay/fire_rescue_native
```

直接在 Isaac Sim 的 **EAI Physics Checkpoints** 窗口中操作：

| 操作 | 作用 |
| --- | --- |
| Save / F5 | 在下一个安全的完整环境步边界新增保存点。 |
| 保存点下拉框 | 选择要返回的保存点，不删除其他保存点。 |
| Restore / F9 | 恢复所选保存点，保持暂停。 |
| Pause/Run / F8 | 暂停或继续仿真及任务逻辑。 |
| Step / F10 | 执行一个完整环境步（包括其控制器、物理子步），然后暂停。 |
| Verify 20 steps | 两次运行后逐帧比较偏差，完成后暂停。 |

快捷键要求 Isaac 窗口获得焦点。请使用上述控件，不要用 Kit 自带的时间轴
Stop/Play 代替：两者的重置语义不同。原生模式同样按上述任务节点自动保存，保存后继续运行。
保存请求暂时不安全时显示 `Save pending`，正常物理和机械臂更新仍会继续；若
已经暂停，可用 F8/F10 推进到安全点。恢复会等待后台工作线程结束并丢弃旧结果，
不会自动继续运行。此模式不启动浏览器 HTTP/WebSocket 服务，也不清理相关端口。

**拿起灭火器后也可以保存和恢复。** 等抓取、抬臂序列完成并进入高位携带姿态后，
按 F5 可保存当前运输状态，不必先放下灭火器。抓取/抬臂途中按 F5 会等待安全点，
完成后自动保存；已暂停时用 F8/F10 继续推进。选择这个保存点按 F9，会同时恢复
底盘、机械臂关节/目标、携带控制器、灭火器代理位置和原物体的隐藏/禁用状态。
恢复后仍然拿着灭火器，F8/F10 继续时代理仍跟随机械臂。可以在抓取前、携带中
保存点之间来回切换，也可以放下后恢复到携带点。放下/中断、其他活动机械臂动作
以及未支持的障碍拖拽阶段仍需等待；旧浏览器分支模式没有放宽为支持携带中保存。

第一版只支持 **CPU PhysX、单环境、同一进程中的保存点**；渲染仍使用 GPU。
不能与 `--branch-interactive`、`--replay-verify-dir` 混用。原生版暂不提供任务
assignment JSON 编辑器。`--replay-local-fallback` 跳过首次 LLM 分配请求，但
不代表后续外部服务调用、ROS 或人工输入已经具有确定性录制回放能力。

### 恢复范围和物理精度

这不是把机器人位置改回去的“视觉回放”。保存范围包括：机器人和机械臂位姿、
速度、关节状态与控制目标，实际 PhysX 驱动目标，支持的执行器内部状态（特别是
延迟 PD 的历史环形缓冲、指针和延迟参数），关节加速度历史，环境命令/计数器，
Python/NumPy/Torch 随机状态，导航任务队列/路径跟踪/预留状态，支持的障碍救援、
UR5、EMOS 状态，以及任务仿真时钟。还保存物理 USD 属性、声明的运行时对象、
灯光，以及没有注册到 InteractiveScene 的刚体状态（例如灭火器），包括物理休眠标志。暂停冻结
任务计时，恢复会回退这些计时。

**它仍不是“任意时刻、完整 PhysX 内部状态、零偏差”的序列化。** 不保存接触/
求解器缓存、相机/ROS/操作系统输入历史。只接受安全检查点：无正在运行的工作
线程/待消费规划结果，无不支持的机械臂抓取或拖拽阶段、活动外力、软体或未适配
执行器。未知 Python 状态会明确报错，不会悄悄跳过。声明范围之外的物理拓扑
变化、跨进程恢复也不支持。

恢复后进行读回检查，`--replay-restore-tolerance` 默认 `1e-5`；这只证明
**恢复瞬间写回接近保存值，不证明后续轨迹一致**。轨迹检查另用严格的 `1e-3`
阈值逐帧判断所有数值通道：位置以米计，姿态角以弧度计，其他值按各通道原生
单位计。即使位置误差很小，速度或关节误差超限也会判失败。写入已经开始后的
恢复失败、或轨迹精度检查失败，都会阻止继续步进，必须先成功恢复有效保存点。
若在写入前的预检查阶段就被拒绝，当前状态未被修改，只保持暂停，可用 F8/F10
继续（但不会解除之前已有的不安全状态锁）。`last_restore.json` 会记录失败阶段
及状态是否可能被修改，不再残留上一条成功报告。阈值是验收条件，不是精度承诺。

报告区分 `original_vs_restored`（刚保存、尚未推进就验证）与
`two_restored_runs`（先恢复旧保存点再建立参考轨迹）。后者只能说明恢复后两次
运行的可重复性，不能冒充与原始不中断轨迹的一致性。比较覆盖全部帧和额外刚体，
记录每通道最大偏差以及参考轨迹的实际运动量；长时运行和复杂接触仍可能放大误差。

要无界面实测，在启动命令后增加 `--headless --replay-native-test-steps 100`。
该显式精度测试完成后退出，严格精度超限会以非零状态退出；
演示循环内设置 180 秒超时（不覆盖应用启动或单次阻塞调用）。普通交互模式保留
Isaac 窗口；无人值守任务运行则用 `--task-checkpoints --headless --trials 1`。
终端输出的每轮目录中包含 `last_restore.json`（瞬时读回）、
`trajectory_verification.json`（后续轨迹）和有校验和的 JSON/NPZ 检查点；重复
启动不会覆盖上一轮目录。

灭火器抓取会禁用原始刚体并创建视觉代理。刚体启用标志属于可恢复状态，不应被
当成“刚体已删除”：恢复抓取前保存点会移除后来的代理，恢复原对象属性；恢复
携带中保存点会在必要时重建代理，并保持原对象隐藏、禁用。随后工具
同步 PhysX 缓冲的变更，再读取刚体视图；不靠多推进物理一步来重建刚体。声明
范围外真正新增/删除的刚体仍会被拒绝，并显示具体路径。USD 运行时检查点组件
已升级为 v2；旧窗口必须重启才能加载修复，旧会话的保存点不能跨进程继续使用。

## 交互式任务分支

要在事实运行结束后回到 EMOS 首次任务分配附近，修改 assignment，再从同一个
物理 checkpoint 启动分支，必须使用带 Dashboard 的非 headless 模式：

```bash
python -m demo.fire_rescue.main \
  --env=EAI-Factory-v0 --device=cpu --trials=1 --auto-fire-delay=0 \
  --branch-interactive \
  --branch-checkpoint-dir=tmp/replay/fire_rescue_branch
```

事实分支会继续运行到成功、失败或超时；完成后 Dashboard 的 EMOS 面板会显示
assignment JSON 编辑框。点击“提交修改”，再点击“开始分支”。启动时工具会恢复
同一进程中的机器人位姿、速度、关节状态、控制目标、导航队列、动态障碍状态和
随机数状态，然后用编辑后的 assignment 替换原任务并继续仿真。

`--branch-interactive --headless` 会被入口拒绝，因为 headless 模式没有可消费的
Dashboard 命令。使用 `--replay-local-fallback` 可以让 Factory 示例跳过 LLM 网络
延迟，更快到达 checkpoint；不使用该选项时，若 assignment 到达时 UR5 正在执行
不可恢复的拖拽阶段，工具会把状态标记为 `checkpoint_pending`，等待下一个安全
checkpoint，而不会损坏事实运行。重复运行同一个输出目录时，工具会保留旧的
`trial_01`，自动使用 `trial_01_run_02`、`trial_01_run_03` 等新目录，不会覆盖旧轨迹。

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

Factory 占据地图是场景 provider 资产，路径为 `EAI_USD_ROOT/scene/factory/factory_map.yaml` 及同目录 PNG。`default_factory_map_yaml()` 通过 `EAI_assets.asset_resolver` 确保该外部文件对；Demo 不再在 `demo/fire_rescue/assets/` 中保存或回退到地图副本。

## Factory JSON 要求

`source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-v0.json` 声明 Factory 场景和四台机器人：

- `carter_1`：Carter 差速底盘，Orsus 数据采集。
- `m20_1`：M20 + UR5。
- `m20_2`：M20 + UR5。
- `scout_1`：Scout + UR5，负责救援通道按钮。

附件使用实验最小配置：

- 只有 `carter_1` 挂载 Orsus；
- `m20_1`、`m20_2` 和 `scout_1` 只挂载任务所需的 UR5；
- Fire Rescue 不使用 Nav2，JSON 中不挂载 `navigation_io`，启动时也不加载 Isaac ROS Bridge。

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
├── README.md                 # English documentation
├── README.zh-CN.md           # 本说明
├── main.py                   # CLI 和 SimulatorSession 启动入口
├── experiment.py             # 多轮实验编排和 env_cfg hook
├── config.py                 # Demo 参数、任务目标和实验出生位置
├── scenario.py               # EMOS 场景、机器人能力和任务解释
├── algorithm_paths.py        # 仓库内 algorithm 导入与 provider Factory 地图
├── llm_compat.py             # OpenAI 兼容修复
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
