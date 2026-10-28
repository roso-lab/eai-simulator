# room_06 语义地图交付审计（2026-10-27）

## 结论

当前 `room_06/semantic_map/current` 不能作为 `liv-t-04` 的最终输入，也不能生成
paper-ready measured case。审计器按格式合同 v2.1 fail-closed 返回 `case_ready=false`。
本记录不覆盖地图、不删除 `.write.lock`，仅记录当前外部依赖状态。

## 输入与命令

- 地图目录：
  `usd/scene/Living and public space/room_06/semantic_map/current`
- 当前 revision/save id：`d471be5423da493e8c19d7b4a60d85c3`
- 运行环境：`/home/pupil/miniconda3/envs/env_isaaclab/bin/python`
- 检查命令：

```bash
/home/pupil/miniconda3/envs/env_isaaclab/bin/python - <<'PY'
from benchmark.case_input_audit_v1 import audit_semantic_map
result = audit_semantic_map(
    "usd/scene/Living and public space/room_06/semantic_map/current",
    semantic_requirements={
        "regions": [], "entities": [], "interaction_poses": [], "routes": [],
        "semantic_points": [
            "floor1-bookshelf", "floor2-bookshelf-1",
            "floor2-bookshelf-2", "floor2-bookshelf-3",
        ],
    },
)
print(result)
PY
```

## 观察结果

字节 hash 与 revision 链本身可读，但 `case_ready=false`，失败项为：

1. `ground topology.nodes is empty`；
2. `ground topology.edges is empty`；
3. `semantic-points.points[0].label is empty`；
4. `semantic-points.points[1].label is empty`；
5. 缺少 `floor1-bookshelf`、`floor2-bookshelf-1`、`floor2-bookshelf-2`、
   `floor2-bookshelf-3` 四个任务要求语义点。

目录中的 `.write.lock` 修改时间为 2026-10-23；只读进程检查未发现当前 topology
authoring 进程，但锁归属仍由协作者确认，本线不自行清理。

使用已推送的历史证据转正名册
`benchmark/robot_resume/robot_runtime_roster.v1.promoted.json`（27/27
`runtime_qualified=true`）重新执行 builder preflight 后，失败项只剩上述 5 项地图
缺口；机器人池不再是当前 room_06 manifest 阻塞源。命令返回码为 2，审计报告成功
写出，`case-manifest.json` 不存在，符合 fail-closed 约束。

## 放行条件

协作者交付完整地图并解除写入后，必须重新记录最终 `save_id`/revision 链和所有产品
SHA-256，再依次运行：

```text
case_input_audit_v2.1
→ room_06/liv-t-04 manifest 重建
→ EMOS initialize/decision dry-run
→ evaluator terminal receipt + evidence
→ oracle/obligation reduction
→ fixed panel → D1–D6 → export/replay
```

在此之前，所有 room_06 结果只能是输入诊断或 `measured_unqualified`，正式资格门保持
`false`。

## 2026-10-27 后续复审：当前远端地图已通过输入门

上面的失败记录保留为历史审计，不代表当前远端 `HeteroCoord` 最新状态。基于
`origin/HeteroCoord@46b358e6`（其中包含 `room_06` 地图修订
`d9872563938c42fd85f43efa3dab3c0b`）重新运行同一 v2.1 审计，结果为
`case_ready=true`，失败列表为空。当前审计确认：

- 12 个语义点均有非空唯一命名与有效 ground/aerial 导航绑定；
- ground topology 为 50 个节点、63 条边；
- `save_id → manifest.revision → semantic-footprints.project_revision` 链一致；
- `semantic_binding_digest` 链一致；
- 任务要求的四个交互点已存在：`floor1-bookshelf`、`floor2-bookshelf-1/2/3`；
- promoted runtime roster 为 27/27，所选 case 的三台机器人均带有历史运行时资格收据。

在同一隔离 worktree 中使用用户已确认的 case binding
`artifacts/case-room06-livt04-20261027/binding.json` 重建 manifest，结果通过
`validate_case_manifest`：

```text
case_id=room06-livt04-20261027
task_id=liv-t-04
scene_id=living_room_06
semantic_map_digest=6e4fed64cd43a6daa53bba4ba5edbcc072186dcc5b020989ff5eb2ed6bd1db1b
roster_digest=1357626ae7c880eac7e0036fd823302442add297222983cd52d4cf2855569cd2
robots=3
object_bindings=heterocoord.task-object-bindings.v1
```

这一步只证明“最终地图 → 审计 → case manifest”输入接缝已打开，不产生 evaluator
终局事实，也不改变 `measured_unqualified` 与三项 formal gate 全部为 `false` 的边界。
下一步可以直接进入 EMOS dry-run 和真实 evaluator terminal receipt；若协作者继续更新
地图，必须重新记录 save-id 链与 digest 后再生成 manifest。
