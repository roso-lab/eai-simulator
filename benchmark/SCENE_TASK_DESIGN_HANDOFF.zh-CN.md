# HeteroCoord 场景任务设计与新版论文接手说明

更新：2026-10-20。状态：设计交接稿，尚未完成本轮任务的物理、oracle、evaluator 或 common 配对认证。

## 1. 当前交付与依据

本轮按用户明确要求，保留 30 场景既有事实和任务主线，完成两份文档的新版论文对齐。详细故事、来源和历史确认只在 [30 场景任务大纲](SCENE_TASK_OUTLINES.zh-CN.md) 维护；本文件提供索引、评测边界和工程交接顺序，避免复制长故事产生两套规则。本次修订授权不将新增设计回写成用户历史确认。

阅读顺序：

1. 论文 `/home/ljy/workspace/CVPR2026_Template` 的正文第 3–5 节（`sec/3_scope_architecture.tex`、`sec/4_benchmark_design.tex`、`sec/5_evaluation_protocol.tex`）和补充材料 `sec/X_suppl.tex`、`sec/supp_dynamic_events.tex`：当前评测、事件与认证依据。
2. 大纲中的 [新版论文对齐规则](SCENE_TASK_OUTLINES.zh-CN.md#新版论文对齐规则2026-10-20) 及下方逐卡索引：本轮有效设计和待绑定需求。
3. `COMMON_ANCHOR_CONTRACTS.zh-CN.md`：保留 11 个 discussion-v2 讨论合同身份及语义边界；历史指标/事件说明与新版有差异时按前两项核对，不直接沿用。
4. `EXPERIMENT_IMPLEMENTATION_PLAN.zh-CN.md`、`TODO.zh-CN.md`、`README.md`、根目录 `BENCHMARK.md`：查工程接口和旧基线。其旧 catalog 数量、认证状态或旧 D4/D5 描述不等于本轮任务已经实现。

历史“用户逐场景提供观察后确认”的工作流只说明证据来源，不是本轮新增指令；本轮已获授权完成两文档修订，不再逐卡重复请求既有授权。后续新增事实、替换主线或认证仍应准确登记其来源和状态。

## 2. 身份、状态与覆盖

- **资产与任务分开。** 30 个完整资产目录为三组各十个；一个场景可有多个任务/难度/事件版本，但不是多个独立场景。两个 restaurant 是不同资产：生产目录为一次性米其林服务，服务目录为有大后厨/仓库的持续运营任务。
- **领域如实登记。** 当前主任务终端价值预审为生产 3、服务 17、生活/公共 10。生产仅 candy_shop、warehouse、warehouse_02；生产目录其余七个故事预审属服务。距论文生产十槽位的差额七如实列缺口，不能改目录名或把一个资产的多个变体当新增槽位。
- **领域仍待裁定。** 家庭持续可用性、工厂救援、设施异常处置、学校清场等边界实例保留终端谓词和理由，按双人标注/第三人裁定流程登记；未决实例不进入领域分层估计。不用补齐十/十/十来反向替换已保留主线。
- **历史确认保留。** 29 张卡记录主线已确认；room_02 为事实与找物意图已明确、整体大纲未升级确认。当前 30 张卡均有新版优化层，该层是依据论文提出的设计，不是历史确认或工程认证。
- **四种覆盖分开。** 讨论合同的语义承载、正式实例的必需关系、物理/评测认证、跨域 common 配对分别登记。历史“8/11 已涉及”只表示语义承载，新增三个复合提案不把它改成 11/11；认证计数须由实际 registry 生成。

论文九个 common anchor、11 个 discussion-v2 合同、30 个场景、base instance 和条件/重复是不同计数。生产–P3、服务–P4、生活/公共–P1 为 typical-only；common-capable 也不代表任意方向或子型已认证。每个实例先冻结完整签名和逐义务 P3-J/S 子型，再以 `P3 > P2 > P4 > P1` 得主标签；关系专属评分使用完整签名，不能把次要 P1/P4 承载当作对应主标签单元已满。

## 3. 30 场景接手索引

下表按资产目录组织，P/S/L 是任务终端领域预审。状态与详细观察见链接任务卡；所有卡的物理执行和 common 资格仍待认证。接手时优先读取各卡“依据新版论文提出的优化”，再查其历史来源和能力约束。

| 完整资产身份 | 当前任务/详细任务卡 | 领域预审 | 本轮接手重点 |
|---|---|---|---|
| `usd/scene/Living and public space/home_02` | [宠物饮水与喂食准备](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-home-02) | L | 双水源合法替代；饮水/喂食共同受影响工作 |
| `usd/scene/Living and public space/house` | [庭院用餐与阅读准备](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-house) | L | 联合搬桌与单体搬椅；支撑不重复计持续服务 |
| `usd/scene/Living and public space/office` | [跨层会议准备与来访接待](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-office) | L | 会议/访客/物料分验；实际梯段容量与完整退出 |
| `usd/scene/Living and public space/room_01` | [别墅多客房入住准备](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-room-01) | L | 按客房齐套交接；各房流水而非全楼屏障 |
| `usd/scene/Living and public space/room_02` | [居家归整、空地查找与房间送物](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-room-02) | L | cf2x 仅观察；信息前置/责任接续/准入分开；整体确认状态保留 |
| `usd/scene/Living and public space/room_03` | [家庭聚会窗口的餐厨侍应保障](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-room-03) | L | 消耗与补给可行余量；服务历史不可被最终补满擦除 |
| `usd/scene/Living and public space/room_04` | [家庭活动用品与餐饮配送准备](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-room-04) | L | 三条配送分验；阳台门只约束餐点路线 |
| `usd/scene/Living and public space/room_05` | [家庭来客接待与空间恢复](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-room-05) | L | 客人阶段与冰箱顺序；资源准入交接另提案 |
| `usd/scene/Living and public space/room_06` | [跨层图书接力与重物联合搬运](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-room-06) | L | 串行双阶段；无人机不能替代联合搬运的依据 |
| `usd/scene/Living and public space/school` | [放学后校园清场与学生离校引导](SCENE_TASK_OUTLINES.zh-CN.md#scene-living-school) | L | 完整区域与学生集合；自主引导合法，无持续巡查 |
| `usd/scene/Production and logistics/bar` | [酒吧订单服务、跨层取物与低电量接替](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-bar) | S | 未完成服务区间内告警；运营限制/电池/D4 分开 |
| `usd/scene/Production and logistics/candy_shop` | [二楼库存向一楼柜台补货与订单配货](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-candy-shop) | P | 普通补货与齐套版分开；楼梯/天井替代合法 |
| `usd/scene/Production and logistics/coffee_shop` | [双入口突发来客与咖啡制作服务](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-coffee-shop) | S | 两门统一队列；逐客订单与逐次门状态 |
| `usd/scene/Production and logistics/consultation_room` | [患者接诊与医生助理协作](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-consultation-room) | S | 医生内部流程与跨角色依赖分开；治疗接口边界 |
| `usd/scene/Production and logistics/factory` | [火灾救援与动态障碍协作](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-factory) | S | 初次派工不算交接；demo 集结版与功能验收提案版 |
| `usd/scene/Production and logistics/factory_02` | [厂区异常物体发现、复核与清除](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-factory-02) | S | 两次必要接续；检测通道失效后的合法替代 |
| `usd/scene/Production and logistics/minimarket` | [便利店巡检、水渍处置与垃圾清理](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-minimarket) | S | 三桶独立容量；等待或换桶均合法；警示不等于清除 |
| `usd/scene/Production and logistics/restaurant` | [米其林餐厅顾客接待、桌边点单与上菜](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-restaurant) | S | 顾客—桌位—订单绑定；单成员连续服务合法 |
| `usd/scene/Production and logistics/warehouse` | [仓库货物整理、清障与重物联合搬运](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-warehouse) | P | 重物不可拆联合义务；失效保留支撑和占用 |
| `usd/scene/Production and logistics/warehouse_02` | [金色货物搜索、跨层交接与室外集中](SCENE_TASK_OUTLINES.zh-CN.md#scene-production-warehouse-02) | P | 三种运输链；上层必要交接；完整目标去重 |
| `usd/scene/Service and operations/city_01` | [突发车祸火灾城市应急响应](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-city-01) | S | 视觉事故/服务确认/实际人员车辆结果分开 |
| `usd/scene/Service and operations/ruined_city` | [震后可步行人员搜索、清障与撤离引导](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-ruined-city) | S | 可步行人员；实际所用路线有效，允许绕行 |
| `usd/scene/Service and operations/city_02` | [CBD 街道巡查、异常处置与维修接续](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-city-02) | S | 清移/警示上报/维修接续各自终端，避免无界等待 |
| `usd/scene/Service and operations/forest` | [山林火情发现与盘山路通行保障](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-forest) | S | 一次检查；仅真实影响响应的道路建立前置 |
| `usd/scene/Service and operations/hospital` | [病房物品准备与患者送诊](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-hospital) | S | 用品与送诊分验；坐稳、载人推行和共同到达 |
| `usd/scene/Service and operations/land` | [马拉松赛道秩序维护与队伍伴随保障](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-land) | S | 路口/伴随需求窗；支持约束与独立持续供给分开 |
| `usd/scene/Service and operations/restaurant` | [餐厅持续接待、供餐、补货与岗位接替](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-restaurant) | S | 窗口内等候者和未完成订单全部收尾；岗位接替另注册 |
| `usd/scene/Service and operations/stadium` | [足球馆看台空地协同巡检与异常处置](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-stadium) | S | 异常去重；放牌上报/垃圾入桶分验，无观众疏散 |
| `usd/scene/Service and operations/subway` | [废弃地下车站空地协同救援与人员撤离引导](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-subway) | S | 连续两段楼梯；物资用途和人员行为待绑定 |
| `usd/scene/Service and operations/tram_station` | [城郊有轨电车站居民回家引导](SCENE_TASK_OUTLINES.zh-CN.md#scene-service-tram-station) | S | 有限居民到正确家门；无持续接待或电车调度 |

## 4. 从任务语义到可评分实例

### 任务卡与技能边界

每卡保留“事实/来源/未知、终端目标、任务链、能力与信息、原合同候选、验收和绑定需求”；新版优化层统一补上义务图、必需关系与合法替代、信息权限、外部里程碑/终止、适用评测/独立变体、八维难度方向及待绑定。优化内容不能反向写成截图已观察到的事实。

机器人从 `usd/robot` 合格候选池选择，允许多型号、多实例、部分能力重叠和不同起点。公开池、初态和预算在比较中固定，系统决定联盟/分工；不能通过为某算法定制不同候选池制造优势。示例型号不代替当前 capability profile、provider、载荷、可达性和控制器验证。

主轨为**有物理约束的理想化技能**：注册的动力学、接触、执行器、载荷、碰撞和感知约束继续生效，外部物理谓词成立后技能才返回成功。未注册的感知/控制随机性另属执行压力轨。搜寻不能直接读取隐藏真值；人物、液体、颗粒物、制作、医疗及电量过程须明确实现和抽象边界。

历史 2026-10-13 远端基线曾包含拓扑导航、多层路线、box transport、aerial pick/place 和 cooperative runtime；这是当时记录，不是当前技能认证。接手时读实际代码/manifest，特别核对 Iris/Pegasus position-robot 等 provider 限制。room_02 的 cf2x 仍仅观察、无载荷；home_02/house 的吊运须另选有真实载荷能力的配置。

### 必需义务和外部证据

- 合同在运行前确定。允许同一成员连续完成的交接、允许绕开的门/楼梯不能算该实例必需关系。若专测交接/资源，则另建有必要性依据的版本，不事后处罚合法策略。
- 必需关系未到达也留零分；不因失败、未换人或没进资源就降标签。初次分配不是已有责任交接；纯条件依赖不伪造 release/accept；重复日志/重试/消息不增加义务权重。
- P3-J 检查全部参与者、同步、共同支撑和稳定完成；同一支撑不再算 P3-S。P3-S 注册需求和服务谓词，不能把一次搜索或重复订单自动称持续覆盖。P4 要真实竞争、容量单位、许可与物理进出/释放，单障碍阻塞不足。
- 公共观察与 evaluator-only 字段分离。交接最低语义包括工作/目标身份、已完成/剩余工作、位置/范围、状态版本与时间、约束和状态来源；接收者须实际接续。物理抓持交接可先抓后放，逻辑责任转移用自己的正式事件，不强制混成一个顺序。
- 里程碑绑定稳定 ID、外部谓词、激活、权重、稳定窗口、必需性、单调性、可见性和去重。最终进度为一不保证成功，还须全部必需条件及全局终止成立；安全停止可以终止运行但不等于任务成功。

### D1–D6 的实施边界

| 维度 | 接手时必须落实 |
|---|---|
| D1 初始组织 | 首次提交计划、初始声明及七类约束；后续重规划不回写。认证不可行与未验证分开，正确拒绝还需无派工的外部安全停止。 |
| D2 通信适应 | 任务起点配对，团队共享容量 `C0 > C1 > C2 > C3 > 0`，固定队列与协议；拥塞由系统流量产生。报告 nominal 和各级成功率，外生延迟/丢包/断链另列因素。 |
| D3 时序协作 | nominal 可行实例按完整签名选 P2、P3-J/S、P4 义务，P1 分配归 D1；P2 端点/状态/顺序同时成立，P3-S 默认全过程，P4 需完工释放、无违规且无认证停滞；必需但未到达得零。 |
| D4 动态重配置 | 跨系统同一验证的事件前检查点及公共历史；注册历史初始化接口，不兼容者登记该面板范围外。主变体局部任务功能失效、无预告且 onset 发送状态；保留其他能力、机体、物体、支撑及占用。 |
| D4 工作评分 | 事件前冻结受影响及经前置、服务依赖、共享替代机器人/工具/空间连通的竞争工作；主面板须认证失效后仍可行。截止型 `q=e`，时间价值/独立持续供给型 `q=(e+φ)/2`，类型/使用窗/稳定窗及权重事先固定。通知、重规划、功能恢复不等于外部工作恢复。 |
| D5 跨场景迁移 | 主面板只生产→服务 P2；同一源记录、无目标示范/参数更新。逐对认证义务图、完整签名、P3 子型、能力/物体功能和结果语义，以及目标必须改变源安排且有合法解。适应单元检查点与完整任务执行分开。 |
| D6 协作成本 | nominal 典型任务以成功门控通信比特与仿真终止时间，失败为零；参考尺度用独立校准协调器成功运行中位数。路径、等待、运动量等作诊断，原始省资源结论需成功/进度非劣。详式见大纲共用规则。 |

D4 healthy/loss 同工作集评分并报告点数差，healthy 不作分母。系统自身首个合格检查点的配对只作因素对照/端到端诊断，并报进入覆盖，不能替代跨系统主分。事件前检查点早于最早通知；发送、到达、消费时间分开。主面板失效观察窗覆盖待评工作使用/截止期；计划性延后不延长原截止或固定需求窗。不可避免不可行另设声明/安全停止面板，方法自己造成的失败不重新分类排除。

失效的接口/依赖、当前动作/持物/支撑/锁后果必须登记。整机退出、备份启用、通知和恢复策略是各自条件；恢复沿当前轨迹进行，不重置位置、对象、历史、预算或锁。电量只有校准荷电单位、容量、消耗/充电、保护规则后才称电池模型，否则标运营资源限制；bar 的原告警情节与 D4 局部功能失效变体分开。

D5 每个评分家族冻结时至少 30 个认证 case；不足报 NA 和缺口，不拿两场景相似故事充数。无需改变安排的 case 单列 no-adjustment 控制；目标 checkpoint 不预选答案，不从源场景拷贝物理状态。声称源参考有帮助还需同目标检查点的有/无参考对照；一个适应单元的角色、物体、顺序和后续操作是共同验收条件，不能拆成多份得分。

### 难度与统计单元

八维为依赖深度、协作宽度、能力稀缺性、竞争、风险、通信、分支、不确定性；方向与四档边界先冻结，等级 `1 + max(八维分箱)`。各卡仅给调整方向，不按层数、求解器耗时或被测系统成功率赋级；数值待地图/技能/校准绑定。

放置、spawn、物理可行见证回放、检查点、事件与可评分性分别过门。认证未知不能标不可行；基础设施丢失按论文重跑/残余未知界限处理，方法超时或缺响应保留风险集。base instance 不含条件，阶段/处理/三次重复不是独立任务；标量端点先平均重复再平均实例，固定单元权重，时间到事件端点使用论文的竞争风险估计。单元覆盖不足报 NA，不重分权重；六个维度不合成总分。

## 5. 优先独立变体与覆盖缺口

以下均为依据新版论文提出的候选，不并入默认任务，也不将历史语义覆盖从 8/11 改为 11/11：

| 候选 | 必须新增/验证的交互 | 详细设计 |
|---|---|---|
| room_05 / C-P2P4-01 | 合法资源准入成为正式责任接收条件；准备/规划、accept、物理进入与释放分开，有真实有用竞争。 | 大纲“room_05：C-P2P4-01 资源准入交接” |
| warehouse / C-P3JP4-01 | 至少两组真实联合重物任务竞争同一资源；明确容量计量、等待和组内支撑，新增物/区先验证。 | 大纲“warehouse：C-P3JP4-01 多组联合搬运” |
| room_03 / C-P3SP4-01 | 持续补给实际依赖有限备餐资源，缓冲/消耗/移动与轮换有可行见证；不凭大地图添加充电。 | 大纲“room_03：C-P3SP4-01 有限备餐资源” |
| candy_shop → 服务目录 restaurant / D5 P2 | 两侧独立匹配“备好一批→必要交接→末端操作”完整变体，目标有必要适应和合法解。不得直接拼接两个完整且不等价的主故事。 | 大纲“candy_shop → Service and operations/restaurant：D5 P2 候选家族” |

D5 若餐厅不可拆的持续服务无法与源工作/结果语义匹配，配对不认证，不能悄悄删除目标义务伪造同构。其他方向/原语另列面板，不能用来补主面板缺口。典型任务中的合法替代和专门机制变体需不同合同版本。

生产领域七槽位缺口另列后续选题：只在真实物料源、工位和交付面支持时，提出独立物料齐套/补给任务；场景终端价值、资产槽位归属和许可/相似性仍需正式裁定。新增同资产任务有助于机制覆盖，不能直接增加独立场景数。

## 6. 接手顺序与交付检查

1. 从索引找到完整资产身份，读取详细卡的历史事实和新版优化；确定唯一终端价值预审及待裁定理由，保持 room_02 历史状态。
2. 形成一个明确版本：默认主线或单独变体；冻结义务图、合法替代、拟必需完整签名/子型、主标签及端点。不能把长任务里同时出现两个原语直接当复合交互。
3. 地图侧提供楼层/地形连接、按形态/载荷可达性、合法飞行、目标/取放面、等待位、真实资源和危险/权限边界；任务侧标公开、现场发现、通信及隐藏验证信息。缺项登记待绑定，不猜坐标或编时限。
4. 技能侧绑定当前版本和依赖图，验证观察、取放、门、联合支撑、人员响应等适用接口。Fleet 只注册能力、路由消息、执行资源/故障和审计，不替被测系统选替补或生成计划。
5. evaluator/oracle 侧冻结里程碑/稳定窗/终止/horizon、难度向量和认证门；D4 加公共历史、失效物理后果与剩余可行性，D5 加逐对证书、必要适应和目录 case 数。重新登记资产、合同、生成器/能力/控制器哈希，不继承旧 catalog 认证。
6. 日志关联 base instance、系统、条件、branch、repeat、工作/目标/资源、计划/观察/消息版本及外部事件；终止原因、任务成功、安全停止、不可行和未知分别留证。私有思维链/自报信心不作为评分证据。
7. 文档核对 30 个唯一资产、两个餐厅、29+1 状态、P=3/S=17/L=10 预审、11 合同语义与未认证 common 边界一致；检查链接、表格和 `git diff --check`。本轮仅文档修订，不新增运行测试。

代表性反例验收：允许的单机连续执行/绕行不误罚；必需义务未到达仍为零；服务中断后恢复不擦历史；局部功能失效后剩余能力可继续使用；未完整退出不因软件退锁而释放；低成本失败 D6 为零；无需改变安排的迁移只进控制目录。

当前交付仍是设计与接手说明。坐标/阈值、物理技能、外部 evaluator/oracle、检查点/事件、正式 catalog 迁移、common 认证及 D1–D6 实验矩阵和结果均须后续工程验证；文档优化不构成已发布或已执行声明。
