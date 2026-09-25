# 阶段62：R4 最小切片原生证据（快照→变化→恢复→读回→双 sham）

日期：2026-09-25
执行：ZCode 工程代理（benchmark-task 侧）
基线：`codex/benchmark-stage57-oracle-integration`，同步到 ljy 的提交
`38637db3bed9104ceba9fae960a03554eb25c817a17`（含阶段61融合的
`branch_data.py` + 五场景 checkpoint 引擎；`38637db3b` 为同步时点的最新
本地 HEAD，`6044a31` 为其祖先）。
本机（pupil）未启动 Isaac；全部原生运行在 ljy 测试机隔离 worktree 中完成。

## 1. 切片选型与结论摘要

- **场景**：Warehouse（`warehouse_nvd`，任务 `W-DAG-01`，场景 JSON
  `demo/warehouse_benchmark/scenarios/business_logistics_01.json`，seed 17，
  version 2）。选它是因为其 checkpoint 引擎（阶段61融合）是五场景中
  文档最完整的原生前缀+恢复实现，且 ljy 有完整本地资产。
- **R4 链**：变化前快照（CheckpointStore 落盘+校验回读+摘要）→ 短窗口
  变化（对 `carter_1` 施加 20 步 `forward=0.25` 命令）→ 真实恢复
  （`WarehouseCheckpoint.restore`，从落盘持久化状态恢复）→ 即时读回
  （全公开物理张量逐域 max-abs 误差 + RNG 流 + 环境缓冲）→ 两次无处理
  sham（同样变化窗口、不恢复、同样读回）。
- **结论（release 语义保持 false，不夸大）**：在所选原生状态子集上，预注册
  判据全部实测通过；本切片是 1 物理行、3 顺序处治臂的最小实验，不构成
  32 分支 formal 容量证据，也不构成参与者状态恢复或通信队列恢复的证据
  （本切片无参与者）。R4 出口仍为未完全闭合（见第 7 节差距清单）。

## 2. ljy 环境与资产配方

- 主机：ljy（AMD Ryzen 9 9950X，96 GB 内存，RTX 5080 16 GB），
  `/home/ljy/miniconda3/envs/env_isaaclab/bin/python` 3.11.16，
  torch 2.7.0+cu128 / CUDA 12.8。
- 隔离树：`/home/ljy/eai-simulator-worktrees/benchmark-stage62-r4-slice`
  （由 `git bundle` 流创建：本机 `git bundle create /tmp/stage62-r4-slice.bundle HEAD`
  → scp → ljy `git fetch bundle HEAD:refs/heads/benchmark-stage62-r4-slice` →
  `GIT_LFS_SKIP_SMUDGE=1 git worktree add`）。未触碰 ljy 主仓检出。
- 资产挂载（对 worktree 的补充，均为符号链接或环境变量）：
  - `EAI_USD_ROOT=/home/ljy/eai-simulator/usd`（主仓 18G 资产），
    并将主仓 `usd/*`、`usd/scene/*` 逐项符号链接进 worktree。
  - `EAI_NVD_ASSET_ROOT=/home/ljy/NVD_assets_sanitized`（已净化的 NVD
    道具 shadow 根，含 `XR_Content_NVD_v10010/...`；直接指向原始
    `usd/assets/nvd` 会因 `@10010` 目录名与注册表 `_v10010` 不一致而
    FileNotFoundError，见第 6 节阻塞记录）。
  - `EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller`。
  - `EAI_ASSETS_AUTO_DOWNLOAD=0`、`PYTHONUNBUFFERED=1`、
    `PYTHONPATH=<worktree>:<worktree>/source/EAI:.../source/EAI_assets:.../source/EAI_hmrs:.../source/EAI_env_diy`。
- 新增本仓库侧适配文件（未改引擎/runner，均未提交，属未跟踪工作区文件）：
  - `demo/benchmark/goal_r4_slice_driver.py`（sha256 前16：`c4b9d9b1c1ba3fb2`，
    完整值见运行报告 `runner_sha256`）
  - `source/EAI/test/test_benchmark_r4_slice_driver.py`
- 本机与 ljy 均先跑纯 Python 测试：
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q source/EAI/test/test_benchmark_r4_slice_driver.py source/EAI/test/test_branch_data.py`
  → **22 passed**（两侧一致）。

## 3. 运行命令原文

均在 ljy worktree 根、headless、`nohup` 后台执行（启动脚本
`tmp/r4_launch.sh`，日志 `tmp/r4_runs/*.log`）：

```bash
# (a) R4 切片驱动（新适配模块，复用引擎的 _configure_env/WarehouseCheckpoint）
/home/ljy/miniconda3/envs/env_isaaclab/bin/python -m demo.benchmark.goal_r4_slice_driver \
  --output tmp/r4_runs/r4_slice_driver
# 默认参数即预注册判据：settle 40 步、变化窗口 20 步、carter forward=0.25、
# restore_tolerance=1e-5、sham_min_drift=1e-3、sham_ratio=100、min_displacement=0.01 m

# (b) 引擎主入口最小原生运行（未修改的融合引擎，单槽 record-static-audit 模式）
/home/ljy/miniconda3/envs/env_isaaclab/bin/python -m demo.warehouse_benchmark.checkpoint_branches \
  --record-static-audit --slots 1 --disable-dormant-camera \
  --output tmp/r4_runs/engine_single_slot_audit \
  --aerial-steps 50 --scan-dwell-steps 10 \
  --restore-probe-steps 5 --branch-max-steps 600 --timeout 5400

# (b') 同命令、branch 预算放开后的复跑（验证 600 步只是切片参数截断）
#     实际以 cwd=tmp/r4_runs、相对 --output engine_single_slot_full 调用；
#     引擎 supervisor 以仓库根为 worker cwd，产物实际落于 <worktree>/engine_single_slot_full/
... checkpoint_branches --record-static-audit --slots 1 --disable-dormant-camera \
  --output engine_single_slot_full \
  --aerial-steps 50 --scan-dwell-steps 10 --restore-probe-steps 5 \
  --branch-max-steps 3600 --timeout 5400
```

## 4. R4 切片驱动结果（tmp/r4_runs/r4_slice_driver）

- 运行条件：`cuda:0` 实测生效（`actual_device=cuda:0`，
  `gpu_dynamics=True`），control dt 0.02 s，1 物理行，wall 13.9 s，
  进程峰值 RAM 7.6 GiB。
- 变化前快照：settle 40 步后在 control_step 40 捕获，写入
  `checkpoints/00000040.{json,npz}` 并从盘上校验回读（json+npz sha256
  门禁），物理态摘要
  `48d77ab66c1600b4a657c662136219e0147bdf571224513a8d6d4e97e6abb75a`；
  `store.verify_all()=[40]`。npz sha256
  `e2d5c342cf089994bb4e9c4f9205b5ac8aed2df675c32467ab0ac54edefaa781`；
  `r4_report.json` sha256
  `0ae34b81a0df41b5c2b84eca42502b74734deb4433628c995e205f44f14adc86`。

### 4.1 三臂数值（账本 + r4_report.json 原文数值）

| 臂 | slot/branch_type | 处治 | 变化窗口位移(m) | 处治前漂移 | 处治后读回 max-abs |
| --- | --- | --- | --- | --- | --- |
| R  | 0 / reference | 真实恢复 | 0.090639 | 10.2792 | **1.788e-07** |
| S1 | 1 / sham | 无恢复 | 0.090644 | 10.2792 | **10.2792**（不恢复） |
| S2 | 2 / sham | 无恢复（臂间先做一次记账的基础设施恢复） | 0.090644 | 10.2792 | **10.2792**（不恢复） |

- 读回逐域（sham 臂主导项，原文值）：`articulations/iris_1/dof_positions`
  10.2792、`articulations/carter_1/dof_velocities` 3.31416、
  `articulations/carter_1/dof_positions` 1.20133、
  `articulations/carter_1/root_velocities` 0.249982（即命令的 0.25 m/s）。
- 恢复臂逐域（全部域 max-abs 均 ≤ 1.79e-07，float32 量级）：
  `carter_1/root_transforms` 1.788e-07、`m20_1/root_transforms` 1.788e-07、
  `scenario_oversized_crate_proxy/transforms` 1.788e-07、
  `scenario_priority_carton_proxy/transforms` 1.192e-07。
- 非物理域：R 臂恢复后 RNG 四流（python/numpy/torch/cuda）全部 bitwise
  相等；环境缓冲（含步数计数器与命令字典）max-abs=0.0。sham 臂缓冲漂移
  80.0（=20 控制步 × decimation 4 的子步计数推进），RNG 流在变化窗口中
  未被消费（确定性控制器，见限制）。
- 复现性旁证：S1 与 S2 的位移与漂移逐位一致（0.09064421260196676 /
  10.279168128967285），说明恢复后的同快照开放链重放一致。

### 4.2 预注册判据（写在驱动常量与报告里，先于运行冻结）

| 判据 | 定义 | 实测 | 阈值 | 结果 |
| --- | --- | --- | --- | --- |
| CR-RESTORE | R 臂恢复后公开物理读回 max-abs | 1.788e-07 | ≤1e-5 | PASS |
| CR-SHAM | 每个 sham 读回 ≥1e-3 且 ≥100×R 臂误差 | 10.2792（两臂，比值 5.7e7） | ≥1e-3/≥100× | PASS |
| CR-CHANGE | 每臂变化窗口真实位移 | 0.0906/0.0906/0.0906 m | ≥0.01 m | PASS |
| CR-RNG | 恢复后 RNG 流 bitwise 相等 | 4/4 True | 全 True | PASS |
| CR-BUFFERS | 恢复后数值缓冲 max-abs | 0.0 | ≤1e-5 | PASS |

### 4.3 branch_data 账本结构与验证

- `manifest.json`：`batch_id=86255531-f703-448e-9b92-6f684f850469`，
  `scene_key=warehouse_nvd`，`task_id=W-DAG-01`，`requested=3`，
  `formal=false`，`execution_scope=measured`，`source_revision=38637db3b...`，
  metadata 写明 slot 语义=顺序处治臂而非并行物理容量。
- 行数：branches 3、steps 60（3 臂×20 步）、checkpoints 4、events 10、
  network 3、costs 3、episodes 3；`run_state.status=completed`。
- checkpoints.jsonl 关键行（字段原文）：
  - slot0/reference `r4_base_snapshot`：`restore_status=pending`、
    `restore_error=null`（捕获未恢复）；
  - slot0/reference `r4_real_restore`：`restore_status=measured`、
    `restore_error=1.7881393432617188e-07`、`restore_passed=true`、
    `restore_error_source=max_abs_public_physics_tensor_error`；
  - slot1、slot2/sham `r4_sham_no_restore`：`restore_status=unavailable`、
    `restore_error=null`（sham 无恢复测量，未把漂移伪装成 restore_error），
    payload 携带 `readback_max_abs_error=10.279168128967285` 与
    `change_window_displacement_m`。
- `validation.json`：`status=passed`、errors=[]、warning=
  "small-scale run is not evidence of formal 32-branch capacity"（如预期）。

## 5. 引擎主入口最小原生运行结果

### 5.1 第一次（branch 预算 600，tmp/r4_runs/engine_single_slot_audit）

- wall 230.98 s、峰值 RAM 7.0 GiB、`cuda:0`+GPU dynamics 生效。
- **原生前缀完整走完 W-DAG-01 九个任务节点**，6 个 checkpoint 全部落盘：
  rack_overview@60、aisle_access@655、oversized_crate_transfer@2087、
  priority_dispatch@3387、dock_confirmation@3597、mission_complete@4197；
  `checkpoint_store_verified=true`（全部文件 checksum 回读校验）。
- **引擎内建 R4 链（dock checkpoint）**：变化（`restore_probe_steps=5`
  对 carter 施加 forward=0.25）→ 真实恢复 → 读回
  `max_abs_error=2.384185791015625e-07`，`passed=true`（阈值 1e-5），
  `restored_task.checkpoint=dock_confirmation_complete`。
- 其 branch_data 账本 `validation.status=passed`（errors=[]），行数：
  branches 1、steps 600、checkpoints 2（dock 恢复 measured + mission
  pending）、events 1、network 2、costs 1、episodes 1。
- 未全绿项（如实记录）：单槽 closeout 分支在切片参数 `--branch-max-steps
  600` 内未到达终端区（`zone_error=3.319 m`，9 航点路线未走完），因此
  `completed=0`、`independently_verified=0`、report `passed=false`、
  stage=`incomplete`；crate/carton 道具误差 ~4e-07/5.5e-08（不受影响）。
  这是切片预算截断，不是链路或引擎缺陷；第 5.2 节放开预算复跑验证。
- 附带产物：`warehouse_static_audit.json`（record-static-audit 模式的
  静态写审计，可供后续多槽 shared-static 运行引用）。

### 5.2 放开 branch 预算的复跑（branch_max_steps=3600）

- **结果：`stage=finished`、`passed=true` 全绿**。wall 255.7 s、峰值 RAM
  7.1 GiB。
- 原生前缀同样完整（六个 checkpoint 步号与 5.1 一致：
  60/655/2087/3387/3597，mission_complete@4623），
  `checkpoint_store_verified=true`。
- 引擎内建 R4 链同样通过：恢复读回 `max_abs_error=2.384185791015625e-07`
  （`passed=true`）。
- 单槽 closeout 分支在 branch step 1026 到达终端区：
  `zone_error=0.4497 m ≤ 0.5`、`target_error=0.4497 m`、
  `crate_error=3.97e-07`、`carton_error=5.48e-08`，
  `completed=1/1`、`independently_verified=1`。
- branch_data `validation.status=passed`：branches 1、steps 1026、
  checkpoints 2（dock 恢复 measured+passed、mission pending）、events 2、
  network 2、costs 1、episodes 1。
- 这验证了 5.1 的未全绿只是切片参数截断：同一命令仅把
  `--branch-max-steps` 从 600 放开到 3600 即全绿。
- 运行位置注意（如实记录）：该复跑误用了相对 `--output`；引擎的
  supervisor 以仓库根为 worker cwd，实际产物落在
  `<worktree>/engine_single_slot_full/`（而非 `tmp/r4_runs/` 下），r4_runs
  下同名目录只含 supervisor 的 fallback report（"worker produced no
  report"，worker_exit=0）。此为调用侧参数问题，非引擎缺陷；不影响
  上述数值，它们取自真实产物目录的 report.json/branch_data。

## 6. 阻塞与修复记录（如实）

1. 首次启动两个入口均立即失败：
   `FileNotFoundError: Warehouse native scenario props are unavailable before
   Isaac startup: ['cardbox_c', 'wooden_crate_a']`。原因是
   `EAI_NVD_ASSET_ROOT` 指向原始下载 `usd/assets/nvd`（目录名
   `XR_Content_NVD@10010`）而道具注册表 `usd_rel` 使用净化名
   `XR_Content_NVD_v10010`（`@` 是 USD 资产路径保留分隔符，
   `tools/assets/prepare_nvd_props.py` 负责重命名）。修复：改指 ljy 已有
   净化 shadow 根 `/home/ljy/NVD_assets_sanitized`，无需改动任何引擎或
   注册表语义。
2. 其余无阻塞：资产、控制器、地图全部本地命中，未触发网络下载。

## 7. 支持范围与不可恢复/未覆盖域（归档限制）

支持（本切片实测读回）的原生状态子集：
- 全部 articulation 的 root transforms/velocities 与 dof positions/velocities
  （iris_1/m20_1/carter_1）；
- 全部 rigid object 的 transforms/velocities（oversized_crate、
  priority_carton 代理）；
- 控制器字典、执行器状态、replay 资源（restore 内部 `validate_state`
  结构校验通过，未单独数值化）；
- 环境缓冲（含 `episode_length_buf`/`common_step_counter`/
  `_sim_step_counter` 与 `actions_dict`/`_commands_dict`）；
- python/numpy/torch/cuda RNG 流。

不可恢复（设计排除，引引擎 docstring 原文语义）：
- **私有 PhysX solver/contact/warm-start 缓存**——公开 checkpoint 按设计
  不含；因此"恢复后继续演化与不恢复的轨迹完全一致"不在本切片结论内，
  严格同快照分支结论仍受此限制。

未覆盖（本切片没有参与者/通信/渲染面）：
- 参与者有效状态与通信队列（无参与者配置；network 行记 0 并带 scope
  说明）——R4 出口中"参与者状态恢复有实测证据"**尚未**满足；
- 渲染/相机状态（dormant camera 关闭；checkpoint 仅含 NullCamera 代计数）；
- 墙钟时间、ROS 桥（本切片禁用）；
- RNG 在变化窗口中的消费（确定性控制器不消费，sham 臂无法展示 RNG
  漂移；RNG 恢复仅验证 setstate 后 bitwise 相等）。

## 8. 与 R4 出口的差距清单

| 出口要素 | 本次状态 |
| --- | --- |
| 支持范围明确的原生状态子集 | 已选定并实测（物理张量+缓冲+RNG） |
| 变化前快照→短窗口变化→真实恢复→即时读回 | 引擎链与切片链均实测通过（2.38e-07 / 1.79e-07 ≤ 1e-5） |
| 两次无处理 sham | 已实测（漂移 10.28，比值 5.7e7，预注册判据 PASS） |
| sham 差异满足预注册判据 | 满足（判据先冻结于驱动常量与报告） |
| 原生恢复实测证据 | 满足（引擎主入口 + 切片驱动两路） |
| 参与者状态恢复实测证据 | **未满足**（本切片无参与者；需后续在含参与者的场景补） |
| 不可恢复域归档限制 | 已归档（PhysX 私有缓存等，见第 7 节） |
| 结论等级 | 诊断性 measured 证据；非 formal（requested=3≠32），release 标志语义保持 false |

## 9. 工件位置（ljy）

```text
/home/ljy/eai-simulator-worktrees/benchmark-stage62-r4-slice/
  tmp/r4_launch.sh                                   启动脚本（环境配方原文）
  tmp/r4_runs/r4_slice_driver/                       切片驱动运行
      r4_report.json  checkpoints/  branch_data/{manifest,run_state,validation}.json ...
  tmp/r4_runs/engine_single_slot_audit/              引擎 600 步预算运行
      report.json  worker.log  warehouse_static_audit.json  branch_data/  checkpoints/
  engine_single_slot_full/                           引擎放开预算复跑（全绿；相对路径产物实际落于此）
      report.json  worker.log  branch_data/  checkpoints/
```

## 10. 与 5.1 未全绿项的对照结论

600 步预算运行与 3600 步复跑的唯一差别是 branch 预算；两次的引擎内建
R4 链（快照→变化→恢复→读回）数值一致（同为 2.384e-07），说明该链证据
稳定，不依赖 branch 阶段是否走完。
