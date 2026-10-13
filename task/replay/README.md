# Replay and branch adapters staging note

The paper-level branch contract is in `benchmark.protocol`. The physical
capture/restore implementation remains in `tools/physics_replay`, and
`benchmark.replay_backend.PhysicsReplayBackend` is the adapter between them.
This directory must not define another `ReplayBackend` or branch-role
vocabulary.

回放工具通过 `ReplayBackend` 注入 `TaskRunner`。checkpoint、分支和并行运行的
元数据必须显式记录；回放成功不能替代当前分支的物理 evidence。
