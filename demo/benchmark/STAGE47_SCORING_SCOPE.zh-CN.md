# 阶段47：停止无效几何扩展，修正评分证据声明

输入提交 `f249df2`；2026-09-25。发布标志全false；本批不运行新的GPU episode。

## 有界几何探索的停止条件

拟议的根 `ComputeUntransformedBound` 变换公式已在OpenUSD 0.26.8被证伪：
根朝向45°、子层20°的非方形fixture仍回到原world AABB，误差不超过3.6e-15。
逐leaf `ComputeRelativeBound`替代方案也不适用于四个实际人物资产：全部含参与
聚合的Boundable SkelRoot，下有Skeleton/Mesh。leaf聚合相对既有world bound最大
坐标差依次0.0400504、0.00594052、0.132651、0.131714m，不能声称保留原包络语义。

因此**不实现v3、不扩展USD通用包络框架、不重复native**。现有v1/v2、圆盘、到达、
安全、任务、业务判卷均未修改。阶段46可见网格证据仍只证明测量分歧，不认证碰撞体，
不把诊断inside变为业务成功。原生业务仍3/4，初始oracle仍failed/null，R1未完成。
失败不能归为模型错误；需要后续明确身体/到达契约后才能修正这类平台测量限制。

只读探索证据（本机工作产物，非发布资产）：
`/home/pupil/Documents/Codex/agent-team/workspaces/stage47-city-arrival-contract-20260925/handoffs/stage47-geometry-closure.md`。
独立审查在同目录 `plan-review.md`；四资产不满足支持集合即为本轮停止条件。

## R2实现边界

评分metadata映射升级为 `protocol-20260925-goal-claim-scopes-v3`，schema v1保留；
参与者公开接口和题包hash不变。移除无条件的固定子任务缺口、没有通信、runner串行、
没有checkpoint等旧描述。raw-only和独立发布验收仍是不可计分原因。

新增按声明分类的证据要求：普通基线、通信适应、并发协调、严格同快照比较。
这是要求清单，不是自动判断本次声明是否适用，也不是认证结果。普通基线无需人为
制造分支或并发，但仍需可信执行/业务与部署oracle。适配器的checkpoint、trusted-state
和clock认证状态明确为未出具认证，具体已有证据保留在原raw observation子块。
四个快照状态字段由无依据的布尔值改为null，mapping v3消费方需支持可空值；字段名
保留。可选branch档案缺失只记absence_reason，格式错误仍是invalid；严格分支声明
仍须独立验收restore/sham，不能把没有全局质量错误误读为分支资格通过。

自主分解不是协议缺陷；分配调度证据为参与者方案的事后审计、二元部署判断与外部
里程碑进展。未冻结评分参数不赋值，不新增公式，不因报告存在或自报verified晋级。

## 验收与下一步

- 本机联合回归：25个文件、410 passed /32.84s；列表与日志在
  `tmp/stage47-validation/test-files.txt`、`local-joint.log`。
- 独立审查：评分adapter与runner focused 62 passed /4.11s，diff-check通过；
  `handoffs/scoring-final-review.md`无阻塞。已修正空档快照真值、可选branch缺失分类
  及任务设计证据尚未观测的说明，旧文档明确由新mapping取代。
- Stage46真实native档案只读兼容重算：9个原始数据/身份子块全部与旧侧车相同，
  30个源文件hash未变；输出`tmp/stage47-validation/native-sidecar-migration.json`。
  这是旧证据的离线读取，不是新增native成功，也没有覆盖旧档案。
- ljy本轮SSH退出255、`No route to host`；远端复验未做。三份用户文件hash保持。

R1–R5仍未完成。下一P0优先接上评分原始事实与实际缺失原因，并对City初始oracle
做有界条件认证；几何争议单独保留，不以更多相同GPU尝试替代修订测量契约。
餐厅完整八机自主接线、其他条件和正式六轴统计仍待完成。

下一评分审计重点：当前`task_allocation_observations`的false_refusal_rate与
false_deployment_rate均除全部verified epochs；须对齐用户“可行实例误拒”和
“不可行实例错误部署”的条件分母，明确零分母、重复epoch与跨实例聚合层级，
保留计数可追溯性。本批不修改公式或将该诊断率宣称为正式成绩。
