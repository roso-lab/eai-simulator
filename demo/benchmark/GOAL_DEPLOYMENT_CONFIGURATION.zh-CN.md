# 四场景部署配置与可靠技能参考执行

`goal_deployment` 将冻结的公开题面编译为作者侧部署配置，支持 Factory、Warehouse NVD、
Modern House NVD 和 City。它交付运行时需要应用和读回的参数、独立实体身份及空间绑定候选。
配置审核通过不表示这些配置已经应用到 Isaac，也不表示业务任务可以计分。

## 参数和身份

题面中的质量、尺寸、固定抓取点、搬运模式、负载及机器人接口是实验契约。
Warehouse 木箱/纸箱保持 12/3 kg，House 扶手椅/书堆保持 14/2 kg；部署指令保留旧道具参数
和显式缩放/覆盖要求。没有通过审核的原生候选会留下阻塞项。编译器不改 USD 或旧固定 DAG。

每名成员具有独立 `runtime_id`、`prim_path`、`command_namespace` 和 `backend_state_key`。
例如两个 M20 分别编译为 `m20_1` 与 `m20_2`，不能映射到同一个实体。
`verify_configuration_readback` 要求完整实体清单、不同实体身份、精确参数及机器人接口配置；
比较结果只说明传入的读回值一致，真实性仍须由可信运行时采集器及物理证据确认。

室内适配器直接重建当前场景清单，不依赖历史 `tmp` 报告。对象占据范围须落在明确的初始区域
候选边界内；点、footprint、路线检查分别标注范围。地面路径按公开宽度保守膨胀，路径长度
与题面名义长度分开保存。地图候选仍缺组合场景一致性、实际碰撞/支撑和动态占用验证。
工厂 `main_stair` 保持公开开放；楼梯连续入口—梯段—平台及机器人通行的物理证据未就绪，
不能用二维地图放行。空中路线和出生位同样需要三维检查。

City 从传入准备包读取全部公开区域、通路和机器人。建筑等静态设施不伪装为可搬物体，
没有公开质量就不推断质量。道路折线检查连续栅格覆盖，并显式比较已有地图膨胀与机体包络；
不足、静态冲突和未规划的接近路线均保持阻塞。人车、危险和机器人动态占用另需当前证据。

## 生成和重验

所有 Python 命令在 `env_isaaclab` 中运行。以下命令不启动 Isaac、ROS 或下载资产。
本地地图、场景记录和已生成的城市准备包需要存在。输出目录必须是新目录。

```bash
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_deployment audit \
  --city-package tmp/city_benchmark/authoring_16 \
  --output-dir tmp/goal_deployments_new

conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_deployment verify tmp/goal_deployments_new/warehouse_nvd.json

conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_deployment_conformance \
  --deployment-dir tmp/goal_deployments_new --output-dir tmp/goal_reference_new
```

`audit` 固定输出四份配置。`verify` 从当前来源重建适配器结果和编译结果，不能靠改状态字段
或重新计算 JSON hash 把未绑定项升级为已验证。代码或输入变化后旧报告应保留为历史证据，
重新生成到新目录。配置包含作者候选、来源路径和阻塞原因，不属于参与者公开题包。

## 可靠技能参考后端

`ReliableSkillReferenceHost` 通过真实 `GoalSession` 接收选队、计划、派遣、取消和当前事件，
独立维护已提交的机器人/对象状态。导航、固定点 pick/place、联合搬运、楼梯和同步具有确定性
参考语义。无需自主抓取搜索或 VLA；合法性仍检查负载、固定点接口、参与成员、公开净空、
资源和依赖。尚未派遣、受阻、取消或失效的任务不能改变持有关系和位置。

宿主必须给出所有当前成员的位置，以及带时间和状态版本的占用事实。缺少或过期即等待；
起点、途经区域、目的地或路线受阻即保持状态。成员退出后旧位置失效，同名重新加入需要
重新绑定位置。等待后执行会重验当前能力和证据有效期。取消完成前保留资源；外部技能的
取消/失效必须由其所属后端确认，参考后端不能代造回执。

参考动作是串行、原子的位置/持有状态变化，没有中间轨迹、接触求解或连续动态避障。
占用输入由宿主提供，空集合只表示该参考状态没有已知阻塞。运行报告固定标记
`origin=deterministic_reference_not_simulator`，不会生成观察、业务交接或评价成绩。

`goal_deployment_conformance` 在每个场景建立明确的符号诊断初态，提交一个合法导航动作，
检查无占用证据时等待、阻塞时保持、清除参考阻塞后提交效果。该初态不是配置中的物理出生位。
`verify_reference_conformance(report, deployment)` 从当前实现重新执行并比较完整结果；
该诊断覆盖协议及参考状态转换，不证明四场景业务流程可执行。

## 城市服务与后续交接

城市侧边会话维护 `CityServiceBackend`、`CityRuntimeReadbackAdapter` 和请求适配器。
配置记录 `guide_people`、`control_traffic`、`report_incident` 的服务要求，但不把它们激活为
共享技能。`field_role_interval(agent_id, role, region_id, as_of_s)` 由可信 episode 宿主提供
历史连续驻留区间：引导和交通控制要求从提交到逐个对象确认期间持续在岗，之后独立服务
轨迹可继续；事故上报只在提交时要求在岗。当前不支持隐式 transfer，替补须提交新请求。

请求确认不等于人员到达、车辆停止或接收方收到事故报告。`evidence_complete` 也不自动证明
读回经过物理认证。City 继续为准备稿，完整业务见证、共享服务接线、当前运行证据和正式
treatment 均需补齐后审核。

下一阶段需运行时实际建立独立机器人及附件，应用对象配置并读回，绑定区域、地面/空中/
楼梯的连续几何，提供当前占用采集器及可靠技能物理适配器。基础运动或抓取算法成功率不作为
协作评价目标；任务物理可行性和真实业务证据仍是正式发布的必要条件。

## 阶段11实际验证记录（2026-09-18）

四场景配置写入 `tmp/goal_deployments_20260918_stage11/`，参考诊断写入
`tmp/goal_reference_20260918_stage11/`。两次 CLI 均退出0；随后从磁盘分别加载四组文件，
调用 `validate_goal_deployment` 与 `verify_reference_conformance` 重建，全部通过。
这些目录为本地运行证据，不作为维护源码或公开题包提交。

下列显式测试在 `env_isaaclab` 中得到 **468 passed in 29.58s**：

```bash
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q \
  source/EAI/test/test_benchmark_goal_deployment.py \
  source/EAI/test/test_benchmark_reliable_skill_backend.py \
  source/EAI/test/test_benchmark_goal_deployment_conformance.py \
  source/EAI/test/test_benchmark_goal_indoor_deployment.py \
  source/EAI/test/test_benchmark_goal_city_deployment.py \
  source/EAI/test/test_benchmark_goal_scene_binding.py \
  source/EAI/test/test_benchmark_goal_session.py \
  source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_city_intake.py \
  demo/city_benchmark/tests/test_service_backend.py \
  demo/city_benchmark/tests/test_foundation.py --tb=short
git diff --check
```

测试和重验没有启动 Isaac/ROS，也没有修改 USD。读取现有木箱 USD 时出现旧 crate 格式弃用
提示，读取成功。阶段10的实跑来源被冻结在历史产物中；阶段11新增生产代码后不能宣称旧
运行已验证当前全部实现。
