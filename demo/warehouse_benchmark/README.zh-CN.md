# Warehouse NVD benchmark 应用

[English](README.md) · [交付状态总览](../benchmark/DELIVERY.zh-CN.md)

**主轨交接更新（2026-09-20）**：`WAREHOUSE-GOAL-01` 与其他三场景使用同一生成、作者审核和
`GoalAlgorithmHost` / `GoalSession` 接口；默认 suite 中每场景九条件题包、七对因子对照。
导航、移动和抓取采用统一固定默认技能策略；下面的原生实测记录属于独立诊断证据。
接入和复跑步骤见 [交接指南](../benchmark/ALGORITHM_HANDOFF.zh-CN.md)，正式执行仍单独验收。

> **当前状态（2026-09-19 · 阶段18）**
>
> - **可跑**：单机固定点承载/输送/释放与 dock 回执（阶段18 v5，CPU PhysX 复验，升降锚点回程误差 4.76μm）；运动学参考 episode v8（10/10 节点）；原生对象/质量/位姿读回（阶段12+）；`--check-assets` 资产闭包审计。
> - **被阻断**：双机联合承载 witness——Carter 碰撞资产缺陷已于 2026-09-19 修复（三个空 Xform 的无效网格碰撞 API 移除，v10 复跑 cooking 通过，详见[阶段18审核](../benchmark/STAGE18_REVIEW.zh-CN.md)；资产修复待 provider 发布），当前卡点转为**联合队形几何**：M20/Carter 与木箱的实测 collider AABB 在 `settled_before_attachment` 相互接触，队形方案需实测验收；实时货架身份 detector、dock 服务、对象位姿 backend 未随源码发布，业务得分保持 `unknown`。
> - **发布口径**：`release_status=pending`、`scoreable=false`（fail-closed 设计）。

## 目录模块

| 文件 | 职责 |
| --- | --- |
| `scenario.py` / `config.py` | 任务蓝图、资产白名单、实验配置、场景 USD 与占据地图 |
| `scenarios/business_logistics_01.json` | `W-DAG-01`：默认固定 DAG 诊断输入（10 节点订单链，节点内分配） |
| `scenarios/navigation_smoke.json` | 显式导航冒烟样例（不能作为正式业务包输入） |
| `challenge.py` | 四层题包编译与发布 QA |
| `manifest.py` | NVD 产物 manifest 构建与校验 |
| `native_witness_probe.py` | 原生固定点/dock 微诊断（两段式冻结+执行） |
| `native_carry.py` | 单机连接器目标驱动的真实承载诊断 |
| `paired_native_probe.py` | 双机承载形状闭包/联合承载 probe（当前被 Carter 资产阻断） |
| `paired_shapes.py` | 双机 probe 的原生形状查询支撑 |
| `dock_receiver.py` | dock 接收回执（完整旋转外形、身份、质量、释放后新物理步） |
| `live_witness.py` | Warehouse 自有实时领域证据适配器 |
| `domain_adapter.py` / `reference_domain_providers` 接入 | 货架身份 detector、dock 服务、对象位势 backend 注入边界 |
| `test_*.py` | 本目录组件专项测试（dock receiver、domain adapter、live witness、native carry、paired probe/shapes） |

生成的任务池和实验结果放在 `tmp/`。

## 快速开始

仓库根目录、`env_isaaclab` 环境：

```bash
# 资产闭包检查（不启动 Isaac）
python -m demo.warehouse_benchmark.main --check-assets
# 导航冒烟（GUI）
python -m demo.warehouse_benchmark.main --scenario demo/warehouse_benchmark/scenarios/navigation_smoke.json --gui --steps 400
# 业务任务执行
python -m demo.warehouse_benchmark.main --scenario <任务.json> --headless --report tmp/warehouse_benchmark/execution.json
# 公开 participant 基线
python -m demo.warehouse_benchmark.main --scenario <任务.json> --headless \
  --participant-mode public_greedy --report tmp/warehouse_benchmark/participant.json
```

离线生成与发布四层契约：

```bash
# 生成完整业务任务及 QA sidecar（需先设置 EAI_NVD_ASSET_ROOT）
python -m demo.warehouse_benchmark.challenge --generate-scenario tmp/warehouse_task.json
# 发布四层包（release/ 公共 + evaluator/ 隐藏）
python -m demo.warehouse_benchmark.challenge \
  --scenario tmp/warehouse_task.json --output-dir tmp/warehouse_benchmark/challenge
```

单机原生见证（两段式：先冻结输入，再执行；输出目录必须新建）：

```bash
python -m demo.warehouse_benchmark.native_witness_probe \
  --output-root tmp/warehouse_native_witness --device cpu
python -m demo.warehouse_benchmark.native_witness_probe \
  --output-root tmp/warehouse_native_witness --device cpu --execute
```

任务 JSON 必须声明 `scene_key: warehouse_nvd`。机器人和附件始终经由
`simulator.py → open_simulator_session → env_builder` 构建，执行正常资产预检；
`demo/benchmark/runtime.py` 复用会话，不另建 Isaac 应用。资源目录、迁移和准备流程见
[公共说明](../benchmark/README.md)。机械臂保持源资产质量和惯量；任务容量策略不等于
真实额定 payload，也不构成 E7 物理操作证据。

## 任务设计

当前 Warehouse DAG：货架扫描、双侧通道进入、大件箱体联合转移、优先纸箱集货/出库、
dock 复核与通道释放——共 10 节点，不与住宅场景共用节点拓扑和业务命名。

任务实例使用 `wooden_crate_a`、`cardbox_c` 的实测保守尺寸和估计质量；缺失资产不会
回退为方盒。生成入口在写文件前执行 schema、二维摆放、连通性、共享执行器规划及连续
三维飞行净空门禁；同目录写出的 `warehouse_task.audit.json` 记录门禁结果，只用于发布 QA。

公开 `public_task.json` 带有受限的再生成骨架：经过审查的节点角色、原生道具角色、
因子轴、团队生命周期和通信边界；不包含隐藏干预时刻、对象真值或参考分配。
公开 fleet 给出 ground/aerial、移动方式和已配置附件接口，额定 payload 保持未知；
运行时 phase-ready 候选由公开约束筛选，不读取隐藏 `preferred_agent`。

被测模型可做的事：`transport_object` 与 `joint_transport` 已开放 participant 分配/改派——
participant 选定的 agent 或团队在 phase 就绪时按实时位姿重建取放路线；对象所有权只经
运行时 pickup/release 事件变化，携带中改派被拒绝；运输交付目标属于任务语义，
不由 participant 改写；成员退出时对象在载具位置强制释放。

评估器入口 `demo.warehouse_benchmark.challenge.evaluate_report` 在公共调度指标之外检查
物流通道、联合搬运和物体运输语义；这些检查仍属任务逻辑证据，不代表额定 payload 或
真实抓取能力。

## 运行与协议边界

正式模板固定使用 Iris 的几何和接口承担空中扫描。NVD demo 默认
`--aerial-motion-mode kinematic`：0.6 m/s 有界推进，对命令段和实测步间位移执行连续
swept-AABB、场景 voxel、任务物体 AABB、有限局部绕行和逐控制步跟踪。
`--aerial-motion-mode controller` 保留为 provider 位置控制器的独立诊断。运动学模式只
保证基础运动接口，不证明飞行动力学、执行器限制、抗扰动或动态障碍安全；两种模式的
飞控表现都不计入异构协作能力。

当前生成组合只在 M20 上配置一套未缩放 Z1，Carter 保持基础导航配置，并扩大两台地面
平台的初始间距。该选择来自多配置短运行的稳定性筛查，不是缩小机械臂或降低质量：
近距离多挂载组合曾产生非有限 PhysX 状态，现由运行器逐控制步检测并以
`physics_state_invalid` 失败关闭。单臂生成组合的短运行只证明该窗口内根状态有限。

Isaac 公共包开放已接入的分配、改派、导航和 `consume_message`。真实采集观测先经过
确定性通信队列，只有已送达且未过期的消息可被消费，依赖节点在声明的后继完成消费前
保持阻塞；participant `wait` 只在独立无运动协议会话中测试。evaluator 可通过程序化
`main(...)` 注入初始通信契约、隐藏条件 schedule 和生命周期 callback。订单身份、
观察/消费、实测交付和 dock 回执同时通过才算业务成功；节点全完成只能获得调度结果。
LLM 生成入口和因子约束见[出题设计](../benchmark/BENCHMARK_DESIGN.zh-CN.md)。

## 物理诊断（goal 轨道）

`native_witness_probe.py` 须在 `env_isaaclab` 下运行，场景间串行，使用统一物理验证锁。
执行阶段复用 `goal_attachment_probe` 的 deployment、`LiveBodies` 和原生固定点 receipt，
在创建场景前显式打开 PhysX scene query，执行纸箱真实固定点承载、逐步 PhysX pose 读回、
释放，再由 Warehouse 自有 dock receiver 按 runtime object path 和释放后的当前 pose 生成
诊断回执。`dock_receiver.py` 检查完整旋转外形在候选 dock AABB 内、物体身份及质量、
约束实际移除、释放后的新物理步与支撑高度；不能只凭中心接近就发接收回执——该回执仅
证明候选区域的释放后读回，不证明完整 dock 服务或联合承载。

`native_carry.py` 只小步调整机器人侧连接器目标，物体响应由 PhysX 产生；诊断检查真实
离地净空及运输中的最低净空，再下降、解除约束并等待新物理步。目标更新本身不是提升
成功的证据；双机负载分担与成员退出恢复仍需独立实测。任务只有木箱和纸箱，不要求
service car；诊断路径不作为参与者必须采用的策略。

双机 `paired_native_probe.py`：使用真实 `QUERY_RIGID_BODY_WITH_COLLIDERS` 原生 shape
查询。当前在完整 collider inventory 闭合前被 Carter
`/World/envs/env_0/carter_1/Carter/caster_swivel_right` 的 deferred convex cooking 失败
（`RESULT_ERROR_INVALID_PARSING`）阻断；该 prim 带 `PhysicsMeshCollisionAPI`、
`approximation=convexHull` 但无可 cooking 表示。已归档 73 条 USD collision inventory。
**解除方式：修复源碰撞资产后重新生成 probe 并串行重跑**；不得用公开尺寸、视觉包络
或手工 AABB 替代 cooked PhysX shape。CUDA Direct GPU 下 CPU scene query 可能读取陈旧
actor pose，因此携物净空证据保持 `unknown`。

## 实时领域证据

公共运行器在真实帧发布后调用领域 hook。Warehouse 自有 adapter 在 `live_witness.py`
校验 detector、dock 和对象 pose claim，Warehouse 入口不使用 reference provider。

运行入口是 `demo.warehouse_benchmark.main.build_domain_evidence_provider(public_task, ...)`：
调用方传入分别实现 `detect`、`issue_handoff`、`sample_pose` 的货架身份、dock 服务和
对象位势 backend，再把返回对象作为 `main(..., domain_evidence_provider=...)` 的参数。
该入口默认把 detector 绑定到货架身份扫描节点，把 dock 服务和对象位势绑定到出库观察帧。
任一 backend 缺失时对应组件保持 `pending`，dock 两个 backend 不完整时不调用半成品交接链，
业务证据保持 `unknown`/pending；不会根据运动学运输完成或仿真对象名伪造成功。
仓库当前没有随源码发布的真实 detector 权重或 dock controller backend。

## 历史参考报告

当前源码参考报告：
`tmp/benchmark_release/warehouse_business_iris_kinematic_full_v8_20260917.json`——
使用 `scene_public_greedy_v2` 和米制自动预算，5594 个控制步完成 10/10 节点，通过 2 个
RGB 窗口、2 次空中路线、地面逐步跟踪和物理健康检查；联合搬运记录 126 个样本，最大
成员间距 3.4279m（上限 3.6727m）。三条运动学 delivery 和最终放置谓词通过；订单身份与
dock 回执因实时 adapter 缺失保持 false，因此 `business_task_success=false`。

阶段18 单机承载 v5：`tmp/warehouse_stage18_native_witness_20260919_v5/`——纸箱位移
1.9727m、载具 2.0059m，运输中最低离地 0.06867m；升降回程精确恢复原始锚点，末端
anchor 误差 4.76μm，释放后 dock ready。双机 v9 证据与 Carter blocker 详见
[阶段18审核记录](../benchmark/STAGE18_REVIEW.zh-CN.md)。

## 相关文档

- [交付状态总览](../benchmark/DELIVERY.zh-CN.md)——三档交付状态与剩余 blocker
- [设计与实现边界](../benchmark/BENCHMARK_DESIGN.zh-CN.md)——三场景出题设计
- [物理诊断边界](../benchmark/GOAL_PHYSICAL.zh-CN.md)——probe 证据口径
- [阶段18审核](../benchmark/STAGE18_REVIEW.zh-CN.md)——最新物理证据与 SHA-256
- [阶段清单](../benchmark/TODO.zh-CN.md)——阶段 1–18 权威勾选记录
