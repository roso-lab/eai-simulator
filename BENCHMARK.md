# EAI Simulator · Benchmark 工程入口

本仓库 fork 自 [eai-simulator](https://github.com/roso-lab/eai-simulator)，承载**异构多机 benchmark 的任务生成与审核**开发线，以及若干并行工作线（物理回放、机械臂物理准入、C01/rmuc2024 导航适配、城市演示）。

## 从这里开始

| 想了解 | 去看 |
| --- | --- |
| 交付状态（已完成 / 现在可用 / 待开发被阻断） | [demo/benchmark/DELIVERY.zh-CN.md](demo/benchmark/DELIVERY.zh-CN.md) ← **推荐入口** |
| 阶段 1–18 权威勾选记录 | [demo/benchmark/TODO.zh-CN.md](demo/benchmark/TODO.zh-CN.md) |
| benchmark 设计（题面/信息边界/四步审核） | [demo/benchmark/BENCHMARK_DESIGN.zh-CN.md](demo/benchmark/BENCHMARK_DESIGN.zh-CN.md)、[TASK_GENERATION_PROGRESS_REPORT.zh-CN.md](demo/benchmark/TASK_GENERATION_PROGRESS_REPORT.zh-CN.md) |
| 场景公共层模块文档 | [demo/benchmark/README.md](demo/benchmark/README.md) |
| 各场景入口 | `demo/{factory,warehouse,modern_house,city}_benchmark/README*.md` |

## 工程一图

```text
场景与业务目标 + 场景资产 + 机器人团队 + 通信/成员因子
        ↓  tools/scenario_generation（生成器）
  场景化任务骨架（demo/benchmark/authoring.py 审核）
        ↓  生成任务实例并完成审核（goal_suite audit）
  公开题面 + 参与者接口 + 隐藏评价条件（四层题包，hash 绑定）
        ↓  （被测 agent 团队决策——外部接入）
  运行时执行并记录证据（runtime/provenance/domain_adapters）
        ↓  （评价与评分——其他开发者负责）
```

任务生成侧（本仓库主线）负责第一到第三层；执行回放与评价模型由其他开发者维护。

## 快速验证

```bash
# 统一题包生成与读回（env_isaaclab，不启动 Isaac）
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite list

# 聚焦回归
conda run -n env_isaaclab env PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_constraints.py --tb=short
```

更多命令见 [DELIVERY.zh-CN.md §4](demo/benchmark/DELIVERY.zh-CN.md)。模拟器本体使用说明见仓库 [README](README.md) 与 [AGENTS.md](AGENTS.md)。

## 当前发布口径

所有题包 `release_status=pending`、`scoreable=false`（fail-closed 设计）。任务生成与审核本体已完成；三个外部 blocker（Carter 碰撞源资产、City 正式 episode 绑定、House 业务干预见证）与楼梯运动模型（待替换）不在此侧。详见 [DELIVERY.zh-CN.md §5–6](demo/benchmark/DELIVERY.zh-CN.md)。
