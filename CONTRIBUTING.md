[English](CONTRIBUTING.md) | [中文](docs/CONTRIBUTING.zh-CN.md)

# Contributing to EAI Simulator

Thank you for helping improve EAI Simulator. GitHub is the public community entry point, while
maintainers continue canonical development in the internal GitLab repository. See
[docs/community_workflow.md](docs/community_workflow.md) for the full mirror and triage process.

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
git clone https://github.com/Huang-Qijun/eai-simulator.git
cd eai-simulator
./tools/setup-git-hooks.sh
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
./tools/install_packages.sh
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

For documentation changes, build the Sphinx site locally:

```bash
python -m pip install -r docs/requirements.txt
make -C docs clean html
```

## Pull Requests and Merge Requests

GitHub pull requests are welcome for public review, but GitLab remains the canonical maintenance
repository. If a GitHub pull request is accepted, maintainers may port the patch into a GitLab
merge request and mirror the final result back to GitHub.

Every pull request or merge request should:

- Link the related Issue or Discussion.
- Explain the change and its user-visible effect.
- List the verification commands and results.
- Identify compatibility or migration concerns.
- Include screenshots for user-interface changes.

Do not commit private assets, credentials, local cache, local memory files, experiment outputs, or
internal-only notes.
