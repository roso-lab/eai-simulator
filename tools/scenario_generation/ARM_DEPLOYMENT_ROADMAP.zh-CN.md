# 机械臂真实物理部署升级路线

目标是让 Env DIY 的机械臂组合由“可装配”升级为“在声明的质量、重心、惯量、安装几何和控制限制下可解释地装配”。任何缺少可靠质量或惯量来源的候选都保持 `unknown`，不会用默认值伪造真实状态。

## TODO

### 阶段 1：物理真值和来源审计（进行中）

- [x] 审计 UR5、Z1、Robotiq 2F-85 的运行时质量与惯量。
- [x] 禁止 benchmark 机械臂质量、惯量和几何缩放。
- [x] 为旧执行报告增加当前质量审计门槛。
- [x] 记录厂商额定值与仿真资产值的差异，禁止自动把厂商额定值填入仿真。
- [ ] 为每个 Env DIY 可选机器人补齐底盘真实型号、重心、惯量和安装附件来源。
- [x] 对缺少实测参数的 USD 建立 `unknown` 状态和用户可见阻断原因。
- [x] 提供 `physical_mount_calibration_template.json` 与纯 Python 校验器，拒绝缺失、非法质量和非物理惯量记录。

阶段出口：每个组合都有 `measured`、`derived`、`declared`、`sim_tuning_only` 或 `unknown` 的逐字段 provenance；没有完整来源就不能进入“真实物理”组合池。

底盘 USD 审计快照（`tools/scenario_generation/audit_usd_physics.py`）：B2 `74.5803 kg / 37` 刚体、Go2 `15.0190 kg / 19` 刚体、M20 `34.4914 kg / 17` 刚体的质量字段完整，但 COM 与惯量尚未完成实测或逐字段来源闭合；Scout `52.0000 kg / 7` 刚体和 Lite3 `11.9376 kg / 17` 刚体仍有缺失字段，因此保持 `unknown`。这些值属于 USD authored declaration，不是实机测量。

2026-09-16 更正：v1 静态审计把关于原点的惯量用于关于质心的合成，并错误使用 USD 行向量旋转矩阵；相同的参考系标签也不能代替安装变换。v1 惯量和 B2＋UR5 静态合成结果撤回，不得用于准入。v2 使用 SI 单位、time-zero stage 坐标和关于质心的惯量，并复用 `combine_components()`。组合器必须显式接收完整安装旋转和平移，拒绝 v1 报告。新报告位于 `tmp/physics_audit_v2/`。

v2 资产审计程序确认：Scout/Lite3 的质量总和可从 authored mass 得到；COM 或主惯性轴不完整时，仅对应聚合量保持未知，不再错误声称它们缺少质量。B2/Go2/M20 的聚合量是指定 USD 默认姿态的 `derived` 数据，不是运行时站姿或实机参数。dossier 中没有绑定版本、坐标和资产证据的硬编码数值已移除，纯 catalog dossier 保持未知；静态报告独立保留来源 layer hash。

按 B2 和裸 UR5 的实际 base-link 锚点、180° 安装旋转及 `0.085 m` 偏移计算，v2 默认 USD 姿态组合质量为 `95.5742 kg`，COM 为 `[-0.04674, -0.01350, 0.01534] m`（B2 asset stage 坐标）。结果位于 `tmp/physics_audit_v2/b2_ur5_bare_time0.json`，不含 Robotiq，不代表默认带夹爪运行时配置或承载通过。

### 阶段 2：UR5 默认夹爪（进行中）

- [x] 将 Robotiq 2F-85 作为 UR5 配置的默认末端工具，而不是只在验证脚本中动态挂载。
- [x] 把夹爪资产、质量分配、惯量假设、固定关节和内部碰撞过滤纳入 UR5 的统一资产依赖解析。
- [x] 让 UR5 控制器默认暴露夹爪 DOF；没有夹爪的 UR5 作为明确的 `ur5_bare` 变体保留。
- [x] 增加 UR5 bare、UR5＋2F-85、开爪、闭爪和负载质量回归。
- [x] 用外部 Robotiq USD 检查 UR5 的 Isaac 初始化、DOF 注册和质量审计路径。
- [ ] 重新检查所有旧 UR5 场景的桌面、碰撞、IK 初始姿态和质量审计。

当前默认 UR5 挂载已在 builder callback 中装配 Robotiq 2F-85，并将夹爪质量纳入挂载质量缩放；benchmark 路径强制使用 `mass_scale=1.0`。外部 Robotiq USD 的 Isaac 初始化、DOF 注册和质量审计路径已通过；旧场景逐项回归仍未完成。

阶段出口：默认 UR5 包含夹爪并通过统一质量审计；所有没有夹爪的旧场景显式选择 `ur5_bare`，不能因隐式 fallback 获得不同物理模型。

### 阶段 3：Env DIY 组合可行性筛选（进行中）

- [x] 修复挂载根部参数未应用、挂载根部 warm-up 位姿复用、Scout 出生穿地和 Scout 桌体相交问题。
- [x] B2 静止承载 fixture 使用独立有界站姿控制，并保留原行走控制器参数。
- [x] 对 Scout、B2 的默认 UR5＋Robotiq 组合记录运行时总质量、COM 和关于系统质心的惯量，保存原始质量属性及五个关键帧进行独立复算。
- [x] 修复 CPU 接触报告初始化，记录逐轮/足对地过滤力、持续支撑比例及反力/重量一致性。
- [x] 在同一 v2 检查器下完成 Scout、B2、Go2、M20、Lite3 的空载与 UR5 装载对照。
- [x] 记录实际承力接触点、瞬时支撑凸包及原始接触缓冲区/力矩阵复算误差；缺失或负裕度保留。
- [ ] 建立每个底盘的静态组合分析：总质量、COM、安装高度、轮/足支撑包络和几何间隙。
- [ ] 建立动态筛选：关节力/速度限制、底盘驱动余量、固定关节反力、碰撞接触和指定加速度。
- [x] 对每个 `robot × arm × tool` 输出 `allowed`、`conditional`、`rejected` 或 `unknown`，并记录具体原因。
- [x] 在 benchmark admission 中执行动态操作适配门禁；没有 `allowed` 部署证据的 `conditional`/`unknown` 组合不得进入动态操作池。
- [x] 在 Env DIY UI 中阻止 rejected/unknown 组合运行，允许用户查看缺失参数和验证命令。
- [x] 将机械狗上的 UR5 组合在未观测到真实地面接触时标为 unknown，并阻止运行。

阶段出口：选择器不会因为“厂家宣称底盘 payload 足够”就允许高重心长悬臂；也不会因为一次静止仿真通过就声称可行走操作。

2026-09-16 运行时组合审计：`tmp/runtime_inertia_scout_ur5_20260916a/report.json` 和 `tmp/runtime_inertia_b2_ur5_20260916a/report.json` 各完成 3600 步、静止摆臂通过。总质量分别为 `73.9189017 kg` 和 `96.4992000 kg`，包含默认 Robotiq。每个样本记录 `simulation_derived` COM/惯量，惯量关于系统质心并表达在仿真 world 坐标系。两个目录中的 `independent_crosscheck.json` 记录五个关键帧的独立复算及源文件 hash；最大惯量绝对差分别为 `8.89e-16` 和 `3.53e-10 kg m²`。原始 PhysX 张量保留，独立参考计算仅消除微小非对称浮点误差。没有修改任何质量、惯量或控制参数；这不建立硬件参数真实性或实际接地支撑多边形。

### 阶段 4：候选机械臂和灵巧手（进行中）

- [x] 将 Gen3 零质量末端帧转换统一到固定底座和移动挂载路径，绑定源 URDF，保留全部承重链接参数。
- [x] 修复 Gen3 移动 articulation root、world joint 禁用和宿主旋转；拒绝固定到世界的移动候选。
- [x] 修复固定底座“零驱动”仍保留 PD 的问题，并直接读取 PhysX 驱动参数验证。
- [x] 重做 Gen3＋Scout/B2 的 3600 步静止摆臂、持续接地和实际接触凸包检查，并核对空载对照资产/驱动一致性。
- [x] 完成 Gen3 同坐标系完整惯量审计，查明缺失有效主轴和惯量积，按 URDF 恢复并核对固定底座/移动运行时张量。
- [ ] 闭合 Gen3 安装附件、工具、实际硬件变体及驱动标定来源。
- [ ] 完成 Dex5 动作语义、接触抓取及挂载验证；补齐 Hand-E 物性来源。
- [x] 审计 Allegro Hand 本地 URDF：21 个刚体、20 个关节（16 个可动）、质量约 0.9549 kg；发现 13 个链接惯量违反物理正定/三角约束。
- [ ] 更换或修正有来源的 Allegro 惯量，锁定不可变源版本并制作 Isaac USD、控制器和挂载接触验证；当前保持 `invalid_source_physics`。
- [x] 对 Dex5 USD 完成 SI 单位、21 个刚体、COM、主轴和惯量字段静态审计；结果为 `declared_complete`，质量约 0.97857 kg。
- [x] 对照 Unitree 官方 Dex5-1 页面：厂商声明重量 1.1 kg，与本地 USD 相差约 0.12143 kg；差值来源未闭合，不将 USD 标记为实机等价。
- [ ] 完成 Dex5 的手部安装转接件、动作语义、控制器和移动宿主验证；当前不得从固定底座烟测推导可安装性。

候选按本地资产和参数可追溯性排序：

1. Kinova Gen3：本地有带惯性参数的 URDF 和 Robotiq 夹爪描述，优先做质量/惯量/关节限制导入。
2. Unitree Dex5：本地有 Isaac USD 配置，优先确认每个刚体质量、惯量、关节驱动和碰撞是否完整。
3. Robotiq Hand-E：作为工业夹爪候选，先验证资产质量、固定安装和控制 DOF。
4. ePick：本地有 ROS2 描述，但需要独立的物理质量、接触和控制模型，不能只因有 ROS 包就纳入候选。

每个候选都必须在独立分支完成：资产来源审计 → 固定底座接触/驱动测试 → 未缩放质量测试 → 裸底盘对照 → 组合静态筛选 → 指定底盘 headless 验证。没有通过这些阶段的候选只进入候选清单，不进入 Env DIY 默认选项。

当前进度：Kinova Gen3 已完成 Scout、B2 的未缩放质量静止挂载和摆臂测试；候选 USD 的源世界关节冲突已在装配前移除。Kinova 仍保持 candidate-only，不进入 Env DIY 默认选项，直到完成安装几何、行走和末端工具证据。

准入规则补充：任何要求机械臂操作的 dossier 现在还必须同时提供底盘质量、重心、惯量、支撑几何、挂载后聚合 COM/惯量和安装几何的物理来源或验证状态；每个机械臂挂载还必须声明安装位置并闭合抓取物理证据。任一字段为 `unknown`、`sim_tuning_only` 或未验证时，结果保持 `ineligible`，不能仅凭静止摆臂或目录兼容性进入动态 benchmark。

2026-09-16 接口修复后的重测见 `tmp/kinova_interface_v2_20260916/`。Kinova 裸臂运行时约 8.0519 kg、8 个承重刚体、7 DOF；Scout/B2 整机分别约 60.0519/82.6322 kg，均通过静止摆臂与 Ground 过滤反力检查。稳定阶段反力均值分别为 589.0944/810.6390 N，对应重量 589.1092/810.6219 N。Scout 的实际接触凸包最小值 -0.005017 m、36 个退化和 41 个负值样本；B2 最小值 +0.178351 m、无退化或负值（各 2880 样本）。所以较轻机械臂也没有消除 Scout 的接触包络问题，不能将负值单独归因于 UR5 总质量。

`crosscheck_corrected.json` 核对复用的空载报告：底盘引用层 hash、质量数组和驱动配置相同。对八个承重链接，运行时质量与源 USD 的最大差为 5.96e-8 kg。旧 `crosscheck.json` 的完整惯量比较撤回：源 USD 主轴为零四元数，不能当作单位旋转；完整张量必须保持 unknown。后续审计确认八个连杆的运行时惯量与 URDF 均不符，见 `tmp/kinova_tensor_v2_20260916/before_scout.json`，必须恢复完整张量后重测。空末端原有 1e-7 kg 占位刚体按明确零质量 URDF 改为坐标帧，不是缩小承重部件质量。固定底座零驱动/PD 各完成 720 步，125 项相关轻量回归通过。

完整惯量恢复后已在 `tmp/kinova_tensor_v2_20260916/` 重测：质量数组、底盘驱动、机械臂力矩上限、源资产引用层 hash 与前一轮一致。八个连杆对完整 URDF 的最大张量元素误差由 0.000691 降至约 8.17e-9 kg m²；这是带明确来源的运行时覆盖，不改写源资产。Scout/B2 静止摆臂和反力门禁再次通过；Scout 最小实际接触凸包裕度 -0.004907 m、51 个退化和 60 个负样本，B2 最小 +0.179157 m、无退化/负样本。全部机械臂仍为 8.0519 kg，140 项相关回归通过。原 USD 主轴未知、指定硬件标定、工具附件和行走操作证据仍不齐全，候选不开放默认准入。

Kinova 旧静止报告记录了轮/足链接位置投影包络的几何裕度：Scout 最小 `0.2243 m`、B2 最小 `0.1777 m`。这些点未经对地接触筛选，不能称为实际支撑多边形。候选型号、质量口径和接口链接问题见 [Gen3 参数核对](KINOVA_GEN3_DEPLOYMENT_GAPS.zh-CN.md)。

### 阶段 5：证据和发布门禁（待实现）

- [x] 将组件物理校准报告绑定到报告内容、runner、manifest 和 validation 文件 hash，并提供统一索引导出器。
- [ ] 将组合验证报告绑定到场景、资产、控制器、参数和源文件 hash。
- [ ] 区分组件级物理接触证据、静止移动平台证据、行走操作证据和实机证据。
- [x] UR5 + Robotiq 2F-85 已完成固定底座 `0.2 kg`、`1.0 kg` 和 `2.0 kg` 的各 3 次闭爪与 3 次开爪对照，生成组件级 E7 汇总；不推广到移动平台或硬件。
- [x] 明确禁止从仿真报告推出真实额定 payload、实机抓取力、抗风能力、碰撞安全裕度和载荷动力学结论。
- [x] 为每个默认 Env DIY 组合生成可审阅的 compatibility matrix。

矩阵导出命令：`python tools/scenario_generation/export_arm_deployment_matrix.py --output tmp/arm_deployment_matrix.json`。矩阵直接读取 `catalog.py` 的部署状态，不从质量或一次性仿真结果推导额定承载能力。

组件物理证据索引命令：`python tools/scenario_generation/export_physical_evidence_index.py --directory tmp/physical_calibration --output tmp/physical_calibration/evidence_index.json`。索引只报告 manifest/validation 链条完整的组件证据；hash 一致性不等于测量认证。当前目录中只有 2 份记录通过完整链条，不能据此生成 UR5 移动挂载或硬件 E7 结论。

准入矩阵复核（2026-09-16）：当前 42 个 `robot × manipulator` 条目中没有 `allowed`，7 个为 `conditional`，6 个为 `rejected`，29 个为 `unknown`。因此所有要求动态机械臂操作的 benchmark 候选都会被 admission gate 拒绝，直到获得配置专属的动态证据。

UR5 组件 E7 证据：`tmp/ur5_e7_component_summary.json`，由 `0.2 kg`、`1.0 kg`、`2.0 kg` 和 `3.0 kg` 四组各 3 次闭爪/3 次开爪报告汇总。试验使用未缩放质量、真实重力/碰撞和非焊接 payload；通过质量仅为离散测试点 `0.2 kg`、`1.0 kg`、`2.0 kg`，`3.0 kg` 在该 fixture 下未通过。这个失败点不是额定承载上限，也不能外推到其他姿态、工具、摩擦、控制参数或硬件；证据只证明固定底座组件 fixture 的接触抓取、抬升、搬移和释放，不证明 UR5 额定 payload、Robotiq 实机夹持力、移动底盘稳定性、抗风、碰撞安全裕度或载荷动力学。
其中 `3.0 kg` 的三次闭爪均未抬升 payload，双侧接触、无桌面支撑和搬移检查失败；三次开爪负对照均正确拒绝运动。该失效模式记录为 fixture 观察，不解释为额定承载边界。

## 当前已知状态

当前已完成的验证是：未缩放 UR5＋Robotiq 2F-85 的固定底座和 Scout 静止抓放；未缩放 Z1 在 Scout、Go2、B2 的静止摆臂；B2 的空载和 Z1 挂载静止站姿；Kinova Gen3 在 Scout、B2 的未缩放静止摆臂。Kinova Gen3、Dex5 和 Hand-E 已完成 Isaac 结构/rollout 烟测，其中 Hand-E 因缺失质量字段保持 `unknown`。UR5 默认夹爪和 `ur5_bare` 已接入 builder、GUI、资产依赖和回归测试。

### 未缩放 UR5 挂载矩阵（2026-09-16，trial v2）

组合均为 UR5＋Robotiq 2F-85，质量缩放为 1.0。质量是运行时仿真资产总和，不是实测整机质量。

| 宿主 | 空载 / 装载总质量 kg | 空载 | 装臂 | 稳定阶段关键观测 | 当前结论 |
|---|---:|---|---|---|---|
| Scout | 52.0000 / 73.9189 | pass | pass | 平均对地竖直反力 725.138 N，重量 725.144 N | `conditional`：仅指定静止 fixture |
| B2 | 74.5803 / 96.4992 | pass | pass | 平均对地竖直反力 946.643 N，重量 946.657 N | `conditional`：使用静止专用 PD，未验证默认行走控制器 |
| Go2 | 15.0190 / 36.9379 | pass | fail | 装臂倾角 90.152°，足端平均竖直反力仅为总重量 2.24% | `rejected`：当前挂载和控制组合失败 |
| M20 | 34.4914 / 56.4103 | pass | fail | 装臂仍接地，但倾角 24.527°，超出当前静止阈值 | `rejected`：接地不等于姿态稳定 |
| Lite3 | 11.9376 / 33.8565 | fail | fail | 空载倾角 18.292°、漂移 0.2265 m；装臂也失稳 | `rejected`：空载基线不成立，不能归因为承载不足 |

报告位于 `tmp/ground_reporting_v2_20260916/`，包含五种底盘的空载/装臂对照和 Scout 接触报告开关对照。Scout/B2 装臂稳定阶段 100% 样本满足支撑阈值。门禁使用 Ground 过滤力矩阵，要求至少 99% 的稳定阶段样本承担至少一半系统重量，平均竖直反力与重量误差不超过 10%；这些只是明确的 fixture 检查容差，不是实机安全裕度。不能从该矩阵推导实机额定 payload 或“一切机械狗都不可能装 UR5”。

接触报告根因更正：本机 Isaac Lab 的 `SimulationContext` 将 `/physics/disableContactProcessing` 设为 true，而 `ContactSensor` 构造时设为 false。此前直接创建 raw tensor view 没有开启 CPU 接触报告；零力/零接触缓冲区不能证明悬空、碰撞体缺失或承载不足。开关对照中资产、质量、位置、速度和关节轨迹完全相同，只有报告从有效反力变为零，见 `reporting_ablation.json`。因此撤销以零报告为依据的出生高度、碰撞强制开启和 purpose 修改；Scout 保留对照前的 0.24 m 出生高度。`get_contact_data()` 与力矩阵共享报告通道，不是独立接触事件证据。

实际接触点追加验证：`*_contacts/report.json` 分别验证 Scout/B2 的 `ur5` 与 `ur5_bare`。四组均通过姿态与平均反力门禁。B2 带夹爪/裸臂的实际接触凸包最小裕度分别为 0.15306/0.16092 m，稳定阶段无缺失或负值；Scout 带夹爪/裸臂则分别出现 105/49 个退化凸包样本，122/73 个负裕度样本（各 2880 个稳定阶段样本），最小值 -0.01720/-0.01478 m。因此 Scout 的 `conditional` 不包含正倾覆裕度承诺。原始接触力乘法向求和与过滤力矩阵的最大分量差不超过 0.0000611 N。这是仿真量内部一致性校验，不是独立硬件测量。

从报告样本计算的轮/足链接位置投影包络裕度：Scout `0.1936 m`（最小）、B2 `0.1531 m`（最小）、Go2 `-0.4424 m`（最小）；M20 和 Lite3 没有有效包络样本。这些点未经接地接触力筛选，负值只表示越过链接投影包络，不能当作真实支撑边界或直接解释失稳原因。

### Z1 宿主准入状态

| 宿主 | 证据状态 | 允许原因 |
|---|---|---|
| Scout | `conditional` | 已完成未缩放静止摆臂测试；未验证行走中操作 |
| Go2 | `conditional` | 已完成未缩放静止摆臂测试；未验证行走中操作 |
| B2 | `conditional` | 已完成未缩放静止摆臂测试；未验证行走中操作 |
| M20 | `unknown` | 缺少宿主特定的未缩放稳定性证据 |
| Lite3 | `unknown` | 缺少宿主特定的未缩放稳定性证据 |

`unknown` 组合由 `source/EAI/EAI/hmrs_env/env_diy/catalog.py` 阻断，不会进入 Env DIY 运行阶段。

### 2026-09-16 补充批次：carter/m20×Z1 与 Gen3 扩展宿主矩阵

`tmp/mount_validation_20260916/` 使用与 v2 相同的 `mobile_arm_mount_smoke.py` 门禁（3600 步静止摆臂、Ground 过滤反力 ≥99% 样本承重 ≥50% 重量、倾角 <0.15 rad、漂移 <0.10 m），完成 10 组 headless CPU 对照试验：

| 宿主 | 机械臂 | 门禁 | 关键观测 | 结论 |
|---|---|---|---|---|
| carter | 裸 | pass | 倾角 0.002 rad，反力 536.6 N ≈ 重量 | 基线成立 |
| carter | Z1 | pass | 倾角 0.002，反力 598.6 N ≈ (54+5.2) kg 重量 | `conditional`（新增） |
| m20 | 裸 | pass | 倾角 0.001 | 基线成立 |
| m20 | Z1 | pass | 倾角 0.001，反力 392.6 N | `conditional`（由 unknown 升级） |
| m20 | Gen3 | pass | 倾角 0.018，漂移 0.031，反力 427.7 N ≈ (34.5+8.1) kg 重量 | `conditional`（新宿主） |
| go2 | 裸 | pass | 倾角 0.030，漂移 0.074（接近阈值） | 基线成立但余量小 |
| go2 | Gen3 | fail | 倾角 0.350 rad、漂移 0.234 m，持续支撑失败 | `rejected`：裸底盘通过，失稳归因 8.1 kg 载荷 |
| lite3 | 裸 | fail | 倾角 0.319 rad、漂移 0.226 m | fixture 基线不成立（与 UR5 矩阵结论一致） |
| lite3 | Z1 | fail | 倾角 1.656 rad（倾覆） | `rejected`（fixture 混杂） |
| lite3 | Gen3 | fail | 倾角 1.551 rad（倾覆） | `rejected`（fixture 混杂） |

同日 Kinova Gen3 结束 candidate-only：资产装入 `usd/payloads/manipulators/kinova_gen3/`（USD+7dof URDF，URDF 惯量运行时覆写保留），`kinova_gen3` 进入 Env DIY 机械臂目录（catalog/builder/flow/接口 YAML/终端与 GUI 卡片），控制器为通用 `ManipulatorIkControllerCfg` 的 `KINOVA_GEN3_IK_CFG`，ROS 话题 `/<robot>/kinova_gen3/{target_pose,joint_command,joint_states,ee_pose}`，无夹爪通道。`go2+kinova_gen3+orsus` 与既有 UR5/Z1 冲突规则一致。Gen3 卡片缩略图为 URDF 零位真实运动学示意图（正式渲染图待补）。这些仍是静止 fixture 证据：不建立行走操作、额定载荷或实机等价性。

### 2026-09-16 第二批：carter 质量审计、carter×UR5/Gen3、lite3 差分归因

**carter 质量审计**（逐刚体 USD authored 值）：总量 54.00 kg 中两个驱动轮各 20 kg（合计 74%），底盘 `Orsus_chassis_link` 仅 6 kg，脚轮组 8 kg，dummy 链接≈0。该尺寸驱动轮的真实质量量级为 1–3 kg，40 kg 轮质量属于仿真稳定性调参写法，会把系统 COM 压到最低点。因此 carter 上的一切"通过"结论相对任何真实质量分布都偏乐观；2024 款 Nova Carter（Segway RMP Lite 220 平台）官方约 48–51 kg，但本 USD 为老款 Carter 几何+EAI 定制底盘，两者不能互相印证。carter 准入理由中已注明该声明值分布警告。

**carter 挂载测试**（挂点与 Z1 相同：`Carter/Orsus_chassis_link`、z=0.414）：

| 组合 | 门禁 | 关键观测 | 结论 |
|---|---|---|---|
| carter | 裸 | pass | 倾角 0.002 rad |
| carter + Gen3 | pass | 倾角 0.002、漂移 0.002 m、反力 657.5 N≈重量 608.7 N | `conditional`（含质量分布警告） |
| carter + UR5(±Robotiq) | fail | 倾角峰值仅 0.038–0.075（非倾覆型），但 0.25 s 沉降瞬态内跌落 0.19 m、倾至 0.30 rad 后被弹开，漂移 1.8 m（默认臂位姿 38 m）；臂全程未跟踪（excursion 0.003）；峰值接触力 4.5 kN | `rejected`：速度阻尼轮（stiffness=0）无恢复力矩，21.9 kg 顶部质量＋0.414 m 力臂在瞬态内不可用 |

**lite3 差分归因**（替代绝对倾角判断，回应用户质询）：裸载对照并非"失稳"，而是**稳定的内在倾斜姿态**——倾角恒定 0.319 rad（18.3°，2400→3599 步不增长），四足持续接地，前后足受力 2.1/2.0/46.1/45.7 N 严重偏载，反力为体重 82%（低于 0.9 阈值，部分重量由非足部身体接触承担，接触凸包裕度 -0.035 m）。装载后同一 fixture 下：+Z1（5.2 kg）倾角 +77° 至 1.656 rad、对地反力降为 0；+Gen3（8.1 kg）倾角 +71° 至 1.551 rad、反力 0——完全翻倒的质变。差分明确指向顶部载荷本身；但裸载基线未达门禁（偏载姿态），结论限定为"当前挂载位置（TORSO+0.073 m）下装载即翻"，不外推 lite3 真实承载能力。加刚度对照（800/20）更差（裸载倾 167°），弃用。

当前矩阵：56 条 = 13 conditional / 11 rejected / 32 unknown（unknown 全部为"该宿主无此臂挂载数据"）。

### 2026-09-16 终态：Kinova Gen3 撤出 Env DIY 选择面

用户决定 Gen3 不进入 Env DIY。集成面（catalog 条目、准入表、builder/flow/simulator 接线、终端与 GUI 卡片、接口 YAML、`KINOVA_GEN3_IK_CFG` 控制器包、`kinova_m20.json` 示例环境）已全部回退删除。保留内容：`EAI_assets/robots/kinova_mount.py` 挂载机制（含 carter profile 与多环境正则修复）、`usd/payloads/manipulators/kinova_gen3/` 资产、smoke 工具的 kinova 支持、以及本文件记录的全部验证证据（scout/b2/m20/carter conditional，go2/lite3 rejected）。最终准入矩阵 42 条：9 conditional / 9 rejected / 24 unknown（unknown 均为无挂载数据宿主）。
