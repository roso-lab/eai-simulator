[English](community_workflow.md) | [中文](community_workflow.zh-CN.md)

# Public Community Workflow

This document defines how the public GitHub repository and the internal GitLab repository work
together.

## Repository Roles

- **Internal GitLab:** canonical development repository, protected branches, CI, merge requests,
  release preparation, and confidential security work.
- **Public GitHub:** public code mirror, issue intake, discussions, documentation visibility, and
  community pull request review.

GitLab remains the source of truth for maintainers. GitHub should stay close to the public state
of GitLab, but GitHub issues and pull requests are treated as public intake until maintainers
triage and port the work internally.

## Mirror Setup

1. Keep `https://github.com/Huang-Qijun/eai-simulator.git` private until the public-readiness
   checklist below is complete.
2. In GitHub, create either:
   - a fine-grained token scoped to this repository with **Contents: Read and write**, or
   - a write-enabled deploy key dedicated to the GitLab mirror.
3. In GitLab, open **Settings > Repository > Mirroring repositories** and add a **Push** mirror to
   the GitHub repository.
4. Mirror only the public maintenance surface:
   - the default branch, usually `main` or `master`;
   - release branches that are meant to be public;
   - release tags.
5. If your GitLab version exposes branch filters or **only protected branches**, use them so
   internal feature branches are not pushed to GitHub.
6. Protect the GitHub default branch. The mirror credential should be the only direct writer to
   the mirrored branch.
7. Run the first mirror while GitHub is still private, then inspect the GitHub repository for
   accidental assets, local output, credentials, and internal-only notes.

Never commit GitHub tokens, deploy keys, GitLab tokens, local cache, local memory files, or private
asset bundles to this repository.

## Issue Flow

1. Community members open GitHub Issues for reproducible bugs, scoped feature requests, and
   documentation problems.
2. Open-ended questions, research directions, and early proposals go to GitHub Discussions first.
3. Maintainers triage GitHub Issues with public labels:
   - `bug`
   - `enhancement`
   - `documentation`
   - `needs-info`
   - `accepted`
   - `wontfix`
4. Accepted issues get an internal GitLab issue created by a maintainer. Copy the GitHub issue URL
   into the GitLab issue.
5. If the internal GitLab issue is not publicly visible, do not paste private GitLab URLs back into
   GitHub. Instead, leave a comment such as `Accepted for internal tracking by maintainers`.
6. Development happens in GitLab through merge requests.
7. After the GitLab merge is mirrored to GitHub, close the GitHub issue with a short public summary
   and the mirrored commit, tag, or release link.

## Pull Request Flow

GitHub pull requests are welcome as public contributions, but maintainers should avoid merging
directly into the mirrored GitHub default branch.

1. A contributor opens a GitHub pull request against the public repository.
2. Maintainers review the pull request publicly and ask for changes there when useful.
3. If accepted, a maintainer ports the patch to GitLab by cherry-picking, applying a patch, or
   recreating the change on a GitLab branch.
4. Preserve attribution with the original GitHub author and pull request link in the GitLab merge
   request description. Add a `Co-authored-by:` trailer when appropriate.
5. The GitLab merge reaches the protected branch, then the mirror updates GitHub.
6. Close the GitHub pull request with the mirrored commit or release link.

Small documentation typo fixes may be merged directly on GitHub only if maintainers intentionally
back-port them to GitLab immediately. The default rule is still GitLab first.

## Release Flow

1. Stabilize release work in GitLab.
2. Create a release branch if the release needs a freeze window.
3. Merge the release branch into the protected default branch.
4. Create the release tag in GitLab.
5. Let the GitLab push mirror update GitHub branches and tags.
6. Publish or update the GitHub Release from the mirrored tag.
7. Confirm GitHub Pages documentation builds from the mirrored public state.

Use GitHub Releases for public release notes. Keep internal planning, private test logs, and
security coordination in GitLab.

## Public-Readiness Checklist

Before switching the GitHub repository from private to public:

- GitHub default branch contains the intended public branch only.
- Issue templates, pull request template, support policy, code of conduct, and security policy are
  present.
- GitHub Discussions are enabled with categories for Q&A and Ideas.
- GitHub Pages is configured for the documentation workflow if public docs should be hosted there.
- Branch protection is enabled on the GitHub default branch.
- GitLab push mirror has completed at least one successful sync.
- `git diff --check` passes.
- `rg -n '/home/airs|AGENTMEMORY|agentmemory|\\.env|PRIVATE KEY|TOKEN'` has no public-risk matches
  outside intentional documentation.
- Large USD assets, downloaded controller bundles, datasets, local logs, cache directories, and
  personal memory files are ignored or stored outside Git.

## Maintainer Cadence

- Triage GitHub Issues and Discussions at least weekly.
- Promote accepted public issues to GitLab before implementation starts.
- Mirror releases from GitLab to GitHub as soon as the protected branch and tag are ready.
- Keep public comments short and factual when an issue is tracked internally.
- Revisit this workflow when the community starts contributing regular pull requests.
