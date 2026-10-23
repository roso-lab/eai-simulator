# 工具双份收敛规则（2026-10-21）

本规则把 Tool 验证工作树收敛为冻结基线，避免主工程与验证副本继续形成两条开发线。

## 规则

1. `/home/pupil/HeteroCoord/tmp/tool-replay-optimization`（`c51e86d7`）和
   `/home/pupil/HeteroCoord/tmp/tool-vectorized-isolation`（`90451fdd`）只作为历史验证基线读取。
   不在其中追加实现、测试、文档或生成产物。
2. 后续 Tool 修复、R7/shared-session 适配、测试和文档全部写入
   `/home/pupil/HeteroCoord` 主工程；主工程是唯一后续开发线。
3. 若需要对照基线，记录基线路径、提交 SHA、输入指纹和差异摘要；不得把副本的新修改
   反向当作主工程事实，也不得用副本覆盖主工程 dirty 修改。
4. Tool worktree 的已有结果可以引用为历史证据，但不能自动升级为当前正式资格证据；正式门
   仍按论文条款和主工程认证收据判定，并继续保持关闭。
5. 删除、归档或重建这两个 worktree 需要用户单独确认。本阶段不删除任何 worktree、模板、
   artifact 或 dirty 文件。

## 当前冻结清单

| 路径 | 冻结 SHA | 用途 |
|---|---|---|
| `tmp/tool-replay-optimization` | `c51e86d756210e9f76623db25fbf090d282b6234` | 分支回溯/有界重放历史验证 |
| `tmp/tool-vectorized-isolation` | `90451fddd0b5b70c17df7dee4e1f363091fdb92b` | 向量化分支隔离历史验证 |

## 待用户确认的临时目录候选

- 删除候选 1：上述 `tmp/tool-replay-optimization` 验证工作树。
- 删除候选 2：上述 `tmp/tool-vectorized-isolation` 验证工作树。
- 旧模板候选：`tmp/CVPR2026_Template`。它不是主论文目录，也不作为当前 Overleaf
  正文来源；是否删除另行确认。

`tmp/physical_calibration` 和 `tmp/project_runtime` 暂不列入删除候选：其中包含校准、日志、
锁和运行快照，当前没有足够证据证明可以安全清除。
