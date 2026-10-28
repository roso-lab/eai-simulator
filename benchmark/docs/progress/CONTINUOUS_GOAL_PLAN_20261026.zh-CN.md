# 持续 Goal：真实语义 case → 论文结果包

## 目标与职责

本 Goal 保持 `active`，停止条件不是“测试通过”，而是一个真实语义 case 的终局 evaluator 收据能够经过完整证据链，自动生成可重放的论文结果包。正式资格门在 D2–D6 固定目录、物理证据、独立校准和发布审计完成前保持关闭。

- 主代理：论文版本与 estimand 对齐、系统架构、接口实现、科学语义判断、优先级、失败分级和最终验收。
- ZCode：按冻结任务书执行 `py_compile`、pytest、schema/digest/负例检测、只读盘点和证据提取；不改源码/测试/论文/正式 artifacts，不启动 GPU/CUDA/Isaac/Kit。
- 协作者：提供真实 semantic-map/topology、runtime-qualified robot roster、合同/oracle/replay/evaluator identity，以及真实 terminal receipt 所需的 evaluator-owned streams/facts。

## 当前基线

论文身份冻结为 `overleaf-old/main@0b006d4efcd25d5160d4b0a6e75cddd9e5265e44`。当前已完成：

1. `case_input_audit_v1`：地图和机器人池逐字节审计，空语义、旧版、review-only、未绑定 runtime 身份 fail-closed；
2. `case_manifest_builder_v1`：只从显式 binding 组装唯一 `case-manifest.v1`，不猜任务语义；
3. `receipt_producer_v1` / `receipt_bridge_v1`：显式 episode/streams/facts → raw receipt，双层 digest 和 artifact manifest 校验；
4. `production_results_v1`：raw receipt → oracle/obligation → D1–D6 → export → replay verify；输出仍为 `measured_unqualified` / `diagnostic_only`；
5. `receipt_material.py`：新增 evaluator 终局材料接缝。实测 isolated worker 必须实现 `collect_receipt_material()` 并明确提供 `episode/streams/facts` 和 evaluator verdict；不会从进程退出、`completed`、进度或 caller score 推断科学事实。

当前真实输入仍未 case-ready：旧 `home_02` semantic-map 为不支持版本/空语义产品，现有 robot pool 为 review-only 且 runtime-qualified 数量为 0。不得生成真实 manifest，也不得用 synthetic fixture 越级替代。

## 推进阶段

### G0：接缝硬化（当前）

- 主代理完成 evaluator-owned receipt-material hook；
- ZCode 复测编译、focused regression、缺 hook/缺 streams/缺 facts/伪造完成状态等负例；
- 通过标准：生产 worker 没有显式材料时成为 `not_evaluable`，不产生 raw receipt 或论文分数；有材料时 artifact 可被 bridge 验证。

### G1：单个真实 case

协作者材料到位后，顺序固定为：

`input audit → case-manifest builder → manifest validation → evaluator terminal artifact → receipt bridge/producer → normalize → compute → export → replay verify`。

主代理审查语义闭包和证据等级；ZCode 只执行命令并报告 exit code、日志和未运行项。真实收据至少包含完整 milestone/event/zone/contact/identity streams、D1 facts、D3 obligations、D4 closure、D6 calibration 引用和 artifact manifest。

### G2：论文固定支持目录

G1 单 case 通过后，再建立 D2 C0–C3、D3 relation ledger、D4 healthy/loss closure、D5 source/target/sham/causal replay（每 family 至少 30 cases）和 D6 独立 calibration。单 case 不能代替论文矩阵；目录缺项只能 `incomplete/NA`。

### G3：GPU 诊断与恢复等价

GPU 只负责产生可审计执行/恢复收据。ToDesk 等显示服务不属于 benchmark 语义或公开资格依赖。三路不一致只保留诊断差异，不打开资格门；GPU 阶段必须等 G1 接缝和显存/进程安全策略明确后再进入。

### G4：独立审计与发布

独立复核 paper/scorebook/catalog/case/roster/evaluator/calibration/artifact/report hash 链、precision/statistical/physical/release gates。此前 `rankable`、`paper_score_eligible`、`physical_qualified` 和认证 flags 均为 `false`。

## 不再采用的流程

不再由主代理反复等待测试、逐项请求运行权限或把回归数量当作开发进度。测试发现契约缺陷时由主代理修复设计，再把固定复测交回 ZCode；ZCode 的轮次超时只登记为验证 harness 未完成，不得虚报全绿，也不得把 synthetic/CPU 结果升级为论文结果。
