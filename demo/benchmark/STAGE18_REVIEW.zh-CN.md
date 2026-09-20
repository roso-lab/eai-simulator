# 阶段18：可靠技能与真实业务接线复核

本阶段尚未完成，所有场景保持 `scoreable=false`。本记录区分后端诊断、业务参考
运行和正式题包；前者通过不会自动赋予后两者通过状态。

## 已核实证据

House：`tmp/house_native_backend_stage18_20260919_v3/` 在 CPU PhysX 中运行约
51.5秒，进程正常结束。482/602物理步取得真实320×240 RGBA帧、唯一观测ID、
两件物体身份和有效的 blocked 通路测量。原始NPY像素摘要与报告一致。
`backend_report.json` SHA256 为
`0c81fb2a9983c5faa670497550038b1949e018dce21b59e63fd9c0e809cd984e`。

House 传感器是有范围和原生射线遮挡限制的全向理想传感器。真实相机画面朝向门窗，
不能把场景身份读回称为RGB识别。首次运行暴露射线命中自身和相机清理参数错误；
已修复，只排除观察者自身刚体，并保留排除记录。其他机器人仍造成遮挡；住户通路
检查不排除任何机器人。此次前后快照无业务干预，不能证明家具恢复或住户通路清空。

Warehouse：v4 单机纸箱约移动1.976m，载具约移动2.008m，运输中纸箱最低离地
0.0687m；lower/detach 后新物理步产生 dock ready 回执。它是单机微诊断。
独立复查发现姿态改变使增量锚点积累约1.153mm残差、dock曾允许未封存字典、
多约束detach可能部分失败，以及anchor质量没有作为拒绝条件。修复和重新运行
验收必须针对新源码，不用v4报告证明修复通过。

修复后 Warehouse v5：`tmp/warehouse_stage18_native_witness_20260919_v5/`，
CPU PhysX 约42.3秒正常结束，纸箱1.9727m、载具2.0059m，最低离地0.06867m。
升降回程joint readback精确恢复原始锚点；末端anchor误差约4.76μm、姿态误差
约1.49μrad。新seal链绑定的释放后dock为ready。报告SHA256：
`fef14edb5db6f4db2048f033191c206fbfa634a7565dd03882d42cce14f7daaa`。

Warehouse paired v9：`tmp/warehouse_stage18_paired_native_20260919_v9/` 使用
`env_isaaclab`、CPU PhysX 和 `QUERY_RIGID_BODY_WITH_COLLIDERS` 原生 shape 查询。
`rigid_body` 与 `finished` callback 能到达，但
`/World/envs/env_0/carter_1/Carter/caster_swivel_right` 的 deferred convex
representation cooking 返回 `RESULT_ERROR_INVALID_PARSING`，报告为
`status=blocked`。v9 同时归档了 73 条实际 USD collision inventory；失败 prim 是
带 `PhysicsMeshCollisionAPI`、`physics:approximation=convexHull` 的 `Xform`，但未
产生可用 convex representation。probe 在完整 collider inventory 闭合前停止，没有
执行双机固定点承载、联合路线、成员退出或重新获取；这不是双机承载失败，也不能
用公开尺寸、视觉包络或手工 AABB 代替 cooked PhysX shape。后续必须修复源碰撞资产
并重新生成、重跑原生 probe。

Warehouse paired v10（2026-09-19 资产修复后）：根因确认为 Carter 源资产中
`caster_swivel_left/right` 与 `caster_frame_base` 是**完全空的 Xform**（无任何子
prim/网格），却被施加了 `PhysicsMeshCollisionAPI` + `convexHull`——凸包烹饪无源
可解析，故 `RESULT_ERROR_INVALID_PARSING`、convex_count=0。修复方式为从这 3 个
prim 移除 `PhysicsCollisionAPI`、`PhysicsMeshCollisionAPI` 与
`physics:approximation`（`PhysicsRigidBodyAPI`/`PhysicsMassAPI` 保留，关节链不
变；轮子碰撞来自独立解析 `Cylinder` prims，不受影响）。没有添加任何代理形状。
本地修复备份与哈希：原 `ef86dc6dc8e3442e01cbe0549c72bb92ecc54c219ddc98ea3270bd0f5117f0cc` →
修后 `86de9633ed450fbfbfb17473d161bab4b12d9808cf55a1a24d453c0b0364604f`，原件存
`usd/robot/carter/carter.usd.bak_pre_caster_fix_20260919`。**该修复尚未发布到
provider revision，属本地资产修复，需维护者按 provider 流程发布后才能作为发布资产。**

v10 复跑（`tmp/warehouse_stage18_paired_native_20260919_v10/`，`env_isaaclab`、
headless、CPU PhysX、串行锁，进程 25.8 s 正常结束）：cooking 失败消失，collider
inventory 由 73 条变为 70 条（恰好移除 3 个缺陷 prim），caster 条目只剩两个带
`PhysicsCollisionAPI` 的解析轮子 `Cylinder`——**形状闭合门禁通过，v9 的资产
blocker 解除**。probe 随即推进到下一道独立门禁并 fail-closed：
`candidate measured collider AABBs collide or touch during
settled_before_attachment`。当前诊断候选站位为木箱 x=-10.625 两侧 M20（-9.84，
距箱心 0.785 m，公开 reach 0.8 m）与 Carter（-11.36，距箱心 0.735 m，公开 reach
0.6 m），静置后按 `actual_enabled_collider_body_local_bounds` 计算的 AABB 相互接
触。这是 STAGE18 已记录的联合队形几何问题（连接器 reach 与碰撞包络互相挤压，
不得以扩大通路或缩小机器人解之），不是资产缺陷；旋转/纵列队形方案需实测后另行
验收。双机承载、联合路线、退出恢复仍保持 pending，`scoreable=false` 不变。

City v8：`tmp/city_benchmark/service_probe_stage18_20260919_v8/` 完成一次真实
CPU PhysX 服务诊断。`guide_people` 提供约301个逐步人员轨迹样本并读回完整集合区
到达；`control_traffic` 的 hold/release/divert 各约256个运行轨迹与安全样本；
`report_incident` 产生独立 dispatch receipt，并在报告后保留约26个连续 access
样本。报告为 `status=completed`、`candidate_witness=evidence_complete`、
`sources_unchanged=true`，同时明确 `formal_episode_binding=false`、
`full_city_service=pending_formal_episode_geometry`、`scoreable=false`。因此这
是可审计的候选服务后端诊断，不是正式城市题包业务通过。

## 队形测量战役（v10–v14，2026-09-19 ZCode 会话）

Carter 资产修复（上文 v10）后，settled_before_attachment 门禁经 v10–v14 五轮
headless CPU 迭代测量，结论如下（证据：`tmp/warehouse_stage18_paired_native_20260919_v10..v14/`）：

- **Carter（轮式）静置稳定**：chassis 漂移 ≤±0.006 m（v11 -0.005 / v13 +0.006 /
  v14 +0.005）。注意其 AABB 中心含底盘不对称偏置（本地 x∈[-0.587,+0.126]），
  与 chassis 位姿相差 ~0.23 m，勿混淆。
- **M20（腿式）x 向散布 ±0.2 m、y 向亚毫米稳定**：同一位_spawn -9.620 两次运行
  分别 +0.019 / -0.194（v12/v14）；y 向四次运行中心均为 -11.725±0.0005。
  散布来源于腿式落位混沌，不可通过站位消除。
- **v12 教训**：Carter 出生位误入箱包络（重叠 0.146 m）会被 PhysX 弹飞并推移
  木箱，出生位必须整体在箱包络外。
- **Carter 参与双机承载几何不可行（测量结论）**：support 锚点 z=0.55 m（箱底
  +0.25，fixture 冻结），Carter 根刚体 z=0，0.6 m reach 的水平预算仅
  √(0.6²−0.55²)=0.240 m；其朝箱向碰撞体前伸（轮 0.205 m / 侧 0.247 m）加锚点
  内缩 0.025 m 后，任一朝向的可行窗 ≤8 mm，小于其 ±6 mm 静置抖动。四轮实测
  均以轮-箱或底盘-箱接触 fail-closed。
- **可行替代（已测量支撑）**：双 M20 配对（stage7 D 审查已 noting 可行，
  12kg/2=6kg ≤ 8kg）沿 y 侧翼——M20 reach 0.8、根 z 0.527（水平预算 0.797 m），
  y 向稳定使 30 cm 窗口稳健；v14 实测 M20 远距站位（spawn -9.620，settled
  -9.814）与箱面间隙 +0.042 m 佐证。

**待契约层决策**：将 fixture 公开配对从 M20+Carter 改为 M20+M20（属作者侧
配对契约变更，载荷/容量数值仍满足），或维持现配对并接受"该配对在此支撑高度
不可行"为最终结论。任务生成侧不擅自改冻结 fixture。

## House 干预 probe 战役（v4–v17，2026-09-19 ZCode 会话）

House before/after 干预 probe（P1 代码 + Isaac 实跑）经 v4–v17 十四轮迭代，
证据目录 `tmp/house_native_backend_stage18_20260919_v4..v17/`。结论分层如下：

**已成立（v17 完整链证据）**：双 M20 干预链首次端到端完成
（`status=diagnostic_completed`，十事件契约全记录，occupancy SHA256 前后
断言通过，hall_release closeout 以原生仿真时刻注入）。关键实测：
- 载具配对必须双 M20：v5 证明 Carter（reach 0.6、根 z 0）在扶手椅
  support 锚点（世界 z≈1.0）下竖直距离即超限——与 Warehouse v10–v14 的
  Carter 结论同型，跨场景一致。
- **M20 RL 策略仅前向**：纯 yaw 指令只产生 ~0.057 rad 后失速，弧线指令
  同样无法改变航向；前向指令完全正常（0.3 m/s 指令实测 ~0.15 m/s）。
  v8–v11b 的转向-再前进/路径点/走廊串行控制全部因此失效。最终设计：
  载具以作者诊断出生位（构建期一次位姿写入，before 快照前，全程记录）
  直接生成在实测 standoff（[4.98,12.66]/[4.98,14.34]，z=0.77 阶段12 v5
  校准根高），朝向 = 搬运方向 +x。
- **抬升是搬运前置**：v14 证明 14 kg 扶手椅在地板摩擦下轮足策略无法拖动
  （1500 步零位移）；经连接器目标抬升 0.0371 m（2 关节）后椅子开始移动。
- **双机搬运实测位移 0.67 m**（[4.974,13.5]→[5.648,13.5]，向东）——
  House 场景首次双机物体搬运证据。

**剩余 blocker（精确）**：
1. 椅子在 x≈5.647 精确停住——v15（1500 步@0.15）与 v17（9000 步@0.25）
   位移分毫不差，为硬阻挡而非速度/预算问题；而 parking 目标 [7,13.5] 被
   占据图准入。**疑似客厅东侧地图/USD 不一致**（与 Warehouse 地图漏采
   bug 同类），需对照 USD 实际碰撞体复核该区域。
2. ground_1 撤离净空 0.841 m < 要求 1.077 m（东向射线亦被同区域阻挡；
   ground_3 1.940 ✓、Carter 2.21–6.98 ✓）。
3. `scoreable=false` 不变；干预为作者侧诊断，非业务执行。

**经验记录**：CPU PhysX（Fabric 回退）约 0.3 s/控制步，全预算墙钟受限；
每步 render 12→1 后无改善，瓶颈在物理求解本身。Kit 关闭会丢弃未 flush
的 print（诊断脚本须 PYTHONUNBUFFERED=1）。`_pre_physics_step` 消费
actions 字典即命令通道；set_command 与 actions 写入二者等效。

## City 正式绑定探针首跑（2026-09-19）

`tmp/city_benchmark/service_probe_formal_20260919/`：prepare 以
`--episode-binding formal_binding_20260919/authoring/private.json` 加载绑定，
对照打包语义图机检通过（`geometry_scope=author_frozen_episode_binding`，
4 人 roster/6 后续车/4 集合区含容量与包络推导全部建立）。execute 在首次
服务提交处 fail-closed：`service_backend._submit → source.admit(...)` 的
路由/权限/容量谓词未放行（`service route, authority or capacity not
admitted`，service_backend.py:169）。报告 `formal_episode_binding=false`、
`full_city_service=formal_binding_execution_incomplete`。**下一步**：在
`_run_bound_episode` 的 admission 回调处逐谓词定位（bound roster 的实测
人员路线 vs 绑定声明路线、role 区间、容量计数），属首跑集成校准，
非契约缺陷。纯代码链（绑定构建/审核/goal bundle/冻结）全部离线通过。

## 阶段19 Warehouse 双 M20 承载战役（v2–v6，2026-09-20）

Carter 锚点不可达结论（上文 v10–v14）落地为计划准入注册表（goal_constraints
`_MEASURED_ANCHOR_INFEASIBLE`，含反例测试）后，paired probe 换双 M20 载具
（配对属被测模型方案空间；诊断只需可行对）。证据
`tmp/warehouse_stage19_paired_dual_m20_v2..v6/`：

- **v2/v3**：同种子静置结果逐位一致（东 -9.8604 / 西 -11.6953）——修正早前
  「±0.2 散布」解读：漂移是**确定性**的；且东侧为「滑至接触为止」（出生
  间隙 <~0.18 m 即滑到贴箱），间隙 ≥~0.24 m 时不滑（v12/v14 的 +0.019）。
- **v4**：西侧按确定性漂移精调（spawn -11.45 → settled -11.645，间隙
  +0.244、reach 0.716 ✓）验证漂移模型；东侧 -9.68 仍滑至接触。
- **v5（突破）**：东 spawn -9.620（无滑阈值）+ 西 -11.45——**settled 与
  lift_apex 门禁首次通过**（measured_separated），双关节抬升 + **78 个运输
  样本 + 40 个抬升样本**实测记录；仅运输末态东载具后轮与箱 1.4 cm 擦碰
  （拖行中编队内微移）。
- **v6**：standoff 修改无效果（probe 为出生即位设计，无接近行驶段），
  末态重叠与 v5 逐位相同——确认为拖行期间编队内**确定性**内移 ~4.6 cm。
- **v7（完成）**：东载具出生东移 5 cm（-9.620→-9.570，reach 0.696 ≤ 0.797）
  对冲内移。**全绿**：`status=diagnostic_completed`，四道几何门禁全部
  `measured_separated`（settled / dual_joint_lift_apex / attached_transport_
  end / lowered_before_detach），**78 个运输样本 + 80 个抬升样本**，抬升
  0.0375 m（双关节），释放后新物理步读回与 detach 回执齐全。证据：
  `tmp/warehouse_stage19_paired_dual_m20_v7/`。这是 Warehouse 首个完整
  双机固定点承载链（attach→lift→transport→lower→detach→release）。

控制栈：双 M20 均为 base_velocity 前向指令（M20 策略仅前向，见 House 战役），
运输为两载具同向前向；`_action_readback`/控制器契约/成员映射/测试全部更新
为双 M20（21 项 probe 测试 + 320 项 goal 套件通过）。

## City C1 收敛记录（2026-09-20，probe 20260920e–j）

`tmp/city_benchmark/service_probe_formal_20260920{e,f,i,j}/`。逐层修复后：

- **静态障碍全清**：路线生成重构为「障碍 bbox 栅格化（0.5 m 格、膨胀
  0.2——0.35 会把街道走廊封死形成孤立口袋）+ 4 连通 A* 绕障（对角步切角
  会吃掉余量致灯 Mesh 命中）+ 出生点重定位（原格空闲不吸附；被封闭口袋
  则重定位到目标连通域内最近空格）」。四人全部获得 17–46 航点绕障路线，
  运行时 static_hits 全部为 0。
- **首个正式服务被接受**：探针加 10 s 预热（车流离开出生段）+ 提交重试环
  （被拒时步进 1 s 重建观察，role 区间过期需每次重建）后，
  `guide_people:assembly_candidate_001` 达成 `command_accepted_pending_readback`。
- **剩余设计缺口（精确）**：person_00 的 150 m 斜穿路线经过**事故区**——
  跟随车辆在事故点排队是永久性动态阻挡（重试 20 s 后仍拒）。事故位需
  USD 射线（不可离线复现），绑定的 hazard 本就以
  `freeze_at_episode_instantiation` 延迟，**疏散路线需同样延迟到实例化时
  以事故盘+排队带为禁区重导出**（探针侧用同款生成函数重推并作为绑定实
  例化补全记录）——这是下一步的实现方向，非契约缺陷。

## 当前复查重点

- Warehouse：绝对提升量从不可变初始锚点重新计算，加入变姿态与双约束回程反例；
  原生报告补apex receipt、逐步载具与物体读回；接收方校验约束/释放来源链。
- Warehouse 联合队形：公开1.3×1.65m包络没有对应的原生可行队形证明。M20+Carter
  常规并排约0.90×1.87m，含安全余量超过1.85m候选通路。旋转/纵列方案需实测，
  不得扩大通路、缩机器人或把载荷匹配直接称为完整可行性。
- City：具体 episode 服务实现、受影响人员非循环路径、车辆 hold/release/divert、
  危险区/集合容量/访问区读回已在 v8 候选诊断中产生；仍需由城市团队把人员/车辆
  roster、区域尺寸、路线和动态实体固定进正式 episode。运行时选择的诊断区域不能
  替代正式任务物理绑定；两份独立见证与 factor treatment 仍需固定题包后验收。
- 公开边界独立审查发现baseline额外字段和tuple嵌套可藏私有内容，以及底层writer
  gate约束弱于外层审查；已收修复，baseline/solver严格字段校验、tuple递归私有键
  扫描、底层writer/validator强制pending gate，代理135+49项回归通过。

## 已运行检查

在 `env_isaaclab`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 下，已完成的旧检查包括
House adapter及参考 provider/Warehouse初始定向测试68项、自体遮挡和升降回程修复后
相关35项、加强 dock/detach 和 anchor 检查后的 Warehouse 定向测试62项，以及
City 协议回归138项。本轮 v8 服务运行时增强、paired shape/probe 与 Stage18 gate
聚焦回归为 `222 passed`；运行命令使用 `env_isaaclab` 和
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`。

`stage18_evidence_gate.py` 已对 House v3/Warehouse v5独立读盘核对，两者均为
diagnostic_only，当前来源摘要覆盖为true。门禁包含像素、跨步/跨episode和重复观测
ID反例；paired v7 是 blocked 诊断，不能进入 Warehouse 成功汇总。City v8 的真实
报告已具备独立服务 trace，但因正式 geometry binding 缺失仍保持候选诊断状态。

场景运行使用同一排他锁，未启动ROS。当前没有可调用ZCode工具，仅进行了本地原始
图像核验，不能标记ZCode视觉验收完成。未处理楼梯/RL，也未改原始场景USD。
