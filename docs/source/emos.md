# EMOS

EMOS（Emergency Multi-robot Operation System）提供**与具体业务无关**的多智能体 LLM 讨论与子任务分配能力：给定任务故事、子任务定义与机器人能力描述，它通过多轮讨论把子任务分配给异构机器人，并在 LLM 不可用或解析失败时回退到启发式分配。

## 定位

- **输入**：调用方以数据结构提供 `EMOSScenarioConfig`（场景叙事、子任务、位置规则、回退策略）与 `EMOSRobotAgentSpec` 机器人档案，以及一个 Isaac Lab 兼容的 `base_env`。
- **输出**：子任务到机器人的分配结果，交由执行层（导航、机械臂等）落地。
- **边界**：EMOS 不构建仿真场景，不内置任何场景内容，也不解读子任务的业务含义。

## 核心流程

```text
EMOSScenarioConfig（任务故事 + 子任务 + 位置规则）
        +
EMOSRobotAgentSpec（机器人档案） + base_env（机器人位置）
        ↓
EMOSDiscussionManager 多轮讨论调度
        ↓
LLM 输出解析 → 子任务分配
        ↓（LLM 不可用 / 解析失败）
preferred_fallback 启发式回退分配
```

`build_from_agent_specs()` 会从 `base_env` 的 articulation / rigid-object 状态读取机器人位置，供讨论与分配使用。

## 在 EAI 中的使用

[Fire Rescue 实验](getting_started.md)演示了 EMOS 与 EAI 的完整集成：工厂火灾巡检场景由 EMOS 完成讨论与任务分配，再由导航与执行层执行。代码、完整配置说明与依赖见 `algorithm/emos/`。

## 来源

算法源码仓库：[EMOS](https://github.com/SgtVincent/EMOS)；EAI 内置副本位于 `algorithm/emos/`。
