# EMOS 长周期实验开发 TODO

本清单对应当前 `/goal`：把论文实验从任务目录推进到可恢复的批量执行和 release audit。

## 当前目标

`task_catalog.json → ExperimentCell → RunKey/BranchSpec → simulator.py session → branch artifact → D1–D6 release index`

所有实验都固定使用论文的 `Δt=0.02s`、`K=3`；没有外部物理证据时保留 `N/A/pending`，不使用零填充。

## 工作项

- [x] 30 个 anchor、9 组 common anchor、Table S2 和 difficulty 校验
- [x] D1–D6 endpoint registry、risk set、denominator 和 N/A 规则
- [x] Phase 0–7 EMOS experiment plan
- [x] branch-local artifact、checkpoint、sham、strict-paired protocol
- [x] cell 到 base instance / repeat / checkpoint / branch 的批量 manifest
- [x] 可恢复、幂等的批量执行队列与 worker hook（只持久化 manifest/branch 状态）
- [x] batch runner：queue → session → artifact → release admission 的组合接缝
- [x] release index：artifact digest、metric observation、coverage、pending/N/A
- [x] 一个真实 EMOS/EAI 场景的端到端 smoke（fact branch；物理判定保留 N/A）
- [x] GPU/Isaac 预检与一次真实 session 运行
- [ ] GPU/Isaac 预热与可复用 session 资源策略
- [ ] common-anchor 几何、能力、skill、oracle、milestone certification
- [ ] causal replay fidelity provider；完成前 D5 保持 N/A
- [ ] 统计 reducer：按 base-instance cluster 聚合，输出论文表格和置信区间

## 停止条件

1. 单个 catalog cell 能稳定生成完整 run identity 和四 branch manifest。
2. 一个真实 EMOS 场景能写出 artifact，并在缺少物理判定时明确报告 `N/A`。
3. release report 能列出 D1–D6 的支持、风险集、artifact hash 和缺失原因。
4. 任何未认证 common pair、非 causal replay 或未验证 milestone 都不会进入正式 paired 结论。

## GPU 使用边界

GPU 只用于 Isaac/EMOS 的真实执行、预热和批量仿真。临时日志和 artifact 放在 `/tmp` 或用户指定的实验输出目录，不把 USD、checkpoint、模型权重或大规模结果提交到仓库。
