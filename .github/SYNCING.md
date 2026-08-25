# Repository synchronization

GitLab `develop` is the canonical maintenance branch. A server-side bridge
publishes its public subset to GitHub `main` and mirrors community pull requests
through GitLab review without using GitHub Actions.

## Published content

- Website source under `docs/` and its deployment workflow remain available to
  the internal GitLab checkout but are absent from every commit reachable from
  GitHub `main`.
- Public repository guides and selected README media remain on GitHub.
- Private-only GitLab commits do not create empty public commits. Mixed commits
  retain their public changes, author, committer, timestamps, message, and merge
  topology.
- Direct changes to GitHub `main` are rejected by the bridge. Maintainers merge
  canonical changes through GitLab merge requests.

## Pull request flow

1. A contributor opens a GitHub pull request against `main`.
2. The bridge rejects changes to GitLab-only paths, applies the pull request's
   public-path patch to the latest GitLab `develop`, and pushes the managed
   `github-pr/N` branch.
3. The bridge opens or updates a GitLab merge request targeting `develop`.
4. Keep the GitHub pull request open during GitLab review. Additional commits
   update the same managed branch and merge request.
5. A GitLab administrator reviews and merges the merge request without
   squashing. The managed commit retains the GitHub pull request head as merge
   ancestry.
6. The bridge publishes the reviewed GitLab result. GitHub then recognizes the
   original pull request as merged and preserves contributor attribution.

Closing or reopening an unmerged managed GitLab merge request closes or reopens
the corresponding GitHub pull request. Closing an unmerged GitHub pull request
closes its managed GitLab merge request. Do not push directly to
`github-pr/*`; the bridge owns those branches.

## Operational safeguards

- Signed GitHub and GitLab webhooks start synchronization immediately. A
  one-minute reconciliation loop repairs missed webhook deliveries.
- SQLite stores webhook delivery state, pull request mappings, source-to-public
  commit mappings, and the last published tips.
- Unexpected GitHub `main` changes, rewritten GitLab `develop` history, and
  unreviewed path-filter changes stop publication instead of overwriting state.
- Git LFS objects for GitLab-only paths are not pushed to GitHub.

Report synchronization failures through a GitHub issue without posting tokens,
webhook payloads, server paths, or other private operational data.
