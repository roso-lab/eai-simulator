# 论文基线冻结审计（2026-10-24）

本记录冻结“唯一最新 Overleaf / 本地镜像”身份，不修改论文正文，也不把旧工程身份冒充为最新论文身份。

## 结论

权威项目是 `overleaf-old`：

- URL：`https://git@git.overleaf.com/6ab4c7c4e984abb8da9c8113`
- `main`：`0b006d4efcd25d5160d4b0a6e75cddd9e5265e44`
- 时间：`2026-10-24T04:33:52+00:00`

另一个 `origin` Overleaf 项目 HEAD 为 `d20acb4ff26acadea3d7005d64694ccbfa60a3c6`（2026-09-29），是旧项目，不能作为当前论文身份。

本地镜像 `/home/pupil/CVPR2026_Template` 工作树保持干净；审计时已抓取 `overleaf-old/main`，但没有覆盖本地分支或提交任何变更。

## 与工程现状的关系

工程核心身份常量、24 锚点合同和 composite 计算原语已迁移到 `0b006d4…`；但 D5 全方向目录和 common-anchor 覆盖的语义迁移尚未完成。旧进度文档仍保留 `72b6a3b…` 作为历史记录，不能生成正式实验身份、开启 formal gate 或作为最新论文证据。

最新论文相对旧工程身份新增/改变了复合协调分数、D5 全六方向的 P1–P4 迁移面板、全 12 个 domain–primitive common-anchor 语义，以及包含 composite 的多重性族。迁移完成前，现有合同与三路 GPU 收据只能作为诊断/兼容性证据。

机器可读冻结收据见 [PAPER_BASELINE_FREEZE_20261024.json](/home/pupil/HeteroCoord/artifacts/protocol/PAPER_BASELINE_FREEZE_20261024.json)。

## 下一步门槛

1. 为 D5 方向和 12 common-anchor/48 common-cell 建立字段映射，并把运行器 manifest 的身份状态升级为已验证。
2. 完成离线三路端到端验收，确认 evaluator-owned 状态覆盖和 fail-closed 收据。
3. 由独立审查确认迁移与运行器语义后，再冻结新目录并请求一次真实 GPU 放行。

在这些门槛完成前，所有 physical/process-isolation/native-full-state/paper-score/rankable flags 继续为 `false`。
