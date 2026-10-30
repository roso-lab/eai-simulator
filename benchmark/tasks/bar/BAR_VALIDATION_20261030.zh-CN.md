# Bar 逻辑轨道验收（2026-10-30）

代码冻结：03b018f1（基于新版抓放接口 4959e104）；论文审阅：0ffd470f。

- 29 项 Bar 专项测试通过，覆盖四级默认任务、地图 digest、楼梯能力、双参与者支持、服务全过程、能耗/回充、容量排队、到点及协调状态通信阻塞、持久单功能损失、真实资源队列快照、新算法仅初始化一次、算法私有状态轮转、跨条件恢复拒绝、P2 端点/归属证据、含无臂 M20 的可解调度。
- 104 项已有场景/共享组件/skill 回归通过。city01 主工作区额外 9 项逻辑模拟器和 evaluator 测试也通过。
- 最终矩阵采用固定 seeds 17/18/19、两个明确标为 diagnostic witness 的兼容适配器、原设计允许的三机 variant、T1–T4 及六种条件/通信组合，共 504 行。T1/T2 不施加电量条件；G1/lite3 不进入 T4。
- 504/504 success，504/504 deadline_met，504/504 checkpoint_replay_equivalent；逐文件 SHA-256 与运行输入一致，压缩证据恰有 504 条记录。

运行：

```bash
PYTHONPATH=. /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m benchmark.run_bar_matrix --seeds 17,18,19 --workers 8
```

输出：`benchmark/results/bar_logic_matrix.v1.json`、同名 CSV、`bar_logic_matrix.v1.evidence.jsonl.gz`。汇总行保存条件、seed、输入契约摘要、通信容量、距离、耗电、补能、等待、丢包/延迟、故障、obligation 与资格状态；证据文件包含原始事件、共享对象会话、库存身份、联合样本、服务 RLE、资源终态及 checkpoint 摘要。运行 API 返回真实 checkpoint snapshot，亦可调用 runtime.checkpoint(path, metadata=...) 持久化；摘要不能替代外部正式 checkpoint 文件/readback 认证。

两个兼容类均为同一个确定性参考策略，标记 native_algorithm=false。全通过仅是逻辑可解性运行证据，不是原生 EMOS/TeamWeaver 的得分，不应解读为题目容易或算法满分。

原任务故事、订单数、语义目标、顺序交付、楼梯入口双参与者同步动作、服务和资源关系保持。初始电量/连续耗电率/同步窗口/服务容差等原文未数值化参数仅登记逻辑候选；四种不可行任务的正式独立证书、完整首计划、八维需求数值审计、独立时间/通信校准及原生控制器证据仍待后续资格流程。正式 D1–D6、rankable、physical_qualified、paper_score_eligible 均未升级。
