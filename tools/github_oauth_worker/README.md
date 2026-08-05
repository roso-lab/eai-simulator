# EAI Community GitHub OAuth Worker

This Cloudflare Worker performs the server-side portion of GitHub OAuth for the
static documentation community page. It exchanges the authorization code,
loads the GitHub profile and verified email, and returns only those profile
fields to the originating page with `postMessage`. The GitHub access token is
never returned to or stored by the browser.

## Required secrets

Create a GitHub OAuth App, or reuse a GitHub App that allows user
authorization. For a GitHub App, set the account permission
`Email addresses` to `Read-only`; repository and Discussions permissions
are not required. Then configure these Worker secrets:

```powershell
npx wrangler secret put GITHUB_CLIENT_ID --config tools/github_oauth_worker/wrangler.toml
npx wrangler secret put GITHUB_CLIENT_SECRET --config tools/github_oauth_worker/wrangler.toml
npx wrangler secret put STATE_SECRET --config tools/github_oauth_worker/wrangler.toml
```

Use a random value of at least 32 bytes for `STATE_SECRET`. Do not commit any of
these values. Set the OAuth App callback URL to:

```text
https://<worker-domain>/oauth/github/callback
```

Review `ALLOWED_ORIGINS` in `wrangler.toml` before deployment. Origins must not
contain a path or trailing slash.

## Test and deploy

```powershell
node tools/github_oauth_worker/oauth_worker_test.mjs
npx wrangler deploy --config tools/github_oauth_worker/wrangler.toml
```

After deployment, set the GitHub repository variable
`EAI_GITHUB_OAUTH_URL` to the Worker origin, without a trailing slash. The
documentation build hides the login control when this value is unset.

See [`docs/community_github_oauth.md`](../../docs/community_github_oauth.md)
for the complete production and local setup procedure.
