# 新版抓放 skill 与已有场景接入审计

依据：GitLab 4959e104；公共 schema、`task/executors/pick_place.py`、`task/runtime/logical_objects/ledger.py`，论文 0ffd470f。

公共 skill 是单机 pick/place。宿主共享逻辑对象账本另有 pick/load/receive/place/deliver/store/join/leave；原快速 `DiagnosticRuntime.TaskSpec.object_action` 的 handoff_release/handoff_receive/place_intermediate 是内部协议，不能直接传入公共 skill。因此不批量重命名旧场景动作，也不把兼容运行解释为生产路径已经迁移。

| 场景 | 当前快速轨道 | 新版工程接入判断 |
|---|---|---|
| city01 | 巡检、充电、事故观测与集合，无抓放对象 | 不需要 PickPlace 参数变更 |
| city02 | warning/box 的 pick/place | 快速轨道兼容；接宿主账本时把 object_id/source/destination 映射为五字段定义 |
| factory | pick/place 与交接内部事件 | 保留 release/accept 外部时序，宿主 receive 原子交换；不把内部事件发给公共 schema |
| candy_shop | 单机端点及 T2 交接 | 同上，保留对象连续性与交接条件 |
| living_house | Pot、surface handoff、Sink 中间放置、椅子联合屏障 | Sink 分腿登记操作点；联合椅子需要独立协作会话，不能用单机 PickPlace 代替 |
| room06 | 旧通用 adapter 是阶段任务抽象；另有 room06_task_instance_v2 的五字段对象及 pick/receive/deliver 绑定 | 使用已有生产逻辑对象路径；旧通用 adapter 的成功不证明书本所有权或联合持有已认证 |
| bar | 新适配使用共享 LogicalObjectRuntime | 由 graph-controller 出到点/会话证据；有逐对象身份、P2 交接、联合动作和资源证据 |

新版已迁移 live demo/Isaac-BT 的普通 PickPlace 构造器，未发现遗留的 aerial_pick_place/pick_place_backends Python 活动 import。本次修正全局对象模型文档中仍声称公开入口接受 semantic_point/robot_ids 的旧说明；修正未来 runtime roster 生成器的 provider_version 为 2.0.0。已有机器人 roster/历史收据记录 1.0.0 或旧源码路径时不静默重签、不沿用其资格：应通过 generate_robot_pool / generate_runtime_roster 重新构建候选名册并重新验证证据，历史档案保持原样。

当前主工作区还存在其他协作者的 city01/room06/skill 改动；本次在独立 worktree 验证，集成时保留这些未提交内容。跨场景快速轨道通过只说明其内部协议未退化；新版真实宿主/Isaac 的控制器会话仍需独立验证。
