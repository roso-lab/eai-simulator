# 空中 Skill 与统一接入修复 TODO

基线：`HeteroCoord a81e164f`。目标是修复审查中 F1–F8 并补齐空中路线与统一执行接口；不修改正式任务、评测公式、机器人权重或场景资产。

- [x] 阶段 0：获取最新分支；隔离工作区；冻结问题清单和文件所有权。
- [x] 阶段 1：搬运全场景隔离、停止确认、物理中断清理、刚体碰撞边界（F1/F2/F3/F6）；恢复维护测试。
- [x] 阶段 2：导航身份/预算冻结、hold 时钟、观测 provenance（F4/F5）；通用 schema 和终点方向合同（F7/F8）。
- [x] 阶段 3：空中拓扑 domain、三维选点/语义保存/载入导出；地面 XY 约束保持；冻结图身份。
- [x] 阶段 4：assignment 到持续 skill 的统一适配、共享 step 和 ResourceManager、实测停止 receipt 与恢复；不伪造 evaluator 结果。
- [x] 阶段 5：相关单测和回归；Isaac Sim 原生路线、搬运/放置与停止恢复；实际截图检查。
- [x] 阶段 6：复审、更新使用说明与验收记录，交付可复现 GUI 指令。

测试顺序：纯 Python 合同/状态机 → fake executor/共享步进 → 内存 OpenUSD → Isaac Sim。实验产物放 `tmp/`。勾选仅表示有实际证据，不能用文档声明替代运行结果。

阶段验收额外发现并覆盖：scene/env 身份、lease 提前释放、NaN/倒退时钟、同 task
新请求重试；Kit body relationship 改写连接帧；timeline STOP 原生视图清理；
关节创建 BaseException 后的所有权恢复。用户追加真实贴合要求：Iris 机腹实际
挂载点替代 30 cm 虚拟偏移，host 接触容差收紧到 2 mm，水平对齐与下降门槛一致。

最终证据：606 项检查通过；真实 GUI 双机共享步进、空中编辑器保存/载入/导出、
贴合搬运和携物停止/回源台面恢复通过。完整命令、误差和限制见
[验收记录](AERIAL_HARDENING_VALIDATION.zh-CN.md)，接入与 GUI 用法见
[运行指南](AERIAL_RUNTIME.zh-CN.md)。
