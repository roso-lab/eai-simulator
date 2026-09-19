# 阶段15审核记录：四场景串行诊断与服务/边界复核（2026-09-19）

## 阶段出口

本阶段完成了 Warehouse、House、City 的新一轮诊断读回，以及 Factory 单机器人楼梯上行 probe。所有结果仍是作者诊断或协议审核，四场景继续保持 `physical_task_execution_validated=false`、`business_outcomes_validated=false`、`scoreable=false`。

## Factory / F-VERT-01

Feynman 代理两次因服务端 `429 Too Many Requests` 终止，未产生物理交接；主会话随后直接使用 `tools.scenario_generation.vertical_task`，通过 `SimulatorLaunchConfig/open_simulator_session` 的 `simulator.py` Factory 入口完成单 Go2 运行。输入为 `vertical_stair_01.json`，只有 `go2_1`，一楼起点 `[1.5,-0.35,0.4]`，二楼目标 `[1.5,-7.5,1.57]`，`0.55 m/s`，20 步接触校准和 900 步预算，`env_isaaclab` CPU，退出 0。

原始证据位于 `tmp/goal_feynman_factory_stairs_20260919_main/go2_upstairs.jsonl`，SHA-256 为 `7faad3e25dd3e68ec13b2ed9d2ab3e34d9aeb969dfd68c371619d7fcca289a91`；摘要位于同目录 `summary.json`。Go2 达到 `route_index=2`，足端命中 `/World/Indoor/SM_StairsSmall/SM_StairsSmall/Section0`，但在第820步以 `support_unknown/support_contact_gap_timeout` 终止，终态约 `[1.9785,-2.6383,0.2003]`，未到二楼。结果证明楼梯碰撞路径开放且有真实接触，不能单独归因 RL 或几何，也不能把失败改写成楼梯关闭。

随后运行的独立 `first_tread` probe 使用相同入口、单 Go2、20 步接触校准和 `0.55 m/s`。校准成功；第21步到达楼梯入口，第353步到达首级目标 `[1.5,-3.33]`，第644步以 `support_unknown/support_contact_gap_timeout` 终止。该结果把当前可复现的失败边界进一步收窄到首级之后的连续支撑/姿态保持阶段，但仍不足以宣称完整上楼成功。原始 JSONL 位于 `tmp/goal_feynman_factory_stairs_20260919_first_tread/go2_first_tread.jsonl`，SHA-256 为 `99fe188ccfc0ae541337cad6e5d0c6e8ece4b17f0a32f4cb831c796e22497b1e`。

Lite3 的 canonical USD 足端名称为 `FL_FOOT/FR_FOOT/HL_FOOT/HR_FOOT`，主会话已补充真实映射并通过 `79 passed` focused 测试。修复后 Lite3 的首级 probe 完成接触校准，第21步到达楼梯入口，第213步到达首级目标，第332步以 `support_unknown/support_contact_gap_timeout` 终止。B2 首轮 `no route` 经复核确定为二维楼梯排除区与常规 tracking reserve 对 `0.70 m` footprint 的重复膨胀；垂直 probe 接近段改用 `0.0 m` reserve 后，B2 接触 sampler 和校准均成功，但在880步预算内尚未到达楼梯入口，结果暂记为运动基线未完成。

Go2 的 `second_tread` 独立 probe 又到达第二级目标 `[1.5,-3.73]`，随后第753步以 `support_unknown/support_contact_gap_timeout` 停止；当前仍没有连续梯段、二楼停留或下行证据。

## Warehouse

`tmp/goal_physical_stage15_warehouse_20260919_v2/` 的 `cuda:0` 240步诊断正常退出。Iris、两台 M20、Carter，以及 12kg 木箱和 3kg 纸箱均完成原生对象/根状态读回；固定点和质量参数与部署一致。但对象 collision cooking、固定点执行、联合承载、动态占用、服务车路线、成员退出后的重新获取和 dock 业务回执都未验证。Iris `convexDecomposition` 和 Carter `Xform` shape 仍阻塞占用；空间报告仍为 diagnostic-only。

## Modern House

`tmp/goal_physical_stage15_house_20260919_v1/` 的 `cuda:0` 240步诊断正常退出，四个机器人和 14kg 扶手椅/2kg 书堆完成原生读回。阶段14的实际像素复核仍显示 M20_2 在门框外/门槛侧；本轮所有区域和 resident/furniture routes 仍 blocked，Iris/Carter cooked shape 仍未支持。主会话补充的 House focused contract/provider tests 为 `96 passed`；这些测试不等价于室内路径业务成功。

## City

`authoring_17` 已在 `tmp/goal_physical_stage15_city_20260919_v1/` 完成 `env_isaaclab/cuda:0` 240步三机器人原生读回，正常退出；城市路面、建筑、crossing、ground/aerial routes 和 occupancy 均未闭合，Carter Xform 与 Iris convexDecomposition 仍阻塞。City 静态服务审查确认 `guide_people`、`control_traffic`、`report_incident` 目前没有 public participant skill，trusted backend 的请求/回执/角色区间接口不能代替 episode witness。focused 结果为 `198 passed`（基础/原 city、service backend、episode adapter、intake/deployment、participant/communication 子集），compileall 通过。

## 反思与下一步

1. 代理服务 429 不能成为关键物理任务的隐性成功或失败；主会话在确认没有活动 Isaac 进程后接管并保留原始证据。
2. `physical_report.initial_objects` 是带 `objects` 列表的字典；错误地遍历顶层字典键会造成审查脚本异常，不能误报为物理失败。
3. 场景诊断可以证明实例和参数已应用，但不能证明可靠技能、业务路径、服务回执或因子效果；所有 release gate 继续 fail closed。
4. 下一阶段优先顺序是单一暂停 Isaac session 的 cooked/property shape 覆盖，然后按 Factory/House/Warehouse/City 逐场景串行补连续净空与业务 witness；RL、实验回放和评价模型由对应团队继续负责。

详细交接见阶段15工作区 `handoffs/`，其中包括 Warehouse、House、City 报告解析、City 服务审查和 Factory Feynman probe。
