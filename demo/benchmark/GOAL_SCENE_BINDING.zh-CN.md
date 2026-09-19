# 目标题目的场景绑定清单

`goal_scene_binding` 为 Factory、Warehouse NVD、Modern House NVD 生成作者专用清单。
它将公开对象、区域、路线、机器人配置和楼梯连接逐项关联到需要审查的来源与缺口，
不把现有固定 DAG 场景自动转换为目标驱动题目的部署实例。

```bash
conda run -n env_isaaclab env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_scene_binding audit --output-dir tmp/goal_bindings_new
conda run -n env_isaaclab env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_scene_binding verify tmp/goal_bindings_new/warehouse_nvd.json
```

输出目录必须是新目录。默认逐个读取并绑定本地源文件，同时用独立 OpenUSD 检查单个 authored
layer，并在占据图上查询旧场景声明的目标点；不启动 Isaac、ROS，不下载或修改资产。
`--no-verify-sources` 仍读取、hash 作者配置，但跳过 USD 几何和栅格查询。独立 `verify` 重建
所有声明、候选和检查结果；修改来源、删项或手工将候选标成 measured，即使重新计算报告
hash，也不能通过。报告包含私有配置和本地路径，不应放入 participant 的 `release/`。

清单区分以下内容：

| 内容 | 可以证明 | 仍需补充 |
| --- | --- | --- |
| 题面实验参数 | 本题承诺的质量、尺寸、负载、固定点和通行规则 | 与实际部署配置及当前状态一致 |
| 旧场景对象、目标点、区域候选 | 存在有业务关联的作者记录 | 明确对象身份、实例、区域含义和接受边界 |
| USD 单层检查 | 文件可读取、该层的 prim、局部顶点范围和单位元数据 | 组合后的世界几何、碰撞/支撑、实例及观测可见性 |
| 目标点栅格查询 | 声明坐标对应的单个像素占据状态 | 机体 footprint、携物/联合队形、连续通路及 USD 版本对齐 |
| 机器人类型匹配 | 旧场景存在同类型候选 | 每名成员的独立实体、附件、控制器、公开能力和后端符合性 |

2026-09-18 的清单共覆盖 75 个公开项：Factory 20 项、Warehouse 26 项、Modern House 29 项。
三份报告均无缺失的所列源文件，但整题绑定仍未通过。具体发现包括：

- Warehouse 目标驱动木箱为 12 kg，旧固定 DAG 木箱声明为 60 kg；纸箱分别为 3 kg 和 8 kg，尺寸也不同。
- Modern House 目标驱动扶手椅为 14 kg，旧场景为 28 kg；书堆分别为 2 kg 和 2.5 kg，尺寸也不同。
- Factory 服务车没有已审查候选；旧检查箱不能直接当作生产异常业务记录。公开楼梯仍需连续支撑面和通行绑定。
- 同类型候选不能自动展开为多个独立成员；三场景还需要明确候选池到实际实体的实例配置。

这些差异不是实测质量冲突，而是两套作者配置尚未统一。接入时应为目标题目编译独立部署配置，
或正式修订、重新审核并冻结题面；不能静默复用旧配置，也不能为了让已有计划通过而在运行中
调整质量、负载或净空。固定点抓取依旧是可靠技能，不以自主抓取算法成功率作为出题要求。

输出的 `review.status=blocked` 与 `scene_binding_validated=false` 表示仍有部署绑定缺口。
`local_evidence_checks_passed` 只计本地检查成功数。该清单不修改统一题包的作者审核结论，
也不提升 execution、held-out 或 scoreable 状态；统一入口见 [GOAL_SUITE.zh-CN.md](GOAL_SUITE.zh-CN.md)。

阶段11新增的 [四场景部署配置](GOAL_DEPLOYMENT_CONFIGURATION.zh-CN.md) 消费当前清单代码，
生成显式参数指令、独立实例身份和空间候选，并加入 City。上文阶段10的候选清单和差异记录
仍是历史范围说明；新编译配置也需要真实应用和读回，不能以参考执行替代物理绑定。
