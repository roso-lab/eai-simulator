# HeteroCoord 工程入口

HeteroCoord 基于 EAI Simulator 的 core、assets 和 HMRS 能力搭建任务测评层。
`demo/` 和仓库内 USD 资产不属于本工程源代码；场景资源由 EAI 的资产解析器
从配置的 provider 或本地资产根目录提供。

## 目录边界

```text
simulator.py       EAI 基础仿真入口；必须显式指定 --env
source/EAI/        EAI 核心环境、控制器合同、ROS/interface 边界
source/EAI_assets/ 机器人、场景和传感器配置及资产解析器
source/EAI_hmrs/   saved environment 和动态环境构建器
algorithm/         算法本体与 EAI adapter；不创建 Isaac App
task/              可复用的任务图、机器人能力和任务运行合同
benchmark/         论文实验协议、tick runtime、证据账本和分支合同
tools/             安装、验证和 ROS 运维工具
```

`source/EAI_env_diy` 3D Kit 扩展已移除。EAI 内部的 `env_diy` Python 模块只
保留为 saved environment 的数据解析兼容层，不再提供 UI、插件或无参数启动
路径。

开发交接以 [`task/HANDOFF.zh-CN.md`](task/HANDOFF.zh-CN.md) 为准；算法状态和
来源以 `algorithm/heterocoord/registry.py` 为准。

## 运行方式

基础 EAI 场景必须显式选择已有 JSON 环境：

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python simulator.py --env EAI-Factory-Z1-v0 --headless
```

任务层的无仿真合同检查：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m pytest -q task/tests
```

任务运行器不会把算法分配当作物理成功。没有注入 EAI `SkillExecutor` 或独立
evaluator 时，结果必须是 `N/A`，原因是 `physical_evidence_required`。

## 开发顺序

1. 在 `task/config/` 注册任务合同和观察边界，不放 USD 或控制器逻辑。
2. 在 `algorithm/system/` 实现论文系统算法，输出任务决策；EMOS 和 TeamWeaver
   的实际实现位于 `algorithm/system/coordination/`，不保留顶层兼容副本；
   底层导航/控制算法统一登记到 `algorithm/skills/`，输出 `SkillPlan`。
3. 在 `benchmark/adapters.py` 冻结 `Assignment -> SkillRequest -> SkillPlan`
   和 evaluator/replay 的适配边界；具体 EAI 调用由 benchmark runtime 的适配器实现。
4. 使用 `benchmark/eai_adapter.py` 将 `SkillPlan` 映射到公开的
   `SimulatorSession.base_env.set_command(...)` 与 `env.step(...)`；adapter 只返回
   `ExecutionFeedback`，不判断物理成功。
5. 使用 `benchmark/p2.py` 从外部姿态、目标和 receipt 评估
   precedence/release/handoff/accept；缺证据分别保持 `N/A` 或 `unknown`。
6. 使用 `benchmark/replay.py` 组织 fact/reference/sham/intervention；只有共享
   checkpoint、`causal_replay` fidelity 和 sham calibration 同时通过时才允许
   strict-paired claim，restore artifact 始终保留。
7. 使用 `benchmark/replay_backend.py` 把 `tools/physics_replay` 的恢复报告接入
   上述协议；默认只声明 `physical_state_only`，必须显式提供 fidelity provider
   才能申请 `causal_replay`。
8. 使用 `benchmark/artifacts.py` 将每个 branch 的 evaluator report、observation
   和 hash-chained ledger 原子写入独立 JSON；已有 artifact 不覆盖，digest 或链不一致时读入失败。
9. 使用 `benchmark/branch_execution.py` 执行四类 branch。单 session 采用顺序
   scheduler；parallel 模式必须为每个 branch 创建独立 coordinator/session，再由
   worker 调用 `run_session_episode`，不能在线程间共享 Isaac 状态。
10. 使用 `benchmark/task_catalog.py` 加载并校验
    [`benchmark/task_catalog.json`](benchmark/task_catalog.json)：三域各 10 个
    anchor、完整 relation signature、P3-J/P3-S、difficulty tier 和 common-anchor
    语义必须先通过校验，再进入仿真。
11. 使用 `benchmark/experiment_plan.py` 展开 Phase 0--7 的 EMOS 矩阵；
    [`benchmark/metrics.py`](benchmark/metrics.py) 冻结 D1--D6 的分母、risk set、
    evidence 和 N/A 规则。没有认证 common pair、causal replay 或外部物理证据时，
    结果保持 `N/A`/`pending`，不能写成零分。
12. 使用 [`benchmark/release.py`](benchmark/release.py) 汇总 branch artifact 和
    endpoint observation。release index 只做身份、支持和缺失性审计，统计区间与
    论文最终推断仍在后续统计阶段完成。
