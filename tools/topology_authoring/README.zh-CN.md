# 通用场景拓扑编辑器

推荐从终端用一个入口启动 Isaac Sim 和地图网页：

```bash
cd <repository-root>
python tools/topology_authoring/launch.py
```

默认打开 `http://127.0.0.1:8766/`，按 USD 三大分类 → 场景名选择，
自动切换三维根场景并连接编辑器。无需粘贴 Script Editor 代码。
配置、迁移参数和仅网页模式见 [统一地图工程工具](MAP_PROJECT.zh-CN.md)。

联动编辑界面提供“点、边、语义点”工具，以及地面拓扑、空中拓扑和独立语义点图层。
当前工具只编辑对应类型，其他图层可通过显示开关作参考。区域已从统一编辑入口移除。

空中节点与语义点不按楼层过滤，参考底图可选择任意楼层或关闭。
新建高度默认世界坐标 Z=1.5 米，可在工具栏修改；已有点的坐标不会随之变化。
每点独立选择 2.5D／3D：2.5D 平面拖动保持当前 Z，仍可手动输入 Z；
3D 在 Isaac 中提供 XYZ 手柄。网页拖动始终修改 XY。

语义点保存名称、类别、说明、别名、扩展 JSON 属性及可选的地面/空中导航节点绑定。
语义点是独立的地图标注，不引用 Isaac 物体，也不要求连接 Isaac；导航绑定只记录图域和节点 ID。

两端支持节点、语义点和整条边拖动。拖动边会在 XY 平移两端节点和该边折线，
同时更新关联边端点；所有 Z 保持不变。松手提交一次事务，可一次撤销；Esc 取消。
并发冲突保留草稿并刷新，需明确重新提交。预览仅在本端，提交后同步。

Isaac 的 **Isaac Topology Editor** 面板提供相同工具。左键编辑，Alt+左键、
中键和右键用于相机导航。叠加及预览写入 session layer，不保存到作者 USD。
属性面板可滚动访问导航绑定、撤销和保存。Linux 安装 Noto Sans CJK 时，统一启动入口
会在 Kit 初始化前注册中文字形，支持中文名称、说明、别名及楼层标签。

打开 room_06 使用场景键 `living_room_06`：

```bash
python tools/topology_authoring/launch.py --scene living_room_06
```

工程保存、旧数据清理和 API 见 [统一地图工程工具](MAP_PROJECT.zh-CN.md)。
下面的 `author` 是保留兼容的独立编辑器入口。

工具通过 `--adapter module:Class` 接收场景。首次创建从空拓扑开始；用 `--draft /path/to/topology.json` 可重新打开已有地图继续编辑。工具不会自动读取道路、路口或道路中线生成节点和拓扑线。适配器只负责加载场景、语义目标、支持面投影和校验。使用 `--headless` 可在不启动 Isaac Sim 时检查适配器；语义地图不在资产目录时可传 `--semantic-map`。

房间/区域绘制、可扩展对象、后补交互位置和三维路线的统一设计与示例见
[语义拓扑地图工作流](SEMANTIC_MAP.zh-CN.md)。

可选楼层分组、关键点标注、有序节点路线导出和 Go2 部署见
[多楼层工作流](MULTILEVEL.zh-CN.md)。内置 `UsdSceneAdapter` 读取场景描述文件；
具体场景数据由调用方提供，不会自动成为正式实验任务。

空中路线加 `--domain aerial`：支持任意世界 XYZ、纯垂直边、英文名称/别名及
方向元数据；楼层为可选分组。草稿、地图和路线预览分别保存在
`authoring/navigation/aerial/`，与地面产品分离。两类产品 JSON schema 完全相同，
运行时分别选择 XY 或 XYZ metric；内置 adapter 只检查结构，不证明净空。

## 兼容选择桥接接口

现有 `IsaacSelectionBridge` API 保留给脚本集成，可发送 prim 路径、世界坐标和
包围盒。精简后的联动网页直接编辑拓扑，不再提供旧“当前选择”转换面板。
需要旧选择数据的调用方可以读取 `/api/isaac/selection`；选择本身不会创建语义记录。

先启动地图服务（端口须与脚本一致）：

```bash
python -m tools.topology_authoring.map_server \
  --project /absolute/path/to/map-project --port 8765
```

然后在 Isaac Sim Script Editor 执行：

```python
from tools.topology_authoring.isaac_bridge import IsaacSelectionBridge, IsaacSelectionWindow

bridge = IsaacSelectionBridge("http://127.0.0.1:8765", scene_key="living_home_02").start()
bridge.bind_selection()                 # 发送 Isaac 当前选中的 prim
window = IsaacSelectionWindow(bridge)   # 可选：显示英文窗口，避免 Kit 字体乱码
window.show()
bridge.enable_point_picking(True)       # 仅手动选择桥接需要；不要与联动拖拽同时启用
```

联动编辑器的楼层由两端共享的楼层选择控制。关闭手动桥接或更换场景时执行：

```python
bridge.stop()
```

如果地图服务重启、切换场景或提示来源不匹配，必须显式执行
`bridge.reconnect()`；桥接不会把选择静默发送到另一个场景。当前只接受 Z-up
USD，`metersPerUnit` 会转换为地图使用的米制；Y-up 场景需先在 Isaac 中完成坐标
转换。桥接接口本身不改变占据栅格，也不证明机器人可通行。

### 网页选择的三维叠加与高亮

联动入口在 `/SemanticMapOverlay` 绘制已提交的地面图、空中图和语义点。
参考图层降低亮度；选中对象高亮。显示开关与当前工具在网页和 Isaac 同步。
内部选择协议支持 `node`、`edge` 和 `semantic_point`，选择操作不会创建数据。

office 测试场景以仓库的 `usd/` 目录为资产根目录，在 Isaac Sim 中打开相对路径：

```text
scene/Living and public space/office/office.usda
```
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

导航只跟踪 node，并按边方向选路。地面 Profile 使用节点间 XY 距离，空中
Profile 使用 XYZ 距离；运行时不读取 traversal label 或 controller capability，
也不使用 edge polyline 或 profile clearance。每个弯道都需要增加 node 并依次连边，例如
`A → 拐角B → C`。仅修改 `A → C` 的折线点不会改变实际运动路径。
相邻节点之间的直接通行条件和间距由作者保证，运行时没有自动节点间距限制，
也不避障或让行；模拟器物理碰撞正常保留。
start/replan 要求机器人已在最近节点的到达半径内，否则返回 `origin_not_reached`。
运行时不会自动驶向最近节点或边。地面图必须配合 XY profile，空中图必须配合 XYZ
profile；类型不匹配时拒绝任务。任意离图位置的接入需要调用方单独处理。

产品与 node-route Skill 共用执行结构校验：ID 必须非空且唯一，XYZ 为有限数值（不接受 bool
或数字字符串），边的端点和方向有效，地面边具有非零 XY 长度，空中边可以为垂直连接。坐标固定为 `scene_world`、
米（`meters_per_unit=1`）、Z 向上，不静默转换坐标。带 adapter/source 加载还核对
场景身份、当前来源摘要和每个语义绑定；权威目标清单为空时不能保留悬空绑定。
空草稿可以保存、加载；生成产品至少需要两个节点及一条边。不要求全图强连通，
单向边造成的不可达请求由 skill 明确返回失败，由 Executor 调用 Adapter 安全停止。

场景 adapter 的 `validate_polyline` 检查导航实际跟随的**两个端点间直线**，按
声明的 profile 调用。编辑折线仅检查结构与端点一致性，不能用绕弯折线证明直线
可通行。`EmptyTopologyAdapter` 不加载真实几何、语义目标或资产摘要，只用于接线
检查；具体场景检查仍由调用方提供的 adapter 负责。

实验接入时，host 应在运行前冻结 `product_digest(document)`、场景/语义输入和
adapter 版本到已有任务/资产身份中，再调用：

```python
from tools.topology_authoring import load_product, product_digest

frozen_digest = product_digest(document)  # 场景准备阶段记录并冻结
validated_topology = load_product(
    product_path, adapter, source, expected_digest=frozen_digest,
)
# 注入已有 NavigationExecutor；不要在运行中无记录地换图。
```

`load_product` 即使没有 adapter 也检查基本产品结构。完整产品摘要包含编辑字段；
skill 的私有选路证据另外记录与数组排列顺序无关的 `route_graph_digest`，仅标识
节点、目标绑定和有向边。它不替代场景来源身份，也不认证物理通行或任务成功。
显式 replan 使用当前任务绑定的地图，控制过程中只跟随已经选定的节点序列。

保存覆盖固定 `current/` 目录，使用临时包、目录交换和事务日志保证原子性，并拒绝非有限 JSON 数值；旧工程首次保存时迁移并删除 `revisions/`。加载草稿会清空旧撤销历史。场景重载
失败保留文档，adapter 应构建替代 stage，避免原地修改当前 stage 后失败。
headless 校验失败以非零退出码结束。编辑器验证、导航到达与外部物理评估保持分离。

验证命令、真实 Isaac 检查步骤及证据边界见 [验证记录](VALIDATION.md)。
