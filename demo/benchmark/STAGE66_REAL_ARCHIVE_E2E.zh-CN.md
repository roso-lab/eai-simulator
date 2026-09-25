# 阶段 66：真实归档端到端协议指标报告

日期：2026-09-26  
归档：`tmp/restaurant_benchmark/formal-episode-20260924-v1`（ljy 验真餐厅正式
多机 episode `restaurant-algo:d35af72d…`，business_episode_execution_validated=true，
`--verify` validated=true）  
工具链：批次 0/1/2A/2B/3 + P0 修复（`9be00e6`）

## 端到端结果（全部离线可复算）

| 指标族 | 模块 | 结果 |
|---|---|---|
| Q(t)/MCR/AUP | goal_progress_curve | **Q(H)=1.0，AUP=0.4832**（三里程碑全完成：厨房检查 t=45.02、用餐检查 t=139.02、回执 t=188.02） |
| 部署五率 | goal_protocol_deployment | 该归档无部署评估 epoch（旧前评估时代 episode）→ `eligible_episode_count=0`，五率分母为零显式 N/A——**不猜测** |
| 时间锚 | goal_temporal_metrics.frozen_time_anchors | start=0.0(host_event)；termination=**unverified**（归档无终止记录→右删失 `termination_unverified_do_not_infer`）|
| 独立复算 | goal_independent_recomputation | progress+deployment+cost 三族 recomputed；communication 族 missing（该归档无通信工具报告）→ status=failed + missing_families 显式 |
| 执行窗 | 独立复算内曲线（P0-3 修复后） | **anchor:termination_unverified_do_not_infer**，t_exec=240（全时窗+未验证标记，不再回填最后里程碑时间 t=188.02） |

## 关键观察

1. **全链路可复算**：真实归档从原生证据记录一路到 Q(t)/部署/时间锚/独立复算，
   无一步依赖手工输入或猜测。
2. **缺证如实**：该归档无部署 epoch（时代限制）、无终止记录、无通信报告——
   各模块全部显式 N/A 或 missing 而非零值/推断。
3. **P0-3 在真实归档上的验证**：归档最后事件 t=190（action_result），最后
   里程碑 t=188.02——两者都不再被当作终止；execution_window 显式声明未验证。
4. **等权 v1 策略**：Q(H)=1.0 与三里程碑全完成一致；AUP=0.4832 反映检查
   逐步完成的进度形态。

## 下一步

跨 episode 聚合器（子代理进行中）落地后，多个此类归档可按 base instance
聚类进入六维度聚合报告；predicate registry 与时序指标事件规范的打通
（viewer 已标记为下批风险点）将关闭"开发态→正式"的来源链。
