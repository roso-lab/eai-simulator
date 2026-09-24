# 通信容量诊断 v2：单位、参考与适用范围

阶段 36A 新增显式 opt-in 的容量诊断 v2；旧 API、旧归档和
`CommunicationRuntime` 均不改语义。v1 历史字段 `capacity_bps` 在 runtime
服务时间公式中实际按 **bytes/s** 使用，且实验函数把 `None` 替换为有限的
20,000 bytes/s。旧原始字节、队列和投递证据仍可按当时语义回放，但旧绝对容量、
C/C0 和“无限参考”不得按 v2 口径重标。

## v2 契约

- 实验边界只接受 `capacity_bits_per_s`，单位为 `bits_per_second`。
- `bits_per_second_to_runtime_bytes()` 是唯一换算点：runtime value = bit/s ÷ 8。
- 为兼容未改动的 runtime，换算后的 bytes/s 写入其历史字段
  `capacity_bps`；v2 observation 明确归档为
  `runtime_capacity_bytes_per_s`，不沿用歧义名称。
- unlimited reference 将 `None` 原样传入 runtime，序列化时长为零；不再以
  有限容量代替无限参考。
- 方向由 pool 唯一派生：`backhaul_uplink→uplink`、`p2p→p2p`；显式冲突值
  会被拒绝，避免污染方向归因。
- C0 只能使用 `c0_bits_per_s`；同一实验必须每行都有同一 C0，或全部不提供。
  旧 `capacity_bps`/`c0_bps`、任何 byte-unit C0、schema/method 混合均拒绝。
- 汇总器固定 metric 名称，并严格校验整数 version、pool 对应方向、正整数 wire
  size、有限非负序列化时长及其与 bit/s→bytes/s 换算的一致性；归档字段被篡改时
  不生成有效曲线。
- v2 observation、analysis 与 report 分别使用独立 schema/version，method 固定为
  `synthetic_link_delivery_capacity_v2`，不会与 v1 样本合并。

## 实际测量对象

容量曲线的 `synthetic_link_delivery_rate` 是固定合成消息流的
`delivered/published`。它验证链路排队、TTL 和容量机制，不是系统业务成功率，
也不是被测模型的通信适应能力分数。报告持续保持 evaluator-only、
`formal_statistics=false`、`execution_validated=false`、
`business_outcomes_validated=false`、`scoreable=false`。

v1 另外两个条件名称也只按历史身份保留：

- `jitter` 是均值两侧固定交替的确定性延迟诊断，不代表随机抖动分布；
- `iid_loss` 是每十条固定丢弃数、种子旋转位置的散布模式，不是 Bernoulli IID。

本阶段没有新增随机分布、容量阈值、C0 或正式评分参数。
容量 workload 是固定消息序列；`channel_seed` 仅作单元格身份标签，当前路径不从
seed 抽样，因此不同 seed 标签不是独立随机重复证据，不能据此生成能力置信区间。

报告的 development marker 使用 `transport_diagnostic` 作为非场景诊断身份；
这是 marker builder 的非空身份字段，不是新增题包 scene key，也不表示运行过 City
或任何正式场景。极低容量下消息即使因 TTL 过期，runtime 仍在入队时记录实际 wire
size 和计划序列化起止时间；v2 据此报告序列化时长，字段缺失或非有限值会明确拒绝，
不会把 `None` 隐式转换成数值。

## API

```python
from demo.benchmark.communication_capacity_v2 import (
    run_capacity_cell_v2,
    capacity_observations_v2,
    summarize_capacity_experiments_v2,
)
```

调用者只有显式导入并调用 v2 才进入新语义。旧
`communication_capacity_experiments.py` 的函数签名、默认值、schema v1 和归档
回放路径保持原样。
