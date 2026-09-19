# Modern House NVD benchmark 应用

新增 goal 轨道的实时住户通路诊断位于 `live_path.py`。`goal_physical_probe`
在同一 Isaac session、同一物理步对明确的候选折线执行正反向连续 box sweep，
公开净宽为 0.9m、净高为 2.0m；家具、书堆和所有停驻机器人均保留为障碍。
上下边界使用显式 1mm 接触容差，支撑面按不超过 0.1m 的间距取中心及四角射线。
射线支撑采样不等价于连续楼面证明；该诊断也不证明家具搬运或任务成功。
对轴对齐静态 Cube 楼面，另以实际启用碰撞体的连续平面并集检查完整通路覆盖，
包括采样点之间的窄缝；只将完全位于脚底平面以下的已核验静态 Cube 视为支撑接触。
其他机器人、物体和未知几何不做排除。静态几何覆盖与实时射线证据分别记录。
无 GUI 时必须显式启用 `env_cfg.sim.enable_scene_query_support`，否则返回未知，
不能把空查询视为通路畅通。该入口已启用此配置。

```bash
# env_isaaclab；一次只执行一个场景。输出目录必须是新目录。
python -m demo.benchmark.goal_physical_probe --scene modern_house_nvd \
  --steps 60 --gui --output-root tmp/house_resident_path
python -m demo.benchmark.goal_physical_probe --execute --output-root tmp/house_resident_path
```

结果位于 `physical_report.json` 的 `resident_path` 字段，包含完整分段查询、
碰撞对象路径、支撑采样和 episode/物理步来源。初始机器人仍在动线内时，
通路查询应保持 blocked；这描述当前状态，并不判定题目作者审核失败。
机器人停留、会车和撤离时机由参与者选择；只有不合理初始布局、强制点位冲突
或不存在可行解才属于出题缺陷。不得排除机器人来生成最终净空证据。此 goal 轨道公开阈值
与下文历史 fixed-DAG 的 0.8m 要求分属不同任务版本，不可混用。

增加 `--house-withdrawal --steps 1200` 可执行有界撤离诊断：先静置60步，
通过原生控制器把 M20_1 和 Iris 移到诊断停车/悬停点，再对同一通路复测。
它不修改根位姿、不移除碰撞体；完整指令与 PhysX 状态保存在报告内。
诊断目标尚非已验收的公共 standby 区域，不可将其直接计作正式任务完成，
也不要求参与者采用这组位置或动作。物体生成须计入本场景 0.25m 楼面高度；
旧 CPU 撤离 v4 中书堆埋入楼板，因此该结果仅验证检查器响应，不作为业务证据。

Direct GPU 模式会抑制 CPU 位姿读回，因此普通 PhysX 场景查询可能命中旧的动态位置。
入口检查 `is_readback_suppressed()` 并将此类动态净空标为 unknown，不因张量位姿已更新
就放行。仅对明确选用 `--device cpu` 的作者诊断验证同步查询；不静默修改生产运行设备。
此限制见 [NVIDIA Direct GPU API](https://nvidia-omniverse.github.io/PhysX/physx/5.5.1/docs/DirectGPUAPI.html)。

[English](README.md)

本 demo 的任务蓝图、资产白名单、实验配置由 `scenario.py` 维护；`config.py` 指定场景 USD 和占据地图。`scenarios/home_recovery_01.json` 是默认的业务挑战输入，`scenarios/navigation_smoke.json` 仅用于显式导航启动检查。生成的任务池和实验结果放在 `tmp/`。

在仓库根目录、`env_isaaclab` 环境中运行：

```bash
python -m demo.modern_house_benchmark.main --check-assets
python -m demo.modern_house_benchmark.main --scenario demo/modern_house_benchmark/scenarios/navigation_smoke.json --gui --steps 400
python -m demo.modern_house_benchmark.main --scenario <任务.json> --headless --report tmp/modern_house_benchmark/execution.json
```

运行公开 participant 基线（导航、空中扫描、等待与运动学运输 phase 由 participant 选择 agent/团队；运输交付目标属于任务语义不由 participant 改写，携带中的对象禁止改派，成员退出时对象在载具位置强制释放）：

```bash
python -m demo.modern_house_benchmark.main --scenario <任务.json> --headless \
  --participant-mode public_greedy --report tmp/modern_house_benchmark/participant.json
```

样例使用 Carter + 未缩放 Z1，执行一段短距离导航；检查场景组装、机械臂质量/惯量和导航，不验证抓取。默认 CPU PhysX。搬运任务目前只支持显式 `--transport-mode kinematic`，报告仍区分运动学搬运与真实物理操作。

任务 JSON 必须声明 `scene_key: modern_house_nvd`。机器人和附件始终经由 `simulator.py → open_simulator_session → env_builder` 构建，执行正常资产预检。`demo/benchmark/runtime.py` 复用会话，不另建 Isaac 应用。

资源目录、迁移和准备流程见 [公共说明](../benchmark/README.md)。机械臂保持源资产质量和惯量；任务容量策略不等于真实额定 payload，也不构成 E7 物理操作证据。

离线生成四层 benchmark 契约：

```bash
# 生成完整业务任务及 QA sidecar
python -m demo.modern_house_benchmark.challenge \
  --generate-scenario tmp/modern_house_task.json
# 以生成的业务任务发布四层包
python -m demo.modern_house_benchmark.challenge \
  --scenario tmp/modern_house_task.json \
  --output-dir tmp/modern_house_benchmark/challenge
```

`navigation_smoke.json` 只用于运行时组装和导航冒烟，不能作为正式业务包的
输入；正式编译器会在写出目录前拒绝它。公开 `public_task.json` 带有受限的再生成
骨架，包含经过审查的节点角色、原生道具角色、因子轴、团队生命周期和通信边界，
不包含隐藏干预时刻、对象真值或参考分配。

上述第二条命令复用 Modern House 自己的任务蓝图和画像，生成 `release/` 公共任务包与
`evaluator/` 隐藏评估包。公开 fleet 给出 ground/aerial、移动方式和已配置附件接口，
额定 payload 保持未知。运行时 phase-ready 候选由这些公开约束筛选，不读取隐藏
`preferred_agent`；`transport_object` 和 `joint_transport` 已开放 participant 分配/改派：participant 选定的 agent 或团队会在该 phase 就绪时按实时位姿重建取放路线；对象所有权只经运行时 pickup/release 事件变化，携带中改派被拒绝。

评估器入口 `demo.modern_house_benchmark.challenge.evaluate_report` 在公共调度
指标之外检查房间入口、空中复核和物体复核语义；这些检查不构成真实家具搬运、
碰撞安全或机械臂物理操作证据。

生成满足当前任务画像的多阶段任务：

```bash
python -m demo.modern_house_benchmark.challenge --generate-scenario tmp/modern_house_task.json
```

生成前设置 `EAI_NVD_ASSET_ROOT`，任务实例采用 `mh_armchair`、`mh_bookstack` 的
实测保守尺寸和估计质量。生成入口执行 schema、二维摆放、连通性、共享执行器规划
以及连续三维飞行净空门禁，避免
“离线连通但运行器无法为后续节点建路”的任务进入任务池。当前住宅 DAG 是入口侦察、
家具双侧协作、家具移动后净空复核、小件暂存、地面成员撤离、最终房间扫描和确认；它不再
复用 Warehouse 的集货与出库拓扑。当前组合为 Iris、M20+未缩放 Z1 和基础 Carter。
同目录的 `modern_house_task.audit.json` 保存发布 QA，不对 participant 公开隐藏真值。
短窗口有限根状态只说明该组合在该窗口未触发 NaN/Inf，不建立真实家具搬运、额定载荷、
抓取力、碰撞安全裕度或载荷动力学结论。

原 2.5m 高的三段飞行被体素净空检查拒绝；当前采用 0.85m 低位观察和独立家具复核位。
Iris 的几何和接口只作为运动基础设施；NVD demo 默认采用
`--aerial-motion-mode kinematic`，以 0.6 m/s 有界推进，同时保留连续 swept-AABB、场景
voxel、任务物体 AABB、有限局部绕行和逐步跟踪。`--aerial-motion-mode controller` 用于
独立诊断 provider 位置控制器。运动学模式不证明飞行动力学、执行器限制、抗扰动或动态
障碍安全，也不计入异构协作能力。
静态几何通过不代替实际飞行、相机视野或并发交通验收。两台地面机器人撤离后，
评测器还要求身份/交付证据及不少于 0.8m 的连续生活动线。Isaac 动作开放分配、改派、
导航和 `consume_message`；真实观测先经过确定性通信队列，已送达且未过期的消息消费后
才放行依赖节点。participant `wait` 仍只在独立协议会话中测试。evaluator 可通过程序化
`main(...)` 注入初始通信契约、隐藏条件 schedule 和生命周期 callback；当前维护场景
尚未配置预加载 reserve，也没有物理生命周期参考 episode。详见[出题设计](../benchmark/BENCHMARK_DESIGN.zh-CN.md)。

当前源码参考报告为
`tmp/benchmark_release/modern_house_business_iris_kinematic_full_v6_20260917.json`。
它使用 `scene_public_greedy_v2` 和米制自动预算，在 9505 个控制步内完成 11/11 节点，
通过 3 个 RGB 窗口、3 次空中路线、地面逐步跟踪和物理健康检查；家具协作记录 116 个
样本，最大成员间距 3.5407m，小于 3.7262m 上限。两条运动学 delivery 和最终放置谓词
通过。入口扫描后的 camera-only Iris 驻留位为 `(15.5, 1.5, 0.85)`，已离开家具作业
通道；小物身份和住户连续动线因实时 adapter 缺失保持 false，因此该报告不构成业务
成功或 release-ready 证据。

公共运行器现已转发真实帧和可信节点完成事件，并提供
`reference_domain_providers.build_modern_house_reference_collector()`：注入的 detector 与
path backend 只能在指定 before/after scan 帧采样，after 必须晚于 `hall_release`，并绑定
运行占据图的实际 SHA256。历史 v6 尚未接入真实小物识别或场景 path backend，因此
上述 v6 结果仍保持 false；参考 provider 本身只定义可审计接线，不构成业务成功。
现已新增实际 session 的 `HouseNativeSceneDetector` 理想场景传感器及
`IsaacResidentPathBackend`。直接调用、观察距离/ray 可见性、当前 physics-step
和 source/occupancy provenance、before 阻挡与未知后端的区别，见
[native 接线说明](NATIVE_BACKENDS.md)。已通过独立 Isaac session 的双快照诊断，
不自动升级旧版 fixed-DAG 的业务结果。理想传感器只排除观察者自身刚体，其他机器人
仍参与遮挡判断；住户通路不排除任何机器人。运行时使用真实相机像素，诊断保存原始
帧及摘要，物品身份来自有距离与射线可见性限制的场景传感器，不是 RGB 识别算法。

在 `env_isaaclab` 中串行运行，不需要启动 ROS：

```bash
python -m demo.modern_house_benchmark.native_probe --output-root tmp/house_native_backend
python -m demo.modern_house_benchmark.native_probe --output-root tmp/house_native_backend --execute
```

输出目录不可复用。前后两次观测之间仅推进物理仿真；此入口不执行恢复家具或撤离
策略，因此 blocked 通路是合法测量，`scoreable` 和完整业务完成仍保持 false。

场景自有入口是
`demo.modern_house_benchmark.main.build_domain_evidence_provider(public_task, ...)`。
调用方传入实现 `detect(context)` 的物品身份 detector 和实现
`measure_path(context)` 的通路 backend，再把返回对象作为
`main(..., domain_evidence_provider=...)` 的参数。默认 detector 绑定公开的
`furniture_clearance_scan` 节点；通路 backend 只接收公开任务、占据地图句柄以及
`room_entry_scan` / `room_condition_scan` 的指定帧请求。任一 backend 缺失时，对应组件
保持 `pending`，入口不会补造 claim。即使两个 backend 都存在，入口也只产生可回放的
采集 claim；私有物品身份、定位误差、通路可行性和业务成功仍由 evaluator 检查。
