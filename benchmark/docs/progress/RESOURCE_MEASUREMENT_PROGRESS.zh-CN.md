# D6 资源测量前置阶段报告

日期：2026-10-21。论文基准：`75ceff6f55202b6b1a31b2e2e0f6de56023940fd`。
工程：`/home/pupil/HeteroCoord`。本阶段不修改论文。

本阶段完成的是一条真实的、项目本地的命令—反馈—物理时间测量链。它现在可以记录
协调器向机器人发送的序列化指令、机器人返回的序列化反馈、实际发送的 bit 数、模拟
物理时间，以及在停车确认后才结束的终止边界。它还没有生成正式 D6 的参考尺度，因为
论文要求每个实例有独立、预注册的多次校准批次，并且实例必须先通过正式的可部署和
物理技能验证门。

## 测量链

运行前先保存固定测量计划，内容包括协调器身份、尝试身份、共享通道容量、延迟、
队列上限、反馈周期、消息格式、双方观察权限和每步操作顺序。协调器只接收公开任务
信息与已经传输完成的机器人反馈；机器人只接收已经传输完成的路线命令和本地可见
状态。传输长度在序列化之后计算，发送者后续修改原对象不会改变已经计费的 payload。

测量记录包含：

- `submitted_bits`、`transmitted_bits`、`delivered_bits`、`dropped_bits` 和完整帧清单；
- 物理开始、结束和经过时间；
- 导航节点的有序到达、终点误差、停止速度和严格递增的仿真时钟；
- 收到的终止反馈及其位置、速度和 `stop_confirmed` 标记；
- 工程 source 的输入摘要和 `external_editable_environment_modified=false`。

## 两次实际原生运行

第一次运行目录：`artifacts/project_runtime/resource_preflight_01/`。监督器最初判为
通过，但事后审计发现终止反馈在“到达”时发送，`stop_confirmed=false`，速度约
`0.0911316423 m/s`，停车是在测量窗口之后的清理阶段才完成。因此该收据被明确
判为无效，原始目录和判定说明 `adjudication.md` 均保留，不能用于校准。

第二次运行目录：`artifacts/project_runtime/resource_preflight_02/`。修正后只有在
停止确认完成时才发送成功终止反馈，结果如下：

| 项目 | 实测值 |
| --- | ---: |
| `submitted_bits` | 99,216 |
| `transmitted_bits` | 99,216 |
| `delivered_bits` | 99,216 |
| `dropped_bits` | 0 |
| 物理时间 | 7.739999827 s |
| 终止反馈速度 | 0.000434772 m/s |
| 端点误差 | 约 0.119088 m |
| 到达节点 | start → corner → finish |

运行前后输入摘要一致，三类 EAI 包均来自工程内 `source/`。这次运行证明测量链和
任务完成边界连通，不能证明该 Factory 场景已经是论文的认证目录实例。

## 验收与反思

32 项测量、传输、拓扑和活动重建聚焦测试通过；在补齐旧场景入口的 fail-closed 边界后，
完整选定工程回归 **706 passed**，
编译与 diff 检查通过，运行所用四个关键源文件摘要与当前文件一致。Sol 独立审查确认
第二轮反馈和测量窗口内的任务状态均已确认停车。第一轮“看起来通过”的收据暴露了
一个重要问题：导航状态进入 completed
并不等于机器人已确认停止，终止反馈必须等待物理停止确认。通过保留第一轮并追加
第二轮而不是覆盖旧目录，实验身份和失败原因保持可审计。

当前仍缺：认证的可部署实例目录、物理技能验证收据、独立校准协调器的完整批次、
每实例的成功率和参考尺度不确定性。因此 `reference_scales=null`、
`paper_score_eligible=false`，正式 D6 仍未完成。

下一步应先由目录/认证审查确认 Factory 实例是否能进入正式目录；只有满足论文门槛
后，才可按冻结的批次身份和重复次数运行正式校准，不应把本次一次性 preflight
重复运行直接称为独立校准。

同阶段随后完成了一项独立的组件级物理证据生产：
`tmp/physical_calibration/z1_component_0p2kg_20261020.json`。CPU Isaac 运行包含
0.2 kg 载荷的 3 次 close 与 3 次 open-control 配对试验，固定底座、预放置物体、
力驱动、重力和碰撞均按报告记录。独立 verifier 重新计算后得到 `physical_validated`
和 `E7`，验证范围为 `standalone_prepositioned_grasp_lift_transfer_release`。
索引 `evidence_index_20261020.json` 也只标记 `verified_component_evidence`，并明确
`mobile_promotion=false`、`hardware_promotion=false`。

这项证据提高了组件层的可审计性，但不能把 Factory 导航实例从 `planned` 提升为
认证目录，也不能替代移动接近、场景取放、P2/P3/P4 协作技能或 D6 的独立参考批次。
