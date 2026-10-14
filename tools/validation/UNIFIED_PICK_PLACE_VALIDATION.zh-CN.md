# 统一局部 PickPlace 验证记录

日期：2026-10-14。工作分支：`codex/unified-pick-place`；合并基线：
`bca3527a53c6691ccba38b4f8d5341989b7728cf`。结果来自该分支尚未提交的工作树实现。
用户确认的第六个型号为 **G1 人形机器人**。

## Isaac 连续十轮结果

UR5、Z1、PegasusIris、PegasusX4、Pepper、G1 各完成 **10 次 pick 和 10 次 place**，
共 **60 轮、120 次布尔调用，全部成功**。每个型号在同一宿主会话内复用同一个物体、
后端和 executor，轮间没有 reset、重新初始化或人为移回源点。放置后继续物理步进，
观测最终物体原点，而非仅检查控制命令或规划终态。

| 模型 | pick | place | 最小实测抬升 | 最大最终原点误差 | place 后占用 |
| --- | --- | --- | --- | --- | --- |
| UR5 | 10/10 | 10/10 | 91.136 mm | 0.114 mm | 每轮全部清空 |
| Z1 | 10/10 | 10/10 | 95.676 mm | 0.279 mm | 每轮全部清空 |
| PegasusIris | 10/10 | 10/10 | 304.606 mm | 3.902 mm | 每轮全部清空 |
| PegasusX4 | 10/10 | 10/10 | 292.427 mm | 3.978 mm | 每轮全部清空 |
| Pepper | 10/10 | 10/10 | 101.061 mm | 21.088 mm | 每轮全部清空 |
| G1 | 10/10 | 10/10 | 78.902 mm | 1.556 mm | 每轮全部清空 |

[逐轮观测与报告校验值](results/unified_pick_place_20261014/README.md) 保存全部 60 轮的
结果、导航／保持步数、实际放置误差与各次运行的源码哈希。原始 JSON 位于同目录。

每轮验收同时要求：

- pick/place 均严格返回 `True`；pick 后为 holding/stopped，place 后为 empty/clean/stopped。
- 物体实际抬升至少 5 cm；外部移动或静止保持期间每个观测步均仍持物。
- 恰好发生一次挂接与一次释放；place 后机器人和物体资源租约全部释放。
- 撤离／回收后继续步进，最终原点误差满足原容差：机械臂和无人机 35 mm、Pepper 40 mm、G1 15 mm。

## 场景与验证范围

`unified_pick_place_smoke.py` 是唯一仿真宿主。`PickPlaceExecutor` 使用宿主已有的步进
回调；后端衔接该宿主的物理钩子。每轮使用独立的 `trial-N/pick` 和 `trial-N/place`
请求 ID。调用者选择导航，并在 place 前重新读取完整底盘世界位姿。成功判据和后端
容差没有因本轮测试而放宽。

| 模型 | 控制与挂接 | 物体及环境 | pick 与 place 之间的调用者动作 |
| --- | --- | --- | --- |
| UR5 / Z1 | 原生 articulation、柱坐标 IK、follow proxy | 固定基座；机器人自身重力和碰撞关闭；0.01 kg 方块保留重力及支撑面接触 | 不导航，保持 120 步；两个相距 25 mm 的目标交替放置 |
| PegasusIris / PegasusX4 | 原位置控制器、PhysX FixedJoint | 0.01 kg 方块；世界支撑、重力及释放后接触 | 调用者独立发送 0.25 m 水平往返位置命令，保持 200 步；实测底盘位移约 0.273–0.278 m |
| Pepper | 原 composite controller 的局部双臂 delegate、实测双手逻辑挂接 | 带重力的 8 kg 箱体及支撑桌；保留场景碰撞过滤 | 调用者正反发送底盘速度，随后停止；每轮 70 步，实测位移约 46.15 mm |
| G1 | 原生 articulation、Inspire 右手标定、影子 stage 规划、实测掌部逻辑挂接 | 固定基座机器人关闭重力及自碰撞；0.05 kg 圆柱关闭重力及碰撞 | 不导航，保持 120 步；原点与偏移 `(0.02, 0.10, 0)` m 的目标交替放置 |

机械臂持物时观察实际 follow proxy 的 `/Visual` 子节点世界位姿；隐藏的 PhysX 源物体
被停放，不能用它的缓存位置代表持物状态。释放后读取 PhysX 物体位姿。实例化碰撞网格
在物理初始化前显式处理；Z1 的局部 IK 使用 1/s 积分增益，并在首次抓取前进入回收准备姿态。

G1 新局部后端拆开 pick 与 place：pick 仅准备、接近、闭合和抬升；place 才规划调用者
提供的目标、释放、撤离及回收。成功由实测掌部、手指、物体位置和关节稳定性门控。
取消冻结停止瞬间的实测关节目标并保留连接；该最终修正后已重新完成 G1 十轮。
使用资产 `usd/robot/g1/g1_29dof_with_inspire_rev_1_0.usd`，SHA256 为
`86047174b87b4df485e996232fb4d2ece5901a9bcd6f9a54b78f961ce664730e`。

这些结果验证理想挂接下的局部运动、持续持物、单次释放、布尔终态和资源管理。
接触力抓取、负载极限、移动机械臂底盘及 G1 行走未在本原生测试中验证。
安装偏移、倾斜底盘和外部控制权交接由相关轻量回归覆盖。

## 轻量回归

现有 `env_isaaclab` Python 的完整相关回归 **363 项通过**（1.17 秒）：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/ljy/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  algorithm/skills/tests task/tests \
  source/EAI/test/test_idealized_manipulation_runtime.py \
  source/EAI/test/test_environment_view.py
```

覆盖完整刚体变换、倾斜底盘、安装外参、物体原点与抓取偏移；验证布尔终态、错误反馈、
非法输入、不支持姿态、不可达、超时、取消、重复请求、宿主异常及线程约束。
还验证失败后持物／占用保留、外部导航接管资源、一次释放及释放后漂移拒绝成功，
并运行合并提交的 G1／Pepper 适配器和宿主循环回归。

G1 停止保持的最终修正后，`task/tests/test_pick_place_g1.py` 的 **5 项再次通过**；
包括关节漂移时持续使用停止瞬间目标、取消保留持物、实测释放门控与 nonfinite 停止拒绝。
本次回归命令覆盖的每个测试文件均来自当前工作树；执行器内部仍只向调用者返回布尔值。

## 复现与原始产物

在仓库根目录用 Isaac Python 依次执行，每个进程创建一个宿主仿真：

```bash
pick_place_python=/home/ljy/miniconda3/envs/env_isaaclab/bin/python
pick_place_reports=tools/validation/results/unified_pick_place_20261014
export OMNI_KIT_ACCEPT_EULA=YES
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs

"$pick_place_python" tools/validation/unified_pick_place_smoke.py --backend arm --arm ur5 --trials 10 --report "$pick_place_reports/ur5.json"
"$pick_place_python" tools/validation/unified_pick_place_smoke.py --backend arm --arm z1 --trials 10 --report "$pick_place_reports/z1.json"
"$pick_place_python" tools/validation/unified_pick_place_smoke.py --backend drone --drone iris --trials 10 --report "$pick_place_reports/iris.json"
"$pick_place_python" tools/validation/unified_pick_place_smoke.py --backend drone --drone pegasus --trials 10 --report "$pick_place_reports/pegasus.json"
"$pick_place_python" tools/validation/unified_pick_place_smoke.py --backend pepper --trials 10 --report "$pick_place_reports/pepper.json"
"$pick_place_python" tools/validation/unified_pick_place_smoke.py --backend g1 --trials 10 --report "$pick_place_reports/g1.json"
```

默认 device 为 CPU，headless Kit 在本机 RTX 5080 上初始化；每个请求的 `timeout_s` 为 100 s。
每轮原子保存 JSON checkpoint，
在 Kit 关闭前保存终态。验收读取 `passed`、`completed_trials` 和逐轮实测证据，
不能仅依赖 Kit 关闭时的进程退出码。

仓库已有 `*.json` 忽略规则，原始报告保留为本地实验产物；本验证记录、逐轮 Markdown
和 `raw_reports.jsonl` 可随实现版本保存。六份报告中的 `source_sha256` 与当前运行时源码
一致，验收使用 `results/unified_pick_place_20261014/{ur5,z1,iris,pegasus,pepper,g1}.json`。
