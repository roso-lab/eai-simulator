# Cursor 接力集成与评分接入：2026-09-23

## 已合入的工作

主仓开发线为 `codex/benchmark-pose-telemetry`，分批推送到 GitLab
`benchmark-task`。餐厅与 EMOS、按需视觉两个工作树的既有提交已等价合入；
没有用旧分支整树覆盖主仓。用户 `my_env.json` 和评分协议原文件未纳入提交。

| 提交 | 内容 |
| --- | --- |
| `7a33fa4`、`b7127dd`、`73334c2` | 可选按需视觉、通信计量、可信GoalSession/episode成员加入 |
| `9bc4b7b`、`d44a671` | 餐厅relay/能力映射、EMOS明确yellow前缀，避免误伤仓库 |
| `4c929f6`、`fd54e5c` | 每回合raw-only评分侧车、归档索引、工具收尾异常 |
| `91d3ced`、`1b07a7b` | EMOS原生root-state接线、逐机器人来源证据；TeamWeaver未消费位置时不伪称采样 |
| `19eee7e` | 用户最新自分解主线与checkpoint需求映射，修正旧固定子任务Track-A建议 |
| `1687a49` | 首次close异常不因重试而消失、render清理未完成报错、缺失事件数不记为零 |

## 评分路线与阶段依赖

最新优先级更新：在以下后续评分/分支工程之前，先实现用户指定的
[部署可行性确认主线P0](DEPLOYMENT_ASSESSMENT_P0.zh-CN.md)。该任务尚未实现，
包括初始/变化后评估、求助终止、五场景团队不可行实例及隐藏可行性校验。

用户已确定：提供总体目标和可验证里程碑，模型自主分解、选团队、指派和排序。
冻结评分参数不能等同冻结作者策略。原固定子任务公式暂不适用，应调整评分定义；
不能把见证计划或时序关系发给参与者来迁就公式。

1. 当前已接：原始动作/事件/技能收据/业务结果/工具账本索引、哈希、失败记录。
2. 当前已设计：[Checkpoint合同](SCORING_CHECKPOINT_DESIGN.zh-CN.md)，按评分指标
   预注册处理前保存点、必要状态、分支对照与恢复验收；元数据为`planned_not_captured`。
3. 下一阶段：状态生产器与save/restore接口、宿主/通信队列/技能进度、参与者可写状态、
   环境与随机流；先通过无处理sham一致性，再建设并行分支调度。
4. 再后：逐场景真实时钟与里程碑进展、删失/NA、分方向通信成本、六维统计。

`scoreable=false`、执行release gate均未提升。原生业务证据、资产验证、完整checkpoint、
并发和因果评分分别验收，不能用一项成功替代其他项。

## 本轮验证与未通过项

本机评分/runner/工具专项249项通过；后续新增生命周期与餐厅测试87项通过。
最终本机完整`source/EAI/test/test_benchmark_*.py`：**2155 passed，230.41秒**；
日志`/tmp/eai-benchmark-final-1687a49.log`。
独立审核餐厅+external adapter25项通过；5项清理异常负向测试通过。
SSH ljy在最终代码`1687a49`上的专项：280 passed、1 skipped。

ljy隔离工作树位于 `/home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e`。
其主仓未提交的 `goal_house_episode.py` 保留；通过Git bundle同步代码，未配置或复制凭据。
完整回归最初2041 passed/70 failed/19 errors/18 skipped；配置已有USD根目录和地图后
2106 passed/17 failed/7 errors/18 skipped；补齐运行时锚点和场景入口后，重跑失败集合
19 passed/5 failed。剩余5项均有明确部署输入缺口：City车辆运行资产、Factory两份
LFS文件未展开、NVD产品验证禁止跨工作树symlink逃逸。保持路径/哈希校验，不改测试掩盖。
远端日志：`/tmp/eai-scoring-19eee7e-tests.log`、`/tmp/eai-scoring-19eee7e-retest.log`、
`/tmp/eai-scoring-1687a49-focused.log`。

餐厅原生启动尝试未进入episode：

- v1使用冻结products-candidate-v7，但overlay引用`/home/eai-simulator/...`不存在，composition检查失败。
- v2用当前source路径重新生成runtime产品，生成/几何校验通过，但resolver拒绝新metadata哈希。
  不能把重建产品直接当作已冻结版本，也不能放宽resolver。
- 输出目录：`tmp/restaurant_benchmark/algo-episode-emos-integration-20260923-v1`与`v2`；
  重建目录：`tmp/restaurant_benchmark/products-integration-20260923`。

下一项环境工作应是版本化、可迁移的资产部署/manifest，不能靠替换题目或放宽执行规则
获得成功。原生相机RTX生命周期仍需独立运行；本轮未声称完成。

## 反思

原始证据是否存在、是否有完整恢复状态、是否具有评分语义是三个不同问题。
本轮审核发现“构造provider但未采样”和“首次清理失败被重试抹除”都能让表面结果
显得正常，已用负向测试修复。多机回归则证明只有代码提交不足以复现实验，资产版本、
运行路径和完整状态必须成为后续checkpoint/部署合同的一部分。
