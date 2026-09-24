# Stage43有限验收：逐项进展与集合区支撑采样

日期2026-09-25，代码提交 `c3c0104`。本批并行完成评分源修复和独立集合区支撑采样器。
规则见 [几何与进展契约](STAGE43_CITY_GEOMETRY_CONTRACT.zh-CN.md)；完整R1–R5仍未完成。
实现已正常推送GitLab `benchmark-task`，ls-remote核验为
`c3c010422216379072d0cf4a89c0f210096cdc58`。后续文档批次只同步实际验收和当前队列。

## 评分事实生产

`CityAcceptanceProgressReader`不再因另一业务项的null时间戳而隐藏独立已满足项。
它保留raw envelope，先校验所有非null时间戳，再经五个明确依赖投影复用原业务evaluator。
四项public predicate与隐藏`response_dispatch`硬条件分开；各自保留预算、相对窗口、
身份全量性、报告/通道覆盖及派遣/观察送达因果。缺少dispatch的观察送达依赖为pending；
只请求部分predicate不能声明全量证据齐全。没有新的权重、任务分配规则或成功谓词。

`city_progress_sources.json`升级为schema v2：保存原始证据、精确roster、projection、
canonical review、budget decision、predicate vector和hard condition。非有限数值导致
基础设施失败，归档用显式marker保留位置/类别，不能因JSON写入失败丢失故障。
`mission_success`保留null；是否收齐证据不等于成功或可打分。

仍在既有runner边界采样，未声称逐帧发现最早满足时刻。Q(t)权重、正式AUP/RMST、
计算成本及完整事件泵仍按路线图待验收。本修复解决跨业务pending互相遮蔽，不改变时钟源。

## 集合区支撑采样

新增 `demo/city_benchmark/assembly_support.py`：按public四候选ID连接冻结map绑定，
保持各半径0.75m、容量1及0.3m参考平面；不输入作者人员指派或身材。每个圆盘9次
PhysX支撑采样并核对静态碰撞体身份；不同步、畸形或不可信来源优先归unknown，
可信缺支撑归unsupported。所有样本收齐后定类，混合证据不能被提前归成事实负例。

ray XY一致性采用固定公式max(1e-5m, 请求世界坐标float32的4 ULP)，逐轴归档，
不改变候选尺寸、平面、容量或身体检查。±75m量化回归及0.1m错误反例均已覆盖。
该采样器不宣称连续支撑或全身净空，不启用v2执行；v1 AABB、准入和到达行为均保持。

独立范围审查删掉了通用triangle-union覆盖门槛和新增人员足部1e-5m到达约束。
这些不是高层协作测量所必需，不能为了基础技能保证无界扩建物理验证。

## 双机与原生证据

最终代码的同一32文件清单位于 `tmp/stage43-validation/test-files.txt`，实际运行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
<env_isaaclab>/bin/python -m pytest -q $(cat tmp/stage43-validation/test-files.txt)
```

本机 **674 passed / 36.94s**，ljy隔离树 **674 passed / 14.49s**。
独审评分44项、支撑30项通过；py_compile/diff通过。ljy以bundle同步、ff-only更新，
沿用已有USD/controller根环境变量；原工作区与三份用户保护文件未改。

| 原生运行 | 结果 | 范围 |
| --- | --- | --- |
| stage43-support-inventory-v2 | 同tick36次hit，四候选均命中静态sidewalk Mesh | 输入盘点，不是资格认证 |
| stage43-assembly-support-v2 | 四候选均sampled_supported，36样本同tick；源SHA与提交一致 | 采样器实测，不是v2执行或连续支撑证明 |
| stage43-city-progress-v1 | c3c0104完整代码，error=null，1 succeeded技能回执 | 局部公开策略；业务仍仅incident_assessed通过 |

支撑采样器探针在Stage42原生宿主上只读运行，与c3c0104相同SHA的模块通过临时路径加载；
随后同模块已进入c3c0104并通过双机回归。支撑probe native episode为
`3caee362-ad86-43a8-a5da-6cda46da9f5d`；时钟前后均`10.019999999999875s`。
四候选坐标、半径、容量、每样本容差及碰撞来源连接离线核对通过；范围是内部一致性，
实际支撑读数来自native查询。

新的Goal原生运行复用既有公开局部策略，实际命令为：
native episode为 `7ddd4b89-ae96-4a3e-b958-170eb7d52935`。

```bash
timeout --signal=TERM --kill-after=30s 600s <env_isaaclab>/bin/python \
  tmp/stage42-validation/native_city_initial_capture.py \
  --run stage43-city-progress-v1 --member 1 --rank 1
```

沿用CPU PhysX/CUDA渲染，并在PYTHONPATH追加source/EAI/test。
29项归档字节/hash、session/progress/communication三类replay、初始资格连接均通过。
额外从磁盘读取评分源 schema v2 档案，独立重算全部5个边界的projection/review/vector/hard condition，
与实际进展账本一致。初始全unknown，skill_completed起incident与dispatch为true，
其余三业务仍unknown；最终业务evaluator将未完成项记failed，二者口径不同，不混为成功。

失败证据也保留：inventory v1的scratch字段拼写错误；collector v1在约75m世界坐标
处的float32回读一致性unknown。前者修脚本，后者按量化误差规则修验证器，均未改物理
几何或资产。所有原档、最终档和核查脚本位于 `tmp/stage43-validation/`，不提交运行数据。

## 下一阶段

把已冻结的独立圆盘与支撑采样接到显式v2的目标、准入、占用、控制和完整身体到达读回，
同一规则所有消费者一致后才启用profile。保留v1和全部候选/16对记录，复用已有路线/身体
检查，不扩大区域或解算通用物理计划。随后运行四人员、六车、报告/持续通路的完整公开
业务诊断；初始二元oracle资格仍单独验收。

全程 `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
当前部署oracle仍failed/null，未进入正式部署统计；405条件/315对不等于原生通过数量。
