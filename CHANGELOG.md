# Changelog

Formatted after [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/AntVsl/oura_mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AntVsl/oura_mcp/releases/tag/v0.1.0
