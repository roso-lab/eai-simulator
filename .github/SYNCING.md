# Repository synchronization

The GitLab `develop` branch and GitHub `main` branch are synchronized by the
`Synchronize GitLab and GitHub` GitHub Actions workflow.

- GitLab changes are pulled into GitHub on the scheduled workflow run.
- GitHub changes are pushed to the managed GitLab branch
  `github-sync/main`, which opens or updates a merge request targeting
  `develop`.
- Do not commit directly to `github-sync/main`; GitHub Actions may replace it.
- Do not squash synchronization merge requests. Keeping the original commits
  preserves author attribution on both services.
- Resolve merge conflicts through the merge request before merging it.

The synchronization workflow itself remains GitHub-only and is excluded from
the GitLab merge request.
