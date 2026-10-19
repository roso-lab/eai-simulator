# 通用场景拓扑编辑器

推荐从终端用一个入口启动 Isaac Sim 和地图网页：

```bash
cd <repository-root>
python tools/topology_authoring/launch.py
```

本机默认打开 `http://127.0.0.1:8770/`，按 USD 三大分类 → 场景名选择，
自动切换三维根场景并重新连接选点。无需粘贴 Script Editor 代码。
配置、迁移参数和仅网页模式见 [统一地图工程工具](MAP_PROJECT.zh-CN.md)。

联动入口默认启用 **Surface Point Mode**：在 Isaac 视口左键单击可观测表面，会将其
世界坐标发送到浏览器。需要选择 prim 时切换到 **Asset Selection Mode**；两种模式下
视口右键导航均保持可用。

二维障碍物语义标注、拓扑设点及同步保存请使用
[统一地图工程工具](MAP_PROJECT.zh-CN.md)：支持导入已有场景地图、浏览器编辑、
语义增删、交互点和同版本 JSON/GeoJSON/栅格/PNG 导出。下面的 `author`
是已有 Isaac Sim 原生三维编辑器入口。

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

## Isaac Sim 联动选点

浏览器编辑器可以接收 Isaac Sim 视口的当前选择。Isaac 负责真实三维对象、prim
路径、世界坐标和包围盒；浏览器只显示紫色临时预览，点击转换按钮后才写入统一
`MapProject`。选择本身不会自动创建语义记录。

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
bridge.enable_point_picking(True)       # 默认联动入口已开启；手动桥接时可开启表面点选
```

回到浏览器后，地图工作区的“Isaac Sim 当前选择”面板会显示 prim 路径及 X/Y/Z。
选择包含有效 `level_id` 时，浏览器会自动切换到对应楼层的二维底图，无需再手动点选楼层。
“转为拓扑节点”适合导航接入点；“转为对象语义”适合桌子、门、设备等后续再
补全的记录。填写名称、类别和所属区域后再保存。关闭 Isaac 或更换场景时执行：

```python
bridge.stop()
```

如果地图服务重启、切换场景或提示来源不匹配，必须显式执行
`bridge.reconnect()`；桥接不会把选择静默发送到另一个场景。当前只接受 Z-up
USD，`metersPerUnit` 会转换为地图使用的米制；Y-up 场景需先在 Isaac 中完成坐标
转换。桥接接口本身不改变占据栅格，也不证明机器人可通行。

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
host 需保证机器人起始位置到最近节点可直接接入；该既有 start/replan 接入段
不是拓扑边，产品校验不证明任意离图位置可达。

产品与 node-route Skill 共用执行结构校验：ID 必须非空且唯一，XYZ 为有限数值（不接受 bool
或数字字符串），边的端点和方向有效，允许在所选 metric 下为零长度的连接。坐标固定为 `scene_world`、
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

保存采用原子替换并拒绝非有限 JSON 数值；加载草稿会清空旧撤销历史。场景重载
失败保留文档，adapter 应构建替代 stage，避免原地修改当前 stage 后失败。
headless 校验失败以非零退出码结束。编辑器验证、导航到达与外部物理评估保持分离。

验证命令、真实 Isaac 检查步骤及证据边界见 [验证记录](VALIDATION.md)。
