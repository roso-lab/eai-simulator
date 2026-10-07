# HeteroMRTA → EAI

HeteroMRTA（IEEE RA-L 2025）公开 AttentionNet/REINFORCE checkpoint，原生训练
分布为 3–5 种机器人、每种 3 台、15–50 个任务和 5 维 traits。Fire Rescue 只有
4 台机器人和 4 个不可拆分技能，因此这里只记录可审计的原生输入描述，不把 padding
后的策略结果宣称为零样本迁移。

EAI 物理轨道使用场景默认分配，并记录
`allocation_provider=fixed_default_assignment;heteromrta_native_decision_N/A`。
官方 checkpoint 只在原生测试集上单独报告 decision-only 结果；不在 EAI 中重新训练。
