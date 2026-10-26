# HeteroCoord 工程结构与提交边界

更新日期：2026-10-27

本文只描述当前工程的目录职责、执行边界和提交策略，不改变运行逻辑，也不是论文正文。
论文冻结权威 ref 为
`overleaf-old/main@dac7b94207a2683d494bdb388fc48533c9219bdf`。scorebook、composite、
31 锚点目录/合同、map-ready 编排与 P4 fixture 的当前发布面均绑定该身份。论文的
contract-only difficulty 原始量、固定分箱以及 pilot/fresh 认证契约已经实现；生产用
certification registries、真实 pilot/fresh 运行和物理/GPU 收据仍为 pending，不能据此打开
任何正式资格门。

## 1. 目录职责

```text
/home/pupil/HeteroCoord/
├── source/                         项目内 EAI、资产解析和环境实现
├── algorithm/                      论文算法、协同逻辑和技能提供者
├── task/                           任务合同、执行器和任务级测试
├── benchmark/                      论文协议、目录、评价器和证据账本
│   ├── fixtures/                   可审计的固定输入；不是运行输出
│   ├── tests/                      benchmark 合同与 v3 评价器测试
│   └── docs/
│       └── progress/               阶段进度、反思、handoff 和对齐记录
├── tools/                          运行入口、验证器、回放和 branch 工具
│   ├── benchmark_branch/           分支恢复、shared-session 和 shutdown 合同
│   ├── scenario_generation/        场景生成与项目边界检查
│   └── validation/                 项目导入、资产和运行质量门
├── usd/                            版本化元数据；大体量运行资产不入库
├── artifacts/                      本地实验收据和快照；默认不提交
└── tmp/                            临时验证工作树和缓存；不作为源码依赖
```

源码和运行时的唯一项目根是当前目录。实验不得从 `/home/pupil/eai-simulator/source/...`
导入 EAI；必须通过项目入口解析 `/home/pupil/HeteroCoord/source/`，并把 USD、控制器、
检查点和实验输出限制在当前项目或明确登记的外部只读依赖内。

## 2. 运行链路

工程按以下顺序传递信息：

```text
论文条款/版本
    ↓
benchmark protocol + task catalog
    ↓
algorithm/system 产生分配与协调决定
    ↓
algorithm/skills + source/EAI 执行控制
    ↓
Isaac/物理状态与独立 evaluator
    ↓
branch-local evidence → ledger → analysis/release gate
```

算法决定、进程退出码、回放成功或静态测试通过，都不能单独构成物理成功。正式结果还
必须绑定 GPU 设备身份、运行模式、检查点、随机流、控制器子流、原始轨迹、独立评价和
完整收据链。

## 3. shared-session 的位置

`tools/benchmark_branch/` 中的向量化实现是论文新增 `app:shared-session` 的候选执行
路径。它可以在一个进程内为多个 slot 保存私有机器人/对象/控制器/技能状态、锁、队列、
消息、brain memory 和逻辑时钟，但目前仍属于协议与诊断实现：

- 真实 GPU-parallel Isaac Lab 资格、independent-process restore、shared-session restore
  和 isolated open-loop audit 尚未形成完整收据；
- 共享进程故障只应使 active/in-flight paired block 失效，queued/not-started block 保持
  可区分的排队状态；
- 在论文条款重新对齐并完成 GPU 资格前，`rankable`、`physical_qualified`、
  `process_isolation_qualified`、`native_full_state_qualified` 和
  `paper_score_eligible` 均保持 `false`。

因此，R7/vectorized 相关未跟踪模块和混有前序修改的 tracked 文件暂不进入本次结构整理
提交；它们需在 observer 时序、cleanup failure、配对终态收据和 fresh import 完成独立
审查后，以完整依赖闭包单独提交。

## 4. 证据与临时文件边界

- `benchmark/fixtures/`：只放可审计、版本化的固定输入。
- `artifacts/`：保存本地运行产物和历史失败证据，不能把它当成源码或正式论文数据；除非
  单独登记并获批准，不提交整目录。
- `tmp/`：验证工作树、旧模板拷贝和缓存的临时位置。删除前必须列出精确路径并取得确认。
- `benchmark/docs/progress/`：只放过程文档、阶段反思和 handoff；它们不替代可重算的
  GPU receipt，也不把诊断结果升级为正式分数。

每次 Isaac Sim/Kit 运行结束后都要清理残留的
`omni.telemetry.transmitter`，并把清理状态写入对应收据，防止遥测进程继续占用磁盘。

## 5. 提交与分支规则

本轮只推送已完成、逻辑不变的结构文档。以下内容保持在本地，待各自阶段验收后再分批
提交：

1. v3 评价器、目录、统计和运行时模块；
2. R7 shared-session/vectorized 依赖闭包；
3. `artifacts/`、`tmp/` 和任何未确认归属的用户修改；
4. 需要真实 Isaac/GPU 的实验收据。

当前远端 `origin/main` 与 `HeteroCoord` 没有共同祖先，不能作为本工程的直接推送目标。
本工程阶段提交应推送到 `origin/HeteroCoord`；若以后需要合并到仓库的 `main`，应由维护
者基于明确的合并策略另行操作。
