# 楼层、地标与节点路线

`TopologyDocument` 新建产品默认使用 `eai.navigation.topology` v2。
地面和空中分别保存自己的地图，JSON schema 相同。`domain` 仅作为编辑器和
场景几何校验的提示；运行时距离语义由注册 Adapter 的 `NavigationProfile`
决定：地面 XY、空中 XYZ。

## 静态 USD 输入

在资产目录放置 `scene.json`：

```json
{
  "schema": "eai.topology.usd_scene",
  "version": 1,
  "scene_key": "office",
  "usd_path": "office.usd",
  "levels": [
    {"id": "1f", "label": "First floor", "support_z_m": 0.0},
    {"id": "2f", "label": "Second floor", "support_z_m": 3.2}
  ],
  "support_mesh_paths": ["/Office/Floors", "/Office/Stairs"]
}
```

可选 `semantic_targets` 提供外部目标绑定；名称和别名也可直接标注在节点上。
语义区域、对象、交互位置和命名路线由同一个导航路由器解析；入口和命名路线
绑定属于可执行地图契约。
`level_view_hide_paths`、`level_view_cut_z_m` 只改变编辑视图。USD adapter 使用
静态碰撞三角网格，拒绝运动或动画支持面。

```bash
python tools/topology_authoring/author.py \
  --scene-key office \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root /path/to/assets --output-root /path/to/package
```

`--headless` 只校验；`--domain aerial` 使用空中 XYZ 放点和走廊校验。
USD adapter 需要 OpenUSD，交互编辑器需要 Isaac Sim。

## 编辑与摘要

选择楼层会过滤节点，并把地面点击投影到对应支持面；**All Levels** 显示全图。
节点选择使用 XYZ 与楼层，切换楼层会取消未完成预览。楼层只是可选作者分组，
不参与运行时距离、选路或到达判断。

节点包含 XYZ、稳定 ID、可选目标绑定、名称、别名、楼层和 `heading_rad`。
键入位姿、拾取航向、撤销重做及保存加载都保留这些信息；航向规范为弧度。
边只需端点和方向。每个实际弯道都要创建节点，polyline 不进入控制器。
地面即使面对叠层节点或纯垂直边也只计算 XY，空中计算 XYZ；作者和运动控制器
负责保证节点间的真实通行条件。

历史产品里的 traversal label 和 controller capability 不影响 Dijkstra 或准入，
新边无需这些字段。重复目标绑定可被读取，但匹配多个节点的请求会返回歧义错误。
精确节点 ID 优先于绑定，建议使用唯一且必要时带楼层的引用。

`route_graph_digest` 包含节点、目标绑定、有向边和语义字段，忽略数组顺序和
装饰性 polyline；完整 `product_digest` 另行冻结编辑字段。冻结拓扑绑定场景和
来源摘要。replan 使用同一绑定图，换图需要新任务。v1 产品仍可读取并保留原版本。

## 预览与执行

编辑器预览可使用精确引用或唯一名称/别名，按 `domain` 提示选择预览 metric。
程序调用 `resolve_landmark_route` 时显式传入 Profile，CLI 使用 `--metric xy/xyz`：

```bash
python -m tools.topology_authoring.route --topology /path/to/topology.json \
  --origin lobby --destination conference --metric xy --output /path/to/route.json
```

`--origin-level`、`--destination-level` 用于消除名称歧义；`--list-landmarks`
列出候选。预览只用于检查。运行时请求只接受 `target_ref`，由 Provider 内部
解析节点、计算 Dijkstra，并核对实测起点。终点航向只在 Profile 支持时生效。

`NavigationExecutor.prepare()` 将当前节点交给注册的控制器 Adapter，宿主统一
推进一次物理 tick，再由 `after_step()` 检查实测进度、速度和停止反馈。
Go2 部署诊断也使用这一接口：

```bash
python -m tools.topology_authoring.deploy \
  --scene-key office --asset-root /path/to/assets --topology /path/to/topology.json \
  --origin lobby --destination conference --output-root tmp/office-route \
  --controller GO2_MIXED_STAIRS_CFG --device cuda:0
```

控制器负责步态与地形运动；地面到达只核对 XY 和可选航向，根部 Z 作为实测信息
记录。姿态检查可终止摔倒机器人。诊断核对真实速度、停止确认、来源一致性及
quarantine，地图预览通过不能替代原生物理验证。
