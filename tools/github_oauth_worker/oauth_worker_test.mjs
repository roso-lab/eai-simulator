import assert from "node:assert/strict";
import {
  createSignedState,
  handleRequest,
  normalizeEnvironmentValue,
  normalizeAllowedOrigin,
  selectVerifiedEmail,
  verifySignedState,
} from "./worker.mjs";

const allowedOrigins = new Set([
  "http://127.0.0.1:8000",
  "https://roso-lab.github.io",
]);
const env = {
  ALLOWED_ORIGINS: [...allowedOrigins].join(","),
  GITHUB_CLIENT_ID: "test-client-id",
  GITHUB_CLIENT_SECRET: "test-client-secret",
  STATE_SECRET: "test-state-secret-with-sufficient-entropy",
};
const nonce = "test_nonce_1234567890";
const now = Date.UTC(2026, 7, 5, 0, 0, 0);

assert.equal(normalizeEnvironmentValue("\uFEFFtest-client-id\r\n"), "test-client-id");

assert.equal(
  normalizeAllowedOrigin("https://roso-lab.github.io", allowedOrigins),
  "https://roso-lab.github.io",
);
assert.equal(normalizeAllowedOrigin("https://attacker.example", allowedOrigins), null);
assert.equal(normalizeAllowedOrigin("https://roso-lab.github.io/path", allowedOrigins), null);

const signedState = await createSignedState({
  language: "zh-CN",
  nonce,
  origin: "https://roso-lab.github.io",
}, env.STATE_SECRET, now);
const verifiedState = await verifySignedState(
  signedState,
  env.STATE_SECRET,
  allowedOrigins,
  now + 60_000,
);
assert.deepEqual(verifiedState, {
  language: "zh-CN",
  nonce,
  origin: "https://roso-lab.github.io",
});
assert.equal(
  await verifySignedState(`${signedState}x`, env.STATE_SECRET, allowedOrigins, now),
  null,
);
assert.equal(
  await verifySignedState(signedState, env.STATE_SECRET, allowedOrigins, now + 601_000),
  null,
);

assert.equal(selectVerifiedEmail([
  { email: "secondary@example.com", primary: false, verified: true },
  { email: "primary@example.com", primary: true, verified: true },
], "public@example.com"), "primary@example.com");
assert.equal(selectVerifiedEmail([], "public@example.com"), "");
assert.equal(selectVerifiedEmail([
  { email: "unverified@example.com", primary: true, verified: false },
], "unverified@example.com"), "");

const healthResponse = await handleRequest(new Request("https://oauth.example/health"), env);
assert.equal(healthResponse.status, 200);
assert.deepEqual(await healthResponse.json(), {
  service: "eai-community-github-oauth",
  status: "ok",
});

const startUrl = new URL("https://oauth.example/oauth/github/start");
startUrl.searchParams.set("origin", "https://roso-lab.github.io");
startUrl.searchParams.set("state", nonce);
startUrl.searchParams.set("lang", "en");
const startResponse = await handleRequest(new Request(startUrl), env);
assert.equal(startResponse.status, 302);
const authorizationUrl = new URL(startResponse.headers.get("location"));
assert.equal(authorizationUrl.origin, "https://github.com");
assert.equal(authorizationUrl.searchParams.get("client_id"), env.GITHUB_CLIENT_ID);
assert.equal(authorizationUrl.searchParams.get("scope"), "read:user user:email");

const bomEnv = {
  ...env,
  GITHUB_CLIENT_ID: `\uFEFF${env.GITHUB_CLIENT_ID}`,
};
const bomStartResponse = await handleRequest(new Request(startUrl), bomEnv);
const bomAuthorizationUrl = new URL(bomStartResponse.headers.get("location"));
assert.equal(bomAuthorizationUrl.searchParams.get("client_id"), env.GITHUB_CLIENT_ID);

const deniedUrl = new URL("https://oauth.example/oauth/github/callback");
deniedUrl.searchParams.set("error", "access_denied");
deniedUrl.searchParams.set("state", authorizationUrl.searchParams.get("state"));
const deniedResponse = await handleRequest(new Request(deniedUrl), env);
const deniedHtml = await deniedResponse.text();
assert.equal(deniedResponse.status, 200);
assert.match(deniedHtml, /authorization_denied/u);
assert.match(deniedHtml, /eai-github-oauth/u);

const callbackUrl = new URL("https://oauth.example/oauth/github/callback");
callbackUrl.searchParams.set("code", "test-code");
callbackUrl.searchParams.set("state", authorizationUrl.searchParams.get("state"));
const requestedUrls = [];
const fakeFetch = async (input) => {
  const url = String(input);
  requestedUrls.push(url);

  if (url === "https://github.com/login/oauth/access_token") {
    return Response.json({ access_token: "server-only-token" });
  }

  if (url === "https://api.github.com/user") {
    return Response.json({
      avatar_url: "https://avatars.githubusercontent.com/u/1?v=4",
      email: null,
      login: "eai-user",
      name: "EAI </script> User",
    });
  }

  if (url === "https://api.github.com/user/emails") {
    return Response.json([
      { email: "eai-user@example.com", primary: true, verified: true },
    ]);
  }

  return new Response("Not found", { status: 404 });
};
const callbackResponse = await handleRequest(new Request(callbackUrl), env, fakeFetch);
const callbackHtml = await callbackResponse.text();
assert.equal(callbackResponse.status, 200);
assert.deepEqual(requestedUrls, [
  "https://github.com/login/oauth/access_token",
  "https://api.github.com/user",
  "https://api.github.com/user/emails",
]);
assert.match(callbackHtml, /eai-user@example\.com/u);
assert.match(callbackHtml, /EAI \\u003c\/script\\u003e User/u);
assert.doesNotMatch(callbackHtml, /server-only-token/u);
assert.match(callbackResponse.headers.get("content-security-policy"), /frame-ancestors 'none'/u);
assert.equal(callbackResponse.headers.get("x-frame-options"), "DENY");

const noEmailUrl = new URL(callbackUrl);
const noEmailFetch = async (input) => {
  const url = String(input);

  if (url === "https://github.com/login/oauth/access_token") {
    return Response.json({ access_token: "another-server-only-token" });
  }

  if (url === "https://api.github.com/user") {
    return Response.json({
      avatar_url: "https://attacker.example/avatar.png",
      email: "unverified@example.com",
      login: "no-email-user",
      name: "No Email User",
    });
  }

  if (url === "https://api.github.com/user/emails") {
    return Response.json([
      { email: "unverified@example.com", primary: true, verified: false },
    ]);
  }

  return new Response("Not found", { status: 404 });
};
const noEmailResponse = await handleRequest(new Request(noEmailUrl), env, noEmailFetch);
const noEmailHtml = await noEmailResponse.text();
assert.match(noEmailHtml, /github_request_failed/u);
assert.doesNotMatch(noEmailHtml, /another-server-only-token|attacker\.example/u);

const methodResponse = await handleRequest(new Request("https://oauth.example/health", {
  method: "POST",
}), env);
assert.equal(methodResponse.status, 405);

console.log("GitHub OAuth Worker tests passed");
