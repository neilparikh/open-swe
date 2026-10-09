---
type: security architecture concept
title: Authentication, authorization, and credential scope
description: How Open SWE authenticates dashboard users and inbound requests, chooses GitHub authority, scopes sandbox credentials, and protects persisted secrets.
tags: [authentication, authorization, github-oauth, github-app, webhooks, encryption, csrf, sandbox-security]
verified:
  - by: openwiki/0.4.2
    at: 2026-10-09T08:17:28.238Z
sources:
  - id: openwiki-source-035276d8c595782faca6e595
    resource: repo://openswe/api/health.py
  - id: openwiki-source-913527bc7b548b4bf81f6a35
    resource: repo://openswe/completion.py
  - id: openwiki-source-fe0fc757d24cd7cfa5264c72
    resource: repo://openswe/credential_scope.py
  - id: openwiki-source-6128627021aa8b6393710ab1
    resource: repo://openswe/dashboard/auth_routes.py
  - id: openwiki-source-50d64b46ab06b6436266b4d0
    resource: repo://openswe/dashboard/oauth.py
  - id: openwiki-source-4cb48d234248941982c6537f
    resource: repo://openswe/dashboard/profiles.py
  - id: openwiki-source-722cee9b515af646d42fbd43
    resource: repo://openswe/dashboard/repo_access.py
  - id: openwiki-source-7fc33e4789861923a6f12e78
    resource: repo://openswe/dashboard/routes.py
  - id: openwiki-source-b11ec0af4e40439361058935
    resource: repo://openswe/encryption.py
  - id: openwiki-source-660db75c29aed6870aab6c3d
    resource: repo://openswe/github/app.py
  - id: openwiki-source-783616155a6663ff5d3b0dfa
    resource: repo://openswe/github/comments.py
  - id: openwiki-source-3a7e1a8d071789849d64c6c9
    resource: repo://openswe/github/org_membership.py
  - id: openwiki-source-5491be991f9727afe1f3163d
    resource: repo://openswe/github/proxy.py
  - id: openwiki-source-d0edf7555209b3e6418b5c5f
    resource: repo://openswe/github/routes.py
  - id: openwiki-source-ceb13e900da7601538741618
    resource: repo://openswe/github/sandbox_access.py
  - id: openwiki-source-0c4b1aac46b8420871177918
    resource: repo://openswe/github/thread_token.py
  - id: openwiki-source-8d544a43b3113d48789eff4f
    resource: repo://openswe/github/token.py
  - id: openwiki-source-ff94e6d6f8e823f174c61b08
    resource: repo://openswe/linear/routes.py
  - id: openwiki-source-1087d65aaa83434d4f7c209b
    resource: repo://openswe/middleware/refresh_github_proxy.py
  - id: openwiki-source-49cd80b1b712410f02d313d6
    resource: repo://openswe/slack/client.py
  - id: openwiki-source-d683445251a7ec19a5def965
    resource: repo://openswe/slack/oauth.py
  - id: openwiki-source-1d8440f14c85812310a67572
    resource: repo://openswe/users/authorization.py
  - id: openwiki-source-3087256f0cd599176fba3c38
    resource: repo://openswe/webhooks/common.py
  - id: openwiki-source-55f6a5ce5ec8434e007c164d
    resource: repo://tests/dashboard/test_dashboard_github_login_gate.py
generated: { by: "openwiki/0.4.2", at: "2026-10-09T08:17:28.238Z" }
---

# Authentication, authorization, and credential scope

Open SWE separates browser identity, GitHub acting authority, workspace authority, and untrusted external input. A dashboard session establishes who is using the product; it does not by itself grant a sandbox a long-lived GitHub secret. Thread metadata and workspace routing are also security inputs, so credential selection validates their ownership and visibility rather than trusting a caller-supplied login. See also [sandbox lifecycle](../architecture/sandbox-lifecycle.md), [dashboard UI](../integrations/dashboard-ui.md), [configuration](../operations/configuration.md), and [invocation](../workflows/invocation.md).

## Dashboard identity and admission

The dashboard uses the GitHub App OAuth code flow. `GET /dashboard/api/auth/login` creates a random nonce, puts its HMAC in a signed, ten-minute state JWT, and retains the raw nonce in `osw_oauth_state`. The callback constant-time compares the cookie-derived HMAC before exchanging the code, resolving the GitHub user, applying the login gate, persisting the OAuth result, and issuing a session. `sanitize_redirect_to` accepts only a non-protocol-relative relative path or an absolute URL whose origin is `DASHBOARD_BASE_URL` or one of `DASHBOARD_ALLOWED_ORIGINS`; login and API paths are excluded to prevent redirect abuse.

```mermaid
sequenceDiagram
    participant Browser
    participant Dashboard
    participant GitHub
    Browser->>Dashboard: GET auth login
    Dashboard->>Browser: state cookie and GitHub redirect
    Browser->>GitHub: authorize
    GitHub->>Dashboard: code and signed state
    Dashboard->>Dashboard: verify nonce and authorize login
    Dashboard->>GitHub: exchange code and read user
    Dashboard->>Browser: session cookie or desktop handoff
```

GitHub OAuth login binds the state JWT to the browser cookie before a session or desktop handoff is issued.

Sessions are HS256 JWTs signed by `DASHBOARD_JWT_SECRET`, expire after seven days, and are carried in the `HttpOnly` `osw_session` cookie. `require_session` rejects a missing or invalid cookie and binds the session actor to audit logging; `/me` reports that identity and recomputes `is_admin`. Cookie policy is derived from deployment topology: HTTPS split-origin deployments use `Secure; SameSite=None`; same-origin and local deployments use `SameSite=Lax`. The OAuth state cookie is `HttpOnly`, `SameSite=Lax`, restricted to `/dashboard/api/auth`, and lasts only for the state TTL.

Startup requires `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS`, except a desktop-local backend configured with `OPEN_SWE_LOCAL_AUTH_TOKEN`. A login is admitted when it case-insensitively matches the explicit user list or is an active member of any allowed organization. Organization membership is checked with a repository-independent GitHub App token requesting `members: read`; missing app access, API failures, invalid responses, and non-active membership deny access. This is deliberately fail-closed.

Desktop login does not leave a browser session on a loopback listener. The callback returns a 120-second signed handoff code to a fixed `127.0.0.1` callback, and the desktop app must provide the matching PKCE S256 verifier to redeem it for a session. Separate cloud-terminal tickets last 60 seconds and are checked for both their fixed audience and the requested thread ID.

All dashboard API routers apply `require_same_origin_for_mutations`. Read-only methods are exempt; configured deployments require an allowed `Origin` or `Referer` for cookie-authenticated mutations. Bearer-only GitHub requests without a session cookie are exempt because the credential is not ambient browser state. With no configured dashboard origin, this CSRF check is intentionally a local-development no-op.

### Authorization after login

`CONFIGURED_ADMINS` is a case-insensitive set of GitHub logins and emails. Dashboard dependencies recompute membership from the session rather than trusting an admin bit stored in thread data. Repository-specific dashboard actions also verify that the user's current GitHub OAuth token can read the requested repository, retrying once after a forced refresh on 401; workspace-scoped actions perform the corresponding check with the GitHub App credential.

Slack linking uses Slack OpenID Connect with `openid email profile`, so the link is based on Slack-verified identity claims rather than a self-asserted account. If `SLACK_TEAM_ID` is set, identities from another workspace, including Slack Connect contexts, are rejected. Shared-thread account-link prompts use only the token-free dashboard settings URL, so another reader cannot redeem or attach someone else's authorization.

## GitHub credentials and thread scope

There are two deliberately distinct acting identities:

- A **private, user-owned thread** may use the saved GitHub OAuth token of its recorded owner, but only when the run's GitHub login matches that owner. A system-owned thread may not use private credentials, and malformed visibility or owner metadata is rejected.
- A **public thread** resolves to a GitHub App installation token. Thus public/shared work does not silently borrow a participant's personal OAuth authority.

`resolve_github_token` reads the saved thread scope, clears thread-local cached tokens before resolution, and raises `GitHubUserAuthRequired` if a private owner has no usable OAuth authorization. It only falls back to the App token when the thread has no eligible private credential owner. `pr_author_login` is a related authorship guard: in eligible shared user threads a requested author must be a recorded participant; private threads remain owner-pinned and system threads use the App.

```mermaid
flowchart TD
    Scope["Load saved thread visibility and owner"] --> Private{"Private thread"}
    Private -->|yes| Owner{"Run login is saved owner"}
    Owner -->|no| Reject["Reject personal credential use"]
    Owner -->|yes| UserToken["Resolve stored user OAuth token"]
    UserToken -->|missing| AuthRequired["Require GitHub reauthentication"]
    Private -->|no| AppToken["Mint GitHub App installation token"]
```

Thread scope determines whether a run may act as a person or must act as the App.

Resolved tokens are process-memory-only cache entries keyed by `(thread_id, principal)`, where normalized `login:`/`email:` principals separate users and `bot` separates App tokens. The cache refuses an unbound user token. User entries expire within 60 seconds of their token expiry or after a 24-hour cap; a cached bot token that is expired can be re-minted with its recorded repository restriction. Explicit invalidation clears every cached token for a thread.

GitHub App installation tokens are obtained through the App SDK and cached only in-process by installation ID, repository IDs/names, and requested permissions. The cache stops reusing a token ten minutes before expiry. A missing App configuration may use the local `gh` CLI only in local development; otherwise token minting returns no credential.

## Sandboxes: scoped authority without secret injection

`workspace_token` gives a sandbox an installation-wide App token unless its caller explicitly supplies repositories; workspace routing/preload repositories are not themselves an access limit. For narrowed access, `repository_token` resolves only requested repository names that the App installation can actually reach and mints a token for those repository IDs. If no requested match exists it grants no credential; the installation-wide discovery token stays server-side. This makes webhook/reviewer callers responsible for passing a restrictive repository set where required.

LangSmith sandboxes receive GitHub access through proxy configuration, not environment variables. The proxy's credential record retains the repository and permission scope, expiry, workspace, and base proxy configuration. Near expiry it refreshes from that recorded scope (or an intersection with a narrower requested set), preventing a refresh from broadening authority. The before-model middleware attempts this refresh for each thread; failures are logged rather than allowing the refresh hook itself to terminate the model call.

## External trust boundaries

Webhook routes verify the raw request body before parsing it and fail closed when signing material is absent:

- GitHub compares `sha256=HMAC(GITHUB_WEBHOOK_SECRET, body)` with `X-Hub-Signature-256` using constant-time comparison; `/webhooks/github` returns 401 on failure.
- Slack verifies `v0:timestamp:body` with `SLACK_SIGNING_SECRET`, constant-time compares the `v0=` signature, and rejects timestamps more than 300 seconds from current time to limit replay.
- Linear constant-time compares the raw-body HMAC-SHA256 in `Linear-Signature`; the route also drops deliveries whose signed `webhookTimestamp` is more than 60 seconds old.
- The public `/webhooks/run-complete` route compares its query token to `RUN_COMPLETE_WEBHOOK_SECRET` in constant time. An unset secret rejects every request and disables completion/failure replies.

GitHub deliveries are additionally routed only when their repository is assigned to a workspace. For public repositories, an optional `PUBLIC_REPO_ORG_GATE` permits triggers only from active members of that organization (or configured internal bots); private repositories bypass that gate. GitHub comment text from an author who is not a registered user is wrapped in reserved untrusted-content tags, after any attempt to supply those tags in the raw comment has been replaced.

## Stored secret protection and operations

`TOKEN_ENCRYPTION_KEY` accepts one Fernet key or a newest-first comma/newline-separated list. `MultiFernet` encrypts with the first key and tries every key to decrypt, allowing staged key rotation. Invalid ciphertext and a missing encryption key decrypt to an empty value rather than raising. GitHub OAuth access and refresh tokens are encrypted before storage; token records are keyed through the user record rather than a mutable GitHub login. When a refresh token produces GitHub's unrecoverable `bad_refresh_token` or `unauthorized_client` error, the stored authorization is removed so callers require a clean sign-in.

### Focused tests and safe changes

`tests/dashboard/test_dashboard_github_login_gate.py` verifies startup failure without an allowlist, the local-backend exception, explicit-user matching, multi-organization membership, union semantics, and rejection outside both lists. Changes to identity policy should extend those tests and preserve the startup gate. Changes to credential selection should preserve the private-owner and system-thread invariants in `openswe/credential_scope.py`; changes to sandbox token minting must ensure a refresh cannot turn a repository-scoped token into installation-wide access.
