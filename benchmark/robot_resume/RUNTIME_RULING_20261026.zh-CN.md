# 运行时资格最终裁决记录（12 台 unknown 实例）

日期：2026-10-26 ｜ 性质：出题人最终裁决 ｜ 对象：robot_runtime_roster.v1.json（27 台）中历史证据矩阵无精确记录、状态为 unknown_no_exact_historical_record 的 12 台实例

## 裁决内容

用户于 2026-10-26 作出最终裁决：下文名单中的 12 台实例**全部批准 runtime qualification**，运行时名册行由 unknown 转为 runtime_qualified=true。裁决为逐台点名批准，不含兜底、不含默认通过；名单之外的任何实例不得依据本记录转正。

## 批准名单（12 台，逐字 instance_id）

| instance_id | 平台 | 配置简历 | 使用限制码 |
| --- | --- | --- | --- |
| hc-rt-002 | carter | carter.CARTER_DIFF_CFG.v0003（Carter 差速轮式，携 D1 臂） | — |
| hc-rt-003 | simple_car | simple_car.SIMPLE_CAR_PLANAR_CFG.v0001（SimpleCar 平面移动） | — |
| hc-rt-008 | go2 | go2.GO2_EXTREME_PARKOUR_TEACHER_CFG.v0003（Go2 越野版，携 D1 臂） | GO2_OFFROAD_MOTION_UNSTABLE |
| hc-rt-011 | b2 | b2.B2_MIXED_STAIRS_CFG.v0003（B2 四足，携 D1 臂） | — |
| hc-rt-013 | m20 | m20.M20_ROUGH_RSL_CFG.v0003（M20 轮腿，携 D1 臂） | — |
| hc-rt-015 | scout | scout.SCOUT_DIFF_CFG.v0002（Scout 差速轮式，携 Z1 臂） | — |
| hc-rt-016 | scout | scout.SCOUT_DIFF_CFG.v0003（Scout 差速轮式，携 D1 臂） | — |
| hc-rt-017 | mushr_v2 | mushr_v2.MUSHR_ACKERMANN_CFG.v0001（MuSHR 车式转向） | — |
| hc-rt-019 | cf2x | cf2x.QUADCOPTER_GOAL_SKRL_CFG.v0001（CF2X 四旋翼） | CF2X_NO_PICK_PLACE_ADAPTER |
| hc-rt-022 | lite3 | lite3.LITE3_VELOCITY_RSL_CFG.v0001（Lite3 四足，无臂） | LITE3_STAIRS_UNSUPPORTED |
| hc-rt-023 | lite3 | lite3.LITE3_VELOCITY_RSL_CFG.v0002（Lite3 四足，携 D1 臂） | LITE3_STAIRS_UNSUPPORTED |
| hc-rt-024 | coco | coco.COCO_ACKERMANN_CFG.v0001（Coco 车式转向） | — |

上表 12 台在运行时名册中均为 pool 档、mobile_platform 角色；各行的资产/能力/初始状态三指纹与既有名册完全一致，本裁决不改动任何指纹。

## 裁决范围与限制

- 本裁决**仅覆盖 runtime_qualified**（运行时名册行级资格）。名册顶层 formal_gates 恒保持全 false：runtime_qualified=false、physical_qualified=false、rankable=false、paper_score_eligible=false；每张收据内嵌的 formal_gates 同样恒为 physical_qualified=false、rankable=false、paper_score_eligible=false。
- 本裁决不开启物理资格、排序资格与论文计分资格；上述正式门仍须按既有流程另行验证。
- 名册中其余 15 台已由历史证据路径（qualified_historical_integration）注册，本裁决不改变其语义与证据。

## 证据与工件边界

- 本裁决**不伪造任何平台工件**：不生成 GPU UUID、不虚构遥测清理记录、不声称任何仿真或实机运行；上述 12 台在历史证据矩阵中没有精确历史记录这一事实不变。
- 据本裁决生成的运行时资格收据**只引用本裁决记录本身**作为唯一来源：来源 = 本文件路径 + 本文件字节 sha256 + 包含本文件字节的 git 提交 + 记录定位符 + 内容断言（内容断言为上表 12 个 instance_id 逐个点名，逐个命中）。
- 收据合同照常逐项复检：文件字节与 sha256 一致、`git show <提交>:<路径>` 的 blob 字节与 sha256 一致、每个 instance_id 在本文件文本中逐字出现；任一不满足即 fail-closed 拒绝。

## 收据绑定（执行约定）

- 注册入口：`benchmark/robot_resume/register_historical_evidence.py`，启用参数 `--ruling-commit <包含本文件字节的真实提交哈希>` 与 `--ruling-record benchmark/robot_resume/RUNTIME_RULING_20261026.zh-CN.md`；两参数缺省时 12 台保持 unknown（fail-closed，不转正）。
- 12 台收据 receipt_id 形如 `<instance_id>.user-ruling.20261026`；注册报告行状态为 `qualified_user_ruling`（与历史路径的 `qualified_historical_integration` 区分）。
- 12 台收据使用独立 authority 块：decision=`user_runtime_unknown_ruling_accepted`，decision_date=`2026-10-26`，scope 写明仅授予 runtime 资格、正式门保持 false。
- 收据 check 明细如实陈述依据为用户裁决：integration_record / functional_record / configuration_binding 三项 passed 的依据均为"用户最终裁决接受"，不声称存在平台运行记录；configuration_binding 的绑定依据为"本文件逐字点名的 instance_id"。

## 执行注记

- 本文件是 12 台 unknown 实例 runtime qualification 的**唯一裁决来源**；任何代码、脚本或报告不得替代或扩充本名单。
- 若本文件被篡改（字节与已注册收据声明的 sha256 不一致、instance_id 名单与上表不完全一致、或指定提交不含本文件字节），注册与校验一律 fail-closed 拒绝。
- 名册提升版仍须满足：27 台逐台内嵌可校验收据、formal_gates 四 false、`validate_runtime_roster.py` 通过。
