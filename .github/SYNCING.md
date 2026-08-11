# Repository synchronization

The GitLab `develop` branch and GitHub `main` branch are synchronized by the
`Synchronize GitLab and GitHub` GitHub Actions workflow.

- GitLab changes are pulled into GitHub on the scheduled workflow run.
- Opening or updating GitHub pull request `N` mirrors its commits to the
  managed GitLab branch `github-pr/N` and opens or updates a merge request
  targeting `develop`.
- Keep the GitHub pull request open while the GitLab merge request is under
  review. Do not merge the GitHub pull request first.
- After a GitLab administrator merges the merge request, the scheduled pull
  brings the GitLab merge commit into GitHub `main`. GitHub then marks the
  original pull request as merged.
- Do not commit directly to `github-pr/*`; GitHub Actions may replace these
  branches.
- Additional commits pushed to an open GitHub pull request update the same
  GitLab branch and merge request.
- Do not squash synchronization merge requests. Keeping the original commits
  preserves author attribution on both services.
- Resolve merge conflicts through the merge request before merging it.

The synchronization workflow itself remains GitHub-only and is excluded from
the GitLab merge request.
