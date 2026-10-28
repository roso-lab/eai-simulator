# room06 CPU 执行接缝阶段交接

日期：2026-10-28。Goal 保持 active。用户已要求释放 GPU；本轮没有启动 Isaac/CUDA，不关闭其他会话进程。正式资格门全 false。

## 已完成

1. 观测桥允许空分配决定到 horizon 后如实失败，不制造 assigned/成功里程碑。零成功轨迹经过 terminal assembler 与 oracle 后得到 success=false；畸形/缺失观测仍拒绝。会话去重允许最终交付与 acceptance 引用同一真实会话，但拒绝摘要变化；被拒绝事件重放不会被缓存吞掉。
2. 公开 pick_place schema/provider/executor 接通 operation/object_ref/semantic_point/可选 robot_ids；联合成员由算法选择，原样交账本验证。拒绝质量覆盖、自报成功和物理几何字段。旧物理编译与显式诊断执行保留，默认生产不回退。
3. simulator source 注册改用 object_bindings，取消已废弃 object_path_bindings 要求。测试使用真实 source 工厂验证此接缝。
4. 新增显式 room06_runtime_setup 与 EAI.room06_runtime_host.install_room06_runtime。冻结 package/部署身份在安装前校验，不覆盖已注册状态。注意：Room06TaskDriver 目前只是 run_case 委托，不是实际 Isaac 控制循环；可调用的回调也不等于真实执行证据。
5. 用户已确认分散后的五台出生布局；GUI 已关闭。历史 GUI/动态诊断原样保留，暂停 GUI 不证明当前布局动态任务成功。

## 本轮验证

解释器 `/home/pupil/miniconda3/envs/env_isaaclab/bin/python`；环境 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs EAI_USD_ROOT=/home/pupil/HeteroCoord/usd`。

```sh
python -m pytest -q \
  benchmark/tests/test_room06_observation_bridge_v1.py \
  benchmark/tests/test_room06_terminal_material_v1.py \
  benchmark/evaluator/test_room06_livt04.py \
  source/EAI/test/test_room06_runtime_hook.py \
  source/EAI/test/test_room06_runtime_source.py \
  source/EAI/test/test_simulator_room06_hook_registration.py \
  source/EAI/test/test_room06_runtime_host.py \
  tools/validation/test_room06_livt04_gpu_probe.py \
  task/tests/test_logical_objects.py task/tests/test_transport.py \
  algorithm/skills/tests/test_simulator_schema.py \
  algorithm/skills/tests/test_contracts.py --tb=short
```

结果：265 passed in 1.08s，退出码 0；相关实现 compileall 与 git diff --check 均退出 0。仅 CPU 证据，不代表 GPU full-task。未跑提交边界全量、未提交或推送。

ZCode plugins/MCP 均返回未找到打开的桌面或 CLI，本轮直接执行上述固定命令。原生 reviewer 曾确认旧 source 注册接口问题，主线程修复；其后句柄不再出现在 live agent 列表且未产出 driver handoff，故不声称完成独立 driver 审查。

当前 package：`ced86d09206397d64f79529a2d3ca52897d523eba6567415b78d640dff91604e`。
当前 probe input：`33fed82e3ebfdcc56ec0be75a376e4c665c8e2bbf97a7058eea9e214e779e815`。
CPU load_probe_input + prepare_full_task 已通过；后续真实 driver 与代码冻结仍需更新，不能把文件入口可导入解释成 source 就绪。

## 下一步实现顺序

1. 给新增宿主 setup 接上实际 controller arrival、技能会话 producer、每拍联合持有监测；同一宿主步进循环，不创建第二仿真时钟。复用现有 NavigationExecutor/控制器适配器，而非脚本预选获胜联盟。
2. 接真实被测系统策略和模型计量入口；EMOS generic round-robin 只能作为 CPU fixture，不能标成真实 EMOS。
3. 从运行时首计划、事件时间线和通信计量生成 D1/D4/D6，禁止用空对象或常量填充。完成真实源后，再生成 terminal material、receipt。
4. 处理旧 handoff=node:0001（实际一楼书架）的运行时语义绑定，不重新要求用户绘制楼梯标签，也不擅自改变拍板谓词。
5. 用户释放 GPU 后再执行 full-task；失败也保留完整观测及收据，不静默重跑。随后在推送边界执行一次全量并汇总阶段进度。

## 并行修改与反思

### 续推：逐拍 driver（CPU）

新增 ControllerBackedRoom06TaskDriver，实际对接 Room06RuntimeHookCollector：逐拍推进宿主整数时钟、从逻辑账本采样持有、经真实 session_record 验证事件；到达 live horizon 或已认证 acceptance 后关闭 collector。空选择的 15,000 拍测试经实际 source 与 collector 返回到期记录，未产生成功事件。driver 禁止再次启动，不静默重跑；拒绝跳拍、布尔时钟、未知机器人、伪造事件、运行步数耗尽和 package 修改。

测试先发现每拍深拷贝约 800 KB package 的性能问题；手动终止该 CPU 测试（3 passed/148.80s，退出 2），改为每次运行复制一次、终局核对摘要。修复后 host/hook/source 30 passed in 1.22s；新增反例后的 host 专项 14 passed in 0.84s，退出均 0。测试使用实际 collector 类及模拟时钟，不是 GPU 证据。

该 driver 仍要求实际 Isaac step/decision/clock/terminal_inputs 回调，尚未通过生产入口自动接线；不能把此合同层完成视为完整 runtime source 就绪。未启动 GPU、未修改冻结地图/部署池、未提交推送。

### 续推：语义操作点到 live articulation（CPU）

新增 `EAI.logical_object_controller.IsaacLogicalArrivalReader`。它读取 root state、速度、四元数和 Pepper 的 XDisp/YDisp/ZRot 虚拟底盘状态，使用宿主登记的 semantic-point → navigation-node XYZ、root height、到达半径和停止速度，产生带完整 readback 摘要的 `ControllerObservation`。它不读任务物体、不读 attachment、不选择联盟、不推进仿真。错误楼层、未停止、非单位四元数、缺点和非法时钟均 fail-closed；7 项专项测试通过。该 reader 可作为 `install_room06_runtime(observe=...)` 的 live callback，仍需 Isaac 配置层提供各实例的节点绑定与控制器 session producer。

本节结束时受影响集合（前述命令加 source/EAI/test/test_logical_object_controller.py）：281 passed in 1.79s，退出 0。新增六书逐本移交 + 双机持有 + 终局验收的 CPU 合同轨迹，经实际 source/collector/bridge/assembler/oracle 得到 success=true；这只证明代码接缝可工作，模拟回调不作为 GPU 成功证据或任务可行性证明。未执行全量套件。

保留 BENCHMARK.md、todolist.md 删除、P2_CROSS_DOMAIN_EXEMPLAR_DRAFT、topology_authoring/launch.py、d1_mount.py 与 d1_mount_check_plane.json 等其他会话修改。本轮观察到 PID 2240176 运行 simulator.py --env=d1_mount_check_plane，未触碰。

框架中“逻辑账本已实现”“公开 skill 能调用”“simulator 能注册”“真实 driver 已产证”是不同完成条件。本轮修复了前两层之间的实际断点，但后两层不可靠新增委托类宣称完成。接下来优先实现真实回调与调度，避免持续堆叠 wrapper/测试而不连接生产。

### 续推：GPU 释放授权恢复、决策边界修复

用户重新授权使用 GPU，撤销此前为地图绘制暂停 GPU 的安排。只读查询显示 RTX 6000 总显存 24576 MiB、已用 1743 MiB；此为瞬时资源观察，不是运行收据。本轮未启动仿真、未触碰 ToDesk。

发现并修复 ControllerBackedRoom06TaskDriver 将整个冻结 evaluator package 交给算法回调的问题。现要求宿主单独组装公开 EaiTaskPackage，经统一 DecisionProvider(task, robots) 返回 AlgorithmDecision；完整部署名册必须公开，不做任务适配性过滤。私有 package 与 instance→agent 别名仅由宿主持有。完整逐任务 decision 传入宿主 step，不能只保留联盟 ID 并丢失任务指派。决策摘要由宿主计算；未知任务/机器人、重复指派、同时 assigned/unassigned、旧字典返回均拒绝。宿主仍须确保公开任务的 metadata 仅来自公开输入，本次并未完成该生产组装器。

ZCode 能力检查仍返回 “Open one ZCode desktop version, or set ZCODE_CLI to a local CLI path.”，因此主线程执行受影响测试：

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs EAI_USD_ROOT=/home/pupil/HeteroCoord/usd /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test/test_room06_runtime_host.py source/EAI/test/test_room06_runtime_hook.py source/EAI/test/test_room06_runtime_source.py --tb=short
```

结果 40 passed in 1.78s，退出 0。包括公开/私有隔离、全部名册可见、决策完整传至宿主和非法合同拒绝。仅 CPU 合同证据；正式门未开启。生产 setup、真实技能 producer、计量终局仍待接线，不能声称 room06 GPU full-task 已通过。未执行全量、未提交推送。

### 续推：公开任务投影与真实 scene-load 诊断

`public_task_package` 从冻结 task setup 展开六书和重物，输出七项 typed task，保留质量、目标、最低持有者数、书籍接力要求和全部五台部署机器人，不生成联盟、不复制 oracle/preflight。语义点显式投影 label/XYZ；真实机器人公开能力仍需宿主从权威名册提供。GPU probe 的 programmatic `run_full_task` 已能将 runtime setup 传给 SimulatorLaunchConfig；实际 setup/producer 未完成，CLI 仍不能据此运行完整任务。

受影响验证：resident-instance + runtime-host 35 passed；新增投影反例后 resident-instance + gpu-probe 46 passed in 0.40s，退出 0；git diff --check 通过。未跑全量。

GPU 诊断 `artifacts/project_runtime/room06-scene-load-20261028-07/report.json`：真实 cuda:0 / GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0，room06 USD e67affcbd6d67941feb8bc7728d6be4cd74ad63d414a8b65db771337b76e300a，64 步，physics_dt=0.005，退出 0。没有机器人任务技能、持有观测或任务 receipt，全部资格门 false。

前两次命令在模拟启动前失败，均为主线程命令组织错误：预创建输出目录；混淆 canonical digest 与 file SHA-256 且误用 full-task 输入。修复为独立 scene-only 输入与新目录，未覆盖任务冻结件。失败审计在同目录 `run_failure.json`。前后 df/free 记录：可用磁盘 506G/506G，可用内存 236Gi/235Gi；前后均无 telemetry 进程，ToDesk 未触碰。空的 -05 目录用 rmdir 移除，无运行数据丢失。

后续重点仍为实际 controller/skill session、公开 map/roster 组装接入真实算法、测量终局的生产闭环。本次 scene-load 仅确认环境运行，不重复将其解释成 full-task 进展。

### 续推：生产 setup 接缝

新增 `install_controller_backed_room06_runtime`：一次性校验并绑定 typed `DecisionProvider`、宿主 step、真实 clock、arrival reader、skill session、准入与逐拍 monitor；生产 setup 不再以空 `run_case` 覆盖 driver。`operation_nodes_from_semantic_products` 按 semantic map 发布的导航域和节点位置做纯投影，忽略无名途经点，不猜测楼梯或跨域路线；未知绑定仍 fail-closed。新增 setup/地图域测试后 source 专项 30 passed。

这仍不是 GPU full-task 完成：当前 `run_full_task` 的调用方尚未提供这些真实回调，也没有从 Isaac 控制器事件生成完成 skill session 和 D1/D4/D6 测量。若缺失，入口应拒绝任务，不得使用空事件或常量结果。下一步应在 SimulatorLaunchConfig 的 room06 setup 调用方组装公开任务、按地面/空中控制器绑定 operation nodes，并接入已有 NavigationExecutor/skill backend 的真实 session 记录。

### 续推：逻辑技能会话 owner

新增 `EAI.logical_skill_sessions.IsaacLogicalSkillSessions` 并在 controller-backed installer 中直接绑定：逻辑操作完成前读取当前拍 arrival 和 articulation root state，记录 command/readback 摘要及宿主会话 ID；联合持有监测核对活跃会话与每拍有限 root state，显式取消或缺失读回令账本中断。释放拍保留旧 session 的连续性证据，下一拍不再 active，避免账本提交前的末次检查被自身释放误伤。没有物体几何、attachment 或物理抓取。

限制：有限 root state 不是控制器全功能健康认证；终止/失活需要宿主生命周期调用 `cancel_robot`，尚未接入该事件源；session owner 还没有 checkpoint 恢复协议，不能据此声称 replay 支持已完成。当前局部逻辑操作为同拍原子会话，真实到达需 arrival reader 证明，运输必须由真实导航驱动。测试仅用替身 articulation 数据，未执行 GPU 技能。

受影响命令（env_isaaclab、原 PYTHONPATH）：pytest -q source/EAI/test/test_logical_skill_sessions.py source/EAI/test/test_room06_runtime_host.py source/EAI/test/test_logical_object_controller.py benchmark/tests/test_room06_resident_instance_v2.py tools/validation/test_room06_livt04_gpu_probe.py --tb=short；79 passed in 1.56s，退出 0；git diff --check 通过。未运行全量、未提交推送。下一步仍需把实际导航调度、失活事件和 terminal measurement 接入运行调用方，正式资格门保持 false。

### 续推：Isaac 生命周期与公开任务组装

`IsaacLogicalSkillSessions` 现在读取 `reset_buf`、`terminated_dict`、`time_out_dict` 和 `_manipulation_reset_generation`。任一信号变化都会撤销对应机器人 session；逐拍 monitor 立即返回 inactive，联合持有账本 fail-closed。无名语义途经点不作为操作点，ground/aerial 域不会混用。

GPU probe 新增 `assemble_public_room06_task(prepared)`：重验 fresh preflight、冻结 package 和 roster 路径，从五台部署机器人能力绑定与出生位置构造 typed `EaiTaskPackage`，从 semantic-points 展开 12 个公开点，任务图为六本书+重物七项；输出的 task 不含 oracle/preflight/private alias，也不含联盟选择。现场调用验证输出 `liv-t-04 / 5 robots / 7 tasks / 12 map points`。

受影响测试：`source/EAI/test/test_logical_skill_sessions.py source/EAI/test/test_room06_runtime_host.py tools/validation/test_room06_livt04_gpu_probe.py` 共 60 passed in 1.43s，退出 0；git diff --check 通过。仍未启动 GPU full-task：公开任务已有组装器，但真实 tested-system decision、导航 step、skill terminal event 和 D1/D4/D6 仍需由 Simulator setup 调用方提供；正式门保持 false。

### 续推：full-task 启动边界收紧

`run_full_task` 现在在生产冻结包（含 package digest、plan、task setup、scene deployment、preflight）下先组装并核对公开 `EaiTaskPackage`，再要求调用方提供 `room06_runtime_setup`。缺失真实 decision/step/session/terminal callbacks 时在 Isaac 启动前 fail-closed；不会产生空事件或伪造 receipt。旧的最小 hook 单测夹具仍兼容。

受影响回归共 **83 passed in 1.65s**，退出 0；`git diff --check` 通过。当前仍未执行 full-task GPU：缺失项已经从隐式空回调变为明确的启动前契约错误。下一步必须由真实 Simulator setup 提供 EMOS/TeamWeaver decision provider、NavigationExecutor step、逻辑 session owner 和 D1/D4/D6 measured terminal callback，之后才有资格启动 full-task。

### 续推：typed EMOS baseline 与启动前契约

新增 `benchmark.emos.EaiTaskRoundRobinAdapter`，实现统一 `initialize(H_r, o_r)` 和 `DecisionProvider(EaiTaskPackage, robots)`：只消费公开历史/位置，完整部署池可见，输出 typed `AlgorithmDecision`，不声明执行成功。它是可执行的诊断 baseline，不等同论文正式结果；真实任务仍需由宿主将 decision 交给导航/技能执行器。

`run_full_task` 在真实冻结包下会先组装公开任务，再要求 `room06_runtime_setup`；无该 setup 会在模拟进程启动前拒绝。测试夹具仍可验证 source factory 的旧边界。EMOS 专项 6 passed；此前受影响集 83 passed。当前没有新增 GPU 运行；剩余生产缺口是把该 provider（或其他算法 adapter）接入逐拍 NavigationExecutor、session owner 和真实 D1/D4/D6 measured callback。

本轮复核 `NavigationExecutor` 的实际生命周期为 `prepare → host advance → after_step`：到达和停止必须由 adapter 的 live pose/velocity/sequence 证明。没有用 round-robin 结果模拟移动，也没有把技能准入当成完成。EMOS baseline 的位置校验补全为有限三元组，受影响回归保持 **66 passed in 1.47s**，`git diff --check` 通过。

下一步需要一个真正的 host bridge：将 `AlgorithmDecision.assignments` 映射到已注册的 `NavigationExecutor` 请求；每次 `after_step` 后读取完成响应，再调用逻辑 session owner；重物任务必须等待至少两个实际 assignment/session，不能由 baseline 自动补第二台。当前 room06 setup 尚未完成各平台 `NavigationControllerAdapter` 注册，因此继续保持 full-task 不启动。

### 续推：NavigationExecutor 宿主 bridge

新增 `EAI.room06_navigation_bridge.Room06NavigationBridge`。它先原子校验 typed decision 和每个 robot 的已注册 adapter，再建立逐机器人任务队列；每个 host tick 对所有机器人执行 `prepare`，共享推进一次 Isaac，再执行全部 `after_step`，避免按机器人重复推进仿真。导航只有在 executor 的 stop confirmation 成功后才产生完成事件；同一机器人后续任务必须等待前一任务退出，未知/重复/部分非法 decision 不会留下队列。

用真实 `NavigationExecutor`、`PlanarVelocityNavigationAdapter` 和 host tick harness 验证：bridge 专项 4 passed，受影响集共 **70 passed in 1.43s**，`git diff --check` 通过。该 bridge 尚未注册到 room06 Simulator setup，也尚未把导航完成事件转换成 pick/handoff/deliver session；重物仍需系统输出两个真实 assignment。GPU full-task 继续保持未启动。

### 续推：导航到逻辑技能的 coordinator

新增 `EAI.room06_execution_coordinator.Room06ExecutionCoordinator`：host-owned leg binding 将已由 NavigationExecutor stop-confirmed 的导航事件映射到逻辑账本 `execute`，再生成 observer 可验证的 `observed-skill-event`。导航响应本身不会直接成为 pickup/handoff/delivery；未知绑定、holder 不一致或失败导航均不会写对象状态。

用真实 `NavigationExecutor` + `PlanarVelocityNavigationAdapter` harness 验证导航完成后才提交逻辑 pickup；受影响集 **71 passed in 1.55s**，`git diff --check` 通过。当前 coordinator 尚未接入生产 setup 的实际 leg bindings；六书接力、重物双 assignment 和 D1/D4/D6 仍未形成 GPU receipt。

### 续推：room06 leg binding 模板

新增 `room06_leg_bindings`，从冻结 task setup 展开六本书各自的 pickup→handoff→delivery，以及重物 pickup→delivery 模板。模板只包含 host-owned operation/object/semantic-point/event 类型，不包含机器人 ID；机器人归属仍由被测系统的真实 assignment 和 host session 决定，避免把联盟泄露给算法。重物 pickup 不生成伪 milestone，联合持有状态由逻辑账本观察。

`Room06ExecutionCoordinator` 接受导航完成后填入实际 robot session，并将成功 session 转换为 observer skill event；没有 event type 的逻辑 pickup 仍只更新账本。模板/host 回归 28 passed in 1.38s，git diff --check 通过。尚未把 public `transport:<object>` assignment 自动展开为第一条 leg，也未在 Isaac setup 注册每个平台 adapter；因此仍未执行 GPU full-task。

本轮修正 coordinator 的多机语义：公开 `transport:heavy_object` assignment 会展开为 host `pickup:heavy_object` leg；所有实际机器人先在导航完成集合中 rendezvous，只有集合完整且每个机器人 stop-confirmed 后才调用一次联合逻辑 session。重复/不完整到达不会重试会话，失败 leg 不写账本。六书模板仍由单机 assignment 逐 leg 运行，handoff/delivery 继续由后续 host 调度产生。

受影响回归：coordinator + navigation + logical session + runtime host + EMOS + probe 共 **73 passed in 1.55s**，`git diff --check` 通过。尚未在 Isaac setup 注册 bridge 或提供连续多阶段 host scheduler，故 GPU full-task 仍不启动。

### 多轮 host leg 提交（CPU）

Navigation bridge 接受后续新 assignment，但同一 leg/robot 不得重复提交。Coordinator 将公开 transport ID 映射到首个尚未成功完成的阶段；pending、已消耗或失败阶段不允许重提，失败不能跳过。新增双机 pickup 成功后重新提交 transport、汇合 delivery、拒绝进行中重复与终局重提的账本测试；coordinator/navigation 6 passed in 0.12s。测试替代了导航事件与控制器读回，不是 GPU 证据。

生产 driver 目前仍只调用一次 DecisionProvider，本轮没有接通运行中的重新决策；不能把 bridge 支持多轮表述为六书接力已经可运行。下一步需要公开运行反馈和重新决策生命周期，以及当前持有者到 handoff 的导航调度；不得由 host 指定接收机器人。

### 多机到达等待与阶段先后纠正

此前到达较早的机器人可能在另一成员到达前离开执行排队任务。新增 operation barrier：coordinator 收到完成导航后保留该成员，直到联合逻辑操作成功才释放排队调度；不改变算法选择的成员，不增加机器人。未知结果或失败不会被当作成功放行。另补直接 host leg ID 的前置阶段检查，防止绕开 transport 展开逻辑而跳过 pickup/handoff。

真实 NavigationExecutor 加替身控制器验证“已停止但联合操作未完成时不启动下一任务”，桥接/协调器 7 passed；受影响集合 74 passed in 1.45s，git diff --check 通过。没有新增 GPU 运行，没有任务 receipt；生产重决策、平台 adapter 和终局测量仍未接通。

### 生产 setup 完整性门

`run_full_task` 的 setup wrapper 现在在 Isaac reset 后检查宿主是否安装四个真实 seam：`logical_object_runtime`、`room06_task_driver`、`room06_navigation_bridge`、`room06_execution_coordinator`。任何一个缺失都会在 observer/terminal capture 前 fail-closed，避免“setup 可调用”被误认为 source 可用。测试/旧夹具仍在启动前通过自身边界验证；真实生产包尚未提供 setup，因此没有启动 GPU full-task。

本轮 probe + coordinator/navigation 回归 **38 passed in 0.32s**，`git diff --check` 通过。当前仍缺真实 adapter 注册、连续多轮决策入口和由通信/事件账本计算的 D1/D4/D6 terminal callback；正式资格门保持 false。

## 阶段收尾与交接（2026-10-28）

本阶段代码已整理为可供后续协作者继续接线的 CPU/合同层基线，提交时只纳入以下 room06 逻辑执行范围：逻辑任务对象与 checkpoint participant、公开任务投影、arrival reader、logical skill sessions、navigation bridge、execution coordinator、runtime host setup seam、EMOS typed baseline、room06 manifest/preflight/observation/transport 接缝，以及对应测试和 GPU probe 的启动前契约。任务对象不生成 USD、刚体、attachment 或物理重建；正式资格门与认证 flags 仍全部为 `false`。

当前已验证：逻辑对象账本、逐本书 pickup→handoff→delivery 模板、重物多机 rendezvous、联合持有中断 fail-closed、公开机器人池不预选联盟、Isaac arrival readback、reset/termination/timeout 生命周期撤销，以及生产包缺少真实 setup 时在模拟器启动前拒绝。已有真实 GPU scene-load diagnostic 位于 `artifacts/project_runtime/room06-scene-load-20261028-07/report.json`，它只证明 CUDA/room06 USD 可加载，不是 full-task receipt。

接手者必须完成：在 Simulator setup 中为五台公开部署机器人注册真实 `NavigationControllerAdapter`，绑定语义操作点和 `NavigationExecutor`；将真实 tested-system adapter 接入多轮公开 decision/feedback；由 live controller/session 事件生成 D1、D4、D6 terminal measurements；再接入 checkpoint/replay 的 bridge/coordinator/session restore。缺少任一项时保持 fail-closed，不得用空对象、常量或 CPU fixture 生成 GPU/论文证据。完成这些接缝后，才可执行 room06 × liv-t-04 full-task 并生成 receipt；本阶段没有声称该结果已通过。

本阶段推送前验证按“受影响集日常、全量一次”执行。若全量存在与本阶段无关的既有失败，应在交接中保留原始退出码和失败项，不得把 focused 结果升级为全量绿。工作树中未纳入提交的语义地图、机器人 skill、论文和其他协作者改动保持原样，后续由相应负责人继续处理。

### 推送确认

- 本阶段实现提交：`91afbd07`（`feat(room06): close logical execution seams and hand off runtime`）。
- 远端协作者提交：`617fa82d`（D1 挂载标定）；经合并提交 `d7f155d8` 整合并已推送 `origin/HeteroCoord`。
- 受影响集：`262 passed`；compileall 与 `git diff --check` 退出码 0。
- 推送边界 benchmark 全量：`1109 passed / 6 failed`，退出码 1。6 项均为历史机器人收据校验，原因是工作树中未纳入本阶段的 `source/EAI_assets/EAI_assets/robots/d1_mount.py` GUI 标定版本与收据记录的提交字节摘要不一致；本阶段提交未修改该文件，失败未被隐藏或重跑。
- 当前正式资格门、认证 flags、GPU full-task receipt 仍为 false/未生成；可交接给后续协作者继续完成真实 adapter、multi-round feedback、D1/D4/D6 measurement 与 full-task source。
