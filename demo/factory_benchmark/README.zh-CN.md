# Factory benchmark 应用

[English](README.md) · [交付状态总览](../benchmark/DELIVERY.zh-CN.md) · [Go2 运动基座](../benchmark/GO2_LOCOMOTION_BASE.zh-CN.md)

**主轨交接更新（2026-09-20）**：`FACTORY-GOAL-01` 与其他三场景使用同一生成、作者审核和
`GoalAlgorithmHost` / `GoalSession` 接口；默认 suite 中每场景九条件题包、七对因子对照。
导航、移动和抓取采用统一固定默认技能策略；下面的原生实测记录属于独立诊断证据。
接入和复跑步骤见 [交接指南](../benchmark/ALGORITHM_HANDOFF.zh-CN.md)，正式执行仍单独验收。

> **当前状态（2026-09-21 · benchmark 基座更新）**
>
> - **可跑**：F-INS-01 证据派遣题包（四层发布 + 派车矩阵 + ArUco 标签工程基线）；F-TRAFFIC 共享通道 / F-COV 覆盖对照任务及分配比较工具；楼梯实测绑定（`flight_02`，根层+67 依赖层 hash）与 F-VERT-01 候选编译入口；14 型配置 smoke 与能力画像导出；`--check-assets` 场景闭包审计。
> - **运动基座**：F-VERT-01 默认 Go2 使用 `GO2_MIXED_STAIRS_CFG`（继续训练权重，checkpoint 3999），模型权重由 resolver 从 `controller/rl/go2_mixed_stairs/model/policy.onnx` 提供；原始 Stage-A 导出已从注册表退役。M20 保留已有楼梯能力基线。
> - **仍待验收**：Go2、B2 的替换模型都需要重新生成连续上行、平台停留和下行的独立 PhysX 证据，当前题包仍不晋升；B2 已注册 `B2_MIXED_STAIRS_CFG`，但不能把控制器接入或单次接触记录写成楼梯通过。楼梯意图 Isaac adapter、PhysX 支撑/连通性校准继续 pending。
> - **发布口径**：`release_status=pending`、`scoreable=false`（fail-closed 设计）。

## 目录模块

| 文件/目录 | 职责 |
| --- | --- |
| `scenario.py` / `config.py` / `tasks.py` | 任务蓝图、资产白名单、实验配置、场景 USD 与占据地图 |
| `scenarios/` | 维护任务：`inspection_01`（F-INS）、`inspection_dispatch_01`（派车）、`traffic_01`（F-TRAFFIC）、`coverage_01[_dropout]`（F-COV）、`vertical_stair_01`（F-VERT）、`navigation_smoke`（冒烟） |
| `evaluator/inspection_01.json` | evaluator-only 真值 fixture（不进入运行时观测） |
| `challenge.py` / `vertical_challenge.py` | F-INS 派遣契约 / F-VERT 候选题包编译 |
| `baseline_solver.py` | 公开参考策略（`immediate` / `wait_for_message`），不是标准答案 |
| `inventory_scene.py` / `label_geometry.py` / `inventory_dispatch.py` | ArUco 标签 USD 覆盖层、标签几何、派车站点与消息规则 |
| `inspect_camera_frames.py` / `inspect_inventory.py` / `inspect_dispatch.py` | evaluator 侧投影核验、像素重解码、派车独立重放 |
| `run_inventory_matrix.py` | 标签/派车条件矩阵（visible17/covered17/absent17/visible23、shared/withheld/delayed） |
| `compare_allocation.py` / `run_allocation_comparison.py` / `route_costs.py` | 固定/贪心/枚举分配对照与路线成本模型 |
| `physx_vertical_evidence.py` | 私有 PhysX 垂直证据 adapter（raycast/contact/support） |
| `scene_contract.py` / `prim_bindings.py` / `evaluation.py` | 场景契约、箱体 prim 绑定、评测 |
| `prepare.py` | 场景准备与 USD 元数据 |

生成的任务池和实验结果放在 `tmp/`。

## 快速开始

仓库根目录、`env_isaaclab` 环境：

```bash
# 场景闭包审计（含核心 USD prim 绑定和来源 hash，不启动 Isaac）
python -m demo.factory_benchmark.main --check-assets
# 导航冒烟（GUI）
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/navigation_smoke.json --gui --steps 400
# 业务任务执行
python -m demo.factory_benchmark.main --scenario <任务.json> --headless --report tmp/factory_benchmark/execution.json
```

生成 Factory 候选任务（可指定任务族，避免所有候选都被强制生成空中扫描与联合搬运）：

```bash
tools/scenario_generation/generate_benchmark.py --scene-key factory --task-id F-TRAFFIC-01 ...
```

发布 F-INS 四层题包：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.factory_benchmark.challenge --output-dir tmp/factory_benchmark/f_ins_challenge
```

任务 JSON 必须声明 `scene_key: factory`。机器人和附件始终经由
`simulator.py → open_simulator_session → env_builder` 构建，执行正常资产预检；
`demo/benchmark/runtime.py` 复用会话，不另建 Isaac 应用。样例使用 Carter + 未缩放 Z1，
执行一段短距离导航；检查场景组装、机械臂质量/惯量和导航，不验证抓取。默认 CPU PhysX。
搬运任务目前只支持显式 `--transport-mode kinematic`，报告仍区分运动学搬运与真实物理操作。

## 任务设计

三场景出题边界见[设计与实现边界](../benchmark/BENCHMARK_DESIGN.zh-CN.md)：

| 场景 | 场景相关骨架与意义 | 当前限制 |
| --- | --- | --- |
| Factory | 俯视取证 → 地面接近 → 近景取证 → 按信息派往 assembly/dock 工位；箱体库存标签把观察结果与作业去向关联 | 模型只控制最后派遣；F-TRAFFIC 评测共享通道调度，F-COV 评测工位值守。没有危险隔离、设备复位或生产恢复验收 |
| Warehouse NVD | 货架扫描、双侧接近、木箱联合集货、纸箱暂存与出库、派送后扫描、dock 交接等待和通道收尾，共 10 节点 | 真实 SKU/dock 适配器及物理搬运仍待接入；固定 DAG 不评测自主订单优先级选择 |
| Modern House NVD | 入口检查、扶手椅联合整理、指定书籍暂存、复核定位、两台机器人撤离、最终动线复核和等待，共 11 节点 | 真实身份/动线采样及物理搬运仍待接入，不评测未知物品搜索或一般住户安全 |

三个业务轨道给被测模型的四类输入：

1. `release/public_task.json`：场景、公开机器人及接口、目标区、子任务 DAG、依赖、资源和业务规则；`business_rules.task_skeleton` 是可供出题 LLM 再生成的职责骨架。
2. `release/participant_api.json`：合法动作/事件格式、受控节点和当前运行时支持范围。
3. `public_task.json` 内的 `factor_manifest`：因子、等级、证据轨道及单因子比较规则。
4. 运行中已投递的观测、消息、状态和生命周期事件，不能提前获得未来事件。

Factory 模型只对 `route_confirmation` 提交 `navigate_goal/wait/consume_message`，决定是否
等待或消费可见消息、派往哪个公开站点；前三阶段由 benchmark 管理。
Warehouse/House Isaac 模型可在既定 DAG 上 `allocate/reassign/navigate_goal`；运输目的地
固定，携带中不能改派。generic 模型 `wait/consume_message` 只在独立的无运动协议 harness
中应用；可执行的 wait 技能节点不代表同名动作已接入。

benchmark 固定资产、初始团队、任务分解、物品业务绑定、目标区、阈值和预算，负责调度、
基础运动、证据采集及评分。模型输出为合法 `participant_action` envelope，说明文字不进入
机器得分。隐藏侧保留真实标签/对象身份、未来故障与消息日程、候补池、参考指派及
detector/dock/access-path 独立真值；模型自报成功不作证据。当前固定 DAG 再生成不计为
解题模型自主分解能力；道具声明也不证明实例化或物理交互。

F-INS 的维护场景显式分为 `aerial_view → ground_approach → ground_view(wait) →
route_confirmation`，其中 `ground_view` 使用真实相机窗口并通过 `observation_ref` 观察
同一 inspection subject。`release/public_task.json` 不包含 `preferred_agent`、真实标签、
隐藏故障时刻或参考决策；当前 F-INS 运行范围只开放 `navigate_goal`、`wait`、
`consume_message`，`allocate`/`reassign` 明确标为待实现；`baseline_manifest.json` 只描述
可选基线，不含分配答案。

## 运行与协议边界

**生命周期与通信**：`member_join` 和递增版本的 `capability_change` 目前只支持协议状态、
资格更新与回放；Isaac 动态创建/激活实体、在途传感器或控制器重配置尚未实现。`member_exit`
仅在受支持的纯地面 scene-neutral/F-COV 任务中使用预加载替补及实测位姿，当前混合无人机
模板和 F-INS dispatch 不接收该故障。延迟、丢包、过期和带宽变化是确定性消息协议，不是
实测无线模型。换班、充电或隔断可作为因子的业务理由，但事件声明不能当作真实干预已生效。

**无人机基线**：二维航点加世界高度目标，连续 **swept-AABB + 保守体素**门禁，阻塞时
尝试有界局部绕行，无可行候选或空间未知则拒绝。不是完整三维规划、动态避碰或飞行安全
证明，也不计为异构团队协作能力。`build_factory_voxel_product.py` 将原生 USD 启用碰撞
Mesh 的世界 AABB 保守栅格化，并记录根 USD、地图、产品 hash 与参数；声明和本地产品
不证明 provider 发布或飞行通过。`aerial_scan` 可设置 `scan_height_m`（米制世界 Z）与
`scan_yaw_rad`（绝对世界偏航）；悬停检查要求位置误差 ≤0.2 m 与偏航误差 ≤0.15 rad 连续
满足 0.5 s。

**地面导航**：F-INS/F-TRAFFIC/F-COV 从场景模板读取楼梯、坡面包络，在规划网格中排除
尚未通过当前协议验证的这些区域；几何准入拒绝位于这些区域的地面出生点/目标。额外
0.02 m 仅包住模板边界的小数舍入差，不是经过测量的碰撞安全裕度。导航恢复监视实际
waypoint 距离或朝向误差的单调改善；来回振荡不能反复重置计时。

**观测契约**：`demo/benchmark/observations.py` 记录观测来源和有效期；帧记录中的
`sensor_pose_only` 是采集时机器人底座位置代理，不能解释为真实相机外参或目标位置。
`ObservationStore` 提供 detector 输出契约和 evaluator-only 的识别/定位评测函数；只有
检测结果绑定真实帧、并由评测器提供独立目标真值时才会通过。领域数据通过
`domain_evidence_provider` 注入公共运行器；没有真实领域适配器时
`domain_evidence.adapter_status` 保持 `pending_live_scene_adapter`。

## 楼梯垂直轨道（F-VERT-01）

楼梯通过显式 vertical metadata、`main_stair` 和 `F-VERT-01` 候选探针保留开放入口，用于
上层工位可达性与形态差异研究；普通地面任务仍避开未验证表面。入口开放及
`admitted_for_probe=true` 与爬楼验收不同，当前准入声明为 `execution_validated=false`。

- `vertical_task.py`（tools/scenario_generation）是 benchmark 管理的候选执行器，无校准
  支撑/接触证据时以零指令采样并停止推进；必须以完整姿态、连续健康停留和实测到达验收，
  不能靠目标高度或 teleport。
- `scenarios/vertical_stair_01.json` 和独立 `vertical_challenge.py` 提供候选声明/编译入口；
  原 `challenge.py` 仍只编译派遣契约。
- `measure_factory_stair.py` 使用 OpenUSD 实测两段 authored tread flight 并绑定根层和
  67 个依赖层 hash；支撑面搜索按楼梯 AABB 裁剪（约 10.13 秒）。编译器可通过私有
  `--stair-measurement` 选择 `flight_02`，但候选坐标不提升为物理测量，
  `release_gate.executable=false`、`release_gate.scoreable=false`。
- 模型只可提交给定机器人的路线意图或有界等待；格式校验不会启动运动，
  `executed_action_types=[]`。此单机诊断未启用生命周期/通信实验，尚无业务轨道的
  `factor_manifest` 和实时事件输入。
- 探针基座现状：维护场景和 `go2_factory_stairs` 默认环境均使用 Go2
  `GO2_MIXED_STAIRS_CFG`（继续训练权重，checkpoint 3999）；原始 Stage-A 导出已退役。M20 是已有楼梯能力基线。
  Go2/B2 新模型的连续上行/平台停留/下行证据尚未入库；B2 已完成控制器注册，
  其接触采样记录仍不等同于楼梯通过。

## F-INS 工程基线（标签与派车）

**标签覆盖层**：`--inventory-label-id 17 --archive-camera-frames` 通过同一 simulator.py
会话加载 Factory USD 覆盖层，把黑框边长 0.09 m、纸张外边长 0.12 m 的 ArUco 标签放在
已绑定箱体的真实正面三角面上（1 mm 边距与表面偏移）；`covered|absent` 生成对照条件。
生成层位于 `usd/scene/factory/benchmark/generated/`（运行数据）。解码器只接收原始 RGB、
相机参数、公共字典和标签边长；期望 ID、真值不传给解码器。同一视角、同一解码 ID 的
全部有效位姿按坐标中位数融合（标签须窗口内静止），评测固定估计并保留每帧误差。

**核验命令**：

```bash
# 归档相机帧的任务运行
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/inspection_01.json \
  --headless --steps 9000 --render-period-s 0.2 --wall-timeout-s 480 \
  --archive-camera-frames --report tmp/factory_benchmark/inspection_capture.json
# evaluator 投影核验（目标中心与包围盒角点投影到实际相机图像）
python -m demo.factory_benchmark.inspect_camera_frames \
  --scenario demo/factory_benchmark/scenarios/inspection_01.json \
  --report tmp/factory_benchmark/inspection_capture.json \
  --output tmp/factory_benchmark/inspection_projection.json
# 像素重解码 + 独立定位误差
python -m demo.factory_benchmark.inspect_inventory --scenario <scenario.json> \
  --report <report.json> --attempt <attempt.json> --output <audit.json>
# 条件矩阵（visible17/covered17/absent17/visible23）
python -m demo.factory_benchmark.run_inventory_matrix --output-dir tmp/factory_benchmark/new_inventory_matrix
```

**派车变体**（`inspection_dispatch_01.json`）：公开业务规则 ID17 → assembly_site、
ID23 → dock_site；`baseline_solver.py` 的 `immediate`/`wait_for_message` 只是公开参考策略。
派车矩阵（`run_inventory_matrix --dispatch`）为两个标签 ID × shared/withheld/delayed 六次
新运行。F-INS Isaac 运行器可通过 `main(..., participant_adapter=<adapter>)` 注入 Python
adapter；adapter 只接收已送达消息、公开 phase/tick 状态和仿真时间，返回动作经
public task/API 校验后才执行。`dispatch.evaluation.json` 独立重放像素、融合、消息可用
时刻及策略，再从位置轨迹核对实际到达；`dispatch_success` 与来源/协议验收分开。

**视角与朝向**：`scan_yaw_rad` 要求 [-pi, pi] 绝对世界偏航，通过 `goal_yaw` 驱动
Iris/Pegasus 位置控制器（CF2X 直接转子模式明确拒绝）。地面取帧 wait 可声明
`wait_yaw_rad`，连续 0.5 s 保持误差不超过 0.05 rad 后才开始完整取帧窗口。C01 Camera
构造器使用 Isaac 默认 world 相机轴单位四元数（历史 bug：重复轴变换致画面横置，已修复）。

## F-TRAFFIC / F-COV 与分配对照

`traffic_01.json` 维护配对：SimpleCar 执行 dock 路线、Carter 执行 yard 路线；反向分配会使
Carter 在 dock 入口停滞（配置/场景配对结果，不能泛化为 Carter 通行能力结论）。

`coverage_01.json` 是单机/同构对照下的持续覆盖基线：三个 SimpleCar 并行到达 storage、
assembly、dock，各用 C01 相机在 45 秒窗口内每秒采集真实 RGB 帧。`coverage_01_dropout.json`
是成员退出变体：`simple_car_1` 退出后由 `simple_car_4` 导航到备用观察位接管观测；
`member_exit_recovery` 分别评价调度延迟、首帧恢复延迟和观测连续性。

分配对照工具：

```bash
python -m demo.factory_benchmark.compare_allocation --scenario demo/factory_benchmark/scenarios/coverage_01.json \
  --measurements <records.json> --output tmp/factory_benchmark/allocation.json
# 路线成本模型（膨胀占据图路线距离，米）
python -m demo.factory_benchmark.compare_allocation ... --cost-model map_route_length ...
# 顺序启动三次 Isaac 的完整配对（输出目录必须新建）
python -m demo.factory_benchmark.run_allocation_comparison --scenario demo/factory_benchmark/scenarios/coverage_01.json \
  --measurements <records.json> --output-dir tmp/factory_benchmark/new_comparison
```

分配对照共用候选、需求和候选评估次数预算，包含固定、贪心及有限枚举优化；预算用尽且
无解时报告 `unknown`，只有完整枚举后才可声明 `optimality_proven`。分配结果还包含
`reallocation_certificate`：`heterogeneity_necessary` 只有在单机和同构团队都失败且完整
搜索完成时才为真（当前 F-COV 结果：最少 3 个机器人、同构可行、异构非必要）。

配置微测试与能力画像：

```bash
python tools/scenario_generation/record_configuration_microtests.py \
  --scenario <冻结任务.json> --attempt <批运行attempt.json> --output <records.json>
python tools/scenario_generation/export_capability_profiles.py \
  --measurements <records.json> --format html --output tmp/factory_benchmark/capabilities.html
```

导入时重新核对启动前请求、执行源码、实际资产、质量审计和原始报告；旧 v1 手工记录不
自动获得 E6 准入。`factory_inventory_detection_v2` 会重新执行归档像素检测与固定逐视角
融合；一次标签观察不填充雷达轴。

## 配置 smoke（14 型）

```bash
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  python tools/scenario_generation/configuration_smoke.py \
  --output tmp/factory_benchmark/configuration_smoke_14.json
```

不启动 Isaac；每条记录固定默认 controller、场景资源、传感器/工具、spawn 条件和适用协议
原语，把缺失控制器/场景资产、附件冲突及未配对空中执行器写入 `execution.blockers`。
`ready_for_isaac_smoke` 只表示输入检查通过，仍必须通过
`simulator.py → open_simulator_session → env_builder` 和 `physics_gate`。协议按运动形态
分开：地面轮式 waypoint、MuSHR/Coco 曲率约束 Ackermann、Go2/B2/M20/Lite3 legged、
G1 biped、空中 camera capture aerial scan。已有独立物理检查结果时可用 `--physics-gates`
覆盖对应配置的 gate。

形态协议备注：导航节点直接采用 evaluator 的访问区域谓词，访问成功后可离开区域。
Pepper 使用移动 `base_link` 位姿和固定根坐标系的虚拟底座速度，已在原场景完成 1763 步
导航；报告的 `navigation_state_sources` 与 `pose_trajectory` 给出反馈来源与采样，支持
核对实际到达事件。M20 原长路线已通过；Go2 原长路线的低高度终态仍需健康诊断。
2026-09-16 的 Iris/Pegasus 配对均以 727 步完成，含 0.5 s 连续悬停及真实 RGB 帧；Iris
首轮因远程材质依赖缺失未获 E6，完整迁入 `usd/robot/pegasus/materials/` 后通过（保留
所有非资产路径内容；Pegasus 优化天线网格保留原拓扑材质索引，世界变换与网格外内容
一致）。

## 历史参考报告

历史 Isaac 记录仅适用于各自冻结的源码、任务和资产，不能自动继承为当前版本的验证：

- 2026-09-16 双视角 CPU smoke：`inspection_01.json` 4/4 节点、1 帧空中 + 3 帧地面 RGB；
  空中位置误差约 0.0317 m、偏航误差约 0.000472 rad；目标中心和八个包围盒角点均入图。
  旧 CF2X 观察位的失败像素保留，不以新结果覆盖。
- `traffic_01.json` 5000 步 CPU smoke：两路线节点完成、共享资源无冲突，等待约 59.68 s，
  吞吐 2。
- `coverage_01.json`：3/3 工位、最大观测年龄约 1.24 s；dropout 变体 3820 步、35 s 退出、
  11.38 s 后备用首帧，各工位最大帧间/结束间隔 25.36 s（阈值 30 s）。失败与成功报告均
  保存在 `tmp/factory_benchmark/recovery_20260916/`。
- 首轮单位成本 Isaac 配对：固定和贪心均 2582 步通过；优化分配 6733 步完成六节点但码头
  观测过期 55.86 s（阈值 30 s）执行验证失败，报告保留于
  `tmp/factory_benchmark/scoped_microtests_20260916/paired_methods/`。
- 路线代价复跑：三方法同选短路线，总估计距离 5.1213 m，各 2582 步通过，
  最大观测空档 1.26 s（`tmp/factory_benchmark/route_cost_comparison_20260916/`）。
- 真实负例：距观察点 1.5 m、允许半径 0.5 m 处取到四帧，等待节点完成但观察区验证失败、
  E6 导入拒绝。
- 运行证据绑定（provenance）：2614 步、3/3 工位，启动前请求与 436 个运行文件一致
  （`tmp/factory_benchmark/provenance_20260916/`）。

## 相关文档

- [交付状态总览](../benchmark/DELIVERY.zh-CN.md)——三档交付状态与剩余 blocker
- [设计与实现边界](../benchmark/BENCHMARK_DESIGN.zh-CN.md)——三场景出题设计
- [垂直探针协议](../benchmark/VERTICAL_TRAVERSAL.md)——楼梯 probe 准入与健康判定
- [阶段清单](../benchmark/TODO.zh-CN.md)——阶段 1–18 权威勾选记录（本目录历史清单见 [TODO.zh-CN.md](TODO.zh-CN.md)，设计依据见 [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)）
