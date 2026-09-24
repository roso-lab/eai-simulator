# 阶段36第一批：计量纠错与自由计划证据契约

基线 `2491fe4`；开发分支 `codex/benchmark-stage36-measurement`。
本批落实36A及36B的证据服务/接线设计，不宣称36C原生并发已实现。

## 实现范围

1. 通信容量新增显式 v2：实验用 bit/s，在单一边界转换成 runtime B/s；真正无限
   参考、C0同口径验证、方向与池一致，旧v1行为不变。固定消息流明确只测链路，
   旧散布丢包不称Bernoulli IID，旧交替延迟不称随机抖动。见
   [容量v2](COMMUNICATION_CAPACITY_V2.zh-CN.md)。
2. 餐厅新增独立 readiness service v2：重复观察/中继重试完整留档，最终回执选择
   满足业务条件的因果证据；允许单机器人复用和不同检查顺序。旧服务未替换。
   这只是可信证据服务及独立回放校验，不冒称新增原生执行或传输送达证据。
3. 自主原生接线契约明确了题面身份、candidate×skill×route覆盖、部署门禁、
   可选通信工具、真实并发与独立计划验真边界。见
   [自主执行契约](RESTAURANT_AUTONOMOUS_EXECUTION.zh-CN.md)。

## 独立审查与反思落实

通信工程由既有工程代理负责；主代理实现服务v2并审查容量代码；另一既有代理
只读梳理自主接线路径并审查服务。写集分离，不同代理没有并发改runner/native入口。

审查要求实际传输时长证明单位换算，不能只断言输出字段。确定性负载的seed只是
身份，不是独立随机重复。服务摘要证明内容一致性，不代替物理证据。

覆盖不足不能用虚构member_exit/capability_change包装成团队失效；该建议已在
独立设计审查中撤回。缺少驱动的完整正式条件在测量前按平台覆盖缺口阻塞。

## 验收记录

容量提交 `2dd0b44`，readiness/自主接线契约提交 `833c4ad`。独立服务审查发现未知
字段可夹带验证声明（原生验证返回本身仍是false），已补三类记录的精确字段集，
6个unknown/missing测试包含注入验证字段后重算摘要的攻击；独立复核关闭阻塞。

本机：通信/归档/评分/池/runtime七文件 **192 passed**；餐厅服务与三个既有入口
五文件 **83 passed**，随后字段集加固后的新旧服务 **46 passed**（含独立审查复跑）。
最后一项替代较早服务结果，不能把这些重叠计数相加冒充独立测试数。

使用命令前缀：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=source/EAI:. \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q <tests>
```

通信七文件为 `source/EAI/test/` 下的
`test_benchmark_communication_capacity.py`、`test_benchmark_communication_analysis.py`、
`test_benchmark_communication_capacity_v2.py`、`test_benchmark_goal_communication_archive.py`、
`test_benchmark_goal_scoring_adapter.py`、`test_benchmark_communication_pools.py`、
`test_benchmark_communication.py`。餐厅五文件为同目录的
`test_benchmark_restaurant_service_v2.py`、`test_restaurant_service_backend.py`、
`test_benchmark_goal_restaurant_episode.py`、`test_restaurant_algorithm_episode.py`、
`test_restaurant_native_transport.py`。

ljy通过 `ba64712..833c4ad` 的git bundle同步到新隔离树
`/home/ljy/eai-simulator-worktrees/benchmark-stage36-measurement`，没有改远端原工作区。
同一12文件首轮 **272 passed, 9 skipped**：隔离树未配置已有控制器/餐厅产品路径。
使用以下已有资产根补验受影响的 `test_benchmark_goal_restaurant_episode.py` 和
`test_restaurant_algorithm_episode.py`，结果 **34 passed, 0 skipped**，9个原跳过项
全部补验；合计覆盖281个不同用例，非单次281通过。日志在本机 `tmp/stage36-validation/`。

```bash
EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller \
RESTAURANT_PRODUCTS_ROOT=/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e/tmp/restaurant_benchmark/products-candidate-v7 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
  /home/ljy/miniconda3/envs/env_isaaclab/bin/python -m pytest -q -rs \
  source/EAI/test/test_benchmark_goal_restaurant_episode.py \
  source/EAI/test/test_restaurant_algorithm_episode.py
```

本机实际生成两份容量v2诊断：默认扫描只报阈值在已测最小值以下，不外推；独立
诊断扫描 `(None,4000,2000,1000,500,250)` bit/s，在uplink与p2p均把0.9/0.5投递
保持率阈值包围在 `[1000,2000]` bit/s。它是固定消息流的链路诊断，不是正式
C90/C50能力成绩；未创建C0、正式随机分布或评分参数，重复seed不算随机重复。
原始输出在 `tmp/stage36-validation/capacity-v2-*-diagnostic.json`，不提交生成文件。

本批为纯Python契约与诊断修复，不启动Isaac/GPU；不能用离线测试勾选原生自主
拆题、并发或同快照恢复。初次裸 `python -m pytest` 因base环境无pytest未运行，
随后全部使用现有 `env_isaaclab`，没有安装或修改环境。

下一批：按36B契约统一开发自主入口/实际计划验真，再实施36C执行协议及事件泵；
运行中部署判断、资源互斥、取消投递和本地安全停止须一起验收。餐厅v2服务接线
必须有独立native evidence绑定，而不是仅更换service类名。

发布门禁 `scoreable=false`、`execution_validated=false`、
`business_outcomes_validated=false` 保持；冻结题包、见证、reach与用户文件不改。
