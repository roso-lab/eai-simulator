# TeamWeaver 逻辑闭环归档与接口对比

本次实际运行：2 机器人、2 批 DAG、4 次 Navigate 派发全部完成；两次原生 MIQP 均为 Optimal，fallback_used=false、native_error_count=0。第二批输入位置为第一批执行后的 [[0,0,0],[0,0,0]]，最终两机器人均到达 [2,1,0]。

## 源码与执行证据

- 原始源码目录：/home/airs/.cache/heterocoord/upstream/teamweaver
- 固定提交：edf4619e4730860b96eebaac39620df4bbdc486a
- 原始入口：class_def.RTA_task.RTA.solve_miqp_phase_aware
- 实际 solver_file：/home/airs/.cache/heterocoord/upstream/teamweaver/TeamWeaver/planner/HRCS/class_def/RTA_task.py
- source_before.json/source_after.json：整个受验证源码、全部 53 个 HRCS Python 文件及工具/测试/native_adapter 文件哈希。
- verification.json：命令、时间、返回码、12 项通过核查；receipt.json：原生输入/输出、两批 before/after 观测、反馈及 runner ledger。
- tests.log/tests.xml：本次独立重跑 21 passed；用户报告的完整 218 passed 未在本次重复执行。
- supplied_decomposition solve 分支直接导入原始 HRCS；本次不进入语义兼容 overlay、LLM 或 Habitat 分支。未修改 native_adapter 或上游。

## 与历史适配的对比

旧 /home/airs/eai-simulator/algorithm/TeamWeaver 工作目录仅残留 pyc；历史 Python 源码可从该仓库 62d6c33^:algorithm/TeamWeaver/ 恢复。核心 pipeline、dynamic_miqp、semantic_decomposer、capability_tracker 与当前 eai_adapter 仅导入路径不同，task_models.py 字节一致。

| 接口 | 可复用部分 | 原生算法边界 |
|---|---|---|
| SymbolicWorldState / RobotSnapshot | 位置、能力、忙闲、事实等输入结构 | 仍带 fire/rescue 场景字段，不是原生 Habitat WorldGraph |
| SemanticTask / TeamWeaverPlan | DAG、任务标识、分配结果、阶段信息 | 输出格式可映射，不应复用其自定义分解与调度作为原生证据 |
| plan_initial / replan / accept_feedback | 生命周期设计与真实执行反馈接入模式 | 旧实现使用自定义分解器、可靠性加减、任务追加、Gurobi/Hungarian fallback |
| 原生 LLMPlanner.get_next_action | instruction + observations + world_graph → 低层动作、planner_info、done | 与只调用 HRCS 的 supplied_decomposition 是不同范围 |
| 本次逻辑工具 | EaiTaskPackage → TaskRunner.run_orchestrated → ExecutionUpdate + WorldSnapshot | DAG 外部提供，完成状态来自 executor；不替代 MIQP/utility，无 LLM |

本次执行器在理想逻辑模型内直接更新坐标，再独立检查身份、依赖、能力、反馈、时间/观测版本与位置；不包含导航动力学、碰撞或 Isaac 步进。physical_execution=not_run、physical_success=N/A、scoreable=false。不能将此次逻辑完成解释为原生 full_semantic 或物理任务成功。

主 agent 正在推进的原生语义 replan/API/MIQP 与 MotorSkillTool grammar 工作独立于本次归档，本次未调用或认证该路径。
