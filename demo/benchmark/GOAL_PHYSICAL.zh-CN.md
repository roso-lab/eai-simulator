# 原生配置、固定点技能与空间占用诊断

任务生成侧用这些作者工具验证题目的物理前提。它们不运行被测模型，不评分，
也不把一条参考解或静置轨迹视为业务完成。正式发布仍由任务审核和业务证据门禁决定。

## 环境与串行执行

所有以下 Python 命令使用 `env_isaaclab`。不启动 ROS；需要 ROS 的进程应另用系统环境。
所有场景共享 `tmp/goal_physical_probe.lock`，同一时刻只运行一个 Isaac worker。
GUI 只用于作者视觉检查；截图不是 agent 已获得的观察。

```bash
conda activate env_isaaclab
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs

# prepare 不启动 Isaac；output-root 必须是不存在的新目录。
python -m demo.benchmark.goal_physical_probe \
  --scene warehouse_nvd --steps 240 --gui --output-root tmp/my_warehouse_probe
python -m demo.benchmark.goal_physical_probe \
  --output-root tmp/my_warehouse_probe --execute
python -m demo.benchmark.goal_physical_review tmp/my_warehouse_probe
python -m demo.benchmark.goal_object_readback_review tmp/my_warehouse_probe
```

其他场景键为 `factory`、`modern_house_nvd`、`city`。City 额外传
`--city-package <authoring目录>`，当前使用城市维护者交付的 `authoring_17`。
城市诊断通过其真实场景hook创建 `/World/City`；禁用flow并设people=0，
所以此配置诊断不能验证行人轨迹、干预效果或城市服务回执。

Factory沿用启动器已有明确的CPU物理回退；请求设备、解析后的设备及实际设备分别冻结/读回。
GUI渲染仍可使用GPU。其他场景遵循请求设备；不会因场景中有人而强制CPU。

父进程冻结部署、selection、源文件和run ID；子进程继承规范文件锁。
可捕获中断和超时会终止并回收子进程组。父进程被强制杀死时，仍存活的子进程继续持锁。
有过launch的目录不能再次执行。失败须保留原始输出，修改后创建新目录。
Kit有时在异常清理时返回进程码0，因此还必须检查完整诊断状态与预期步数。

## 每类证据能证明什么

| 输出/模块 | 可以检查 | 仍不能证明 |
| --- | --- | --- |
| `request.json`、`deployment.json`、`selection.json` | 本次冻结题目、实际选型/出生位、来源版本 | 场景已创建、公开能力已实现 |
| `runtime_manifest.json`、`physical_report.json` | 实际机器人实体/控制状态键、PhysX身体质量与位姿、控制步/物理步时钟、实际设备 | 完整工具装配、足端接触、载重能力、导航或操作成功 |
| `goal_object_readback_review` | 特定对象原生源、实际局部mesh尺寸、质量、固定点、动态体/碰撞声明；与manifest同一stage | PhysX cooking、接触稳定、业务区域语义、抓取成功 |
| `goal_live_spatial` | 有限离散支撑/净空射线及命中身份 | 连续通路、全体机器人包络、三维空域准入 |
| `goal_live_occupancy` | 在可信完整当前快照下，active/idle机器人与持物的3D包络；平移段对当前动态包络及静态overlap/sweep查询 | 未来动态预测、旋转/关节变化、地面步态、实际通行 |
| `goal_native_occupancy` | 组合USD碰撞几何与当前PhysX link姿态构建完整机器人清单；明确记录无碰撞link及未知几何 | 未支持的几何、缺失/未加载子树、未读回的持有joint；不能用公开外形参数补齐 |
| GUI PNG与相机矩阵 | ZCode可核查实际可见场景、物体姿态、遮挡及明显摆放问题 | 碰撞历史、物理净宽、质量、任务身份或最终业务完成 |

`goal_physical_review`专门检查机器人实体和控制步序列。
对象还须运行独立的`goal_object_readback_review`；不存在的对象输出明确未完成。
空对象清单不会产生“全部对象已验证”的真值。
文件指纹是可信本地工作流的一致性检查，不是远程证明机制。
覆盖范围为实际加载的仓库Python与组合USD层，不包括全部纹理、控制权重或未加载依赖。

原生占用适配器读取完整articulation实例/link清单，并保留known_noncolliding身体的审核记录。
未加载子payload、未知碰撞几何、缺失实时姿态或无法确认的外部joint使整个快照阻塞，不能交付部分清单。
碰撞命中的点实例索引若不是普通命中的`0xFFFFFFFF`，保持unknown，不能因路径相同就当作自身排除。
本轮Factory/Warehouse实际读取已遇到Iris的`convexDecomposition`和Carter的CollisionAPI Xform结构；
当前适配器不支持这些碰撞包络，因此记录具体阻塞，不发出clear平移结论。
未来接入cooked shape读取或经验证的保守包络后，应重新取当前位姿并复验；不能将公开外形声明当作测量。

## 理想固定点连接

`goal_native_attachment`提供prepare/commit/cancel/detach。输入仅由可信host创建：
公开任务、活跃成员、实际刚体路径、同一当前PhysX步的身体/物体姿态。
检查槽位覆盖、能力接口、分配载重、实际物体质量、固定点局部坐标和实时距离。
使用实时PhysX姿态变换固定点；Fabric模式下USD世界坐标可能仍是出生位，不能直接作抓取目标。

合法操作只创建所拥有的FixedJoint，显式排除加入原机器人articulation。
它不改变物体质量、不瞬移物体、不关闭全局碰撞，也不运行IK/VLA。
连接保持当前相对变换，属于基准规定的理想连接技能；不是物理机械臂工作空间认证。
joint写入与读回仍不等于solver响应、双机载荷分配或协同运动成功。
撤销会重新检查所有权，拒绝删除他人修改的joint；重复撤销无副作用。

```bash
python -m demo.benchmark.goal_attachment_probe \
  --output-root tmp/my_attachment_probe --gui
python -m demo.benchmark.goal_attachment_probe \
  --output-root tmp/my_attachment_probe --execute --timeout-s 600
python -m demo.benchmark.goal_attachment_review tmp/my_attachment_probe
```

此仓库微诊断单独记录M20_2的实验接近出生位，**不是正式订单任务的出生位或完成证据**。
冻结物体3kg、接口和能力不变；进行240步静置、100步连接后低速后退、100步释放后后退，
记录实际连接前后姿态与anchor误差，并检验超载、不活跃、超距三类拒绝。
应从实际位移和连接误差判定solver响应，不能仅看脚本跑完。
独立审核器从原始位姿重新计算连接误差和位移，核对时钟、joint所有权、释放前后状态及拒绝理由。
通过仅表示本次单物体短距离连接跟随与释放被观察到，不表示抬升、双机承载或完整可靠技能已通过。

## 2026-09-18 验证发现与后续门禁

四景已有240控制步/960物理子步的真实GUI诊断和独立审核记录。
Factory/Warehouse/House各4台，City为3台，均有独立实体和有界静置状态。
Warehouse的12kg木箱、3kg纸箱，以及House的14kg扶手椅、2kg书堆有实际匹配读回。
Factory异常记录/维护服务车缺合适原生绑定，继续未完成；不会用清洁车或普通箱子冒充。

首轮ZCode发现Factory相机贴面、House候选区域位于后院露台。
工厂改相机及近景复核；楼梯始终保留公开题面，视觉不清不会被改写为关闭使用。
House已依据室内地毯/沙发等原生资产，将入口候选移至约(4,3.5)、生活区移至约(4.5,12.3)，
任务扶手椅与书堆作为独立原生道具放入室内；冻结质量/尺寸不变，也不冒充原有装饰物身份。
这些是修订后的作者候选，仍需新一轮物理和像素验证；旧露台坐标不能证明业务关系。
地图可达、命中地板和机器人未倒下都不解决语义错位。

原始运行保存在`tmp/goal_physical_stage12/`及`tmp/goal_attachment_stage12/`，不提交。
源文件改变后旧记录是历史证据，不能回填、重签或宣称验证了新源码。
每次正式审核应新建输出目录；单独强化审核器而未改变执行源时，可对原始记录产生新的审核文件。
分项状态与待办见 [TODO](TODO.zh-CN.md#阶段12配置实际应用与可信运行接线执行中2026-09-18)。

闭合源码批次目录为 `factory_v7`、`warehouse_v8`、`house_v5`、`city_v5`；四者均完成
240 控制步和独立审核。House_v4只勉强通过倾角阈值，其初始脚轮穿入0.25m楼板的问题
不能由这个阈值排除。House_v5将两台M20根高改为0.77m、Carter根高改为0.45m，恢复
M20_2原XY；最终M20脚轮中心约0.340m、原生半径0.09m，与0.250m实测地板吻合，
最大倾角约0.006rad。该几何一致性不是接触力认证。ZCode近景发现M20_2位于门外台面，
完整初始区域语义仍待准入；室内道具与无人机视图不能替地面机器人证明室内位置。
`tmp/goal_attachment_stage12/warehouse_v4` 是闭合源码下的固定点微诊断，独立审核记录在其 `review.json`。
这些归档仍属于作者配置/技能诊断，四场景的 `physical_task_execution_validated`、
`business_outcomes_validated` 和 `scoreable` 都为 false。

Factory楼梯持续向题面开放。源几何核查表明平台黄色构件为静态无碰撞的RampShort，
距中心线至少0.911m；不是可开合安全门。中心线源网格射线不能替代PhysX连续支撑和实际爬楼，
首级约0.210m与公开0.18m声明的差异也需作者解决，禁止静默改参数或隐藏楼梯。

下一步三维占用读回需要独立标定PhysX property/cooked collider回调：完整shape身份、
分解凸体覆盖、Xform组合、嵌套变换/缩放和同一物理步的时间绑定。当前接口的存在不等于
原生包络已被测量；在这些证据齐备前保留明确blocker。
