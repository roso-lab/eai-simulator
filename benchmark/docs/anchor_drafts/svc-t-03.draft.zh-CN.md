# 锚点底稿：svc-t-03（仓库 · 持续覆盖与电力点检查并行，P1+P3-S）

- anchor_id：svc-t-03
- 域 / 场景：service / warehouse_nvd
- scene_anchor：`coverage_with_power_inspection`；关系签名：P1、P3（P3-S）
- 目标：维持公共区域覆盖，同时独立检查两个基础设施电力点
- 时间范围（horizon）：240 秒（12000 拍）[待出题人裁决]
- 难度向量（目录骨架建议值，八项均 `[待出题人裁决]`）：依赖深度 3、协调宽度 3、能力稀缺性 3、竞争度 2、危害级 3、通信需求 3、分支负荷 3、不确定性 3

## 1. 任务故事

仓库公共通道在登记服务窗口内必须持续有覆盖，覆盖机器人在区域内保持可观测在位。另一台检查机器人前往电力检查点 A、B，完成相机/传感器记录；检查工作有真实机会时可与覆盖并行。覆盖机器人短暂离开去检查会留下空窗，历史空窗不能被最后回到通道抵消。任务需在窗口结束前完成两点检查和覆盖验收。

## 2. 义务图

| 义务 | 内容 | 结构关系 |
| --- | --- | --- |
| cover_area | 在公共仓库通道建立服务覆盖并启动窗口 | 先于持续服务 |
| inspect_power_a | 对电力点 A 完成外部可核验检查 | 与 B 可并行 |
| inspect_power_b | 对电力点 B 完成外部可核验检查 | 与 A 可并行 |
| maintain_service | 从覆盖开始到窗口结束维持允许空窗以内的覆盖 | P3-S 全过程 |

## 3. 角色与能力

- 角色：`coverage_robot`、`inspector`；系统可动态复用，但复用产生的空窗照实记录。
- 必需能力：`mobile_base`、`inspection_camera`；电力点读数接口和 `service_interaction` 待绑定。

## 4. 里程碑（外部可观测定义）

| 标签 | 可观测定义 |
| --- | --- |
| assigned | 通道覆盖窗口、电力点 A/B、检查范围和终止条件写入账本。 |
| coverage_started | 外部任务需求窗起始事件已登记，覆盖机器人在该窗内达到稳定在位条件（建议 25 拍）[待出题人裁决]。 |
| inspections_complete | A、B 两点均有有效相机/传感器检查记录并关联位置。 |
| coverage_continuous | 从预注册外部需求窗起点到终点的历史覆盖空窗审查通过，首尾缺口也计入。 |
| accepted | 检查、覆盖和截止期条件同时满足，评估器写入验收。 |

## 5. 资源与区域

| 资源 | 建议命名 | 位置描述 | 容量/互斥 |
| --- | --- | --- | --- |
| 公共覆盖通道 | `warehouse_public_aisle` | 仓库主通道中供人员和服务经过的登记区域 | 空窗建议 ≤50 拍（1 秒）[待出题人裁决] |
| 电力检查点 A/B | `power_check_a`、`power_check_b` | 仓库两端配电柜或设施检查面；精确实体待语义地图确认 | 各点建议一次一检查者 [待出题人裁决] |
| 检查工具 | `inspection_tool` | 检查机器人携带的相机/传感器组件 | 每点一工具；资源共享规则待绑定 |

任务描述可公开通道需持续覆盖；不公开空窗账本、未来电力点状态或评估器内部心跳。

## 6. 谓词清单（参数均待出题人裁决）

| # | 谓词 id | 判法 | 考察内容 | 建议事件对 / 阈值 |
| --- | --- | --- | --- | --- |
| 1 | `coverage_has_no_uncovered_window` | 覆盖空窗（`coverage_no_gap`） | 覆盖窗口历史缺口受限 | 预注册 `service_window_start`→`service_window_end`；每段空窗 ≤50 拍（1 秒）`[待出题人裁决]`，首尾缺口计入 |
| 2 | `inspections_may_run_in_parallel` | 区间重叠（`min_overlap`，诊断级） | A/B 检查的独立并行机会 | 各点首个检查动作→完成；重叠 ≥1 拍 [待出题人裁决] |
| 3 | `inspection_records_after_point_arrival` | 严格先后（`precedence`） | 检查记录需发生在实际到点后 | `arrive_power_point` → `inspection_complete`；严格晚于 [待出题人裁决] |
| 4 | `coverage_and_inspections_within_horizon` | 截止期（新原语提案） | 持续服务和检查均在窗口内收束 | 建议 12000 拍；终点=`accepted`，[待出题人裁决] |

P3-S 空窗按 `maintain_service` 义务计入 D3；P1 重叠只作诊断，同源工具容量不重复计分。跟踪器、检查点或接口未知属于待绑定/评估器不足；D1 仅对预注册且有可检查不可行证明的变体安全拒绝。


目录约束映射：`coverage_has_no_uncovered_window`、`inspections_may_run_in_parallel`、`one_inspection_tool_per_point`。
## 7. 难度与设计意图

本题考察持续覆盖与独立基础设施检查的复合调度。预期失败包括覆盖机器人离开造成长空窗、检查记录无到点证据、只完成一个电力点、最后回通道掩盖中途缺口。D4 可冻结检查相机或覆盖能力，需冻结相应义务并保留完整性原因码；D6 可报告覆盖成本与检查时延。
