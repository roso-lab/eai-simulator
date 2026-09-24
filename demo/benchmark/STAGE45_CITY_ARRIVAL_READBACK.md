# Stage45 City 到达轮询最小读回

Stage45 只优化 `requested_target_v2_support_footprint` 的逐 tick 到达轮询，
不改变 City 任务、到达/安全谓词、采样频率或发布边界。

`CityEpisodeServiceRuntime.arrival_readback(person_id)` 是轻量只读接口：

- v2 轮询先用既有 `motion_readback` 读取当前 `path_finished`；未结束时按合取
  谓词必要条件立即返回，不调用 candidate/support/world geometry，也不伪造完整身体值；
- 路径结束后，`arrival_readback` 通过当前 native disk readback 检查实际完整身体
  XY containment；
- `arrived` 仍且仅在 `path_finished` 与 `full_body_inside_assembly` 同时为真；
- 身份或 assignment 错误继续抛出原有错误；
- legacy/v1 保留现有 controller/AABB readback。

该接口不构建 `trajectory_samples`，也不生成最终
`safe_route_and_assembly`。正式结果记录仍调用 `person_readback()`，完整保留逐步
3D route history、当前 support/capacity/foreign-body safety、trajectory evidence 与
safety evidence。`stamp_arrival_times()` 只在 exact v2 profile 使用轻量接口；其他
profile 的代码路径不变。

因此该优化只移除“路径已结束但完整身体尚未进入 disk”期间每 tick 对不断增长轨迹
的重复深拷贝，同时保留未结束路径的原有低成本短路。不把 root 到达当作整身到达，
不缓存动态几何，也不改变失败为成功。
