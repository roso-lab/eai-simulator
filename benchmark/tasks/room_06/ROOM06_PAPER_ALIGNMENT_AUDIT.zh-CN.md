# room_06 论文检测对齐审计

`room_06` 使用最新 semantic-map revision `60878828dc38412496b1b322f687a829`，逻辑时钟为 `0.02 s/tick`。任务族是跨层图书接力与重物联合搬运。ground 图当前 1F/2F 不连通，`room06_transition_handoff` 是明确登记的 aerial logical proxy；未取得跨层 route witness 前，所有结果都是逻辑诊断，`paper_score_eligible=false`。

## 四级合同与协作证据

- T1：一册书的 ground pick → aerial handoff → 2F place，验证域/能力匹配和顺序依赖。
- T2：三册书的阶段性接力，验证公开任务释放、报告传输和多阶段重规划。
- T3：T2 加两台具备 `joint_carry` 的 ground robot；两个 heavy task 使用同一 `cooperative_group=heavy_carry`，必须分别到达后由 evaluator barrier 同步，再以容量 1 的 `joint_carry_capacity` 资源启动一次联合服务。
- T4：T3 加 C3 和 `book_2_handoff` 后的 aerial availability-loss，验证失联通知、重规划和剩余任务闭合。

`distinct_group` 只限制同一机器人重复承担两侧角色；`cooperative_group` 才表达 P3-J 的两成员同步。单方到达、串行完成或 controller 自报均不能替代 barrier 和两份报告。

## Checkpoint、通信与指标

阶段 checkpoint 为 `spawn_valid → books_assigned → relay_started → transition_handoff → books_complete → joint_carry_started → joint_carry_complete → closeout`。checkpoint 保存任务 ledger、联合组状态、资源 lease、通信队列、机器人状态和事件 digest，支持 restore、replay、fork/branch。

C0–C3 只改变通信传输；C3 丢包/重试会阻塞报告闭合。D1 检查能力、域和 logical feasibility；D2 比较延迟/丢包/报告保持率；D3 检查 relay precedence、P3-J barrier 和资源 lease；D4 检查 availability-loss 后重规划；D5 当前无认证跨场景配对；D6 需要物理时间和路线校准。后五项中未满足证据门槛的字段保持 diagnostic/pending，不能写成论文分数。
