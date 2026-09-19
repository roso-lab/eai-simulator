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
产生可用 convex representation。probe 在完整 collider inventory 闭合前停止，没
有执行双机固定点承载、联合路线、成员退出或重新获取；这不是双机承载失败，也不能
用公开尺寸、视觉包络或手工 AABB 代替 cooked PhysX shape。后续必须修复源碰撞资产
并重新生成、重跑原生 probe。

City v8：`tmp/city_benchmark/service_probe_stage18_20260919_v8/` 完成一次真实
CPU PhysX 服务诊断。`guide_people` 提供约301个逐步人员轨迹样本并读回完整集合区
到达；`control_traffic` 的 hold/release/divert 各约256个运行轨迹与安全样本；
`report_incident` 产生独立 dispatch receipt，并在报告后保留约26个连续 access
样本。报告为 `status=completed`、`candidate_witness=evidence_complete`、
`sources_unchanged=true`，同时明确 `formal_episode_binding=false`、
`full_city_service=pending_formal_episode_geometry`、`scoreable=false`。因此这
是可审计的候选服务后端诊断，不是正式城市题包业务通过。

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
