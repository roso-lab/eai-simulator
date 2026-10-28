# 算法适配整理与分支集成（2026-10-28）

本次将当前会话的算法适配、测试、工具、配置和实测记录整理到远端已有的 **HeteroCoord** 分支（区分大小写；没有另建 HeterCoord）。集成基线为 `e21c543e7f9cac48f3a908e04bf0cfe27d31b084`。原工作树的场景、网站、论文和训练改动不在此提交范围。

## 接入与目录

- 统一入口：`benchmark.native_algorithms.build_native_adapter`，CLI：`python -m tools.algorithm_adapters.run`。
- 算法来源、锁定版本、论文 PDF 摘要：`paper_algorithms.json`；约束：`ADAPTER_IO_POLICY.zh-CN.md`。
- 适配实现：`algorithm/system/coordination/<method>/native_adapter.py`。
- 环境和调用说明：`tools/algorithm_adapters/README.md`、`REPRODUCTION_PLAN.zh-CN.md`；上游 checkout 和正式权重放在工程外缓存。
- 实测回执和历史失败：`records/algorithm_adapters/<method>/`；本次分支集成回归：`records/algorithm_adapters/publication_20261028/`。
- 注册表保持远端的正式六列顺序和信息权利/预算验收规则。SADCHER 的正式列名是 `SADCHER`，旧 `SADCHER-adapted-to-EAI` 条目作为历史适配条件保留。旧条件信息保存在 `historical_condition_registration`，不能用当前原生实现解释旧回执。
- RobotFleet、LaMMA-P 和旧 Fire Rescue facade 仅保留为历史诊断兼容代码，不进入这六列原算法比较；HeteroMRTA 不在本轮。

## 算法边界与已取得的证据

| 方法 | 当前原生适配 | 已有执行证据与限制 |
|---|---|---|
| FMC-ATA | 原版分配与完整 schedule，经适配器按机器人队首消费；源码不改 | 双 UR5 2/2 有界任务完成；单任务收尾等限制仍 xfail，不支持自动重新竞拍、分数任务物理执行和原生反馈重规划 |
| BT capabilities | 原生 ROS 竞拍、Parallel/MemorySequence、DAG 就绪批次、执行反馈 | 双 UR5 三任务含依赖汇合完成；完整 MissionControl 和论文全条件未验收 |
| SADCHER | 官方完整 benchmark checkpoint；原生 scheduler/matcher/filter | 三机器人三任务二维原生闭环；UR5 输入可能原生 idle；未完成 Isaac 操作和同任务 coalition 执行 |
| HARL:HAPPO | 原始 actor/trainer/runner，严格匹配观测、动作、机器人及训练契约 | 有本地符号训练/holdout 证据；所附小型 `.pt` 仅是自训练 smoke 权重，不是官方权重；UR5 任务契约不匹配 |
| TeamWeaver | 原 HRCS + 原 LLMPlanner；临时副本中两处 hash 锁定语法修复；输出格式约束独立记录 | 原生分配器两机器人四次逻辑执行；真实 LLM 单批语义链两任务完成；多轮语义持久状态和 coalition 未验收，失败推理也保留 |
| EMOS | 原生讨论/反思，显式结构化输出条件，保留原始响应及 hash | 有界单 UR5 执行；原生 stage-two 全流程和完整论文比较未验收 |

以上执行证据来自此前运行时记录的源码版本，**本次集成没有重跑 Isaac 物理实验或付费 LLM 推理**。本次测试结果不能替代旧回执的版本身份，也不代表整套论文 Benchmark 已完成。

## 合并处理

保留远端 EMOS 通用任务接口、room06 相关变更、注册表校验和当前执行器。共享代码的修改限于接入所需的合并与兼容修正：

1. 保留正式注册表 API，补充原生来源和适配状态，继续将正式比较条件设为 pending。
2. 保留 EMOS 既有接口与测试；新增原生入口和方法专项测试。
3. BT 宿主测试使用本目录内的测试后端，不再依赖旧分支已删除的测试模块。
4. 补入物理宿主依赖的 `unified_pick_place_smoke.py` 和 `pick_place_clearance.py`；单臂入口只接受当前 arm backend 支持的 `cartesian_pose`。

<a id="bounded-host"></a>
### 有界仿真宿主

用户已授权本轮算法接入及理想化仿真测试。上述独立验证宿主显式设置当前执行器要求的 `physical_object_experiment_authorization`，只用于已登记的 follow-proxy 有界仿真验证。普通 Benchmark 执行器仍按远端默认逻辑对象模式运行，未更改默认模式。此配置不意味着接触抓取、真实机器人执行或完整物理验收；集成后的宿主只通过 CPU 契约回归，物理复跑待需要时另行记录。

## 本次集成回归

结果来自此发布工作树的 XML；各测试集合有交叉，**不得相加当作独立用例数**。

| 集合 | 结果 | XML |
|---|---|---|
| TeamWeaver + 共享接口/宿主 | 331 passed, 3 skipped（当时解释器无 greenlet） | `shared_teamweaver.xml` |
| SADCHER 专项 | 60 passed | `sadcher.xml` |
| HARL 专项 | 69 passed | `harl.xml` |
| FMC-ATA 专项 | 65 passed, 3 xfailed | `fmc_ata.xml` |
| EMOS 原生专项 | 97 passed | `emos.xml` |
| BT + 宿主 + 抓放边界，含真实 ROS endpoint | 112 passed，未跳过 | `bt_host_live.xml` |
| 最终注册表/正式矩阵/既有 EMOS/TeamWeaver 接口 | 56 passed | `merged_registry.xml` |

三个 greenlet 用例已在最后的 Isaac Python 宿主测试集合执行通过。BT ROS 端点复用本机现有容器，端点脚本与本次发布副本一致，原 ROS 算法未改；此轮 ROS 测试是协议反馈，不是物理运行。

FMC 历史记录包含超时和失败尝试。本次独立环境复跑为 65 passed、3 xfailed；既不删除历史失败，也不把有限测试提升为全输入可靠性结论。原始 XML 的 `skipped` 对应 pytest xfail 时，请结合 `.log` 与 testcase 原因读取。

复现测试按方法使用隔离解释器，统一设置 `PYTHONPATH=.:source/EAI`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`；多目录同名测试加 `--import-mode=importlib`。SADCHER 使用 `CUDA_VISIBLE_DEVICES=''`。具体测试集合、解释器和 BT 端点条件见本目录 `records/algorithm_adapters/publication_20261028/run_regression.sh`（从项目根目录运行）。

发布目录保留原始 JSON、XML、日志和轨迹，包括失败证据；仅排除 Python 缓存。`artifact_manifest.json` 提供本次发布算法文件及记录的 SHA-256。未提交 API 密钥、外部上游源码包、大型官方权重、场景资产或录像。
