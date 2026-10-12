# 多楼层拓扑编辑

本文说明如何为静态、米制、Z 轴向上的 USD 场景，通过描述文件编辑版本 2
拓扑。版本 2 显式增加楼层、语义地标、过渡节点和带通行类型的边；工具不会从
网格几何自动推断楼层，也不会仅凭编辑结果证明机器人能够实际通过路线。

## 准备场景描述文件

在场景资产目录中创建 `scene.json`。`usd_path` 可以是绝对路径，也可以相对描述
文件目录。楼层 ID 是作者维护的稳定标识；`support_z_m` 是场景世界坐标中以米为
单位的地面支持高度。

```json
{
  "schema": "eai.topology.usd_scene",
  "version": 1,
  "scene_key": "office",
  "usd_path": "office.usd",
  "levels": [
    {"id": "1f", "label": "一层", "support_z_m": 0.0},
    {"id": "2f", "label": "二层", "support_z_m": 3.2}
  ],
  "semantic_targets": [
    {
      "ref": "place:lobby@1f",
      "label": "大厅",
      "kind": "target_entrance",
      "position_xyz": [1.0, 2.0, 0.0]
    },
    {
      "ref": "place:conference@2f",
      "label": "会议室",
      "kind": "target_entrance",
      "position_xyz": [8.0, 5.0, 3.2]
    }
  ],
  "support_mesh_paths": ["/Office/Floors", "/Office/Stairs"],
  "level_view_hide_paths": {
    "1f": ["/Office/Roof", "/Office/SecondFloor"],
    "2f": ["/Office/Roof"]
  },
  "level_view_cut_z_m": {
    "1f": 2.8,
    "2f": 6.0
  }
}
```

`semantic_targets` 以及两类视图辅助配置均可省略。省略
`support_mesh_paths` 时，适配器会使用场景中受支持的网格几何。
支持源要求是静态碰撞三角网格；动态/运动学刚体以及随时间变化的变换、顶点或
拓扑会被拒绝，避免使用冻结几何误判实时支持高度。
`level_view_hide_paths` 保存仅在编辑会话层隐藏的绝对 USD prim 路径。如果一个
网格横跨多个楼层，还可使用适配器支持的 `level_view_cut_z_m` 映射进行仅影响
显示的几何剖切。这些视图操作不修改源 USD、碰撞几何、支持面提取结果或运行时
射线代理。

不打开交互式 Kit 窗口即可检查描述文件与来源摘要：

```bash
python tools/topology_authoring/author.py \
  --scene-key office \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root /path/to/office-assets \
  --output-root /path/to/prepared-package \
  --headless
```

删除 `--headless` 即可打开编辑窗口。USD 适配器仍要求环境提供 OpenUSD；交互式
编辑要求 Isaac Sim。

## 版本 2 拓扑契约

生成产品使用 `schema: eai.navigation.topology` 和 `version: 2`。以下示例省略了
既有的来源摘要、锁定、polyline 和 profile 字段，只展示多楼层关键字段：

```json
{
  "schema": "eai.navigation.topology",
  "version": 2,
  "scene_key": "office",
  "coordinate_frame": "scene_world",
  "meters_per_unit": 1.0,
  "up_axis": "Z",
  "levels": [
    {"id": "1f", "label": "一层", "support_z_m": 0.0},
    {"id": "2f", "label": "二层", "support_z_m": 3.2}
  ],
  "nodes": [
    {
      "id": "lobby",
      "kind": "target_entrance",
      "position_xyz": [1.0, 2.0, 0.0],
      "level_id": "1f",
      "label": "大厅",
      "aliases": ["入口"],
      "target_refs": ["place:lobby@1f"]
    },
    {
      "id": "stair_mid",
      "kind": "transition",
      "position_xyz": [4.0, 3.0, 1.6],
      "level_id": null,
      "label": "主楼梯中点",
      "aliases": [],
      "target_refs": []
    }
  ],
  "edges": [
    {
      "id": "lobby_to_stair",
      "from": "lobby",
      "to": "stair_mid",
      "directed": true,
      "traversal_mode": "stairs",
      "required_controller_capabilities": ["rough_terrain"]
    }
  ]
}
```

每个普通节点必须引用已声明楼层，且 Z 坐标与该楼层支持高度相差不超过 0.20 m。
只有 `kind: transition` 的节点可以使用 `level_id: null`。每条边都必须具有非零
XY 长度。`walk` 边必须位于同一已声明楼层，且不能连接未分配楼层的过渡节点。
`stairs` 和 `ramp` 用于通行准入；通行类型本身就是一项隐式控制器能力，此外还
必须满足 `required_controller_capabilities` 中列出的能力。

不同楼层的普通节点高度范围（包括楼层支持高度）必须相隔大于 0.40 m，防止
0.20 m 到达容差重叠。相近的楼梯小平台应建成 `transition` 节点。

版本 2 中的稳定 `target_refs` 在所有节点间必须唯一。面向人的 label 和 alias
可以重复，但若没有楼层过滤，同名查询会因歧义而被拒绝。可执行请求应优先使用
带楼层限定的引用，例如 `place:conference@2f`。

## 编辑单层或整栋建筑

编辑器默认选择第一个已声明楼层，并显示其精确支持 Z。选择某一楼层后，点击会
投影到该层附近，叠加层和拾取也按该层过滤。选择 **All Levels** 可检查完整图，
或放置不归属于单一楼层的过渡节点。切换楼层会取消未完成的节点或边预览，避免
一次点击操作无意跨越不同视图。

普通节点要求当前已选择楼层。楼梯或坡道的中间点使用 **Transition Node**；在
All Levels 模式确认时，其 `level_id` 为 `null`。相邻楼层视图仍会显示相关过渡
节点和跨层连接。对于 XY 完全相同的上下层节点，编辑器通过完整 XYZ 距离和当前
楼层过滤来区分。

选中节点后可编辑 XYZ、楼层 ID、地标名称、别名和稳定目标绑定。选中边后可指定
`walk`、`stairs` 或 `ramp`，并填写额外控制器能力。若上下行并非都可执行，应
使用单向边。

### 自定义英文地点与朝向

仍使用本工具 `tools/topology_authoring`，不需要另开一个地图工程。
例如在 1F 标记 `pantry_1f`，在 2F 标记 `meeting_room_2f`。Label 和 Aliases
直接保存在地图节点里，即使场景描述没有预登记 semantic_targets 也可查询。
外部 target ref 绑定继续受场景权威清单校验，作者自定义地点使用稳定 node ID
作为执行标识。重复名称必须显式指定楼层；建议使用带楼层后缀的唯一英文名称。
**Named Landmarks** 列表展示已标记地点及楼层：**Select** 切到对应楼层并选中，
**Origin / Destination** 把稳定节点 ID 填入路线查询，避免重名歧义。

在 XYZ 输入框填写世界坐标（米），点击 **Typed Node Preview** 创建预览；
也可先在视口点选支持面。选择对应楼层并填写 Label / Aliases 后确认节点。
朝向输入以度表示：0° 朝世界 +X，90° 朝 +Y；留空不约束朝向。点击
**Pick Heading** 后，再在视口点击一个方向点，取两点的 XY 方向作为 yaw。
方向点的 Z 不改变节点坐标。方向箭头和通路的单向/双向箭头含义不同。

节点可选字段 `heading_rad` 以弧度保存（范围 [-π, π]），并参与地图摘要。
XYZ、名称、楼层、别名和朝向均随 Save Draft / Generate 保存，重载后保留，
朝向编辑支持 Undo / Redo。路线导出包含与 XYZ 一一对应的 `route_headings_rad`，
终点朝向也放入 `skill_parameters.target_yaw`。中间点朝向只作为作者元数据，
执行时仅在终点对齐朝向；如果转向中偏离位置容差，会重新跟踪终点位置。
起终点相同的查询仍不证明机器人朝向已满足要求，必须由执行器读取真实位姿判定。

上层任务将由用户后续确定的固定 JSON 输入对接；本工具不添加自然语言解析器，
也不在本阶段规定该 JSON 格式。目前可直接通过英文起终点调用既有解析接口：

```bash
python -m tools.topology_authoring.route \
  --topology /path/to/topology.json \
  --origin pantry_1f --destination meeting_room_2f \
  --capability walk --capability stairs \
  --output tmp/route.json
```

执行器只跟随节点端点，不跟随装饰性 polyline 的弯折。楼梯和坡道必须放置足够
的节点以形成真实 XY 前进，并把每个必要转弯编码到节点图中。平面地面控制器不
接受纯竖直边。

草稿和产品写入 `authoring/navigation/ground/`。`scene.json` 中的 levels 是版本
2 文档的初始状态，因此撤销操作不会退回到与场景不匹配的版本 1 文档。

## 预览并导出地标路线

在 **Landmark Route Preview** 中输入精确 target ref，或无歧义的节点 ID、label
或 alias。控制器能力以逗号分隔并按实际能力填写，例如
`walk, stairs, rough_terrain`。预览调用与导航准入相同的纯解析器和图摘要。查询
失败或图已变化时，旧预览会被移除，不会继续显示过期路线。

点击 **Export Preview** 会原子写入：

```text
authoring/navigation/ground/route.preview.json
```

其中记录解析后的节点 ID、XYZ 点、稳定引用和图身份。这是规划/检查产物，不是
运行成功回执。

无需启动 Isaac Sim 也可执行同样的查询：

```bash
python -m tools.topology_authoring.route \
  --topology /path/to/topology.json \
  --list-landmarks

python -m tools.topology_authoring.route \
  --topology /path/to/topology.json \
  --origin place:lobby@1f \
  --destination place:conference@2f \
  --capability walk \
  --capability stairs \
  --capability rough_terrain \
  --output /path/to/route.json
```

只有当 label 或 alias 需要显式楼层消歧时才使用 `--origin-level` 和
`--destination-level`。完整参数见
`python -m tools.topology_authoring.route --help`。不可达或控制器能力不满足的路线
不会通过准入。

模型侧先接收 `landmark_inventory(document)` 的作者标记，再输出明确的
`origin_ref`、`destination_ref`（例如一楼大厅、三楼会议室对应的稳定 ID）。
`resolve_landmark_route` 返回的 `target_ref` 和 `skill_parameters` 可直接组成
既有 `eai.navigation.ground_navigation` 请求；host 仍须填写 robot/task/request
ID、最新 `world_state_seq` 和 `intent`。准入时另查实测起点，不能用声明起点代替
机器人当前位姿。图摘要固定后，不接受运行中无记录的换图。

executor 注入 `support_height_reader(robot_id)` 读取机器人当前脚下支持高度，
而非把 root Z 减去固定机身高度。地面控制仍仅输出 `[vx, 0, wz]`，高差由已接入
RL 控制器执行。声明 `stairs` 能力只允许路线选择，不代表任意楼梯物理通过。

## 部署诊断

部署 CLI 通过共享模拟器会话执行已声明路线。它要求准备好的 Isaac Sim 环境，
通常还需要 CUDA GPU：

```bash
python -m tools.topology_authoring.deploy \
  --scene-key office \
  --asset-root /path/to/office-assets \
  --topology /path/to/topology.json \
  --origin place:lobby@1f \
  --destination place:conference@2f \
  --output-root tmp/office-route-01 \
  --controller GO2_MIXED_STAIRS_CFG \
  --device cuda:0
```

输出目录必须事先不存在。目录中会写入固定输入、路线进度、原生采样、可选图像和
最终诊断结果。计时、速度、容差、headless、扫描器偏移和 GUI 保持选项见
`python -m tools.topology_authoring.deploy --help`。该命令只产生诊断证据，不会创建
基准任务或正式实验契约。

GUI 截图使用仅渲染接口，并断言截图前后仿真时间不变，防止暂停策略更新却继续
推进物理。诊断记录真实支持高度、姿态、187 条高度射线有效性、终态零命令和
停车速度；翻倒（机身竖直轴与世界竖直轴夹角超过 60°）、采样或截图异常会终止
路线并保存失败结果。零速度是底盘停止请求，不是冻结机器人或自动扶正。
mixed-stairs 的转向限幅为训练范围内的 0.6 rad/s。

此次实际运行结果与本机可直接复制的 GUI 命令见
[多楼层原生验证记录](MULTILEVEL_VALIDATION.md)。

## 版本 1 兼容性与能力边界

版本 1 产品继续保持原有稀疏节点/边序列化、XY 选路、图摘要和仅 XY 到达判定；
不会自动获得楼层或版本 2 元数据。只有场景描述文件显式提供 `levels` 时，新文档
才初始化为版本 2。

结构校验、来源摘要、支持面投影、路线解析以及编辑器中的绿色预览都不能单独证明
物理可通行。它们不能自动证明机器人外形净空、楼梯尺寸、摩擦、碰撞质量、策略
训练覆盖、扫描器有效性、避障、交通处理，或出生点到首节点的安全接入。支持 Z
表示地面高度，不是机器人 articulation root 的 Z。只有保留测量数据并使用合适
任务评估器的原生运行，才能作为物理成功依据。
