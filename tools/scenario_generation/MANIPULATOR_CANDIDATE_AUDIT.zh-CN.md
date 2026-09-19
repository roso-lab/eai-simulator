# 机械臂与灵巧手候选审计

本清单只记录资产来源和参数可追溯性，不代表动力学、抓取或实机验证通过。候选在完成固定底座、未缩放质量、关节驱动和接触测试前，不加入 Env DIY 默认选项。

2026-09-16：Gen3 的 NOVISION/视觉配置差异、8.05/8.0519/8.2 kg 口径、质量比较容差和已修复的 massless 接口问题，见 [Gen3 参数核对](KINOVA_GEN3_DEPLOYMENT_GAPS.zh-CN.md)。旧报告的 `match` 不代表完整物理等价。

| 候选 | 质量/COM/惯量 | 关节与碰撞 | 控制来源 | 状态 |
|---|---|---|---|---|
| Kinova Gen3 | USD 质量/COM 保留；缺失有效主轴的惯量已从完整 URDF 恢复，末端帧按零质量源修复，未实测 | 7 DoF；转换后 8 个承重刚体 | 独立挂载 fixture 有界 PD，非厂家标定控制器 | `candidate_physics_smoke_pass`（源 USD tensor 仍 unknown） |
| Unitree Dex5 | 本地 USD 21 个刚体的质量、COM、主轴和惯量字段完整；静态 SI 审计为 `declared`，未实测 | 20 个可动 DOF；物理 USD 存在 | 仓库暂无 Dex5 动作语义和控制器 | `candidate_physics_smoke_pass`（未完成挂载） |
| Robotiq Hand-E | USD 可打开并有 3 个刚体、22 个碰撞 API，但 3 个刚体均未 authored mass/COM/惯量 | 2 个 slider 和线性驱动 | 仓库暂无 Hand-E 控制器 | `unknown` |
| Allegro Hand | 本地右手 URDF 有 21 个刚体和质量字段，合计约 0.9549 kg；13 个指节惯量违反正定/主惯量三角约束 | 20 个关节（16 个可动）；没有对应 Isaac USD | 本地 ROS 描述/驱动，暂无 Isaac 控制器 | `invalid_source_physics` |
| Robotiq ePick | 没有对应 Isaac 物理 USD | 仿真关节和接触模型未知 | 本地 ROS2 驱动提供 `grip_cmd` 和物体检测状态 | `hardware_bridge_only` |
| Franka Panda + Panda Hand | 当前 Nucleus 资产质量来源尚未锁定；不能从配置推断 | 仓库已有关节、碰撞和手部驱动配置 | 已有控制配置 | `candidate_pending_asset_audit` |
| Franka + Robotiq 2F-85 | Robotiq 质量可按厂家总质量声明，但连杆惯量仍需审计 | 已有 Robotiq 驱动模式可复用 | 已有 Panda/Robotiq 配置参考 | `candidate_pending_asset_audit` |
| Shadow Hand | 本地 USD 有质量和 COM 字段，但惯量/主轴无效或缺失，不能形成完整物性 | 有 USD 关节/碰撞配置；完整惯量未知 | 有 Isaac 配置，未完成挂载控制语义 | `unknown` |
| Ridgeback + Franka | 当前仅有 Isaac Nucleus 组合资产引用，底盘、安装接口和整机 COM/惯量未锁定 | 有组合关节配置，未完成真实安装几何审计 | 有 Isaac 隐式驱动配置，非硬件标定 | `candidate_pending_asset_audit` |

## 证据边界

- `declared` 表示参数写在资产或配置中，不表示与实机标定一致。
- `estimated` 只允许用于明确标注的缺失质量/惯量补全，不能伪装成厂家数据。
- `unknown` 候选不得自动下载后直接注册为可运行挂载。
- ePick 的 ROS2 接口不能替代仿真质量、惯量、接触和吸附力模型。
- 当前候选审计没有提供额定负载、抓取力、抗风能力、碰撞安全裕度或真实载荷动力学结论。

## 下一条验证顺序

1. Kinova Gen3：逐刚体参数导出、缺失惯量处理、关节限位和固定底座驱动测试（入口：`tools/validation/kinova_mount_smoke.py`）。
2. Unitree Dex5：动作语义、默认姿态、20 个可动 DOF 控制映射和安装转接件。
3. Franka + Robotiq 2F-85：锁定资产版本后做固定底座和质量审计基线。
4. Shadow Hand / Ridgeback + Franka：先锁定本地资产层、底盘安装接口和整机物性，再决定是否进入验证队列。
5. Allegro Hand：锁定上游版本，生成 Isaac USD 并检查 20 DOF 控制/碰撞；Hand-E：逐刚体质量/惯量/COM 与 mimic 方向检查。

## 本地 USD 快照

- Kinova Gen3：原 USD 有 9 个刚体、7 个可动 DOF，质量合计 `8.0519 kg`；按 Kortex URDF 将 `1e-7 kg` 占位链接转换为随腕部运动的非刚体接口后，运行时为 8 个承重刚体。转换必须绑定源 URDF。
- Unitree Dex5：21 个刚体、20 个可动 DOF，质量合计 `0.97857 kg`；SI 静态审计为 `declared_complete`，所有刚体都有 authored mass、COM、主轴和对角惯量。`tmp/dex5_physics_audit_v2.json` 不代表实测或可直接安装。
- Robotiq Hand-E：3 个刚体，2 个关节，22 个碰撞 API；质量字段全部缺失，因此不能作为真实物理候选直接接入。
- Allegro Hand：右手 URDF 21 个刚体、20 个关节（16 个可动）、质量合计 `0.9549 kg`；13 个链接的惯量违反正定/主惯量三角约束，因此源物性审计失败。没有 Isaac USD，不能挂载或运行。
- Shadow Hand USD：静态审计报告 `tmp/shadow_hand_physics_audit.json`；26 个刚体、质量字段合计约 `5.0110 kg`，但 0 个刚体满足完整物性字段，惯量/主轴缺失或无效，状态为 `unknown`。
- Allegro Hand USD：静态审计报告 `tmp/allegro_hand_usd_physics_audit.json`；21 个刚体、质量字段合计约 `2.1354 kg`，但 0 个刚体满足完整物性字段，惯量/主轴缺失或无效，不能用 USD 绕过 Allegro URDF 的源物性失败。

Dex5 的官方产品页给出的 Dex5-1/Dex5-1P 重量为 `1100 g`，20 个自由度（16 主动＋4 个被动），并注明尺寸和最终出货版本可能变化；本地 USD 合计 `0.97857 kg`，比该厂商声明轻约 `0.12143 kg`。因此 `declared_complete` 只表示本地 USD 字段完整，不能称为真实 Dex5-1 质量等价，也不能把差值擅自归因于某个部件。官方页面还将 `10 N` 指尖力和 `3.5/4.5 kg` 抓取条件绑定到具体姿态、物体、温度和型号；这些不是本地仿真已经验证的抓取力或负载结论。[Unitree Dex5-1 官方规格](https://unitree-robot.com/Dex5-1/)

Kinova 的 Isaac 120 步零驱动报告：`tmp/candidate_rollout_v2/kinova.json`。该报告证明 USD 可被 `Articulation` 加载并完成有限状态 rollout，不证明挂载稳定性、控制器正确性或额定负载。

Dex5 的 Isaac 120 步零驱动报告：`tmp/candidate_rollout_v2/dex5.json`。该报告证明 USD 可被 `Articulation` 加载并完成有限状态 rollout，不证明灵巧手控制、抓取接触或机械臂挂载稳定性。

Kinova 旧固定底座双阶段报告 `tmp/kinova_mount_v7.json` 的“零驱动”阶段仍保留 PD，不能用作零驱动对照。修复后的 `tmp/kinova_interface_v2_20260916/fixed_corrected.json` 每阶段运行 720 步，并从 PhysX 读取实际刚度、阻尼和驱动上限，证实零驱动参数全部为零。

`tmp/kinova_massless_link_v1.json` 的覆盖没有完整处理随腕部运动的坐标帧和旧固定关节；其 `source_resolved_runtime` 结论撤回。新共享转换器通过引用 stage 回归验证，不修改源层，拒绝删除真实质量或已有工具连接。完整惯量已查明 USD 零主轴及丢失惯量积的问题，并按 URDF 在运行时恢复；硬件标定仍未完成。

`tools/validation/kinova_urdf_usd_audit.py` 对 Kortex URDF 与 USD 做了逐链接质量对照：9 个链接质量在报告容差内匹配，`end_effector_link` 的零质量来源明确。该对照不能替代惯性坐标变换，也不会修改外部 USD；真实物理等价性仍为 `unknown`。
