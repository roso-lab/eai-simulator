# Warehouse NVD benchmark 应用

goal 轨道新增 `native_witness_probe.py` 原生固定点/dock 微诊断，须在
`env_isaaclab` 下运行，场景间串行。先用 `--device cpu --output-root <新目录>`
冻结输入，再用 `--execute --output-root <同一目录>` 启动独立 Kit worker。
它沿用公共物体参数，明确记录诊断载具起点，使用统一物理验证锁。
`dock_receiver.py` 检查完整旋转外形在候选 dock AABB 内、物体身份及质量、
约束实际移除、释放后的新物理步与支撑高度；不能只凭中心接近就发接收回执。
该回执仅证明候选区域的释放后读回，不证明完整 dock 服务或联合承载。
`native_carry.py` 只小步调整机器人侧连接器目标，物体响应由 PhysX 产生；
诊断检查真实离地净空及运输中的最低净空，再下降、解除约束并等待新物理步。
目标更新本身不是提升成功的证据；双机负载分担与成员退出恢复仍需独立实测。
任务只有木箱和纸箱，不要求 service car；诊断路径不作为参与者必须采用的策略。

[English](README.md)

本 demo 的任务蓝图、资产白名单、实验配置由 `scenario.py` 维护；`config.py` 指定场景 USD 和占据地图。`scenarios/business_logistics_01.json` 是默认的业务挑战输入，`scenarios/navigation_smoke.json` 仅用于显式导航启动检查。生成的任务池和实验结果放在 `tmp/`。

在仓库根目录、`env_isaaclab` 环境中运行：

```bash
python -m demo.warehouse_benchmark.main --check-assets
python -m demo.warehouse_benchmark.main --scenario demo/warehouse_benchmark/scenarios/navigation_smoke.json --gui --steps 400
python -m demo.warehouse_benchmark.main --scenario <任务.json> --headless --report tmp/warehouse_benchmark/execution.json
```

运行公开 participant 基线（导航、空中扫描、等待与运动学运输 phase 由 participant 选择 agent/团队；运输交付目标属于任务语义不由 participant 改写，携带中的对象禁止改派，成员退出时对象在载具位置强制释放）：

```bash
python -m demo.warehouse_benchmark.main --scenario <任务.json> --headless \
  --participant-mode public_greedy --report tmp/warehouse_benchmark/participant.json
```

样例使用 Carter + 未缩放 Z1，执行一段短距离导航；检查场景组装、机械臂质量/惯量和导航，不验证抓取。默认 CPU PhysX。搬运任务目前只支持显式 `--transport-mode kinematic`，报告仍区分运动学搬运与真实物理操作。

任务 JSON 必须声明 `scene_key: warehouse_nvd`。机器人和附件始终经由 `simulator.py → open_simulator_session → env_builder` 构建，执行正常资产预检。`demo/benchmark/runtime.py` 复用会话，不另建 Isaac 应用。

资源目录、迁移和准备流程见 [公共说明](../benchmark/README.md)。机械臂保持源资产质量和惯量；任务容量策略不等于真实额定 payload，也不构成 E7 物理操作证据。

离线生成四层 benchmark 契约：

```bash
# 生成完整业务任务及 QA sidecar
python -m demo.warehouse_benchmark.challenge \
  --generate-scenario tmp/warehouse_task.json
# 以生成的业务任务发布四层包
python -m demo.warehouse_benchmark.challenge \
  --scenario tmp/warehouse_task.json \
  --output-dir tmp/warehouse_benchmark/challenge
```

`navigation_smoke.json` 只用于运行时组装和导航冒烟，不能作为正式业务包的
输入；正式编译器会在写出目录前拒绝它。公开 `public_task.json` 带有受限的再生成
骨架，包含经过审查的节点角色、原生道具角色、因子轴、团队生命周期和通信边界，
不包含隐藏干预时刻、对象真值或参考分配。

上述第二条命令复用 Warehouse 自己的任务蓝图和画像，生成 `release/` 公共任务包与
`evaluator/` 隐藏评估包。公开 fleet 给出 ground/aerial、移动方式和已配置附件接口，
额定 payload 保持未知。运行时 phase-ready 候选由这些公开约束筛选，不读取隐藏
`preferred_agent`；`transport_object` 和 `joint_transport` 已开放 participant 分配/改派：participant 选定的 agent 或团队会在该 phase 就绪时按实时位姿重建取放路线；对象所有权只经运行时 pickup/release 事件变化，携带中改派被拒绝。

评估器入口 `demo.warehouse_benchmark.challenge.evaluate_report` 在公共调度指标
之外检查物流通道、联合搬运和物体运输语义；这些检查仍属于任务逻辑证据，
不代表额定 payload 或真实抓取能力。

生成满足当前任务画像的多阶段任务：

```bash
python -m demo.warehouse_benchmark.challenge --generate-scenario tmp/warehouse_task.json
```

生成前设置 `EAI_NVD_ASSET_ROOT` 指向准备后的原生道具根目录。任务实例使用
`wooden_crate_a`、`cardbox_c` 的实测保守尺寸和估计质量；缺失资产不会回退为方盒。
生成入口会在写文件前执行 schema、二维摆放、连通性、共享执行器规划及连续三维飞行净空门禁。
同目录会写出 `warehouse_task.audit.json`，记录门禁结果和任务画像审核；这些内容只用于
发布 QA，不作为 participant 必须提供的证据。
当前 Warehouse DAG 是货架扫描、双侧通道进入、大件箱体联合转移、优先纸箱集货/出库、
dock 复核与通道释放，不再与住宅场景共用节点拓扑和业务命名。

正式模板固定使用 Iris 的几何和接口承担空中扫描。NVD demo 默认采用
`--aerial-motion-mode kinematic`：以 0.6 m/s 有界推进，并继续对命令段和实测步间位移
执行连续 swept-AABB、场景 voxel、当前任务物体 AABB、有限局部绕行和逐控制步跟踪。
`--aerial-motion-mode controller` 保留为 provider 位置控制器的独立诊断。运动学模式只保证
benchmark 的基础运动接口，不证明飞行动力学、执行器限制、抗扰动或动态障碍安全；两种
模式的飞控表现都不计入异构协作能力。

当前生成组合只在 M20 上配置一套未缩放 Z1，Carter 保持基础导航配置，并扩大两台地面
平台的初始间距。该选择来自 Warehouse 多配置短运行的稳定性筛查，不是缩小机械臂或
降低质量：近距离多挂载组合曾产生非有限 PhysX 状态，现由运行器逐控制步检测并以
`physics_state_invalid` 失败关闭。单臂生成组合的短运行只能证明该窗口内根状态有限，
不建立抓取、额定载荷、碰撞安全裕度或载荷动力学结论。

Isaac 公共包开放已接入的分配、改派、导航和 `consume_message`。真实采集观测先经过
确定性通信队列，只有已送达且未过期的消息可被消费，依赖节点在声明的后继完成消费前
保持阻塞；participant `wait` 仍只在独立无运动协议会话中测试。evaluator 可通过程序化
`main(...)` 注入初始通信契约、隐藏条件 schedule 和生命周期 callback。当前维护场景尚未
配置预加载 reserve，也没有物理生命周期参考 episode。订单身份、观察/消费、实测交付和 dock 回执同时通过才算业务成功；
节点全完成只能获得调度结果。LLM 生成入口和因子约束见
[出题设计](../benchmark/BENCHMARK_DESIGN.zh-CN.md)。

当前源码参考报告为
`tmp/benchmark_release/warehouse_business_iris_kinematic_full_v8_20260917.json`。
它使用 `scene_public_greedy_v2` 和米制自动预算，在 5594 个控制步内完成 10/10 节点，
通过 2 个 RGB 窗口、2 次空中路线、地面逐步跟踪和物理健康检查；联合搬运记录 126 个
样本，最大成员间距 3.4279m，小于 3.6727m 上限。三条运动学 delivery 和最终放置谓词
通过；订单身份与 dock 回执因实时 adapter 缺失保持 false，因此
`business_task_success=false`，不得据此提升 release 状态。

下一次串行 Isaac 原生见证运行命令（显式选择 CPU 诊断设备）：

```bash
python -m demo.warehouse_benchmark.native_witness_probe \
  --output-root tmp/warehouse_native_witness_stage17 --device cpu
python -m demo.warehouse_benchmark.native_witness_probe \
  --output-root tmp/warehouse_native_witness_stage17 --device cpu --execute
```

执行阶段复用 `goal_attachment_probe` 的 deployment、`LiveBodies` 和原生固定点
receipt，在创建场景前显式打开 PhysX scene query，执行纸箱真实固定点承载、逐步
PhysX pose 读回、释放，再由 Warehouse 自有 dock receiver 按 runtime object path
和释放后的当前 pose 生成诊断回执。CUDA Direct GPU 下 CPU scene query 可能读取
陈旧 actor pose，因此携物净空证据保持 `unknown`；本 probe 不把 query 配置或空
query 当作 clearance 证明。木箱双机固定点/载荷读回仍明确保持 blocked。

公共运行器现已在真实帧发布后调用领域 hook。Warehouse 自有 adapter 在
`demo.warehouse_benchmark.live_witness` 校验 detector、dock 和对象 pose claim，Warehouse
入口不使用 reference provider。仓库当前没有随源码发布的真实 detector 权重或 dock
controller backend，因此上述 v8 结果仍保持 false。

Warehouse 自己的运行入口是
`demo.warehouse_benchmark.main.build_domain_evidence_provider(public_task, ...)`。
调用方可以传入分别实现 `detect`、`issue_handoff`、`sample_pose` 的货架身份、dock 服务和
对象位姿 backend，再把返回对象作为 `main(..., domain_evidence_provider=...)` 的参数。
该入口默认把 detector 绑定到货架身份扫描节点（也可以显式增加其他扫描节点），把 dock
服务和对象位姿绑定到出库观察帧。
任一 backend 缺失时，对应组件保持 `pending`，dock 两个 backend 不完整时不会调用半成品
交接链，业务证据保持 `unknown`/pending；不会根据运动学运输完成或仿真对象名伪造成功。
