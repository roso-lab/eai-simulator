# Go2 benchmark 运动基座

Factory 的 `F-VERT-01` 楼梯诊断和 `source/EAI_hmrs/EAI_hmrs/envs/go2_factory_stairs.json`
默认使用 `GO2_MIXED_STAIRS_CONTINUED_CFG`。该配置对应继续训练的 Stage-B policy
（checkpoint 3999），保留旧的 `GO2_MIXED_STAIRS_CFG` 作为显式 Stage-A 回退对照。

控制器和权重属于 resolver/provider 管理的资产，不随 Git 提交。运行时需要从
`rosolab/eai-simulator-assets` 的不可变 revision 获取以下远端路径：

```text
controller/rl/go2_mixed_stairs/go2_mixed_stairs.py
controller/rl/go2_mixed_stairs/model/policy.continued3999.onnx
```

当前本地验收文件为 1,141,527 bytes，SHA-256 为
`5ec06c2a28e9f8b33f094ba9f74a5493dd684039315803cfd12ca334c101dc4e`。
提交前仍需由资产维护者发布到 provider 的不可变 revision，并在干净 checkout 中复核
该 revision；本地文件存在不能替代发布证据。

`GO2_MIXED_STAIRS_CONTINUED_CFG` 使用 232 维观测（含 187 条高度扫描）和 12 维动作，
Factory spawner 会生成单一三角网格 raycast proxy 供高度扫描。`demo/go2_stairs/diagnose.py`
提供 headless 的维度、有限值、姿态稳定、扫描范围和前进响应检查；`main.py` 提供上行、
平台和下行演示。它们是运动基座验收工具，不会把结果自动升级为 F-VERT-01 的业务得分。

M20 保留已有楼梯能力作为对照基线。B2 的运动模型仍在修复；接触采样或校准日志不能
单独宣称 B2 已通过楼梯。Go2 新 policy 仍需连续上行、平台停留、下行和独立支撑证据，
因此当前 `execution_validated=false`、`scoreable=false` 保持不变。
