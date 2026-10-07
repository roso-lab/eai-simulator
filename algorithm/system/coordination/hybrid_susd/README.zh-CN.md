# Hybrid SUSD MRTA → EAI

该适配实现 ICRA 2023 Hybrid SUSD 异构多机器人任务分配的 EAI 接口：先用
market pass 生成可行分配，再用无梯度的 SUSD 风格邻域搜索改进效用。官方实现
是 MATLAB，本目录是依照公开源码接口逻辑的依赖受控 Python 等价适配，不声称
在 EAI 中零修改运行 MATLAB。

Fire Rescue 使用 `algorithm.system.coordination.hybrid_susd.fire_rescue_policy:assign`。Z1
控制、导航、机械臂、物理里程碑和回放仍由 EAI 负责。发生超时、成员退出或任务
延迟时，适配器可在剩余任务上重新运行；论文原生故障修复能力记为 `N/A`，账本
会明确写成 adapter-level reallocation。
