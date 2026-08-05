const GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize";
const GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token";
const GITHUB_USER_URL = "https://api.github.com/user";
const GITHUB_EMAILS_URL = "https://api.github.com/user/emails";
const STATE_MAX_AGE_SECONDS = 10 * 60;
const encoder = new TextEncoder();
const decoder = new TextDecoder();

function encodeBase64Url(bytes) {
  let binary = "";

  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }

  return btoa(binary)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/u, "");
}

function decodeBase64Url(value) {
  const base64 = value.replaceAll("-", "+").replaceAll("_", "/");
  const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function parseAllowedOrigins(value) {
  return new Set(
    normalizeEnvironmentValue(value)
      .split(",")
      .map((origin) => origin.trim())
      .filter(Boolean),
  );
}

export function normalizeEnvironmentValue(value) {
  return String(value || "").replace(/^\uFEFF/u, "").trim();
}

export function normalizeAllowedOrigin(value, allowedOrigins) {
  try {
    const url = new URL(value);
    const origin = url.origin;

    if (url.href !== `${origin}/` || !allowedOrigins.has(origin)) {
      return null;
    }

    return origin;
  } catch (error) {
    return null;
  }
}

async function importStateKey(secret) {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { hash: "SHA-256", name: "HMAC" },
    false,
    ["sign", "verify"],
  );
}

export async function createSignedState(payload, secret, now = Date.now()) {
  const body = {
    issuedAt: Math.floor(now / 1000),
    language: payload.language === "en" ? "en" : "zh-CN",
    nonce: payload.nonce,
    origin: payload.origin,
  };
  const encodedPayload = encodeBase64Url(encoder.encode(JSON.stringify(body)));
  const key = await importStateKey(secret);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(encodedPayload));
  return `${encodedPayload}.${encodeBase64Url(new Uint8Array(signature))}`;
}

export async function verifySignedState(
  state,
  secret,
  allowedOrigins,
  now = Date.now(),
) {
  if (typeof state !== "string" || !state.includes(".")) {
    return null;
  }

  const [encodedPayload, encodedSignature, ...extra] = state.split(".");

  if (!encodedPayload || !encodedSignature || extra.length > 0) {
    return null;
  }

  try {
    const key = await importStateKey(secret);
    const isValid = await crypto.subtle.verify(
      "HMAC",
      key,
      decodeBase64Url(encodedSignature),
      encoder.encode(encodedPayload),
    );

    if (!isValid) {
      return null;
    }

    const payload = JSON.parse(decoder.decode(decodeBase64Url(encodedPayload)));
    const issuedAt = Number(payload.issuedAt);
    const age = Math.floor(now / 1000) - issuedAt;
    const origin = normalizeAllowedOrigin(payload.origin, allowedOrigins);
    const nonceIsValid = typeof payload.nonce === "string"
      && /^[A-Za-z0-9_-]{16,128}$/u.test(payload.nonce);

    if (!origin || !nonceIsValid || !Number.isSafeInteger(issuedAt)
        || age < 0 || age > STATE_MAX_AGE_SECONDS) {
      return null;
    }

    return {
      language: payload.language === "en" ? "en" : "zh-CN",
      nonce: payload.nonce,
      origin,
    };
  } catch (error) {
    return null;
  }
}

export function selectVerifiedEmail(emails, publicEmail = "") {
  const candidates = Array.isArray(emails) ? emails : [];
  const selected = candidates.find((email) => email?.verified && email?.primary)
    || candidates.find((email) => email?.verified);
  const publicEmailIsVerified = candidates.some(
    (email) => email?.verified && email?.email === publicEmail,
  );
  const value = selected?.email || (publicEmailIsVerified ? publicEmail : "");
  return typeof value === "string" ? value.trim().slice(0, 320) : "";
}

function sanitizeAvatarUrl(value) {
  try {
    const url = new URL(value);
    const trustedHost = url.hostname === "avatars.githubusercontent.com"
      || url.hostname.endsWith(".githubusercontent.com");
    return url.protocol === "https:" && trustedHost ? url.href.slice(0, 500) : "";
  } catch (error) {
    return "";
  }
}

function sanitizeProfile(user, emails) {
  const login = typeof user?.login === "string" ? user.login.trim().slice(0, 80) : "";

  if (!login) {
    throw new Error("GitHub profile did not include a login");
  }

  const name = typeof user?.name === "string" && user.name.trim()
    ? user.name.trim().slice(0, 200)
    : login;
  const email = selectVerifiedEmail(emails, user.email);

  if (!email) {
    throw new Error("GitHub profile did not include a verified email");
  }

  return {
    avatarUrl: sanitizeAvatarUrl(user.avatar_url),
    email,
    login,
    name,
  };
}

function scriptJson(value) {
  return JSON.stringify(value)
    .replaceAll("<", "\\u003c")
    .replaceAll(">", "\\u003e")
    .replaceAll("&", "\\u0026")
    .replaceAll("\u2028", "\\u2028")
    .replaceAll("\u2029", "\\u2029");
}

function randomNonce() {
  const bytes = crypto.getRandomValues(new Uint8Array(18));
  return encodeBase64Url(bytes);
}

function resultPage(state, profile, errorCode = "") {
  const isEnglish = state.language === "en";
  const title = errorCode
    ? (isEnglish ? "GitHub sign-in failed" : "GitHub 登录失败")
    : (isEnglish ? "GitHub sign-in complete" : "GitHub 登录完成");
  const message = errorCode
    ? (isEnglish ? "Return to the community page and try again." : "请返回社区页面后重试。")
    : (isEnglish ? "This window will close automatically." : "此窗口将自动关闭。")
  const cspNonce = randomNonce();
  const payload = {
    error: errorCode,
    profile,
    source: "eai-github-oauth",
    state: state.nonce,
  };

  const html = `<!doctype html>
<html lang="${isEnglish ? "en" : "zh-CN"}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${title}</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { display: grid; min-height: 100vh; margin: 0; padding: 24px; box-sizing: border-box; place-items: center; }
    main { max-width: 420px; text-align: center; }
    h1 { margin: 0 0 10px; font-size: 20px; }
    p { margin: 0; color: #6b7280; line-height: 1.6; }
  </style>
</head>
<body>
  <main><h1>${title}</h1><p>${message}</p></main>
  <script nonce="${cspNonce}">
    const targetOrigin = ${scriptJson(state.origin)};
    const payload = ${scriptJson(payload)};
    if (window.opener) window.opener.postMessage(payload, targetOrigin);
    window.close();
  </script>
</body>
</html>`;

  return new Response(html, {
    headers: {
      "Cache-Control": "no-store",
      "Content-Security-Policy": `default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${cspNonce}'; base-uri 'none'; frame-ancestors 'none'`,
      "Content-Type": "text/html; charset=utf-8",
      "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
    },
  });
}

function requireEnvironment(env) {
  const required = ["ALLOWED_ORIGINS", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "STATE_SECRET"];
  const missing = required.filter((name) => !normalizeEnvironmentValue(env[name]));

  if (missing.length > 0) {
    throw new Error(`Missing Worker configuration: ${missing.join(", ")}`);
  }

  if (normalizeEnvironmentValue(env.STATE_SECRET).length < 32) {
    throw new Error("STATE_SECRET must contain at least 32 characters");
  }
}

async function startAuthorization(request, env) {
  requireEnvironment(env);
  const requestUrl = new URL(request.url);
  const allowedOrigins = parseAllowedOrigins(env.ALLOWED_ORIGINS);
  const origin = normalizeAllowedOrigin(requestUrl.searchParams.get("origin"), allowedOrigins);
  const nonce = requestUrl.searchParams.get("state") || "";
  const language = requestUrl.searchParams.get("lang") === "en" ? "en" : "zh-CN";

  if (!origin || !/^[A-Za-z0-9_-]{16,128}$/u.test(nonce)) {
    return new Response("Invalid OAuth request", { status: 400 });
  }

  const callbackUrl = new URL("/oauth/github/callback", requestUrl.origin).href;
  const stateSecret = normalizeEnvironmentValue(env.STATE_SECRET);
  const clientId = normalizeEnvironmentValue(env.GITHUB_CLIENT_ID);
  const state = await createSignedState({ language, nonce, origin }, stateSecret);
  const authorizeUrl = new URL(GITHUB_AUTHORIZE_URL);
  authorizeUrl.searchParams.set("allow_signup", "true");
  authorizeUrl.searchParams.set("client_id", clientId);
  authorizeUrl.searchParams.set("redirect_uri", callbackUrl);
  authorizeUrl.searchParams.set("scope", "read:user user:email");
  authorizeUrl.searchParams.set("state", state);

  return Response.redirect(authorizeUrl.href, 302);
}

async function exchangeCode(code, callbackUrl, env, fetchImpl) {
  const tokenResponse = await fetchImpl(GITHUB_TOKEN_URL, {
    body: new URLSearchParams({
      client_id: normalizeEnvironmentValue(env.GITHUB_CLIENT_ID),
      client_secret: normalizeEnvironmentValue(env.GITHUB_CLIENT_SECRET),
      code,
      redirect_uri: callbackUrl,
    }),
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "EAI-Simulator-Community",
    },
    method: "POST",
  });
  const tokenPayload = await tokenResponse.json();

  if (!tokenResponse.ok || !tokenPayload.access_token) {
    throw new Error("GitHub token exchange failed");
  }

  return tokenPayload.access_token;
}

async function loadProfile(accessToken, fetchImpl) {
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${accessToken}`,
    "User-Agent": "EAI-Simulator-Community",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  const [userResponse, emailResponse] = await Promise.all([
    fetchImpl(GITHUB_USER_URL, { headers }),
    fetchImpl(GITHUB_EMAILS_URL, { headers }),
  ]);

  if (!userResponse.ok) {
    throw new Error("GitHub profile request failed");
  }

  const user = await userResponse.json();
  const emails = emailResponse.ok ? await emailResponse.json() : [];
  return sanitizeProfile(user, emails);
}

async function finishAuthorization(request, env, fetchImpl) {
  requireEnvironment(env);
  const requestUrl = new URL(request.url);
  const allowedOrigins = parseAllowedOrigins(env.ALLOWED_ORIGINS);
  const state = await verifySignedState(
    requestUrl.searchParams.get("state"),
    normalizeEnvironmentValue(env.STATE_SECRET),
    allowedOrigins,
  );

  if (!state) {
    return new Response("Invalid or expired OAuth state", { status: 400 });
  }

  if (requestUrl.searchParams.get("error")) {
    return resultPage(state, null, "authorization_denied");
  }

  const code = requestUrl.searchParams.get("code");

  if (!code) {
    return resultPage(state, null, "missing_code");
  }

  try {
    const callbackUrl = new URL("/oauth/github/callback", requestUrl.origin).href;
    const accessToken = await exchangeCode(code, callbackUrl, env, fetchImpl);
    const profile = await loadProfile(accessToken, fetchImpl);
    return resultPage(state, profile);
  } catch (error) {
    return resultPage(state, null, "github_request_failed");
  }
}

export async function handleRequest(request, env, fetchImpl = fetch) {
  const url = new URL(request.url);

  if (request.method !== "GET") {
    return new Response("Method not allowed", { status: 405 });
  }

  if (url.pathname === "/health") {
    return Response.json({ service: "eai-community-github-oauth", status: "ok" }, {
      headers: { "Cache-Control": "no-store" },
    });
  }

  if (url.pathname === "/oauth/github/start") {
    return startAuthorization(request, env);
  }

  if (url.pathname === "/oauth/github/callback") {
    return finishAuthorization(request, env, fetchImpl);
  }

  return new Response("Not found", { status: 404 });
}

export default {
  fetch(request, env) {
    return handleRequest(request, env);
  },
};
