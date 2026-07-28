# Changelog

Formatted after [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
