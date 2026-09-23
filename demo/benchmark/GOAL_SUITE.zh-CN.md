# 任务生成、冻结与城市接入

`goal_suite` 是任务生成侧的统一入口。注册表包含五个任务生成场景；工厂、仓库、家居和城市均生成完整的作者审核题包，
每场景九种因子条件、七组控制/变体对；城市地图准备包是可选的独立补充材料。
Restaurant 当前仅有 authoring-only 题包，仍保持 `execution_status=external_pending`、
`scoreable=false`，因此不进入默认四场景条件矩阵。注册表不会修改模拟器场景选择、激活服务技能或启动实验。

## 重复执行审核

所有命令在仓库根目录使用 `env_isaaclab`。本流程不启动 ROS 或 Isaac：

```bash
conda run -n env_isaaclab env PYTHONPATH=. python -m demo.benchmark.goal_suite list

# 四个已审业务族：36 个题包、28 个控制/变体对。
conda run -n env_isaaclab env PYTHONPATH=. python -m demo.benchmark.goal_suite audit \
  --output-root tmp/goal_suite_new --verify-sources

# 城市团队更新源码或协议后，先重新导出准备包；目录必须尚不存在。
conda run -n env_isaaclab env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.city_benchmark.prepare --output-dir tmp/city_benchmark/prepare_new

# 在同一次审核中纳入城市候选与它的地图产物。
conda run -n env_isaaclab env PYTHONPATH=. python -m demo.benchmark.goal_suite audit \
  --city-package tmp/city_benchmark/prepare_new \
  --output-root tmp/goal_suite_with_city_new --verify-sources

conda run -n env_isaaclab env PYTHONPATH=. python -m demo.benchmark.goal_suite verify \
  --output-root tmp/goal_suite_with_city_new --verify-sources
```

`--scene` 可重复指定，默认选择四个已准入场景；Restaurant 仅用于独立作者契约编译，不能作为
`goal_suite` 条件矩阵输入。`--scene city` 可独立生成城市九条件题包，
不需要准备包。`--city-package` 只增加地图准备包审核，且要求选择 City。
输出已存在时拒绝覆盖，失败时清理本次创建的
暂存目录。输出包含：

```text
audit_report.json                         作者总报告
packages/<factor>/<level>/<scene>/        四层题包及 authoring/review.json
manifests/<factor>/<level>.json           每个条件的发布门槛
authoring/freeze_manifest.json            完整题包和出题源码冻结
candidates/city/                          城市准备包的已校验副本（可选）
authoring/city_intake.json                 城市接入结果（可选）
authoring/city_service_proposals.json      三种服务扩展提案（可选）
authoring/city_service_review.json         服务参数和能力缺口（可选）
```

具体资产/空间绑定缺口另由 [场景绑定清单](GOAL_SCENE_BINDING.zh-CN.md) 只读审核。
该报告保存实验声明、旧场景候选与局部 USD/map 证据，尚未成为题包物理准入证书。

总目录是作者工作产物，不能整体交给被测 agent。参与者只收到对应题包的 `release/` 和
运行时已投递事件。`evaluator/`、`authoring/`、冻结清单及城市 integration manifest 保持私有。
可选城市准备包的地图在该包自己的 `release/maps/` 中；它不是默认 goal 题包的必备文件，
也不通过私有源路径提供给参与者。算法接入见 [接入交接指南](ALGORITHM_HANDOFF.zh-CN.md)。

`verify` 重新读取文件、重算哈希、验证题包和业务必要条件、核对全部因子对及发布清单，
不会把保存的 `validated=true` 当作当前证据。额外文件、私有文件进入 release、路径越界、
符号链接、过期 API 和被改动的事件计划都会阻止通过。

当前总报告为 schema v2，显式记录 `city_preparation_included`。旧 v1 总报告应保留为历史
证据，使用新目录重新生成，不能仅改版本字段来升级。

## 冻结含义

`goal_freeze` 绑定四层题包、任务身份、布局及私有事件内容。划分必须由调用者显式指定；
相同任务身份、相同公开世界或相同声明空间布局的连通组必须留在同一划分。
更改说明文字、列表顺序、载荷或因子标签不能把同一布局拆成独立测试组。

统一 CLI 默认把现有公开模板放在 `reserved` 中，意为待后续审核的保留候选。当前每个场景
只有一个独立声明布局，不能形成每场景的 development/test 泛化比较。因此
`held_out_validated=false` 保持不变；跨场景挪一个模板或重命名任务不能代替未曝光数据。

`source_bindings` 的 revision 和文件 SHA 是声明。`verify_goal_freeze_files()` 单独读取
本地源文件，并报告缺失、改动或哈希不符；统一 CLI 的 `--verify-sources` 把任一失败视为
审核失败。默认冻结的源是出题/审核代码，数值布局来自公共实验定义，不是 USD 测量。
城市另外校验其 prepare 包列出的 JSON/USD 和组合层文件。这不认证远程 provider 的不可变
revision，也不证明 USD 依赖闭包完整或实际导航、遮挡和碰撞成立。

使用 `build_goal_freeze_manifest()` 可以提供后续正式场景版本与内容声明，但必须保留来源、
内容实读验证和物理绑定三个不同结论。新内容需要新的冻结文件，已有版本不能原地覆盖。

## 城市任务与服务

城市业务是事故确认、受影响人员引导、后续交通保护和救援通路/态势交付。准备包保留当前
公开地图与候选机器人，动态人员、车辆及危险区必须通过运行时观察进入模型可见状态。

goal 公开技能已注册 `guide_people`、`control_following_traffic` 和 `report_incident`，
包含参数、能力、前置条件、效果、证据及超时语义。`goal_service_catalog` 的
`control_traffic` 是服务提案/后端名称，与公开技能通过显式映射对应。注册不代表服务已在
正式 Isaac episode 完成验证；非法或过期动态引用仍拒绝准入。

人员引导必须逐人核对身份、确认与到达；交通服务只针对后续车辆，事故车是外生事件；报告
必须得到响应方回执。导航到附近、消息已发出、背景车自动制动、路人既有动画都不能替代这些
业务结果。公共 API 已支持城市业务计划提交，真实服务回执和正式执行证据仍须由可信宿主提供。

城市已有两份语义不同的声明级业务 witness，并与其他三场景一样生成通信五条件、团队连续性
四条件；审核器复核具体 treatment 和单因子隔离。声明见证通过不代表物理执行或因子实测通过。

作者判定也须保持解法开放。响应者只要满足当前能力、路线、资源和业务截止期，就不能仅因
其 ETA 不是最小而判错；局部最快选择可作为诊断信息。用于验收的分阶段时限及计时起点应在
题面公开，未来事故发生时刻和隐藏实体全集仍保持私有。报告回执不能代替救援通路净空；
人员和车辆成功必须绑定隐藏全集、逐一无重复核对，且事件顺序符合因果关系。

## 后续交接

| 所有者 | 需要提供的具体材料 | 任务生成侧的验收方式 |
| --- | --- | --- |
| 城市场景团队 | 固定版本的区域/路线/人员标识、受影响人员名单、危险区、集合容量和初始状态 | 重新 prepare、内容与地图一致性审核、动态引用契约 |
| 运行时团队 | 固定默认技能后端、引导/交通/报告服务、事件和能力读回、plan ledger | 使用统一 GoalSession；以可信回执核验已注册技能，缺后端保持 pending |
| 评价团队 | 场景业务真值和独立回执/净空谓词 | 审核 outcome、证据和失败归因一致性 |
| 因子实验团队 | 具体通信与成员事件、冻结范围、可影响决策的时间窗口 | 重算单因子输入隔离及执行证据审核 |
| 数据与发布负责人 | 每场景独立且未曝光的布局/身份/事件组、固定资产 revision | 显式 split、泄漏分组、内容冻结和发布门禁 |

这些外部材料未齐备时，作者审核可以继续迭代，`execution_validated`、`held_out_validated`
和 `scoreable` 均保持 false。现有 TODO 中对应外部任务继续保留，不计入本轮工程结项。

2026-09-18 首次交接发现的四项旧接口测试失败已由城市维护者修复：人员和交通服务引用已投递
观察，报告服务先登记观察并验证有效期。`authoring_13` 继续保持 10 个产物和原有包边界，主线
实读核验其 24 个本地源文件，并运行城市、foundation、接入、服务提案和统一入口联合回归，
225 项通过。独立反例促成了时间倒序、重复身份、隐藏全集缺失及通路证据缺失的拒绝；合法的
非最短 ETA 选择仍被允许，分阶段时限及计时起点已公开。新增 `task_logic.py` 仍是作者判定
骨架，未接入共享业务准入或正式评价；真实证据来源与完整任务可行性仍需后续绑定。
