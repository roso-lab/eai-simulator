# bar 论文检测对齐审计

bar 采用 Isaac-free 逻辑轨道，地图 revision 为 `754f3e1731ca464a83455132375279a0`，逻辑时钟为 `0.02 s/tick`。当前 ground topology 为 59/59，aerial topology 为 0/0，`robot_validated=false`，`physical_qualified=false`，`paper_score_eligible=false`。

T1/T2 是无电量基线；T3 使用 `battery_hard`，T4 使用 `battery_very_hard`。电量归一化为 `[0,100]`，按逻辑 tick 扣减；低于安全阈值必须前往 `floor1-charging_point/node:0066`。困难模式到达并占用后立即充满，非常困难模式在容量 1 的充电点等待固定 `charge_wait_s` 后充满。耗尽、不可达或资源冲突均保留 safe-stop 和 failure ledger 证据。

正式关系以 P1/P2/P3/P4 合同为准；电量与充电用于 D1、D2、D3、D4 和 D6 诊断。paired condition 从共同 `cp_02_pre_handoff` 开始，并共享三机 variant、订单版本、电量参数、地图 digest 和随机种子摘要。所有结果仅为逻辑诊断，不能升级论文资格。
