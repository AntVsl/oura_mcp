# my-oura-mcp

[![CI](https://github.com/AntVsl/oura_mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/AntVsl/oura_mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

An MCP server that gives Claude access to your Oura Ring data.

Ask "how did I sleep last week" and Claude calls the right tool and gets a
summary back — not a wall of JSON.

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

Connect it to Claude Code:

```bash
claude mcp add --scope user oura -- uv --directory /path/to/oura_mcp run my-oura-mcp
```

Then ask Claude for your Oura summary. The `get_status` tool reports which
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
| `OURA_MCP_TOKEN` | Shared secret guarding the HTTP endpoint (remote only) |
| `OURA_TOKEN_STORE` | Where the OAuth flow writes tokens. Not set by hand |
| `OURA_CACHE_DB` | SQLite cache file |

## Running it

The same code serves both cases — only the transport differs.

### Locally, in Claude Code

```bash
claude mcp add --scope user oura -- uv --directory /path/to/oura_mcp run my-oura-mcp
```

`--scope user` makes the server visible from any directory; without it, only
from where you ran the command. Verify with `claude mcp list`.

### Locally over HTTP, for debugging

```bash
uv run my-oura-mcp --transport http --port 8000
```

No secret required on loopback.

### Remotely, reachable from anywhere

This is what makes the server usable from any device and from claude.ai in the
browser. You need a host with a public IP and a domain already pointing at it.

**1. Prepare secrets** in `.env` on the host:

```bash
python3 -c "import secrets;print(secrets.token_urlsafe(32))"
```

That string goes into `OURA_MCP_TOKEN`, your domain into `OURA_DOMAIN`, and
`OURA_TZ` to your actual timezone — a server's clock is almost certainly UTC.

**2. Bring it up:**

```bash
docker compose up -d
```

Caddy issues the TLS certificate itself. Only Caddy is exposed; the MCP port
stays inside the Docker network. Liveness check is `curl https://your-domain/healthz`,
which needs no token and returns no data.

**3. Connect claude.ai:** Settings → Connectors → Add custom connector, URL
`https://your-domain/mcp`. Under **Request headers** add `Authorization` with
the value `Bearer <your OURA_MCP_TOKEN>` — including the word `Bearer` and the
space.

> Request-header authentication is in beta at Claude and not rolled out to
> everyone. If the section is missing, the alternative requires a full
> OAuth 2.1 server on the MCP side.

**4. Connect Claude Code** from any machine:

```bash
claude mcp add --scope user --transport http oura https://your-domain/mcp --header "Authorization: Bearer YOUR_TOKEN"
```

Without the header, or with a wrong token, the endpoint answers `401`.

### Which one

| | stdio, local | HTTP, remote |
|---|---|---|
| Claude Code on this machine | yes | yes |
| Other devices | no | yes |
| claude.ai in the browser | no | yes |
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
- The HTTP endpoint is guarded by `OURA_MCP_TOKEN` using a constant-time
  comparison. The access model is deliberately simple: one secret, one owner,
  no per-user separation.
- **The server refuses to start on a non-loopback address without a secret**
  rather than quietly serving health data to the open internet. Try it:
  `uv run my-oura-mcp --transport http --host 0.0.0.0`.
- `/healthz` is intentionally open — a reverse proxy needs it, and it returns
  nothing but `ok`.
- Caddy strips the `Authorization` header from its logs.

## Development

```bash
uv run pytest
```

Tests never touch the network; Oura's responses are stubbed with `respx`.
Tool tests go through `mcp.call_tool()` rather than calling the functions
directly — some bugs only appear on the real protocol layer, where MCP clients
pass declared defaults as explicit arguments.

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
