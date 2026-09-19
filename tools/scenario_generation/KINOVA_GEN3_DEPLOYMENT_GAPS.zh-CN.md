# Kinova Gen3 候选参数核对

审计日期：2026-09-16。结论：继续保持 candidate-only；本地模型可用于带来源说明的仿真研究，尚不能标记为与指定实机等价。未添加夹爪，也未开放 Env DIY 默认选项。

## 型号与质量口径

| 数据来源 | 变体与总质量 | 证据等级 |
|---|---|---|
| 本地 `gen3n7_instanceable.usd` | 7 DoF，质量合计约 8.0519 kg | authored 参数之和，`derived`；没有实机测量 |
| 本地 `GEN3-7DOF-NOVISION_FOR_URDF_ARM_V12.urdf` | 文件声明 7 DoF、无视觉模块，质量字段之和 8.05 kg | URDF declaration 的 `derived` 总和 |
| 厂商 User Guide R07，Table 25，正文第 64 页 | 7 DoF、带视觉模块、不含夹爪，8.2 kg | `manufacturer_declared` |

厂商规格的视觉配置与本地 NOVISION URDF 不同，不能直接用 8.2 kg 替换所有连杆质量，也不能把约 0.1481 kg 的差额直接当作视觉模块质量。尚未取得该差额的部件级质量、COM 和惯量来源。[厂商 User Guide R07](https://www.kinovarobotics.com/uploads/User-Guide-Gen3-R07.pdf)

厂商当前可获取的 `Kinova_Onepager_Gen3_2026_EN.pdf` 给出 7 DoF 总质量 8.2 kg、全范围连续负载 2 kg、中范围连续负载 4 kg，脚注说明负载不含夹爪、全范围连续值适用于运动。文件名包含 2026，但页脚版权为 2024；不得将其自行称为特定固件/硬件修订版认证。[厂商规格单](https://www.kinovarobotics.com/uploads/Kinova_Onepager_Gen3_2026_EN.pdf)

## 本地逐字段差异

`tmp/kinova_urdf_usd_audit_v1.json` 中的九个 `match` 是容差内的质量匹配，不能解释为逐位相等或完整惯量匹配。USD/URDF 的质量总差约 0.0019 kg，例如 shoulder 为 1.3773/1.377 kg，half_arm_1 与 half_arm_2 各为 1.1636/1.163 kg。必须保留原始数值和比较容差。

URDF 的 `end_effector_link` 显式声明零质量、零惯量、零惯性原点；USD 的同名链接有约 `1e-7 kg` 占位质量，缺少 authored COM/惯量。旧 massless-link 诊断只移除同级链接的物理 API，没有让末端跟随腕部，也没有移除指向它的固定关节；其 `source_resolved_runtime` 不能作为正确装配证据。

2026-09-16 修复：固定底座和移动挂载共用 `EAI_assets.robots.kinova_physics.model_massless_interface()`。必须提供 URDF，核对零质量、六个零惯量项、固定父关节、空几何及 USD/URDF 相对位姿；随后禁用旧链接和固定关节，将坐标帧挂在 `bracelet_link` 下，并更新 Isaac 刚体登记关系。已有工具连接或非占位质量会被拒绝。转换只覆盖运行时 stage，不修改外部 USD/URDF；八个承重链接的质量和惯量不缩放。接口状态精确标为 `source_resolved_runtime_interface_only`。

移动挂载还把 articulation root 放到真实 `base_link`，保留原求解器属性及 source world joint 的禁用覆盖，并将宿主旋转纳入装配变换。删除引用 prim 的本层 override 会撤销禁用，不能用 `RemovePrim` 代替 `SetActive(False)`。

`tmp/kinova_interface_v2_20260916/fixed_corrected.json` 记录修复后的 8 个承重刚体、7 个 DOF、约 8.0519 kg。零驱动和受限 PD 各运行 720 步；零驱动的 PhysX 刚度、阻尼、驱动上限全部为 0，最终最大关节位移约 2.7625 rad，PD 约 0.000133 rad。两阶段仅证明有限状态及驱动切换有效，不是抓取或额定负载验证。旧 `kinova_mount_v7.json` 中名为零驱动的阶段仍保留 PD，不再用作零驱动证据。

URDF 含非零惯量积。完整核对发现：八个承重链接的源 USD 主轴均为 `(0,0,0,0)`，不是有效旋转，必须保持 `unknown`；原运行时矩阵近似只保留 URDF 对角项。Scout 旧报告的八个链接全部不匹配完整 URDF，最大元素误差为 `0.000691 kg m²`，单链接 Frobenius 相对误差最高约 16.46%。先前 `crosscheck.json` 隐式把零四元数当成单位旋转，其完整惯量一致性结论撤回，见 `crosscheck_corrected.json`。

`kinova_inertials.py` 现在核对零关节位姿下各连杆相对 base 的坐标系、COM 和质量口径，读取包括惯性 RPY 在内的六项完整 URDF 张量，再分解为特征值和有效主轴。移动候选默认应用该运行时覆盖；固定底座诊断须显式给出 `--apply-urdf-inertias`。保留全部承重链接 USD 质量和 COM，质量舍入差保留，不把 source correction 当作实机测量。源 USD/URDF 文件不改写。

`kinova_inertia_audit.py` 对源文件及运行时报告逐字段核对，并校验资产引用层 hash。固定底座与 Scout 的修复后八个链接均在声明容差内匹配完整 URDF，最大元素误差约 `8.17e-9 kg m²`。证据目录为 `tmp/kinova_tensor_v2_20260916/`；原 USD 的 authored tensor 状态仍是 unknown，运行时参数来源则为 `urdf_declared_inertia_runtime_override`。

当前候选 PD 的 `39/9 Nm` 限制及刚度、阻尼是配置值；本次审计没有闭合它们到指定电机/固件的连续、峰值、速度及热限制。不得将这些数值直接标记为实机已标定参数。

## 来源绑定

- USD 根文件：`/home/pupil/下载/eai_manipulation_assets_20260915/nvidia_isaac_5.1/Isaac/Robots/Kinova/Gen3/gen3n7_instanceable.usd`；SHA-256 `4f42cc344aaa0e86e0fbf3fa8538324713fa6dc8810c391fb1dd5ad1f689d882`。这只是根文件 hash，不代表引用层完整闭包。
- URDF：`/home/pupil/下载/eai_manipulation_assets_20260915/sources/kinova_ros_kortex/ros_kortex-noetic-devel/kortex_description/arms/gen3/7dof/urdf/GEN3-7DOF-NOVISION_FOR_URDF_ARM_V12.urdf`；SHA-256 `a7fc81b8a5e8df9e25591e43b1aa7e369ab4d3a915d3960bb77381c48983db54`。
- `noetic-devel` 目录名不是不可变 Git commit。上游提交与下载版本仍需记录，不能由目录名推断。
- USD 与 URDF 均未提供当前实机序列号、测量方法及不确定度，`hardware_calibration=unknown`。

## 进入默认组合之前的工作

1. 固定实际采用的 NOVISION/视觉、6/7 DoF、底座接口版本，补齐所有引用层与上游 commit。
2. massless 接口、移动根节点和完整 URDF 惯量恢复已完成；继续带工具的碰撞、接触验证及指定型号的硬件参数来源闭合。
3. 给安装板、快拆接口、线缆及末端工具建立独立质量、COM、惯量与紧固件来源；不要将它们默认为零。
4. 重做固定底座驱动、末端接触、空载底盘和装臂底盘对照，使用开启接触报告的独立对地过滤传感器。
5. 审计 PD 与厂商控制器差异，再测试指定加速度、姿态、制动及行走中操作。

R07 的正文第 69 页说明承载随距离、持续时间及夹爪而变，因此公开的 2/4 kg 只能作为有适用条件的厂商声明。它们既不是当前无视觉候选已经验证的工作域，也不能证明 Scout/B2 的整机额定 payload、夹持力或安全裕度。[载荷与工作域说明](https://www.kinovarobotics.com/uploads/User-Guide-Gen3-R07.pdf#page=75)
