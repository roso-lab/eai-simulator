# 论文原算法适配与复现

以用户提供的 PDF Table 3 / J.1 六列为准：FMC-ATA、BT capabilities、SADCHER、
HARL:HAPPO、TeamWeaver、EMOS。来源和固定版本见 `paper_algorithms.json`。
HeteroMRTA 不进入本轮。旧计划中的 Java FisherMarketSimulator、SciPy fallback、
固定分配和等价重写均不能用于这六列的原算法验收。

## 目录及启动

- 原始源码：`~/.cache/heterocoord/upstream/<method>`，固定 commit，禁止修改。
- 依赖环境：`~/.cache/heterocoord/envs/<method>`；ROS1 用独立容器。
- 输入输出适配：`algorithm/system/coordination/<method>/native_adapter.py`。
- 统一构造：`benchmark.native_algorithms.build_native_adapter`。
- 工具和可复用输入：`tools/algorithm_adapters/`。
- 实测记录、训练产物和报告：`records/algorithm_adapters/<method>/`。

```bash
cd /home/airs/hetrocoord
python3 -m tools.algorithm_adapters.setup_sources
# 使用所选方法的解释器；CONFIG 只含非敏感输入。
python3 -m tools.algorithm_adapters.run \
  --algorithm METHOD --task TASK.json --config CONFIG.json --report REPORT.json
```

CLI 是原生决策测试，默认不创建 Isaac，不给物理成功结论。正式 host 将同一 provider
交给 TaskRunner，绑定真实 executor 和独立 evaluator。执行反馈须按各方法契约回传；
无 replan 映射的方法不能获得适配器发明的重规划器。

## 方法边界

| 方法 | 必须执行的原组件 | 当前明确的输入/运行边界 |
|---|---|---|
| FMC-ATA | Python `solver_fmc_distributed_asy.FMC_ATA` | 原生异步市场及调度；静态、独立任务、单能力、单 owner；不取整 fractional 输出 |
| BT capabilities | ROS1 `ParallelAuctionManager`、`MemorySequence` | 显式 bids，真实 ROS 消息，线性链；完整 MissionControl 尚未验收 |
| SADCHER | `SadcherScheduler`、原生 matcher 和两层 filter | 三个显式二值技能、二维位置、持续时间、DAG；完整 checkpoint 审计 |
| HARL:HAPPO | 官方 HAPPO actor / runner / trainer | observation/action manifest 精确匹配；本地训练条件独立标识 |
| TeamWeaver | 原 HRCS MIQP；完整方法还需语义 planner | 已运行 supplied-decomposition allocation；完整 Habitat world graph/tool/LLM 链路单独验收 |
| EMOS | 原 stage-one discussion 与模型循环 | 原生 scene description/resume、明确输出词表、计量模型端点；脚本 completion 只计协议测试 |

SADCHER 显式兼容权重：
`~/.cache/heterocoord/upstream/sadcher/imitation_learning/checkpoints/hyperparam_2_8t3r3s/best_checkpoint.pt`。
默认旧 `model_0` 不兼容时直接拒绝，不能部分加载或静默替换。

## 验收顺序

1. 核对 commit、tracked 内容和导入遮蔽，记录源码与适配器 hash。
2. 测试合法/非法输入、原生输出及不能表达的输出，保留真实拒绝。
3. 在隔离环境执行原算法，并与直接原生输出比对。
4. 通过统一入口测试 TaskRunner 和实际反馈闭环。
5. 在支持的域绑定 Isaac 技能及外部 evaluator，保留物理收据。
6. 补齐完整方法组件和论文场景、重复、故障/通信条件后，才进入 Benchmark 统计。

详细结果以 `NATIVE_ALGORITHM_ADAPTATION_REPORT_20261028.zh-CN.md` 及实测收据为准。
原生决策、符号成功、有限物理执行和完整论文条件分别记录。缺失证据保持
`N/A` / `not_passed`，论文占位数值不填造。
