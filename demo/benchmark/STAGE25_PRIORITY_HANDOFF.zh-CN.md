# 阶段 25：部署门禁、资产准入与评分接入收口

状态：**进行中；发布标志保持关闭**  
基线：`benchmark-task` / `70459d9`，工作分支：`codex/benchmark-pose-telemetry`

## 本阶段的排序

1. **P0 部署可行性门禁**：确认 `GoalEpisodeRunner` 在每个初始/预注册动态节点先取得隐藏剩余任务可达性 oracle。正式标签只有 `deployable` 和 `not_deployable`；reader、注册、绑定或完备性证据失败时只归档 `oracle_status=failed` 且 `feasibility_label=null`，该轮退出 IRR/FIR 分母。
2. **P0 餐厅资产准入**：在本机和 ljy 隔离工作树分别从冻结 v7 candidate 复现 source closure、哈希和 Goal preflight。路径布局错误、资产缺失和代码错误分别记账，不能放宽 overlay/hash，也不能抬升 episode 发布标志。
3. **P1 评分侧车接入**：保留 Goal 自主分解，侧车只汇总模型实际提交的 plan admission（接受/拒绝、能力/负载/资源冲突、修订存活）与已验证的部署门禁记录；不套用固定子任务 VAR/FSR/IRR，不把漏分配子任务作为单独判罚。
4. **P1 通信计量地基**：离线 `CommunicationRuntime` 已有 backhaul 上/下行共享池、p2p 共享池、单机 access 上限、固定延迟/序列化/排队、buffer/TTL/主动丢弃分类和 AoI。正式 Goal companion service 与 C90/C50、JP/IID/BP 配对实验仍未完成，当前只报告 raw evidence。
5. **P1 checkpoint/sham**：保留变化生效前保存点、编键随机流和 fail-closed sham 校验。原生物理状态/参与者恢复 provider 未实现前，不进入严格 fact/reference/sham 统计。
6. **P2 E2 接口设计**：先拍板云—边—端 endpoint、可选 companion contract v1、p2p participant service 和多端点适配器，再实现，不静默升级旧 Goal v2/v3。

## 评分与发布边界

- 评分侧车输出原始、episode-bound 的证据和 N/A/缺失原因，不计算六轴最终分数。
- `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false` 在本阶段及所有回归中保持不变。
- 任务包、见证计划、`reach_m`、overlay/hash 和用户文件不修改、不提交。
- 基础设施或资产失败单独归类为平台证据缺口，不能伪装成被测模型决策错误。

## 验收清单

- [ ] 五场景 native remaining-task reader、终局谓词完备性和全部预注册点达到 `oracle_status=verified`。
- [ ] 初始可行、初始不可行、运行中失效后可行/不可行三类门禁回归通过；求助终止不注入资源、不自动重试。
- [ ] 本机与 ljy 正确 staging 深度完成餐厅准备和 preflight；正式 episode 启动证据另行归档。
- [ ] 评分侧车与部署门禁、plan admission、raw cost、通信 replay 的 episode 绑定检查通过。
- [ ] 本机和 ljy 在同一提交运行静态全量回归；所有跳过项写明环境原因。
- [ ] 原生 checkpoint restore/replay、E2 participant service 和六轴正式统计继续保持未完成状态，不能以离线 fixture 替代。

## 阶段实证（2026-09-24）

- P0 oracle reader fail-closed：`4537001`、`c1de19c`；聚焦回归 29 passed。缺失、异常或
  非对象 readback 均归档为 `oracle_status=failed`、`feasibility_label=null`，不会把
  `unverified` 传给被测模型。
- 餐厅 A2（ljy 隔离树，`70459d9`）：正确 staging
  `/home/ljy/a/b/c/d/products-candidate-v7` 的哈希、source closure、resolver 和
  `runtime_probe.prepare_probe` 全部通过；餐厅静态回归 44 passed、8 skipped。受限
  native 启动因 ljy 无显示环境的 GLFW 初始化超时停止，所有执行/业务/scoreable 门仍关闭。
- 本机静态全量（`PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy`
  与 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）：`2952 passed`。Factory evaluator fixture 单测
  需要本机未纳入 Git 的外部 USD，其实际哈希与冻结 fixture 不同，作为资产版本缺口单列，
  未修改 fixture、资产或哈希门禁。
- ljy 隔离树同提交全量（排除上述外部 Factory fixture）：`2788 passed, 9 skipped, 1 failed`。
  唯一失败是 `test_usd_closure.py` 的 metrics layer relocation；只读对比确认 ljy
  OpenUSD `0.24.11`、本机 `0.26.8`，因此归类为运行时依赖差异，不能修改测试预期来掩盖。
