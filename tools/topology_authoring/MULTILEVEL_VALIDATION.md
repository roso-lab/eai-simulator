# 多楼层工作流：2026-10-12 原生验证

## 英文地点、XYZ 与终点朝向追加验证

复用本工具新增可选 `heading_rad`、XYZ/角度输入、视口第二点定向以及
**Named Landmarks** 地点列表。英文 label/alias、楼层、坐标和朝向随地图保存；
上层固定 JSON 格式由用户后续确定，本轮未添加自然语言解析或正式任务定义。

追加完整回归 **717 passed, 1 skipped in 33.93s**，最终界面改动后相关 61 项
再验通过；独立核心审查修复后 64 项通过。命令见 [VALIDATION.md](VALIDATION.md)。
补充覆盖开始前 hold 不得改写 v2 朝向、仅凭地点查询不得认定朝向到达、v1 清单
兼容、预览移动保留朝向以及跨楼层地点选择。

实际 Isaac Sim 5.1 GUI 检查产物保留于忽略目录 `tmp/semantic_pose/`：

| 检查 | 实测证据 |
| --- | --- |
| 编辑器 | 真实 Kit 回调设置 XYZ、90°方向、英文名称/别名，保存/加载、路线导出、选择已标记地点；原场景摘要未变 |
| 方向显示 | 原生 USD 世界变换确认箭头不重复平移；单节点路线显示终点箭头，隐藏楼层不显示其箭头 |
| Go2 `go2-01` / 最终 `go2-02` | 从 `pantry_1f` 到 `meeting_room_2f`，上行 12 级实体台阶后对齐世界 +Y，两次 physical_pass=true，进程 exit 0 |
| 终点支持高度 | 1.200000048 m，目标 1.2 m |
| 位置 / 方向误差 | XY 0.086585 m；yaw 0.087657 rad（约 5.02°），设定容差 0.10 rad |
| 停车 / 姿态 | 平面速度 0.000511 m/s；竖直方向余弦 0.999331；终态零命令已执行 |
| 过程记录 | 884 个样本，终点有 105 个纯转向命令样本；每次 187 条有效高度射线；输入摘要未变 |

已实际检查 GUI 截图及机器人最终近景。编辑器操作通过真实回调进行，不包含
OS 鼠标投递认证。原始结果见 `editor-04/result.json`、`go2-02/result.json`；
数据是临时台阶上的诊断，不是 office 楼梯验收、正式任务成功率或下楼认证。

本轮拓扑产品摘要：
`ab198061ca61ee6b458b4419556fc75163c2406e2907aa2a9198b08b2c82cc06`。
最终导航使用 `editor-03/topology.json`；后续 `editor-04` 仅检查界面，产品摘要相同。

### 本机打开编辑器

```bash
cd /home/pupil/eai-simulator-worktrees/experiment-analysis-framework
source /home/pupil/miniconda3/etc/profile.d/conda.sh
conda activate env_isaaclab
python -m tools.topology_authoring.author \
  --scene-key three_levels_diagnostic \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root tmp/multilevel/three-levels \
  --output-root tmp/semantic_pose/editor-04
```

点击 **Load Draft** 加载已验证的地点。**Named Landmarks** 中可选择地点或
设为起终点；**Node Properties** 编辑已有点，**Preview** 创建新点。
**Pick Heading** 后点击方向点；角度框留空表示不约束，0°=+X、90°=+Y。
在 **Landmark Route Preview** 填写 `pantry_1f`、`meeting_room_2f`，能力填
`walk,stairs`，然后预览并导出。输入框或地点列表没有显示时向下滚动。

若需编辑 office，使用本文末尾的 office 编辑器命令；流程完全相同。

### 本机复验 Go2 上楼并定向

在同一环境及工作目录中运行：

```bash
export EAI_USD_ROOT=/home/pupil/eai-simulator/usd
export EAI_CONTROLLER_ROOT=/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs
python -m tools.topology_authoring.deploy \
  --scene-key three_levels_diagnostic \
  --asset-root tmp/multilevel/three-levels \
  --topology tmp/semantic_pose/editor-04/topology.json \
  --origin pantry_1f --destination meeting_room_2f \
  --controller GO2_MIXED_STAIRS_CFG \
  --speed .5 --node-radius .18 --timeout 70 --max-steps 4500 \
  --output-root tmp/semantic_pose/gui-user-01 --hold-gui
```

每次复验使用新的输出目录。`--hold-gui` 在到达后持续给零速度并保留窗口。
以下保留此前三层上行与 office 检查记录，其数据与本轮朝向验证分开。

本轮验证的是通用的楼层/地标编辑、XYZ 路线解析和现有 ground-navigation skill
接入。具体 office 描述、路线、临时台阶 USD、截图和测量结果均保留在忽略的
`tmp/multilevel/`，不作为正式实验任务、合同或论文成功率数据。

## 实际执行结果

完整轻量测试命令见 [VALIDATION.md](VALIDATION.md)，本轮运行结果为
**684 passed, 1 skipped in 33.82s**。跳过的是当前 checkout 不具备的历史场景
插件。独立审查的相关子集另有 168 passed。

原生环境为 Isaac Sim 5.1、`env_isaaclab`、Quadro RTX 6000、CUDA 物理。
编辑和导航均使用真实 GUI；导航复用 `open_simulator_session`，没有另造生命周期、
瞬移机器人、运动学搬运或修改 RL 权重。

| 检查 | 观测结果 |
| --- | --- |
| office 场景实际检查 | 101 个碰撞网格、1,479,525 个三角形；三个支持高度约 0.1095 / 1.1372 / 2.1140 m；最高层为屋顶平台 |
| office 物理路线 | 未通过；机器人在开放式踏步处受阻，用户确认脚卡进缝隙并计划修场景。原 USD 未修改，屋顶平台没有臆造可达连接 |
| 编辑器 `editor-check-02` | 真实 Kit 回调完成绘点、短边连线、关键点绑定、同 XY 分层拾取、楼层剖切、路线预览/导出、保存/加载；source unchanged；进程 exit 0 |
| 三层实体台阶 `three-level-route-03` | 两段楼梯，每段 12 级，级高 0.10 m / 踏面 0.30 m；完整上行路线成功，进程 exit 0 |
| 最终支持面高度 | 2.400000095 m，目标 2.4 m |
| 终点水平误差 | 0.143584 m，节点到达半径 0.18 m |
| 停车实测平面速度 | 0.005437 m/s，验收上限 0.12 m/s |
| 终态姿态 | 竖直方向余弦 0.999980；最终近景已实际查看，四脚支撑 |
| 控制与扫描 | 1489 个样本，间隔均约 0.020 s；187 条高度射线有效；转向不超过 0.6 rad/s |
| 停止与来源 | terminal zero applied；场景、机器人 USD、策略、控制器及部署代码的记录摘要未变化 |

编辑器验证调用真实 Kit/UI 回调并实际检查渲染图片；没有模拟 OS 鼠标投递，不能
把回调检查写成完整的人工交互测试。楼层剖切使用无纹理碰撞几何预览；原始场景
的材质、UV 警告和光照并未修复。

## 本次翻倒的定位与修复

`three-level-route-01` 确实翻倒，保留为失败证据。旧截图函数在时间轴运行时直接
调用 12 次 `app.update()`，额外推进 0.24 s 物理而没有刷新策略、关节目标或
传感器；原轨迹在截图处出现 0.26 s 的控制间隔，正常间隔仅 0.02 s。

改为 `SimulationContext.render()`，截图前后断言物理时间未推进。相同几何和
策略的 `three-level-route-02` 已通过；再把 mixed-stairs 转向限幅对齐训练范围
0.6 rad/s，最终 `three-level-route-03` 仍通过。没有通过改变楼梯或绕过物理来
使失败结果变成功。

另外增加了姿态失稳检测、异常兜底零命令及失败回执；这些分支有故障注入回归。
最终成功运行没有触发跌倒，因此不将它表述为“真实跌倒恢复通过”。零速度并不
保证机器人自动站起。office 之前的测试也使用旧截图路径，其受阻结论不能用于
单独判断 RL 策略能力，后续应在用户修复场景后用新程序重验。

最终产品摘要：
`8fc5a4d45c657d6dc6a3bc438ec96c2865fab9a601354cf0ba3f088e42a89acf`。
部署 ONNX 摘要：
`5ec06c2a28e9f8b33f094ba9f74a5493dd684039315803cfd12ca334c101dc4e`。
完整输入回执见各运行目录的 `inputs.json` / `result.json`。

该结果证明此实体台阶上的上行执行。未测试下楼、任意楼梯、载荷变化或所有场景；
RL 的更广泛地形能力需另行资格验证。来源摘要、支持面连续性和图准入不能认证
机器人净空、复杂碰撞、动态避障或正式任务成功。

## 本机 GUI 复验命令

下面路径对应本次隔离工作树及已有临时资产，其他机器按
[通用工作流](MULTILEVEL.zh-CN.md) 准备自己的描述文件和地图。
在已激活的 `env_isaaclab` 终端中运行：

```bash
cd /home/pupil/eai-simulator-worktrees/experiment-analysis-framework
export EAI_USD_ROOT=/home/pupil/eai-simulator/usd
export EAI_CONTROLLER_ROOT=/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller
export PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs
python -m tools.topology_authoring.deploy \
  --scene-key three_levels_diagnostic \
  --asset-root tmp/multilevel/three-levels \
  --topology tmp/multilevel/three-levels/topology.json \
  --origin place:lobby --destination place:meeting \
  --controller GO2_MIXED_STAIRS_CFG \
  --speed 0.5 --node-radius 0.18 --timeout 70 \
  --output-root tmp/multilevel/gui-user-01 --hold-gui
```

每次使用新的 `--output-root`，避免覆盖证据。`--hold-gui` 到达后保持零速度，
保留窗口供检查；关闭窗口退出。

打开 office 分层编辑器，并加载本轮已生成的草稿：

```bash
python -m tools.topology_authoring.author \
  --scene-key office_probe \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root tmp/multilevel/office \
  --output-root tmp/multilevel/editor-check-02
```

启动后点击 **Load Draft**，在楼层选择器切换 1F / 2F / 3F 或 All Levels。
3F 屋顶节点没有被验证的跨层连接，查询到该节点应显示不可达。office 几何修改后
先重新加载场景、审查并重建地图来源摘要，再测试；旧产品会因来源变化被拒绝。
