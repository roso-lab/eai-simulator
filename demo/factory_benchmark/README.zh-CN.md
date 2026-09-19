# Factory benchmark 应用

[English](README.md)

本 demo 的任务蓝图、资产白名单、实验配置由 `scenario.py` 维护；`config.py` 指定场景 USD 和占据地图。`scenarios/navigation_smoke.json` 是维护的导航启动样例。后续可在此目录增加人工维护的任务；生成的任务池和实验结果放在 `tmp/`。

2026-09-17 出题边界核对：当前 Factory 主题包是 F-INS-01 的证据驱动工位派遣，
不是完整工业巡检或生产恢复。本文历史 Isaac 记录仅适用于各自冻结的源码、任务和
资产，不能自动继承为当前版本的验证。本次仅更新文档，未运行 Isaac、部署新任务或
发布资产；不宣称三个场景的完整任务已被真实感知、物理搬运或爬楼验证。

三场景的完整出题说明见 [设计与实现边界](../benchmark/BENCHMARK_DESIGN.zh-CN.md)：

| 场景 | 场景相关骨架与意义 | 当前限制 |
| --- | --- | --- |
| Factory | 俯视取证 → 地面接近 → 近景取证 → 按信息派往 assembly/dock 工位；箱体库存标签把观察结果与作业去向关联 | 模型只控制最后派遣；F-TRAFFIC 评测共享通道调度，F-COV 评测工位值守。没有危险隔离、设备复位或生产恢复验收 |
| Warehouse NVD | 货架扫描、双侧接近、木箱联合集货、纸箱暂存与出库、派送后扫描、dock 交接等待和通道收尾，共 10 节点 | `wooden_crate_a`/`cardbox_c` 对应订单身份、数量与交接链；真实 SKU/dock 适配器及物理搬运仍待接入。固定 DAG 不评测自主订单优先级选择 |
| Modern House NVD | 入口检查、扶手椅联合整理、指定书籍暂存、复核定位、两台机器人撤离、最终动线复核和等待，共 11 节点 | `mh_armchair`/`mh_bookstack` 对应恢复入口至生活区通行的目标；真实身份/动线采样及物理搬运仍待接入，不评测未知物品搜索或一般住户安全 |

三个业务轨道给被测模型的是四类输入：

1. `release/public_task.json`：场景、公开机器人及接口、目标区、子任务 DAG、依赖、
   资源和业务规则；`business_rules.task_skeleton` 是可供出题 LLM 再生成的职责骨架。
2. `release/participant_api.json`：合法动作/事件格式、受控节点和当前运行时支持范围。
3. `public_task.json` 内的 `factor_manifest`：因子、等级、证据轨道及单因子比较规则。
4. 运行中已投递的观测、消息、状态和生命周期事件，不能提前获得未来事件。

Factory 模型只对 `route_confirmation` 提交 `navigate_goal/wait/consume_message`，
决定是否等待或消费可见消息、派往哪个公开站点。前三阶段由 benchmark 管理。
Warehouse/House Isaac 模型可在既定 DAG 上 `allocate/reassign/navigate_goal`；
运输目的地固定，携带中不能改派。generic 模型 `wait/consume_message` 只在独立的
无运动协议 harness 中应用；可执行的 wait 技能节点不代表同名动作已接入。

benchmark 固定资产、初始团队、任务分解、物品业务绑定、目标区、阈值和预算，负责
调度、基础运动、证据采集及评分。模型输出为合法 `participant_action` envelope，
说明文字不进入机器得分。隐藏侧保留真实标签/对象身份、未来故障与消息日程、
候补池、参考指派及 detector/dock/access-path 独立真值；模型自报成功不作证据。
当前固定 DAG 再生成不计为解题模型自主分解能力；道具声明也不证明实例化或物理交互。

领域数据通过 `domain_evidence_provider` 注入公共运行器。没有真实领域适配器时，
`domain_evidence.adapter_status` 保持 `pending_live_scene_adapter`；即使所有节点
完成也不能认定订单履约或家居动线恢复。收集到记录仍须隐藏评测器核验；异常批次
回滚并标记 `collector_error`。F-INS 的专用标签评测不能替代其他领域的检测器。

`member_join` 和递增版本的 `capability_change` 目前只支持协议状态、资格更新与
回放；Isaac 动态创建/激活实体、在途传感器或控制器重配置尚未实现，运行边界会拒绝。
`member_exit` 仅在受支持的纯地面 scene-neutral/F-COV 任务中使用预加载替补及实测
位姿，当前混合无人机模板和 F-INS dispatch 不接收该故障。延迟、丢包、过期和带宽
变化是确定性消息协议，不是实测无线模型。换班、充电或隔断可作为因子的业务理由，
但事件声明不能当作真实干预已生效。

无人机只承担基础运动：二维航点加世界高度目标，连续 **swept-AABB + 保守体素**
门禁，阻塞时尝试有界局部绕行，无可行候选或空间未知则拒绝。它不是完整三维规划、
动态避碰或飞行安全证明，也不计为异构团队协作能力。三个场景均已声明体素路径。
Factory 的 `build_factory_voxel_product.py` 将原生 USD 启用碰撞 Mesh 的世界 AABB
保守栅格化，并记录根 USD、地图、产品 hash 与参数。声明和本地产品不证明
provider 发布或飞行通过；缺失、无效产品或元数据校验失败仍拒绝准入。
历史 F-INS 飞行记录不能豁免当前体素与运行证据门禁。

楼梯通过显式 vertical metadata、`main_stair` 和 `F-VERT-01` 候选探针保留开放入口，
用于上层工位可达性与形态差异研究；普通地面任务仍避开未验证表面。入口开放及
`admitted_for_probe=true` 与爬楼验收不同，当前准入声明为 `execution_validated=false`。
`vertical_task` 是 benchmark 管理的候选执行器，无校准支撑/接触证据时以零指令
采样并停止推进；必须以完整姿态、连续健康停留和实测到达验收，不能靠目标高度或
teleport。`scenarios/vertical_stair_01.json` 和独立 `vertical_challenge.py` 已提供
候选声明/编译入口，原 `challenge.py` 仍只编译派遣契约。`measure_factory_stair.py`
使用 OpenUSD 实测两段 authored tread flight 并绑定根层和 67 个依赖层 hash；支持面
搜索现按楼梯 AABB 裁剪，生产资产测量由约 90 秒降至 10.13 秒。编译器可通过私有
`--stair-measurement` 选择 `flight_02`，但候选坐标仍不提升为物理测量，
`release_gate.executable=false`、`release_gate.scoreable=false`；模型只可提交
给定机器人的路线意图或有界等待，格式校验不会启动运动，`executed_action_types=[]`。
Isaac 意图 adapter、PhysX 支撑/连通性、接触校准及独立物理验收仍 pending。此单机诊断未启用
生命周期/通信实验，尚无业务轨道的 `factor_manifest` 和实时事件输入，不能称为
可执行计分的参赛任务或爬楼通过。

在仓库根目录、`env_isaaclab` 环境中运行：

```bash
python -m demo.factory_benchmark.main --check-assets
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/navigation_smoke.json --gui --steps 400
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/traffic_01.json --headless --steps 5000
python -m demo.factory_benchmark.main --scenario <任务.json> --headless --report tmp/factory_benchmark/execution.json
```

生成 Factory 候选时可指定任务族，例如 `tools/scenario_generation/generate_benchmark.py
--scene-key factory --task-id F-TRAFFIC-01 ...`；任务族会校验阶段 ID、共享资源和技能，
避免所有候选都被强制生成空中扫描与联合搬运。

样例使用 Carter + 未缩放 Z1，执行一段短距离导航；检查场景组装、机械臂质量/惯量和导航，不验证抓取。默认 CPU PhysX。搬运任务目前只支持显式 `--transport-mode kinematic`，报告仍区分运动学搬运与真实物理操作。

任务 JSON 必须声明 `scene_key: factory`。机器人和附件始终经由 `simulator.py → open_simulator_session → env_builder` 构建，执行正常资产预检。`demo/benchmark/runtime.py` 复用会话，不另建 Isaac 应用。

阶段清单见 [TODO.zh-CN.md](TODO.zh-CN.md)，总体方案见 [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)。
Factory 场景契约由 `scene_contract.py` 维护；任务需求与 Fleet 配置可通过共享
`demo/benchmark/allocation.py` 做可解释分配。能力雷达只接收配置级测量，未测量轴保持未知。
可用 `python tools/scenario_generation/export_capability_profiles.py --output <report.json>`
导出当前 14 型配置覆盖矩阵，或使用 `--format html` 查看审核表。导出结果不排序机器人，
未测量雷达轴保持 `unknown`；它还不是导航、传感器或操作能力的实测结论。
分配结果必须经过 `compile_assignment_plan()` 冻结到 V2 `phase_graph` 后才能执行；
`demo/benchmark/observations.py` 记录可供任务消费的观测来源和有效期。F-INS 样例已经
使用 Iris 已有位置/偏航控制器，声明观察高度与朝向，从 Isaac RGB annotator 取得真实帧，
并记录 frame hash、shape/dtype、observation_id 与 consumed_by。帧记录中的 `sensor_pose_only`
仍是采集时机器人底座位置代理，不能解释为真实相机外参或目标位置；
2026-09-16 的历史双视角 CPU Isaac smoke 记录为 4/4 节点，获得 1 帧空中和 3 帧地面 RGB；
当前尚未接入目标检测、定位误差或遮挡复核，因此不构成完整感知巡检结论。
当时的维护场景 `inspection_01.json` 曾用 2267 步完成；独立投影核验中，目标中心和
八个包围盒角点均位于两视角图内。空中位置误差约 0.0317 m、偏航误差约 0.000472 rad。
旧 CF2X 原观察位和调整后的观察位均未使目标入镜，相关失败与像素保留，不以新结果覆盖。
`ObservationStore` 已提供 detector 输出契约和 evaluator-only 的识别/定位评测函数；它们只有在
检测结果绑定真实帧、并由评测器提供独立目标真值时才会通过，不能由观测记录自证。
Factory USD 审计还绑定了存储区候选箱体的真实 prim 和世界包围盒；该绑定目前只表示
`visual_reference_only`；目标真值、两个允许视角、scenario/USD hash 已在 evaluator-only fixture 登记，
遮挡与检测仍待验证。
F-INS 的维护场景现在显式分为 `aerial_view → ground_approach → ground_view(wait) → route_confirmation`，
其中 `ground_view` 使用真实相机窗口并通过 `observation_ref` 观察同一 inspection subject；
evaluator-only fixture 位于 `evaluator/inspection_01.json`，不进入运行时观测。
Factory 运行报告还会输出 `factory_metrics`，其中包括共享资源预约区间、等待/吞吐、
实际轨迹中的最小机器人间距，以及基于观测记录的巡检/覆盖评测接口。

`traffic_01.json` 的维护配对使用 SimpleCar 执行 dock 路线、Carter 执行 yard 路线。
在当前 Factory 碰撞几何下，反向分配会使 Carter 在 dock 入口停滞；这属于配置/场景配对
结果，不能泛化为 Carter 的通行能力结论。一次 5000 步 CPU Isaac smoke 已验证该维护配对
完成两个路线节点、共享资源无冲突，等待时间约 59.68 s，吞吐为 2。

`coverage_01.json` 是单机/同构对照模式下的持续覆盖基线：三个 SimpleCar 并行到达
storage、assembly、dock，各自使用 C01 相机在 45 秒窗口内每秒采集真实 RGB 帧。并行
CPU Isaac smoke 完成 3/3 工位，最大观测年龄约 1.24 s，资源无冲突；这证明观测新鲜度
链路，不证明目标识别准确率、定位误差或成员退出后的恢复能力。`coverage_01_dropout.json`
是同一任务族的成员退出变体：`simple_car_1` 在 storage 观测期间退出，`simple_car_4`
先导航到独立备用观察位，再观察同一 storage subject；退出车保留原地。
接管必须完成导航及完整观测窗口。报告中的 `member_exit_recovery` 分别评价调度延迟、首帧恢复延迟
和观测连续性，不推断机器人可靠性、安全裕度、payload、抓取力、抗风或载荷动力学。

该变体的历史一次显式 5 Hz 渲染 CPU Isaac 记录为：3820 个控制步、35 s 成员退出、
11.38 s 后产生备用首帧，各工位最大帧间/结束间隔 25.36 s，小于 30 s 阈值。
默认渲染在 180 s 控制循环墙钟预算内超时，失败和成功报告均保存在
`tmp/factory_benchmark/recovery_20260916/`；事后记录不等于运行前冻结证书，不自动晋升 dossier。

```bash
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/coverage_01_dropout.json --headless --steps 9000 --render-period-s 0.2 --wall-timeout-s 480 --report tmp/factory_benchmark/dropout.json
python -m demo.factory_benchmark.compare_allocation --scenario demo/factory_benchmark/scenarios/coverage_01.json --measurements <records.json> --output tmp/factory_benchmark/allocation.json
```

`--render-period-s` 向上对齐控制步，不改变 physics dt 或 control decimation；记录运行仍为
200 Hz 物理与 50 Hz 控制，不验证传感器原生发布率。`--wall-timeout-s` 只限制控制循环墙钟，
启动时间另计；超时保留失败报告。分配对照共用候选、需求和候选评估次数预算，包含固定、
贪心及有限枚举优化。顺序阶段可复用机器人，`same_team_as` 绑定到达与观测成员。
默认单位分配代价不代表路线时间；预算用尽且无解时报告 `unknown`，只有完整枚举后才可
声明对应离散成本的 `optimality_proven`。首轮单位成本 Isaac 配对中，固定和贪心均
2582 步通过；优化分配 6733 步完成全部六节点，但码头观测过期 55.86 s，超过原有
30 s 阈值，故执行验证失败。原报告保留在
`tmp/factory_benchmark/scoped_microtests_20260916/paired_methods/`，不构成统计排名。

`--cost-model map_route_length` 按膨胀占据图计算候选出生点至工位的路线距离（米），
绑定同一成员的 wait 增量路程为零。无路径候选保留明确拒绝原因，不挪动原目标。
目前仅支持独立导航根节点和其后同观察位的 wait；中间起点、空中空间、转弯动力学及
实际耗时不由此模型预测，观测新鲜度仍通过真实运行验收。

```bash
python -m demo.factory_benchmark.compare_allocation \
  --scenario demo/factory_benchmark/scenarios/coverage_01.json --measurements <records.json> \
  --cost-model map_route_length --output tmp/factory_benchmark/allocation.json
python -m demo.factory_benchmark.run_allocation_comparison \
  --scenario demo/factory_benchmark/scenarios/coverage_01.json --measurements <records.json> \
  --output-dir tmp/factory_benchmark/new_comparison
```

第二条命令会顺序启动三次 Isaac，启动前冻结分配、任务和请求，输出目录必须是新目录。
默认使用地图路线成本、CPU、9000 步、0.2 s 渲染周期、480 s 控制墙钟和 630 s 子进程
时限。每种方法保留完整失败记录，比较实际资产与时间步；源码/输入漂移会中止配对。
测量必须匹配当前源码；修改运行代码后应重跑准入，保留旧版本原报告。

历史路线代价复跑记录为：三方法均选择同一组短路线，总估计距离 5.1213 m，各 2582 步通过，
最大观测空档 1.26 s、共享资源冲突 0。结果位于
`tmp/factory_benchmark/route_cost_comparison_20260916/`。这只证明一个布局的执行闭环，
不证明优化器相对于其他方法有普遍优势。

真实负例在距观察点 1.5 m、允许半径 0.5 m 的位置取到四帧，等待节点虽完成，但运行
观察区验证失败、E6 导入拒绝。当前覆盖 freshness 从每站首帧起检查帧间及结束空档，
不包含初次建站延迟，不能解释为从 episode 起点连续覆盖。

Factory 分配消费来源可复核的 v2 配置微测试。导入时重新核对启动前请求、执行源码、实际
资产文件、质量审计、配置 ID 和原始报告，再从到达事件/轨迹、真实帧和完整观察窗口提取指标。
修改记录中的指标、适用范围或附件不能改变原始证据；旧 v1 手工记录不会自动获得 E6 准入。
F-INS 的 `factory_inventory_detection_v2` 还会重新执行归档像素检测与固定逐视角融合，保留
帧数、识别帧数、实际 marker ID 和融合定位误差。它只准入完全相同的配置、世界、观察节点和
协议 scope；一次标签观察不会填充雷达轴，也不能作为通用识别可靠率。导航记录不能替代检测记录。

```bash
python tools/scenario_generation/record_configuration_microtests.py \
  --scenario <冻结任务.json> --attempt <批运行attempt.json> --output <records.json>
python tools/scenario_generation/export_capability_profiles.py \
  --measurements <records.json> --format html --output tmp/factory_benchmark/capabilities.html
```

可重复传入 `--attempt`，每次失败/不可核验尝试都会保留原因；仅提供成功记录不能估计成功率。
导航/取帧的一次通过显示为 `protocol_checks` 及原始指标，不再填成雷达满分。
导航原始路程是采样的 episode 前缀长度下界；它不等于某一阶段的完整路径或真实能耗。
取帧协议要求完整窗口、最大 30 s 空档、逐帧位于目标区以及窗口轨迹采样未离区；空中
帧还需匹配目标高度、实际悬停事件和 0.2 m 位置容差。运行判定和证据转换共用检查，
不能以“曾经到过”或帧上的工位标签代替实际观察位。该位置是机器人底座世界坐标代理，
不是已校验的相机外参；这些检查也不证明连续不离区、目标可见、识别或定位。
分配同时匹配配置、场景布局、目标区域、观测对象和窗口长度；某近场目标通过不能准入另一个工位。
新分配仍须重新校验几何与实际执行。当前 SimpleCar+C01 的六条记录来自一个三成员 episode，
不能计为六次独立重复或硬件能力分数。

F-INS 视角审核可加 `--archive-camera-frames --report <report.json>`。这会在同级新建
`<report>.frames/`，保存未修改的 RGB/RGBA `.npy`、PNG 预览及同一渲染产品的
`CameraParams`/`ReferenceTime`；数组/预览摘要绑定到 observation，转换时检查像素一致性。
该标志进入启动前运行条件，目录已存在时拒绝覆盖。参数来自仿真渲染器，不是实物相机标定；
渲染参考时间与控制采样时间分别保留。它不会改动 EAI 相机挂载、机器人质量或机械臂。

`aerial_scan` 可设置 `scan_height_m`，表示米制世界 Z，必须高于目标位置；目标仍放在
声明楼层。省略或 null 时兼容 `max(1.5, target_z + 1.0)`；悬停检查和配置证据绑定实际
高度。此配置不证明飞行净空。归档 annotator 初始化结束后、正式任务前正常 reset，报告
保留前后原生位姿。原 F-INS 视角实测发现空中目标在图外、地面目标在相机后方，即使取帧
窗口完成也不能宣称目标入镜、识别或定位成功。

`scan_yaw_rad` 可要求 [-pi, pi] 内的绝对世界偏航角。执行器通过现有 `goal_yaw` 接口驱动
Iris/Pegasus 位置控制器；CF2X 的位置策略和直接转子模式会被明确拒绝。位置误差 ≤0.2 m
与偏航误差 ≤0.15 rad 必须同时连续满足 0.5 s，报告和配置证据绑定要求与测量。机身朝向
控制仍不等于相机光学标定或目标可见性。

C01 Camera 构造器现使用 Isaac 默认 world 相机轴（+X 前、+Z 上）的单位四元数。
旧代码把已转换的 USD 四元数再传入该 API，造成重复轴变换，画面横置、视线偏离安装前方。
本修复保留实体安装位置和机器人动力学。

```bash
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/inspection_01.json \
  --headless --steps 9000 --render-period-s 0.2 --wall-timeout-s 480 \
  --archive-camera-frames --report tmp/factory_benchmark/inspection_capture.json
python -m demo.factory_benchmark.inspect_camera_frames \
  --scenario demo/factory_benchmark/scenarios/inspection_01.json \
  --report tmp/factory_benchmark/inspection_capture.json \
  --output tmp/factory_benchmark/inspection_projection.json
```

第二条命令仅供 evaluator：核对独立真值 fixture、来源哈希、原始像素后，把目标中心和
包围盒角点投影到实际相机图像；不向机器人策略提供真值。投影入图不等于无遮挡可见，
更不等于检测或定位成功。

库存标签工程基线使用 `--inventory-label-id 17 --archive-camera-frames --report <report.json>`。
它通过同一 simulator.py 会话加载一个 Factory USD 覆盖层，把黑框边长 0.09 m、纸张外边长
0.12 m 的 ArUco 标签放在已绑定箱体的真实正面三角面上。整张纸必须在该三角面内，保留
1 mm 边距与 1 mm 表面偏移；独立评测重新核对渲染网格的实际尺寸与支撑，不能以箱体
包围盒代替。源场景和物理参数不变。生成层位于 `usd/scene/factory/benchmark/generated/`
并通过相对 sublayer 引用原场景，属于运行数据。`--inventory-label-state covered|absent` 分别
生成不透明表面覆盖和无标签条件，均写入启动请求与实际场景来源。

解码器只接收原始 RGB、同渲染产品相机参数、公共字典和标签边长；期望 ID、箱体/标签真值
不传给解码器。在线输出保留角点、PnP 重投影残差和备选解残差，未校准置信度为 null。
角点使用 OpenCV 的 `CORNER_REFINE_APRILTAG`，解码字典仍是 ArUco 4x4_50；IPPE_SQUARE
与迭代 PnP 候选只按像素重投影误差选择，保留方法名及备选残差，避免已复现的完全正视
图像 IPPE 退化，不扰动原像素。同一视角、同一解码 ID 的全部有效位姿按坐标中位数融合，
前提是标签在窗口内静止；评测这个固定估计，保留每帧误差，不按真值挑最好的一帧。
该方法在已有 ID17 开发图像上选择，源码冻结后须重新运行条件矩阵，不能把开发重算作为留出验证。
位姿按 OpenCV 的相机轴转换回世界坐标；世界外参来自仿真器，不声明机器人自身定位精度。
实现依据：[OpenCV ArUco](https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html)、
[PnP/IPPE_SQUARE](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html)。

`python -m demo.factory_benchmark.inspect_inventory --scenario <scenario.json> --report <report.json>
--attempt <attempt.json> --output <audit.json>` 重新解码归档像素，核对在线输出，并从实际 USD
标签几何计算独立定位误差。标签识别、所有视角可定位、遮挡负例和业务成功分别报告；
当前还需验证检测驱动的后续决策，因此此基线不称为完整 F-INS 验收。
无论实际采集到几路图像，评测都要求任务声明的空中和地面视角。缺失视角不能使正例或负例
通过；矩阵退出状态单独检查这些条件，来源核验成功不等于识别条件成功。

地面取帧 wait 可声明 `wait_yaw_rad`。Carter/Scout 的差速控制器与 SimpleCar 平面控制器
使用原有角速度接口转向，连续 0.5 s 保持误差不超过 0.05 rad 后，才开始完整的 `duration_s`
取帧窗口。转向计入节点 timeout，但不占用观察时长；逐帧及轨迹采样的朝向均需核验。
朝向进入配置证据作用域，其他控制器组合在执行前明确拒绝。
导航恢复监视实际 waypoint 距离或朝向误差的单调改善，阈值分别为 0.01 m 和 0.05 rad。
持续慢速接近及有效转向都算进展；来回振荡不能反复重置计时。到达仍按原任务半径判定。

`python -m demo.factory_benchmark.run_inventory_matrix --output-dir tmp/factory_benchmark/new_inventory_matrix`
冻结并依次执行 visible17、covered17、absent17、visible23；每次原始报告、失败和像素评测均保留，
拒绝覆盖已有目录。配对运行的文件闭包仅允许声明的标签覆盖层不同。
`--conditions visible17` 可选单条件工程检查。原 0.18 m 标签版本的纸张跨过箱体侧壁边缘，
已作为场景制作失败证据保留，不沿用为本协议验收。

派车变体使用维护的 `scenarios/inspection_dispatch_01.json`：公开业务规则为 ID17 →
assembly_site、ID23 → dock_site，二者是实际 Factory 观察站。任务要求派车到对应站点，
不包含物料搬运或设备操作。站点、坐标和消息有效期定义在 `inventory_dispatch.py`；
`immediate` / `wait_for_message` 已移到 `baseline_solver.py`，只作为公开参考策略，
不是参赛方必须采用的任务答案。两个目的地在启动前都经过几何、连通和路径检查。

面向第三方评分的 F-INS 发布包由以下命令生成：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.factory_benchmark.challenge \
  --output-dir tmp/factory_benchmark/f_ins_challenge
```

`release/public_task.json` 不包含 `preferred_agent`、真实标签、隐藏故障时刻或参考决策；
`release/participant_api.json` 声明完整动作词汇；当前 F-INS 运行范围只开放
`navigate_goal`、`wait`、`consume_message`，并明确把 `allocate`、`reassign` 标为待实现；
`release/baseline_manifest.json` 只描述可选基线，不包含分配答案；
`evaluator/hidden_evaluator.json` 保存真值来源、成功谓词和发布前 QA。配置 E6、像素重放、
场景闭包和参考路线属于发布方 QA，不是参赛系统必须预先提交的能力证明。

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.factory_benchmark.run_inventory_matrix --dispatch \
  --output-dir tmp/factory_benchmark/new_dispatch_matrix
```

默认矩阵为两个真实标签 ID × shared/withheld/delayed 六次新运行，用于参考基线和内部 QA。
每个观察窗口完成后，用全部有效像素检测按固定规则融合并发布带来源/时间的消息。
当前 `immediate` 基线在派车阶段开始时只做一次决策：所有可用消息一致指向一个已知 ID 时选择对应目的地，
否则走默认 assembly_site。禁发只作用于传感器到派车者的消息链；延迟条件增加 60 s
仿真时间的送达延迟，决策后的消息不改变已经选择的路线。有效期从首个源帧起算 120 s。
消息缺失不会锁死 DAG；默认猜对仍判派车成功。F-INS Isaac 运行器可通过
`demo.factory_benchmark.main.main(..., participant_adapter=<adapter>)` 注入 Python adapter。
adapter 只接收已送达消息、公开 phase/tick 状态和仿真时间，返回动作经过 public task/API
校验后才执行；schema v3 报告保存完整 event/action log。baseline 使用同一入口，审核器只对
`kind=baseline_solver` 重放策略；第三方策略不被当成标准答案，只核验动作链和业务结果。
跨节点分配和故障后的改派仍待实现。可用 `--conditions shared17 shared23`
先检查两条路线，或在单次 demo 入口设置 `--inventory-dispatch-mode shared|withheld|delayed`
和 `--inventory-message-delay-s`。这些选项都进入启动请求。

派车者不自动消费 prerequisite 的原始图像，也不获取场景真值 ID。选中目标后按当前位置
重新规划，同时替换该节点的到达谓词。原 scenario 保持不变，报告单列派车决定、
真实起点、路线、解析后任务摘要；决策前经过目的地不能算本次派车到达。
`dispatch.evaluation.json` 独立重放像素、融合、消息可用时刻及策略，再从位置轨迹
核对实际到达。`dispatch_success` 是正确站点到达结果；来源/协议验收与该结果分开。
例如 ID23 禁发后到达默认站点是一个保留的业务失败，不应变成无法取帧或来源失败。
矩阵命令对执行/来源/观察/决策核验错误返回非零；一个可核验的错误默认路线仍写为
`dispatch_success=false`，不因研究条件产生预期业务失败而丢弃整组结果。

两个视角的识别定位检查是独立的传感器工程验收，不要求派车必须收到两路消息，
也不能据此证明空中平台、双视角或异构团队必要。这些是新会话配对，不是完整状态回滚
或同噪声重放；跨世界可靠率仍待评估。动态派车报告暂不导入固定目标的配置微测试协议。

Factory 的 F-INS/F-TRAFFIC/F-COV 地面导航从已有场景模板读取楼梯、坡面包络，
在规划网格中排除尚未通过当前协议验证的这些区域；机器人足迹仍由原膨胀地图处理。
几何准入拒绝位于这些区域的地面出生点/目标，初始规划、恢复规划、连通门禁和分配路线
代价使用同一掩码。额外 0.02 m 仅包住模板边界的小数舍入差，不是经过测量的碰撞安全裕度。
派车启动前核验这些边界确实包住当前 USD 对应 prim。生产地图、USD、机器人几何和物理
均不改写。该限制也适用于尚无楼梯/坡面证据的足式配置，不因机器人形态授予通过能力。
旧路线曾穿过 main_stair：两种共享信息条件均正确识别并选择目标，却在楼梯前各经
12 次重规划失败。这些属于导航失败，不会被识别成功覆盖。

空中帧和完成事件现在记录读取状态时已完成的物理步，避免相对调度完成时间多记一个
控制周期。执行器异常在仿真器清理前保存到 `.failure.json`，批运行保存完整 stdout/stderr
及摘要；缺少正式报告仍是执行失败，退出码为零不能替代有效报告。

可用下面的命令生成全部 14 型的 Factory 配置 smoke 输入。它不会启动 Isaac；每条记录都
固定默认 controller、场景资源、传感器/工具、spawn 条件和适用的协议原语，并把缺失控制器
或场景资产、附件冲突以及未配对的空中执行器写入 `execution.blockers`。`ready_for_isaac_smoke`
只表示已声明资源及输入检查通过，不保证完整 USD 依赖或物理有效；仍必须通过 `simulator.py → open_simulator_session → env_builder` 和
`physics_gate`；`blocked`/`not_executable` 不能进入运行。

```bash
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  python tools/scenario_generation/configuration_smoke.py \
  --output tmp/factory_benchmark/configuration_smoke_14.json
```

若已有独立物理检查结果，可用 `--physics-gates gates.json` 覆盖对应配置的 gate；
`{"m20":{"status":"failed","reasons":["base_support"]}}` 会生成
`execution.status=not_executable`，而不是把失败配置放入可运行池。

协议按运动形态分开：地面轮式使用 waypoint navigation，MuSHR/Coco 使用曲率约束的
Ackermann navigation，Go2/B2/M20/Lite3 使用 legged 原语，G1 使用 biped 原语，空中配置
使用 camera capture 的 aerial scan。CF2X、Iris 和 Pegasus 各有历史位置控制与取帧配对记录；
准入绑定各自的目标位置控制器，直接转子速度配置仍不支持此协议。
生成器仅建立输入契约，不产生额定 payload、抓取力、碰撞安全
裕度、抗风能力、载荷动力学或 E7 physical manipulation 证据。

足式/轮足近场协议为 `legged_navigation_v2`，双足为 `biped_navigation_v2`。新输入保存
scenario hash，并校验出生位姿/附件/控制器元数据、几何、连通性和初始未成功。
smoke schema v2 的配置 ID 与能力画像共用同一算法，dossier 快照哈希独立保存；旧版输入需重新生成。
近场通过只证明该路线与配置，不能替代保留的 Go2 长路线失败或支持型号排名。
M20 原长路线已通过；Go2 原长路线的低高度终态仍需健康诊断。
导航节点直接采用 evaluator 的访问区域谓词，保留精确末端目标；访问成功后可离开区域。
Pepper 使用移动 `base_link` 位姿和固定根坐标系的虚拟底座速度，已在原场景完成 1763 步导航。
报告的 `navigation_state_sources` 与 `pose_trajectory` 给出反馈来源、米制世界坐标、yaw 弧度、
每 10 步采样及初始/到达/失败/终态样本，支持核对实际到达事件。

2026-09-16 的 Iris/Pegasus 配对均以 727 步完成，含 0.5 s 连续悬停及真实 RGB 帧。
Iris 首轮因为远程材质依赖缺失而未获 E6，完整迁入 `usd/robot/pegasus/materials/` 后才通过；
该修改保留所有非资产路径内容。Pegasus 优化天线网格保留了原拓扑的材质索引，已恢复源资产
中的该无碰撞视觉网格；世界变换与网格外内容保持一致，727 步复跑通过且原 geomSubset
告警消失。单布局配置 smoke 与完整巡检识别任务分别验收。

分配结果还包含 `reallocation_certificate`。它保持 Factory 任务需求、前置关系和成功条件
不变，重新枚举单机和同构团队，而不是把原始 DAG 成员数当作“任务必要性”。`single_agent_feasible`
表示允许重排后存在单机方案，`homogeneous_team_feasible` 表示存在同构团队方案，
`heterogeneity_necessary` 只有在单机和同构团队都失败且完整搜索完成时才会为真；预算耗尽时
返回 `unknown`。当前 F-COV 结果是最少 3 个机器人、同构团队可行、异构不是必要条件，仍待
真实 Isaac 配对执行复核。

新增运行证据绑定也已通过完整 `coverage_01.json` 验证：2614 步、3/3 工位、最大观测间隔
1.24 s；启动前请求、436 个运行文件和实际时间步一致，批量记录与 E6 导入预览通过。
结果位于 `tmp/factory_benchmark/provenance_20260916/`，未合并或晋升机器人档案。
该机制保留每次运行的原始条件，不能替代跨布局统计或目标识别验证。

资源目录、迁移和准备流程见 [公共说明](../benchmark/README.md)。Factory 的 `--check-assets`
还会审计核心 USD prim 绑定和来源 hash。机械臂保持源资产质量和惯量；任务容量策略不等于
真实额定 payload，也不构成 E7 物理操作证据。
