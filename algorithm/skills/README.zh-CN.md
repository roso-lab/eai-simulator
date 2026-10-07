# Skill 层算法

论文中的系统算法决定“哪个机器人做哪个任务”；Skill 层把已经分配的一个任务
转换为可执行的底层能力，例如路径、同步轨迹、Nav2 目标、抓取命令或速度命令。

因此，HeteroCoord 中的底层运动/控制算法属于 skill 能力，不应作为高层多机器人
协作算法的额外比较列。

## 现有 Skill 提供器

| Skill 分组 | Provider 上传路径 | 输出 | 运行时所有者 |
|---|---|---|---|
| 单机导航 | `algorithm/skills/navigation/global_planner/` | 路径和速度命令 | EAI 导航控制器 |
| 多机无碰导航 | `algorithm/skills/navigation/multi_robot_navigation/` | 同步轨迹 | EAI 仿真循环 |
| Nav2 传输 | `algorithm/skills/external_io/nav2/` | ROS goal / bridge 命令 | system ROS2 + EAI bridge |
| 手动速度输入 | `algorithm/skills/external_io/keyboard/` | ROS Twist | system ROS2 + EAI bridge |
| EMOS 内部 skills | `algorithm/system/coordination/emos/brain/skills/` | Torch command | EMOS 内部 runtime，不是独立的系统算法 |

这四个 provider 当前未包含在本分支，后续由专门的 Skill 开发者上传到上述路径。
机器可读清单位于 `algorithm/skills/registry.py`。新增 provider 尽量实现
`algorithm/skills/contracts.py` 中的无依赖 `SkillProvider` 合同。provider 可以计算
计划，但不能推进 Isaac、直接调用 controller，也不能伪造里程碑成功。

## 运行顺序

```text
系统 AlgorithmDecision / Assignment
                   |
                   v
             task executor 构造 SkillRequest
                   |
                   v
              skill provider 构造 SkillPlan
                   |
                   v
        EAI executor 将 SkillCommand 映射到 controller
                   |
                   v
       simulator 推进 -> feedback -> evaluator 物理证据
```

`source/EAI/EAI/controllers/` 仍是 controller 合同和动作应用的唯一所有者，不能
复制到 `algorithm/skills/`。`task/executors/` 负责把 skill plan 接入该合同。
