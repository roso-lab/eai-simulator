# 主线与 ljy 协作者整合记录（2026-10-28）

## 输入与整合原则

- GitLab 最新基线：`origin/HeteroCoord@1b81f702`。
- ljy 的语义地图/skill 工具链已在基线合并，保留其完整的 Isaac/Web 双端编辑、框选、多选、删除键、导航绑定保护和当前存储链。
- D5 主线设计来自会话 `codex://threads/01a17d47-d36e-7161-a218-a115dc1ea47f`；只移植该会话明确产生的工程边界，未把主工作树中其他会话的脏产物混入。
- 主工作树未执行 pull、reset、clean 或覆盖；整合在独立 worktree 完成。

## 已整合的主线设计

1. `task/transport.py` 固化 `heterocoord.transport_command.v1`，从 `AlgorithmDecision` 生成语义搬运命令；质量/负载由场景物理 JSON 与机器人简历重新解析，超载在资源租约、attachment 和 backend 启动前返回 `false/0`，并可转换为 `ExecutionFeedback(INFEASIBLE_TEAM)`。
2. `PickPlaceExecutor` 与 `SkillTaskExecutor` 对齐理想化搬运语义：导航到语义点且准入通过即完成；旧几何/IK/工具轴/生命周期字段只保留为底层诊断。
3. 多机器人环境默认保留机器人与场景物体碰撞，仅过滤不同机器人实体之间的碰撞；宿主机器人及名称前缀匹配的挂载机械臂/传感器归为同一实体，无法归属的 articulation 保留为独立实体。
4. 统一被测系统规范新增搬运第二层协议与上述边界，同时保留远端已接通的 TeamWeaver 状态及两个待用户裁决项。

## 验证证据

| 范围 | 命令结果 |
|---|---|
| transport/runtime/runner | 57 passed |
| robot collision policy | 4 passed |
| EMOS/production/room06 evaluator | 31 passed |
| room06 input audit/task runtime/evaluator | 82 passed |
| topology authoring 与 Web/Isaac bridge | 107 passed（`env_isaaclab`） |
| compile/diff | `compileall` 与 `git diff --check` 通过 |

运行环境：语义地图专项使用 `/home/pupil/miniconda3/envs/env_isaaclab/bin/python`；该环境提供 `torch 2.7.0+cu128`、`lark 1.3.1`、`shapely 2.1.2`。默认 Python 缺 `shapely`，不作为代码失败处理。

## 尚未宣称完成的接缝

- `TransportAdmissionResolver` 尚需由正式场景 runtime 注入 object/robot resolver，并把结果接入生产 `ExecutionUpdate`/episode ledger。
- 具体 arm backend 的 `start()` 内仍可能保留物理几何路径；它们不得改变 D1/D5 理想化协作结果，需继续按适配器逐个降级为诊断。
- 语义地图工具测试通过不等于各场景地图已经输入闭合；正式资格门与认证 flags 继续保持 `false`。
