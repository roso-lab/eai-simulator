# coffee_shop 论文对齐审计

coffee_shop 的六节任务设计与 `coffee_shop_task_manifest.v1.json`、difficulty contracts、scenario adapter 和逻辑矩阵逐项对应。T1–T4 均以多顾客、多步骤服务为基础，T2 增加操作容量，T3 增加座椅容量和固定处理等待，T4 增加双入口高峰、通信压力及 runtime 注册的 freshness window。P2 是主协作关系，P1/P4 作为并行订单、共享设备、容量和恢复诊断。

所有 checkpoint 从 `cp_00_spawn_valid` 到 `cp_09_closeout` 使用 `0.02 s` 逻辑时钟，并要求 paired condition 共享同一注册状态。当前地图、机器人、设备、顾客和配方均未完成物理资格认证；`robot_validated=false`、`physical_qualified=false`、`runtime_oracle_bound=false`、`paper_score_eligible=false`。
