# Replay and branch adapters

回放工具通过 `ReplayBackend` 注入 `TaskRunner`。checkpoint、分支和并行运行的
元数据必须显式记录；回放成功不能替代当前分支的物理 evidence。
