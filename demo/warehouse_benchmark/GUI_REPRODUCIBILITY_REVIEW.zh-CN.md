# Warehouse GUI Benchmark 复现与修复交接

更新时间：2026-09-21

本文记录 2026-09-21 对 `W-DAG-01` Warehouse 固定 DAG 任务进行 GUI 运行的复现条件、实际结果和交接给其他工程师的修复项。运行使用 benchmark 自带的 `public_greedy` 参考执行器，没有接入 TeamWeaver、EMOS 或其他被测模型。

## 结论

| 项目 | 结论 | 说明 |
| --- | --- | --- |
| Git 代码 | 可复现 | GitLab 远端 `benchmark-task` 当前指向 `d74443661469af850adbd66dac0578fdcfb261e8`。 |
| 场景输入 | 可复现 | `demo/warehouse_benchmark/scenarios/business_logistics_01.json` 已入库，SHA-256 为 `1c651e756a03c8b01864cce492db05ee3b66211f02ed71c8d04cf20a834ca504`。 |
| NVD 道具 | 团队环境已具备 | `wooden_crate_a` 和 `cardbox_c` 已在运行机的 NVD 资产目录中；它们不在 Git 中，但不构成本次交接的缺失项。复现时仍需使用团队约定的 sanitized shadow root。 |
| Isaac 运行环境 | 团队环境已具备 | `env_isaaclab`、Isaac Sim 5.1、Isaac Lab 2.x 和 CUDA/显示环境已安装；文档保留版本记录用于排查漂移。 |
| GUI 完整实验 | 本次未完成 | 进程被用户中止；最后约在第 1551 个控制步，节点仍停留在 `oversized_crate_transfer` 联合接近阶段。 |
| 运行产物 | 仅有预检快照 | `gui_full_20260921.json` 未生成；`.request.json` 和 `.runtime.json` 是输入与启动后预检快照，不能当作任务成功报告。 |

因此，其他工程师在使用同一团队运行机资产和 Isaac 环境时，可以从 GitLab checkout 后复现代码和启动过程。NVD 闭包和 Isaac 环境虽然不属于 Git 提交内容，但当前已有且不是交接阻塞；仍应把它们的标准路径、版本和校验方式写入团队运行手册，避免换机器时出现隐式依赖。

## 本次实际运行配置

```text
scenario                 demo/warehouse_benchmark/scenarios/business_logistics_01.json
participant              public_greedy
transport                kinematic
aerial motion            kinematic
device                   cpu
gui                      true
camera                   overview
render period            0.2 s of simulation time
wall timeout             1800 s
auto budget              11988 control steps
physics dt               0.005 s
control dt               0.02 s
Isaac Sim                5.1.0.0
Isaac Lab                0.54.2
Python                   3.11.16
Torch                    2.7.0+cu128
NumPy                    1.26.0
OpenCV                   4.11.0
GPU                      Quadro RTX 6000, driver 580.178.04
```

实际启动命令为：

```bash
export EAI_NVD_ASSET_ROOT=/home/pupil/nvd_assets_sanitized_xr
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs

/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.warehouse_benchmark.main \
  --scenario demo/warehouse_benchmark/scenarios/business_logistics_01.json \
  --gui \
  --device cpu \
  --camera-mode overview \
  --participant-mode public_greedy \
  --transport-mode kinematic \
  --aerial-motion-mode kinematic \
  --render-period-s 0.2 \
  --wall-timeout-s 1800 \
  --report tmp/warehouse_benchmark/gui_full_20260921.json
```

## 代码复现步骤

工程师应使用远端 `benchmark-task`，不要依赖当前操作者的本地分支名：

```bash
git fetch origin benchmark-task
git checkout -B benchmark-task origin/benchmark-task
git rev-parse HEAD
# 期望：d74443661469af850adbd66dac0578fdcfb261e8
```

按仓库 `AGENTS.md` 准备 `env_isaaclab`、Isaac Sim 5.1 和 editable packages。确认解释器和 pip 都来自该环境：

```bash
command -v python
command -v pip
python -m pip show EAI EAI_assets EAI_hmrs
```

## NVD 资产路径与复现

任务运行时需要两个原生道具：

```text
XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Warehouse/Containers/Wooden/WoodenCrate_A1.usd
XR_Content_NVD_v10010/Assets/XR/Stages/Indoor/Warehouse/Containers/Cardboard/Cardbox_C2.usd
```

本次运行机已经准备好原始 NVD 下载和 `/home/pupil/nvd_assets_sanitized_xr` shadow root。原始下载目录不能提交到 Git，也不应直接把带 `@` 或非 ASCII 路径的目录交给 USD。换运行机时，使用仓库脚本生成 ASCII 安全的 shadow root：

```bash
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
python tools/assets/prepare_nvd_props.py \
  --source-root /path/to/NVD_assets \
  --output-root /path/to/nvd_assets_sanitized_xr \
  --keys cardbox_c wooden_crate_a \
  --report /path/to/nvd_assets_sanitized_xr_report.json
```

本次生成的闭包包含 17 个文件，报告中的两个根道具均 `validated: true`。在当前团队运行机上直接设置：

```bash
export EAI_NVD_ASSET_ROOT=/home/pupil/nvd_assets_sanitized_xr
```

运行前先执行不启动完整 episode 的资产检查：

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
python -m demo.warehouse_benchmark.main --check-assets
```

注意：`--check-assets` 主要验证仓库中的 Warehouse 场景闭包；复现前仍应确认 `EAI_NVD_ASSET_ROOT` 下上述两个精确相对路径存在。当前团队资产已满足该条件。

## 性能与设备结论

本次 CPU GUI 运行约为 10 控制步/秒，完整自动预算大约需要 20 分钟墙钟时间；GUI 观察到的低帧率来自仿真推进速度。把 `--render-period-s` 从 0.2 调到 1.0 只降低显示刷新频率，不能解决 CPU PhysX 和传感器同步的主要瓶颈。

另外实测了 `--device cuda`。它能把环境标记为 `cuda:0`，但 Isaac Sim 仍需要 CPU 执行 Kit/USD 场景管理、Python DAG 调度、传感器读回、OmniGraph/ROS 边界和状态同步；这个单环境任务的 CUDA 版本约为 5.7 控制步/秒，反而慢于 CPU。当前 benchmark 不支持纯 GPU 管线，不能通过一个启动参数消除 CPU 负载。

## 本次中断证据

以下文件保留在 `tmp/`，仅用于审计启动输入和预检：

```text
tmp/warehouse_benchmark/gui_full_20260921.request.json
tmp/warehouse_benchmark/gui_full_20260921.runtime.json
```

`.runtime.json` 的 `complete: true` 表示运行时预检快照捕获完成，不表示 10 个 DAG 节点完成，也不表示 `business_task_success`。本次没有最终 report，因此不能计入 benchmark 成绩。

工作树还存在一个与本运行无关的本地修改，应保留但不要提交到本修复：

```text
M source/EAI_hmrs/EAI_hmrs/envs/my_env.json
```

## 建议工程修复项

1. **资产可复现性**：在内部制品存储发布 `cardbox_c`/`wooden_crate_a` 的 sanitized 闭包和报告，或提供受控下载入口；启动前输出缺失键、期望相对路径和生成命令。
2. **预检完整性**：把业务道具精确路径检查纳入 `--check-assets`，避免场景预检通过后才在 Isaac 进程中报 “registered but unavailable”。
3. **中断状态**：Ctrl-C 当前返回码为 0，容易被 CI 或交接脚本误判为成功；应写出 `interrupted` 状态、已执行步数和节点状态，并返回非零码，除非调用者显式请求允许中断。
4. **GUI 与 CI 分工**：完整 episode 默认使用 headless 生成可计分报告；GUI 只做短时可视诊断或单独的 240 步 smoke。若要求完整 GUI，应明确其约 20 分钟墙钟成本。
5. **设备口径**：在报告中同时记录 requested device、effective device、PhysX backend 和传感器 backend，避免把 `cuda:0` 误解为纯 GPU 执行。
6. **可交付命令**：将本文中的资产准备、`--check-assets` 和完整启动命令收敛到一个脚本或 `Makefile` 目标，减少工程师手工设置 `PYTHONPATH` 和环境变量的机会。

## 工程师验收标准

修复后至少应满足：

- `origin/benchmark-task` checkout 后代码哈希与交接版本一致；
- NVD shadow 报告显示两个业务道具闭包完整且 ASCII 安全；
- `--check-assets` 和业务道具精确路径检查均通过；
- GUI smoke 能启动并显示 overview 相机，且退出时生成结构化状态；
- 完整 episode 的正常结束生成 `gui_full_*.json`，中断或超时不会返回成功码；
- 最终报告明确区分 DAG 节点完成、kinematic transport、domain evidence pending 和 `business_task_success`。
