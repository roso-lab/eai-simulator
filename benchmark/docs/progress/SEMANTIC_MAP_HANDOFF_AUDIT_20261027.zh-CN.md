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
