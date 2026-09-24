# 阶段42有限验收：模型作答前的原生资格证据

证据日期：2026-09-25（Asia/Shanghai）。实现提交 `d529daa`，说明见
[初始资格证据](STAGE42_CITY_INITIAL_QUALIFICATION.zh-CN.md)。本批完成证据采集与评分
归档接线，不构成完整City任务可部署证明，也不改变R1–R5发布出口。
实现已正常推送GitLab `benchmark-task`；`git ls-remote`核对远端为
`d529daa296edd6695acc8decdc56881d20b24c16`。随后文档批次只同步验收与路线。

## 实现与验证范围

- 显式启用 `requested_target_v1` 时，精确绑定City v3任务hash，在参与者initialize前
  读取单一原生时刻，保存evaluator-only初始档案。旧入口不静默升级。
- 采集不推进仿真、不派遣、不预约、不占用通信链路；参与者仍只接收原有计量工具消息。
  有/无采集的受控测试对比中，participant trace和communication report完全相同。
- 记录缺证、声明能力和执行后才能获得的证据；16条记录的完整性与有效测量数分开统计。
  来源、精确任务、原生时钟、实体范围和归档hash均可核对。
- 保持冻结题包、见证、reach、容量、资产与hash不变；三份用户保护文件hash核对一致。

最终代码同一30文件清单：`tmp/stage42-validation/test-files.txt`。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
<env_isaaclab>/bin/python -m pytest -q $(cat tmp/stage42-validation/test-files.txt)
```

本机 **613 passed / 36.81s**；ljy隔离树 **613 passed / 14.20s**。
独立代码审查在最终文件hash上运行初始资格与自主入口测试，**28 passed / 5.25s**，
diff检查通过，无代码级阻塞。双机通过不增加独立实验样本数。

ljy沿用隔离树 `benchmark-stage39-autonomous-measurement`，bundle同步、ff-only更新，
配置已有 `EAI_USD_ROOT` 和 `EAI_CONTROLLER_ROOT`，未修改其原工作区。

## 有界原生诊断

运行：`stage42-city-initial-capture-v1`；native episode：
`a9425b1b-192a-4831-ae44-9a8e290a1140`。复用已有CPU PhysX/CUDA渲染配置，单次600s上限，
正常退出。确定性公开局部策略从公开测量选择Carter引导人员02到candidate004，
获得1条succeeded技能回执，`result.error=null`。

在上述ljy环境变量基础上，将 `source/EAI/test` 追加到PYTHONPATH，实际运行：

```bash
timeout --signal=TERM --kill-after=30s 600s \
  <env_isaaclab>/bin/python tmp/stage42-validation/native_city_initial_capture.py \
  --run stage42-city-initial-capture-v1 --member 1 --rank 1
```

初始档案测量时刻为native `10.019999999999875s`，episode相对 `0s`，前后时钟一致，
采集时所有候选预约数为0。精确task SHA为
`143a7ec986e256eaa0ec96c5853164ed558da5b57d52c7ab9f41234561c2be2f`。

| 证据 | 原生结果 | 解释边界 |
| --- | --- | --- |
| 人员/后续车辆 | 4人、6车精确身份和初始位置 | 初始读数，不是未来业务保证 |
| 候选与路线记录 | 4候选、16条记录 | 13条有效测量：5 admissible、8 blocked；3 unavailable |
| 单提案图最大匹配 | cardinality=3 | 未穷尽替代路线，不能推出not_deployable |
| 资格问题 | quality_issues为空；作者派生几何限制保留 | 读数完整不表示集合区资格已通过 |
| 业务 | incident_assessed通过，其余3项failed | 本次局部策略没有尝试完成全任务 |

磁盘独立核验29项归档字节数/hash；session audit、progress、communication replay均通过。
初始档案与task、source、clock、participant trace和scoring index连接核对通过；
参与者事件中无初始资格档案。源文件前后hash一致。回放证明内部一致性，物理结论仍以
原生读数为据，不因回放成功成为正式物理认证。

本机证据保留在 `tmp/stage42-validation/`：双机日志、完整原生归档、native.log、
`native-disk-replay.json`、`initial-capture-check.json`及独立核查脚本。
初次scratch核查脚本使用UTF-8直写JSON，而冻结canonical hash使用ASCII转义，导致一次
hash断言失败；按既有 `provenance.canonical_sha256` 编码修正核查脚本后通过。
生产代码与归档未修改，未跳过断言。

## 下一出口与停止扩张边界

R1下一P0是公开几何与原生集合区资格一致，再完成公开输入下的完整City业务诊断：
四人员、六后续车辆、报告/持续通道及response_dispatch硬条件。修复须为显式版本化扩展，
不按作者指派扩容量，不为了某次成功选择尺寸，不继续扩建通用PhysX规划器。
完整构造性成功只能支持对应精确初始状态的正例；失败路线或一次运行失败不支持负例。

R2继续使用同一归档生产评分事实，缺失计算成本不写零，未来终局证据不冒充初始已满足。
R3条件效度、R4原生restore/sham、R5发布认证仍未完成。405条件包/315对仍是题包覆盖数量。

`oracle_status=failed`、`feasibility_label=null`、`platform_valid=false`保留。
这是平台未认证证据状态，不是模型第三种部署答案，不进入正式部署判断分母。
全程 `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
