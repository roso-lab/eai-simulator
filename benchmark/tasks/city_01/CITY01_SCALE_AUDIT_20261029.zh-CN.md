# city_01 尺度审计（2026-10-29）

## 结论

当前语义地图和 `city_01.usda` 使用的是同一套尺度，不能把 `meters_per_unit` 当成缩小错误直接修改。语义拓扑声明 `meters_per_unit = 1.0`；USD 根层 `World/Sketchfab_model` 声明 `metersPerUnit = 1`，但对导入模型统一施加了 `xformOp:scale = 0.16008645`。语义点坐标已经按这个根层变换后的世界坐标登记。

因此，当前逻辑仿真中的距离是米，且与 Isaac Sim 当前场景相互一致。只改 `topology*.json` 会使逻辑仿真和 Isaac Sim 分叉；只改 USD 根缩放则会使所有语义点、路径和电量预算同时失配。

## 当前测量

| 产品 | X 范围（m） | Y 范围（m） | Z 范围（m） | 边数量 | 边长均值（m） | 边长总和（m） |
|---|---:|---:|---:|---:|---:|---:|
| ground topology | -67.125–9.058 | -81.408–19.463 | 0.725–3.000 | 78 | 8.842 | 689.656 |
| aerial topology | -58.923–4.573 | -73.013–20.072 | 2.451–32.356 | 36 | 20.964 | 754.698 |

原始导入网格的最大平面范围约为 `499 × 661` 个资产单位；乘以 `0.16008645` 后约为 `80 × 106 m`，与语义拓扑约 `76 × 101 m` 的范围相符。当前不是地图产品之间的比例冲突。

## 如何改成指定真实尺度

需要先冻结一个可复核的现实参照长度，例如道路宽度、一个街区边长，或 `start_1` 到某一固定点的实测距离。给定参照长度后统一计算比例：

```text
s = reference_length_real / reference_length_current
```

然后以同一个 `s` 同步更新：

1. `city_01.usda` 的 `World/Sketchfab_model` 根缩放；
2. `topology.json`、`topology-aerial.json`、语义点和 footprint 坐标；
3. `coverage_radius_m`、机器人速度/电量距离预算、recover 服务点相关距离；
4. 所有地图哈希和 `city_01_task_manifest.v1.json` 的 `revision` / digest。

在参照长度未确定前，本轮只保留审计，不写入任意放大系数。若目标只是做算法时间敏感性实验，可以在 episode 层增加 `scene_scale_factor`，但该因子必须同时作用于图距离和运动/电量参数，并单独作为实验条件记录。
