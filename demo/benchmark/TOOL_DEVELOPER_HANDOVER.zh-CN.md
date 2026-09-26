# Benchmark 主线 ↔ Branch Tool 开发者交接文档

日期：2026-09-26  
主线分支：`codex/benchmark-stage57-oracle-integration`（推送目标 `benchmark-task`，HEAD `cc6ec50`）  
Tool 工具分支：`benchmark-branch`（HEAD `5d2181e`，主线未改动该分支）  
文档目的：向 benchmark-branch tool 开发者交接主线侧的使用状态、已发现的问题与建议修改清单

---

## 1. 当前融合状态

### 1.1 已融合内容

benchmark-branch 的 4 个独有提交（`3a13cc5` 工具本体 / `7ce8a89` 依赖补齐 / `2fc2d35` merge / `5d2181e` 视觉克隆修复）已于 2026-09-25 融合进主线，共 63 文件 ~15.7k 行，**零冲突**（双方变更集文件级不相交）。

主线当前使用的工具模块：

| 模块 | 行数 | 主线使用状态 |
|---|---|---|
| `demo/benchmark/branch_data.py` | 988 | 已融合；主线侧 `goal_condition_branch_plan.py` 消费其 API 做 405 条件映射 |
| `demo/benchmark/protocol_runner.py` | 557 | 已融合；作为六维度协议 runner 参考骨架（评分语义按最新协议另建） |
| `demo/benchmark/visual_static.py` | 139 | 已融合；测试通过 |
| 五场景 `checkpoint_branches.py` | 559-1845 | 已融合；warehouse R4 最小切片已在 ljy 原生验证（恢复 1.79e-07 vs sham 10.28） |
| 五场景 `branch_data_adapter.py` | 119-463 | 已融合；restaurant adapter 停留在 authoring 语义（详见 §3.2） |
| `tools/physics_replay/` 增强 | ~1k | 已融合；budget/memory_profile/contact_reports/wrench_state |
| 五个 `benchmark_*.json` 场景注册 | 各 32 | 已融合；Env DIY 集成通过 |
| 测试（test_branch_data 等 4 份） | ~700 | 已融合；20 passed |

### 1.2 已搁置范围（用户决定，非工具问题）

用户已明确**搁置 branch 工具的全部功能**（含快照恢复+sham+32 分支并行），先推进协议指标主线。搁置原因是优先级调度（"先推进其他内容"），不是工具有缺陷。涉及此功能的批次暂停，已落地代码和验证保留。

搁置边界：
- **搁置**：32 分支并行批执行、checkpoint 恢复/sham 实验、配对反事实分支
- **不删除**：已融合的代码/测试/ljy 验证证据全部保留，随时可恢复
- **影响**：协议中依赖配对分支的指标（§3.4 同快照配对差、§3.5 学习 R/C 环境回档、§7 探针 fact/sham/intervention）暂不可算

---

## 2. 主线侧已验证的工具能力

### 2.1 R4 最小切片（warehouse，ljy cuda:0）

**已证明**：checkpoint 捕获→变化→真实恢复→读回→双 sham 完整链路原生可用。

| 预注册判据 | 结果 | 说明 |
|---|---|---|
| CR-RESTORE 恢复读回 | 1.79e-07 ≤ 1e-5 | **通过** |
| CR-SHAM 两次无处理漂移 | 10.279 ≥ 1e-3 | **通过**（恢复误差的 5.7e7 倍，清晰区分） |
| CR-CHANGE 每臂位移 | 0.0906 m | 预期内 |
| CR-RNG 四流 bitwise | 相等 | **通过** |
| CR-BUFFERS | 0.0 | **通过** |

引擎主入口 3600 步复跑**全绿**（256s，zone_err 0.45 ≤ 0.5），两次恢复链数值一致。

证据报告：`demo/benchmark/STAGE62_R4_MINIMAL_SLICE.zh-CN.md`

### 2.2 分支类型命名

`branch_data.py` 的 `BRANCH_TYPES = {"fact", "reference", "sham", "intervention"}` 与最新测评协议 §2.6 的四类分支命名**完全一致**——这是一个重要的正面发现，不需要改名。

### 2.3 405 条件映射

主线 `goal_condition_branch_plan.py` 已实现 405 条件/315 对到 32 分支批的映射（配对共基+独立样本=基态数），等待工具恢复执行后可直接消费。

---

## 3. 发现的问题与建议修改清单（待你审核）

以下三项是主线侧在融合和使用过程中发现的具体问题。**主线未修改 benchmark-branch 分支**，按用户要求，修改需先经你审核。

### 3.1 LFS 对象不在 git bundle 中

**问题**：五个场景缩略图（`usd/picture/scene/{city,factory_benchmark,modern_house_nvd,restaurant,warehouse_nvd}.png`）是 Git LFS 对象。ljy 测试机无法直连 GitLab（需 bundle 离线同步），但 `git bundle` 不包含 LFS 对象，导致 ljy 检出时 LFS smudge 失败。

**主线侧当前绕法**：`GIT_LFS_SKIP_SMUDGE=1` 建树（跳过 LFS 下载，PNG 为指针文件）。测试不受影响，但缩略图不可用。

**建议修改**（二选一）：
- A. 把五个 UI 缩略图改为非 LFS 小文件（如果实际尺寸 <100KB）
- B. 在发布清单中附加 LFS 对象包（`.git/lfs/objects/` 打包）

### 3.2 Restaurant adapter 停留在 authoring 语义

**问题**：`demo/restaurant_benchmark/branch_data_adapter.py`（159 行）中 restaurant 的 `BranchSceneAdapter` 实现声明"scene-lifecycle probe; Restaurant business task remains authoring-only"。这是基于 Stage35 前的旧餐厅栈。

主线侧餐厅已大幅演进（Stage51-60）：
- 公开策略运行入口（`goal_restaurant_public_run.py`，八机原候选池）
- 初始正向可行性 oracle v2 证书+校准域
- 业务事实与结果可见性分层
- 原生异步执行 backend

**建议修改**：待主线侧对齐调用面后，可能需要 benchmark-branch 侧更新 restaurant adapter 的 `task_status()`/`scene_payload()` 以匹配公开运行入口。届时主线会提供精确 diff 给你审核。

**优先级**：中（当前搁置中，不紧急）

### 3.3 `execution_scope` 词表建议对齐（可选）

**问题**：`branch_data.open_batch()` 的 `execution_scope` 枚举为 `{"measured", "protocol_synthetic", "pending", "failed"}`。主线侧使用两轨制标记（`development_track_marker.py`）：`asset_status ∈ {frozen_verified, development_unverified}` + `evidence_class ∈ {raw, diagnostic, contract}`。

**建议**（可选，非阻塞）：对齐词表，或在 `open_batch` 增加 `evidence_class` 可选参数，使两套语义不漂移。主线侧可以自己写适配层，不需要 tool 侧必须改。

---

## 4. 融合过程中已修复的主线侧问题

以下问题出在主线侧的融合操作或已有代码中，已由主线修复，**不影响 benchmark-branch 分支**：

| 问题 | 修复 | 主线提交 |
|---|---|---|
| `simulator.py` 传 `publish_ros` 给 `AerialSensorSuiteManager`（签名不匹配导致含空中机器人的启动崩溃） | 主线 `aerial_sensor_suite.py` 增加 `publish_ros` 参数（独立门禁语义：物理传感器保留、ROS 图门控） | `210ffc3` |
| `asset_requirements.py` 餐厅场景需求从单文件变三文件（usda+assembly+demopack） | 主线更新 `test_restaurant_benchmark.py` 断言 | `9c5667d` |
| 融合提交曾误吸保护文件（`my_env.json`+会议纪要） | 推送前 amend 摘除 | `4d01e94`（amend 后） |

---

## 5. 主线当前不需要 tool 侧做的事

以下内容主线已自行完成或有替代方案，**不需要 benchmark-branch 侧配合**：

- 通信容量实验（JP/IID/BP/C90/C50）：主线 `communication_capacity_experiments.py` + `goal_communication_metrics.py` 已独立实现
- 并发证据：主线 `goal_concurrency_evidence.py` + runner 异步模式已独立实现
- 重评估 oracle：主线 `goal_restaurant_reassessment.py` + 注入链已独立实现
- 评分侧车/聚合器/认证入口：主线 `goal_scoring_adapter.py` / `goal_metric_aggregation.py` / `goal_qualification_attestation.py` 已独立实现
- 协议六维度离线指标（Q(t)/五率/CS 向量/Prec-Sync/停滞/KM-RMST/通信任务表现/成本三分量）：全部在主线独立落地

---

## 6. 恢复使用时的对接点

当用户解除搁置时，主线侧需要 tool 侧提供/配合的接口：

| 接口 | 用途 | 状态 |
|---|---|---|
| `branch_data.open_batch / resume_batch / validate_run` | 405 条件批执行 | API 已融合，主线计划已就绪 |
| `BranchSceneAdapter` 协议 | 场景 runner 对接 | warehouse 已验证，restaurant 需更新（§3.2） |
| `checkpoint_branches` 引擎 CLI | R4 分支实验 | warehouse 已全绿 |
| `physics_replay` 增强（budget/memory/contact/wrench） | 回溯物理测量 | 已融合 |
| 32 分支批的 GPU 并行调度 | 多条件并行运行 | 已验证可行（ljy RTX 5080） |

---

## 7. 联系与版本

- 主线维护者：ZCode 主任务（受用户 Huang Qijun 指导）
- Tool 维护者：Gengbiao Lin（benchmark-branch 分支作者）
- 当前共同上游：GitLab `roso-lab/eai-simulator` `benchmark-task` 分支
- Tool 分支：`benchmark-branch`（`5d2181e`），主线融合基于此版本
