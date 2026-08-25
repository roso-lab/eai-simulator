# 仓库同步流程

GitLab `develop` 是维护主线。服务器桥接器会把其中的公开子集发布到 GitHub `main`，并让社区 Pull Request 经过 GitLab 评审，不依赖 GitHub Actions。

## 发布内容

- `docs/` 下的网站源码和文档部署 workflow 只保留在内部 GitLab，在 GitHub `main` 的每一个可达提交中都不存在。
- 公开仓库指南和 README 所需的精选媒体继续保留在 GitHub。
- 只修改私有资料的 GitLab commit 不生成空的公开 commit；混合 commit 会保留公开改动及其作者、提交者、时间、消息和合并拓扑。
- 桥接器拒绝 GitHub `main` 的意外直接改动。维护者通过 GitLab Merge Request 合并主线改动。

## Pull Request 流程

1. 贡献者向 GitHub `main` 提交 Pull Request。
2. 桥接器拒绝对 GitLab-only 路径的改动，把 Pull Request 的公开 patch 应用到最新 GitLab `develop`，并推送受管理的 `github-pr/N` 分支。
3. 桥接器创建或更新目标为 `develop` 的 GitLab Merge Request。
4. GitLab 评审期间保持 GitHub Pull Request 打开。后续 commit 会更新同一个分支和 Merge Request。
5. GitLab 管理员评审并在不 squash 的情况下合并。受管理的 commit 会把 GitHub Pull Request head 保留为合并祖先。
6. 桥接器发布评审后的 GitLab 结果，GitHub 随后会把原 Pull Request 识别为已合并，并保留贡献者归属。

关闭或重新打开尚未合并的 GitLab Merge Request，会同步关闭或重新打开 GitHub Pull Request。关闭尚未合并的 GitHub Pull Request，也会关闭对应的 GitLab Merge Request。不要直接推送 `github-pr/*`，这些分支由桥接器管理。

## 运行保护

- 签名的 GitHub 和 GitLab webhook 会即时启动同步，每分钟对账负责修复遗漏的 webhook。
- SQLite 保存 webhook、Pull Request 映射、源 commit 到公开 commit 的映射以及上次发布位置。
- GitHub `main` 意外改动、GitLab `develop` 历史重写或未经迁移的过滤规则变更都会停止发布，不会覆盖现有状态。
- GitLab-only 路径引用的 Git LFS 对象不会推送到 GitHub。

报告同步失败时可以使用 GitHub Issue，但不要发布 token、webhook payload、服务器路径或其他私有运行数据。
