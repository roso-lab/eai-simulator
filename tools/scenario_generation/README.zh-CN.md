# 场景生成

[English](README.md)

为 `python simulator.py --scenario-file` 提供 Factory、Warehouse NVD、Modern House NVD 三类场景的 V2 JSON 离线校验、异构 benchmark 门禁与可选 LLM 生成。V2 契约保持场景骨架本身不可变：模型只能选择已注册的机器人、挂载、经过审计的资产、目标点和基于 prerequisite 的阶段 DAG；模型看不到也不输出 USD 路径、Prim 路径或控制器源码路径。

这是 benchmark 基础设施，尚不是可直接发表的完整 benchmark。解释标签或比较方法前，请先阅读[科学范围](SCIENTIFIC_SCOPE.zh-CN.md)。

## 契约

三个应用入口分别位于 `demo/factory_benchmark`、`demo/warehouse_benchmark`、
`demo/modern_house_benchmark`，各自维护任务蓝图、实验配置、资产白名单和导航启动样例。
目录说明和资源迁移命令见[公共 demo 文档](../../demo/benchmark/README.md)。
`execute_episode.py` 保留兼容入口，实际执行器归属 `demo/benchmark/runtime.py`；
机器人和附件仍由 `simulator.py` 统一创建。

- `source/EAI/EAI/scenario_spec/` 负责 JSON Schema、语义校验（ID、引用、DAG、能力、物理范围）、基于占据图的地面几何门禁，以及连通性门禁（静态障碍先栅格化再做洪泛填充，封死路线的摆放会被拒绝）。
- `source/EAI/EAI/scenario_spec/templates.py` 是场景样板层：factory 舞台 + 语义区域 + 从占据图确定性派生的锚点。`operators.py` 是其上的 V3 层：语义任务规格（按区域指定机器人/实体/目标）、解析锚点并编译冻结 V2 episode 的种子采样器、按条件的 delta，以及 TRN-01/SAR-01/COV-03 骨架配方。
- `source/EAI_assets/EAI_assets/scenario_assets.py` 是审计过的资产注册表；`source/EAI_hmrs/EAI_hmrs/scenario_builder.py` 把通过校验的实体编译为 `{ENV_REGEX_NS}/scenario_<id>`（env 根的直接子级，与机器人一致——尚未创建的中间 prim 下的 spawn 路径会被环境克隆器拒绝）的 `RigidObjectCfg`/`AssetBaseCfg`。
- 保存的环境 JSON 保持 version 1，除非它是完整且通过校验的 V2 载荷；把 `composition`/`phase_graph` 混入 V1 文件会被拒绝。
- 本地门禁是权威。Structured Outputs 只约束模型响应的形态；一个场景只有在 schema、语义、几何和连通性校验全部通过后才被接受，且状态只是 `placement_validated`，不是一次执行结果。kinematic 实体是移动障碍（参与栅格化、不作为连通性检查点）；导航到静态实体采用接近语义（最近可通行格）。
- `source/EAI/EAI/scenario_spec/oracle.py` 是确定性**可行性 Oracle**（存在性证书，区别于 Performance oracle 与探针）：每段阶段路径用按机器人膨胀的占据栅格做 A* 求解，只有机器人完整圆盘能通过的路线才算存在——窄门死局在离线阶段即被拒绝，不使用 RL。它同时产出基于能力的最小团队证书（`necessary_robots`、`forced_cooperation`）。机械臂级 3D 无碰检查（CuRobo）是分阶段接入项：报告显式携带 `arm_collision_check: not_installed` 状态而非静默跳过；待 CuRobo 在 GPU 环境安装后，UR5/Z1 搬运段将在 headless Isaac 内做 IK + 无碰轨迹求解，此后实例才可能获得超出 `feasibility_validated` 的标签。
- 实例族是 Suite 算子的数据面：一个基础摆放按条件 delta 物化为多个实例，配对条件共享锚点、位姿、机器人与阶段图，只在声明轴上不同（例如 H2 载荷质量扫掠）。

## 命令

配置证据可以从冻结任务与 `batch_execute` 的 attempt 对象离线导出，不启动 Isaac：

```bash
python tools/scenario_generation/record_configuration_microtests.py \
  --scenario <scenario.json> --attempt <attempt.json> --output <records.json>
```

可重复传入 `--attempt`，对应同一个冻结输入的多次运行；使用 pool 时，先将目标实例的
`execution` 对象导出为 attempt 文件。当前转换器接受没有故障干预和操作 payload policy
的 Factory 导航/相机 episode。它校验当前执行源码、资产、档案身份和质量审计；旧版或
不可核验输入保留拒绝原因。v2 记录还绑定转换器与协议源码 hash，能力导出/准入会重新
从来源文件计算记录，不能只依赖用户填写的 E6 标签。保留任务和 attempt 文件，删除或改写
会使记录失效。单次通过保留原始指标与任务范围，不填为归一化雷达分数。
输出包括所有已提供尝试的账本，但该列表本身不构成预注册的失败分母。

取帧核验复用运行端 `demo/benchmark/camera_validation.py`：逐帧位置和窗口轨迹采样须在
声明观察区内；空中帧还须匹配实测悬停位置、目标与时长。当前位置是机器人底座世界坐标
代理，不是已校验相机外参；通过不能证明目标可见、识别正确或连续不离区。
`aerial_scan` 的可选 `scan_height_m` 是绝对世界 Z，`scan_yaw_rad` 是绝对世界偏航角；
二者均进入微测试作用域，指定朝向时还需匹配真实偏航测量。生成/编译保留这些参数，
执行器只将朝向请求配对到 Iris/Pegasus 位置控制器的原生 goal_yaw 接口。

`batch_execute.py --archive-camera-frames` 将归档选项绑定到启动请求，并传给 demo 执行器。
启用后会在每个独立报告旁保存原始数组、PNG 预览与同一 RGB 渲染流的相机参数；旧版
底座位置字段仍保留其代理含义。归档参数来自仿真器，不代表真机相机标定。

```bash
# 离线注册表视图（不调用 API、不启动 Isaac）
python tools/scenario_generation/generate_factory.py --schema
python tools/scenario_generation/generate_factory.py --manifest
python tools/scenario_generation/generate_family.py --manifest

# 不启动 Isaac Sim 的校验；完整模式会解析 Factory 占据图
python tools/scenario_generation/validate_factory.py --schema-only <scenario.json>
python tools/scenario_generation/validate_factory.py <scenario.json>

# 沉降冒烟：在 Isaac 中实例化、零指令步进、检查漂移
# headless 需 enable_cameras（工具已内置：plain headless.kit 建不起 SDG 管线）
python tools/scenario_generation/settle_smoke.py <scenario.json> --steps 50 --headless --device cpu

# 采样模式：骨架配方 -> 配对实例族（离线，不调用 API）
python tools/scenario_generation/generate_family.py \
  --skeleton TRN-01 --seed 17 --out-dir tmp/generated_scenarios/trn01_demo

# 提案模式：LLM 提出语义任务规格，采样器负责几何
OPENAI_API_KEY=... python tools/scenario_generation/generate_family.py \
  --instruction "设计一个跨封锁区域的双机接力搬运任务" \
  --seed 17 --out-dir tmp/generated_scenarios/llm_demo

# 统一三场景 benchmark 生成器：LLM 只提出完整 V2 候选，程序化门禁决定是否接纳。
# 每轮失败的 schema/几何/连通性/Oracle/团队必要性/执行 contract 反馈都会传给下一轮。
OPENAI_API_KEY=... EAI_NVD_ASSET_ROOT=<NVD_assets_sanitized> \
  python tools/scenario_generation/generate_benchmark.py \
  --scene-key modern_house_nvd \
  --instruction "让 cf2x 先侦察目标，再由带机械臂地面机器人完成搬运" \
  --payload-policy '{"cf2x":1,"m20":40}' \
  --rounds 5 --output tmp/generated_scenarios/modern_house_candidate.json

# 兼容入口也支持三类 scene_key；Factory skeleton 不会静默复用到 NVD 场景。
OPENAI_API_KEY=... python tools/scenario_generation/generate_factory.py \
  --scene-key warehouse_nvd --payload-policy '{"m20":40,"carter":40}' \
  --instruction "设计一个必须由两台异构机器人联合搬运的重箱任务" \
  --output tmp/generated_scenarios/warehouse_candidate.json
python simulator.py --scenario-file tmp/generated_scenarios/warehouse_candidate.json --num_envs 1
```

生成流程会对每次尝试重新执行本地校验，并把结构化错误回传给模型做有界修复（`--attempts`，默认 3 次，最多 5 次）。输出文件以独占方式创建，绝不覆盖已有文件。`EAI_SCENARIO_MODEL` 可覆盖默认模型。

`benchmark_experiment.py` 维护场景资产白名单、可比较的机器人组合单元和难度因子。生成器 manifest 现在只向模型暴露当前 scene allowlist 中且在 `EAI_NVD_ASSET_ROOT` 下实际可见的资产，同时报告已注册但当前不可用的键；因此“注册”“本地存在”和“物理运行已验证”保持分离。机器人 manifest 覆盖 simulator catalog 中已接线的 14 个类型（包括 `simple_car`、`pepper`、`b2`、`mushr_v2`、`g1`、`iris`、`pegasus`、`lite3` 和 `coco`），但 controller、attachment、payload policy 仍必须按每个实验单元显式固定，不能从机器人名称推断额定载荷。

`robot_dossier.py` 从 EAI 的 14 个已接入机器人 catalog、schema profile、controller mapping 和 UR5/Z1 mount profile 生成 JSON-ready 档案。档案区分底盘形态与运动学、传感器挂载、机械臂组合、挂载质量缩放、场景适配、证据来源和未验证项。B2 与 Go2 是不同的普通四足配置；B2 使用独立的 actuator/资产路径选择，Go2 使用自己的 USD 和速度控制配置。M20 是轮足机器人，包含腿部关节和 wheel 关节组。`cf2x`、`iris`、`pegasus` 都声明当前 `aerial_scan` 位置目标适配，但带绝对偏航约束的任务仍只接受成对的 Iris/Pegasus 位置控制器；任务准入还必须匹配配置级、场景级协议证据。
可运行 `python tools/scenario_generation/export_capability_profiles.py --output <report.json>`
导出 14 型配置覆盖矩阵和雷达状态，或加 `--format html` 生成审核表。未测量轴保持
`unknown`，导出器不计算排名，也不将静态声明转成运行成功率。
能力画像 v2 还为每个机器人列出 3 个 Env DIY 机械臂变体的配置级挂载证据，以及单独的
Kinova Gen3 `candidate_only` 记录。最终 Env DIY 矩阵仍为 42 条；Kinova 不进入附件目录或
选择器。静止挂载 fixture、固定底座 UR5+Robotiq 组件 E7 和移动配置动态操作是三个不同
证据域：前两者都不能填充雷达轴或满足动态操作准入。Carter 的通过记录始终携带轮质量分布
偏乐观警告；额定 payload、实机抓取力、碰撞安全裕度、抗风和载荷动力学保持 `unknown`。
F-INS 的配置检测记录由源 attempt 的像素检测和固定逐视角融合重算，保留识别帧数、实际
marker ID 与融合定位误差；只有配置、世界、节点和 scope 完全一致时才能准入。导航证据不能
替代检测协议，二者都不会自动填充雷达轴。

`robot_dossier_schema.py` 提供 `eai.robot_dossier` 的 Draft 2020-12 schema。它动态从当前 catalog 生成 14 个合法 `robot_key`，并校验顶层版本、Claim 状态、E0-E7 evidence tier、validation gate 和 unknown Claim 规则。`combination_profile.py` 用于把机器人、机械臂、传感器和货物条件绑定到同一 payload policy hash 与 dossier snapshot hash；组件质量或 COM 未知时，组合质量/惯量保持 unknown。生成器 admission、batch pool 和 protocol freeze 会保存 `robot_dossier_snapshot_sha256` 与 `payload_policy_hash`；`robot_suitability.py` 的 `requires_manipulation=True` 同时要求挂载动态准入为 `allowed` 且存在同一移动配置的 E7 记录，因此静止 `conditional` 或固定底座组件 E7 都不能进入动态操作 benchmark；该模块仍不把 suitability 提升为 Isaac 或物理运行成功。

档案中的 `declared_capacity_kg` 始终为未知。实验 payload 必须通过 policy 显式提供；UR5/Z1 的 `mass_scale` 只是当前挂载模型的质量和控制参数缩放，不能解释为额定承载力。档案的证据等级目前是本地声明和资产配置，`runtime_validated` 默认为 false。生成 manifest 会把档案传给 LLM，帮助它按能力组合机器人，而不是根据机器人名称猜测载荷、传感器或室内/室外适用性。


## 机器人档案

`runtime_evidence.py` 把 runtime report 转成 dossier evidence。`import_settle_report()` 只接受带 scenario/dossier hash、entity drift 和 `spawn_validated` 状态的报告，产生 E5；`import_execution_attempt()` 要求 batch attempt 为 zero-return completed，并同时匹配 scenario、dossier 和 payload policy hash，产生 E6 navigation/kinematic evidence；`import_physics_measurements()` 只接受有限的 `PhysxMeasurementReader` body coverage，产生观测型 E5，不能自动升级 spawn 或 physical status。E7 必须由独立的 physical validation report 显式声明，当前 importer 不会从接触数据推断抓取成功。

`merge_runtime_evidence()` 只追加唯一 evidence，并按照 evidence 中明确的 validation status 单调更新 dossier gate；纯 PhysX observation 不会被当成 spawn 成功。`settle_smoke.py` 现在在运行开始冻结 scenario/dossier hash，运行结束检查输入是否漂移；`batch_validate.py` 只有通过 importer 的 settle report 才会写入 runtime evidence 并保留 `spawn_validated`。

`robot_static_config.py` 用 AST literal 提取 Go2、B2、M20 和 Lite3 配置中的 actuator groups。它保留 Go2 的 `23.5` effort、B2 的分组 limits、M20 的 leg `76.4` 与 wheel `21.6` limits 以及源码 hash；这些都是 E1 source declarations，和 EAI mount 的质量缩放、仿真执行以及物理承载能力分开。`robot_dossier.py` 会把可提取的 actuator snapshot 和 source hash 纳入对应档案。

`robot_dossier.py` 从 EAI 的 14 个已接入机器人 catalog、schema profile、controller mapping 和 UR5/Z1 mount profile 生成 JSON-ready 档案。档案区分底盘形态与运动学、传感器挂载、机械臂组合、挂载质量缩放、场景适配、证据来源和未验证项。B2 与 Go2 是不同的普通四足配置；B2 使用独立的 actuator/资产路径选择，Go2 使用自己的 USD 和速度控制配置。M20 是轮足机器人，包含腿部关节和 wheel 关节组。`cf2x`、`iris`、`pegasus` 都声明当前 `aerial_scan` 位置目标适配，但带绝对偏航约束的任务仍只接受成对的 Iris/Pegasus 位置控制器；任务准入还必须匹配配置级、场景级协议证据。

`robot_dossier_schema.py` 提供 `eai.robot_dossier` 的 Draft 2020-12 schema。它动态从当前 catalog 生成 14 个合法 `robot_key`，并校验顶层版本、Claim 状态、E0-E7 evidence tier、validation gate 和 unknown Claim 规则。`combination_profile.py` 用于把机器人、机械臂、传感器和货物条件绑定到同一 payload policy hash 与 dossier snapshot hash；组件质量或 COM 未知时，组合质量/惯量保持 unknown。生成器 admission、batch pool、protocol freeze 和机械臂部署矩阵会保存 `robot_dossier_snapshot_sha256` 与 `payload_policy_hash`；`robot_suitability.py` 对操作任务还要求底盘物性、挂载后聚合量、安装几何和抓取证据均已闭合，并要求具体机械臂部署状态为 `allowed`，不把 conditional/eligible 提升为 Isaac 或物理运行成功。

真实装配数据填写模板为 `physical_mount_calibration_template.json`。模板中的 `null` 必须在获得型号匹配的厂商资料或实测记录前保持为空；仿真导出的质量、COM、惯量和静态接触结果不能替代这些字段。只有补齐安装件、线缆、支撑几何、动态测试和抓取测量后，才能把 `status` 从 `incomplete` 推进到相应证据等级。

档案中的 `declared_capacity_kg` 始终为未知。实验 payload 必须通过 policy 显式提供；UR5/Z1 的 `mass_scale` 只是当前挂载模型的质量和控制参数缩放，不能解释为额定承载力。档案的证据等级目前是本地声明和资产配置，`runtime_validated` 默认为 false。生成 manifest 会把档案传给 LLM，帮助它按能力组合机器人，而不是根据机器人名称猜测载荷、传感器或室内/室外适用性。


- **Factory：** `cf2x` 先建立封锁区与货物可见性信息；`m20` 与 `carter` 分别从两侧接近；两者联合搬运超单机容量的重件到中转区；随后由两个地面机器人分段推进、返回确认通道、完成末端放置和状态复核。
- **Warehouse NVD：** `cf2x` 扫描货架、叉车遮挡和可通行 aisle；`m20`、`carter` 依据扫描结果进入木箱两侧；两机联合将超容量木箱移到 pallet staging；之后经过 dock 路线确认、推进、放置和库存复核。
- **Modern House NVD：** `cf2x` 侦察房间、楼梯、狭窄通道与目标可见性；`m20`、`carter` 依据侦察结果从家具两侧进入；两机联合移动家具或堆叠物到 staging；再经过最终房间入口确认、定位、稳定放置和房间复核。

`STRONG_TASK_BLUEPRINTS` 和 `validate_long_horizon_task()` 位于 `benchmark_task_design.py`。已有的 `heavy_wh_joint_s17`、`combo_isr_heavy_s23` 和 `isr_mh_cf2x_s17` 可以作为能力门禁通过的 seed，但按正式长流程门禁，它们目前都应被视为短流程候选，不能直接作为最终强异构 release 实例；生成器会要求 LLM 将它们扩展成上述完整流程。

## 完美检验流程

正式审核按以下顺序执行，每一层都应保留输入场景 hash、场景模板/map hash、payload policy hash、代码版本和结构化证书：

1. **Intake/schema：** strict schema、场景一致性、引用、节点分配、DAG、支持的 skill；`joint_transport` 必须绑定两名不同机器人。
2. **Geometry/connectivity：** 所有机器人、实体、目标必须有实际映射；检查 footprint、边界、静态障碍、各层和运输路径。当前 2D/kinematic Oracle 不等价于 3D 碰撞、IK 或接触证明。
3. **Capability/payload：** payload policy 必须覆盖所有参与类型且容量有限；计算单机反事实、联合容量、抓取尺寸/质量和 attachment capability。
4. **Strong team flow：** 检查 8 节点、5 个 substantive 节点、关键路径深度 5、`cf2x` 信息节点、两机 joint team、角色分离和 scan 到后续阶段的因果依赖；删除任一角色或 scan 信息时应失败。
5. **Executor preflight：** 冻结 seed、分配、mode、timeout 和 expected events；当前不支持的 `handoff_object`/`wait` 只能被拒绝，不能靠名称或 prerequisite 冒充执行语义。
6. **Rollout/evidence：** settle 和 rollout 报告必须匹配场景 hash、policy、map/template、实体句柄和 return code；事件顺序、唯一性、团队 ownership、节点完成条件由独立 verifier 检查。executor 自己产生的 event 只能作为原始记录。
7. **Protocol：** 只统计 immutable、hash 匹配且达到声明 tier 的 evidence。kinematic transport 只能报告为理想化/运动学层；没有真实 grasp、IK、接触动力学、载荷动力学或 sensor observation 证据时，不得宣称 physical benchmark success。


校验与几何检查需要 `jsonschema`、`numpy`、`Pillow` 和 `PyYAML`；生成额外需要 `openai` 和已配置的 `OPENAI_API_KEY`。Factory 占据图遵循标准资产 resolver 环境变量，首次使用时可能下载。

## 聚焦测试

```bash
PYTHONPATH=source/EAI:source/EAI_assets \
  python -m pytest -q source/EAI/test/test_scenario_spec.py source/EAI/test/test_scenario_family.py source/EAI/test/test_scenario_oracle.py
```

## 实例池

`batch_validate.py` 把多 seed 实例族做成带标签的实例池。每个实例先过离线门禁栈（`placement_validated`），再逐个跑 headless Isaac 沉降子进程（`spawn_validated`，零指令漂移检查）。池目录保存实例文件、逐实例沉降报告、族记录和 `pool.json` 清单；重跑时已带 spawn_validated 标签的实例自动跳过。

```bash
# 干跑：只生成 + 离线门禁
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/trn01_first --skeleton TRN-01 --arm ur5 \
  --seeds 17 23 31 --dry-run

# 只接受反事实最小团队规模大于 1 的实例；现有 TRN-03 会被诚实拒绝。
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/team_only --skeleton TRN-03 --arm ur5 \
  --seeds 17 23 31 --dry-run --require-team-necessary

# 正式：逐实例 headless 沉降（CPU 物理约每实例 10 分钟）
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/trn01_first --skeleton TRN-01 --arm ur5 \
  --seeds 17 23 31 --steps 50 --settle-timeout 1200 --device cpu
```

`--arm ur5` 选择 TRN-01 的 m20+UR5 载臂变体（默认 `--arm z1` 配方需要 Z1 控制器包，离线时可能未缓存）。TRN-01 现在明确是单载具质量基线，不是协作任务。池标签会区分 `single_robot_feasible` 与 `team_necessary`；`--require-team-necessary` 会拒绝前者。可用完整的 `--payload-policy` JSON/文件启用质量门禁；一旦启用，策略中缺失的机器人类型按不可用处理，绝不视为无限载荷。

### NVIDIA SimReady 道具（可选）

注册表可吸收 NVIDIA SimReady 道具库（NVD 下载）。审计、构建 tracked 注册表，并通过 `EAI_NVD_ASSET_ROOT` 暴露：

```bash
python tools/scenario_generation/audit_nvd_props.py \
  --roots <NVD_assets>/SimReady_Warehouse_01_NVD@10010 \
  --output tmp/nvd_audit/simready_props.json
python tools/scenario_generation/build_prop_registry.py \
  --audit tmp/nvd_audit/simready_props.json \
  --nvd-root <NVD_assets>
EAI_NVD_ASSET_ROOT=<NVD_assets_sanitized> python simulator.py --scenario-file <scenario.json>
```

注意：SimReady 道具只带视觉网格（盘上无物理）——scenario builder 在 spawn 时注入刚体状态与注册表建议质量；这类网格的 dynamic（刚体）注入当前会使 Kit 崩溃，请以 `"mode": "visual"` 使用。USD 把 `@` 当路径定界符，需建 sanitized shadow 根（包目录名 `@` 改 `_v`）并把 `EAI_NVD_ASSET_ROOT` 指向它；路径必须全 ASCII（含非 ASCII 的下载路径会使 USD 文件解析失败）。


## Phase-Graph 执行器

`execute_episode.py` 用确定性航点控制驱动已显式分配机器人的 `navigate`
阶段：可行性 Oracle 的 A* 路线配合比例航向/速度控制（差速与速度 RL
控制器共享 `[v, 0, wz]` 命令形态），运行时求值器记录谓词轨迹及首满足步。

`transport_object` 必须显式选择 `--transport-mode kinematic`。该模式把物体
根位姿写到载具上方，只验证控制闭环和逻辑目标状态；**不**验证抓取、接触、
机械臂 IK、机械臂运动、载荷上限，也不会让 8 kg 与 30 kg 在执行中产生差异。
报告会明确写出 `mass_affects_execution: false` 与
`manipulation_model: scripted_root_pose`。正常接触被允许；只有长时间没有完成当前航点且重规划
次数耗尽时，有界看门狗才报告卡死。成功报告称为
`navigation_execution_validated` 或 `kinematic_execution_validated`，不会称作
`oracle_validated`。

```bash
python tools/scenario_generation/execute_episode.py <仅导航场景.json> --report /tmp/exec.json

# 显式选择的非物理搬运执行。
python tools/scenario_generation/execute_episode.py <搬运场景.json> \
  --transport-mode kinematic --report /tmp/exec.json

# 调节无进展看门狗的耐心度（接触仍被允许）。
python tools/scenario_generation/execute_episode.py <导航场景.json> \
  --stall-window 150 --max-replans 12 --report /tmp/exec.json
```

默认自动步数预算会为每个允许的恢复周期额外预留一个完整的
`stall-window`，使持续卡死先被诊断为 `deadlocked`，而不会被外层步数上限
掩盖成普通 `steps_exhausted`。显式传入 `--steps` 会有意覆盖该预留。

执行器会拒绝未分配、尚不支持或只能部分执行的 DAG，不会只对其中已支持的
前缀计分。恢复重规划与原始可行性检查使用相同的 static/kinematic 障碍图。

### 保留执行证据

`batch_execute.py` 默认只执行带 `spawn_validated` 标签的池记录，并把输入
SHA-256、执行器配置、报告路径/内容、stdout/stderr 尾部和每一次失败写入
`pool.json`。新增标签还要求零退出成功、未缩放质量审计及完整运行证据绑定。
`demo/benchmark/provenance.py` 在启动前冻结请求，在控制前冻结本地 USD/材质/纹理、
控制器资产、已加载源码、地图和实际物理/控制/渲染周期；结束时检查文件变化。
每次重试使用独立报告及 request/runtime 快照，报告摘要参与校验，不事后补写旧结果的源码指纹。
`--render-period-s` 与 `--wall-timeout-s` 会传给执行器并参与请求指纹；后者只限制控制循环，
外层 `--timeout` 仍覆盖整个子进程。不同条件不能复用为同一次成功。
E6 importer 要求本地文件仍与快照一致；新协议冻结源码，汇总拒绝缺失或不一致的证据。
运行时 MDL 模块和驱动二进制未做内容指纹，这不是完整状态重放证书。

比较方法前，应先用 `benchmark_protocol.py freeze` 冻结协议划分：它会把配对
条件和重复物理布局留在同一侧，`summarize` 会拒绝把旧结果继承给已变更的输入。
这只是 Factory 场景内按 family 的保留集，并非跨场景泛化划分；详见
[科学范围](SCIENTIFIC_SCOPE.zh-CN.md)。
若冻结实例包含当前执行器不能支持的 phase skill，批量记录会明确标为
`not_executable_by_current_harness`；它不被当成 Isaac 运行失败，也不进入运行时
覆盖率的分母。

```bash
# 执行池内所有已沉降的纯导航实例。
python tools/scenario_generation/batch_execute.py \
  --pool tmp/validated_pool/factory --device cpu

# 执行一个显式声明为非物理的搬运实例。
python tools/scenario_generation/batch_execute.py \
  --pool tmp/validated_pool/factory --ids trn01_s17_ur5_low \
  --transport-mode kinematic --device cpu

# 冻结研究应优先使用：批量执行器只选择并校验已声明的开发集哈希。
# 只有最终盲测时才明确使用 --split test。
python tools/scenario_generation/batch_execute.py \
  --pool tmp/validated_pool/factory --protocol tmp/validated_pool/factory/protocol_v2.json \
  --split development --transport-mode auto --device cpu
```

Scenario V2 benchmark 现在强制保留机械臂原始 USD 质量和惯量，独立于普通
simulator/Env DIY 的质量缩放配置。settle 和执行报告逐连杆核对 PhysX 值；
新冻结协议不接受缺少该核对的历史执行证据。请重新运行旧的缩放质量实验。
未缩放 USD 仍需与具体厂商型号、夹爪、驱动和惯量实测数据对齐。

新增 [物理标定说明](PHYSICAL_CALIBRATION.md)：含厂商规格来源、Z1 未缩放接触抓取
试验、质量/夹持力/碰撞间隙/稳态风载筛选和独立证据验收。独立 Z1 夹爪试验的 E7
只覆盖固定基座、预放置物体和实际通过的质量点，不能给三场景任务或载臂机器人
整体升级到 E7。真实夹持力、抗风能力和完整移动搬运仍须对应的标定与任务试验。
