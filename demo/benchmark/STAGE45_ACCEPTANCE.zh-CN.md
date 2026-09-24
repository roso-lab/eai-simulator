# Stage45有限验收：到达读取耗时与完整诊断归档

2026-09-25；实现 `33e23853f54e6a77f948d16e3db84963e74f8507` 已正常推送
GitLab benchmark-task并ls-remote核对。[最小接口](STAGE45_CITY_ARRIVAL_READBACK.md)
只移除到达轮询中无用的轨迹复制，保留路径完成短路、整身进入与最终完整安全证据。

## 问题、修复与回归

Stage44 full-v1在600s宿主上限后没有终态。后续full-v2保持相同公开策略，增加
刷盘动作日志与90s周期栈：两次主线程都落在到达轮询的`_trajectory→deepcopy`。
到达轮询只读取`arrived`，却重复复制不断增长的轨迹；尤其路径结束但整身未进入时
反复发生。取得定位证据后主动停止full-v2，不能将它写成1200s超时或业务失败。

新增轻量`arrival_readback`与完整`person_readback`共享同一当前状态读取和到达公式。
前者不读取历史轨迹；后者仍完整检验轨迹、支撑、容量及真实3D相交。主审拦下了初稿
删除未完成路径短路的性能回退；最终保留该短路，新增raising probe证明未完成时
不读取candidate/support/world。采样频率、时钟、成功谓词、v1默认行为均未改变。

同35个tracked测试文件，`tmp/stage45-validation/test-files.txt`：本机
**732 passed /40.87s**，ljy **732 passed /15.76s**；独审聚焦71项通过，语法/diff检查通过。
测试命令沿用Stage44，在ljy显式设置：

```bash
EAI_USD_ROOT=/home/ljy/eai-simulator/usd \
EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
<env_isaaclab>/bin/python -m pytest -q $(cat tmp/stage45-validation/test-files.txt)
```

首轮ljy漏配资产根路径，731通过、1项找不到bow.json；保留`ljy-missing-asset-root.log`。
补齐既有配置后原代码、原35文件全量通过，未跳过测试或修改资产。三份用户文件hash不变。

## 原生完整诊断

同一公开输入确定性策略运行`stage45-city-footprint-full-v1`，native episode为
`2ac51483-ad71-4306-93b3-be44b618d076`，源码33e2385。仅从投递观测/API形成6任务，
未向参与者提供隐藏绑定。使用原600s宿主上限和180s模拟业务预算，exit 0、error=null，
6条技能回执均succeeded。它仍是宿主诊断策略，不是正式算法成绩或见证资格认证。

同机观察到第四次引导的宿主间隔由147.91s变为13.01s，全部动作结果在62.13s返回，
96.22s进入close。该比较用于定位优化效果，没有独立重复/CI，不是正式性能实验。
90s栈仍可见最终完整证据读取；这正是保留的终局验真，不能为了速度删掉。

| 业务/硬条件 | 结果 |
| --- | --- |
| incident_assessed | validated |
| secondary_traffic_protected | validated；完整6辆跟随车的确认与轨迹 |
| response_access_preserved | validated；报告回执与持续通路覆盖 |
| exposed_people_accounted_for | failed；人员00、01、03到达，02未通过整身进入检查 |
| response_dispatch（独立硬条件） | 通过；不与四项public业务数混计 |

人员02选择candidate003，准入时admitted=true；在最后两份公开观测对应的native读回中，
该人员占用圆盘但不在contained列表。圆盘中心[60,44]、半径0.75m，当前world AABB
最远角点距中心约0.86353m。这证明准入与终点包络存在差异；当前档案尚不能区分
身体真实超界与朝向/轴对齐包络的保守扩张，不能先归因模型或直接扩大区域。

完整evaluator同时输出该人员`hazard_boundary_violated`、`assembly_arrival_missing`和
`arrival_timing_missing`。原envelope没有该人员的`entered_hazard`字段，因此第一个
fail-closed代码不能被转述成“已经实测进入危险区”；需要在后续归因/证据接线中消歧。
评分进展的terminal人员谓词仍为unknown，而完整业务判failed，二者口径分别保留。

## 独立复算与下一出口

原档位于`tmp/stage45-validation/stage45-city-footprint-full-v1/`。29项归档字节/hash、
47次会话调用、进展和通信replay通过；10个评分源边界独立重算一致，7份原生/公开
观测的profile、几何、身份、隐私和支撑连接检查通过。完整业务从磁盘重新调用原
evaluator，与3/4结果逐项相符。分别见同目录外的`native-full-replay.json`、
`native-full-progress-replay.json`、`native-full-v2-joins.json`和`native-full-business-replay.json`。

初始16组合仍6 admissible、10 blocked、0 unavailable；oracle failed/null，
platform_valid=false。唯一公开matching不是全任务可行性证明，技能确认成功不是人员
到达证明。replay验证内部一致性，不自动认证物理来源或正式统计。

下一最小切片只核对人员02的终点身体测量及准入一致性；若是测量伪限制则修读取，
若是真实超界则保留执行结果。不得调大圆盘、修改见证、强制参考分配或扩建通用几何。
完整业务、外部参与者策略、初始二元oracle与可信评分时序继续按R1/R2验收。
R1–R5及整体Goal未完成；405条件/315对数量不变。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`保持。
