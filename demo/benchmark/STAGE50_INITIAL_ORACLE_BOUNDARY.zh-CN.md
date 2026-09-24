# 阶段50：City初始资格有界停止，餐厅全队执行与通信基座

输入 `cce6cdd`，分支 `codex/benchmark-stage50-initial-oracle`。
本阶段先核查初始可行性证明的实际支持范围；不修改冻结任务、几何、见证或发布标志。

## City资格结论

当前证据不能在参与者初始化前认证完整任务 `deployable`，也不能认证
`not_deployable`。保留内部 `oracle_status=failed`、`feasibility_label=null`，
不把证据不足变成参与者第三个答案或模型错误。

正标签只需要一个完整合法完成方案，不要求当前episode未来业务已经成功；但方案中
每项必要技能转换必须有可依赖的前提/后置保证。已有16个pair提案、6个admissible和
matching=4只证明初始提案图，不能代替整题技能和终局保证。

在现有每区capacity1的已测提案图上，00只能到004、01只能到002，故完整matching
迫使02→003、03→001。Stage46运行在02→003处出现path_finished/inside为真、
full_body_inside为假的原生读回。Stage46可见网格与world AABB分歧不等于获得新的
可信到达契约；Stage47已停止不适用的USD定向包络扩展。因此本批不重开几何、不再
重跑相同GPU诊断，也不把失败业务回执反过来认作“当前团队不可行”。未测其他合法
路线仍可能存在，负标签所需的完整替代方案排除并未具备。

旧 `goal_reader_city.CITY_MODEL` 也不能直接接上：它对应2人/1车开发抽象、不同能力
名称和dispatch_budget6，任务digest为`10b1b9b40f5b3f1b7ca74be879cacff14c65085561eac5c7866de352237aecfb`；
当前自主任务 `CITY-GOAL-NATIVE-01` 为4人/6车/3机器人，digest为
`143a7ec986e256eaa0ec96c5853164ed558da5b57d52c7ab9f41234561c2be2f`。
仅更换scope、native前缀或task hash不会补足模型有效性。

## 下一可执行工作

独立审查完成，选择餐厅原始8候选池作为下一完整R1目标。餐厅已有全部8个锚点的
路线级验证、共享物理时钟与独立业务校验器；但当前入口仍缩到3台、固定mission展开。
Factory额外缺少真实初始区域、原生时钟、可信observe与业务evaluator；其时钟候选后置。
House/Warehouse不重开搬运几何调参。可先补参与者自主接口和可信raw结果生产；oracle
尚缺证的诊断须明确排除正式部署统计，不能以“所有oracle先完成”为开发死锁。
不静默缩减题包或候选池，不恢复作者固定任务分配，不以更多评分格式修复替代执行接线。

## 实现检查点

工程槽负责新增`goal_restaurant_fleet.py`及专项测试：8台一一对应的实际运行身份、
维护锚点/控制器声明、共享物理epoch、每tick全队命令及可取消增量导航。不以注册
handler等同完成全参数域原生验证，不更改旧三机诊断。

主代理负责新增`goal_restaurant_transport.py`及专项测试：复用调用方同一
`CommunicationRuntime`，readiness v2 publication按规范序列化进入共享回传池，
实际送达且具有后续原生tick才消费并产生回执；丢包/过期保留失败，允许新派发重试。
输入sample须绑定producer/native articulation与物理epoch；输出沿用独立business v2
证据格式。联调发现legacy链路显式direction未写入published事件导致无法replay，
本批仅补该元数据；未传direction的旧接口行为不变。

原生自主入口、观察覆盖、并发事件泵/取消与完整归档回放尚未接齐，静态准入保持
`platform_blocked`。M20导航控制器仍为本地provider未发布依赖。组件测试不能晋级R1、
oracle或发布标志。独立审查同时跟踪代码正确性、任务进度和下一原生出口。

## 本批验收与下一接线

- 工程专项23项通过；主代理通信/业务v2/通信池联合147项通过。
- 独立审查Restaurant/communication组合349项通过；另6个评分/成本/源绑定/因子矩阵/
  runner/通信capacity-v2回归134项通过（6.41s），执行期间所有benchmark Python源码
  和该组测试hash未变。上述有重叠的检查不相加为独立样本数。
- 本机native2有界检查进程返回0（27.39s）：8台实际控制器共场景，240个control tick，
  960个physics substep，原生4.8s；5个采样tick均有全8命令/状态，始末全8读回，
  源码hash未变。只证明共存与idle pump时钟；不证明路线、接触、静止稳定性或业务。
- native1排除：误用了只通过产品hash但未闭合源USD路径的目录，且Kit退出前没有
  保存终态report。已复用A2准备检查，native2使用
  `/home/pupil/a/b/c/d/products-candidate-v7`，源闭包和冻结hash均通过；未改overlay。
  不以进程exit0代替档案验收，终态证据须在Kit context退出前持久化。
- ljy仍`No route to host`；没有本批远端验证。三个受保护用户文件不改、不提交。

下一检查点按顺序推进：

1. 从全8 settled native sample建立真实初始状态和逐机器人observe producer；缺metric
   geometry的standby区域仍是明确平台缺口，不能猜点或删去公开候选/合法参数。
2. 版本化public-only参与者入口接GoalSession和同一fleet/transport事件泵；保留模型
   自主拆题/选人/排时序。取消/截止/在途外生变化必须发生在实际共享tick上。
3. 先用两种合法计划验收接口自由度，再有界native业务执行和独立档案回放；隐藏
   initial/remaining oracle单独验真。无原生证明时保留failed/null，不伪装正式成绩。

该执行基座中`arrived`仅指实测root达到profile容差，不能被后续adapter直接当成已验证
的业务回执。进程内route seal保护plan→begin未被修改；离线验收仍须独立复算来源，
不能把seal当物理证明或跨进程认证。

## 证据

只读输入为Stage46原生档，未改写；任务对比及输入hash存于
`tmp/stage50-validation/reader-task-comparison.json`、`city-source-hashes.json`。
团队交接工作区：`stage50-city-initial-oracle-20260925`，包含独立场景比较、City技能
保证审查和既有solver边界。代码交接为`restaurant-fleet.md`、`transport-checkpoint.md`，
独审为`restaurant-implementation-review.md`。原始native检查和磁盘核对位于
`tmp/stage50-validation/native-fleet-2`，评分回归记录为`scoring-regression.json/.log`。
本阶段未新增原生导航/业务成功或二元oracle认证。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
405条件/315对、R1–R5目标均保留，不把本次有界停止写作完整发布完成。
