# Stage46：City 终态身体几何诊断

基线：4882c6a；显式执行 profile：requested_target_v2_support_footprint。

Stage45 完整诊断有 6 个成功技能回执、3/4 业务通过。person02 的 root 已到目标中心，
但 world AABB 最远 XY 角距约 0.86353m，大于固定圆盘半径 0.75m。
轴对齐包络超界不能单独证明实际身体越界。原 arrival 记录又没有保留完整终态读回，
缺少 entered_hazard 的 fail-closed 结果也不能表述成实测进入危险区。

本切片只增加私有诊断和准确记录，不改变准入、到达、安全、业务或评分谓词。
旧 v1/default 路径不采新几何，冻结题包、见证、圆盘、reach、容量和业务时限不变。

## 采集与归档边界

- 仅 v2 的终态 person_readback 后，同一原生 tick 每名被引导人员采集一次。
  到达失败也保存 path_finished、inside_assembly、full_body_inside_assembly、
  safe_route_and_assembly、world_readback 和原有证据引用。
- trajectory_samples 不再复制到新增记录，只保留 sample count 与既有 trajectory 引用。
  新记录不冒充完整轨迹原始档，也不新增 entered_hazard 推断。
- prim 优先来自目标区 dynamic_envelopes；人员完全在区外时使用同次读回的
  all_dynamic_envelopes。诊断不改变人的位置或朝向。
- capture_body_geometry 只读一个 actor 子树的当前 Default timecode；
  native animation runtime 将当前动画写到该 timecode。
- 当前 visible default/render mesh 使用继承 purpose/visibility。骨骼 mesh 要求有效
  当前动画与 skinning，ComputeSkinnedPoints 的结果按 skeleton-local 空间转世界。
  静态 mesh 使用当前 authored points 与 mesh transform。
- 不支持的 blend shape、缺失当前动画/点/绑定、非法变换等返回 unknown；
  不用 bind pose、static extent 或 BBox fallback 伪造当前点。

私有 native_evidence_log 的 arrival 新增 terminal_readback 与 body_geometry_diagnostic。
诊断 schema 为 eai.city.body_geometry_diagnostic/version 1，包含原生身份/时刻、冻结
footprint、world AABB、root/Asset 变换、逐 mesh 世界点、actor-local range、定向角点及距离。
数据不进入 public task、participant API/trace，也不参与 business evaluator 的判定。

## 验收与停止规则

CPU 内存 USD fixture 核对旋转、当前 skinning、非单位变换、缺证和版本兼容。
一次有界 native 采样后，由独立脚本从磁盘原始点复算，并核对同 tick/episode/prim/
footprint hash、私有边界及既有业务回放。结果据证据记为包络保守、当前可见几何越界
或 unknown，不把 visible mesh 诊断当成碰撞体、完整任务 oracle 或发布认证。

本切片结束后回到 R1 自主执行/初始 oracle 与 R2 评分事实，不继续扩建通用几何框架。
验证结果及尚缺门禁另见本阶段验收记录；实现存在不代表原生验收通过。

scoreable=false、execution_validated=false、business_outcomes_validated=false 全程保持。
