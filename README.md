# my-oura-mcp

[![CI](https://github.com/AntVsl/oura_mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/AntVsl/oura_mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

Gives Claude, ChatGPT, and Codex access to your Oura Ring data: sleep,
readiness, HRV, resting heart rate, activity, SpO₂, stress.

Ask "how did I sleep last week" and your MCP client calls the right tool and
gets a summary back — not a wall of JSON:

```json
{
  "metric": "sleep_detail",
  "period": { "start": "2026-07-23", "end": "2026-07-29", "days": 7 },
  "stats": {
    "total_h":    { "mean": 7.1, "min": 5.9, "max": 8.4 },
    "deep_h":     { "mean": 1.3, "min": 0.9, "max": 1.8 },
    "avg_hrv":    { "mean": 42,  "min": 31,  "max": 55, "trend_per_week": 1.8 },
    "efficiency": { "mean": 88,  "min": 82,  "max": 93 }
  }
}
```

> **New to MCP?** Model Context Protocol lets AI clients reach external data.
> Install this server, connect it once, then ask about your sleep in plain
> language. No coding involved.

Works locally in Claude Code and Codex, and remotely in claude.ai and ChatGPT
once it is deployed to a server of your own.

*Читать по-русски: [README.ru.md](README.ru.md)*

## Why another one

- **Compact by default.** Tools return per-day values plus statistics with a
  trend, not the raw API payload. Raw responses stay one `raw=True` away.
  A month of heart-rate data shrinks by more than 10×.
- **One codebase, two transports.** `stdio` for local use, `streamable-http`
  for remote. A flag apart, not a rewrite.
- **Timezone-correct.** Oura filters some endpoints by an internal UTC
  timestamp while returning a local `day` field, and returns heart-rate
  timestamps in UTC. Both quietly lose or misplace data outside UTC. This
  server handles it — see [Timezone handling](#timezone-handling).
- **Survives flaky networks.** Follows `next_token` pagination, retries
  dropped connections with exponential backoff, and turns HTTP status codes
  into messages that say what to fix.
- **Try before authorizing.** Oura's sandbox works with no credentials at all.

## Quick start

Requires [uv](https://docs.astral.sh/uv/). No Oura token needed for this part.

```bash
git clone https://github.com/AntVsl/oura_mcp && cd oura_mcp
cp .env.example .env
uv sync
```

Check that data flows (hits Oura's sandbox, no auth required):

```bash
uv run python -m my_oura_mcp.smoke
```

Connect it locally to Claude Code or Codex:

```bash
claude mcp add --scope user oura -- uv --directory /path/to/oura_mcp run my-oura-mcp
codex mcp add oura -- uv --directory /path/to/oura_mcp run my-oura-mcp
```

Then ask your client for an Oura summary. The `get_status` tool reports which
mode the server is in.

## Tools

| Tool | Returns | Default range |
|---|---|---|
| `get_daily_summary` | Sleep, readiness and activity scores at once | 7 days |
| `get_sleep` | Sleep stages, efficiency, HRV, resting HR, breathing, temperature | 7 days |
| `get_sleep_score` | Daily sleep score only — lighter than `get_sleep` | 7 days |
| `get_readiness` | Readiness score, HRV balance, temperature deviation | 7 days |
| `get_activity` | Activity score, steps, calories | 7 days |
| `get_heartrate` | Per-minute heart rate collapsed to daily stats | 3 days |
| `get_spo2` | Blood oxygen during sleep, breathing disturbance index | 7 days |
| `get_stress` | Time under load and in recovery | 7 days |
| `get_heart_health` | Cardiovascular age, VO₂max | 30 days |
| `get_tags` | Tags you entered in the Oura app | 30 days |
| `get_status` | Server mode and authorization state | — |

Every data tool takes either `days_back` or an explicit `start_date`/`end_date`
pair (`YYYY-MM-DD`), plus `raw` to get Oura's untouched response.

## Using your own data

The sandbox returns synthetic data. For your own you need an Oura application
and a one-time authorization.

**1. Register an application** at
[developer.ouraring.com/applications](https://developer.ouraring.com/applications):

| Field | Value |
|---|---|
| Redirect URI | `http://localhost:8765/callback` — matched byte for byte |
| Scopes | `daily`, `heartrate`, `tag`, `spo2`, `stress`, `heart_health` |
| Everything else | Arbitrary; not enforced for personal applications |

No review needed: a fresh application works immediately, capped at 10 users.

**2. Put `OURA_CLIENT_ID` and `OURA_CLIENT_SECRET` into `.env`.**

**3. Authorize once:**

```bash
uv run my-oura-mcp auth
```

This starts a local server on your `OURA_REDIRECT_URI`, opens a browser, and
stores tokens in `.oura/tokens.json` with mode `600`. The server refreshes
them on its own from there.

**4. Set `OURA_API_MODE=production`** in `.env`.

Housekeeping:

```bash
uv run my-oura-mcp auth --status   # authorized? how long is the token good for?
uv run my-oura-mcp auth --logout   # forget stored tokens
```

> Personal Access Tokens no longer work: Oura stopped issuing them in
> December 2025. OAuth2 is the only way in.

> **Refresh tokens are single-use.** Each refresh mints a new one and kills the
> old, so two servers sharing a token store will knock each other out. The
> symptom is a `400` mentioning single use; the cure is re-running
> `my-oura-mcp auth` and keeping exactly one live instance.

## Configuration

Everything lives in `.env` (see `.env.example`). Secrets never reach git.

| Variable | Purpose |
|---|---|
| `OURA_CLIENT_ID` / `OURA_CLIENT_SECRET` | Oura application credentials |
| `OURA_REDIRECT_URI` | Must match the application exactly |
| `OURA_API_MODE` | `sandbox` (synthetic data) or `production` |
| `OURA_TZ` | Timezone deciding what "today" means. **Set explicitly on servers** |
| `OURA_MCP_TOKEN` | Shared secret guarding the HTTP endpoint; also the consent-page password |
| `OURA_PUBLIC_URL` | Public `https` address. When set, enables OAuth for Claude.ai and ChatGPT |
| `OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS` | Comma-separated OAuth client origins; defaults to Claude.ai and ChatGPT |
| `OURA_TOKEN_STORE` | Where the OAuth flow writes tokens. Not set by hand |
| `OURA_CACHE_DB` | SQLite cache file. An empty value disables caching |

## Running it

The same code serves both cases — only the transport differs.

### Locally, in Claude Code or Codex

```bash
claude mcp add --scope user oura -- uv --directory /path/to/oura_mcp run my-oura-mcp
codex mcp add oura -- uv --directory /path/to/oura_mcp run my-oura-mcp
```

`--scope user` makes the Claude Code server visible from any directory; verify
with `claude mcp list`. Verify the Codex connection with `codex mcp list`.

### Locally over HTTP, for debugging

```bash
uv run my-oura-mcp --transport http --port 8000
```

No secret required on loopback.

### Remotely, reachable from anywhere

This is what makes the server usable from any device, Claude.ai, and ChatGPT.
You need a host with a public address and a domain of your own.

The step-by-step runbook is **[docs/DEPLOY.md](docs/DEPLOY.md)** (Russian) — how
to let traffic in, how to move Oura authorization onto the server, and how to
connect web clients. What follows is only what makes this path different.

There are two ways to expose the server, and the choice is not cosmetic. Caddy
with a Let's Encrypt certificate is simpler, but the certificate lands in
Certificate Transparency, a public log, which reveals that this address hosts a
service. A Cloudflare Tunnel opens no inbound ports at all. If a VPN lives on
the same host, only the tunnel will do.

Claude.ai and ChatGPT connect over OAuth, which `OURA_PUBLIC_URL` turns on:
the server becomes its own authorization server with dynamic client
registration. No client ID or secret is stored in the connector dialog; the
client registers itself and the consent page asks for `OURA_MCP_TOKEN`.

Dynamic OAuth registration is restricted to `https://claude.ai` and
`https://chatgpt.com` by default. If another trusted client needs to connect,
add only its exact origin to
`OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS`; the consent page shows the client and
return origin before asking for the secret.

Claude Code connects from any machine with a header, no OAuth involved:

```bash
claude mcp add --scope user --transport http oura https://your-domain/mcp --header "Authorization: Bearer YOUR_TOKEN"
```

Without the header, or with a wrong token, the endpoint answers `401`.

Codex can use the same remote endpoint with the token kept in its environment:

```bash
export OURA_MCP_TOKEN=YOUR_TOKEN
codex mcp add oura --url https://your-domain/mcp --bearer-token-env-var OURA_MCP_TOKEN
```

For ChatGPT, enable developer mode, then create a custom app in **Settings →
Apps → Create** with `https://your-domain/mcp`, select OAuth, and complete the
consent prompt while tools are scanned. ChatGPT connects to remote MCP servers;
it cannot connect directly to a local process. See [the deployment runbook](docs/DEPLOY.md).

### Which one

| | stdio, local | HTTP, remote |
|---|---|---|
| Claude Code on this machine | yes | yes |
| Codex on this machine | yes | yes |
| Other devices | no | yes |
| claude.ai in the browser | no | yes |
| ChatGPT in the browser | no | yes |
| Needs a domain and a host | no | yes |
| Data leaves the machine | no | yes, to your host |

Keep **one live instance**: Oura's refresh token is single-use, and two servers
sharing a token store will fight. Once the remote one is up, point Claude Code
at it too, using step 4.

## Timezone handling

Three separate bugs came from Oura's date semantics, all of which lost data
silently rather than raising an error. Worth knowing if you build against this
API yourself:

- **`sleep` and `daily_activity` are filtered by an internal UTC timestamp**,
  not by the `day` field Oura itself returns. At UTC+3 a night that starts
  after midnight lands in the previous UTC day: asking for `28..28` returns
  nothing while the record with `day=28` plainly exists. The server widens the
  window and trims by `day` afterwards. Verified by sweeping every endpoint;
  the other six behave.
- **`heartrate` returns timestamps in UTC.** Grouping by the first ten
  characters of that string splits a local day in two, pushing 00:00–03:00
  local into the previous day — exactly the resting heart rate you care about.
  Grouping uses `OURA_TZ`.
- **Oura returns several sleep records per day** — the night plus naps. Picking
  an arbitrary one lets a 12-minute nap displace a full night. The record typed
  `long_sleep` wins, or the longest one; naps are reported separately as
  `naps_h` so their HRV never averages with the night's.

## Security

- `.env`, the token store and the cache are in `.gitignore`. Verify before
  committing: `git status --porcelain`.
- The token store and SQLite cache are written with owner-only (`600`) file
  permissions.
- The HTTP endpoint is guarded by `OURA_MCP_TOKEN` using a constant-time
  comparison. The access model is deliberately simple: one secret, one owner,
  no per-user separation.
- **The server refuses to start on a non-loopback address without a secret**
  rather than quietly serving health data to the open internet. Try it:
  `uv run my-oura-mcp --transport http --host 0.0.0.0`.
- `/healthz` is intentionally open — a reverse proxy needs it, and it returns
  nothing but `ok`.
- Caddy strips the `Authorization` header from its logs.

## Caching

Older days go into SQLite. The two most recent completed days are rechecked on
every request because late syncs can update them; older history is served from
the cache without a network call.

```bash
uv run my-oura-mcp cache --status   # what is cached
uv run my-oura-mcp cache --clear    # forget it
```

Three things worth knowing. **Today is never cached** — Oura is still writing
it. **Empty days are not cached either**: an empty day means either "did not
wear the ring" or "has not synced yet", and the second resolves itself within
hours, whereas a cached blank would last forever. **The mode is part of the
key**, so sandbox data cannot surface in production.

Per-minute heart rate bypasses the cache: its rows carry no `day` field.

## MCP resources

Clients that support resources can read `oura://today`, `oura://yesterday`,
and `oura://week`. They provide the same sleep/readiness/activity summaries
as the tools, without choosing arguments manually.

## Skill with recipes

[skills/oura](skills/oura) ships a Claude-oriented skill — not more tools, but workflows
on top of them: whether sleep is actually improving, whether today can take
load, what the body was doing on a bad day, whether a change in routine did
anything. Each is a sequence of calls plus a way to reason about the answer,
which no single tool can express.

Install it by copying into your client's skills directory:

```bash
cp -r skills/oura ~/.claude/skills/
```

The recipes are checked against the code by tests: a field name that no tool
returns fails `uv run pytest` instead of quietly sending the model nowhere.

Codex can also load these repository skills after copying them to its skills
directory:

```bash
mkdir -p ~/.codex/skills
cp -R skills/oura skills/oura-mcp-maintenance ~/.codex/skills/
```

`oura` interprets data through the MCP server. `oura-mcp-maintenance` guides
safe changes to this repository and its Claude/ChatGPT/Codex integration.

## When something doesn't work

**Claude says there are no Oura tools.** The server didn't connect. `claude mcp
list` shows its state. A common cause is a relative path where a full one is
required — `uv run my-oura-mcp install` prints the command with the right one.

**"Авторизация не пройдена — токенов нет".** The server is in `production` mode
but has never signed in to Oura. Run `uv run my-oura-mcp auth`; check token state
with `uv run my-oura-mcp auth --status`.

**`401` against a server on a VPS.** `OURA_MCP_TOKEN` doesn't match. Header
values are sent verbatim, so the word `Bearer` and the space belong to the value:
`Bearer abc123`, not `abc123`.

**Data comes back for the wrong day.** `OURA_TZ` isn't set. A server clock is
almost always UTC, so "today" starts hours off from yours and a night's sleep
lands in the previous day. Set it explicitly, e.g. `OURA_TZ=Europe/Moscow`.

**"refresh-токен отвергнут".** Oura's refresh token is single-use, and this
happens when a second instance spent it. Keep exactly one alive: once the VPS is
up, point local Claude Code at it too. Recover with `my-oura-mcp auth`.

**Requests to `api.ouraring.com` fail** with `SSL_ERROR_SYSCALL` or a timeout.
Usually not the server: the client retries four times with backoff. If that
doesn't help, a VPN generally does.

**claude.ai won't connect to your server.** Check that `OURA_PUBLIC_URL` is set
and matches the connector URL character for character, including `https://` and
no trailing slash. The startup banner says whether OAuth came up. Beyond that,
see [docs/DEPLOY.md](docs/DEPLOY.md).

**You press Allow on the consent page and nothing happens.** Check the server
logs: a `POST /oauth/consent` returning `303` with no `POST /token` after it
means the browser blocked the hop back to claude.ai. That is what an over-strict
`Content-Security-Policy` looks like — and `curl` cannot reproduce it, since it
does not enforce CSP at all.

**"Запрос устарел" / request expired.** Authorization requests live in process
memory, so restarting the server invalidates any consent page already open. The
secret is not the problem — go back to claude.ai and start the connection again.

## Development

```bash
uv run pytest
```

Tests never touch the network; Oura's responses are stubbed with `respx`.
Tool tests go through `mcp.call_tool()` rather than calling the functions
directly — some bugs only appear on the real protocol layer, where MCP clients
pass declared defaults as explicit arguments.

For Codex and other coding agents, repository instructions and a task/review
structure live in [AGENTS.md](AGENTS.md) and [docs/agents](docs/agents/README.md).

**x86_64 macOS:** `cryptography` 49+ ships no wheel for this platform and tries
to build from Rust sources. `pyproject.toml` pins `48.0.0` for it specifically;
Linux and native arm64 are untouched. This bites Apple Silicon too whenever
Homebrew lives in `/usr/local` rather than `/opt/homebrew` — check with
`file $(which python3)`.

**Flaky network:** if requests to `api.ouraring.com` fail with
`SSL_ERROR_SYSCALL` or time out, it usually isn't the server. The client makes
four attempts with backoff; beyond that, try a VPN.

See [docs/ROADMAP.md](docs/ROADMAP.md) for what's planned and what was
deliberately deferred.

## License

MIT

<!--
Ownership proof for the MCP registry: this token ties the PyPI package to the
server name in server.json, and the registry refuses to publish without it.
Kept in a comment because it is machine-facing, not something a reader needs.
Do not edit by hand — tests/test_packaging.py checks it against server.json.

mcp-name: io.github.AntVsl/oura-mcp
-->
