# HeteroCoord 算法复现与 EAI 适配计划

## 复现边界

最新 PDF 是匿名 CVPR 评审稿，Table 3/4 的数值仍为 `xxx` 占位符。因此当前工作冻结算法身份、源码版本和测评接口，不虚构论文数值复现。Isaac 侧仍遵循 Isaac Sim 5.1 / Isaac Lab 2.x；算法只能提交规划、分配、通信或重规划事件，EAI runtime/evaluator 才能提交物理 milestone 和终止证据。

## 优先执行顺序

1. `FMC-ATA-adapted-to-EAI`：官方 Java FisherMarketSimulator 已固定；使用 `algorithm/system/coordination/fmc_ata/` 保留可分割 workload、市场价格、异步消息、延迟和丢包。接入 Fire Rescue 前必须明确任务是否可分割。
2. `BT-capabilities-adapted-to-EAI`：官方 `ros_bt_py` v1.1.0 已固定；使用 `algorithm/system/coordination/bt_capabilities/` 保留 capability interface、utility/precondition、remote slot、auction 和 member failure reauction。ROS1/catkin 不导入 Isaac 进程。
3. `COHERENT-adapted-to-EAI`：复用 `algorithm/system/coordination/coherent/` 和官方 commit `17554a5`，只报告 assignment-level PEFA；原 OmniGibson serial physical loop 不在 EAI 中重建。
4. `EMOS-adapted-to-EAI` 与 `TeamWeaver-adapted-to-EAI`：复用仓库已有适配，保持现有模型/API、Gurobi、任务 DAG 和回放合同；不把它们改写成 HeteroCoord 主表中的其他算法。
5. `HARL-HAPPO`：只固定官方 HARL 源码版本用于 provenance；当前源码树不存在 `algorithm/harl_isaac/`，也没有可核验的官方 HAPPO weights，因此 EAI adapter 与官方权重评测均为 `N/A`。未来新增适配或 Isaac 自训练结果必须分别注册新 condition，不能沿用官方权重身份。
6. `MRTA heuristic`：使用官方 ICRA 2024/IEEE T-RO 2025 源码等价 Python 适配，先完成异构能力/距离代价分配，再记录 `planRepair` 等价的延迟修复；可在 Z1 Fire Rescue 中运行。
7. `Hybrid SUSD MRTA`：使用官方 ICRA 2023 的 market 初始化 + SUSD 局部搜索等价适配；可在 Z1 Fire Rescue 中运行，论文原生故障重规划记为 `N/A`，适配器级重分配单独记录。
8. `DCMRTA` 与 `HeteroMRTA`：保留官方 checkpoint 的原生测试集 decision-only 轨道；Fire Rescue 使用默认分配跑完整 Z1 流程，跨域 checkpoint 泛化记为 `N/A`。

## 不进入复现结果的候选

LiP-LLM、Robust Online Epistemic Replanning、Qrainbow 与 SADCHER 当前不在
可运行算法目录中：本分支不把纸面推导或接口 fixture 当作复现。SADCHER 当前
没有可核验的官方代码登记（Gosrich 已退出主列，亦无可核验官方代码）；
Qrainbow 没有可核验的官方代码；LiP-LLM 没有作者代码；Epistemic Replanning
虽然有公开研究仓库，但尚未完成与本 EAI 版本的源码级接入，因此也不放入本轮
运行矩阵。它们只在 `algorithm/heterocoord/registry.py` 保留来源和 `N/A`
记录，不能用自写替代算法填入原论文结果。

## 当前完成与剩余 TODO

- 已将 FMC-ATA、BT capabilities、COHERENT、MRTA heuristic 和 Hybrid SUSD 接到
  `algorithm/heterocoord/fire_rescue_bridge.py`。bridge 统一记录 typed plan、实际派遣、
  `ExecutionFeedback`、evaluator `MilestoneEvidence`、事件账本和 `RunManifest`。
- FMC-ATA 在现有 Fire Rescue 红/绿/蓝/黄色任务上仍将“FMC-ATA 分配结果”标记为
  `N/A`：这些是不可拆分的物理技能，不能把 fractional market share 伪装成一台机器人
  可执行的任务。为了测试完整 EAI 链路，bridge 使用场景声明的默认分配，账本写入
  `allocation_provider=fixed_default_assignment`；只有新任务明确提供 workload
  decomposition 时才运行 FMC fractional 分配。
- BT capabilities 和 COHERENT 分别通过
  `algorithm.system.coordination.bt_capabilities.fire_rescue_policy:assign`、
  `algorithm.system.coordination.coherent.fire_rescue_policy:assign` 接入现有无状态 assignment callback；
  EAI 仍独占导航、机械臂、物理谓词、回放和终止判断。
- Fire Rescue CLI 可用 `--coordination-ledger-dir DIR` 保存每个 trial 的
  `coordination_run.json`。算法分配成功不会自动写成 mission success，只有 evaluator 的
  `mission_success` 证据才能验证物理成功。
- MRTA heuristic 和 Hybrid SUSD 曾针对一个本地 Z1 场景完成短 Isaac smoke；这只是
  scenario-bound 历史诊断，不能证明当前源码、当前资产或其他场景的 published runtime
  readiness。对应输入不是正式 `task/config/` 任务。未来若重跑，场景输入只能作为 `tmp/`
  下的临时诊断材料，并须固定 runtime/plugin identity、资产 revision、机器人身份、seed、
  动作 schema、外部依赖 provenance 和回放分支；计划/派遣成功仍不能升级为物理成功。
- DCMRTA 已加入双轨条件：官方 checkpoint 只在原生测试集上计入 decision-only；Fire
  Rescue 轨道使用默认分配完成 Z1 执行/反馈/回放，跨域 checkpoint 泛化记录为 `N/A`。
- HeteroMRTA（IEEE RA-L 2025）已加入同样的双轨条件：官方 checkpoint 原生输入为
  3–5 种机器人、每种 3 台、15–50 个任务；Fire Rescue 轨道使用默认分配完成
  Z1 执行/反馈/回放，跨域 checkpoint 泛化记录为 `N/A`。
