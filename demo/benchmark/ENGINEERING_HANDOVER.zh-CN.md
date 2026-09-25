# EAI Simulator Benchmark 工程交接文档

日期：2026-09-26  
分支：`codex/benchmark-pose-telemetry`（推送目标 `benchmark-task`）  
HEAD：`f249048`  
编写：ZCode 主任务（goal 循环暂停时的阶段性交接）

---

## 1. 用户核心原则（不可违反）

以下原则由用户在会话过程中逐条确立，是所有开发决策的最高约束。

### 1.1 出题人视角（考官纪律）

- **不限制团队规模**：用多少机器人是算法的决策，协调成本是测量信号。
- **不预过滤能力错配**：选队/交计划免费，派遣执行才计失败且单任务级。
- **低层控制黑名单只限接口级**：被测模型只能发高层目标动作（6 动作词汇），不得直接控制关节/速度。
- **不把考生能力当考场基础设施要求**：考场的问题（通信、时钟、恢复）不转嫁考生。
- **作者 witness 不是唯一合法路径**：remaining-task oracle 判断"从可信当前状态是否存在任一合法完成方案"。
- **能力已经不再必要时仍 deployable**：如书堆已搬完，搬运成员退出不影响 deployable。
- **不瞬移、不放宽 reach_m、不伪造 attachment、不改业务评分。**

### 1.2 两条轨道开发模式

- **开发轨道**：允许 development_unverified 资产、合成状态、contract-only 后端；所有结果强制携带开发态标记。
- **发布轨道**：只使用冻结资产和正式 provider；Factory 冻结哈希不匹配保持 blocked。
- 三个发布标志 `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false` **全程保持 false** 直到正式门禁全过。
- **`unverified` 只是平台证据状态**，不能传给被测模型、不能进入正式统计。

### 1.3 设备策略

- **场景任务的并行运行统一走 GPU 槽（cuda:0）**——这是 benchmark-branch 工具的并行优化设计（32 分支共享 GPU）。
- ljy 是专用测试机（RTX 5080），本机持续推进框架。
- 本机可并行两个 headless 测试但不在测试过程中等待。
- 旧的 CPU-for-evidence 纪律仅适用于明确要求逐位可复现的单跑证据（且 run profile 如实记录设备）。

### 1.4 保护文件（绝不提交/修改）

- `source/EAI_hmrs/EAI_hmrs/envs/my_env.json`
- 根目录 `异构多机器人高层协作智能+Benchmark+能力分类与评测协议.md`
- `demo/benchmark/BENCHMARK_TASK_GENERATION_REVIEW_MEETING_20260924.zh-CN.md`
- 冻结题包/见证计划/冻结 hash/fixture 不可修改

### 1.5 并行/回溯工具搁置

用户已明确搁置 benchmark-branch 工具的**全部功能**（含快照恢复+sham），先推进其他内容。涉及此功能的批次暂停，但不删除已落地的代码和验证。

---

## 2. 协作工作流

### 2.1 Goal 循环模式

用户要求开启 goal 模式持续推动搭建，循环结构：

```
工程子代理交付 → 主任务验收提交 → viewer 阶段审核 → P0 修复 → 推送 → 下一件
```

- 每阶段交付交 viewer 审核（viewer 是用户配置的专用审核代理类型）。
- viewer 发现 P0 → 主任务修复 → 回归测试 → 推送。
- viewer 可用的一般性审计问题问 viewer；具体代码测试走 general-purpose 子代理或主对话。

### 2.2 子代理分工纪律

- **工程子代理**（general-purpose）：指定文件范围（只创建新文件或限定可修改清单）、验收标准、测试命令。
- **viewer 子代理**：只读审核，输出中文报告（总判定/P0 清单/证据）。
- 不同代理不同时修改同一核心文件。
- 代理失败或限流时不重复启动同一任务；由主任务接管。

### 2.3 测试流程

**本机轻量全量**（最常用）：
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test
```

**ljy 双机验证**（bundle 流，ljy 无 GitLab 直连）：
```bash
# 本机
git bundle create /tmp/x.bundle HEAD
scp /tmp/x.bundle ljy:/home/ljy/tmp/

# ljy
cd /home/ljy/eai-simulator
git fetch /home/ljy/tmp/x.bundle HEAD:refs/heads/<branch-name>
GIT_LFS_SKIP_SMUDGE=1 git worktree add \
  /home/ljy/eai-simulator-worktrees/<branch-name> <branch-name>
cd /home/ljy/eai-simulator-worktrees/<branch-name>
# 资产挂载（新 worktree 必须做）
M=/home/ljy/eai-simulator
for sub in usd usd/scene; do for d in "$M/$sub"/*; do
  n=$(basename "$d"); [ -e "$sub/$n" ] || ln -s "$d" "$sub/$n"; done; done
mkdir -p tmp; for d in "$M"/tmp/*; do
  n=$(basename "$d"); [ -e "tmp/$n" ] || ln -s "$d" "tmp/$n" 2>/dev/null; done
export EAI_USD_ROOT=$M/usd
export EAI_CONTROLLER_ROOT=$M/source/EAI_assets/EAI_assets/controller
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs:source/EAI_env_diy
/home/ljy/miniconda3/envs/env_isaaclab/bin/python -m pytest -q source/EAI/test
```

**关键坑**：
- ljy remote 是本地 bundle 文件，不是 GitLab
- 不要 bundle 本地旧同名分支 `benchmark-task`（存在 dd995a25 旧提交）
- bundle 不含 LFS 对象 → 用 `GIT_LFS_SKIP_SMUDGE=1` 建树
- 新 worktree 需要符号链接 usd/scene/tmp 资产
- `EAI_NVD_ASSET_ROOT=/home/ljy/NVD_assets_sanitized`（原始下载名与注册表净化名不匹配）

### 2.4 Git 提交纪律

- 分批正常推送 GitLab `benchmark-task`，不 force push（除非 amend 后确实需要）。
- 推送前核对：当前分支、远端 benchmark-task、工作区未提交修改、运行中代理、用户文件保持原样。
- 阶段出口先写交接文档再提交源码。

---

## 3. 测评协议（最新版）

**文件**：`/home/pupil/下载/异构多机器人高层协作智能+Benchmark+能力分类与评测协议(1).md`（2476 行）

### 3.1 六个协作能力维度

| 维度 | 英文 | 核心问题 |
|---|---|---|
| 任务组织 | Task Decomposition and Allocation | 把高层目标组织成谁做什么 |
| 通信适应 | Communication Adaptation | 通信变差后还能否协作 |
| 时序协同 | Temporal Coordination | 任务何时执行以及如何同步/互斥/交接 |
| 动态重构 | Dynamic Reconfiguration | 执行中任务或团队突变后能否重新组织 |
| 经验学习 | Cross-Episode Learning | 同一场景或任务多轮执行后是否产生可验证改进 |
| 成本效率 | Coordination Cost Efficiency | 在取得有效进展的同时付出了多少高层资源和协调开销 |

### 3.2 关键协议概念

- **任务合同**（§2.6）：contract_milestones（weight/stability/activation/anti_double_count）+ capability_constraints（aggregation_rule 四值）+ role_constraints + deployment_oracle + assistance_policy + snapshot_schema
- **Q(t) 进展轴**（§2.6）：加权里程碑完成率，所有维度的共同结果轴
- **部署判断**（§3.1）：oracle 真值 `team_state_infeasible` vs 系统声明 `not_deployable`；五率+assessment_latency+三维约束向量
- **统计纪律**（§2.6）：base instance 是统计单位、实例内先均值再跨实例、Wilson CI 在实例级、配对分层 bootstrap、右删失双报告、N/A 不零值
- **配对分支**（§3.4/7）：同快照 fact/reference/sham/intervention——**已搁置**（branch 工具功能）
- **探针**（§7）：I-role/I-timing/I-comm/I-skill——需要配对分支——**已搁置**

### 3.3 协议↔实现交叉映射

详见 `demo/benchmark/PROTOCOL_CONFORMANCE_MATRIX.zh-CN.md`（130 行逐节映射）。

---

## 4. 当前代码架构

### 4.1 协议指标层（批次 0-3 + 聚合器）

| 模块 | 批次 | 功能 | 测试 |
|---|---|---|---|
| `goal_protocol_deployment.py` | 0 | 真值词双读+部署五率+四约束失败标签 | 13 |
| `goal_progress_curve.py` | 1 | Q(t)/MCR/AUP/PGI/nPGI 等权 v1 | 12 |
| `goal_contract_v4.py` | 2A | v4 schema+零默认迁移器+封印 | 26 |
| `goal_cs_vector.py` | 2B | committed_plan 提取+CS 三维判定+predicate 注册表 | 23 |
| `goal_temporal_metrics.py` | 3 | 时间锚冻结+Prec/Sync/交接/互斥/等待/停滞状态机 | 27 |
| `goal_communication_metrics.py` | — | §3.2 五实验族任务表现指标 | 17 |
| `goal_survival_analysis.py` | — | KM/RMST/学习 R/C 增益 | 21 |
| `goal_metric_aggregation.py` | — | 跨 episode 聚合器+来源门禁 | 34 |
| `goal_qualification_attestation.py` | — | R5 认证入口（pending/blocked 无 approved） | 21 |

### 4.2 场景层（五场景 reader + oracle）

| 模块 | 场景 | 功能 |
|---|---|---|
| `goal_reader_house.py` | modern_house_nvd | 合成后端 reader+替代路径证书 |
| `goal_reader_city.py` | city | 同上 |
| `goal_reader_warehouse.py` | warehouse_nvd | 同上+资源互斥对照 |
| `goal_reader_factory.py` | factory | contract-only+哈希不匹配标记 |
| `goal_reader_restaurant.py` | restaurant | 单机/多机并行替代+时间预算 |
| `goal_restaurant_initial_oracle.py` | restaurant | 初始正向可行性 oracle（v2 证书+校准域） |
| `goal_restaurant_reassessment.py` | restaurant | 运行中重评估 oracle |
| `goal_restaurant_probe_evidence.py` | restaurant | 归档→判卷证据推导 |

### 4.3 执行层

| 模块 | 功能 |
|---|---|
| `goal_episode_runner.py` | 正式 episode runner（串行/异步模式） |
| `goal_concurrency_evidence.py` | 并发证据（fake backend 四类竞争+runner 级） |
| `goal_restaurant_async_backend.py` | 原生异步 Isaac backend |
| `goal_restaurant_public_run.py` | 可复现公开策略入口 |
| `goal_deployment_feasibility.py` | 部署可行性诊断 |

### 4.4 评分与归档层

| 模块 | 功能 |
|---|---|
| `goal_scoring_adapter.py` | 评分侧车（协议部署/通信/成本/发展轨标记） |
| `goal_independent_recomputation.py` | 独立复算（进展/部署/通信/成本四族） |
| `goal_declaration_cost_snapshot.py` | 声明边界成本快照 |
| `goal_cost_evidence.py` | 原始成本证据 |

### 4.5 工具层

| 模块 | 功能 | 状态 |
|---|---|---|
| `communication.py` | 三池通信运行时+回放验证 | 活跃 |
| `communication_capacity_experiments.py` | 离线容量实验（JP/IID/BP/C90/C50） | 活跃 |
| `branch_data.py` | 32 分支账本+生命周期 | **已搁置** |
| `protocol_runner.py` | 六维度协议 runner | 参考实现 |
| 五场景 `checkpoint_branches.py` | checkpoint/回溯引擎 | **已搁置** |
| `physics_replay/` | 物理回放增强 | **已搁置** |

---

## 5. 已完成的里程碑

### 5.1 Stage 31-33（两条轨道+四场景 reader）

- 两条轨道模式确立与落地（开发态标记+评分侧车集成）
- 四场景 remaining-task reader（合成后端+替代路径证书+证书门禁入发布链）
- 发布单元=逐场景（ Factory 资产阻塞不死锁他人）
- 餐厅审计文档+判卷证据链（归档重放四测量点 verified/deployable）

### 5.2 Stage 34-60（codex 主线）

- 通信容量 v4 字节口径、部署分母修复、City 原生准入/到达（业务 3/4）
- 餐厅初始正向 oracle v2 证书+校准域、公开策略入口、通信条件绑定
- 双机各阶段回归全绿

### 5.3 Stage 61-63（ZCode 接手后）

- benchmark-branch 工具融合（63 文件零冲突）
- R4 最小切片原生闭合（恢复 1.79e-07 vs sham 10.28 差 7 个数量级）→ 后搁置
- runner 异步执行路径+原生并发（真实重叠 12.82s+机队互斥）
- 重评估注入链（合成端到端 verified/deployable）

### 5.4 Stage 64-67（协议符合性批次推进）

- 协议全文审读+交叉映射矩阵（130 行）+路线校准
- 批次 0-3 + 聚合器 + 通信指标 + KM/RMST 全部落地
- 六维度离线指标面全部就位
- viewer 循环审核+P0 修复多轮

---

## 6. 当前遗留工作

### 6.1 高优先级（viewer 已标记）

| # | 内容 | 来源 |
|---|---|---|
| 1 | N/A 尾点保持率/容量阈值边界（P0-3） | viewer stage67 |
| 2 | 通信维度接线契约（P0-5） | viewer stage67 |
| 3 | 动态重构维度指标映射（§3.4 规模响应/失效恢复） | conformance matrix |
| 4 | 学习 R/C 维度接线到聚合器 | conformance matrix |
| 5 | 聚合器三维（通信/动态重构/学习）从 na_dimension_source_not_wired 升级 | aggregator 遗留 |

### 6.2 中优先级

| # | 内容 |
|---|---|
| 6 | predicate registry 与时序指标事件规范打通 |
| 7 | 合同 v4 题包实际生成（当前只有 schema+迁移器，尚无 v4 题包） |
| 8 | 正式 episode 的任务流量流经共享账本（解除 communication_formal_episode_pending） |
| 9 | KM/RMST 在真实停滞数据上的首次应用 |

### 6.3 低优先级/需用户决策

| # | 内容 |
|---|---|
| 10 | 里程碑权重从等权 v1 升级（需拍板） |
| 11 | 评分定义矩阵剩余 ~30 项参数拍板 |
| 12 | Factory 冻结资产哈希（外部阻塞） |
| 13 | 餐厅 GPU 物理域兼容（tool 工程师处置） |
| 14 | Stage60 v2 证书补 GPU profile 校准 |

### 6.4 已搁置（用户决定）

- benchmark-branch 工具全部功能（32 分支并行、checkpoint 恢复、sham、探针）
- R4 分支实验扩展
- R3 条件批执行计划（goal_condition_branch_plan 的执行侧）

---

## 7. 关键文档索引

| 文档 | 位置 |
|---|---|
| 最新协议 | `~/下载/异构多机器人高层协作智能+Benchmark+能力分类与评测协议(1).md` |
| 协议↔实现交叉映射 | `demo/benchmark/PROTOCOL_CONFORMANCE_MATRIX.zh-CN.md` |
| 路线校准 | `demo/benchmark/STAGE64_PROTOCOL_CALIBRATION.zh-CN.md` |
| 发布路线图（R1-R5） | `demo/benchmark/RELEASE_ROADMAP.zh-CN.md` |
| 评分定义矩阵（44 参数） | `demo/benchmark/SCORING_DEFINITION_MATRIX.zh-CN.md` |
| 拍板决策摘要 | `demo/benchmark/SCORING_DECISION_DIGEST.zh-CN.md` |
| 工具融合分析 | `demo/benchmark/STAGE61_BRANCH_TOOL_FUSION.zh-CN.md` |
| 真实归档端到端 | `demo/benchmark/STAGE66_REAL_ARCHIVE_E2E.zh-CN.md` |
| 餐厅准入审计 | `demo/benchmark/STAGE32_RESTAURANT_ADMISSION_AUDIT.zh-CN.md` |
| 部署门禁接口 | `demo/benchmark/DEPLOYMENT_ASSESSMENT_INTERFACE.zh-CN.md` |
| 算法接入契约 | `demo/benchmark/ALGORITHM_HANDOFF.zh-CN.md` |
| 阶段 31-67 历史 | `demo/benchmark/STAGE*.zh-CN.md`（约 40 份） |
| viewer 审核报告 | `tmp/*_viewer.md`（多份） |

---

## 8. 恢复工作时的快速启动

1. **同步**：`git fetch origin benchmark-task` 确认远端最新
2. **检查工作区**：`git status --short` 确认保护文件未被意外提交
3. **跑轻量全量**：确认基线健康（预期除 Factory 哈希外全绿）
4. **读路线**：STAGE64（协议校准批次）→ viewer 最新审核报告 → 本文档 §6 遗留清单
5. **启动 goal 循环**：按 §2.1 工作流和 §6 优先级继续
