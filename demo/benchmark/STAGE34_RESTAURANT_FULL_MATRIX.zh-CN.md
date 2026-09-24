# 阶段 34：餐厅完整接入五场景 405 任务矩阵

日期：2026-09-24；接力基线：`5aa2626`；开发分支：`codex/benchmark-five-scene-405`。

## 用户决定与计数

餐厅按其他四场景相同范围全量加入。目标为 **5 场景 × 9 任务变体 × 9 条件 = 405**。
每场景的九个变体是 baseline、四个 M0 和四个 M1；九个条件是五档通信条件与四档团队连续性条件。
因此完整矩阵有 45 个 scene/variant 组合、405 个条件题包、315 个单因子对照对。
只看 baseline 的条件子矩阵是 45 包/35 对；只看机制模板是 40 个（M0 20 + M1 20）。
这些数字分别记账，不把 45 个 baseline 条件包称为已经生成 405 包，也不把条件重复数当成独立业务目标数。

405 表示可复现的出题/实验矩阵，不能解释成 405 个原生 episode 已通过或 405 个独立学习机会。
机制变体沿用既有生成方式，业务目标相同，测量合同不同；还需真实干预和证据才能支持能力归因。

## 接力事实与流程

已读取 `/home/pupil/.zcode/cli/log/zcode-2026-09-24.jsonl` 及其关联会话
`sess_08c96c93-d2bb-4414-a108-1620884696ba` 的 model-io 记录，并对照阶段 32/33 文档和实际源码。
ZCode 已完成五场景 reader 契约、餐厅三机业务 episode 验真、判卷归档重放、p2p 工具接线和共享传输计量去重。
因此不再重复五场景 reader 盘点，也不以 Factory 资产缺口阻止无关开发。

沿用“快速并行开发、严格发布隔离”：主任务协调/审核/提交，两个工程代理分别负责模板覆盖与全量生成/交接；文件所有权互斥，代理不执行 Git 写操作。
本机持续集成，ljy 用 bundle 同步到新隔离工作树运行回归，不动其原工作区。

| 优先级 | 所有者 | 工作 | 验收 |
|---|---|---|---|
| P0 | 主任务 | 餐厅 registry 与九档因子、生命周期关键能力 | 单机串行合法，失效作用于 relay 而非不存在的 pick；基线哈希不变 |
| P0 | 模板代理 | 餐厅 M0/M1 八机制、覆盖矩阵 v2 | 五场景全覆盖；不能凭模板宣称必须双机或必须空中成员 |
| P0 | 矩阵代理 | 完整 405 包生成、读回、315 对照、五场景交接 | 缺包/重复/篡改拒绝；参与者公开区不含作者见证 |
| P1 | 主任务 + ljy | 集成回归、真实生成与验证、分批推送 | 当前代码的双机证据和差异归因落盘 |
| 后续 | 分批另行所有权 | 真实下行调度、单场景 native 恢复、Q(t)/六轴生产器 | 沿用阶段 33 路线；未拍板参数不私自赋值 |

## 发布与兼容边界

`goal_bundle` 在本批表示纳入统一出题矩阵，**不意味着原生执行或正式评分通过**。
保持 `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
冻结 baseline 的题面、候选池、见证、reach、overlay 和哈希不改；旧餐厅题面/config 中的待准入文字保留供历史哈希重放，当前矩阵范围由 registry 和本文说明。
新生成的机制变体与条件包写到全新目录，旧归档不覆盖，旧参与者 API 不新增必答动作。

餐厅已有原生证据只覆盖相应运行与测量点。通信/团队干预仍须独立实测，旧 `cross_mobility` 触发窗口不保证在合法单 Carter 路径上出现；未触发不得算成一次有效干预。
Factory 冻结 USD 不匹配仅阻塞其正式资产准入，不阻塞本批模板/矩阵/契约回归。

独立审查发现的实验设计缺口也保留为后续 P0：既有机制变体的 public task_id 和
business_constraints 会显示 mechanism 名称，因此不能宣称机制分类对模型隐藏。
这不是本批新增行为；保留既有四场景变体内容，后续采用版本化的公开任务身份/约束
投影处理盲测，连同可达的 observation/业务谓词干预触发一起验收。不能仅改触发字符串
便宣称真实干预已生效，也不能借此强制餐厅使用空中机器人。

新餐厅 M1 包使用正确的 `M1` 任务 ID 后缀。既有四场景 M1 包沿用历史 `M0`
后缀以保护已归档身份；机制分类以隐藏 template_id 和矩阵 variant_kind 为准，
不能从旧 public task_id 后缀推断分类。餐厅新增断言覆盖全部八种变体。

## 本批证据

第一批注册/模板已完成；完整矩阵及双机验收仍在进行。当前实际证据：

- `test_benchmark_measurement_contract.py`、`test_restaurant_goal_task.py`、
  `test_benchmark_goal_driven.py`：本机 **96 passed**。
- `test_benchmark_task_coverage.py`、`demo/benchmark/tests/test_measurement_contract.py`、
  `test_benchmark_mechanism_templates.py`、`test_restaurant_runtime_scene.py`：本机 **90 passed**。
- 使用 `env_isaaclab/bin/python -m pytest -q`，关闭自动加载外部 pytest 插件，
  `PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy`。
- 与接力基线生成前快照逐项比较：五场景 baseline 的 public_task、participant_api、
  hidden_evaluator、baseline_manifest 共 20 个 canonical SHA-256 全部不变；
  原有四场景 32 个机制模板的聚合 SHA-256 亦不变。

后续矩阵、交接、ljy 和推送结果在实际完成后补记。

首轮广泛回归发现覆盖矩阵扩容后的相邻遗漏：modality_matrix 从共享注册表读取五场景，
但审计 JSON 仍是四场景，19 项测试失败，已交同一模板代理补齐有源码证据的餐厅审计。
该轮还收集了正在开发的矩阵测试（显式文件参数使 `--ignore` 未排除它们），
七个 setup 错误来自校验器错误读取 public_task.execution_validated，已定位并修复。
首轮不是最终验收证据；最终运行应冻结所有被哈希的源文件再生成和回归。

## 后续阶段的明确出口

1. 版本化的事件触发：在单 Carter 与多机合法路径上，记录真实 observation 发布、
   treatment 注入、投递/失效生效、业务提交四个时点；未触发样本不得进入有效干预分母。
2. 公开身份与盲测设计：保留旧包重放接口，新版参与者投影使用中性身份和证据要求；
   检查所有公开字段，不把机制名或作者参考顺序当作提示。
3. 沿用阶段33主线，打通真实下行派遣/取消、一个场景的 native checkpoint/restore
   与 sham 一致性，然后接六轴生产器和统计。评分矩阵中 44 项未冻结参数仍不赋临时值。

上述是原生干预/测量效度的下一阶段工作，不以“405 个作者包已生成”替代验收。
