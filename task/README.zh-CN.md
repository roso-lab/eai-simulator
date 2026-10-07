# HeteroCoord 任务层

`task/` 是建立在 EAI core、assets 和 HMRS 之上的独立任务编排层。它维护任务
合同、机器人能力输入、算法决策记录、执行反馈、回放接口和物理证据边界。

任务层不维护 USD、不注册 Gym task、不创建 Isaac App，也不实现机器人控制器。
`task/fixtures/` 仅提供无仿真的符号化适配器测试输入，不代表物理场景。
底层路径/轨迹/命令 provider 统一归类在 `algorithm/skills/`；`task/executors/`
负责将它们接到 EAI controller/skill。场景与机器人由 EAI 的 saved environment
和资产解析器负责，物理动作由 EAI runtime 负责。

## 依赖方向

```text
task/config  ->  algorithm adapter  ->  task/runner
                                           |
                                           +-> EAI SkillExecutor
                                           +-> evaluator / ledger
```

没有注入真实 `SkillExecutor` 或 evaluator 时，运行结果只能是
`N/A / physical_evidence_required`。算法返回的 assignment、导航派遣和计划状态
都不能直接证明物理成功。

## 当前边界

- `simulator.py` 只接受显式 `--env` 或 EAI 自身 schema 的 `--scenario-file`；
  `task/config/*.json` 是任务合同，不能直接作为 simulator scenario-file。
- 独立的 `source/EAI_env_diy` Kit 扩展和 `--diy-3d` 已删除。
- EAI 内部 `EAI.hmrs_env.env_diy` 仅保留 saved environment 的 JSON 兼容解析，不能
  被重新扩展成 UI 或插件入口。
- USD 和大模型权重由 provider/本地资产根目录提供，不上传到 HeteroCoord 源码分支。
- 任务场景逻辑进入 `task/`；系统算法放在 `algorithm/system/`。EMOS 和 TeamWeaver
  的被测实现位于 `algorithm/system/coordination/`，不再保留顶层兼容副本；底层算法
  放在 `algorithm/skills/`，两者都不反向依赖 task runner 的实现细节。

## 交接顺序

先读 `task/contracts.py` 和 `task/runner.py`，再实现算法 adapter；随后由执行层
接入 EAI 控制器/skill，再由 evaluator 写入物理证据。回放和并行分支属于
`task/replay/` 的工具边界，不能成为算法隐藏状态。
