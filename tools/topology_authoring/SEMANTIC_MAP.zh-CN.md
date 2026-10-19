# 可编辑语义拓扑地图

统一入口仍是 `tools.topology_authoring.author`。场景几何由 adapter 提供；房间、区域、对象和任务交互点保存在同一个可重开的 `topology.json` 中。house、办公室、仓库等共用这套编辑与保存契约，场景差异放在 adapter 和数据中。

## 地图结构

| 信息 | 保存方式 | 用途 |
| --- | --- | --- |
| 楼层 | `levels`：ID、名称、`support_z_m` | 显式高度与楼层过滤 |
| 导航点/边 | `nodes`、`edges`，XYZ、方向、walk/stairs/ramp 与能力要求 | 机器人实际跟随的路线 |
| 房间/区域 | `semantics.regions`：水平多边形、向上高度、楼层、入口节点 | 标注房间位置与三维范围 |
| 对象 | `semantics.entities`：类别、名称、位置、所属区域与自定义属性 | 桌子、设备等可扩展事实 |
| 命名路线 | `semantics.routes`：有序节点和边 ID | 可编辑的房间间、跨层路线 |
| 任务交互位置 | `semantics.interaction_poses`：所属对象/区域、XYZ、yaw、可选接近节点 | 后补抓取、放置或观测位置 |

语义扩展独立版本化：`schema: eai.scene.semantics`、`version: 1`，包含上述四个数组和 `extensions` 对象。不带此扩展的旧拓扑 v1/v2 文件保持原有格式；未知语义版本拒绝加载。坐标为 `scene_world`、米、Z 向上，位置不接受 NaN、Infinity 或布尔值。

区域目前是水平多边形向上拉伸的棱柱；不同楼层分别绘制区域。三维楼梯通过 XYZ 导航节点表达，不用倾斜房间体积代替。每条命名路线必须沿真实边依次连接，符合单向约束和遍历类型。

## 编辑流程

1. 加载场景，在 **Add / Edit Floors** 中填写楼层 ID、名称和支持面高度。旧 v1 地图可升级，节点必须能唯一匹配楼层。
2. 在各楼层创建并命名房间入口、楼梯底端/顶端等导航点。普通节点绑定楼层；楼梯中间点属于过渡点。
3. 在 **Rooms / Regions / 3D Routes** 填写区域 ID、名称、类别、高度和入口节点，点击 **Draw Region**，依次点击地面边界，最后 **Confirm Region**。不要重复闭合顶点。**Undo Vertex** 删除最后一次点击，取消操作清除预览。
4. 选择现有起终点 ID，使用 **Draw walk / stairs / ramp** 点击中间航点，再 **Confirm Route**。stairs/ramp 自动切到 All Levels，保留点击所得的真实 XYZ。弯道、楼梯转角和平台都应明确设点；整条路线作为一次可撤销操作提交。
5. **Save Draft** 保存草稿，**Validate** 检查，**Generate** 输出正式地图。未确认的预览不能生成产品。重开地图后继续编辑，后续任务再增加对象、抓取位置等。

地图显示紫色区域棱柱、黄色对象、粉色交互位置；walk 绿色、stairs 橙色、ramp 蓝色。楼层过滤按区域/对象归属继承，All Levels 可查看跨层路线。绿色导航点和红色预览延续旧编辑器约定。

**Editable Semantic Records** 可选中已有记录查看和修改 JSON；提供 New Entity、New Interaction Pose、New Region JSON 模板。稳定 ID 不能直接改名，需要创建新记录并更新引用。删除被引用的区域、对象、导航点或边会被拒绝；删除命名路线记录保留底层图。Undo/Redo 和重载清空记录编辑框，防止旧文本覆盖恢复后的内容。

任务位置可以位于桌面高度；`node_id` 是机器人接近该对象的地面导航点，二者不必同高，但归属楼层必须一致。当前交互朝向是可选 `heading_rad`，范围 [-π, π]；额外的末端姿态、夹爪和任务参数可放入 `properties`，本工具不自动执行抓取。

## 两层 house 示例

在准备好的 Isaac/OpenUSD Python 环境运行；目录必须是新的。示例生成两层支持面、20 级实心台阶、两个房间标注和一张桌子的语义记录，初始不含抓取位置。这是合成演示资产。

```bash
python -m tools.topology_authoring.examples.two_floor_house \
  --output-root /tmp/semantic-house-demo

python -m tools.topology_authoring.author \
  --scene-key synthetic_house \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root /tmp/semantic-house-demo \
  --output-root /tmp/semantic-house-edited \
  --draft /tmp/semantic-house-demo/topology.json
```

命令行 `--draft` 可读取草稿或正式地图；加 `--headless` 可验证重开而不启动 Kit。界面 Load Draft 读取当前输出目录的 `authoring/navigation/ground/topology.draft.json`。正式输出为同目录 `topology.json`。同源 Load Source 保留语义地图，来源摘要改变则拒绝套用旧地图。

在示例中后补桌面抓取位置：New Interaction Pose，然后替换 JSON 为：

```json
{
  "id": "pose:table_grasp",
  "kind": "grasp",
  "label": "桌面抓取位置",
  "owner_id": "object:table",
  "level_id": "ground",
  "position_xyz": [-2, 0.5, 0.85],
  "heading_rad": 1.57,
  "node_id": "living",
  "properties": {"task": "pick"}
}
```

点击 Apply Record，再保存/生成。添加或调整该记录不改变已有 nodes/edges/levels 和导航 `route_graph_digest`；完整 `product_digest` 会变化，以便追踪地图修订。

## 程序接口与边界

`TopologyDocument` 提供 `upsert_semantic`、`remove_semantic`、`replace_semantics` 和 `add_waypoint_route`，共享撤销/重做和原子保存。`semantic_inventory` 返回分类清单，`resolve_semantic` 按稳定 ID/名称/别名解析并拒绝歧义，支持继承楼层过滤；`point_in_region` 检查是否在标注棱柱内。

导航会先消费语义绑定，再在可执行图上选路：区域解析到入口节点；对象优先解析到所属交互位置的接近节点，否则使用所属区域入口；交互位置使用可选的接近节点；命名路线限定为其有序节点和边。运行时仍沿真实导航图执行，因此作者需要确定航点间距并保证直线段可通行。后补对象和交互位置的几何修改不会改变导航图摘要；修改区域入口或命名路线绑定会改变摘要。场景无关指数据和编辑接口通用；真实几何检查依赖 adapter。内置 USD adapter 适用静态、米制、Z 向上、三角化碰撞支持面；支持面连续性检查不等于完整机器人碰撞/净空认证。

相关回归测试在 `tools/validation/test_semantic_topology_authoring.py`，包含持久化、后补任务时路线不变、能力与方向、引用安全、原子失败与历史、三维绘制回调和 CLI 重开。原生 Kit 检查命令：

```bash
python -m tools.validation.semantic_topology_editor_smoke \
  --asset-root /tmp/semantic-house-demo \
  --output-root /tmp/semantic-house-native-check
```

该检查调用真实 UI 回调并保存、生成、重载和渲染；面板截图使用相同构建函数的隔离原生窗口，不证明操作系统鼠标投递或机器人实际爬梯。实际验证记录见 [SEMANTIC_MAP_VALIDATION.md](SEMANTIC_MAP_VALIDATION.md)。
