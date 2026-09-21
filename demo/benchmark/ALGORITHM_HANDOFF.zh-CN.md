# 四场景任务生成、审核与算法接入交接

当前可交接范围是四场景任务契约、生成器、作者审核、冻结读回和高层算法协议。
Factory、Warehouse、Modern House、City 使用同一个 goal 主轨接口；多进程、多机器调度
由接手开发者实现。正式 Isaac 业务执行与统计发布仍须满足题包自己的 release gate。

## 测评变量

TeamWeaver、EMOS 和后续异构多机系统是被测对象。它们决定团队选择、任务分解、分配、
协同、通信和重规划。导航、移动、抓取的实现、参数和技能耗时由 benchmark 固定，
导航可采用简单 A*；本接口不向参与者提供选择全局规划器、db-CBS 或抓取算法的入口。
额外干扰必须经过显式 treatment、单因子审核及冻结，不能偷偷改变默认控制条件。

四场景共享 `execution_contract()`：条件确定性技能、固定耗时、明确可见范围和占用阻塞
反馈。异构能力、资源和业务前置条件仍须满足；理想技能不等于任何请求都成功。
`ReliableSkillReferenceHost` 已提供确定性的符号导航与操作效果，观察、交接和城市服务
需要可信后端回执。该参考宿主不是 Isaac 控制器，也不与 `GoalAlgorithmHost` 自动共享
session；运行时维护者必须连接单一账本和真实后端，不能并行维护两份互不一致的状态。
`benchmark_default_ideal` 是必须遵守的实验策略，不能用这个字符串证明物理执行已验收。

## 两种接口分别接入

| 轨道 | 被测决策 | 接入点 |
| --- | --- | --- |
| 四场景 goal 主轨 | 自行分解业务目标、选择团队、分配及重规划 | `GoalAlgorithmAdapter` → `GoalAlgorithmHost` → `GoalSession` |
| 固定 DAG 诊断 | 公开 DAG 内分配/改派；Factory F-INS 仅受限控制 | `CallableAlgorithmAdapter` / `PlannerAdapter` → `ParticipantSession` |
| City 分项诊断 | 服务请求、状态及回执测量 | `C-SERVICE-01` 服务探针，无固定业务 DAG |

主轨代码在 `demo/benchmark/goal_algorithm_adapter.py`。被测 adapter 实现：

```python
def manifest(self):
    return {
        "kind": "participant", "id": "my-multi-robot-system", "version": 1,
        "interface": "eai.benchmark.goal_algorithm_adapter",
        "interface_version": 1, "evaluation_track": "goal_driven",
        "input_scope": "participant_visible_events_only",
        "control_scope": "benchmark_managed_low_level_control",
    }

def initialize(self, public_task, participant_api): ...
def act(self, new_public_events): ...  # 返回 goal_action 列表
def close(self): ...
```

题包/API 为 v2，动作 envelope 仍为 `eai.benchmark.goal_action` **version 1**，
计划使用 `eai.benchmark.goal_plan` v2；请按公开 API 构造动作，不根据题包版本猜测动作版本。
初始化收到公开题面/API 的副本；每次 `act` 只收到新投递的公开事件。
算法不得读取作者见证、未来事件、隐藏评价器或宿主报告。

可信 episode 宿主负责 `emit`、`update_runtime_state` 和 `complete_skill`，并以固定后端
执行已准入技能。请求接受不等于技能成功；只有合法终态 receipt 才能完成账本记录。
回调或动作格式异常会关闭桥接，保留已接受动作，不自动重放半批动作。宿主按当前时间
投递事件并串行调用；该 Python 接口是信任边界约定，非不可信代码的进程沙箱。

以下例子在仓库根目录用 `env_isaaclab` 运行，只验证公开接入和团队选择，不执行业务技能：

```python
import json
from pathlib import Path
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost

release = Path("tmp/four_scene_handoff_new/factory/release")
public = json.loads((release / "public_task.json").read_text())
api = json.loads((release / "participant_api.json").read_text())

class SelectOne:
    def manifest(self):
        return {"kind": "participant", "id": "interface-smoke", "version": 1}

    def initialize(self, public_task, participant_api):
        self.robot = public_task["candidate_robot_pool"][0]["id"]

    def act(self, events):
        if not any(event["type"] == "episode_started" for event in events):
            return []
        return [{"schema": "eai.benchmark.goal_action", "version": 1,
                 "action_id": "select-1", "submitted_at_s": events[-1]["time_s"],
                 "type": "select_team", "payload": {"agent_ids": [self.robot]}}]

    def close(self):
        pass

with GoalAlgorithmHost(public, api, SelectOne()) as host:
    assert host.request_actions()[0]["accepted"]
```

## 生成与独立读回

目录必须尚不存在；所有命令均为离线操作，不启动 Isaac：

```bash
python -m demo.benchmark.goal_suite audit --output-root tmp/goal_suite_new --verify-sources
python -m demo.benchmark.goal_suite verify --output-root tmp/goal_suite_new --verify-sources
python -m demo.benchmark.handoff_manifest --output-root tmp/four_scene_handoff_new
python -m demo.benchmark.handoff_manifest --verify tmp/four_scene_handoff_new
```

默认矩阵为四场景 × 九条件 = **36 个题包、28 对单因子对照**。每个条件使用同一审核流程。
City `--city-package` 是额外地图准备材料，不是生成 City goal 题包的前提。
交接矩阵包含四份基准题包；suite 则包含完整因子矩阵、源码冻结与独立审核报告。
读回会重跑契约和作者审核、重算哈希并核对文件清单，不信任保存的通过标志。
两个工具的报告当前均为 v2；旧 v1 归档保留，换新目录生成。

交接前可用一个命令执行完整离线预检。它会在新目录中生成 suite 和 handoff，重新读回，
检查每个 `release/` 只有三个公开文件，并用公开文件完成四场景各一次团队选择。失败时
自动删除部分目录，避免把不完整结果交给接手者：

```bash
python -m demo.benchmark.handoff_preflight \
  --output-root tmp/handoff_preflight_<date>
```

预检输出 `preflight.json`，明确记录 `isaac_started=false`、`ros_started=false`、
`external_scheduler_started=false` 和 `scoreable=false`。它是交接冒烟检查，不替代真实
后端、物理执行或业务评价。

总目录交给可信开发者。被测系统只收到对应题包的 `release/` 和已投递事件；
`authoring/`、`evaluator/`、总报告及未来 treatment 不进入算法工作目录。
实际资产版本、场景布局和后端证据必须另行绑定，源码冻结不代表资产测量完成。

## 运行记录与统计接口

`algorithm_adapter.py` 的 manifest/result/statistics 工具可用于两种轨道。记录 algorithm
版本、benchmark commit、题包/场景 SHA-256、provider revision、seed、分支、独立运行目录、
factor 和 runtime 配置。固定后端的实现版本、参数和 execution contract 摘要应放入
`runtime_config`，同一比较必须完全一致。不同轨道不可混用题包 hash。

`expand_algorithm_cases` 展开算法×任务×seed×factor×分支并生成独立 ID；
`run_algorithm_batch` 只是离线回调线程池，不能在这些线程内建立多个 Isaac 应用。
实际进程隔离、GPU/机器分配、重试及远程调度由外部调度器负责。

`build_algorithm_result` 记录 `completed` / `failed` / `timeout` / `invalid`，
`completed` 表示运行结束，不自动表示业务成功。业务成功率须由可信评价器产生。
后端失效、算法超时与任务失败应分别记录，不能把控制器故障当作多机模型决策失败。

`summarize_algorithm_metric` 给出状态数、缺测数和描述性 bootstrap 区间；
`compare_paired_metric` 要求相同冻结输入，拒绝混版本和重复结果，并报告缺对。
耗时/成本用 `higher_is_better=False`，成功率等用 True；差值始终是 candidate − baseline。
缺失指标不会自动填零，失败如何进入主指标必须在实验方案中预先定义。
`holm_bonferroni` 仅校正调用者提供的有效 p 值，不生成统计检验。

当前四场景各只有一个独立声明布局，`held_out_validated=false`。同一布局的多个分支或
种子不等于独立任务样本；不能用本工具的普通 episode bootstrap 宣称跨任务泛化显著性。
正式实验应预定义独立任务组、成对种子、失败处理、指标方向及多重比较族；相关分支按任务
聚类分析。新增未曝光布局经 split/泄漏分组审核后，才可讨论正式泛化测试。

## 接手开发者验收清单

1. 在新目录复跑四场景生成、源码冻结与读回，核对 36/28 及四个主轨 ID。
2. 接入自己的 adapter，仅加载 release；复跑 `test_benchmark_goal_algorithm_adapter.py`。
3. 绑定同一固定技能后端、事件源及独立业务回执，核对时钟、取消、成员变化和控制参数。
4. 由外部调度器隔离各运行，结果回填 manifest/result；按预注册统计方案比较。
5. 单独验收 execution / held-out / release gate；当前题包均保持 `external_pending`、
   `scoreable=false`，不会因作者交接通过而自动晋升。

## 交接后必须补齐的工程门禁

这些项目属于接手的运行/实验平台，不应伪装成任务生成已完成：

- **运行幂等**：为每个 `episode_id` 建立持久 claim/lock、`attempt_id`、checkpoint 和
  结果唯一约束。`run_algorithm_batch` 只做离线回调 fan-out，不防止跨进程重复运行；重试
  必须生成新 attempt 并绑定原 episode，禁止覆盖旧结果。
- **执行证明**：`benchmark_default_ideal` 只是比较策略。完成结果应额外绑定实际控制器、
  技能后端、execution contract、资产/provider revision 和 runtime provenance；没有证明的
  结果只能标为协议/模拟通过，不能写成物理业务成功。
- **实验注册**：正式统计前冻结 task/layout split、任务聚类键、成对 seed、失败处理、
  指标方向和比较族。当前 bootstrap 是描述性工具，不能把同一布局的多分支当成独立任务，
  也不能代替按任务聚类的泛化分析。
- **环境封装**：接手者应锁定 `env_isaaclab`、Python 依赖、benchmark revision、资产
  revision 和外部调度器版本；预检只验证纯 Python 边界，不能替代 GPU/Isaac/ROS 运行矩阵。
