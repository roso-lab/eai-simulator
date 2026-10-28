# 锚点底稿：svc-p2-01（城市 · 维修工单责任转移，P2）

- anchor_id：svc-p2-01
- 域 / 场景：service / city
- scene_anchor：`facility_maintenance_transfer`；关系签名：P2；common `C-P2-01` 服务域成员
- 目标：把已发现的维修工单转给响应人员并取得回执
- 时间范围（horizon）：150 秒（7500 拍）[待出题人裁决]
- 难度向量（目录骨架建议值，八项均 `[待出题人裁决]`）：依赖深度 1、协调宽度 1、能力稀缺性 1、竞争度 1、危害级 0、通信需求 1、分支负荷 0、不确定性 0

## 1. 任务故事

发现人员在城市设施内确认一项开放维修工单，记录位置、故障类型和剩余工作。响应人员接收同一工单的责任并在现场继续处理；交接必须由外部账本和后续动作共同证明。发送工单消息或响应人员抵达附近不等于接收。工单需在任务时间范围内形成可核验回执。

## 2. 义务图

| 义务 | 内容 | 结构关系 |
| --- | --- | --- |
| assemble_pallet | 发现并确认开放工单身份、位置和状态 | 先于 `move_to_dock` |
| move_to_dock | 在工单交接面整理状态、剩余工作和责任 | 先于 `receive_pallet` |
| receive_pallet | 响应人员确认同一工单并继续执行剩余工作 | 严格晚于释放 |

## 3. 角色与能力

- 角色：`source_operator`、`receiver`。
- 必需能力：`mobile_base`、`payload_transport`；维修交互/读取工单接口为待绑定项。

## 4. 里程碑（外部可观测定义）

| 标签 | 可观测定义 |
| --- | --- |
| assigned | 工单身份、检测位置、交接方和剩余工作写入账本。 |
| ready_for_handoff | 工单状态由外部观察确认，交接所需信息在交接面可核对。 |
| released | 源端完成责任释放，工单不再由源端占用。 |
| received | 响应端对同一工单形成接收记录并实际开始剩余工作。 |
| accepted | 回执、后续动作和截止期条件满足。 |

## 5. 资源与区域

| 资源 | 建议命名 | 位置描述 | 容量/互斥 |
| --- | --- | --- | --- |
| 工单现场 | `assembly_area` | 城市设施内故障点及周围可观察范围 | 一工单一当前责任记录 [待出题人裁决] |
| 交接面 | `dock_handoff_face` | 设施服务台或故障点旁可核对工单状态的位置；[场景缺口：需确认] | 同一工单一次一接收方 |
| 响应通道 | `responder_route` | 现场与响应待命区之间的公共通道 | 不默认容量互斥，待场景绑定 |

## 6. 谓词清单（参数均待出题人裁决）

| # | 谓词 id | 判法 | 考察内容 | 建议事件对 / 阈值 |
| --- | --- | --- | --- | --- |
| 1 | `move_after_assembly` | 严格先后（`precedence`） | 释放前必须有可核对的工单状态 | `ready_for_handoff` → `released`；严格晚于 [待出题人裁决] |
| 2 | `receive_after_release` | 严格先后（`precedence`） | 接收责任不得早于释放 | `released` → `received`；严格晚于，零容差 [待出题人裁决] |
| 3 | `accept_after_receipt` | 严格先后（`precedence`） | 终局验收不得早于责任接收 | `received` → `accepted`；严格晚于 `[待出题人裁决]` |
| 4 | `handoff_complete_within_horizon` | 截止期（新原语提案） | 工单责任及时接续 | 建议 7500 拍 `[待出题人裁决]`；终点=`accepted` `[待出题人裁决]` |

未知工单或接收面属于待绑定/评估器不足；D1 仅对预注册且具可检查不可行证明的变体安全拒绝。数据不足须标明 `evaluator_record_missing`，不补写回执。


目录约束映射：`move_after_assembly`、`receive_after_release`、`accept_after_receipt`、`dock_slot_capacity_one`；必需能力包含 `payload_transport`。
## 7. 难度与设计意图

考察开放工单的身份保持、责任释放与接收后继续执行。失败模式包括初次派工被误计为交接、消息无接收、错误工单接收和等待超时。交接违约进入 D3，不把工单内部动作重复计作跨机器人协作。
