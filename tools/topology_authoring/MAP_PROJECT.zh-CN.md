# 统一语义地图工具

`map_project` 管理地图工程；`map_server` 提供本地浏览器编辑界面。两者复用
`TopologyDocument`，无需启动 Isaac Sim。底图、语义和拓扑通过同一次保存更新。

## 打开已有地图

### 一次启动 Isaac Sim 和网页（推荐）

发布包会在 `usd/scene/<分类>/<场景>/semantic_map/` 发现 30 个场景地图，默认
打开 revised office 工程。直接在终端执行：

```bash
cd <repository-root>
python tools/topology_authoring/launch.py
```

入口会打开 Isaac Sim 和 `http://127.0.0.1:8766/`，可用 `--port` 指定其他端口。
无需向 Script Editor 粘贴代码。
网页先选择 USD 分类，再选择场景名；工具自动打开对应根 USD、适配视角、启用
Camera Light 并连接编辑器。等待顶部显示“Isaac Sim 场景已同步”后编辑。
Isaac 面板使用英文，两端共享当前图、工具、楼层和已选拓扑元素。

无需依赖本机 `artifacts/` 绝对路径。其他机器可显式指定自己的目录：

```bash
python tools/topology_authoring/launch.py \
  --catalog /path/to/map-catalog --scene living_office --asset-root usd --port 8770
```

USD 来源显示为相对 `usd/` 的 `scene/Living and public space/office/office.usda`，
根据 `--asset-root` 解析；只打开记录中指定的根文件，不替换为子资产。
`--check` 只检查全部场景绑定，不启动 Isaac；`--no-browser` 不自动打开浏览器。
切换前须保存或放弃网页编辑；Isaac 中真实 USD 的未保存修改也会阻止换场景，
请先在 Isaac 保存或放弃。退出终端程序会关闭该入口启动的服务和 Isaac。
独立 `map_server` 不控制 Isaac 场景加载；联动编辑请使用 `launch.py`。

### 只打开网页

从仓库根目录，在包含 numpy、Pillow、shapely 的 Python 环境执行：

```bash
python -m tools.topology_authoring.map_project import \
  --source /absolute/path/to/scene-map --project /absolute/path/to/new-map-project
python -m tools.topology_authoring.map_server \
  --project /absolute/path/to/new-map-project --port 8765
```

打开 `http://127.0.0.1:8765/`。使用安装了 Isaac Sim 依赖的 Python 环境。
输入目录包含 `topology.json` 及每层 `<level-id>-semantic-grid.npz`，兼容已有
30 场景批处理。工程目录必须是新目录；原始地图和 USD 不改写。可选的
`semantic-footprints.json`、`semantic-manifest.json` 作为来源证据保留。

批量导入并在界面切换场景：

```bash
python -m tools.topology_authoring.map_project import-batch \
  --source /absolute/path/to/30-scene-batch --catalog /absolute/path/to/new-catalog
python -m tools.topology_authoring.map_server \
  --project /absolute/path/to/new-catalog/living_home_02 \
  --catalog /absolute/path/to/new-catalog
```

`catalog.json` 记录逐场景结果；任何导入失败均返回非零退出码。

## 编辑

统一入口提供点、边、语义点三种工具；当前工具只编辑对应类型。连线工具可选择节点
作为端点，但不修改节点属性。图层分为地面拓扑、空中拓扑和项目共享的语义点。
参考图层通过显示开关开启，不参与当前工具命中。区域及旧语义类型已退出统一编辑入口。

地面图按工作楼层显示，保留实际 XYZ；楼梯或坡道的过渡节点可不绑定楼层。
空中节点和语义点始终显示所有高度，参考底图选择不筛选点。新点高度默认 Z=1.5 米，
空中节点与语义点分别记忆新建高度和默认模式。新点不绑定地面楼层；旧空中节点的楼层
元数据兼容保留，但不参与显示过滤。

每点支持 `2.5d`／`3d`。2.5D 拖动只改 XY、保持该点 Z；属性始终允许显式输入 Z。
3D 在 Isaac 中提供 XYZ 手柄，网页始终在 XY 画布拖动。切换模式不移动点或改变引用。
边工具依次选两个当前导航图节点，预览后确认。拖动整条边在 XY 平移两端节点及整条
折线，其他关联边只更新受影响端点；各点 Z 不变。地面跨层边使用楼梯／坡道。

语义点字段为稳定 ID、`position_xyz`、`label`、`kind`、`description`、`aliases`、
`properties`、`edit_mode` 和 `navigation_bindings`。后者是任意数量
的 `{domain: ground|aerial, node_id}`，按图域和节点 ID 规范化排序并校验节点存在；同一
语义点可跨地面/空中图绑定。绑定只引用导航图节点，不引用 USD 或 Isaac Prim。
语义点不是导航节点，不参与连线或直接最近点匹配；导航请求命中稳定语义点 ID 后，按
机器人绑定图域过滤 `navigation_bindings`，以当前图的可达路线代价选择最短节点。没有
当前图绑定或候选均不可达时失败，不跨图域、不回退语义点坐标。

一次拖动松手提交一个事务，可一次撤销。Esc 取消预览；两端共享工具、图层、选择、
可见性及新点设置。显示设置按场景保留于当前服务会话，不写入导航产品。请求携带
场景身份、上下文代次、活动图层、图域及预期修订。过期请求返回 409，客户端保留草稿
供明确重试；换图、断连和换场景取消旧手势。Isaac 预览及叠加只写 session layer。

地面导航使用 ground/XY，空中导航使用 aerial/XYZ；2.5D 只影响编辑操作。
机器人必须已在最近节点到达半径内，否则返回 `origin_not_reached`，不会自动接入离图起点。

## 保存与产物

```text
project.json                  唯一索引，current_revision 固定为 current
base/                         原始几何与标签参考栅格，带摘要
current/
  topology.json               地面节点和边
  topology-aerial.json        空中节点和边
  semantic-points.json        独立共享语义点（schema version 2）
  semantic-footprints.json    兼容语义导出，清理后旧集合为空
  semantic-footprints.geojson 拓扑边的兼容 GeoJSON
  level-000-semantic-grid.npz 几何及当前实例/区域栅格
  level-000-layer.png         几何底图
  level-000-semantic-map.png  拓扑预览
  manifest.json              navigation_products、semantic_point_product 及 SHA-256
```

manifest 沿用版本 2 并增加 `semantic_point_product` 文件映射。旧工程没有该字段时
加载为空语义点集合，首次保存增加第三份产品。完整工程摘要包含语义点；两图的
`route_graph_digest` 不受语义点编辑或 2.5D／3D 模式切换影响。

保存先完整生成三份数据与派生文件，随后在写锁内原子交换 `current/` 并更新索引。
失败时旧目录和旧索引保持有效；工程级撤销/重做覆盖两份图和共享语义点。
磁盘锁及索引摘要拒绝覆盖另一进程的新保存。旧 `revisions/` 工程首次成功保存时迁移到
`current/` 并删除旧目录；不要直接修改派生文件。

语义点导航绑定的变化计入 `product_digest` 和独立 `semantic_binding_digest`，不改变
两图的 `route_graph_digest`。绑定节点在解绑前不能删除或清空所属拓扑。

## 清理已移除的数据

先预演，确认逐图统计后使用 `--apply` 覆盖当前包：

```bash
python -m tools.topology_authoring.cleanup --asset-root usd --report /tmp/map-cleanup-preview.json
python -m tools.topology_authoring.cleanup --asset-root usd --apply --report /tmp/map-cleanup-applied.json
```

命令清空当前两图的 `regions/entities/interaction_poses/routes`，重建旧标注派生文件。
所有节点、边、独立语义点、几何底图和作者 USD 保留。不自动将旧物体转换为
语义点。每图单独原子覆盖并报告迁移前后保存标识；重复执行不会生成无效保存。

## Python 与命令接口

```python
from tools.topology_authoring.map_project import MapProject

project = MapProject('/absolute/path/to/map-project')
project.edit('add_node', position_xyz=[1.2, 3.4, 0.0],
             level_id='ground', label='操作入口')
project.edit('add_node', domain='aerial', position_xyz=[1.2, 3.4, 2.0],
             edit_mode='2.5d', label='空中入口')
aerial = project.document_for('aerial')
revision_directory = project.save()
```

统一 `edit` 缺省操作地面图，通过 `domain='aerial'` 操作空中图。
导航操作支持 `add_node/move_node/move_node_xy/update_node/remove_node`、
`add_edge/update_edge/remove_edge/translate_edge_xy`。
`move_node_xy` 保留 Z；`translate_edge_xy(id, delta_xy)` 一次移动边和端点。

语义点通过 `add_semantic_point/update_semantic_point/move_semantic_point_xy/`、
`move_semantic_point/remove_semantic_point` 编辑；
导航绑定使用原子操作 `set_semantic_point_navigation_bindings`，传空数组表示全部解绑。
旧语义点产品中的 `isaac_binding` 在读取时自动丢弃，下一次保存会导出不含该字段的新产品。

`/api/editor/context` 接收 `active_layer`、`editor_tool`、`active_level`、
`reference_level`、`visible_layers`、`visible_types` 和 `new_point_settings`；
`/api/edit` 保留导航 `domain=ground|aerial`，语义点不创建第三种导航域。
旧区域/物体操作仅为历史 Python/CLI 兼容保留，统一 HTTP 界面拒绝这些操作。

相同操作可以写入含 `operation` 和参数的 JSON 文件：

```bash
python -m tools.topology_authoring.map_project edit \
  --project /absolute/path/to/map-project --command-file /absolute/path/to/edit.json
python -m tools.topology_authoring.map_project validate --project /absolute/path/to/map-project
python -m tools.topology_authoring.map_project create \
  --project /absolute/path/to/new-project --scene-key custom_scene \
  --bounds -10 -10 10 10 --resolution 0.1
```

新建时可用 `--levels /path/to/levels.json` 指定楼层数组。空底图全部标为未知，
仍可标注节点、连线和语义点，但不能凭空绑定障碍物。新 USD 的几何采集使用既有
捕获/栅格流程，编辑工具不从单张图片推测未观测几何。

## 边界

工具不自动修复导入地图的几何错误；原有待确认状态随导入保留。三维视图表示
XYZ 关系，不能证明机器人可走。输出继续声明 `robot_validated=false`，物理净空、
动力学、楼梯能力和任务成功需单独验收。没有在线机器人同步或抓取执行功能。
已有 Isaac `author` 编辑器保留；当前统一工程提供 Python/CLI/本地 HTTP 接口，
尚未注册为外部 MCP 工具。
