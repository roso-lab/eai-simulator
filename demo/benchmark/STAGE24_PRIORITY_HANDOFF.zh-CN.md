# Stage 24：评分证据绑定与并行收口

## 输入与冻结边界

- 基线：`cff39d9`，目标远端 `benchmark-task`；用户文件和根目录评测协议文档保持未提交。
- `scoreable`、`execution_validated`、`business_outcomes_validated` 继续为 `false`。
- 题包、见证计划、overlay/hash、`reach_m` 和 Goal 自主分解契约不变。
- 正式部署真值只有 `deployable` / `not_deployable`；oracle 证据失败归为平台质量故障。

## 本阶段优先级

1. **P0 部署可行性**：完成五场景 native state reader、剩余任务合法路径和预注册动态测量点的可信绑定；未认证前不得进入正式部署指标。
2. **P0 评分证据**：计划准入拒绝、部署门禁、通信字节/AoI 和 episode 身份必须在侧车中可追溯；缺证据保留 `null`，不得估算为零。
3. **P1 通信**：云—边—端三池的回放、方向和丢包分类保持 fail-closed；E2 参与者机间服务及多端点适配器先完成接口文档再实现。
4. **P1 checkpoint/sham**：保存点必须位于变化发生前；恢复、重放、编键 RNG 和 sham 证据未验证时不进入协议统计。
5. **P1 A2/双机**：使用真实 `env_isaaclab` 在本机和 ljy 隔离工作树复核资产准备与全量静态回归；资产/依赖故障单列，不计为模型失败。

## 本阶段已落盘修复

评分侧车的 raw cost evidence 现在支持传入 episode 身份：显式绑定的通信报告若属于其他 episode 会整体失效；同一通信账本以工具文件和通用文件重复归档时只计一次；方向缺失仍只使方向分解为 `null`，不影响已验证总字节/池字节。该修复不抬升任何发布标志，并有针对性回归测试。

## 待验收

- checkpoint validator 的 fail-closed 加固（独立工程任务）；
- native oracle 真实 reader/完备性证书；
- ljy `env_isaaclab` 全量回归和 A2 正式 episode 启动前准入；
- 所有通过后再分批正常推送，禁止 force push。

