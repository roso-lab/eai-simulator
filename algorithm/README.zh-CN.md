# 算法层与 Skill 层

`algorithm/` 明确分成两种职责，不能混用：

1. `algorithm/system/` 对论文中的系统算法做分类。它们负责任务分解、异构分配、
   团队协作、通信和重规划。
2. `algorithm/skills/` 对底层 provider 做分类。它们把已经分配的一个任务转换成
   路径、轨迹、controller 命令或 ROS 传输事件。

系统层回答“团队应该做什么”，Skill 层回答“单个机器人怎样执行已经分配的部分”。
EAI 负责 controller 合同和仿真推进，`task/executors/` 负责把 skill plan 接到 EAI。

## 规范结构

```text
algorithm/
├── system/
│   └── coordination/
│       ├── emos/             # 被测 EMOS 系统算法
│       ├── TeamWeaver/       # 被测 TeamWeaver 系统算法
│       ├── coherent/         # 被测 COHERENT 适配器
│       ├── fmc_ata/          # 被测 FMC-ATA 适配器
│       ├── dcmrta/           # 被测 DCMRTA 适配器
│       ├── heteromrta/       # 被测 HeteroMRTA 适配器
│       ├── hybrid_susd/      # 被测 Hybrid-SUSD 适配器
│       ├── mrta_heuristic/   # 被测 MRTA heuristic 适配器
│       ├── bt_capabilities/  # 被测能力拍卖适配器
│       └── registry.py       # 论文/系统适配分类
├── skills/
│   ├── contracts.py          # 无依赖 SkillRequest/SkillPlan
│   ├── registry.py           # 导航与外部 IO skill 清单
│   ├── navigation/           # 导航 provider 的上传位置
│   └── external_io/          # Nav2 和 keyboard provider 的上传位置
├── common_coordination/       # 无 Isaac 的任务/系统合同
└── heterocoord/               # provenance 和测评决策桥
```

EMOS 和 TeamWeaver 是被测系统算法本体，不是兼容副本。它们唯一支持的导入路径是
`algorithm.system.coordination.emos` 和
`algorithm.system.coordination.TeamWeaver`。导航、Nav2 和键盘 provider 当前从本分支
移除，等待专门的 Skill 开发者上传；`algorithm/skills/` 保留合同和 registry 槽位，
它们不属于论文级任务协同算法。

## 系统算法边界

系统适配器可以读取：

- `task.EaiTaskPackage` 及其任务图；
- 当前机器人能力表；
- 符号世界状态和执行反馈；
- 追加式协调账本。

输出是 `AlgorithmDecision`、任务 DAG、分配或重规划事件。系统算法不能创建 Isaac
应用、读取 PhysX/关节句柄、调用 EAI skill、输出 controller action tensor，也不能
写入物理成功。

系统清单在 `algorithm/system/coordination/registry.py`；论文来源和 checkpoint 状态
仍由 `algorithm/heterocoord/registry.py` 维护，并明确记录 `N/A` 条件。

## Skill 边界

Skill provider 在任务分配之后接收 `SkillRequest`，返回包含
`SkillCommand` 的 `SkillPlan`，可负责地图/路径规划、无碰轨迹生成、controller 命令
转换或 ROS/Nav2 传输。它们不能决定任务归属，也不能声称物理里程碑已经完成。

无依赖合同在 `algorithm/skills/contracts.py`；现有 provider 在
`algorithm/skills/registry.py` 中登记：

| Skill provider | 计划路径 | 职责 |
|---|---|---|
| global 2D navigation | `algorithm/skills/navigation/global_planner/` | 路径和速度生成 |
| db-CBS navigation | `algorithm/skills/navigation/multi_robot_navigation/` | 多机轨迹与碰撞规避 |
| Nav2 | `algorithm/skills/external_io/nav2/` | system-ROS 目标/传输 |
| keyboard | `algorithm/skills/external_io/keyboard/` | 手动速度传输 |
| EMOS 内部 skills | `algorithm/system/coordination/emos/brain/skills/` | EMOS 内部使用的 EAI/Torch runtime，不是单独系统算法 |

`source/EAI/EAI/controllers/` 仍是 controller/动作应用的唯一合同所有者。
`task/executors/` 将 `SkillPlan` 适配到该合同，`task/evaluators/` 负责物理证据。

## 决策到执行的数据流

```text
任务包 + 机器人能力 + 符号状态
                  |
                  v
              系统算法适配器
                  |
                  v
         AlgorithmDecision / Assignment
                  |
                  v
          task executor -> SkillRequest
                  |
                  v
            skill provider -> SkillPlan
                  |
                  v
         EAI controller 和仿真推进
                  |
                  v
       feedback -> evaluator -> 物理结论
```

```python
from algorithm.skills import SkillRequest
from task.contracts import EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner

package = EaiTaskPackage.from_json("task/config/fire_rescue.json")
result = TaskRunner().run(TaskRunRequest(package), decision_provider)
```

没有注入 EAI executor 或 evaluator 时，`TaskRunner` 固定记录
`N/A / physical_evidence_required`。算法包不能新增第二个 simulator launcher，也不
能存放 USD、下载的 checkpoint、运行账本或实验输出。
