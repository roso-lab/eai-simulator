# 全局逻辑任务对象执行模型 v1

权威：用户 2026-10-28 全局实验设计裁决，适用于全部场景。本规范取代先前任务物体 USD、刚体、接触抓取、吸附/attachment 与对象物理重建要求；不修改论文正文。

## 数据与权限边界

静态 USD 保留环境、导航空间和机器人本体。任务对象只存在于 case manifest 与 benchmark 私有账本：独立 `entity_id`、`category`、必要 `metadata`（质量/容量/最少持有者/批次/身份等）、`initial_semantic_point`、`target_semantic_point`。质量用于技能准入和成本，不进入任务物体物理仿真。

新 manifest 默认使用 `heterocoord.logical-task-object-bindings.v1`。旧 `task_generated` 的 definition_ref/digest 仍可作为逻辑定义来源，不表示要生成物体。旧 prim 绑定可读取历史档案，但新构建仅在宿主明确传入独立物理实验授权引用时接受，算法不能自行选择该路径。

被测系统只输出决策和技能调用，不获得可写账本，也不能提交 held_by、完成标志、质量/载荷覆盖值。`task.logical_objects.LogicalObjectRuntime` 由 benchmark 宿主创建；所有场景复用同一实现，不内置场景白名单。`from_case_manifest()` 从已验证 manifest 读取对象和部署池身份，拒绝调用者替换 robot_ids。

## 状态转换

- pick/load：机器人到达对象当前登记语义点，能力/载荷准入通过，宿主技能会话成功后，账本从 at_point 变为 held_by。
- receive：原持有者和新持有者均在同一登记操作点完成移交会话，原子更换持有者集合。
- place/deliver/store：合法持有者到达对象目标语义点，成功完成会话后更新归属/终局状态。
- join/leave：替补必须先加入，再让旧持有者离开；任何一次操作都不能降到最少持有者以下。
- 被拒绝或失败的会话不会改变对象状态。未知控制器异常保留 consumed request 并隔离后续新调用，禁止换 ID 静默重试或封装成成功 checkpoint；由宿主协调恢复已登记检查点后才清除隔离。

宿主注入 `ControllerObservation`（机器人、语义点、当前 tick、真实到达结果及证据摘要）和 `SkillSessionResult`（请求、会话、对象、机器人集合、操作、点位、tick、终态和证据摘要）。执行器在会话前后读取到点证据并绑定全部身份。调用者提供 bool 成功、计划中的命令或锁 grant 均不构成这些证据。

`PickPlaceExecutor` 默认只走逻辑运行时，无运行时就失败，不调用 native backend 兜底；单凭旧 `idealized_semantics=True` 不再制造成功。旧原生操作代码作为独立物理实验兼容实现保留，只有宿主 `physical_object_experiment_authorization` 才能选择。

公开 `eai.manipulation.pick_place` schema/provider 采用 `operation/object_ref/semantic_point`，可选 `robot_ids` 表示系统自主选择的联合会话成员，必须唯一且包含请求机器人；省略则单机调用。公开入口拒绝目标几何、物体质量覆盖及成功声明，宿主观测才具有裁决效力。物理诊断编译器不再使用公开逻辑 schema，也不能通过请求参数启用。

## 联合持有、oracle 与失败

联合拾取要求同一会话同时覆盖至少登记数量的不同机器人，不能拼接两次排他 pick。运输期间每拍调用宿主 session monitor 并记录完整持有集合；缺拍、缺会话或畸形记录均 fail-closed。监测先验证整组再原子更新，避免半写持有状态。持有中断标记 interrupted，不能随后补写完成来恢复成功。

oracle 消费逻辑持有区间和成功会话事件；容量、互斥、身份、批次集合、位置占用和安全判定继续使用评价器账本。不从物理碰撞、task-object prim、锁服务 grant/deny 派生这些事实。账本状态与系统输出分离；本模型不替系统预分配联盟。

常驻部署池不得按当前任务抓取/载荷/跨层适配性筛选。名册保留真实能力与限制，不生成任务推荐、必选机器人或预定联盟。能力不匹配只在被测系统实际调用时导致执行失败并记录，不替算法选机或改派；不能通过伪造能力信息制造难度。

## checkpoint/replay

逻辑对象 checkpoint 包含定义/部署身份摘要、逻辑归属、持有者、会话、请求去重、事件与时钟绑定，无位置几何、prim、attachment 或对象刚体。恢复必须提供外部登记的 checkpoint 摘要，并校验持有者所属会话和部署池。

`benchmark.logical_object_replay_v1.register_logical_object_replay()` 注册独立 `logical_objects` participant，使用既有因果 replay registry 的逐位 readback 对账。机器人 world/controller、clock、skill、task、algorithm、transport/队列、locks、events、rng 参与者仍保留；任务物体取消物理状态不等于取消机器人本体和导航物理恢复。

## room06 落地与证据边界

六本书各 0.2 kg，三个二楼书架各两本，重物 2.2 kg/至少双持有；七个独立逻辑 ID。`room06_task_objects` 仅生成语义布置记录，simulator configs 恒为空；probe 不再修改场景添加任务物体。source 校验完整 package 与逻辑定义，collector 从 host 账本采样并核验成功会话，不再查 USD prim 或 attachment readback。

CPU 测试证明接口/状态机规则；真实 GPU controller/session producer 仍须接入，不能用测试回调替代真实观测。旧 GPU scene-load 和失败档案原样保留，不升级证据。所有正式资格门持续 false。
