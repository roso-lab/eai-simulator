[English](community_workflow.md) | [中文](community_workflow.zh-CN.md)

# 公开社区协作流程

本文说明公开 GitHub 仓库和内部 GitLab 仓库如何配合维护。

## 仓库角色

- **内部 GitLab：** 维护主仓，负责受保护分支、CI、Merge Request、发布准备和安全问题私下处理。
- **公开 GitHub：** 公开代码镜像，负责 issue 收集、discussion 讨论、文档展示和社区 Pull Request 评审入口。

维护者仍以 GitLab 为事实主线。GitHub 需要尽量贴近 GitLab 的公开状态，但 GitHub issue 和
Pull Request 在完成 triage 之前都视为公开入口信息。

## 镜像设置

1. 在公开准备清单完成前，保持 `https://github.com/Huang-Qijun/eai-simulator.git` 为 private。
2. 在 GitHub 创建以下任一凭据：
   - 仅限该仓库、具备 **Contents: Read and write** 权限的 fine-grained token；
   - 或者一个专用于 GitLab 镜像、具备写权限的 deploy key。
3. 在 GitLab 打开 **Settings > Repository > Mirroring repositories**，添加指向 GitHub 的
   **Push** mirror。
4. 只镜像计划公开的维护面：
   - 默认分支，通常是 `main` 或 `master`；
   - 计划公开的 release 分支；
   - release tag。
5. 如果你们的 GitLab 版本支持分支过滤或 **only protected branches**，请启用它们，避免内部
   feature 分支被推送到 GitHub。
6. 保护 GitHub 默认分支。镜像凭据应是镜像分支的唯一直接写入者。
7. 第一次镜像时保持 GitHub private，镜像后检查是否误带资产、本地输出、凭据或内部笔记。

不要把 GitHub token、deploy key、GitLab token、本地缓存、本地 memory 文件或私有资产包提交进仓库。

## Issue 流程

1. 社区成员通过 GitHub Issues 提交可复现 bug、范围清晰的功能请求和文档问题。
2. 开放性问题、研究方向和早期想法先进入 GitHub Discussions。
3. 维护者使用公开标签 triage GitHub Issues：
   - `bug`
   - `enhancement`
   - `documentation`
   - `needs-info`
   - `accepted`
   - `wontfix`
4. 被接受的 issue 由维护者在内部 GitLab 创建对应 issue，并把 GitHub issue 链接复制进去。
5. 如果内部 GitLab issue 不可公开访问，不要把私有 GitLab 链接贴回 GitHub。可以只留言：
   `Accepted for internal tracking by maintainers`。
6. 实际开发在 GitLab 中通过 Merge Request 完成。
7. GitLab 合并并镜像到 GitHub 后，在 GitHub issue 中用简短公开总结、镜像 commit、tag 或 release
   链接关闭问题。

## Pull Request 流程

GitHub Pull Request 可以作为公开贡献入口，但维护者默认不要直接合并到 GitHub 的镜像默认分支。

1. 贡献者向公开 GitHub 仓库提交 Pull Request。
2. 维护者在 GitHub 上公开评审，并在需要时要求修改。
3. 如果决定接受，由维护者将改动 cherry-pick、应用 patch 或重建到 GitLab 分支。
4. 在 GitLab Merge Request 描述中保留原 GitHub 作者和 Pull Request 链接；合适时添加
   `Co-authored-by:` trailer。
5. GitLab 合并到受保护分支后，由镜像把改动同步到 GitHub。
6. 使用镜像 commit 或 release 链接关闭 GitHub Pull Request。

很小的文档 typo 可以直接在 GitHub 合并，但维护者必须立刻反向同步到 GitLab。默认规则仍然是
GitLab 优先。

## 发布流程

1. 在 GitLab 中完成发布稳定化。
2. 如果需要冻结窗口，创建 release 分支。
3. 将 release 分支合并到受保护默认分支。
4. 在 GitLab 创建 release tag。
5. 等待 GitLab push mirror 同步 GitHub 分支和 tag。
6. 基于镜像 tag 发布或更新 GitHub Release。
7. 确认 GitHub Pages 文档基于公开镜像状态构建成功。

GitHub Releases 用于公开 release notes。内部计划、私有测试日志和安全协调继续留在 GitLab。

## 公开前检查清单

将 GitHub 仓库从 private 切换到 public 之前，请确认：

- GitHub 默认分支只包含计划公开的分支内容。
- issue 模板、Pull Request 模板、支持策略、行为准则和安全策略已经存在。
- GitHub Discussions 已启用，并包含 Q&A 和 Ideas 分类。
- 如果需要公开文档站点，GitHub Pages 已配置好文档 workflow。
- GitHub 默认分支已开启保护。
- GitLab push mirror 至少成功同步过一次。
- `git diff --check` 通过。
- `rg -n '/home/airs|AGENTMEMORY|agentmemory|\\.env|PRIVATE KEY|TOKEN'` 除有意文档外没有公开风险匹配。
- 大型 USD 资产、下载的 controller 包、数据集、本地日志、缓存目录和个人 memory 文件已经被忽略或放在
  Git 外。

## 维护节奏

- 至少每周 triage 一次 GitHub Issues 和 Discussions。
- 公开 issue 被接受后，先提升到 GitLab，再开始实现。
- GitLab 受保护分支和 tag 准备好后，尽快镜像 release 到 GitHub。
- 对内部追踪中的 issue，公开回复保持简短、事实明确。
- 当社区开始稳定贡献 Pull Request 后，再重新评估是否需要更自动化的同步流程。
