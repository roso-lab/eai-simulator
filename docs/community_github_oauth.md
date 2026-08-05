# 社区 GitHub 登录部署说明

社区页由静态文档站托管。GitHub OAuth 的授权码交换由
Cloudflare Worker 完成，浏览器和仓库中都不保存 GitHub Client Secret 或访问令牌。

## 1. 创建 GitHub OAuth App

在 GitHub 的 **Settings > Developer settings > OAuth Apps** 中创建应用：

- Homepage URL：文档站地址，例如 `https://roso-lab.github.io/eai-simulator/`
- Authorization callback URL：`https://<worker-domain>/oauth/github/callback`

也可以复用允许用户授权的 GitHub App，但必须把账户权限
**Email addresses** 设为 **Read-only**。此模式不需要安装到仓库，也不需要
Discussions 或其他仓库权限。

Worker 请求 `read:user user:email`。登录完成后只把 GitHub 用户名、显示名称、
头像地址和已验证邮箱发回原社区页面；访问令牌不会发送或存入浏览器。

## 2. 配置并部署 Worker

先检查 `tools/github_oauth_worker/wrangler.toml` 中的 `ALLOWED_ORIGINS`。
每一项必须是完整 origin，不应带路径或末尾斜杠。生产环境至少应包含：

```text
https://www.rosolab.com
https://roso-lab.github.io
```

设置 Worker 密钥。`STATE_SECRET` 应使用至少 32 字节的随机值：

```powershell
npx wrangler secret put GITHUB_CLIENT_ID --config tools/github_oauth_worker/wrangler.toml
npx wrangler secret put GITHUB_CLIENT_SECRET --config tools/github_oauth_worker/wrangler.toml
npx wrangler secret put STATE_SECRET --config tools/github_oauth_worker/wrangler.toml
```

运行测试并部署：

```powershell
node tools/github_oauth_worker/oauth_worker_test.mjs
npx wrangler deploy --config tools/github_oauth_worker/wrangler.toml
```

部署后访问 `https://<worker-domain>/health`，应返回 `status: ok`。

## 3. 启用文档站登录入口

文档配置默认使用正式 Worker：

```text
https://eai-community-oauth.2190891063.workers.dev
```

构建环境不需要额外设置变量。需要切换 Worker 时，可以通过
`EAI_GITHUB_OAUTH_URL` 覆盖默认地址；地址不要带末尾斜杠。

## 4. 本地联调

建议为本地调试创建独立的 GitHub OAuth App，并把回调地址设置为：

```text
http://127.0.0.1:8787/oauth/github/callback
```

在 `tools/github_oauth_worker/.dev.vars` 中填写三个密钥；该文件已被 Git 忽略：

```dotenv
GITHUB_CLIENT_ID=...
GITHUB_CLIENT_SECRET=...
STATE_SECRET=...
```

分别启动 Worker 和文档站：

```powershell
npx wrangler dev --config tools/github_oauth_worker/wrangler.toml --ip 127.0.0.1 --port 8787
$env:EAI_GITHUB_OAUTH_URL='http://127.0.0.1:8787'
python -m sphinx -E -a -W --keep-going -b html docs/source docs/build
python -m http.server 8000 --directory docs/build
```

打开 `http://127.0.0.1:8000/community.html` 测试。局域网地址若也要登录，必须把
对应的页面 origin 加入 `ALLOWED_ORIGINS`；本地 Worker 仍应只监听可信网络接口。
