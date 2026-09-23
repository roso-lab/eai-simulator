# 阶段 22 交接：通信契约、评分证据与总体优先级

日期：2026-09-24  
基线：`codex/benchmark-pose-telemetry` / `9b59fe3`，已推送 `origin/benchmark-task`

## 本阶段完成

- 完成云—边—端通信接线设计，端点命名为 `cloud`、`edge:<id>`、`robot:<id>`，并
  冻结 backhaul 上/下行、p2p 和单机 access 的责任边界。
- 采用“可选 companion v1 或新 Goal v4”二选一的版本化边界。用户拍板前不实现
  participant p2p publish/consume，不静默修改 Goal v2/v3 action/event。
- 修复通信账本方向证据：显式 publication direction 会传播到 served/drop/expiry
  记录；旧记录缺方向时，raw cost 仍可保留总字节和池字节，方向分解为 `null`。
- 13 项通信池、回放和成本侧车专项测试通过。

## 当前 P0 顺序

1. 五场景 native remaining-task oracle：先绑定可信状态、硬终局谓词和合法替代
   路径，再进入部署判断正式分母。`oracle_status=failed` 仍是平台缺口。
2. 通信契约拍板后，实现 GoalSession companion service、统一 transport replay，
   再做本机和 ljy 的固定延迟/抖动、IID/BP、C90/C50 回归。
3. checkpoint/save-restore 与无处理 sham：覆盖环境、参与者、可见历史、通信队列、
   时钟和 RNG；sham 未通过前不启动正式 fact/reference/intervention 或 R/C 统计。

餐厅 A2 source closure 可独立运行，但必须分别在本机和 ljy 隔离树记录 source
SHA256、冻结 resolver 输入和 episode 启动前 preflight。不能放宽 overlay/hash，不能
用重建产品替换冻结 v7。

## 评分细则接入状态

当前可归档 accepted action、host receipt clock、声明为 proxy 的 activation cost、
经过 replay 校验的通信字节和 AoI。真实物理时钟、规划/重规划耗时、通知送达延迟、
未接线 p2p 流量和消息对决策的因果作用继续为 `null`。计划拒绝率、资源冲突率和
修订存活率用于事后任务分配画像；不得用固定子任务漏分配判罚模型。

## 未完成与发布门禁

- 当前 Goal episode 尚未统一接入机器人→云、云→机器人和机器人↔机器人真实通信。
- 五场景 native oracle 尚未全部达到 `verified`。
- checkpoint/sham 尚未实现真实保存恢复。
- 餐厅 A2 双机资产证据尚未完成。

全程保持：

```yaml
scoreable: false
execution_validated: false
business_outcomes_validated: false
```

用户文件 `source/EAI_hmrs/EAI_hmrs/envs/my_env.json` 和根目录协议文档未提交。
