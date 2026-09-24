# 阶段39验收与下一批出口

本批代码已分两次推送 GitLab `benchmark-task`：`cb971ed`（可信进展原始事实、回执对账）和
`1504a99`（City 外部自主适配器入口、相对时钟、身份及源码一致性）。
这是 R1/R2 的组件与原生局部诊断进展；R1/R2 总任务仍为部分完成。

## 已核验结果

| 验证 | 结果 | 解释边界 |
| --- | --- | --- |
| R2 本机 / ljy 同组回归 | 各 173 passed | `cb971ed` 的契约、采样、回执对账和 replay |
| City 集成后本机 12 文件 | 222 passed，18.25s | 含新入口、epoch、progress、runner、scoring、audit、deployment、通信/位姿 |
| 同提交 ljy 同组 12 文件 | 222 passed，6.92s | 缺地图首次失败已单独保留，补资产引用后通过 |
| 独立 City 复核 | 三项阻断关闭 | 运行前后源码指纹、非零 native epoch、单次 manifest |
| ljy 原生局部诊断 | completed，1 条成功技能回执，error=null | 手写局部 plan；不是真实模型完整任务证据 |
| 原生归档的独立磁盘复算 | session/progress/sidecar 通过，21 文件指纹一致 | 内部一致性，不是原生 reader 的完整认证 |

本机证据目录 `tmp/stage39-validation/`：
`local-city-final.log`、`ljy-city-final.log`、`test-files.txt`、
`ljy-city-before-assets.log`、`ljy-city-native.log`、`native-disk-replay.json`、
`ljy-native-artifacts/`。运行输出不提交，本文保留结果与可恢复路径。

独立审核与全局进程审核分别记录在团队共享区的
`stage39-autonomous-measurement-20260924/handoffs/city-review-final.md` 和
`benchmark-global-progress-20260924/GLOBAL_STATUS.md`。全局审查角色后续复用，
按验收证据更新状态，不用测试数量估算完成百分比。

## 原生诊断实际测到了什么

提交 `1504a99`，隔离工作树
`/home/ljy/eai-simulator-worktrees/benchmark-stage39-autonomous-measurement`；episode
`stage39-native-city-guide-go2-v1`，输出 `tmp/stage39-city-native-guide-go2-v1`。
使用 City 完整三机候选池，手写适配器只选择 Go2 并派发一次 `guide_people`。
PhysX 使用 City service-probe 所需的 CPU current-query 模式，Isaac 渲染仍使用 ljy GPU。
有界运行上限 600 秒；没有修改题包、见证、资产哈希或机器人能力。

预热 epoch 为原生 `10.02s`。五个进展边界的相对时间约为
`0 / 29.98 / 29.98 / 60 / 60s`；技能回执的 host 时间为 `30s`。
业务 evaluator 等待人员读回会继续推进物理时间，所以末尾 host 时间与 native 时间不同；
二者分别归档，没有把 host 时钟冒充物理时间。源码前后指纹一致，session audit 通过。

业务仅 `incident_assessed` 通过；人员核实、后续交通保护、响应通道保持三项失败。
这是局部计划的实际归档结果，不可当作整体业务成功，也不是被测模型成绩。
默认初始 oracle 仍是 `failed/null/platform_valid=false`，不进入部署判断正式统计。
进展 `observed` 只表示采样链完整；首次物理满足时刻、连续变化覆盖和正式 Q(t) 尚未认证。
发布标志继续保持 `execution_validated=false`、`business_outcomes_validated=false`、
`scoreable=false`。

## ljy 隔离树准备与复验

代码通过增量 `git bundle` 同步，干净隔离树 `merge --ff-only` 到冻结提交；不改 ljy 原工作树。
在新隔离树中补充仅指向既有资产的引用：

```bash
mkdir -p usd/demo tmp/city_benchmark
ln -s /home/ljy/eai-simulator/usd/scene/city usd/scene/city
ln -s /home/ljy/eai-simulator/usd/demo/city usd/demo/city
ln -s /home/ljy/eai-simulator/tmp/city_benchmark/recovery_binding_v2_20260922 \
  tmp/city_benchmark/recovery_binding_v2_20260922
```

只在目标不存在时创建；不覆盖已有路径。City 地图 SHA256 双机均为
`5ef52d1a1ade14355de9062f6371131eaa092e1c7bec38e45e75e0ea616fdc93`。
运行时设置 `EAI_USD_ROOT=/home/ljy/eai-simulator/usd` 和
`EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller`。
单元测试使用 `env_isaaclab/bin/python`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 及仓库四包 PYTHONPATH。
这些步骤复用本机已有资产，不宣称从空白机器完成资产发布准备。

## 下一批优先级

1. **R1 公开观测与完整自主计划**：提供可信宿主测量的版本化 City observation intake，
   将真实算法接到公开输入；不能以旧 witness-derived `base_plan` 或测试私有常量代替。
   补齐实际服务开始、ledger dispatch 和计划窗口对账。普通 baseline 不强加动态故障。
2. **R1 初始 native oracle + R2 逐谓词事实**：先闭合 `city_initial` 二元部署判断；
   逐谓词处理 pending 证据，避免一个未完成字段延后其他已满足谓词的记录。
   沿同一真实 episode 归档通信、成本与缺失原因，独立磁盘复算。
3. **完整 City baseline 后再扩条件**：真实并发、成员退出及取消只对有相应机会的条件验收；
   原生 restore/sham 只阻挡严格同快照结论。随后逐场景形成资格证据，不用405数量替代运行。

权重、正式 Q(t)/AUP 参数及新多端点接口先形成可审阅提案；不擅自补值。
无需这些参数即可继续 R1 接线、oracle 原始证据和 R2 数据生产。
