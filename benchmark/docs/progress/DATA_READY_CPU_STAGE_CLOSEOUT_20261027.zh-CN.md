# 数据就绪 CPU 阶段收尾（2026-10-27）

## 阶段结论

本阶段完成两条互补的 CPU-only 生产接缝：`room_06 × liv-t-04` 从场景目录、资产摘要和观测事件进入 terminal-material 评价边界；D1–D6 statistical-report 链从完整 reducer 输入与事件账本生成内容寻址的 v2 报告、索引和发布绑定。两条线都保持正式资格门为 `false`，合成测试与静态场景检查没有被升级为真实物理或论文结果。

整个阶段设置 `CUDA_VISIBLE_DEVICES=''`，没有启动 Isaac Sim、Kit、SimulationApp 或 CUDA 任务，也没有修改用户主工作树。GPU 继续留给语义地图制作。

## 已完成

1. room_06 CPU 纵向接线：
   - 固定逻辑 USD 路径与 SHA-256，接入 EAI/Isaac 场景目录；
   - 去活内嵌 PhysicsScene，建立场景自有 raycast proxy；
   - observer-owned 完成事件和完整 `held_object` 快照进入里程碑/联合持有判定；
   - 命令、锁应答、未分配机器人、单机持有、缺采样和篡改均不能伪造成功证据。
2. 统计证据 v2：
   - 完整 reducer payload、结果身份、ledger、支持登记与实现摘要绑定；
   - `observed / unavailable / not_applicable` 三态分离真实零、未知和不适用；
   - 合法 `status="na"` 逐键生成 `not_estimable`，不污染同批结果；
   - 下游自校验拒绝时回滚本轮全部新工件，不残留半成品。
3. 派生清单同步：room_06 修改 `env_builder.py` 后，相机注册值未变，仅源摘要漂移；已更新 `observation_camera_registration_patch.v1.json` 的生成时间和源摘要。

## 验收事实

- 统计 focused：`92 passed / 0 failed / 0 errors / 0 skipped`，退出码 0；
- room_06 focused：`120 passed / 0 failed / 0 errors / 0 skipped`，退出码 0；
- 推送边界全量只执行一次：`1046 passed / 1 failed / 0 errors / 0 skipped`；唯一失败为相机注册派生清单的旧源摘要；
- 摘要修复后仅复验唯一失败项：`1 passed`，退出码 0；其余 1046 项未重复执行；
- `git diff --check` 通过。

因此当前提交上的 1047 个 benchmark 测试均有本批次通过证据，但证据来自“一次全量中的 1046 项 + 修复后单项 1 项”，不是虚构的一次 1047 全绿运行。

## 明确未完成

- 未执行真实 GPU `room_06 × liv-t-04` 场景加载、技能运行与 terminal receipt；
- 未生成真实测量的 D1–D6 statistical input/report/release；旧 v1 统计工件必须从未来真实输入重新生成；
- 未证明物理、GPU、配置或论文资格，正式门继续保持 `false`。

GPU 可用后的下一步是冻结单个隔离纵向探针，真实加载 room_06，驱动观测桥至终局，并把 terminal material 接入现有 receipt/production/statistical v2 链。失败必须原样留证，不静默重跑。
