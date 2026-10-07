# DCMRTA → EAI

DCMRTA（ICRA 2024）公开了 REINFORCE checkpoint，但原生输入是 10–20 台机器人、
20–50 个任务、5 维能力需求和 coalition 任务；Fire Rescue 只有 4 台机器人和 4
个不可拆分技能。

因此 EAI 保留两条独立结果：

1. 在官方 DCMRTA 测试集上运行 checkpoint；
2. Fire Rescue 完整流程使用场景声明的默认分配，再执行 Z1 导航、机械臂、反馈、
   超时和回放。第二条是执行适配条件，不是 checkpoint 的零样本跨域泛化；padding
   结果明确标为 `cross_domain_mismatch`。
