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

联动界面只保留点、连线和绘制区域三种工具，点击已有元素即可选择。
地面图和空中图共享场景、楼层、底图与区域；切换图不会迁移节点或连线。
已有对象、交互点和命名路线仍保存在各自原图的数据中，可通过 Python/CLI 编辑。

- 点：点击空白处新建，拖动修改 XY，属性面板可输入 XYZ、名称与类型。地面图
  保留真实 Z，可用于楼层、楼梯和坡道；楼梯中间点可用 `transition` 和空楼层。
- 空中节点默认 `3d`，点工具可选择新建默认模式，已有节点可独立切换 `2.5d`／`3d`。
  2.5D 拖动保持该节点当前 Z，仍可在属性中输入高度；3D 在 Isaac 可使用 XYZ 手柄。
  切换模式保留 ID、坐标及引用，不同模式的节点可以连线。网页画布始终拖动 XY。
- 连线：依次选择当前图中的两个节点，预览后确认；属性可修改单向／双向，支持删除。
  地面连线可选步行、楼梯或坡道；跨楼层或含未绑定楼层过渡点的边使用楼梯／坡道。
  移动节点会同步更新关联边的端点。
- 区域：逐点绘制，点击首点或“完成区域”闭合。选择区域后可直接拖动顶点；
  点击任意边（含闭合边）再选“在此加点”，在该边投影位置插点。底面 Z 在拖动时
  保持不变，区域高度单独修改。重复顶点、自交及零面积会被拒绝并保留有效版本。
- 一次拖动松手提交一次事务，对应一次撤销；Esc 取消预览或绘制。支持撤销、重做、
  缩放和保存，撤销历史仅存在当前进程。换场景前保存或明确放弃未保存修改。

两端共用当前图、工具、楼层和选择。编辑通过 `/api/edit` 提交，携带图域、场景身份、
上下文代次和预期修订；`/api/editor/context` 更新当前图和工具。并发修订或过期手势
返回 409，客户端刷新权威状态并保留待处理草稿，需确认后重新操作。断连、换图或
换场景会使旧手势失效。Isaac 的叠加与拖动预览写入 session layer。

地面机器人绑定 `ground` 产品并使用 XY 距离；空中机器人绑定 `aerial` 产品并使用
XYZ 距离。2.5D 是编辑模式，不改变空中导航的高度判断。错误图域绑定会被拒绝。
机器人须已在最近节点的到达半径内，否则返回 `origin_not_reached`；工具不自动接入离图起点。

## 保存与产物

```text
project.json                  唯一索引，current_revision 指向最新完整修订
base/                         原始几何与标签参考栅格，带摘要
revisions/<revision-id>/
  topology.json               地面图：语义、节点、边、交互位置
  topology-aerial.json        空中图：独立节点、边及导航引用，共享区域
  semantic-footprints.json    对象掩码、语义和原始视觉观察
  semantic-footprints.geojson 语义轮廓、点和实际拓扑边
  level-000-semantic-grid.npz 原始几何 + 当前 instance_id / region_id
  level-000-layer.png         与界面一致的颜色底图
  level-000-semantic-map.png  带标签和拓扑线的预览
  manifest.json              两图 navigation_products 映射、楼层、修订 ID、SHA-256
```

楼层按序编号，真实 ID 在修订 manifest 中。轮廓保留孔洞和不连续部分；GeoJSON
使用场景局部米制坐标，**不是经纬度**。语义操作不改变占据、支持和未知格。

工程与修订 manifest 使用版本 2，`navigation_products` 明确映射两图文件；区域属性
与几何在两图内一致，`entrance_node_ids` 按图隔离。预览和语义栅格使用地面图。
旧工程按原 `domain` 加载，缺省归地面，另一图初始化为空；首次保存升级索引。
保存先生成包含两图和共享派生文件的完整目录，最后原子替换工程指针。
失败保存不影响原版本，旧修订保留。撤销与重做覆盖两图和区域的完整事务。
界面修订号防止旧请求覆盖新修改，磁盘锁和索引摘要防止另一进程覆盖新保存。
打开时校验导出文件和底图摘要。不要直接改派生文件，应通过工程接口保存。
失败保存的未引用目录保留供排错，工具不自动删除历史。

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
支持 `add_node/move_node/move_node_xy/update_node/remove_node`、
`add_edge/update_edge/remove_edge`、`upsert_semantic/remove_semantic`、`bind_obstacle`。
`move_node_xy(id, position_xy)` 由服务端保留 Z，`move_node(id, position_xyz)` 可显式修改 Z。
`insert_region_vertex(id, index, position_xyz)` 的 index 是起始边顶点，插入在其后，
包括最后一条闭合边；`move_region_vertex(id, index, position_xy)` 保持底面高度。
`update_region(id, label?, kind?, height_m?)` 只修改指定属性，保留原有引用和自定义信息。
语义集合为 `regions/entities/interaction_poses/routes`，记录格式沿用
[语义拓扑协议](SEMANTIC_MAP.zh-CN.md)。`upsert_semantic` 整条替换，更新时保留原属性。
`bind_obstacle` 参数为 `id,label,kind,level_id,polygon_xy,region_id`。

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
仍可标注点、区域和路线，但不能凭空绑定障碍物。新 USD 的几何采集使用既有
捕获/栅格流程，编辑工具不从单张图片推测未观测几何。

## 边界

工具不自动修复导入地图的几何错误；原有待确认状态随导入保留。三维视图表示
XYZ 关系，不能证明机器人可走。输出继续声明 `robot_validated=false`，物理净空、
动力学、楼梯能力和任务成功需单独验收。没有在线机器人同步或抓取执行功能。
已有 Isaac `author` 编辑器保留；当前统一工程提供 Python/CLI/本地 HTTP 接口，
尚未注册为外部 MCP 工具。
