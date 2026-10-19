# HeteroCoord 完整机器人池简历（中文审核稿）

本稿是配置池设计：任务可以任取子集，也可以创建同配置的多个实例；不是要求每个场景同时放入所有机器人。

代码基线：`e8f97448a55feef76d9ec1ed993f01695580efc6`。14 类平台，24 份核心配置简历，21 份平台/控制器/携臂合法配置，另加 2 份独立机械臂配置，共 23 条。

核心审核条目还包含已拒绝的 Lite3 + Z1；该条只记录排除原因，不进入配置池。

JSON 中每条记录都具备同一套固定字段，未知值写 `null`。`configuration_profiles` 用于审核能力差异，`resumes` 按机器人本体与外挂臂区分，另列独立机械臂；G1 的移动与抓放能力归于同一机器人身份。

这些是供审核的配置简历，尚未转为运行时输入。`runtime_qualified=false`，实例身份、状态和可用技能均未伪造；技能的 support 描述配置能力，support_basis 区分源码分析与用户确认；availability 记录执行接入状态，实际可执行还要满足该条前提。

**确认约束：Lite3 不能上楼、不能下楼；UR5 可作为独立机器人。UR5 载荷 5 kg、相对安装基座限高 0.824073 m；Z1 载荷 3 kg、相对安装基座限高 0.798026 m。限高测量点与载荷是否含工具质量待你确认。**

UR5 仅作为独立机器人，固定配备 Robotiq 2F-85；不提供裸臂或任何机器人搭载 UR5 的组合。Pepper 和 G1 默认具备抓取能力；Pepper 默认选用 PEPPER_PICK_PLACE_CFG。G1 只保留一个机器人身份，其行走和标定抓放是同一机器人在不同运行模式下的能力，模式接线仍待统一。Z1 独立配置作为有资产但待接入的候选列出。Kinova Gen3 按已有项目决定不进入正式池。未受目录支持的任意跨型号拼接、多外挂臂或双重登记均不纳入。

HeteroCoord 实验不使用键盘控制和 Navigation I/O 工具。相机是机器人默认属性，不作为可选开关。SimpleCar 与三类无人机使用内建相机；Carter、Pepper、Go2、B2、M20、Scout、MuSHR v2、Coco、Lite3 默认装配 RealSense D455。G1 相机适配待后续完成；独立 UR5/Z1 的相机接线也尚未建立。除 Go2 外，每个平台只使用一个实验控制器：B2 为 `B2_MIXED_STAIRS_CFG`，Pepper 为 `PEPPER_PICK_PLACE_CFG`，G1 已由 `G1_DWAQ_CFG` 完全替换旧 `G1_SKRL_CFG`。Go2 保留两份简历：`GO2_MIXED_STAIRS_CFG` 是正常稳定版本，`GO2_EXTREME_PARKOUR_TEACHER_CFG` 是运动不稳定的实验性越野版本。楼梯控制器的选择不自动确认携臂或持物状态的楼梯资格。

## 平台与组合覆盖

|平台|本体/携臂配置数|正式控制器数|目录外控制器数|简历数|
|---|---:|---:|---:|---:|
|Carter 差速轮式机器人|2|1|0|2|
|SimpleCar 平面移动机器人|1|1|0|1|
|Pepper 全向底盘双臂机器人|1|1|0|1|
|Go2 四足机器人|2|2|0|4|
|B2 四足机器人|2|1|0|2|
|M20 轮腿机器人|2|1|0|2|
|Scout 差速轮式机器人|2|1|0|2|
|MuSHR Nano v2 车式转向机器人|1|1|0|1|
|G1 人形机器人|1|1|0|1|
|CF2X 四旋翼机器人|1|1|0|1|
|Iris 四旋翼机器人|1|1|0|1|
|Pegasus X4 四旋翼机器人|1|1|0|1|
|Lite3 四足机器人|1|1|0|1|
|Coco 车式转向机器人|1|1|0|1|

本池仅区分无外挂臂与单外挂 Z1 配置，按 `validate_attachment_types` 筛查。通过配置筛查不代表物理能力已全部认证。历史和备选控制器均不进入本实验简历池。

外挂臂仅保留 Z1 配对，包括当前拒绝配对；拒绝配对不进入合法变体池。

## 全部外挂臂配对

|底盘|Z1 + 夹具|
|---|---|
|Carter 差速轮式机器人|附条件：仅有静止挂载证据|
|Go2 四足机器人|附条件：仅有静止挂载证据|
|B2 四足机器人|附条件：仅有静止挂载证据|
|M20 轮腿机器人|附条件：仅有静止挂载证据|
|Scout 差速轮式机器人|附条件：仅有静止挂载证据|
|Lite3 四足机器人|当前挂载筛查拒绝|

## 逐项核心简历

每项共享上述限值与实例身份规则；以下逐条列明能力、控制器、技能前提和特殊限制。无外挂臂的 Pepper/G1 默认具备原生抓取能力；普通移动底盘无臂配置不具备抓放。

### 01. Carter 差速轮式机器人 / 无外挂臂

配置 ID：`carter.no_external_arm.CARTER_DIFF_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮式 / 差速驱动；运动控制器：`CARTER_DIFF_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

证据范围：源码接线与声明；本简历未新增物理验证。

### 02. Carter 差速轮式机器人 / 携带 Z1 + Z1 夹具

配置 ID：`carter.z1.CARTER_DIFF_CFG`。

部署：**附条件：仅有静止挂载证据**。未缩放 Z1 静止摆臂、对地反力与漂移门禁通过；宿主 54 kg 为 USD 声明值且 40 kg 集中在两驱动轮（分布偏乐观），未验证行走中操作。

运动：轮式 / 差速驱动；运动控制器：`CARTER_DIFF_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待当前组合验证|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待当前组合验证|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 03. SimpleCar 平面移动机器人 / 无外挂臂

配置 ID：`simple_car.no_external_arm.SIMPLE_CAR_PLANAR_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮式 / 平面根速度控制；运动控制器：`SIMPLE_CAR_PLANAR_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `builtin_dual_camera_suite`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：平面根速度控制；当前 ROS cmd_vel 桥接不保留横向速度。

证据范围：源码接线与声明；本简历未新增物理验证。

### 04. Pepper 全向底盘双臂机器人 / 无外挂臂（保留原生双臂）

配置 ID：`pepper.no_external_arm.PEPPER_PICK_PLACE_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮式 / 虚拟关节全向驱动；运动控制器：`PEPPER_PICK_PLACE_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：2；来源：原生集成；末端：`native_hands`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待实例绑定|PepperPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：复合控制器与实例适配器注册；双臂链可用；物理初始化前设置对象碰撞过滤；适配器当前要求单环境视图。

`eai.manipulation.pick_place` 的限制：双臂局部 IK 抓放与理想化对象跟随。；不控制任意对象姿态；不套用 UR5/Z1 限制。

特殊说明：原生双臂，不套用 UR5/Z1 的载荷或高度数值。；全向底盘通过虚拟关节目标实现；底盘固定根与移动底盘坐标不能混用。

证据范围：源码接线与声明；本简历未新增物理验证。

### 05. Go2 四足机器人 / 无外挂臂 / 正常稳定版

配置 ID：`go2.no_external_arm.GO2_MIXED_STAIRS_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：四足 / 足式运动；运动控制器：`GO2_MIXED_STAIRS_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

控制器版本：**正常稳定版本**；成熟度：`stable`；跌倒恢复：未确认。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=支持，下楼=未确认；依据：用户确认当前稳定 Go2 控制器具备上楼能力；下楼尚未确认。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：楼梯资格必须区分稳定版/越野版控制器、方向、阶梯几何与负载。；存在 stairs 控制器只说明控制接线，不代表当前负载具备已验证的上下楼资格。；Go2 正常稳定版本；使用原有 GO2_MIXED_STAIRS_CFG。

证据范围：源码接线与声明；本简历未新增物理验证。

### 06. Go2 四足机器人 / 无外挂臂 / 实验性越野版（运动不稳定）

配置 ID：`go2.no_external_arm.GO2_EXTREME_PARKOUR_TEACHER_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：四足 / 足式运动；运动控制器：`GO2_EXTREME_PARKOUR_TEACHER_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

控制器版本：**实验性越野版本**；成熟度：`experimental_unstable`；跌倒恢复：不支持。

已知稳定性限制：**用户已明确该越野版本运动不稳定；任务分配必须保留该限制。**

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=未确认，下楼=未确认；依据：实验性越野策略已接线，但运动不稳定且没有当前路线专项资格。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：楼梯资格必须区分稳定版/越野版控制器、方向、阶梯几何与负载。；实验性越野版本；运动不稳定，不能作为稳定版的透明替代。；753 维 privileged Teacher 依赖 132 射线真实场景高度扫描和两帧足端接触历史。；不包含站起或跌倒恢复控制器，也不是视觉 Student 或全局路径规划器。

证据范围：源码接线与声明；本简历未新增物理验证。

### 07. Go2 四足机器人 / 携带 Z1 + Z1 夹具 / 正常稳定版

配置 ID：`go2.z1.GO2_MIXED_STAIRS_CFG`。

部署：**附条件：仅有静止挂载证据**。仅完成未缩放静止摆臂测试，尚未验证行走中操作。

运动：四足 / 足式运动；运动控制器：`GO2_MIXED_STAIRS_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

控制器版本：**正常稳定版本**；成熟度：`stable`；跌倒恢复：未确认。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=未确认，下楼=未确认；依据：携臂后的完整配置尚待验证。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待当前组合验证|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待当前组合验证|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：楼梯资格必须区分稳定版/越野版控制器、方向、阶梯几何与负载。；外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。；存在 stairs 控制器只说明控制接线，不代表当前负载具备已验证的上下楼资格。；Go2 正常稳定版本；使用原有 GO2_MIXED_STAIRS_CFG。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 08. Go2 四足机器人 / 携带 Z1 + Z1 夹具 / 实验性越野版（运动不稳定）

配置 ID：`go2.z1.GO2_EXTREME_PARKOUR_TEACHER_CFG`。

部署：**附条件：仅有静止挂载证据**。仅完成未缩放静止摆臂测试，尚未验证行走中操作。

运动：四足 / 足式运动；运动控制器：`GO2_EXTREME_PARKOUR_TEACHER_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

控制器版本：**实验性越野版本**；成熟度：`experimental_unstable`；跌倒恢复：不支持。

已知稳定性限制：**用户已明确该越野版本运动不稳定；任务分配必须保留该限制。**

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=未确认，下楼=未确认；依据：实验性越野策略已接线，但运动不稳定且没有当前路线专项资格。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待当前组合验证|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待当前组合验证|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：楼梯资格必须区分稳定版/越野版控制器、方向、阶梯几何与负载。；外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。；实验性越野版本；运动不稳定，不能作为稳定版的透明替代。；753 维 privileged Teacher 依赖 132 射线真实场景高度扫描和两帧足端接触历史。；不包含站起或跌倒恢复控制器，也不是视觉 Student 或全局路径规划器。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 09. B2 四足机器人 / 无外挂臂

配置 ID：`b2.no_external_arm.B2_MIXED_STAIRS_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：四足 / 足式运动；运动控制器：`B2_MIXED_STAIRS_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=未确认，下楼=未确认；依据：缺少当前路线和控制器的专项验证。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：B2 使用自身资产和控制器，不继承 Go2 的参数或资格。；存在 stairs 控制器只说明控制接线，不代表当前负载具备已验证的上下楼资格。

证据范围：源码接线与声明；本简历未新增物理验证。

### 10. B2 四足机器人 / 携带 Z1 + Z1 夹具

配置 ID：`b2.z1.B2_MIXED_STAIRS_CFG`。

部署：**附条件：仅有静止挂载证据**。仅完成未缩放静止摆臂测试，尚未验证行走中操作。

运动：四足 / 足式运动；运动控制器：`B2_MIXED_STAIRS_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=未确认，下楼=未确认；依据：携臂后的完整配置尚待验证。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待当前组合验证|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待当前组合验证|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：B2 使用自身资产和控制器，不继承 Go2 的参数或资格。；外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。；存在 stairs 控制器只说明控制接线，不代表当前负载具备已验证的上下楼资格。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 11. M20 轮腿机器人 / 无外挂臂

配置 ID：`m20.no_external_arm.M20_ROUGH_RSL_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮腿 / 足式与轮式复合运动；运动控制器：`M20_ROUGH_RSL_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=未确认，下楼=未确认；依据：缺少当前路线和控制器的专项验证。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：轮腿形态不自动代表具备当前路线的上下楼能力。

证据范围：源码接线与声明；本简历未新增物理验证。

### 12. M20 轮腿机器人 / 携带 Z1 + Z1 夹具

配置 ID：`m20.z1.M20_ROUGH_RSL_CFG`。

部署：**附条件：仅有静止挂载证据**。未缩放 Z1 静止摆臂、对地反力与漂移门禁通过；未验证行走中操作。

运动：轮腿 / 足式与轮式复合运动；运动控制器：`M20_ROUGH_RSL_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=未确认，下楼=未确认；依据：携臂后的完整配置尚待验证。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待当前组合验证|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待当前组合验证|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：轮腿形态不自动代表具备当前路线的上下楼能力。；外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 13. Scout 差速轮式机器人 / 无外挂臂

配置 ID：`scout.no_external_arm.SCOUT_DIFF_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮式 / 差速驱动；运动控制器：`SCOUT_DIFF_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

证据范围：源码接线与声明；本简历未新增物理验证。

### 14. Scout 差速轮式机器人 / 携带 Z1 + Z1 夹具

配置 ID：`scout.z1.SCOUT_DIFF_CFG`。

部署：**附条件：仅有静止挂载证据**。仅完成未缩放静止摆臂测试，尚未验证行走中操作。

运动：轮式 / 差速驱动；运动控制器：`SCOUT_DIFF_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待当前组合验证|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待当前组合验证|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 15. MuSHR Nano v2 车式转向机器人 / 无外挂臂

配置 ID：`mushr_v2.no_external_arm.MUSHR_ACKERMANN_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮式 / 车式转向；运动控制器：`MUSHR_ACKERMANN_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：车式转向，不按全向横移或原地旋转机器人分配动作。

证据范围：源码接线与声明；本简历未新增物理验证。

### 16. G1 人形机器人 / 无外挂臂（保留原生双臂） / DWAQ 行走版

配置 ID：`g1.no_external_arm.G1_DWAQ_CFG`。

部署：**已注册，但运行时资格被阻止**。控制器和匹配资产已经注册；纯 G1 环境使用合同要求的 0.001 s × 20。G1 与其他机器人混合时缺少多速率方案或共同物理时序重验，构建器会明确拒绝。

运动：双足 / 足式运动；运动控制器：`G1_DWAQ_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：2；来源：原生集成；末端：`native_hands`。

控制器版本：**DWAQ 行走版本**；成熟度：`deployment_baseline_limited`；跌倒恢复：不支持。

运行时阻塞：纯 G1 使用 sim.dt=0.001、decimation=20；与任何非 G1 控制器的混合环境当前拒绝构建。；DWAQ 行走与固定基座 G1PickPlaceAdapter 的策略切换和关节写入仲裁尚未形成统一运行模式。

相机：实验要求默认相机，但当前工程尚未完成该配置的相机适配。

楼梯：上楼=未确认，下楼=未确认；依据：历史楼梯结果只部分成功，不构成可靠楼梯资格。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|部署被阻止|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|用户确认的项目能力|pick, place|待接入|G1PickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：同一 G1 实例切换到经标定的抓放模式；Inspire 手、对象绑定与 G1LiveRuntime 接线。

`eai.manipulation.pick_place` 的限制：行走与固定抓放当前使用不同的资产/运行方式，合并为同一机器人身份不等于可同时执行。；抓放模式限已标定对象与直立轴向姿态；持物行走和边走边操作仍待验证。

特殊说明：项目默认具备抓取能力；DWAQ 行走策略控制 29 个身体关节并将 24 个 Inspire 手指保持为零，抓放需要切换到已标定抓放运行模式。；DWAQ 历史 School entrance 长楼梯上行仅 19/32 成功，不声明可靠楼梯能力。；不包含跌倒恢复；切换策略或 episode 重置时必须清空五帧历史。

证据范围：模型/适配器接线与纯 G1 配置时序已接入；混合机器人部署资格仍被阻止。

### 17. CF2X 四旋翼机器人 / 无外挂臂

配置 ID：`cf2x.no_external_arm.QUADCOPTER_GOAL_SKRL_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：空中 / 多旋翼推力/力矩控制；运动控制器：`QUADCOPTER_GOAL_SKRL_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `builtin_monocular_camera`；安装位姿与图像流待实例绑定。

楼梯：上楼=不适用，下楼=不适用；依据：固定基座或空中配置，不使用地面楼梯能力。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：当前 DronePickPlaceAdapter 仅支持 PegasusIris/PegasusX4，不支持 CF2X。

证据范围：源码接线与声明；本简历未新增物理验证。

### 18. Iris 四旋翼机器人 / 无外挂臂

配置 ID：`iris.no_external_arm.PEGASUS_IRIS_POSITION_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：空中 / 多旋翼飞行；运动控制器：`PEGASUS_IRIS_POSITION_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `builtin_monocular_camera`；安装位姿与图像流待实例绑定。

楼梯：上楼=不适用，下楼=不适用；依据：固定基座或空中配置，不使用地面楼梯能力。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待实例绑定|DronePickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：PegasusIris/PegasusX4 的 goal_position 接线；Owned FixedJoint 运输桥与实例适配器注册；对象/飞行锚点、净空与飞行负载验证。

`eai.manipulation.pick_place` 的限制：物理 FixedJoint 挂接/释放，不代表机械手抓取。；不控制任意物体姿态；飞行负载上限未知。

特殊说明：飞行能力与地面楼梯通行无关；飞行净空、负载和姿态约束需单独确认。

证据范围：源码接线与声明；本简历未新增物理验证。

### 19. Pegasus X4 四旋翼机器人 / 无外挂臂

配置 ID：`pegasus.no_external_arm.PEGASUS_X4_POSITION_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：空中 / 多旋翼飞行；运动控制器：`PEGASUS_X4_POSITION_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `builtin_monocular_camera`；安装位姿与图像流待实例绑定。

楼梯：上楼=不适用，下楼=不适用；依据：固定基座或空中配置，不使用地面楼梯能力。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待实例绑定|DronePickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：PegasusIris/PegasusX4 的 goal_position 接线；Owned FixedJoint 运输桥与实例适配器注册；对象/飞行锚点、净空与飞行负载验证。

`eai.manipulation.pick_place` 的限制：物理 FixedJoint 挂接/释放，不代表机械手抓取。；不控制任意物体姿态；飞行负载上限未知。

特殊说明：飞行能力与地面楼梯通行无关；飞行净空、负载和姿态约束需单独确认。

证据范围：源码接线与声明；本简历未新增物理验证。

### 20. Lite3 四足机器人 / 无外挂臂

配置 ID：`lite3.no_external_arm.LITE3_VELOCITY_RSL_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：四足 / 足式运动；运动控制器：`LITE3_VELOCITY_RSL_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：用户明确的当前项目限制。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：特殊限制：当前项目中的 Lite3 不能上楼，也不能下楼。

证据范围：源码接线与声明；本简历未新增物理验证。

### 21. Lite3 四足机器人 / 携带 Z1 + Z1 夹具

配置 ID：`lite3.z1.LITE3_VELOCITY_RSL_CFG`。

部署：**当前挂载筛查拒绝**。裸载对照为稳定的内在倾斜姿态（恒定 18°、前后足受力 2/46 N 严重偏载、反力为体重 82%）；装载 Z1 后同一 fixture 下倾角增加 77°、对地反力降为 0（完全翻倒）。差分明确指向 5.2 kg 顶部载荷；但裸载基线未达门禁，结论限于当前挂载位置。

运动：四足 / 足式运动；运动控制器：`LITE3_VELOCITY_RSL_CFG`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：外挂；末端：`z1_gripper`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=不支持，下楼=不支持；依据：用户明确的当前项目限制。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|部署被阻止|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|部署被阻止|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边；携臂整机的移动稳定性与当前运动控制器验证。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：特殊限制：当前项目中的 Lite3 不能上楼，也不能下楼。；外挂机械臂属于本组合机器人，不另占一个机器人池成员。；移动、持物行走及上下楼均须按携臂后的完整配置重新验证。

证据范围：当前未缩放挂载的静止 fixture；不包含携臂行走、持物导航、楼梯或边走边操作。

### 22. Coco 车式转向机器人 / 无外挂臂

配置 ID：`coco.no_external_arm.COCO_ACKERMANN_CFG`。

部署：**目录已注册，实例尚未绑定**。目录与构建器注册；实际会话中仍须绑定实例、控制器、地图和执行后端。

运动：轮式 / 车式转向；运动控制器：`COCO_ACKERMANN_CFG`；机械臂控制：`无独立外挂臂控制器`。

手臂：0；来源：无；末端：`无`。

相机：默认配备 `realsense_d455`；安装位姿与图像流待实例绑定。

楼梯：上楼=不支持，下楼=不支持；依据：本稿对当前普通轮式配置采用的不支持策略。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|支持|源码分析|navigate_to_node|待实例绑定|NavigationControllerAdapter|
|`eai.manipulation.pick_place`|不支持|源码分析|无|不支持|无|

`eai.navigation.node_route` 的前提：实例导航适配器；该机器人可达的拓扑地图和目标节点；按当前配置过滤路径、净空和楼梯边。

`eai.navigation.node_route` 的限制：公共 node_route 不自动校验楼梯资格，宿主必须在路线发布前过滤。

`eai.manipulation.pick_place` 的限制：当前配置无受支持的公共抓放后端。

特殊说明：车式转向，不按全向横移或原地旋转机器人分配动作。

证据范围：源码接线与声明；本简历未新增物理验证。

### 23. 独立 UR5 机械臂机器人 / UR5 + Robotiq 2F-85

配置 ID：`ur5.standalone_fixed_ur5.host_runtime`。

部署：**独立/专用配置待接入本次机器人池**。用户明确要求独立 UR5；存在独立资产与控制/验证工具，尚非 Env DIY 的顶层 robot key。

运动：机械臂 / 固定基座；运动控制器：`无（固定/专用配置）`；机械臂控制：`UR5_IK_CFG`。

手臂：1；来源：独立机械臂机器人；末端：`robotiq_2f85`。

相机：实验要求默认相机，但当前工程尚未完成该配置的相机适配。

机械臂限制：5 kg；抓放限高 0.824073 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=不适用，下楼=不适用；依据：固定基座或空中配置，不使用地面楼梯能力。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|不支持|源码分析|无|不支持|无|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待接入|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的限制：固定/独立机械臂配置不提供移动导航。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：固定安装的独立机器人，不依赖移动底盘，不具备导航或自行移动能力。

证据范围：专用资产/后端存在；不代表已接入任意场景的机器人池。

### 24. 独立 Z1 机械臂候选机器人 / Z1 + Z1 夹具

配置 ID：`z1.standalone_fixed_z1.host_runtime`。

部署：**独立/专用配置待接入本次机器人池**。存在独立 Z1 资产，但尚非 Env DIY 顶层 robot key；固定基座安装与后端待确认。

运动：机械臂 / 固定基座；运动控制器：`无（固定/专用配置）`；机械臂控制：`Z1_IK_CFG`。

手臂：1；来源：独立机械臂机器人；末端：`z1_gripper`。

相机：实验要求默认相机，但当前工程尚未完成该配置的相机适配。

机械臂限制：3 kg；抓放限高 0.798026 m，相对机械臂安装基座。限高测量点、载荷质量口径、下界与径向工作空间未知。此数值不是整机携臂移动载荷或整机满载验证结论。

楼梯：上楼=不适用，下楼=不适用；依据：固定基座或空中配置，不使用地面楼梯能力。

|技能|配置支持|能力依据|操作|实例可用性|后端|
|---|---|---|---|---|---|
|`eai.navigation.node_route`|不支持|源码分析|无|不支持|无|
|`eai.manipulation.pick_place`|支持|源码分析|pick, place|待接入|ArmPickPlaceAdapter|

`eai.navigation.node_route` 的限制：固定/独立机械臂配置不提供移动导航。

`eai.manipulation.pick_place` 的前提：实例绑定到机械臂运行时；有效夹具和安装位姿；对象与目标可达且恢复轨迹可达；应用用户给定的载荷和相对安装基座高度限制；确认限高测量点与载荷质量口径。

`eai.manipulation.pick_place` 的限制：局部静止操作；不支持任意对象姿态。；理想化附着/释放运行时，不等于已验证真实摩擦夹持或满载能力。

特殊说明：存在独立 Z1 资产；固定基座和控制接线尚需显式配置，不能从资产存在推断可部署。

证据范围：专用资产/后端存在；不代表已接入任意场景的机器人池。

## 实验相机与传感器规则

相机是机器人默认属性，不单独产生简历。无键盘控制或 Navigation I/O 工具。传感器实际装配变化仍须更新实例配置身份与 generation，但不预先穷举成不同机器人类型。每个宿主最多一个外挂 Z1。

Orsus、独立 LiDAR、C01 三者互斥。Carter + Z1 不得再加 Orsus/LiDAR/C01；Go2 + Z1 不得再加 Orsus。其他已拒绝臂配对也不因附加传感器重新成为合法组合。

工程现有相机接线仍须核对实际图像来源；C01 单独不能证明具有可发布图像的相机来源。SimpleCar 和空中平台已有内建相机配置，其余实验相机的型号与安装方式待实例明确。

默认相机声明不替代具体相机硬件与图像接口的接线。相机发布也不等于已经提供识别、分割、目标定位等 skill。

## 实验配置中文索引

下表每行对应 JSON `resumes` 中一份完整简历；核心能力读取同 `profile_id` 的上文，附件名称全部在此展开。实例数量不固定，任务可对同一行创建多个不同 `robot_id`。

|完整简历 ID|核心配置 ID|外挂臂（所有配置均带相机）|部署状态|
|---|---|---|---|
|`carter.CARTER_DIFF_CFG.v0001`|`carter.no_external_arm.CARTER_DIFF_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`carter.CARTER_DIFF_CFG.v0002`|`carter.z1.CARTER_DIFF_CFG`|Z1 + Z1 夹具|附条件：仅有静止挂载证据|
|`simple_car.SIMPLE_CAR_PLANAR_CFG.v0001`|`simple_car.no_external_arm.SIMPLE_CAR_PLANAR_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`pepper.PEPPER_PICK_PLACE_CFG.v0001`|`pepper.no_external_arm.PEPPER_PICK_PLACE_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`go2.GO2_MIXED_STAIRS_CFG.v0001`|`go2.no_external_arm.GO2_MIXED_STAIRS_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`go2.GO2_MIXED_STAIRS_CFG.v0002`|`go2.z1.GO2_MIXED_STAIRS_CFG`|Z1 + Z1 夹具|附条件：仅有静止挂载证据|
|`go2.GO2_EXTREME_PARKOUR_TEACHER_CFG.v0001`|`go2.no_external_arm.GO2_EXTREME_PARKOUR_TEACHER_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`go2.GO2_EXTREME_PARKOUR_TEACHER_CFG.v0002`|`go2.z1.GO2_EXTREME_PARKOUR_TEACHER_CFG`|Z1 + Z1 夹具|附条件：仅有静止挂载证据|
|`b2.B2_MIXED_STAIRS_CFG.v0001`|`b2.no_external_arm.B2_MIXED_STAIRS_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`b2.B2_MIXED_STAIRS_CFG.v0002`|`b2.z1.B2_MIXED_STAIRS_CFG`|Z1 + Z1 夹具|附条件：仅有静止挂载证据|
|`m20.M20_ROUGH_RSL_CFG.v0001`|`m20.no_external_arm.M20_ROUGH_RSL_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`m20.M20_ROUGH_RSL_CFG.v0002`|`m20.z1.M20_ROUGH_RSL_CFG`|Z1 + Z1 夹具|附条件：仅有静止挂载证据|
|`scout.SCOUT_DIFF_CFG.v0001`|`scout.no_external_arm.SCOUT_DIFF_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`scout.SCOUT_DIFF_CFG.v0002`|`scout.z1.SCOUT_DIFF_CFG`|Z1 + Z1 夹具|附条件：仅有静止挂载证据|
|`mushr_v2.MUSHR_ACKERMANN_CFG.v0001`|`mushr_v2.no_external_arm.MUSHR_ACKERMANN_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`g1.G1_DWAQ_CFG.v0001`|`g1.no_external_arm.G1_DWAQ_CFG`|无外挂臂|已注册，但运行时资格被阻止|
|`cf2x.QUADCOPTER_GOAL_SKRL_CFG.v0001`|`cf2x.no_external_arm.QUADCOPTER_GOAL_SKRL_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`iris.PEGASUS_IRIS_POSITION_CFG.v0001`|`iris.no_external_arm.PEGASUS_IRIS_POSITION_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`pegasus.PEGASUS_X4_POSITION_CFG.v0001`|`pegasus.no_external_arm.PEGASUS_X4_POSITION_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`lite3.LITE3_VELOCITY_RSL_CFG.v0001`|`lite3.no_external_arm.LITE3_VELOCITY_RSL_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`coco.COCO_ACKERMANN_CFG.v0001`|`coco.no_external_arm.COCO_ACKERMANN_CFG`|无外挂臂|目录已注册，实例尚未绑定|
|`ur5.standalone_fixed_ur5.host_runtime.v0001`|`ur5.standalone_fixed_ur5.host_runtime`|无外挂臂|独立/专用配置待接入本次机器人池|
|`z1.standalone_fixed_z1.host_runtime.v0001`|`z1.standalone_fixed_z1.host_runtime`|无外挂臂|独立/专用配置待接入本次机器人池|

## 需要审核的项目

1. UR5/Z1 限高对应的点：物体原点、抓取点，还是末端执行器参考点；载荷是否包含夹具/工具质量。
2. Go2 稳定版已按用户确认标记可上楼、下楼仍待确认；Go2 越野版、B2、M20、G1 的路线专项资格继续审核。Lite3 已明确不支持上下楼。
3. 目前静止通过的携臂组合，是否需要继续验证携臂移动、持物导航和楼梯；现稿不继承裸机资格。
4. 独立 UR5 的实际安装坐标与对象限制（夹具固定为 Robotiq 2F-85）；独立 Z1 候选是否保留；Pepper/G1 的自有载荷、工作空间另填。
5. 审核通过后再落地运行时 schema、注册与资格检查；本轮仅形成完整固定 JSON 和对应中文简历。
