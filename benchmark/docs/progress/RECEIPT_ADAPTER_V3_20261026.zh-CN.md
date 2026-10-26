# evaluator 收据到论文结果包的生产接线（2026-10-26）

本阶段将工程主线从“手工/合成 normalized package 可计算”推进到
“evaluator-owned 原始收据可自动生成 normalized package”。实现入口为
`benchmark/receipt_adapter_v3.py`，不导入 Isaac、GPU 或被测方法。

真实 case 还应提供 `benchmark/case_manifest_v1.py` 定义的
`heterocoord.case-manifest.v1`。适配器在传入 `case_manifest=` 时会逐条核对
task、anchor、domain、relation、base instance、catalog/plan digest 和 evaluator
version，并把 case、scene、semantic-map、topology、roster、contract、oracle 与
evaluator provenance 纳入 normalized package 的 `input_digest`。

## 接口边界

输入使用 `heterocoord.raw-evaluator-receipt.v1`。每条收据必须携带：

- task/anchor/domain/relation/repeat/branch/checkpoint 身份；
- 与其他收据完全一致的 catalog、plan、参数；
- evaluator version、外部 evidence hash 和 evidence refs；
- 事件流、资源/接触区间、里程碑、身份账本以及完整性标记；
- evaluator-owned 的 D1、义务、D4、D5、D6 事实。

v1 的 `evaluator.evidence_hash` 定义为去掉该字段后的 canonical receipt
envelope SHA-256，用来拒绝合法格式但内容被替换的收据；`evidence_refs` 指向的
外部文件仍由 `artifact_verifier_v3` 独立做字节级校验。

适配器会调用 `oracle_predicates.judge_anchor()` 并保留去重后的 verdict 摘要，
再将 D1–D6 所需的固定目录字段生成给 `results_pipeline_v3`。任何
`unknown`、`incomplete`、基础设施无效、缺少 C0–C3、缺少 D3 relation support、
缺少 D6 独立校准的输入都拒绝生成点估计；不会把缺失改写为失败。

输出固定使用 `measured_unqualified` 或 `measured_diagnostic` scope，结果管线
仍强制 `rankable=false`、`paper_score_eligible=false`、`physical_qualified=false`。
D5 在 family/case/unit 固定目录未达到论文 floor 时显式保持 NA/排除披露。

## 当前有意保留的限制

适配器已经建立了生产数据入口，但不宣称 evaluator 本身可信或完成物理资格。
真实执行器仍需生成 evaluator-owned 收据；D4 的功能丢失配对、D5 的六方向目录、
D6 的独立 calibration 和 G1–G3 GPU 资格仍是上游证据任务。

下一步先用一个 CPU/模拟纵向切片验证：四个通信条件、四类 D3 obligation、
一个 D4 closure、D6 calibration 和 oracle verdict 能被一次性归一化并重放；随后
再将同一输入契约接入真实 GPU evaluator。测试、回归和检测由 ZCode 执行，主代理
只负责接口、语义和科学验收。

## 生产入口（2026-10-26）

`benchmark/production_results_v1.py` 现在提供单一 CPU-only 编排入口：

`receipts + case-manifest + artifact evidence`
→ `normalize_receipts`
→ `compute_results`
→ `export_results`
→ `verify_export`

它强制要求真实 case manifest、artifact manifest 和 artifact root，拒绝退回
legacy/diagnostic 输入路径；输出仍固定为 `measured_unqualified` / `diagnostic_only`，
三个正式资格门保持 false。该入口只组合已有边界，不从进程退出、caller score 或
锁服务应答推断科学事实。ZCode 负责用 CPU fixture 验收正例、篡改 artifact 负例和
replay；真实 evaluator receipt 到位后复用同一命令链。

ZCode CPU/模拟纵向切片已完成：good artifact root 正例 exit 0，输出 7 个文件并通过
独立 `verify_export`；tampered/missing evidence 分别以 mismatch/missing exit 2，且没有
生成结果目录。该结果证明软件接缝可部署，不代表真实 evaluator 或论文资格已经完成。
