# 科学范围与证据等级

此目录目前是可复现的场景生成与校验原型，可用于已注册 Factory 场景上的受控工程实验；它尚不足以支撑关于通用多机智能、物理搬运能力或 sim-to-real 的结论。

PARTNR 的“生成指令 + 仿真落地评测”设计值得借鉴，但其已发表数据集包含 10 万任务、60 个环境、5,819 个物体；本原型只有一个场景、少量人工编写骨架且没有保留场景划分，二者不能作为同等级数据集比较。参见 [PARTNR（ICLR 2025）](https://arxiv.org/abs/2411.00081)。

## 证据等级

| 等级 | 可以证明什么 | 不能证明什么 |
| --- | --- | --- |
| `placement_validated` | 已通过 schema、语义引用、足迹摆放、二维连通性、确定性路径存在性和非平凡目标校验。 | 资产可加载、运行时稳定、物理可行性、策略表现。 |
| `spawn_validated` | 指定 Isaac 配置可启动，并在零机器人命令下以设定漂移阈值完成沉降。 | 导航、搬运、避碰、任务完成。 |
| `navigation_execution_validated` | 此执行器中已分配的机器人到达导航目标，状态评测器观测到目标谓词，且有界的无进展看门狗未触发。正常接触被允许。 | 最优性、泛化、接触力安全性、感知鲁棒性。 |
| `kinematic_execution_validated` | 在前一等级基础上，用显式根位姿覆盖完成逻辑搬运目标。 | 抓取、机械臂 IK、接触、载荷、物体动力学、物理搬运。 |
| 未来 `physical_execution_validated` | 需要文档化的物理操作栈，并在接触动力学下测得成功。 | 测试分布之外的泛化。 |

[独立 Z1 标定](PHYSICAL_CALIBRATION.md) 可以提供限定到固定基座、预放置物体及已通过
质量点的接触抓取/抬升/搬移/释放 E7 证据；它不等于上表的完整场景物理执行等级。
benchmark 强制保留机械臂质量与惯量，并核对运行时与源资产的一致性；源资产本身
是否对应真实硬件，仍须单独标定。

离线可行性报告是二维膨胀栅格的存在性证书，不是性能 Oracle，也不能命名或统计为一次经验执行成功。

## 当前可以科学研究的问题

固定 Factory 地图、资产、控制器包、随机种子和完整场景 JSON 后，本管线可以支持：

- 受约束场景生成的拒绝率与失败类型；
- 声明机器人足迹和障碍下的确定性路线可行性；
- 固定且披露的任务分配/导航器在固定分布上的表现；
- 仅改变已声明 family delta 的配对条件比较（前提是保留逐实例执行证据）。

每项结论都必须声明证据等级、控制器/模型版本、种子集合、超时、硬件/物理配置与剔除实例。`tmp/` 是运行时数据，不是可长期引用的 benchmark 发布物。

## 阻碍“可发表 benchmark”结论的缺口

1. **构念效度：** 当前骨架数量小且人工编写，尚未证明标签衡量的是协作而不是任意路线分配；能力覆盖也不等于任务尺度上的协作必需性。
2. **内部效度：** 执行器允许接触并检测持续无进展的卡死；它不会判别接触是否对任务安全，也不测量接触力。kinematic 搬运还绕过物理操作，不能据此给出物理任务成功标签。
3. **外部效度：** 单一 Factory 布局和资产不能检验跨场景泛化；报告通用表现前需要保留的场景/几何测试集。
4. **统计结论效度：** 少量成功 episode 或种子只是演示，不是效应估计；应报告预先声明的独立种子上的逐实例结果、置信区间和失败，而不能只展示成功样例。
5. **可复现性：** 需发布冻结 episode、地图/资产哈希、生成配置、控制器版本、报告和评测代码，并把失败报告作为数据集溯源的一部分保留。

## 下一阶段最低实验协议

1. 预注册任务轴和假设（如形态、载荷、时序依赖、真正的并发分配），按场景和 family 而非仅 episode 划分固定 train/dev/test。
2. 为分配与规划提供非 LLM 和 LLM 基线，使用同一观测/动作接口与计算预算。
3. 评测成功率、部分完成度、makespan、距离/能耗代理、卡死次数、干预/重规划数、墙钟时间和失败类别。只有任务定义了何种接触不安全时，才单独记录该类接触；不要把正常任务接触视为失败。
4. 接入真实抓取-释放链路（IK、任务相关的净空检查、接触/稳定性检查、载荷测试）；若仍使用 kinematic 方案，应把它单独命名为符号/运动学基线。
5. 自动生成溯源清单，使每个汇总数字都能追溯到逐实例报告和配置哈希。

## 当前的划分与汇总防护

`benchmark_protocol.py` 可以为已沉降的实例池冻结一个可复现的**同一
Factory 场景内**划分。它将 `family_id` 与 `layout_signature` 的连通组整体
分到同一侧，因此配对条件和意外重复布局不会从开发集泄漏到测试集。汇总时，
只有记录的场景 SHA-256 仍与冻结实例字节一致的执行尝试才会被统计；输入被
修改或丢失会被明确标为不完整证据。测试集证据还必须带有不早于
`frozen_at_utc` 的时间戳；更早的报告仅保留作审计历史，不可复用为盲测结果。

```bash
# 创建新文件；已有冻结清单不会被覆盖。
python tools/scenario_generation/benchmark_protocol.py freeze \
  --pool tmp/validated_pool/factory \
  --output tmp/validated_pool/factory/protocol_v2.json \
  --split-seed 20260911 --test-fraction 0.25

# 运行预先声明的划分，再只汇总哈希匹配的逐实例证据。
python tools/scenario_generation/batch_execute.py --pool tmp/validated_pool/factory \
  --protocol tmp/validated_pool/factory/protocol_v2.json --split development \
  --transport-mode auto
python tools/scenario_generation/benchmark_protocol.py summarize \
  --pool tmp/validated_pool/factory \
  --protocol tmp/validated_pool/factory/protocol_v2.json --split test
```

它阻止的是 episode 级泄漏，不能把单一 Factory 布局变成跨场景泛化基准；任何
汇总仍须把导航与运动学搬运证据等级分开报告。

在这些条件满足前，应将本工作表述为**经过校验的场景生成与执行器原型**，而不是测量通用物理多机协作能力的 benchmark。
