[English](#contributing-to-eai-simulator) | [中文](#为-eai-simulator-做贡献)

# Contributing to EAI Simulator

Thank you for helping improve EAI Simulator. GitHub is the public community entry point, while
maintainers continue canonical development in the internal GitLab repository. See
[SYNCING.md](SYNCING.md) for the full mirror and triage process.

## Before You Start

- Search existing GitHub Issues and Discussions before opening a new thread.
- Use GitHub Discussions for questions, early ideas, and design exploration.
- Open a GitHub Issue for reproducible bugs, scoped feature requests, or documentation problems.
  Describe the problem, proposed result, and acceptance criteria.
- Keep changes focused on one Issue or Discussion and avoid unrelated refactoring.
- Do not report vulnerabilities in a public Issue, pull request, discussion, or chat. Follow
  [SECURITY.md](SECURITY.md).

## Development Setup

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
./tools/setup/install_packages.sh
```

## Branches

| Type | Pattern | Example |
|---|---|---|
| Main | `main` or `master` | `main` |
| Development | `develop` or `development` | `develop` |
| Feature | `feature/<name>` | `feature/robot-import` |
| Bug fix | `bugfix/<name>` | `bugfix/env-loading` |
| General fix | `fix/<name>` | `fix/config-validation` |
| Hotfix | `hotfix/<name>` | `hotfix/security-check` |
| Release | `release/<name>` | `release/v0.2.0` |
| Maintenance | `chore/<name>` | `chore/update-dependencies` |
| Build | `build/<name>` | `build/package-metadata` |
| Documentation | `docs/<name>` | `docs/quick-start` |
| Refactor | `refactor/<name>` | `refactor/controller-loader` |
| Style | `style/<name>` | `style/markdown-formatting` |
| Test | `test/<name>` | `test/env-builder` |
| CI | `ci/<name>` | `ci/docs-build` |
| Performance | `perf/<name>` | `perf/asset-loading` |

## Commit Messages

Every regular commit message must start with its related Issue ID. Maintainers use the internal
GitLab issue ID when the work has been promoted to GitLab; public contributors may use the GitHub
issue number:

```text
#<IID> <description>
```

Examples:

```bash
git commit -m "#123 add robot configuration validation"
git commit -m "#456 fix asset download retry handling"
```

Merge, Revert, `fixup!`, `squash!`, and `amend!` commits are exempt from this validation.

## Testing and Documentation

Run the tests closest to the changed behavior. If you change public behavior, update its
documentation in the same pull request or merge request.

The hosted website source is maintained in the internal GitLab view and is not
part of the public GitHub export. Public contributors can update repository
guides such as this file or report website documentation changes with the
documentation issue template. Do not recreate the private `docs/` tree in a
public pull request.

## Pull Requests and Merge Requests

GitHub pull requests are welcome for public review, but GitLab remains the canonical maintenance
repository. The synchronization bridge projects the public patch onto a managed
GitLab merge request and publishes the reviewed result back to GitHub.

Every pull request or merge request should:

- Link the related Issue or Discussion.
- Explain the change and its user-visible effect.
- List the verification commands and results.
- Identify compatibility or migration concerns.
- Include screenshots for user-interface changes.

Do not commit private assets, credentials, local cache, local memory files, experiment outputs, or
internal-only notes.

---

# 为 EAI Simulator 做贡献

感谢你帮助改进 EAI Simulator。GitHub 是公开社区入口，维护者继续在内部 GitLab 仓库中进行主线开发。完整同步与 triage 流程见 [SYNCING.zh-CN.md](SYNCING.zh-CN.md)。

## 开始之前

- 新建 thread 前请先搜索已有 GitHub Issues 和 Discussions。
- 问答、早期想法和设计探索请使用 GitHub Discussions。
- 可复现 bug、范围清晰的功能请求和文档问题请创建 GitHub Issue，并说明问题、预期结果和验收条件。
- 每项改动只解决一个 Issue 或 Discussion，避免混入无关重构。
- 不要通过公开 Issue、Pull Request、Discussion 或聊天报告安全漏洞。请遵循 [SECURITY.zh-CN.md](SECURITY.zh-CN.md)。

## 开发环境

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
./tools/setup/install_packages.sh
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

每条常规 commit 消息必须以关联 Issue ID 开头。维护者在工作已经提升到 GitLab 后使用内部 GitLab issue ID；公开贡献者也可以使用 GitHub issue 编号：

```text
#<IID> <描述>
```

Merge、Revert、`fixup!`、`squash!` 和 `amend!` commit 不受此校验限制。

## 测试与文档

运行与改动行为最接近的测试。修改公开行为时，应在同一个 Pull Request 或 Merge Request 中更新文档。

网站源码由内部 GitLab 维护，不包含在公开 GitHub 导出中。公开贡献者可以修改仓库内的指南，或通过 documentation issue 模板报告网站文档问题。不要在公开 Pull Request 中重新创建私有 `docs/` 目录。

## Pull Request 和 Merge Request

GitHub Pull Request 是公开贡献入口，但 GitLab 仍然是维护主仓。同步桥接器会把公开 patch 投影到受管理的 GitLab Merge Request，并在评审合并后把结果发布回 GitHub。

每个 Pull Request 或 Merge Request 应当：

- 关联对应 Issue 或 Discussion。
- 说明改动及其对用户可见的影响。
- 列出验证命令和结果。
- 说明兼容性或迁移注意事项。
- 对用户界面改动提供截图。

不要提交私有资产、凭据、本地缓存、本地 memory 文件、实验输出或内部专用笔记。
