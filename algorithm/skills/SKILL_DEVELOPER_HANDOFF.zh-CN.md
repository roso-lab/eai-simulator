# Skill 开发约定（simulator.skill.v1）

Skill 负责把上层请求编译成可执行的抽象动作，不能伪造物理成功。公共
合同位于 `algorithm/skills/contracts.py`，仿真 Skill schema 位于
`algorithm/skills/simulator_schema.py`。

## 统一节点路线导航

导航唯一 Skill ID 是 `eai.navigation.node_route`。请求必须包含：

```python
SkillRequest(
    request_id="branch/task/0",
    skill_id="eai.navigation.node_route",
    robot_id="robot_1",
    target_ref="dock_A",
    parameters={"intent": "start", "world_state_seq": 42},
)
```

导航请求只接受 `target_ref`，拒绝 `target_pose`、`mode`、控制器 frame、
`horizon_ticks` 和 controller capability。可选字段是 `intent`、
`route_graph_digest`、`origin_ref`、`target_yaw`、`timing` 和 `budget`。
Provider 绑定冻结 `TopologyDocument`，内部解析引用并执行确定性 Dijkstra，
输出一个 `navigation_target`（节点 ID、节点序号、节点 XYZ 和可选航向）。

地面与空中使用不同拓扑产品，但 JSON schema 相同。`metric="xy"` 的地面
Profile 在起点、边权、到达和路线长度中忽略 Z；`metric="xyz"` 的空中
Profile 保留 Z。polyline、通行标签和编辑元数据不参与运行时选路。

## Skill 与控制器解耦

`task.executors.navigation.NavigationExecutor` 只处理 Provider 生命周期、
共享 host tick、robot lease、序号、仿真时间、超时、取消、停止确认和
quarantine。`NavigationControllerAdapter` 读取实时状态，把
`navigation_target` 编译为本控制器命令并报告停止/稳定/失败反馈。

现有适配器分别生成 `base_velocity` 和世界坐标 `goal_position`。接入新
机器人或底盘运动模式时，只需实现并注册新的 Adapter，不修改 Skill、拓扑
Provider 或公共 Executor。

## 拓扑作者工具与验证

`tools/topology_authoring.TopologyDocument` 输出 v2 canonical 产品并兼容读取
v1。route digest 对节点、目标绑定、有向边和语义元数据稳定哈希，忽略数组
顺序与装饰性 polyline。真实 Isaac 验收在空白 Plane 中分别对
Scout、Go2、Iris 各运行三次，全部 9 次成功后清理临时证据目录。

控制器命令和真实物理结果由宿主/evaluator 负责。没有对应 Adapter 或实测
停止证据时必须返回失败并保留私有诊断证据。
