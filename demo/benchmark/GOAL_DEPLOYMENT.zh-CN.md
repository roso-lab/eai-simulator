# 目标计划执行接入与导航部署诊断

`goal_session.py` 为 goal-driven v2 提供有状态准入账本。它复用现有公开题面和动作协议，
记录选队、计划版本、依赖、当前观察、资源预约以及派发/取消状态。接纳动作不会自动完成技能，
技能完成也不会推导业务结果。原公开 API 的执行状态仍为 external_pending；这里是独立接入层，
尚未替换三个业务场景的旧固定 DAG 执行器。

## 参与者与可信运行时

参与者只能通过宿主提交 `submit_action(action)`。宿主负责串行调用以下可信接口：

- `emit(type, time_s, payload)`：仅发布已经发生的观察、通信或成员事件。
- `update_runtime_state(time_s, robot_regions, object_states)`：提交真实当前状态；不能把计划预测的位置或所有权当作读回。
- `complete_skill(task_id, time_s=..., status=..., receipt=...)`：记录后端终态回执。

回执必须绑定后端、唯一引用、任务 ID、派发 action ID、终态、完成时刻与非空证据引用。
账本审核绑定和状态迁移；宿主验证证据内容及后端来源。不要把可信接口或整个会话对象暴露给
被测模型。缺证据保持 pending；取消请求保留资源，直到后端确认；失去成员或能力的在途任务
不能静默成功。计划修订提交剩余工作 DAG，已派发 task ID 不可重新使用。

预约时窗允许真实后端提前完成，不能把声明 duration 当成已经执行的时间。非零时刻需要当时
的完整物体状态及相关成员位置快照。资源模型只表达公开路线和区域的容量，尚不覆盖静止机体
占地、接触力、三维转弯以及安全中断。

## 可执行的导航诊断

`goal_navigation_probe` 把一次作者计划派发接到现有 Warehouse 的真实导航入口。它使用独立
任务、区域和路线 ID，坐标来自维护的 `navigation_smoke.json`。这不是仓库订单题的物理绑定，
也不生成隐藏评价器或完整业务题包。只实例化一个 Carter，其他候选没有被本诊断验证。

所有命令从仓库根目录运行；Isaac 在 `env_isaaclab`，本流程不需要外部 ROS：

```bash
conda run -n env_isaaclab env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.warehouse_benchmark.main --check-assets

# 必须使用新目录。此步不启动 Isaac。
conda run -n env_isaaclab env PYTHONPATH=. \
  python -m demo.benchmark.goal_navigation_probe --output-root tmp/navigation_probe_new

# 派发落盘后启动一次真实 Isaac session；同一目录不能重复执行。
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_navigation_probe \
  --output-root tmp/navigation_probe_new --execute --device cuda:0

# 独立只读复核报告与场景、request、runtime 和当前位置来源。
conda run -n env_isaaclab env PYTHONPATH=. \
  python -m demo.benchmark.navigation_deployment_review \
  tmp/navigation_probe_new/run/navigation_report.json \
  --scenario tmp/navigation_probe_new/scenario.json --expected-scene warehouse_nvd
```

目录包含公开题面与 API、作者诊断计划、绑定及场景副本；`run/` 保存启动前账本、启动请求、
进程退出状态、Isaac 日志、原始导航报告、只读审核、审核后账本和摘要。它整体是作者运行材料，
不能作为模型输入。程序不会复用旧报告：运行目录独占创建，输入在启动前后按 hash 核对，
只有本次进程退出成功且导航证据被接纳时，宿主才生成绑定回执。启动失败、没有最终报告、
超时和证据缺失均不补写成功。

## 结论边界与下一步

`navigation_deployment_review` 重算文件和已有 provenance，检查仿真步数、时钟、位置轨迹、
调度完成和仿真步后的目标距离。它识别显式合成数据与位姿覆盖模式，缺失证据时保持 pending，
不从保存的成功标志反推事实。文件绑定不提供独立进程认证，可信启动宿主还需保存真实退出码。

共享运行时在每次 `env.step` 前复制提交命令，并在返回后、运动学位姿覆盖前读取 articulation
根状态，保存到报告的 `control_step_evidence`。它记录命令三元组和根状态 13 元组，时间由
连续控制步及实际 `step_dt` 推导；命令或状态非有限、缺步、身份改变均使记录无效。这是单环境
诊断的输入与回读记录，不包含独立执行器测量，也不替代 `set_command` 等辅助控制通道。

审核器分别核对内容 hash、步数、时钟、机器人身份和已有位置样本。导航来源为移动 body 的
机器人不能用 articulation 根状态冒充移动 body 读回；运动学位姿覆盖同样不能取得物理准入。
旧报告缺少该字段时，命令证据仍为 pending。即使新记录通过，`physical_control_validated=false`；
有限位置读回也不能证明地面支撑、接触安全或连续碰撞自由。此次部署打通的是“计划接纳 → 派发 →
实际导航 → 宿主审核 → 账本终态”的诊断链。后续仍需绑定业务区域与道具、联合载荷和固定点、
观测与业务回执，再做通信/成员变化的完整任务对照。完整业务、全任务物理执行与 `scoreable`
始终保留为独立门槛。

主轨道按可靠技能契约验收：固定抓取点、质量与逐成员负载、机体/物体净空、资源占用、当前
状态和合法调用的效果必须一致。这里保留的物理控制诊断标志用于描述证据范围；自主抓取、
VLA 成功率和接触力求解不应成为任务生成侧新增的模型考题，也不应替代上述契约验收。

2026-09-18 的阶段 10 实跑归档位于 `tmp/goal_navigation_probe_20260918_stage10/run/`：
254 条连续步记录覆盖 5.08 秒，Carter 和挂载机械臂根状态共完成 54 次轨迹位姿交叉核验。
命令证据为 `submitted_and_post_step_readback`，第 253 步目标误差 0.4976769 m，小于 0.5 m
目标半径；可信宿主回执在 5.08 秒完成账本任务。独立子代理重新审核并重放账本，结果一致。
机械臂仅提供根状态读回，未验证关节作业或抓取。该目录是作者诊断证据，不是可计分题包。
