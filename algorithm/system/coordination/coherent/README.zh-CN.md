# COHERENT → EAI 适配说明

这里是 COHERENT 的 PEFA（Proposal–Execution–Feedback–Adjustment）适配层，不是把官方 OmniGibson 仓库复制进 EAI，也不声称原论文环境的零修改复现。

## 当前已实现

- `TaskSpec`、`CapabilityProfile`、`SkillSpec`、`Feedback` 和 `ActionCommand` 的纯 Python typed contract。
- 中心 proposal、能力检查、单动作执行、反馈调整、依赖顺序和 append-only event ledger。
- Fire Rescue 纯 Python fixture，覆盖搜索、清障、器材交付、灭火、复核和失败后能力重分配。
- 安全的 dongli OpenAI-compatible provenance/client。默认支持用户指定的 `gpt-6-luna`，密钥只从 `DONGLI_API_KEY` 读取，不写入日志或结果。
- 明确暴露当前原生实现不承担的条件：通信延迟/丢包（D2）、成员退出恢复（D4）、跨域 matched transfer（D5）和并行动作。
- 提供可直接挂接 Fire Rescue 现有 `module:assign` 入口的确定性策略：
  `algorithm.system.coordination.coherent.fire_rescue_policy:assign`。它负责 Proposal/能力匹配和
  一对一分配；原有 EAI runtime 继续负责导航、机械臂、物理 milestone 和终止判定。

## 运行离线测试

```bash
python -m py_compile algorithm/system/coordination/coherent/*.py algorithm/system/coordination/coherent/tests/*.py
python -m unittest discover -s algorithm/system/coordination/coherent/tests -v
```

## 运行一次最小 API 连通性检查

调用前由 shell 提供 `DONGLI_BASE_URL` 和 `DONGLI_API_KEY`。不要把 key 写入代码、配置文件或日志。

```bash
python - <<'PY'
from algorithm.system.coordination.coherent.provenance import DongliConfig, chat_completion
config = DongliConfig.from_env(model="gpt-6-luna")
text, usage = chat_completion(
    [{"role": "user", "content": "Reply with exactly READY."}],
    config=config,
    max_tokens=8,
)
print(config.manifest())
print(text, usage)
PY
```

这只是 API/模型 provenance 检查，不是 Fire Rescue 或 Isaac Sim 结果。生产评测还需记录 prompt revision、temperature、token budget、任务/场景 hash、API client 版本和完整的 PEFA event ledger。

## 连接 EAI/Isaac

EAI 运行时应提供一个 executor，把 `ActionCommand.skill` 映射到现有 typed controller/skill，并在外部 milestone verifier 通过后返回 `Feedback`。本目录不导入 Isaac、ROS 或 Torch，不创建第二个 simulator application，也不负责物理完成判定。

## Fire Rescue 运行入口

在任务层加载适配条件：

```text
algorithm.system.coordination.coherent.fire_rescue_policy:assign
```

这会替换任务分配方法，但不会替换 EAI 的场景、控制器或 milestone 记录。建议正式结果
将算法名写成 `COHERENT-adapted-to-EAI`，并把 `task_checkpoints.json`、assignment
ledger 和场景/任务 hash 一起归档。
程序化调用 `condition_manifest()` 可得到无凭据的 condition 元数据，其中明确标记
`execution_scope=assignment_level`、无 learned checkpoint 和 EAI physical executor
所有权。

注意：该入口受宿主 `module:assign` 合同限制，返回的是本轮 robot→subtask 分配；
它不把 Fire Rescue 主循环改造成逐物理步的原始 OmniGibson PEFA action loop。因此
正式表格中应标为 `assignment-level adapted condition`；若要声称完整 serial action
PEFA，必须另行提供 executor/milestone 回调并注册新的运行条件。

COHERENT 原版依赖 Isaac Sim 2022.2.0、OmniGibson 0.2.1、ROS1 Noetic 和自定义资产；本适配必须标记为 `COHERENT-adapted-to-EAI`。如果后续加入并行回放或成员退出处理，必须登记为新的 comparison condition，不能覆盖原始 serial-PEFA 语义。
