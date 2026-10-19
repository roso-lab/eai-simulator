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

入口会打开 Isaac Sim 和 `http://127.0.0.1:8770/`。无需向 Script Editor 粘贴代码。
网页先选择 USD 分类，再选择场景名；工具自动打开对应根 USD、适配视角、启用
Camera Light 并重新连接选点。等待顶部显示“Isaac Sim 场景已同步”后选点。
Isaac 面板使用英文，选中对象/发送视口点后网页会跟随其已识别楼层。

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
旧的 `8766` 独立网页不具备自动开关场景功能，请使用新入口的端口。

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

- 选择楼层，在二维底图圈选障碍物并填写名称、类别。只标注圈内的实际障碍格。
  空选择或与其他对象重叠会被拒绝；可重划已有对象，或先删去原语义再重新分类。
- 绘制区域：保存原始多边形，栅格归属裁剪到本层支持格；重叠格由后面的区域
  记录拥有。图中多边形是选择范围，栅格是实际归属。
- 导航点可点击添加或输入 XYZ。边保存方向及 walk/stairs/ramp；每个转弯须设点。
  楼梯中间点使用 `transition` 类型和空楼层，记录真实 Z。
- 名称、类别和属性可修改；抓取/放置/观察位置关联对象或区域，并保留独立高度。
  高级 JSON 支持完整属性扩展。
- 删除语义不删除物理障碍；删除仍被引用的对象/节点会被拒绝，须先更新引用。
- 支持撤销/重做。点击保存统一生成新修订；重开以最近保存版本为准。撤销历史
  只存在当前进程。切换场景前保存或明确放弃未保存修改。

## 保存与产物

```text
project.json                  唯一索引，current_revision 指向最新完整修订
base/                         原始几何与标签参考栅格，带摘要
revisions/<revision-id>/
  topology.json               可重开的语义、节点、边、交互位置
  semantic-footprints.json    对象掩码、语义和原始视觉观察
  semantic-footprints.geojson 语义轮廓、点和实际拓扑边
  level-000-semantic-grid.npz 原始几何 + 当前 instance_id / region_id
  level-000-layer.png         与界面一致的颜色底图
  level-000-semantic-map.png  带标签和拓扑线的预览
  manifest.json              楼层文件映射、修订 ID、输出 SHA-256
```

楼层按序编号，真实 ID 在修订 manifest 中。轮廓保留孔洞和不连续部分；GeoJSON
使用场景局部米制坐标，**不是经纬度**。语义操作不改变占据、支持和未知格。

保存先生成完整目录，最后原子替换工程指针。失败保存不影响原版本，旧修订保留。
界面修订号防止旧请求覆盖新修改，磁盘锁和索引摘要防止另一进程覆盖新保存。
打开时校验导出文件和底图摘要。不要直接改派生文件，应通过工程接口保存。
失败保存的未引用目录保留供排错，工具不自动删除历史。

## Python 与命令接口

```python
from tools.topology_authoring.map_project import MapProject

project = MapProject('/absolute/path/to/map-project')
project.edit('add_node', position_xyz=[1.2, 3.4, 0.0],
             level_id='ground', label='操作入口')
revision_directory = project.save()
```

统一 `edit` 支持 `add_node/move_node/update_node/remove_node`、
`add_edge/update_edge/remove_edge`、`upsert_semantic/remove_semantic`、`bind_obstacle`。
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
