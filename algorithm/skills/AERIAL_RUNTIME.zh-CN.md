# 空中路线与搬运的统一执行接入

本入口把已编排的 `SkillRequest` 接到 EAI 位置控制器。导航和搬运共用场景、拓扑身份、资源租约及 host 的一次物理步进；不生成正式任务、分配机器人或评判物理成功。

## 地图与固定请求

通用拓扑编辑器增加 `--domain aerial`。产品为 `eai.navigation.topology` v2、`domain=aerial`，坐标为 Z-up 世界米制 XYZ。每个转弯/升降点建 node，按飞行顺序连 `fly` 边，可单向或双向。楼层为可选分组，不把空中节点吸附到地面。`label`、`aliases`、`target_refs`、`heading_rad` 随地图保存。

固定 JSON 中的英文地点名通过 `resolve_landmark_route(document, origin_ref=..., destination_ref=...)` 转为稳定节点引用和 `route_graph_digest`。空中执行目前只保证 XYZ；保存的 heading 是元数据，`AerialSkillRuntime` 明确拒绝 `target_yaw`，不会假装已转向。

内置 `EmptyTopologyAdapter` 和 `UsdSceneAdapter.validate_aerial_polyline()` 只检查空中段结构，不认证障碍物净空。作者负责相邻节点间及机器人到起点间可直接飞行。每个转弯必须建 node，装饰 polyline 不改变执行路线。

## 场景启动时绑定

```python
from algorithm.skills.navigation.aerial_topology import FrozenAerialTopology
from task.executors.aerial_runtime import AerialSkillRuntime
from task.resources import ResourceManager
from tools.topology_authoring.product import load_product

document = load_product(product_path, adapter, source)
frozen = FrozenAerialTopology(
    document.to_dict(), scene_key=source.scene_key,
    source_digests=adapter.source_digests(source),
)
resources = ResourceManager()  # 同一场景中的 executor 共用这个实例
runtime = AerialSkillRuntime(
    session.base_env, topology=frozen, resources=resources,
    source_reader=lambda: (source.scene_key, adapter.source_digests(source)),
)
binding = runtime.binding(request)
```

`source_reader` 必须核验真实来源，不应返回硬编码的冻结副本。复杂场景可由 host 根据输入文件变更维护摘要，避免逐 tick 重算大文件。冻结对象保存独立副本，同时记录完整产品和路线图摘要。来源、图身份或开始前的实测绑定发生变化会被拒绝。

完整产品使用 `load_product` 验证，包括作者的装饰 polyline/profile 等字段；
`FrozenAerialTopology` 只检查执行所需的 v2 空中图、显式坐标和来源，再冻结副本。
不要把它当作完整作者产品 loader 的替代品。binding 仅接受创建 runtime 的 env
或其 `SimulatorSession`，每次 start、共享 step 和 receipt 边界重新核验真实 active lease。

`parameters.world_state_seq` 使用当前 `env.sim.current_time_step_index`，后续由 handle 更新。robot、task、target、预算、branch context 等身份保持不变，只有 request_id、intent、当前观测序号和 snapshot 可刷新。导航 hold 也更新单调时钟、计入整体时长预算；不允许通过改预算延长任务。

## 单任务与共享执行

`benchmark.aerial_adapter.AerialLifecycleSkillAdapter(runtime)` 编译初始计划，`AerialTaskExecutor(runtime, max_ticks=...)` 满足既有 `execute(assignment, task, scene=..., plan=..., step=...)` 接口。`step` 使用 host 的 `EaiSessionRuntime.step`，保持原有 evaluator。`EpisodeRunner` 的执行顺序未改变。

并行执行使用 `CooperativeBatchExecutor`，binding factory 返回 `runtime.binding(request)`。外层统一租用 robot/object 并传入真实 lease；各 handle 的 `command()` 仅准备动作，全体动作由 host 合成一次 `step`，随后逐一 `after_step()`。不要让每个 skill 独立推进模拟时间。可运行组合见 `tools/validation/aerial_skill_native.py`。

receipt 需要严格递增的实测时间、双方速度达到静止门槛，以及搬运原生状态清理成功。到达后有时间上限地等待稳定。`command_applied` / `DependencyOutcome.COMPLETED` 只表示执行周期完成，`physical_outcome` 仍为 `unknown`，由外部 evaluator 判定任务成功。

## 停止与恢复

队列写入而未推进物理、异常中断、无法确认停止、携物终止均不能释放租约。场景存在 quarantined lease 时，其他空中任务也不得推进。正常搬运结束恢复碰撞并实测稳定，外层才释放租约。

携物停止保留连接和所有权。host 先隔离场景，在没有其他任务运行时完成安全悬停/落地等明确恢复，再调用 `AerialPickPlaceExecutor.host_release(task_id)`。它重新测量速度、清理自有状态并恢复租约；外部 lease 仍由原所有者 `resources.release(lease)`。布尔声明不能替代速度测量。详见 [搬运指南](aerial_transport/AERIAL_PICK_PLACE.zh-CN.md)。

## 原生诊断

从仓库根目录，在已安装 EAI/Isaac Lab 的 `env_isaaclab` 中运行：

```bash
python tools/validation/aerial_skill_native.py --mode navigation --editor --output tmp/air-navigation-check
python tools/validation/aerial_skill_native.py --mode cycle --output tmp/air-cycle-check
python tools/validation/aerial_skill_native.py --mode retained-stop --output tmp/air-stop-check
```

默认 GUI；`--headless` 可关闭窗口，`--hold-gui` 在检查后保留窗口。输出目录必须不存在。诊断创建临时 plane、台面、Iris 与 5 g 物体，记录实测 XYZ、阶段、租约、碰撞恢复和截图。导航模式两架 Iris 共用主循环；搬运模式走 benchmark 接口；停止模式显式演示隔离后的 host 恢复。数据只用于工具诊断，不构成正式任务/成功率，也不证明其他载荷和控制器已完成适配。

搬运接触点是 Iris 真实机腹表面 `(0,0,-0.04090353)` m，host 容差为 2 mm。
完整顺序为下降接触、粘连、上升、水平路线、下降放置、分离、上升撤离。
停止恢复先在隔离场景中把物体送回源台面，确认稳定后解除连接；单纯静止不等于
物体已有支撑。接触近景另存为 `contact-closeup.png`。
