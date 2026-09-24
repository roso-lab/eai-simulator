# Stage43：独立集合区资格与逐项进展证据

基线1626016。本阶段并行修复评分事实生产，并为下一版本请求目标接口冻结集合区规则。
v1保持原样，未启用新profile的参与者行为不变。以下几何规则是显式版本化执行扩展，
不改任务、见证、资产、reach、时限或容量；不是已完成完整业务或oracle认证的声明。

## 几何来源与规则

1. 候选ID及容量只取exact public task：四个既有assembly_candidate、各容量1。
   坐标及半径只按ID连接冻结semantic map的`bindings`，禁止使用private episode binding的
   `bound_person_ids`、疏散路线终点或人员身材推导区域。地图区域列表是公开描述，
   几何在bindings中，二者必须精确连接；地图为city_world、Z-up、米。
2. 该地图的候选定义是Point及0.75m半径，并由生成器检查其圆盘位于sidewalk。
   新profile使用这个固定圆盘，地面参考来自地图`surface_heights_m.sidewalks`（0.3m）。
   不增加由作者人员身高决定的虚构天花板，不扩大XY边界来容纳特定人员。
3. 支撑资格须读取当前已组合场景：同tick下中心及圆周8个点向下PhysX查询，记录命中
   collider、法线和高度，并核对命中路径在已组合USD中确为启用的静态碰撞体。
   状态明确命名sampled_supported，只证明9个点，不能宣称连续支撑。地图仍只是
   冻结静态先验；完整身体和路线检查另由现有native reader执行。动态支撑、
   未同步查询或不支持的来源保持unknown，不把通用网格覆盖证明新增为业务准入门槛。
4. 数值测量容差为1e-5m（支撑高度/距离）、法线上向偏差1e-5。ray XY一致性取
   max(1e-5m, 对应世界坐标float32的4 ULP)，并逐样本归档；这是输入/输出量化
   核对边界，不是可调业务范围，不移动或扩大圆盘。首次collector实测在约75m坐标
   处暴露固定1e-5m不足的数值缺证，原档保留。诊断看到高度误差约4.2e-8m，
   该容差不能吞掉v1已发现的约0.07m错误。地面目标仍为地图参考平面，不按运行结果平移。
5. 人员到达仍要求路径完成、实际完整XY身体包络在圆盘内，
   实际三维身体与静态/动态物体无碰撞，以及既有安全路线证据。禁止改为root-only到达，
   不把上述1e-5m测量核对容差新增为人员控制器的到达/足部接触容差。
   高度由每次真实身体体积净空检查决定；缺测量归平台unknown，真实碰撞归执行blocked。
6. 容量仍按实际occupant身份与reservation身份的并集计数。不同身材可以使某pair被阻挡，
   但不能改变候选几何、支撑平面或容量。四候选×四人员记录必须全部保留，不只公开成功项。

## 接口边界与交付顺序

拟用显式 `requested_target_v2_support_footprint`，带版本2 affordance；不重解释v1 AABB。
任务hash仍为 `143a7ec986e256eaa0ec96c5853164ed558da5b57d52c7ab9f41234561c2be2f`，
地图hash仍为 `5ef52d1a1ade14355de9062f6371131eaa092e1c7bec38e45e75e0ea616fdc93`。
当前阶段先交付资格测量和负例测试，随后将同一个规则接入目标生成、准入、占用、
控制与到达读回；只有这些消费者一致后才开放v2执行。不在消费者未接齐时改profile标识。

测量档案保留episode/source/native tick、map hash、输入几何hash、支撑原始证据和
资格原因。公开projection只允许固定几何、容量及资格状态/引用，不泄露native路径、
作者指派、路线或评测器方案；测量仍按用户选择通过既有计量工具读取。

这属于已授权版本化执行修复。若实施发现必须移动候选、扩大半径/容量、更换任务或
放宽完整身体安全条件，则停止该变更，另交具体设计；已有授权不涵盖这些变化。

## 有界实测与非目标

ljy只读inventory v2在native `10.019999999999875s`同tick完成36次支撑raycast，
四候选均命中同一静态sidewalk Mesh，高度约0.30000001m。该Mesh670点、1300面。
此结果只证明支撑采样和下一测量的输入存在，尚不证明整个圆盘覆盖或完整业务可行。
inventory v1因scratch读取`sidewalk`而非`sidewalks`字段失败，保留失败记录；未修改资产。

不新增通用planner、IK或普遍地形适配；不把四人员一次matching或某条路线成功变成
初始deployable标签。完整City业务诊断仍需四人员、六后续车辆、报告/连续通路以及
response_dispatch硬条件。所有oracle/platform/release晋级仍被禁止。

## 评分侧并行修复

各业务项的pending时间戳独立处理，避免未收到另一服务回执时丢失已完成里程碑。
复用原evaluate_city_acceptance，不新增评分轨或权重；raw envelope原样归档，
逐项依赖投影及review也归档以支持离线复算。非null畸形时间戳仍归基础设施失败，
预算、跨section因果、全量人员/车辆身份及额外response_dispatch硬条件不放宽。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`保持。
