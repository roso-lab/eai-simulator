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
task/              HeteroCoord 任务合同、编排、回放和证据边界
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
3. 在 `task/executors/` 把 `Assignment -> SkillRequest -> SkillPlan` 注入 EAI
   skill/controller 调用。
4. 在 `task/evaluators/` 读取物理证据并判定 milestone/terminal。
5. 在 `task/replay/` 接入 checkpoint 和并行分支工具；分支状态不能成为算法隐藏输入。
