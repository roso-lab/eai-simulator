# 空中 Skill 接入修复验收

2026-10-13，基线 `HeteroCoord a81e164f`，实现分支 `codex/aerial-skill-hardening`。
测试场景是 `tmp/` 下的临时诊断，不改变正式任务、实验合同或物理 evaluator。
团队分工用于搬运实现和独立边界复审，最终原生运行与截图由主代理验收。

## 审查问题与对应修复

| 问题 | 修复与证据入口 |
| --- | --- |
| F1：其他任务可绕过隔离场景 | 场景共享 ResourceManager，任意 quarantined lease 阻止推进；executor/runtime 回归 |
| F2：未推进物理也确认 stop、提前释放 | 严格递增模拟时间、实测线/角速度、有限 settling、外部 lease receipt；executor/runtime 回归 |
| F3：原生操作中断后失去所有权 | BaseException 隔离；关节创建前登记归属，清理失败保留可恢复路径；真实 OpenUSD notice/中断回归 |
| F4：持续导航可修改预算和上下文 | 请求身份冻结，允许刷新的 envelope 字段单列；navigation safety 回归 |
| F5：hold 允许时间倒退 | hold 同样更新单调时钟，终态 settling 拒绝 NaN/Inf/bool/倒退；navigation/runtime 回归 |
| F6：载荷子树跨独立刚体修改碰撞 | 在碰撞写入前拒绝嵌套 RigidBodyAPI；OpenUSD 回归 |
| F7：target_yaw 静默忽略 | 通用 goal executor 仅向明确声明 yaw_command_name 的控制器写入；不支持时写入前拒绝 |
| F8：通用动作合同过宽 | 版本、坐标系、向量维数、非零四元数、唯一关节及对应数量校验；schema/adapter 回归 |
| 接入缺口 | aerial 作者产品、英文地点/XYZ 导出、来源/图冻结、共享 tick binding、benchmark 适配 |

复审额外修复 scene/env 错绑、回调提前释放 lease、同 task 新请求重试和异常遮盖。
原生检验额外修复 Kit 关联刚体时覆盖 local frames、native reader 退出清理及
下降阶段冻结水平残差。用户指出的悬空挂载已改为真实 Iris 机腹接触点，容差 2 mm。

## 可复现检查

使用 `env_isaaclab` Python 3.11，关闭第三方 pytest 插件自动加载：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider \
  algorithm/skills/tests task/tests benchmark/tests \
  tools/validation/test_topology_authoring.py \
  tools/validation/test_topology_route_contract.py \
  tools/validation/test_topology_authoring_ui.py \
  tools/validation/test_topology_multilevel_authoring.py \
  tools/validation/test_topology_pose_ui.py \
  tools/validation/test_topology_surfaces.py \
  tools/validation/test_topology_aerial_authoring.py
```

结果：**606 passed**（包含 OpenUSD，不用它冒充 PhysX 步进）。独立 reviewer 对
最后的边界修复又运行 140 项聚焦检查。`git diff --check` 通过。

原生使用 Isaac Sim 5.1、CUDA、Quadro RTX 6000、GUI，入口为
`tools/validation/aerial_skill_native.py`。测试物体有质量/重力/碰撞，质量 5 g，
尺寸 0.10 × 0.10 × 0.25 m；两个台面相距 0.60 m。连接机腹实测表面与物体顶部，
理想 fixed joint 不要求摩擦抓取，既不传送物体也不修改控制器权重。

已通过的贴合搬运记录 `tmp/aerial-native-contact-cycle-02/evidence.json`：

- 下降接触 → 粘连 → 抬升 → 水平移动 → 下降 → 释放 → 上升撤离，约 60 模拟秒。
- 接触点距离 0.924 mm；最终物体中心 `(0.600799, -0.000311, 0.675000)` m。
- 每个 20 ms 步长中的最大物体位移约 20.1 mm，无原先的约 0.38 m 连接跳变。
- 质量保持、碰撞恢复、租约释放均通过；近景和最终画面已实际查看。

携物停止与恢复 `tmp/aerial-native-contact-stop/evidence.json`：停止时保持连接、
隔离 robot/object 租约；3 s 悬停漂移 12.97 mm，随后由隔离的 host 把物体送回源台面，
实测稳定后解除连接并恢复碰撞。最终中心约 `(-0.000070,-0.000127,0.675000)` m，
租约全部释放，所有诊断门槛通过，进程退出码 0。携物画面已实际检查。

`tmp/aerial-native-navigation-final/` 通过两架 Iris 同一共享循环的三维节点导航，
两个执行 receipt 均完成，全部 lease 释放，距各自终点约 0.221 m（默认节点半径
0.30 m；不冒充毫米级导航）。同一次真实 GUI 执行编辑器的 Save Draft、Load Draft、
英文地点解析、路线导出与 Generate，产物位于 `authoring/navigation/aerial/`。
已检查实际编辑器和双机场景截图；未宣称自动化操作系统鼠标点击或障碍物净空认证。

Kit 在配置暂时禁用的关节关系时仍可能输出 disjointed-transform 警告；原始日志
保留，验收依赖写完实测局部帧后的真实位姿和完整流程，不以屏蔽告警作为成功。
这些是工程诊断数据，`physical_outcome=unknown` 保留给正式 evaluator；不等同于
通用承载能力、自动避障、其他机器人适配或正式实验成功率。

GUI 命令、固定 JSON 和共享执行接入见 [运行指南](AERIAL_RUNTIME.zh-CN.md)。
