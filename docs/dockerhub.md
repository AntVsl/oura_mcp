# my-oura-mcp

An MCP server that gives Claude access to your Oura Ring data — sleep,
readiness, HRV, resting heart rate, activity, SpO₂ and stress.

Ask *"how did I sleep last week"* and Claude calls the right tool and gets a
compact summary back, not a wall of JSON.

**Source and full documentation:** https://github.com/AntVsl/oura_mcp

## Tags

| Tag | Meaning |
|---|---|
| `latest` | Most recent release |
| `0.3`, `0.3.0` | Pinned versions |

Built for `linux/amd64` and `linux/arm64`.

## Quick start

This image serves MCP over streamable HTTP, meant to be reached from
claude.ai or from Claude Code on any machine.

```bash
docker run -d \
  -e OURA_MCP_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  -e OURA_CLIENT_ID=your_client_id \
  -e OURA_CLIENT_SECRET=your_client_secret \
  -e OURA_TZ=Europe/Moscow \
  -e OURA_PUBLIC_URL=https://mcp.example.com \
  -v oura-data:/data \
  -p 8000:8000 \
  iican/oura-mcp:0.3.0
```

Then point a client at `https://your-host/mcp`. Claude Code authenticates with
the header `Authorization: Bearer <your OURA_MCP_TOKEN>`; claude.ai and the
mobile apps go through OAuth, which `OURA_PUBLIC_URL` enables — Claude registers
itself and the consent page asks for that same token. Drop `OURA_PUBLIC_URL` if
you only ever connect from Claude Code.

Liveness check: `curl http://your-host:8000/healthz` — needs no token and
returns no data.

### Not using containers?

For a local setup with Claude Code, the PyPI package is simpler — no image, no
exposed port, no shared secret to manage:

```bash
uvx my-oura-mcp
```

https://pypi.org/project/my-oura-mcp/

## Environment

| Variable | Purpose |
|---|---|
| `OURA_MCP_TOKEN` | **Required.** Shared secret guarding the endpoint; also the consent-page password |
| `OURA_PUBLIC_URL` | Public `https` address, no trailing slash. When set, enables OAuth so claude.ai can connect |
| `OURA_CLIENT_ID` / `OURA_CLIENT_SECRET` | Oura application credentials from developer.ouraring.com |
| `OURA_TZ` | IANA timezone deciding what "today" means. **Set this** — the container clock is UTC |
| `OURA_API_MODE` | `sandbox` (Oura's synthetic data, no auth) or `production` |
| `OURA_TOKEN_STORE` | Defaults to `/data/tokens.json`; mount a volume to keep it |

## Two things worth knowing

**The server refuses to start without `OURA_MCP_TOKEN`** when bound to
anything other than loopback. This is deliberate: the alternative is quietly
serving health data to the open internet. Requests without a valid bearer
token get `401`.

**Authorization happens outside the container.** Oura's OAuth flow needs a
browser and a loopback redirect, so run `my-oura-mcp auth` locally first, then
mount the resulting `tokens.json` into `/data`. Refresh tokens are single-use,
so run exactly one instance against a given token store — two will knock each
other out of authorization.

Put the container behind TLS. A ready `compose.yml` with Caddy and automatic
certificates ships in the repository.

## License

MIT — https://github.com/AntVsl/oura_mcp/blob/main/LICENSE
