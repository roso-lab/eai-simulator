# 通用场景拓扑编辑器

工具通过 `--adapter module:Class` 接收场景。所有场景都从空拓扑开始，工具不会自动读取道路、路口或道路中线生成节点和拓扑线。适配器只负责加载场景、语义目标、支持面投影和校验。使用 `--headless` 可在不启动 Isaac Sim 时检查适配器；语义地图不在资产目录时可传 `--semantic-map`。

楼层切换、楼梯/坡道连接、关键点标注、有序 XYZ 路线导出和 Go2 部署见
[多楼层工作流](MULTILEVEL.zh-CN.md)。内置 `UsdSceneAdapter` 读取场景描述文件；
具体场景数据由调用方提供，不会自动成为正式实验任务。

空中路线加 `--domain aerial`：支持任意世界 XYZ、纯垂直边、英文名称/别名及
方向元数据；楼层为可选分组，连线使用 `fly`。草稿、地图和路线预览分别保存在
`authoring/navigation/aerial/`，与地面产品分离。内置空中 adapter 只检查结构，
不证明净空；每个拐点仍需建 node。heading 暂不用于 XYZ 飞行控制。地图冻结、
固定 JSON 地点解析与共享执行见 [空中接入指南](../../algorithm/skills/AERIAL_RUNTIME.zh-CN.md)。

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

地面导航只跟踪 node，并按边方向选路。版本 1 使用节点间 XY 距离；版本 2
使用 XYZ 距离和控制器通行能力，并用实测支持面高度判定进度。不使用 edge
polyline 或 profile clearance。每个弯道都需要增加 node 并依次连边，例如
`A → 拐角B → C`。仅修改 `A → C` 的折线点不会改变实际运动路径。
相邻节点之间的直接通行条件和间距由作者保证，运行时没有自动节点间距限制，
也不避障或让行；模拟器物理碰撞正常保留。
host 需保证机器人起始位置到最近节点可直接接入；该既有 start/replan 接入段
不是拓扑边，产品校验不证明任意离图位置可达。

产品与导航共用执行结构校验：ID 必须非空且唯一，XYZ 为有限数值（不接受 bool
或数字字符串），边的端点和方向有效，XY 长度非零。坐标固定为 `scene_world`、
米（`meters_per_unit=1`）、Z 向上，不静默转换坐标。带 adapter/source 加载还核对
场景身份、当前来源摘要和每个语义绑定；权威目标清单为空时不能保留悬空绑定。
空草稿可以保存、加载；生成产品至少需要两个节点及一条边。不要求全图强连通，
单向边造成的不可达请求由 skill 明确返回失败并输出零速度。

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
# 注入已有 GroundNavigationExecutor；不要在运行中无记录地换图。
```

`load_product` 即使没有 adapter 也检查基本产品结构。完整产品摘要包含编辑字段；
skill 的私有选路证据另外记录与数组排列顺序无关的 `route_graph_digest`，仅标识
节点、目标绑定和有向边。它不替代场景来源身份，也不认证物理通行或任务成功。
显式 replan 使用 host 提供的地图，控制过程中只跟随已经选定的节点序列。

保存采用原子替换并拒绝非有限 JSON 数值；加载草稿会清空旧撤销历史。场景重载
失败保留文档，adapter 应构建替代 stage，避免原地修改当前 stage 后失败。
headless 校验失败以非零退出码结束。编辑器验证、导航到达与外部物理评估保持分离。

验证命令、真实 Isaac 检查步骤及证据边界见 [验证记录](VALIDATION.md)。
