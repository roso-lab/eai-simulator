[English](../CONTRIBUTING.md) | [中文](CONTRIBUTING.zh-CN.md)

# 为 EAI Simulator 做贡献

感谢你帮助改进 EAI Simulator。GitHub 是公开社区入口，维护者继续在内部 GitLab 仓库中进行主线开发。
完整镜像与 triage 流程见 [community_workflow.zh-CN.md](community_workflow.zh-CN.md)。

## 开始之前

- 新建 thread 前请先搜索已有 GitHub Issues 和 Discussions。
- 问答、早期想法和设计探索请使用 GitHub Discussions。
- 可复现 bug、范围清晰的功能请求和文档问题请创建 GitHub Issue，并说明问题、预期结果和验收条件。
- 每项改动只解决一个 Issue 或 Discussion，避免混入无关重构。
- 不要通过公开 Issue、Pull Request、Discussion 或聊天报告安全漏洞。请遵循
  [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。

## 开发环境

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
./tools/setup-git-hooks.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
./tools/install_packages.sh
```

## 分支

| 类型 | 格式 | 示例 |
|---|---|---|
| 主分支 | `main` 或 `master` | `main` |
| 开发分支 | `develop` 或 `development` | `develop` |
| 功能 | `feature/<name>` | `feature/robot-import` |
| Bug 修复 | `bugfix/<name>` | `bugfix/env-loading` |
| 常规修复 | `fix/<name>` | `fix/config-validation` |
| 紧急修复 | `hotfix/<name>` | `hotfix/security-check` |
| 发布 | `release/<name>` | `release/v0.2.0` |
| 维护 | `chore/<name>` | `chore/update-dependencies` |
| 构建 | `build/<name>` | `build/package-metadata` |
| 文档 | `docs/<name>` | `docs/quick-start` |
| 重构 | `refactor/<name>` | `refactor/controller-loader` |
| 样式 | `style/<name>` | `style/markdown-formatting` |
| 测试 | `test/<name>` | `test/env-builder` |
| CI | `ci/<name>` | `ci/docs-build` |
| 性能 | `perf/<name>` | `perf/asset-loading` |

## Commit 消息

每条常规 commit 消息必须以关联 Issue ID 开头。维护者在工作已经提升到 GitLab 后使用内部 GitLab
issue ID；公开贡献者也可以使用 GitHub issue 编号：

```text
#<IID> <描述>
```

示例：

```bash
git commit -m "#123 添加机器人配置校验"
git commit -m "#456 修复资产下载重试处理"
```

Merge、Revert、`fixup!`、`squash!` 和 `amend!` commit 不受此校验限制。

## 测试与文档

运行与改动行为最接近的测试。修改公开行为时，应在同一个 Pull Request 或 Merge Request 中更新文档。

文档改动需要在本地构建 Sphinx 站点：

```bash
python -m pip install -r docs/requirements.txt
make -C docs clean html
```

## Pull Request 和 Merge Request

GitHub Pull Request 可以作为公开评审入口，但 GitLab 仍然是维护主仓。如果 GitHub Pull Request
被接受，维护者可以将 patch 搬运到 GitLab Merge Request，再将最终结果镜像回 GitHub。

每个 Pull Request 或 Merge Request 应当：

- 关联对应 Issue 或 Discussion。
- 说明改动及其对用户可见的影响。
- 列出验证命令和结果。
- 说明兼容性或迁移注意事项。
- 对用户界面改动提供截图。

不要提交私有资产、凭据、本地缓存、本地 memory 文件、实验输出或内部专用笔记。
