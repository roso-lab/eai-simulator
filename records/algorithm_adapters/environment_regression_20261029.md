# 算法专项回归环境记录

日期：2026-10-29

本记录区分代码结果与解释器/可选依赖结果，不把环境缺失计为算法语义失败。

| 算法 | 解释器 | 结果 | 备注 |
|---|---|---:|---|
| FMC-ATA | `/home/airs/.cache/heterocoord/envs/fmc-ata/bin/python` | 66 passed, 2 xfailed | xfail 为已登记上游限制 |
| BT capabilities | 系统 Python 3.10 | 61 passed, 7 skipped | ROS live 条件按环境跳过 |
| HARL:HAPPO | `/home/airs/.cache/heterocoord/envs/harl/bin/python` | 69 passed | 专用环境 |
| EMOS | `/home/airs/.cache/heterocoord/envs/benchmark-py310/bin/python` | 98 passed | 新建 Python 3.10 隔离环境，关闭外部 pytest 插件自动加载 |
| TeamWeaver | `/home/airs/.cache/heterocoord/envs/benchmark-py310/bin/python` | 234 passed, 2 skipped | 2 项仅 Gurobi MIQP 路径按缺少可选 `gurobipy` 跳过 |

旧 TeamWeaver/EMOS Python 3.9 环境不能直接承载当前工程的 `dataclass(slots=True)`，不作为当前代码回归证据。TeamWeaver 的 Hungarian fallback 仍保留；没有 Gurobi 时不能声称 MIQP 路径已验证。
