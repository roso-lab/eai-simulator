# 阶段41有限验收：公开选择与原生执行一致

证据日期：2026-09-25（Asia/Shanghai）。代码 `05c1bd5`，跨工作树测试修正
`f2d7429`；两者均正常推送GitLab `benchmark-task`，已用 `git ls-remote`核对。
实现说明见 [请求目标接口](STAGE41_CITY_REQUESTED_TARGET.zh-CN.md)。

## 已接受的范围

- `requested_target_v1`显式启用，旧接口保留。公开测量、准入、控制器和到达读回使用
  同一个请求目标；不读取作者目的地/路线来替代模型选择，不移动出生位置。
- 公开候选和person×candidate测量经过共享上行计量；工具引用可选，盲选仍合法。
  引用过期/状态变化产生事实失败；缺证、同状态矛盾和旧AABB资格缺口归平台问题。
- 批容量按占位、预约和请求人员的身份并集计算。非法公开目标/未观测人员为请求失败，
  公开实体与原生登记不一致为平台失败。没有证据的“不通”不编造静态障碍。
- 原始未知时间戳保留null，业务评价派生输入按缺字段进入既有missing-timing分支。
  冻结题包、见证、reach、资源及资产hash未修改。

## 验证

同一29文件清单位于 `tmp/stage41-validation/test-files.txt`。实际命令为：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
<env_isaaclab>/bin/python -m pytest -q $(cat tmp/stage41-validation/test-files.txt)
```

本机595 passed（34.48s）；ljy隔离树595 passed（13.15s）。独立审查49项焦点测试通过，
AST及diff检查通过，无代码合并阻塞。ljy首次593/2失败原始日志保留：缺少USD资产根配置，
以及测试写死主机路径。前者用已有 `EAI_USD_ROOT=/home/ljy/eai-simulator/usd`修复，
后者由 `f2d7429`改为实际checkout ROOT。没有跳过或削弱断言。

ljy工作树为 `/home/ljy/eai-simulator-worktrees/benchmark-stage39-autonomous-measurement`，
以bundle同步并ff-only更新；原远端工作区未修改。原生诊断复用既有CPU PhysX配置和CUDA渲染，
单次设600s超时，并设置既有 `EAI_CONTROLLER_ROOT`。没有通过改变设备来修测试。

## 两轮原生局部诊断

| episode | 公开选择策略 | 机器人 / 人员 / 集合区 | 技能回执 | 到达读回 |
| --- | --- | --- | --- | --- |
| stage41-city-requested-target-v1 | 已测admissible路线中最短 | go2_1 / affected_person_00 / assembly_candidate_004 | 1 succeeded | arrived=true，safe=true，原生17.98s / episode相对7.96s |
| stage41-city-requested-target-v2 | 已测admissible路线中第二短 | carter_1 / affected_person_02 / assembly_candidate_004 | 1 succeeded | arrived=true，safe=true，原生21.86s / episode相对11.84s |

第二轮人员02作者绑定为003，但策略仅从已收到的公开测量排序得到004，并由native接纳、
执行和读回。它提供不强迫复刻作者目标的正向证据。两轮均为有意只提交一个技能的确定性
局部诊断，不是真实模型baseline，不是完整自主解题，也不代表所有备选路线均可行。

两轮 `result.error=null`，源文件前后hash一致；各28项归档字节/hash核对通过，session audit、
progress和communication均重新从磁盘独立replay通过。第一轮上行9364+9674字节、下行925；
第二轮上行9364+9670字节、下行931。这里使用显式无带宽瓶颈诊断配置，不是通信退化曲线。

本机证据目录：`tmp/stage41-validation/`，分别包含两轮完整归档、原生日志、
`native-disk-replay.json`、`native-v2-disk-replay.json`及双机测试日志。上述replay证明内部
一致性；物理结论来自本次native测量和到达读回，不能由hash或replay单独推出。

## 未完成与下一门禁

两轮业务均仅 `incident_assessed` validated；全人员安置、后续交通保护、持续通道/报告
未验证。引导review还可呈现后续窗口timed_out，不能把成功技能回执等同完整业务成功。
注册集合区高度历史上依赖作者人员身体包络；涉及 `target_body_outside_region` 的选择为
平台资格缺口，不能用来处罚模型或证明没有合法替代方案。

只读41B盘点进一步确认：candidate001 maxZ为2.1269849988m，而人员03目标身体上界
2.1966037941m，差约6.96cm，并非浮点噪声；该上界来自私有绑定人员01身体高度加0.1m。
当前单提案测量图也不提供四人员的容量一一匹配，但它不穷尽替代路线，不能作负证。

下一P0仍是完整任务资格和可信初始oracle的证据盘点。41B不得从单提案失败推出
not_deployable，也不得把synthetic证据重命名为原生认证。全任务当前oracle仍failed/null，
不进入部署指标二元分母；这不是模型第三种答案。
下一批先冻结精确任务身份、单初始测量点、技能保证和硬终局谓词，检查已有读数能支持
什么有限判断；不将求解任意未来PhysX状态的通用规划器设为前置需求。完整合法构造性
成功证据可支持特定正例；一次运行失败或某条路线受阻不能支持负例。

R1–R5总项、405条件原生覆盖、正式评分与发布认证仍未完成。全程
`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
三份用户保护文件SHA保持不变，未纳入提交。
