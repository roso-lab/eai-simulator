# 通信曲线离线分析

`communication.py` 负责传输事实和回放账本；`communication_analysis.py` 只消费已经
绑定到 episode 的结果，不改变容量档位，也不生成正式六轴分数。

## 容量曲线

调用 `summarize_capacity_curve(observations, metric=...)` 时，每条记录必须包含：

- `base_instance_id`：冻结布局、任务和环境随机带的实例身份；
- `channel_seed`：通信随机重复；
- `capacity_bps`：有限容量，或用 `null` 表示明确的完美通信参考；
- `metric_value` 或 `metrics[metric]`；
- 可选的冻结 `c0_bps` 和 `method_id`。

函数先在实例内平均 channel seed，再以 base instance 为 bootstrap 单位。输出同时保留
绝对 bit/s、`C/C0`、参考均值、95% bootstrap CI 和每个容量点的性能保持率。
`C90`/`C50` 只在已测试容量点上给出：相邻点跨过阈值时返回包围区间，测试范围内没有
达到阈值时返回 `above_tested_maximum`，不会外推单一数值。参考均值低于调用者提供的
预注册门槛时，阈值只返回 `reference_below_minimum`。

## 同均值分布对照

`summarize_paired_distribution_effect` 按 `base_instance_id`、`channel_seed` 和
`comparison_level` 配对 `fixed/jitter` 或 `iid/burst` 条件，先在实例内平均，再报告
`right-left` 的均值和 bootstrap CI。缺一侧配对时只报告 `missing_pair`，不把未配对样本
当作分布效应。

这些结果是通信适应的原始/诊断证据；真实 Goal episode 接线、participant p2p service
和 `scoreable` 晋升仍需独立门禁。

