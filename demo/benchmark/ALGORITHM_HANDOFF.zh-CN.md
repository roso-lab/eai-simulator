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
需要可信后端回执。该参考宿主不是 Isaac 控制器；运行器现在可以注入并校验与
`GoalAlgorithmHost` 相同的 `GoalSession`，共享模式下技能终态会回写算法主轨。真实
正式 runner 会为同一 session 绑定唯一 `episode_id`，并要求 trusted skill receipt 携带同一
身份；participant trace、hidden evaluator trace、runtime snapshot 与业务 evidence 由 runner
分别归档。这个 runner/receipt 身份约束仍不等同于 Isaac 业务执行验证。
Isaac/外部进程 runner 仍必须显式使用该注入路径，不能并行维护两份互不一致的状态。
`benchmark_default_ideal` 是必须遵守的实验策略，不能用这个字符串证明物理执行已验收。

Warehouse 的 `paired_native_probe` 现归档一个 GoalSession 的单机超载失败与双 M20
短程动作：前者保持木箱原位，后者有原生双支撑连接、抬升和约 0.65 m 位移。该探针使用
作者专用初始位，未走完整 `rack_a_to_consolidation` 路线；配对任务的可信回执因此记录
局部进展后的失败终态，业务结果仍未评估。它不是 TeamWeaver/EMOS 自主重规划运行，
也不能替代正式 episode runner 的业务证据闭环。运行目录与复现命令见
`TODO.zh-CN.md` 的持续运行清单。

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
计划使用 `eai.benchmark.goal_plan` v3；v3 将参与者路径的物理搬运失败交给执行层
确定性回执，同时保留 schema、能力声明、证据和预算的 fail-closed 校验。请按公开 API
构造动作，不根据题包版本猜测动作版本。
初始化收到公开题面/API 的副本；每次 `act` 只收到新投递的公开事件。
算法不得读取作者见证、未来事件、隐藏评价器或宿主报告。

接入启动时应显式检查 `participant_api` 中声明的 action/plan schema、版本和
`interface_version`，遇到不支持的版本立即返回 `invalid` 并记录原因；不要把题包的 `version=2`
传播到 `goal_action` envelope，也不要静默降级到旧字段。固定 DAG 诊断沿用其自己的
`ParticipantSession` action schema，不能与 goal 主轨的 envelope 混用。

可信 episode 宿主负责 `emit`、`update_runtime_state` 和 `complete_skill`，并以固定后端
执行已准入技能。请求接受不等于技能成功；只有合法终态 receipt 才能完成账本记录。
回调或动作格式异常会关闭桥接，保留已接受动作，不自动重放半批动作。宿主按当前时间
投递事件并串行调用。`GoalSession` 是 episode 的唯一权威账本：动作准入、运行时快照、
技能终态和证据引用必须回写同一个 session；外部后端不得维护第二份可独立宣布完成的账本。
正式 `FormalGoalEpisodeRunner` v3 每次只驱动一条 pending 技能到终态；同步 Isaac 后端
必须依据同一物理时钟返回不早于当前可信时间的完成时间。并行计划的两个任务不会因此
获得并行执行证明；若需要真正的并行技能，应另接基于实际完成事件排序的生产调度器，
并在原生 episode 中验证完成顺序、共享状态和资源占用。
已请求取消的技能只交给后端 `cancel()`；它必须提供 `cancelled` 终态的可信回执和
实际物理状态读回，不能再由普通 `execute()` 启动。没有取消能力时保留
`cancel_requested`，不冒充已停止或已完成。
该 Python 接口是信任边界约定，非不可信代码的进程沙箱。接手者必须把不可信 adapter
放在独立子进程/调度作业中，不能把它直接加载进持有 `GoalSession` 信任方法的 benchmark
进程。

### 运行边界与业务结果口径

benchmark 提供题包、公开 participant API、事件/动作 schema、证据引用规则和固定的低层
导航、移动、抓取技能；被测系统只负责高层选择团队、分解、分配、通信响应和重规划。
`benchmark_default_ideal` 是固定控制变量，不是物理或业务成功证明，也不是允许 adapter
替换低层控制器的入口。

每次运行都必须把以下链路绑定到同一个 `episode_id`：

1. participant 提交并被 `GoalSession` 接受的动作；
2. 固定技能后端的开始/结束、取消和失败 receipt；
3. 机器人/对象的可信 runtime snapshot；
4. detector、dock、服务等领域业务证据及其 provenance。

`algorithm_result.status=completed` 只表示 adapter 进程结束并返回了合法结果。运动学或
符号 transport 完成只能记为 execution evidence；`business_success` 必须由可信领域评价器
依据独立业务证据产生。缺少后端回执、对象身份/位姿、服务回执或证据 provenance 时，结果
保持 `external_pending`/`scoreable=false`，不能把 `completed`、`transported` 或机器人自报
成功提升为业务成功。

### 外部算法进程隔离契约（下游平台必须实现）

该契约属于运行平台，不改变任务生成与审核的输入。每个 episode/attempt 应遵守：

- 调度器以只读 `release/`（`public_task.json`、`participant_api.json` 及已投递事件）启动
  一个独立子进程或远程作业；不把 `authoring/`、hidden evaluator、宿主报告或未来事件挂载
  给被测算法。
- 通过版本化 IPC/文件边界传递公开事件和高层动作；子进程不能调用 `GoalSession` 的可信
  方法、写入 benchmark 账本或覆盖输入。输出必须经过 `validate_algorithm_result`，并核对
  `manifest_sha256`、`episode_id` 和独立 `run_root`。
- 每回合设置 wall-clock timeout，超时先终止整个进程组，再按平台策略强制回收；记录
  `timeout` 与 `failure_class=algorithm_timeout`，不要把超时伪装成任务失败或 `completed`。
  非零退出、崩溃、协议非法输出和宿主故障分别记录为 `failed`/`invalid` 的细分原因。
- stdout/stderr、退出码、超时、信号、输入/输出摘要和调度器版本写入该 attempt 的运行
  目录。benchmark 只接收结构化结果和证据引用，不信任进程退出码单独证明业务完成。
- 一个 adapter 进程只服务一个隔离 attempt；并行度、GPU/机器分配和资源清理由调度器拥有，
  不得在 `run_algorithm_batch` 的线程里创建多个 Isaac application。

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

这些项目属于接手的运行/实验平台，不应伪装成任务生成已完成。benchmark 负责题包、API、
证据语义和固定低层技能；以下项目由下游运行/实验工程师实现并在独立分支验证：

- **外部进程隔离与超时**：按上面的进程契约实现子进程/远程作业、进程组回收、日志捕获和
  `timeout`/`failed`/`invalid` 分类；这不是 `GoalAlgorithmHost` 的 Python 回调可以替代的。

- **运行幂等**：为每个 `episode_id` 建立持久 claim/lock、`attempt_id`、checkpoint 和
  结果唯一约束。`run_algorithm_batch` 只做离线回调 fan-out，不防止跨进程重复运行；重试
  必须生成新 attempt 并绑定原 episode，禁止覆盖旧结果。

- **通信与生命周期真实执行**：把 `communication_quality`、`member_join`、`member_exit`、
  `capability_change` 的协议 treatment 接入真实 episode 事件源，验证投递/消费/过期、替补
  成员和所有权迁移；仅有离线 factor matrix 或作者声明不算执行证据。

- **统计注册与泛化分析**：冻结任务/布局 split、任务聚类键、成对 seed、失败处理、指标
  方向和比较族；当前 bootstrap 只做描述统计。跨任务泛化和多分支并行实验由下游统计工程
  师负责，不属于任务生成与作者审核的发布结论。
- **执行证明**：`benchmark_default_ideal` 只是比较策略。完成结果应额外绑定实际控制器、
  技能后端、execution contract、资产/provider revision 和 runtime provenance；没有证明的
  结果只能标为协议/模拟通过，不能写成物理业务成功。
- **环境封装**：接手者应锁定 `env_isaaclab`、Python 依赖、benchmark revision、资产
  revision 和外部调度器版本；预检只验证纯 Python 边界，不能替代 GPU/Isaac/ROS 运行矩阵。

## 运动模型发布状态（2026-09-21）

用户已确认 Go2、B2 的替换运动模型具备上下楼梯能力，目前仍在部署和独立验证阶段。M20
继续作为已有楼梯能力基线。Go2/B2 的模型或 checkpoint、机器人配置和运行参数必须写入
`runtime_config` 并绑定 provider/model hash；部署完成前不得把楼梯探针或相关 goal episode
标为 `execution_validated=true` 或 `scoreable=true`。发布前至少需要每个模型的连续上行、
平台停留、下行和失败/超时读回，以及与 `F-VERT-01` 题包的场景绑定证据。该状态只影响
运动执行与发布门禁，不改变任务生成、审核和高层算法接口。

# Goal runner 失败反馈（诊断宿主 v2）

正式 runner 的可信执行后端可在失败终态附带有限的 `skill_execution` 公开
`observation`：`skill_succeeded`、`object_moved`、`task_advanced` 及逐载具
`requested_load_kg`/`effective_payload_kg`。其中负载数值是请求分担质量和冻结公开
额定上限，并非实测夹爪力。`action_id` 将观测与派遣关联；内部错误原因、
PhysX 路径和隐藏业务评测证据不跨参与者边界。旧归档没有该反馈保证，
不能作为算法消费失败事实后的恢复证据。没有新鲜原生区域快照时，
GoalSession 仍拒绝后续空间计划；收到失败消息本身不构成恢复成功。
