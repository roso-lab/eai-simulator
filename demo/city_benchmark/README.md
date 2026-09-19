# City benchmark preparation

城市第四场景的**任务生成与审核基础接入层**。场景运行时（事故、车辆、路人、Flow 和单一
`open_simulator_session` 生命周期）位于本包 [`scene/`](scene/README.md) 子包——原独立目录
`demo/city/`，2026-09-19 合并至此；不重命名原演示语义，不更改三个已有场景
或共享 goal-driven 契约。题包场景身份是 `city`，而原演示选择器中的
`modern_house_nvd` 只是动态 builder 的配置支架，不是城市任务的场景身份。

> **当前状态（2026-09-19 · 阶段18）**
>
> - **可跑**：prepare 产物（地图/语义区域/题面草稿 + 源 hash 绑定）；`goal_city_intake` 接入审核（10 产物 + manifest 校验）；`guide_people`/`control_following_traffic`/`report_incident` 已登记为 participant-facing 公共技能（`registered_runtime_pending`）；v8 候选服务诊断——五项服务真实人员轨迹（~301 样本）、交通控制（~256 样本/类）、事故回执与连续通路读回（`tmp/city_benchmark/service_probe_stage18_20260919_v8/`）。
> - **被阻断**：正式 episode 绑定——人员/车辆 roster、危险区、集合容量、角色区间、动态实体须由城市维护方固定进正式 episode（当前 `formal_episode_binding=false`）；之后任务生成侧补两份独立业务见证与具体因子 treatment，再执行完整题包和单因子冻结门槛。
> - **发布口径**：preparation draft，`scoreable=false`，不纳入三个已审完整业务族或正式计分集。

当前交付：真实 USD 静态地图、语义区域、候选集合点、三机器人可选池和选队配置适配、
目标驱动公开题面、共享 participant API、人员响应/跑步动作适配、后续交通控制边界和作者私有接入审核。**这是 preparation draft，
不能计分，也不是已有 `validate_goal_bundle` 接受的完整正式题包。**

## 任务设计

业务目标是“应对道路突发事故，保护受影响人员和后续交通，保持救援通路”。
公开题面只有四个结果：事故态势被确认、受影响人员去向可核对、后续交通得到保护、
救援通路与态势交接成立。任务分解、机器人选择、并行顺序、重规划和路线由参与者决定。
不公开事故时间表、目标建筑、参考执行 DAG、eligibility 列表或成员退出影响的任务答案。

Go2 / Carter 是两种可选地面平台，Iris 是当前唯一空中观察平台；三者都通过公开的
`event_bus` 提供团队消息能力，Iris 额外提供规范化的 `camera` 观察接口。它们没有被指定为
“必选疏散机器人”或“必选拦截机器人”。原演示的控制接口声明可以复用，但不能因此
宣称爬楼、机械臂操作、灭火、人群引导或车辆控制已经实现。只有一个空中观察者的事实
会进入作者审核，不能把简单换名的计划当成多个有效解。

事故演示中，车辆运动和撞击是脚本采样后写入位姿；Flow 是可视效果；背景车接近事故车
会自动暂停；4 名路人有意被放在事故路线与建筑火点以外，碰撞关闭且只循环行走。
因此这些自动行为都不是参与者的成功证据，演示事件日志也不是 PhysX 撞击测量。

交通保护在首个任务族中只针对**事故发生后的后续交通**：需要服务确认和后续车辆实际运动
证据，不能把机器人机体挡住 18 m/s 事故车，也不把“阻止事故”作为本场景任务条件。事故车
继续作为外生脚本事件；若未来增加事故预防题，应另建可响应车辆模型和独立任务族。

## 业务成功条件

城市题的成功判定是一条有时序的业务证据链，不能用“机器人到达某个点”替代：事故观察必须
来自当前可见的观察引用，并在确认时限内保持新鲜；派遣的地面响应者必须是当前在线、能力和
路线均被运行时接纳的成员，并在公开观察送达后按当前候选的可行 ETA 在响应时限内到达；人员
必须逐人被观察、确认接受引导、沿被接纳的路线到达有容量且当前安全的集合区，且没有穿越动态
危险边界；后续车辆必须是事故后的跟随车辆，控制服务必须确认，经过独立反应窗口后还要有实测
停车或改道轨迹；事件报告必须引用已送达且未过期的观察，并获得响应服务回执。交接时还必须
有覆盖回执时刻的完整救援进入和人员撤离通路净空检查，不能用回执替代通路证据。

因此 Carter、Go2 或未来接入并公开声明同类能力的 Coco 都通过同一个能力、在线状态、路线和
ETA 信息供分配使用；能力、路线和在线状态合法且满足截止期的响应者均可接受，最小 ETA
仅是诊断，不是业务成功条件。题面不发布 `eligible_agent_ids`，也不把机器人类型写成唯一答案。Iris 当前的
公开价值是空中导航、观察和中继；它是否被选中取决于当前信息和团队决策。若只有 Iris 观察而
没有地面响应者，观察结果不能自动满足疏散或交通保护结果；若地面机器人到达但没有人员/车辆
业务回执，也不能算成功。

上述条件的纯逻辑骨架在 `task_logic.py`，只用于作者定义证据和失败原因，尚未连接真实
participant runtime 或 hidden evaluator。通信延迟/丢包必须能够影响观察到达、派遣或服务请求
的截止期；成员退出、加入或能力变化必须使正在执行的计划失效并触发新的可行分配，不能只在
日志中增加一条事件而不改变任务结果。

2.5 s 自燃、seed 7 下约 6.8 s 撞击和约 8.3 s 建筑着火属于事故演示序列；后两者
从所选路线导出，不是全场景常量。20 s 视觉循环不是救援截止期。题面暂定 180 s
响应预算，正式实例须做可达性与时间预算审核，计分执行禁止自动 replay/loop 重置。

`public_task.business_constraints` 与 `authoring/private.task_logic.deadline_policy` 使用同一
生成源公开草稿时限：观察送达→确认 15 s；派遣→到达 35 s；交通请求提交→确认 10 s；
交通确认后轨迹观察至少 5 s；人员引导请求提交→逐人确认 20 s；确认→集合到达 120 s；
报告提交→回执 15 s。时间均为本轮 episode_started 起的仿真秒，相对窗口不延长总预算。
这些规则已写入公开草稿，几何与物理可行性仍待准入审核。

`evaluate_city_acceptance` 的输入须由作者或可信运行时提供；两份完整隐藏名单缺失、重复实体、
时间倒序或缺少通路证据均不能成功。参与者自报的标志不得直接传入；此骨架本身不提供取证、
来源认证或评分。
即便合成正例的 `mission_success=true`，仍有 `predicate_only=true`、`execution_validated=false`、
`scoreable=false`，不能据此发布计分任务。

## 生成地图和题面

使用 `env_isaaclab`；只加载本地 JSON / OpenUSD，不启动 Kit、PhysX、ROS、GPU 或下载资产。
需要环境中的 OpenUSD、NumPy、Shapely 2、Pillow 和 PyYAML。输出目录必须尚不存在。

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m demo.city_benchmark.prepare \
  --output-dir tmp/city_benchmark/prepared \
  --resolution 0.25 --inflation 0.45 --response-budget 180 --max-active-agents 3
```

默认资源为 `usd/scene/city/{city_static.usd,city_layout.json,city_scenario.json}`，可用
`--asset-root` 指向另一套完整的本地 city bundle。这里没有注册 provider，也不会通过
`simulator.py assets ensure --scene city` 下载。不要把运行生成目录或 Flow 预设提交源码。

| 输出 | 使用方与边界 |
| --- | --- |
| `release/public_task.json` | 共享 goal-driven schema 的公开业务题面；无固定 DAG、分配或未来事故信息 |
| `release/participant_api.json` | 复用主轨的 select_team / submit_plan / revise_plan 等协议；执行仍未接入 |
| `release/maps/occupancy.png`、`occupancy.yaml` | ROS 常见地图格式的保守静态候选图；不要求运行 ROS |
| `release/maps/map_metadata.json` | 米/Z-up、原点、分辨率、膨胀、未知区域和动态层边界 |
| `release/maps/semantic_map.json` | 道路、人行道、建筑外围、过街点、候选集合点、3D 障碍包围盒、静态交通/步行路线 |
| `release/maps/map_preview.png` | 便于人工复核的地图预览；不是可替代 occupancy 的导航输入 |
| `authoring/private.json` | 原始资产/组合层哈希、prim 绑定、事故模板、服务扩展建议与私有因子设计 |
| `authoring/review.json` | 只读准入检查、候选数量和明确阻塞项；不声称完整任务可行 |
| `authoring/navigation_config.json` | 原演示起点审计及附近地图净空候选起点；均未获物理部署认证 |
| `integration_manifest.json` | 接入入口、全部产物哈希、发布状态；是管理材料，不作为模型输入 |

**地图坐标必须保留：**原始 USD 的 defaultPrim 是 `/World`，建筑在其直接子级。
演示把它引用到 `/World/City`；因此道路位于组合后的 `/World/City/City/...`，
建筑位于 `/World/City/Building_...`。导出器在内存中按相同方式组合后读取，直接读取
原始文件的 `/World/City` 会漏掉所有建筑。建筑 Z 向贴地修正与演示一致，源 USD 不被写入。

地图白色（254）只来自扣除障碍和净空的人行道/道路；黑色（0）表示占用；灰色（205）
表示未知，不能当作自由空间。图像第 0 行在北侧，Y 向上；YAML 原点是西南角，yaw=0。
树冠、建筑及灯杆包围盒采用保守投影，会拒绝一些实际可能可行的位置；这不是碰撞真值。
道路/人行道台阶边界保持阻塞，轮式或足式跨越能力必须另行验证。

车辆、人员、烟火、机器人当前占地没有写入静态图，运行时必须叠加。
无人机要使用三维障碍、机体包络和实时传感器；地面二维图不能证明飞行路线安全。
集合点仅做静态净空筛选：容量、火场距离、路径和实际人员到达仍未审核。

`navigation_config.json` 会指出原演示 Carter/Iris 起点与保守树木包围盒的冲突；
这不等价于实际碰撞。附近候选点提供配置准备，不会自动移动原演示机器人。
审核后的坐标可显式交给 `build_selected_selection(..., spawn_positions={...})`。

## 主对话如何接入

```python
from demo.city_benchmark.contracts import build_public_task
from demo.city_benchmark.config import integration_manifest
from demo.city_benchmark.selection import build_selected_selection
from demo.benchmark.goal_driven_contract import build_goal_participant_api

# semantic_map 是本目录 exporter 生成的静态语义地图。
task = build_public_task(semantic_map)
api = build_goal_participant_api(task)
selection = build_selected_selection(["go2_1", "iris_1"], seed=7, max_active_agents=2)
```

调用者必须使用 `demo.city_benchmark.scene.main.configure_city_env_cfg` 和现有 session API 组合环境。
此 selection helper 只生成配置；不会激活机器人、代替加入事件、建立导航或实施参与者动作。
共享 `goal_scene_registry.py` 已注册 city 为 `preparation_draft_not_goal_bundle`，并由
`goal_city_intake.py` 校验 prepare v1 的 10 个 artifact 加 manifest。完整题包生成器
`goal_driven_tasks.py` 尚未准入 city；注册准备稿不激活共享技能或模拟器场景。

共享 hidden-evaluator validator 要求至少两份完整见证。目前 observe/navigation 不能产生
人员到达、交通管制确认或救援接收回执，所以没有为了通过校验而生成伪造 witness。
`authoring/private.json` 使用独立 draft schema，不会冒充 `hidden_evaluator.json`。

必须由后续开发者接入运行时并完成物理验收的服务接口见 `contracts.required_services()`：

- `guide_people`：可观察人员身份、逐人引导确认、真实路线/到达、撤销和无响应处理。
- `control_traffic`：只针对后续车辆的停车/改道/放行、服务确认和车辆轨迹；不控制事故车。
- `report_incident`：仅引用当前证据，取得响应服务的接收回执。

`response.py` 已提供这些服务的纯状态机和 USD 适配边界。人员服务只有在
`PeopleResponseRuntime.observe()` 返回的观察引用被提交后，`PeopleResponseRuntime.guide()` 才能
进入疏散，`HumanEvacuationBinding.start()` 会对已
绑定的人类资产调用 `UsdHumanStageRuntime.play_action(..., "jog")` 并提高路径速度，到达后
 `finish()` 恢复 `walk`。本地 manifest 和 retarget cache 已确认当前四类城市行人支持 `jog`。
`spawn_response_people()` 可在后续城市 session 中显式生成受影响人员；它与原演示的背景行人
分组隔离，不会把背景循环动画误当成疏散证据。
`traffic.py` 将请求、`acknowledge()`、反应窗口和轨迹采样分开，防止提交控制请求或服务确认
被误记为车辆已经受控。`incident.py` 只接受已登记且在有效期内的观察引用。

`service_backend.py` 提供新的城市服务交接边界 `CityServiceBackend`；
`CityServiceRequestAdapter` 保留三个业务方法名称，由部署层绑定当前现场执行者和区域；
`CityRuntimeReadbackAdapter` 接入**episode 所有者**提供的时钟、观察登记表、最近已送达
捕获、现场角色与区域驻留、路线/容量/权限准入、服务和世界状态读回。参与者仅提交可见的实体
引用与服务请求，不得构造适配器的回调或提交回执。网关拒绝未来、未送达、过期、被新捕获
取代的观察，重复身份/请求、事故车管制、无现场角色和未准入的行动。它逐请求核对确认、
逐人到达、逐车反应窗口和轨迹、精确接收方回执及覆盖回执时刻的连续通路证据；公开截止期
适用且总预算不能被延长。`reference_complete` 仅表示参考模型闭合，`evidence_complete`
表示外部运行时**声称**提供了读回；两者均为 `scoreable=false`，后者仍需审核读回来源、
完整隐藏人车名单及实体物理轨迹，不能当作最终业务成功。`eai.skills` 已登记
`guide_people`、`control_following_traffic` 和 `report_incident` 作为 participant-facing
公共调用契约。阶段18已有独立原生服务诊断，正式题包三项覆盖状态仍是
`registered_runtime_pending`。`CityResponseServices` 等旧入口只用于参考状态机和运行时接线，
不能因为技能已注册就把服务确认或请求 ack 视为业务成功。

现场角色按服务分阶段：`guide_people` 的原执行者须连续驻留至每名人员确认接受引导；
`control_traffic` 的原执行者须连续驻留至每辆后续车辆的管制服务确认。之后人员沿路线移动、
车辆在已确认的管制策略下反应，可由独立服务持续推进，不要求原机器人留到到达或轨迹检查。
`report_incident` 在提交时要求有非零时长的现场角色区间；后续回执由接收服务产生，完整
通路检查由独立世界读回提供，不要求报告者一直留到回执。历史角色区间必须由 episode 所有者
在 `role_interval(agent_id, role, region_id, as_of_s)` 返回连续的起止时间；失去角色后不能让
旧请求使用其他成员的确认。当前没有 transfer 协议，替补必须在当前有效观察与自身角色证据下
另行提交请求。`continuous_access` 指救援和撤离路线持续净空，不能误解为机器人持续驻留。

`episode_adapter.py` 的 `CityEpisodeEvidenceHost` 是**宿主专用的离线接线缓存**，不是新的
participant API。宿主传入当前区域/路线/容量/服务权限审核回调 `admission`，并在每次真实
仿真推进后按时钟调用 `advance(time_s)`。采集器仅在消息实际送达后调用
`deliver_observation(row)`；用测得的时间段调用 `record_role_occupancy(...)`，连续时间段
合并，有间隙的重新到岗不能补齐旧请求。`as_source()` 返回已实现
`CityRuntimeReadbackAdapter` 的只读回调集，用于构造 `CityServiceBackend`。

阶段17增加的 `live_witness.py` 是 episode-owned service witness 的运行时入口。
`assess_city_runtime()` / `bind_city_runtime()` 只接受 runtime 发布的
`city_service_witness` host；host 必须带稳定的 `episode_id` 并实现 observation、角色、服务
回执、人员/车辆世界读回和连续通路读回方法。`LiveEvidence` 还要求每条证据绑定 episode、
source、实体、时间和明确的状态变化，拒绝 `reference`、`backend`、`participant` 或
`ack` 作为来源。缺少 host 时，当前 `demo.city_benchmark.scene.runtime` 只暴露脚本事故车、背景交通和
循环背景行人，入口具体报告 `service_witness_host_missing`、
`people_source_is_background_only`、`vehicle_source_is_scene_pose_only` 和
`incident_receipt_endpoint_missing`；没有服务请求、ACK 或 readback 可以被升级为真实成功。
即使将来 host 完整，live witness 结果仍保持 `scoreable=false`，直到独立审核确认物理来源。

在 `CityServiceBackend` 接纳请求后，宿主以原请求调用
`register_request(request, evidence_class="runtime"|"reference")`。随后**仅由服务端/世界
采集器**调用 `record_actor_ack(...)`、`record_person_readback(...)`、
`record_traffic_readback(...)`、`record_receipt(...)`、`record_access_readback(...)`；
每条确认、轨迹、安全或接收证据都须提供外部引用，观察需提供来源、捕获/送达/失效时刻。
缓存不采集物理状态，不推断准入，不生成正例旗标，不把 request/ack 自动提升为到达或
世界成功。外部引用是溯源键，不是物理真实性认证；当前仍需主线在 Isaac/session 层建立
真实数据源，禁止把宿主写接口暴露给参与者。离线 host 可以先做协议联测，但不能提升任务
准入或计分状态。

阶段18的 `CityEpisodeServiceRuntime` 已接入真实 actor 路径和车辆控制。
`python -m demo.city_benchmark.service_probe --prepare --output-root <fresh-dir> --device cpu`
准备冻结输入，再以相同参数加 `--execute`（替换 `--prepare`）执行独立串行诊断。
Isaac 使用 `env_isaaclab`，不需要 ROS。查看 `service_probe_report.json` 的状态；
Kit 清理时可能吞掉异常，单独的进程 exit code 不能证明成功。

人员出生路径的高度来自变换后的可碰撞人行道三角形，每0.25m检查横向1m范围的支撑。
这属于出生配置绑定，不在运行中搬移人物；运行期仍需 PhysX 路线和集合区检查。
集合区以完整人物包络加余量定义，原生读回同时检查容量、全身包含与途中逐步净空。
车辆改道查询覆盖当前真实包络和整个实际转角；停止、放行、改道都保留每控制步轨迹，
危险区及访问区必须在整个请求窗口内满足条件，不能用安全终点覆盖中途失败。

`service_probe_stage18_20260919_v8` 完成5个阶段：人员引导301个轨迹样本，
车辆停止／放行／改道各256个轨迹与安全样本，事故回执后26个连续访问区样本。
这是候选服务诊断：区域在运行时选择、角色区为诊断范围、危险区未完成正式动态绑定；
两份独立业务见证和具体因子 treatment 仍待正式参数冻结。因此继续保持 `scoreable=false`。

这些是规范建议，不在本阶段偷偷注册为新的城市专用技能；当前题面读取共享技能本体的版本，不能把 relay 等价为
行人听从指令。公共动态对象引用也要扩展：现有主轨验证只认识初始 public_world 的对象，
还不能完整接收观察后才出现的人员/车辆身份。服务执行、评价实现和实验回放由各自所有者完成。

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -p no:cacheprovider -q \
  demo/city_benchmark/tests/test_foundation.py demo/city_benchmark/scene/tests \
  demo/city_benchmark/tests/test_service_backend.py \
  demo/city_benchmark/tests/test_episode_adapter.py \
  source/EAI/test/test_benchmark_goal_city_intake.py
```

环境的 ROS pytest 自动插件需要不同 Python 依赖，纯测试关闭其自动加载。
测试覆盖地图坐标/未知值、障碍净空、候选集合点、源 USD 保持不变、选队预算与身份、
公共/私有隔离和缺执行服务时禁止计分。真实资产导出另行执行上述 prepare 命令。
上述 pytest 不启动 Isaac；服务诊断单独执行，不以既有 demo 报告代替本轮 benchmark 执行验收。

2026-09-18 的验证记录见 `VALIDATION.zh-CN.md`；共享模块由主对话并行修改，
其构造器回归结果应与城市局部测试分开理解。
