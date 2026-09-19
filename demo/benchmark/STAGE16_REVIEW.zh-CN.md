# 阶段16：非楼梯任务生成与审核复核（2026-09-19）

## 本阶段目标

楼梯与 RL 暂按基础能力可行处理，不在本阶段改模型或改变 `F-VERT-01` 题面。任务生成侧继续完善四场景中除楼梯运动验证以外的可审核契约：City 的 participant-facing 服务技能、Modern House 的家庭业务审核、Warehouse 的订单业务审核，以及统一的发布状态。

## 已完成交付

### City 公共服务技能

`demo/benchmark/skill_ontology.py` 已登记：

- `guide_people`：地面导航与 relay；要求当前人员观察、集合区容量/路线准入，成功需要人员轨迹和集合到达证据；
- `control_following_traffic`：地面导航与 relay；区分后续车辆和事故车辆，要求管制确认及车辆反应轨迹；
- `report_incident`：observe 与 relay；要求新鲜已送达事故证据、可达响应服务和真实 receipt。

三项技能都声明参数、前置、效果、失败码、证据类型、取消/超时语义和运行时 owner。`goal_driven_fixtures.py` 的技能时长表同步覆盖新技能，避免共享约束校验因目录扩展而失配。

`participant_service_coverage()` 现在报告三项服务为 `registered_runtime_pending`。公共技能注册只解决 agent 的调用入口；City episode 的人员 roster、车辆轨迹、危险区/集合容量、服务 authority、独立 receipt 和因子效果仍保持 pending。旧 `control_traffic` 适配器 proposal 保留为运行时接线入口，并通过 `control_following_traffic` 映射到公开技能。

### Modern House 任务审核

House 任务已经具备家庭业务闭环：扶手椅安全放置、书堆身份连续、入口到生活区的整条 resident path 恢复、团队退出待机。公开参数包含 14 kg 扶手椅、2 kg 书堆、固定支撑点、单机/双机 handling mode、携物包络和通路净空；两个作者 witness 使用不同物品处理顺序。审核文档见 `handoffs/house_task_review.md`。

当前门槛仍是门外/门槛/门内初始区域、连续三维路径、动态占用、固定点操作和业务 detector。静态 audit、单帧图像、零命令 settling 和可靠技能参考状态不能将 House 提升为可计分。

### Warehouse 任务审核

Warehouse 任务已经具备订单业务闭环：空中货架核对、木箱/纸箱集货、dock 交接、库存与通道 closeout。公开参数包含 12 kg 木箱、3 kg 纸箱、固定支撑点和分担比例；木箱要求按每个成员载荷和联合包络决定是否双机，Carter 的 support connector 没有被当作 pick/place 机械臂。两个作者 witness 仅交换订单处理顺序，保持同一对象、团队池和预算。审核文档见 `handoffs/warehouse_task_review.md`。

当前门槛仍是 Iris/Carter cooked shape 回调、货架和 dock 连续净空、携物 swept envelope、episode-owned service car、双机承载、退出再获取、safe-hold/clear-route、dock receipt 与订单身份连续性。

## 验证结果

在 `env_isaaclab`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 下完成：

```text
demo/city_benchmark/tests/test_foundation.py
source/EAI/test/test_benchmark_goal_service_catalog.py
128 passed

source/EAI/test/test_benchmark_goal_city_intake.py
60 passed
```

此外，目标任务、约束、业务审核、City 部署、可靠技能后端、City service backend/episode adapter 联合 focused 回归在修订后通过；最终批次需在阶段出口再次执行并记录完整结果。代码通过 `git diff --check` 和 Python 编译检查后才允许进入账本收尾。

阶段出口批次实际结果为 `432 passed`。在 `env_isaaclab` 中重新生成并读回 City prepare 包，结果为 `authoring_status=blocked`、`execution_status=external_pending`、`scoreable=false`；统一 `goal_suite audit --verify-sources` 生成 27 个题包、21 组因子对照和 1 个城市候选，`validated=true`，`held_out_validated=false`、`scoreable=false`。输出目录为 `tmp/goal_suite_stage16_20260919/`。

## 审核结论

| 交付层 | 结论 |
|---|---|
| City participant-facing public skill | `passed`，运行时 pending |
| House public task / participant API / symbolic business review | `passed`，场景证据 pending |
| Warehouse public task / participant API / symbolic business review | `passed`，场景证据 pending |
| City/House/Warehouse 真实 episode witness | `blocked/pending` |
| `physical_task_execution_validated` | `false` |
| `business_outcomes_validated` | `false` |
| `scoreable` | `false` |

## 阶段反思

本阶段修复的是“服务是否能被题面和 participant API 表达”的边界，不是把服务 ack、参考后端状态或声明级能力变成业务成功。新增 City 技能后，共享技能目录、时长约束、城市 intake 和旧 adapter proposal 必须一起更新；只更新注册表会使场景契约在加载时失败。House/Warehouse 的任务骨架已经能支持 LLM 生成有业务意义的计划，但最终质量仍取决于真实区域、通路、动态占用和场景业务证据的绑定。

## 未完成交接

- City：完成真实 episode-owned 服务回执和至少一个通信/成员变化成对 treatment；
- House：完成门边界、resident path、家具/书堆操作和退出复核；
- Warehouse：完成物理形状、携物通路、联合承载、成员恢复和 dock/库存回执；
- 所有场景：保持串行 Isaac 验证，并在固定 provider revision 上重新生成题包；
- 楼梯：后续更换运动模型后再验证连续支撑、上下行和 B2 入口，当前不阻塞本阶段任务生成审核。
