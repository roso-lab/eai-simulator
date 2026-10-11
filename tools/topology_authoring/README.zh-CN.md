# 通用场景拓扑编辑器

工具通过 `--adapter module:Class` 接收场景。所有场景都从空拓扑开始，工具不会自动读取道路、路口或道路中线生成节点和拓扑线。适配器只负责加载场景、语义目标、支持面投影和校验。使用 `--headless` 可在不启动 Isaac Sim 时检查适配器；语义地图不在资产目录时可传 `--semantic-map`。

启动 Isaac Sim 编辑器：

```bash
python tools/topology_authoring/author.py \
  --scene-key warehouse \
  --adapter tools.topology_authoring.adapters.empty:EmptyTopologyAdapter \
  --asset-root /path/to/scene-assets \
  --output-root /path/to/prepared-package
```

具体场景适配器由调用方提供，不随这个通用工具迁移。内置的
`EmptyTopologyAdapter` 可用于检查编辑器，也适合由其他进程提供支持面事实的场景。

视口操作：

- 空白地面左键只创建一个红色节点预览；点击 `Confirm Node` 后节点变为绿色。
- 点击绿色节点进入连线模式，再点击另一个绿色节点生成红色预览线；`Confirm Edge` 后变为绿色。
- `One-way` 和 `Two-way` 控制箭头方向。单向边显示一个箭头，双向边显示相反的两个箭头。
- 右键取消当前红色节点或连线预览；红色对象不会写入正式产品。
- 不存在自动导入的道路、路口或道路中线；所有节点和拓扑线都由用户创建。
- 点击已创建的边可选中它；右侧属性面板可移动/删除节点，删除边，绑定多个目标，编辑方向、profile、clearance 和折线点。

`Save Draft` 保存可继续编辑的草稿，`Validate` 检查拓扑，`Generate` 通过检查后写入 `authoring/navigation/ground/topology.json`。

地面导航只跟踪 node，并按边方向和节点间 XY 距离选择最短路；不使用 edge
polyline 或 profile clearance。每个弯道都需要增加 node 并依次连边，例如
`A → 拐角B → C`。仅修改 `A → C` 的折线点不会改变实际运动路径。
相邻节点之间的直接通行条件和间距由作者保证，运行时没有自动节点间距限制，
也不避障或让行；模拟器物理碰撞正常保留。
