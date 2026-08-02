# Changelog

Formatted after [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] — 2026-08-02

### Added

- Cross-client agent support: `AGENTS.md`, task/review workflow templates, and
  a Codex-ready `oura-mcp-maintenance` skill. The existing Oura insight skill
  now explicitly supports Claude, Codex, and other MCP-capable agents.
- `py.typed` marker and MCP resources coverage in the package test suite.

### Fixed

- Revalidate the two latest completed Oura days instead of serving stale cache
  data after a late sync; cache and token-store files are owner-readable only.
- Bound transient OAuth registrations, pending consent requests, and failed
  consent attempts to prevent unbounded state growth.
- Close per-request HTTP clients, report the actual authorization state, and
  fetch independent daily summaries concurrently.
- Pin reproducible Docker and CI dependency resolution to the committed lockfile.

### Changed

- Public OAuth accepts the explicit trusted origins for Claude.ai and ChatGPT;
  setup instructions and the installer now cover Claude Code, Claude.ai, Codex,
  and ChatGPT.

## [0.4.0] — 2026-07-30

### Fixed

- **Five endpoints silently lost data on narrow date windows.** Oura filters by
  an internal UTC timestamp rather than the `day` field it returns, so a
  single-day query came back empty while the same day appeared in a wider one.
  The code carried a comment stating these endpoints had been checked and were
  unaffected; a sweep over seven consecutive days showed `daily_sleep`,
  `daily_readiness`, `daily_spo2`, `daily_stress` and `daily_resilience` each
  losing the record 7 times out of 7. `enhanced_tag` lost tags the same way and
  needed its own rule, since it is dated by a `start_day`/`end_day` span rather
  than a `day`.

  The window is now widened for every endpoint and trimmed locally, instead of
  only for a two-name allowlist of "known broken" ones — a list protects only
  what someone remembered to add to it. Verified across fourteen endpoints:
  zero losses.

  Found by the new `oura://yesterday` resource returning an empty period on its
  first read.

### Added

- **SQLite cache for completed days.** Past days in Oura are immutable, so they
  are stored once and never requested again: a repeated two-week query now sends
  only today over the network, and a purely historical window makes no request at
  all — verified against live data, 1.82 s down to 0.38 s. Today is never cached,
  because Oura is still writing it, and the day boundary is taken in the
  configured timezone rather than the system one. Empty days are not cached
  either: an empty day means either "did not wear the ring" or "has not synced
  yet", and caching the second would freeze a wrong answer forever. The API mode
  is part of the key so sandbox data cannot surface in production, and any SQLite
  failure degrades to "no cache" rather than breaking access to data.
  Manage it with `my-oura-mcp cache --status` / `--clear`.

- `my-oura-mcp install` prints ready-to-paste configuration for Claude Code,
  Claude Desktop and Cursor, with the absolute path already filled in — the
  relative-path mistake it prevents was common enough to have its own entry in
  the troubleshooting section. It deliberately **writes nothing**: the
  neighbouring `YasuakiOmokawa/oura-mcp` merges itself into those config files,
  but a bad merge breaks servers that belong to someone else, and the file
  formats drift per app version and OS.

### Changed

- Publishing to the MCP registry now happens in the release workflow over GitHub
  OIDC, alongside PyPI and Docker Hub. The interactive `login github` issues a
  token that expires in under an hour, which in practice meant a race between
  logging in and publishing — three device codes were burned in one session
  while working through the registry's schema rejections. The job runs last and
  depends on both publish jobs, since the registry verifies the artifacts already
  exist and carry their ownership proofs, and it waits for PyPI to actually serve
  the new version — that took up to two minutes in practice.

## [0.3.0] — 2026-07-30

### Added

- `skills/oura` — a Claude skill with four recipes: sleep trend, recovery check,
  symptom investigation, and judging whether a change in routine did anything.
  Workflows, not tools: a sequence of calls plus how to reason about the answer.
  Borrowed from `YasuakiOmokawa/oura-mcp`, which ships nine of them; on top of
  these tools the recipes come out shorter, since they need not explain
  pagination or UTC conversion. Field names are checked against the code by
  tests — the first run caught four that did not exist.
- Access-log redaction. uvicorn logs full query strings, and the deployed
  server's logs contained `GET /oauth/consent?request=…` — the one-time
  authorization request secret. Keys are kept, values replaced.

## [0.2.3] — 2026-07-30

### Added

- Ownership proofs required to publish in the MCP registry: the `mcp-name:` token
  in the README that ties the PyPI package to the server name, and the
  `io.modelcontextprotocol.server.name` label on the Docker image. Both are
  checked by tests, because the registry only rejects a publish *after* the
  version has already shipped to PyPI and Docker Hub — so the fix costs a whole
  release.
- The registry also caps `description` at 100 characters, which is likewise only
  discovered at publish time. Shortened, and covered by a test.

## [0.2.2] — 2026-07-30

First release verified against claude.ai end to end, on desktop and on a phone.
Three separate defects stood between a working server and a working connection,
and all three failed silently — no error in the logs, no message a user could act
on.

### Fixed

- **`form-action 'self'` blocked the redirect back to claude.ai.** Added in 0.2.1
  to stop the consent form being redirected to a foreign host, the directive is
  enforced by browsers across the *entire redirect chain* that follows a form
  submission — not just the form's own action. So the `303` to
  `https://claude.ai/…` was blocked silently: the "Разрешить" button looked
  broken, claude.ai never received the callback, and `/token` was never called.
  Three rounds of `curl` verification passed and could not have caught this —
  `curl` does not enforce CSP. `form-action` now names the client's registered
  redirect origin, taken from the pending request rather than hardcoded, so the
  protection stays meaningful.
- **claude.ai never called `/token`.** Live traffic on the deployed server showed
  the consent step succeeding (`303 See Other` with a fresh code) but no token
  exchange ever following it — confirmed by grepping the entire log history for
  `POST /token`: zero hits, across two full connection attempts. The redirect was
  missing `iss` (RFC 9207, the authorization-server-issuer parameter that guards
  against mix-up attacks when a client talks to many different authorization
  servers, which is exactly Claude's situation). Without it a strict client can
  discard the callback before ever reaching the token endpoint — apparently
  without surfacing an error a user could act on. The value must match the
  `issuer` field in `/.well-known/oauth-authorization-server` character for
  character; it now does, taken from the same normalized string. Verified end to
  end against a running server: register, authorize, consent, and `POST /token`
  now all complete with an access and refresh token returned.
- **A stale authorization request claimed the secret was wrong.** "Request
  expired" and "wrong secret" shared one message, on the reasoning that
  distinguishing them would help someone guessing. It doesn't: the request id is
  itself 24 random bytes, so anyone holding a live one already knows it is live.
  What it did do was send the owner to check a secret that was correct all along
  — the requests live in process memory, so any server restart invalidates an
  open consent page. A stale request now answers `410` with a page that says the
  secret is fine and points back to claude.ai, and offers no form, because
  retyping the secret cannot help.

## [0.2.1] — 2026-07-30

### Fixed

- **Reflected XSS on the consent page.** The authorization request id came from a
  query parameter and was interpolated into HTML unescaped, so a link like
  `?request="><script>…` could inject a script into the very page where the owner
  types the shared secret — and read that field. The id is now escaped, and the
  page ships a `Content-Security-Policy` with `default-src 'none'` (the page has
  no scripts of its own, so the ban is total), `form-action 'self'`,
  `frame-ancestors 'none'` plus `X-Frame-Options` against clickjacking, and
  `Referrer-Policy: no-referrer`.
- OAuth no longer stays off silently. Setting `OURA_PUBLIC_URL` without
  `OURA_MCP_TOKEN` used to start a server with no OAuth and no warning; the
  failure surfaced only when claude.ai refused to connect.
- Refresh tokens expire after 90 days and expired rows are pruned when new tokens
  are issued. Previously every reconnect left a row behind forever.
- `/healthz` is answered in one place instead of two. The middleware still lets it
  through without a token — that is what keeps the Docker `HEALTHCHECK` working —
  but the response comes from the route, which exists in both auth modes.

### Changed

- Documentation: deployment was described twice and had started to drift, so the
  README now summarizes and links to `docs/DEPLOY.md`. Quick start moved above the
  feature list, a sample response and a plain-language note on what MCP is were
  added up front, and a troubleshooting section covers the common failures — a
  `401` from a missing `Bearer` prefix, an unset `OURA_TZ`, a spent refresh token,
  a mismatched `OURA_PUBLIC_URL`.

## [0.2.0] — 2026-07-29

Reaching the server from claude.ai, including the mobile apps.

### Added

- Built-in OAuth 2.1 authorization server, enabled by setting
  `OURA_PUBLIC_URL`. This is what makes the server connectable from claude.ai
  and the mobile apps: the static-header field is a limited beta and may be
  absent from the connector dialog. Dynamic Client Registration is supported,
  so nothing has to be configured on the Claude side. The protocol layer comes
  from the official SDK (`mcp.server.auth`) — PKCE S256, code and token
  lifetimes, `redirect_uri` consistency; this package supplies only policy and
  storage.
- Consent page at `/oauth/consent`, laid out for phones. It asks for the
  existing `OURA_MCP_TOKEN` rather than introducing a second password.
- `"none"` is advertised in `token_endpoint_auth_methods_supported`. The SDK
  hardcodes that list without it, yet serves public clients correctly — and
  Claude registers as a public client under DCR. A custom route cannot win over
  the SDK's, so an ASGI middleware amends the finished response instead of
  replacing the document, leaving room for fields the SDK may add later.
- Deployment runbook in `docs/DEPLOY.md`, plus `compose.server.yml` with two
  entrances: `compose.caddy.yml` (direct, Let's Encrypt) and
  `compose.tunnel.yml` (Cloudflare Tunnel, no inbound ports and the origin IP
  stays out of Certificate Transparency).

### Changed

- The shared secret now also works as a non-expiring access token, so a single
  code path serves both Claude Code (header) and claude.ai (OAuth). Existing
  `claude mcp add --header` setups keep working unchanged.
- `/healthz` is a route rather than a middleware branch, so it survives with
  OAuth enabled — the Docker `HEALTHCHECK` depends on it.

## [0.1.0] — 2026-07-28

First release.

### Added

- Eleven MCP tools covering sleep, readiness, activity, heart rate, SpO₂,
  stress, cardiovascular age and tags. Compact summaries by default, raw API
  responses via `raw=True`.
- OAuth2 authorization with a one-time browser flow. Tokens are stored
  atomically with mode `600`; refreshes are serialized so concurrent tool calls
  cannot spend a single-use refresh token twice.
- Two transports from one codebase: `stdio` for local use, `streamable-http`
  for remote.
- Shared-secret authentication for the HTTP endpoint, compared in constant
  time. The server refuses to start on a non-loopback address without one.
- Docker image and a `compose.yml` fronted by Caddy with automatic TLS.
- Sandbox mode, letting the server run against Oura's synthetic data with no
  credentials.

### Fixed

Five defects that all shared one trait — data disappeared with no error raised:

- Date ranges resolved in the system timezone rather than the configured one,
  shifting "today" on hosts running UTC.
- `sleep` and `daily_activity` are filtered by Oura using an internal UTC
  timestamp rather than the `day` field they return, so records went missing
  from narrow windows. The request window is widened and trimmed locally.
- `heartrate` timestamps arrive in UTC and were grouped by their date prefix,
  pushing local 00:00–03:00 readings into the previous day.
- Multiple sleep records per day let a short nap displace the full night.
- `days_back` was declared with a concrete default, which MCP clients pass as
  an explicit argument, breaking every request that also carried explicit
  dates.

[Unreleased]: https://github.com/AntVsl/oura_mcp/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/AntVsl/oura_mcp/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/AntVsl/oura_mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/AntVsl/oura_mcp/compare/v0.2.3...v0.3.0
[0.2.3]: https://github.com/AntVsl/oura_mcp/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/AntVsl/oura_mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/AntVsl/oura_mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/AntVsl/oura_mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/AntVsl/oura_mcp/releases/tag/v0.1.0
