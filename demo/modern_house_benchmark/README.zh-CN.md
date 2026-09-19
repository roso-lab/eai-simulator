# Modern House NVD benchmark 应用

[English](README.md) · [交付状态总览](../benchmark/DELIVERY.zh-CN.md)

> **当前状态（2026-09-19 · 阶段18）**
>
> - **可跑**：原生 detector/path backend 读回（阶段18 v4，双快照之间加入作者侧家具干预：paired-support 搬运扶手椅 + 全部地面机器人撤离，after 相先经公开 `process_event`/`record_closeout` 路径注入 closeout）；离线实测区域区间（占据图射线 + 授权 bench 墙体 cuboid 双绑定，含门外/门槛/门内 corroborated 门洞与出生位一致性校验）；运动学参考 episode v6（11/11 节点）；出生高度修正后的四机器人静置诊断（阶段12 v5，脚轮贴地）；住户通路/撤离诊断入口；`--check-assets` 资产闭包审计。
> - **被阻断**：正式 before/after **业务**见证——两快照之间的干预是作者侧诊断动作，不是参与者驱动的任务执行，不能证明恢复任务成功。
> - **发布口径**：`release_status=pending`、`scoreable=false`（fail-closed 设计）。

## 目录模块

| 文件 | 职责 |
| --- | --- |
| `scenario.py` / `config.py` | 任务蓝图、资产白名单、实验配置、场景 USD 与占据地图 |
| `scenarios/home_recovery_01.json` | 默认业务挑战输入（11 节点恢复链） |
| `scenarios/navigation_smoke.json` | 显式导航冒烟样例（不能作为正式业务包输入） |
| `challenge.py` | 四层题包编译与发布 QA |
| `manifest.py` | NVD 产物 manifest 构建与校验 |
| `live_path.py` | 实时住户通路诊断（goal 轨道） |
| `native_probe.py` / `intervention_plan.py` | 双快照原生 backend 读回诊断；两快照之间的作者侧家具干预（占据图推导的停靠/撤离单元格与时序记录） |
| `native_adapters.py` | `HouseNativeSceneDetector` 理想场景传感器与 `IsaacResidentPathBackend` |
| `NATIVE_BACKENDS.md` | 原生后端接线细节（观察距离/ray 可见性/provenance） |
| `domain_adapter.py` | 物品身份 detector 与通路 backend 注入边界 |

生成的任务池和实验结果放在 `tmp/`。

## 快速开始

仓库根目录、`env_isaaclab` 环境：

```bash
# 资产闭包检查（不启动 Isaac）
python -m demo.modern_house_benchmark.main --check-assets
# 导航冒烟（GUI）
python -m demo.modern_house_benchmark.main --scenario demo/modern_house_benchmark/scenarios/navigation_smoke.json --gui --steps 400
# 业务任务执行
python -m demo.modern_house_benchmark.main --scenario <任务.json> --headless --report tmp/modern_house_benchmark/execution.json
# 公开 participant 基线
python -m demo.modern_house_benchmark.main --scenario <任务.json> --headless \
  --participant-mode public_greedy --report tmp/modern_house_benchmark/participant.json
```

离线生成与发布四层契约：

```bash
# 生成完整业务任务及 QA sidecar（需先设置 EAI_NVD_ASSET_ROOT）
python -m demo.modern_house_benchmark.challenge --generate-scenario tmp/modern_house_task.json
# 发布四层包（release/ 公共 + evaluator/ 隐藏）
python -m demo.modern_house_benchmark.challenge \
  --scenario tmp/modern_house_task.json --output-dir tmp/modern_house_benchmark/challenge
```

原生 backend 双快照诊断（输出目录不可复用；两快照之间执行作者侧干预：两台载具
paired-support 固定关节搬运扶手椅到占据图推导的停靠单元格、脱离后全部地面机器人
撤离走廊，closeout 经公开事件路径注入后才采样 after 相。干预是诊断动作，
`scoreable` 保持 false，不证明恢复业务成功）：

```bash
python -m demo.modern_house_benchmark.native_probe --output-root tmp/house_native_backend
python -m demo.modern_house_benchmark.native_probe --output-root tmp/house_native_backend --execute
```

任务 JSON 必须声明 `scene_key: modern_house_nvd`。机器人和附件始终经由
`simulator.py → open_simulator_session → env_builder` 构建，执行正常资产预检；
`demo/benchmark/runtime.py` 复用会话，不另建 Isaac 应用。资源目录、迁移和准备流程见
[公共说明](../benchmark/README.md)。机械臂保持源资产质量和惯量；任务容量策略不等于
真实额定 payload，也不构成 E7 物理操作证据。

## 任务设计

当前住宅 DAG：入口侦察、家具双侧协作、家具移动后净空复核、小件暂存、地面成员撤离、
最终房间扫描和确认——共 11 节点，不复用 Warehouse 的集货与出库拓扑。当前组合为
Iris、M20+未缩放 Z1 和基础 Carter。

任务实例采用 `mh_armchair`、`mh_bookstack` 的实测保守尺寸和估计质量。生成入口执行
schema、二维摆放、连通性、共享执行器规划以及连续三维飞行净空门禁，避免"离线连通但
运行器无法为后续节点建路"的任务进入任务池。同目录的 `modern_house_task.audit.json`
保存发布 QA，不对 participant 公开隐藏真值。

公开 `public_task.json` 带有受限的再生成骨架：经过审查的节点角色、原生道具角色、
因子轴、团队生命周期和通信边界；不包含隐藏干预时刻、对象真值或参考分配。
公开 fleet 给出 ground/aerial、移动方式和已配置附件接口，额定 payload 保持未知。

被测模型可做的事：`transport_object` 与 `joint_transport` 已开放 participant 分配/改派——
participant 选定的 agent 或团队在 phase 就绪时按实时位姿重建取放路线；对象所有权只经
运行时 pickup/release 事件变化，携带中改派被拒绝；运输交付目标属于任务语义不由
participant 改写；成员退出时对象在载具位置强制释放。

评估器入口 `demo.modern_house_benchmark.challenge.evaluate_report` 在公共调度指标之外
检查房间入口、空中复核和物体复核语义；这些检查不构成真实家具搬运、碰撞安全或机械臂
物理操作证据。

## 运行与协议边界

原 2.5m 高的三段飞行被体素净空检查拒绝；当前采用 0.85m 低位观察和独立家具复核位。
Iris 的几何和接口只作为运动基础设施；NVD demo 默认
`--aerial-motion-mode kinematic`，以 0.6 m/s 有界推进，同时保留连续 swept-AABB、场景
voxel、任务物体 AABB、有限局部绕行和逐步跟踪。`--aerial-motion-mode controller` 用于
独立诊断 provider 位置控制器。运动学模式不证明飞行动力学、执行器限制、抗扰动或动态
障碍安全，也不计入异构协作能力。

两台地面机器人撤离后，评测器还要求身份/交付证据及不少于 0.8m 的连续生活动线（注意：
goal 轨道公开净宽为 0.9m/净高 2.0m，与历史 fixed-DAG 的 0.8m 要求分属不同任务版本，
不可混用）。Isaac 动作开放分配、改派、导航和 `consume_message`；真实观测先经过确定性
通信队列，已送达且未过期的消息消费后才放行依赖节点。participant `wait` 只在独立协议
会话中测试。evaluator 可通过程序化 `main(...)` 注入初始通信契约、隐藏条件 schedule 和
生命周期 callback；当前维护场景尚未配置预加载 reserve，也没有物理生命周期参考 episode。
详见[出题设计](../benchmark/BENCHMARK_DESIGN.zh-CN.md)。

## 物理诊断（goal 轨道）

**实时住户通路**（`live_path.py` + `goal_physical_probe`）：在同一 Isaac session、同一
物理步对明确的候选折线执行正反向连续 box sweep，公开净宽 0.9m、净高 2.0m；家具、
书堆和所有停驻机器人均保留为障碍。上下边界使用显式 1mm 接触容差，支撑面按不超过
0.1m 的间距取中心及四角射线。射线支撑采样不等价于连续楼面证明。对轴对齐静态 Cube
楼面，另以实际启用碰撞体的连续平面并集检查完整通路覆盖（含采样点之间的窄缝）；只将
完全位于脚底平面以下的已核验静态 Cube 视为支撑接触。其他机器人、物体和未知几何不做
排除；静态几何覆盖与实时射线证据分别记录。

```bash
# env_isaaclab；一次只执行一个场景。输出目录必须是新目录。
python -m demo.benchmark.goal_physical_probe --scene modern_house_nvd \
  --steps 60 --gui --output-root tmp/house_resident_path
python -m demo.benchmark.goal_physical_probe --execute --output-root tmp/house_resident_path
# 有界撤离诊断（先静置 60 步，再用原生控制器移开 M20_1 和 Iris 后复测）
python -m demo.benchmark.goal_physical_probe --scene modern_house_nvd \
  --house-withdrawal --steps 1200 --output-root tmp/house_withdrawal
```

结果位于 `physical_report.json` 的 `resident_path` 字段。初始机器人仍在动线内时，通路
查询应保持 blocked；这描述当前状态，并不判定题目作者审核失败——机器人停留、会车和
撤离时机由参与者选择，只有不合理初始布局、强制点位冲突或不存在可行解才属于出题缺陷。
不得排除机器人来生成最终净空证据。

无 GUI 时必须显式启用 `env_cfg.sim.enable_scene_query_support`，否则返回未知，不能把
空查询视为通路畅通（该入口已启用此配置）。Direct GPU 模式会抑制 CPU 位姿读回，入口
检查 `is_readback_suppressed()` 并将此类动态净空标为 unknown；仅对明确选用
`--device cpu` 的作者诊断验证同步查询，不静默修改生产运行设备。物体生成须计入本场景
0.25m 楼面高度；旧 CPU 撤离 v4 中书堆曾埋入楼板，该结果仅验证检查器响应。

## 实时领域证据

公共运行器转发真实帧和可信节点完成事件，并提供
`reference_domain_providers.build_modern_house_reference_collector()`：注入的 detector 与
path backend 只能在指定 before/after scan 帧采样，after 必须晚于 `hall_release`，并绑定
运行占据图的实际 SHA256。

场景自有入口是
`demo.modern_house_benchmark.main.build_domain_evidence_provider(public_task, ...)`：
调用方传入实现 `detect(context)` 的物品身份 detector 和实现 `measure_path(context)` 的
通路 backend。默认 detector 绑定公开的 `furniture_clearance_scan` 节点；通路 backend
只接收公开任务、占据地图句柄以及 `room_entry_scan` / `room_condition_scan` 的指定帧
请求。任一 backend 缺失时对应组件保持 `pending`，入口不会补造 claim。即使两个 backend
都存在，入口也只产生可回放的采集 claim；私有物品身份、定位误差、通路可行性和业务
成功仍由 evaluator 检查。

现已新增实际 session 的 `HouseNativeSceneDetector` 理想场景传感器及
`IsaacResidentPathBackend`（`native_adapters.py`）：直接调用、观察距离/ray 可见性、
当前 physics-step 和 source/occupancy provenance、before 阻挡与未知后端的区别，见
[native 接线说明](NATIVE_BACKENDS.md)。理想传感器只排除观察者自身刚体，其他机器人仍
参与遮挡判断；住户通路不排除任何机器人。运行时使用真实相机像素，诊断保存原始帧及
摘要；物品身份来自有距离与射线可见性限制的场景传感器，不是 RGB 识别算法。已通过
独立 Isaac session 的双快照诊断，不自动升级旧版 fixed-DAG 的业务结果。

## 历史参考报告

当前源码参考报告：
`tmp/benchmark_release/modern_house_business_iris_kinematic_full_v6_20260917.json`——
使用 `scene_public_greedy_v2` 和米制自动预算，9505 个控制步完成 11/11 节点，通过 3 个
RGB 窗口、3 次空中路线、地面逐步跟踪和物理健康检查；家具协作记录 116 个样本，最大
成员间距 3.5407m（上限 3.7262m）。两条运动学 delivery 和最终放置谓词通过；小物身份和
住户连续动线因实时 adapter 缺失保持 false，因此该报告不构成业务成功或 release-ready
证据。入口扫描后的 camera-only Iris 驻留位为 `(15.5, 1.5, 0.85)`，已离开家具作业通道。

阶段18 原生后端 v3：`tmp/house_native_backend_stage18_20260919_v3/`——482/602 物理步
取得真实 320×240 RGBA 帧、唯一观测 ID、两件物体身份和有效 blocked 通路测量；原始
NPY 像素摘要与报告一致。前后快照无业务干预，不能证明家具恢复或住户通路清空。

## 相关文档

- [交付状态总览](../benchmark/DELIVERY.zh-CN.md)——三档交付状态与剩余 blocker
- [设计与实现边界](../benchmark/BENCHMARK_DESIGN.zh-CN.md)——三场景出题设计
- [native 接线说明](NATIVE_BACKENDS.md)——原生 detector/path backend 细节
- [物理诊断边界](../benchmark/GOAL_PHYSICAL.zh-CN.md)——probe 证据口径
- [阶段18审核](../benchmark/STAGE18_REVIEW.zh-CN.md)——最新物理证据与 SHA-256
- [阶段清单](../benchmark/TODO.zh-CN.md)——阶段 1–18 权威勾选记录
