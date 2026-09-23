# 阶段 26 收尾交接：通信分析与 USD 兼容性

基线已推进到 GitLab `benchmark-task` 的 `14fefdd`。本阶段不改变冻结题包、见证计划、`reach_m` 或任何发布标志；`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false` 继续保持。

## 已完成

- 通信容量曲线分析、C90/C50 包围区间、绝对 bit/s 与 `C/C0`、配对差异和 bootstrap CI 已进入主线。
- 云边端三池、AoI、位姿遥测、编键随机流、部署 oracle、checkpoint/sham、原始成本和计划准入诊断均保留在同一提交链上。
- `tools/assets/relocate_usd_closure.py` 在原子替换后强制 `Reload(True)`，修复 ljy OpenUSD 0.24.11 的粗粒度 mtime 缓存问题；不放宽哈希或 overlay 校验。

## 验证证据

在 `/home/pupil/miniconda3/envs/env_isaaclab` 中使用
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`（避免 ROS `launch_testing` 缺失 `lark` 干扰）运行：

```text
通信/位姿测试：142 passed
remaining oracle、deployment、checkpoint、scoring、native pose：77 passed
编键随机：3 passed
USD closure：9 passed
```

完整证据见：
`/home/pupil/Documents/Codex/agent-team/workspaces/benchmark-stage22-comm-contract-20260924/handoffs/stage26-local-runner.md`
和 `stage26-usd-compat.md`。

## 未完成与下一阶段

- 尚未启动 Isaac/GPU 正式 episode；不能据此宣称五场景发布就绪。
- ljy 隔离工作树的完整静态验证须在同步 `14fefdd` 后复跑；原 ljy 工作区保持不动。
- 下一优先级：五场景 remaining-task oracle 完备性、checkpoint restore/replay、E2 机间服务与多端点适配器接口评审，再接入评分侧车的通信中介指标和 R/C sham 实验。
