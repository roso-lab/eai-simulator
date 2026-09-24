# 餐厅自主执行：证据服务与原生接线契约

阶段36，基线 `2491fe4`。本文区分已实现的证据服务和待实施的原生入口。
用户既定原则是公开总体目标和里程碑，由参与者拆题、选人和安排时序。
当前固定 mission 的 EMOS/TeamWeaver 入口继续作为原有诊断，不冒称自主分解。

## 已实现：显式 readiness service v2

`demo.restaurant_benchmark.service_backend_v2.RestaurantReadinessBackendV2`
使用独立 schema `eai.restaurant.readiness_service.v2`，不替换 v1。

- `record_observation` 以唯一 observation_id 保存可信观察；同区域可复查，不同区域
  可由同一机器人检查，多个独立观察可发生在同一个物理 tick。
- `publish_relay` 引用两个区域各一条已记录观察。可有多次出版记录，重试必须使用
  新 message_id；每次都须在其依赖观察之后产生。记录表示服务侧 publication，
  **不等于通信送达**，原生接线必须另核对真实链路投递事实。
- `commit_readiness_receipt` 显式选择两个 observation_refs 和 relay_message_id。
  选中中继须绑定同一对观察，且先于回执；不要求选最新观察，也不强加题面没有的
  新鲜度阈值。若未来真实任务规定有效期，应另作明确契约，不能由服务擅自添加。
- 回执绑定选中观察/中继内容摘要及自身完整内容摘要。额外有效观察和重试不能
  导致失败；未知引用、重复身份、跨 episode、非有限时间、时钟矛盾及因果倒置拒绝。
- 被拒调用不消耗 ID、不推进时钟、不修改账本；快照深拷贝。一次回执终止此服务。
  无关观察可与回执同 tick，不因此给已完成的证据链添加新依赖。

`evaluate_readiness_receipt_v2` 从归档独立复算选中证据链，并检查额外证据的结构、
episode 和时间一致性。它返回的是**服务证据有效性**；
`native_execution_verified=false` 明确表示尚未证明 PhysX/传感器读数的真实性。
它不提升 `scoreable`、`execution_validated`、`business_outcomes_validated`。
修改记录后重算全部摘要不构成物理证明；外层可信日志、原生事实绑定与源码身份仍必需。

最小用法（可信后端内部）：

```python
from demo.restaurant_benchmark.service_backend_v2 import (
    RestaurantReadinessBackendV2, evaluate_readiness_receipt_v2,
)

service = RestaurantReadinessBackendV2(episode_id=episode_id)
# 后端从原生观察构造 record_observation；不是接收模型自报的成功。
# 可反复观察，publication 与 receipt 分别记录在真实发生的时刻。
receipt = service.commit_readiness_receipt(
    receipt_id=receipt_id, simulation_step=step, time_s=now,
    observation_refs=[kitchen_observation_id, dining_observation_id],
    relay_message_id=delivered_relay_id,
)
snapshot = service.snapshot()
review = evaluate_readiness_receipt_v2(
    episode_id=episode_id, receipt=receipt,
    observations=snapshot["observations"],
    relay_publications=snapshot["relay_publications"],
)
```

变量 `delivered_relay_id` 的投递检查由可信调用方负责，服务不会因变量名称而断言已送达。
本批未将 v2 接入旧原生 request、旧 backend 或旧 archive verifier；它们继续按 v1 验证。

## 待实现：自主参与者与原生归档共同升级

仅删除 `_expand_mission_plan` 不够：旧验真器会从 allocation 再生成它，并要求
提交 plan 哈希相同；旧 request/verifier 也会重新过滤三机器人题面。新版应作为
显式自主入口，冻结以下边界后实现，不能默默放宽旧验真器。

| 边界 | 新入口要求 | 最小验收 |
|---|---|---|
| 题面身份 | 原始题包哈希、实际公开任务哈希、candidate×skill×route覆盖矩阵分别绑定；不静默缩小八机器人实例 | 不支持的后端能力在准入时列为平台覆盖缺口，不能伪装成当前团队真不可行 |
| 参与者 | 接收公开 task/API 和新可见事件，直接产生 Goal actions/plan；不灌入作者拆题、assignment或顺序 | 两个结构不同的合法 plan、单机复用、多机方案都可被审计；伪装的固定目录不能称自主算法 |
| 部署门禁 | 新版公开契约显式启用 v3 部署判断；隐藏 oracle 在首次声明前缓存；外生变化重评估 | deployable/not_deployable两种答案；求助终止；证据缺失归平台，不新增第三种答案 |
| 工具与信息 | 显式 opt-in 接已有位姿/视觉/通信 companion；共用一个 runtime，不再直读 native 位置给参测策略 | 关闭工具不强发；延迟/过期改变实际可见内容，未送达不提前执行，不旁路计量 |
| 执行 | 后端接收已准入的参与者 task，按真实技能/资源解析；真实时间推进与事件泵分离 | 同步兼容入口不变；并发版单一物理循环推进多个技能，含资源互斥和中途取消/重评估 |
| 业务校验 | v2回执所选观察与原生观察一一绑定；检查实际位置/感知等必要事实，不要求唯一到达/观察数量 | 重复检查、重试、合法改序通过；跨机器人冒领观察、虚假位置、未送达交接仍拒绝 |
| 计划审计 | 重放参与者实际提交/修订/派遣与可信回执，验证 GoalSession 准入事实 | 不重建作者计划；不要求所有尝试成功，失败后合法恢复可完成业务；所有尝试计成本 |
| 验真身份 | 新 request/episode/verifier identity 与服务版本、源码哈希绑定；v1/v2旧诊断照常读回 | 旧固定计划归档不得仅改版本晋升；部分执行覆盖不能冒充405格全原生验证 |

后端若缺必要原生能力，应在进入参与者测量前关闭对应条件并报告覆盖缺口；不能
缩小候选池后将模型对剩余团队的判断混入原始条件。开发时允许明确命名、单独身份
的执行子集诊断，其成绩不能并入完整条件。
不能通过虚构 `member_exit` 或 `capability_change` 将驱动未实现转换成“真实团队
失效”；这些事件只能来自可信外生变化。现有餐厅剩余任务 oracle 的旧题面哈希和
synthetic 证据不能直接用于新版原生实例，缺少匹配证书时仍为平台证据缺口。

并发版先以受控假后端证明时间/事件/取消和部署门禁契约，再做原生重叠与响应测试。
不得用两个线程同时调用同步 `execute`：两个导航器都推进同一个世界会重复步进。
技能循环中途出现一条消息，诊断参与者应有机会接收并重规划；被测方法是否响应
属于自身策略，不是硬编码正确答案。

## 本批验收和后续门禁

定向服务测试覆盖不同区域顺序、单机/多机、同tick独立观察、重试、原子拒绝和
防篡改。旧餐厅服务、episode、算法归档及下行传输测试保留；精确执行结果记录在
阶段36交付文档。未做原生接线，因此本批不启动 GPU、不宣称原生自主执行已完成。

下一批统一拥有 runner/backend/native request/verifier 的工程槽先完成上述契约；
容量工程可独立并行，checkpoint不作为它的前置条件。E2多端点新接口仍单独评审。
