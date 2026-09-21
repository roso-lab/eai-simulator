# 阶段19日志恢复与实测复核（2026-09-20）

范围：复核并修复未完成的 P0 City C1。Warehouse 双 M20
已有历史完成证据，本轮未重新验收。Go2 楼梯能力不在本轮复验范围内。

## 已确认并修复

- 半米栅格负坐标由朝零截断改为 floor，路径、障碍和出生点查询一致。
- 出生点搜索改为有界连通遍历：旧运行把 p03 移动139.16m；修复后对应
  实例化候选从 `[-55,-63.5]` 移至 `[-56.75,-64.25]`，约1.90m。
- 正式人员到达等待遵循公共120秒策略，episode总预算180秒；诊断模式保留45秒。
- 人员在引导获准前暂停，执行路线重规划不覆盖出生时的 binding。
- 动态碰撞采用逐段连续轴对齐包络扫掠，消除总路径包围盒将远处车辆误判为碰撞。
- 暂停后引导使用声明的walk/jog，避免把待机idle传给仅支持行走步态的PathFollower。
- 到达轮询只读取即时状态，最终服务读回继续保留完整轨迹与逐步安全证据。

旧“缺录生成灯杆”结论已被同批结构化数据反证：Lamp_0211中心
`(-54.95,51.87)` 在 `roadside_177` bounds 内。没有修改或发布城市USD来迎合结论。

## 原生实验记录

- `tmp/city_benchmark/recovery_20260920_v1/`：负坐标修复后4人净空和4请求接受；
  独立审查发现139m越界出生与16秒预算不可能容纳103m路线，主动中止。
  Kit退出0且报告停留starting，不能视为成功。
- `recovery_20260920_v2/`：出生和预算修复后，p03动态准入拒绝，报告blocked。
- `recovery_20260920_v3/`：人员暂停暴露idle步态错误，报告blocked；随后补修及回归。
- `recovery_20260920_v4/`：有限动态重规划仍被拒绝；追查发现动态检查使用
  总路径包围盒，逐段连续扫掠修复后再执行新批次。
- `recovery_20260920_v5/`：4人到达、3人安全；p03在41.38–41.48秒与车辆动态包络
  相交，6步安全谓词未通过。随后改为先hold交通、疏导结束再release。
- `recovery_20260920_v6/`：4人全部到达且服务审核为 `evidence_complete`；
  全体后续车辆hold/release审核也为 `evidence_complete`。严格原生探针在
  following_vehicle_02的12条有限改道候选处停止（标线，以及部分路缘/人行道碰撞）；
  `status=blocked`、`formal_episode_binding=false`，事故回执最终环节尚未执行。

用户随后明确：背景车辆可碰撞，不要围绕该问题继续研究。本轮停止该物理排障，
不将背景碰撞作为任务流程整改门槛，也不篡改v6或宣称其全流程通过。
City后续分项流程应区分服务结果与背景车辆物理表现；正式计分门禁保持独立。

以上历史产物原样保留。准备、实际状态、正式绑定一致性、业务证据完整性是不同
门禁；进程退出0、请求ACK、单元测试通过均不直接提升 `scoreable`。

原生命令使用 `env_isaaclab/bin/python -m demo.city_benchmark.service_probe`，
分别 `--prepare`、`--execute`，参数为 `--device cpu`、独立 `--output-root`、
v1–v5 prepare绑定 `tmp/city_benchmark/formal_binding_20260921k/authoring/private.json`。
v6使用独立 `tmp/city_benchmark/recovery_binding_20260920/authoring/private.json`，
在prepare前固化校准后的出生点/路线；执行后与原冻结binding比较，避免运行时自改自证。
v3起设置 `OMP_NUM_THREADS=4`。CPU是本探针当前查询同步性的限制，不是动画人物规则。

外部依赖仍包括M20运动模型选择/验收、Carter修复资产provider发布和后续成对
treatment业务见证；本轮不自动发布资产、提交合并或把这些标成完成。

## 最新聊天四项整改

- 决策落地：City服务诊断就是分项测量，不补固定DAG；`C-SERVICE-01`写入新探针报告。
- 明确F-INS仅开放route_confirmation，Warehouse/House开放固定图节点内分配；
  F-VERT为不计分的平台能力探针。统一说明见DELIVERY工程结构节。
- House主任务改为`MODERN_HOUSE-GOAL-01`；默认室内DAG与新生成题包分别使用
  `W-DAG-01`、`MH-DAG-01`，保留历史报告、task_name和冻结摘要。
- 新增规范native_probe兼容入口；七个维护场景具备可重建、输入摘要绑定的audit
  sidecar，规范写入AGENTS.md和DELIVERY。Factory审计仅覆盖schema/契约，
  Warehouse/House另做现有几何/计划审核；二者均不声称物理执行完成。

House侧重建audit时使用了本机已更新的地图/体素产品，摘要随实际输入更新；
本轮没有重建或发布这些资产，sidecar不表示provider已包含本地产品。

## 本轮验收

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 <env_isaaclab>/bin/python -m pytest -q
  source/EAI/test/test_benchmark_diagnostic_audit.py
  source/EAI/test/test_scene_challenge_reuse.py
  source/EAI/test/test_benchmark_goal_driven.py
  source/EAI/test/test_factory_task_admission.py`：125 passed。
- 同环境 `python -m pytest -q demo/city_benchmark/tests
  demo/city_benchmark/scene/tests/test_service_runtime.py`：274 passed。
- `python -m demo.benchmark.diagnostic_audit --write`生成七份维护sidecar；
  随后不带`--write`独立读回核验七份全部通过。
- 四个新增native_probe兼容入口的`--help`均退出0；未再次启动Isaac。
- `git diff --check`通过。未提交或推送；未改动其他会话的Go2/Factory运行时工作。

团队研究代理已完成日志取证和前期独立审查；后续整改盘点因服务端429未能执行，
由主代理接手实现与验收。当前工具未暴露agentmemory，因此未伪称完成记忆保存。
