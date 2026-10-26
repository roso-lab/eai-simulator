# 锚点底稿：prod-t-01（工厂基准 · 长托板联合搬运与交接，P3-J+P2）

- anchor_id：prod-t-01
- 域 / 场景：production / factory_benchmark
- scene_anchor：`robotic_joint_pallet_carry`；关系签名：P3、P2；P3-J
- 目标：两台机器人共同抬运长托板，再交给泊位接收端
- 时间范围（horizon）：220 秒（11000 拍）[待出题人裁决]
- 难度向量（目录已定）：依赖深度 3、协调宽度 3、能力稀缺性 3、竞争度 2、危害级 2、通信需求 3、分支负荷 2、不确定性 2

## 1. 任务故事

长托板位于工厂基准场景的取板区，需要左、右两台机器人共同承托并保持联合搬运。两台机器人先分别到达各自承托姿态，随后建立至少两台机器人的持续接触承托，再把托板运到泊位。联合搬运完成后，责任释放给泊位接收端，接收端实际取得托板。单机拖行、只发送同步消息或联合搬运期间承托数降到一台均不满足安全条件。

## 2. 义务图

| 义务 | 内容 | 结构关系 |
| --- | --- | --- |
| align_left_and_right | 左右承托者到达登记姿态并建立可观测准备状态 | 先于联合搬运 |
| carry_jointly | 两台机器人持续承托并共同运输长托板 | 承托接触数至少为二 |
| release_to_receiver | 联合搬运结束后源端释放托板责任 | 先于接收 |
| accept_pallet | 泊位接收端取得同一托板并登记责任接收 | 严格晚于释放 |

## 3. 角色与能力

- 角色：`left_carrier`、`right_carrier`、`receiver`。
- 必需能力：`mobile_base`、`payload_transport`、`synchronized_motion`。
- 承托机器人允许动态替补，但任一时刻有效接触数必须满足安全条件；不预设型号。

## 4. 里程碑（外部可观测定义）

| 标签 | 可观测定义 |
| --- | --- |
| assigned | 托板身份、左右承托姿态、目标泊位和接收责任写入账本。 |
| synchronized | 左右承托者均到达承托姿态，且两次到位事件差在同步窗口内。 |
| joint_carry_complete | 托板完成登记路线运输，联合承托区间结束且外部位置可核验。 |
| released | 托板责任从承托端移出并形成释放观测。 |
| accepted | 接收端实际取得托板并记录接收责任。 |

## 5. 资源与区域

| 资源 | 建议命名 | 位置描述 | 容量/互斥 |
| --- | --- | --- | --- |
| 长托板 | `long_pallet` | 工厂基准取板区内登记的长尺寸托板 | 联合承托安全条件：任意时刻至少两台有效接触 [待出题人裁决] |
| 左/右承托位 | `left_support_pose`、`right_support_pose` | 托板两端可接近的承托姿态区；精确姿态待地图绑定 | 同一端同一时刻一台有效承托者 [待出题人裁决] |
| 交接泊位 | `joint_handoff_dock` | 长托板运输终点的泊位及接收前沿；[场景缺口：需确认长度] | 接收区容量 1 [待出题人裁决] |

## 6. 谓词清单（参数均待出题人裁决）

| # | 谓词 id | 判法 | 考察内容 | 建议事件对 / 阈值 |
| --- | --- | --- | --- | --- |
| 1 | `carriers_start_within_sync_window` | 双事件窗口（`within_window`） | 左右承托姿态建立的同步性 | `left_arrive_in_position` ↔ `right_arrive_in_position`；差 ≤250 拍（5 秒）[待出题人裁决] |
| 2 | `pallet_requires_two_carriers` | 接触计数（`contact_count`） | 联合搬运期间安全承托数不少于二 | 区间=`joint_carry_start`→`joint_carry_complete`；逐拍接触数 ≥2 [待出题人裁决]；允许替补 |
| 3 | `release_after_joint_carry` | 严格先后（`precedence`） | 释放不得早于联合搬运完成 | `joint_carry_complete` → `released`；严格晚于，零容差 [待出题人裁决] |
| 4 | `accept_after_release` | 严格先后（`precedence`） | 接收需在释放后发生 | `released` → `accepted`；严格晚于，零容差 [待出题人裁决] |

承托不足是成功安全条件，记录为 D3/任务失败并不打断观测；数据不足须给 `invariant_insufficient` 原因码。若不存在两处可承托姿态或托板不可被外部跟踪，设计上不可行，D1 应安全拒绝。

## 7. 难度与设计意图

本题考察多机到位同步、持续承托安全性和交接顺序。失败模式包括一侧迟到导致单机承重、替补未保持接触、联合搬运未结束即释放、接收端只收消息。D1 需验证 `synchronized_motion` 与载重能力；D4 可冻结一侧承托能力，仍须明确替代或安全拒绝。
