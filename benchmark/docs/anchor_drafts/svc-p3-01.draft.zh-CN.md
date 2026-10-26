# 锚点底稿：svc-p3-01（餐厅 · 访客同步协助，P3-J）

- anchor_id：svc-p3-01
- 域 / 场景：service / restaurant
- scene_anchor：`two_operator_assistance`；关系签名：P3；P3-J；common `C-P3-01` 服务域成员
- 目标：两台服务机器人同步协助一位访客
- 时间范围（horizon）：220 秒（11000 拍）[待出题人裁决]
- 难度向量（目录骨架建议值，八项均 `[待出题人裁决]`）：依赖深度 3、协调宽度 3、能力稀缺性 3、竞争度 2、危害级 2、通信需求 3、分支负荷 2、不确定性 2

## 1. 任务故事

餐厅中一位访客需要两台服务机器人在左右两侧共同协助通过登记的服务区域。两台机器人先分别到达访客两侧的协助姿态，再建立持续的双侧接触/支撑并共同移动。完成协助后，把访客的服务责任交给接收端或终点服务位。单机拉拽、只发送同步消息或中途支撑数降到一台均不满足安全条件。

## 2. 义务图

| 义务 | 内容 | 结构关系 |
| --- | --- | --- |
| align_left_and_right | 左右协助者到达登记姿态并准备接触 | 先于联合协助 |
| carry_jointly | 两台机器人持续提供双侧支撑并移动访客 | 接触数至少为二 |
| release_to_receiver | 联合协助结束后释放访客服务责任 | 先于接收 |
| accept_pallet | 接收方/服务位确认访客到达并接续服务 | 严格晚于释放 |

## 3. 角色与能力

- 角色：`left_carrier`、`right_carrier`、`receiver`。
- 必需能力：`mobile_base`、`payload_transport`、`synchronized_motion`；访客交互能力待场景绑定。

## 4. 里程碑（外部可观测定义）

| 标签 | 可观测定义 |
| --- | --- |
| assigned | 访客身份、目标位置、左右姿态和接收方义务写入账本。 |
| synchronized | 两侧机器人均到达协助姿态，且到位事件落在同步窗口内。 |
| joint_carry_complete | 访客与两机共同到达目标服务区，联合支撑区间结束。 |
| released | 源端不再承接访客，责任释放由外部位置/接触状态确认。 |
| accepted | 接收端实际取得服务责任并登记访客到达。 |

## 5. 资源与区域

| 资源 | 建议命名 | 位置描述 | 容量/互斥 |
| --- | --- | --- | --- |
| 访客对象 | `visitor_target` | 餐厅入口或桌边登记的访客交互区域；对象接口待绑定 | 协助期间至少两台有效支撑 [待出题人裁决] |
| 左/右协助位 | `visitor_left_pose`、`visitor_right_pose` | 访客两侧可接近的服务姿态区 | 每侧同一时刻一台协助者 [待出题人裁决] |
| 接收服务位 | `visitor_receipt_area` | 目标桌边或服务台前沿；[场景缺口：需确认] | 单一责任接收 |

## 6. 谓词清单（参数均待出题人裁决）

| # | 谓词 id | 判法 | 考察内容 | 建议事件对 / 阈值 |
| --- | --- | --- | --- | --- |
| 1 | `carriers_start_within_sync_window` | 双事件窗口（`within_window`） | 两侧到位准备的同步性 | `left_arrive_in_position` ↔ `right_arrive_in_position`；差 ≤250 拍（5 秒）`[待出题人裁决]`；测到位而非起抬 |
| 2 | `pallet_requires_two_carriers` | 接触计数（`contact_count`） | 协助期间安全支撑数 | `joint_carry_start`→`joint_carry_complete`；逐拍接触数 ≥2 [待出题人裁决]；允许替补 |
| 3 | `release_after_joint_carry` | 严格先后（`precedence`） | 释放不得早于联合协助完成 | `joint_carry_complete` → `released`；严格晚于 [待出题人裁决] |
| 4 | `accept_after_release` | 严格先后（`precedence`） | 接收不得早于释放 | `released` → `accepted`；严格晚于 [待出题人裁决] |

接触不足是安全条件；记录不足给 `invariant_insufficient`，不把缺失当作通过。访客、姿态或接口未知属于待绑定/评估器不足；D1 仅对预注册且有可检查不可行证明的变体安全拒绝。


目录约束映射：`carriers_start_within_sync_window`、`release_after_joint_carry`、`accept_after_release`、`pallet_requires_two_carriers`。
## 7. 难度与设计意图

考察 P3-J 的到位同步、逐拍双侧支撑和访客责任接续。预期失败包括一侧迟到、单机承接、访客到位前释放、接收消息代替实际服务。D4 可冻结一侧交互能力，必须明确替代或安全拒绝；执行期违约进 D3。
