# HeteroCoord 工程交接说明

## 目标

本分支是在 EAI Simulator 基础能力上建立的异构多机器人测评骨架。它提供
任务合同、算法适配边界、EAI 执行注入点、物理证据判定和回放接口。它不携带
USD、下载的 checkpoint、实验输出或 demo 场景代码。

当前工作区中的 `demo/` 和 `usd/` 删除是有意的；场景资源由 EAI 资产解析器
从本地资产根目录或 provider 提供。不要为了让单元测试通过而恢复它们。

## 目录所有权

```text
simulator.py             EAI 基础启动和 open_simulator_session API
source/EAI/              EAI core、controller contract、ROS/interface
source/EAI_assets/       资产配置和 provider resolver
source/EAI_hmrs/         saved environment 和动态 env builder
algorithm/system/        系统级算法的规范分类和 registry
algorithm/skills/        底层运动/控制 provider 的规范合同和 registry
algorithm/system/coordination/emos/
                         被测 EMOS 系统算法本体
algorithm/system/coordination/TeamWeaver/
                         被测 TeamWeaver 系统算法本体
algorithm/skills/navigation/
                         导航 Skill provider 上传位置（当前为空）
algorithm/skills/external_io/
                         外部 ROS/输入 Skill provider 上传位置（当前为空）
algorithm/common_coordination/
                         算法和任务层共用的无 Isaac 合同
algorithm/heterocoord/   provenance registry 和 Fire Rescue decision ledger
task/config/             与场景解耦的任务合同和机器人能力输入
task/fixtures/           无 Isaac 的符号化适配器测试输入
task/contracts.py        对外数据类型
task/runner.py           算法 → EAI executor → evaluator 编排
task/executors/          Assignment/SkillPlan → EAI controller/skill（后续开发）
task/evaluators/         物理 milestone 和终止谓词（后续开发）
task/replay/             checkpoint、分支和并行回放注入点
task/tests/              不启动 Isaac 的合同测试
tools/                   安装、验证和 ROS 运维工具
```

## 不可违反的边界

1. `algorithm/system/` 不创建 Isaac App，不读取 PhysX/joint handle，不调用 EAI
   skill，也不写入物理成功结果。`algorithm/skills/` 可以计算路径、轨迹或命令，
   但同样不能推进 simulator、直接应用 controller 或伪造物理成功。
2. `task/runner.py` 不创建 simulator。真实运行时由调用者先创建
   `open_simulator_session(SimulatorLaunchConfig(env=...))`，再注入
   `scene_provider`、`SkillExecutor` 和 `EvidenceEvaluator`。
3. `Assignment`、规划成功、进程退出码和回放成功都只是算法或运行事件。
   只有 evaluator 提供的物理证据可以把 verdict 置为 `success`。
4. 没有 executor 或 evaluator 时，结果必须是
   `status=N/A, reason=physical_evidence_required`。
5. 分支必须显式使用 `TaskRunRequest.branch_id/checkpoint`，不得从算法内部
   读取隐藏 checkpoint 或复用事实分支的 milestone。

## 当前算法状态

可作为任务分配适配器使用的系统算法目录包括 EMOS、TeamWeaver、COHERENT、FMC-ATA、
BT capabilities、MRTA heuristic、Hybrid SUSD、DCMRTA 和 HeteroMRTA。它们
均只返回 assignment/plan。导航、db-CBS、Nav2 和 keyboard provider 当前由专门的
Skill 开发者负责上传，暂不随本分支提供。Z1、抓取和物理判定属于后续
EAI executor/evaluator。

DCMRTA/HeteroMRTA 的官方 checkpoint 只在原论文输入分布上做 decision-only
轨道；Fire Rescue 物理轨道使用任务合同声明的默认分配，并将跨域 checkpoint
泛化记为 `N/A`。FMC-ATA 在不可拆分的 Fire Rescue skill 上同样只保留默认分配
以验证执行链路，fractional allocation 指标记为 `N/A`。

没有作者源码或可核验官方权重的候选算法不放入运行目录；文献来源可在
`algorithm/heterocoord/registry.py` 留下 `N/A` 记录。HAPPO 目前只有相关工作
记录，没有可核验的 EAI 官方 checkpoint，不进入测评矩阵。

## 推荐开发顺序

1. 先运行无 Isaac 的合同测试，确认任务和算法输出可序列化。
2. 在 `task/executors/` 实现 `Assignment -> SkillRequest -> SkillPlan -> EAI
   controller/skill`，只使用已存在的 `SimulatorSession.base_env` 和 controller
   contract。系统算法不要反向调用 executor。
3. 在 `task/evaluators/` 读取 EAI 物理状态、里程碑和失败原因，写入独立
   evidence；不得让算法自行判定成功。
4. 在 `task/replay/` 接入现有并行回溯工具，保证每个 branch 使用独立 ledger
   和 checkpoint 元数据。
5. 再用固定 `source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-Z1-v0.json`
   做单环境 Isaac smoke；确认后才扩展场景或并行度。

## 最小验证

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m pytest -q task/tests algorithm/common_coordination/tests

python -m py_compile simulator.py task/contracts.py task/runner.py
bash -n tools/setup/install_packages.sh
```

完整 Isaac 验证必须在 `env_isaaclab` 中单独执行，不要在开发者环境中覆盖
原有 Isaac Sim、Torch 或资产缓存。
