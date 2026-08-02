# Agent guide

This file applies to the entire repository. It is the source of truth for
Codex and other coding agents; more-specific `AGENTS.md` files would override
it for their directory.

## Mission and boundaries

`my-oura-mcp` is a privacy-sensitive MCP server for Oura Ring data. Preserve
the same public tool schema and response shape for every client:

- Claude Code and Codex use local `stdio` or an authenticated HTTP endpoint.
- Claude.ai and ChatGPT use the public `streamable-http` endpoint and OAuth.
- The server summarizes consumer health data; do not add diagnosis, treatment,
  or medical claims to tools, prompts, skills, or documentation.

Never print, commit, or put real values from `.env`, `.oura/`, bearer headers,
or OAuth tokens into tests, documentation, fixtures, logs, or issue text.
Do not broaden `OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS` without an explicit,
reviewed client origin. It is an OAuth redirect allowlist, not a convenience
setting.

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/my_oura_mcp/` | Server, OAuth, Oura client, cache, output shaping |
| `tests/` | Offline unit and MCP protocol tests |
| `skills/oura/` | Client-neutral Oura analysis recipes; keep medical boundary |
| `skills/oura-mcp-maintenance/` | Focused maintenance skill for this repository |
| `docs/` | Deployment, design decisions, and agent workflow |
| `server.json` | MCP Registry manifest; version must match `pyproject.toml` |
| `compose*.yml`, `Dockerfile` | Production HTTP deployment |

Read the relevant module and its tests before changing behavior. Prefer a
small focused patch; do not reformat unrelated files.

## Standard workflow

1. Inspect `git status --short` first and preserve unrelated working-tree
   changes.
2. State the owned files and acceptance criteria in the task brief from
   [`docs/agents/task-template.md`](docs/agents/task-template.md) for any
   change larger than a one-file fix.
3. Add or update a regression test whenever externally observable behavior,
   OAuth policy, parsing, cache behavior, packaging, or documentation contract
   changes.
4. Run the narrow test first, then the full gate:

   ```bash
   uv lock --check
   uv run pytest -q
   git diff --check
   ```

5. Report changed files, verification output, and any known limitation. Do
   not stage, commit, push, publish, or deploy unless explicitly asked.

## Cross-client compatibility

When changing transport, authentication, metadata, or setup instructions,
verify the appropriate surface:

| Surface | Required check |
| --- | --- |
| Local `stdio` | `uv run my-oura-mcp install`; both Claude and Codex commands remain valid |
| Remote HTTP | `/healthz`, `/mcp` authentication, and OAuth discovery routes |
| Claude.ai / ChatGPT | Public HTTPS URL, dynamic registration allowlist, consent page wording |
| Registry / package | `server.json`, package version, wheel marker, README ownership token |

ChatGPT is a remote-MCP client: do not document a local ChatGPT connection.
Codex supports both local stdio and remote HTTP. Keep this distinction clear in
README and deployment documentation.

## Code conventions

- Python 3.12+, typed public interfaces, `async` for network I/O.
- Keep tests network-free: mock Oura with `respx` and call MCP tools through
  `mcp.call_tool()` when testing the protocol boundary.
- Preserve explicit timezone handling and deterministic chronological ordering.
- Treat refresh tokens as single-use; never introduce concurrent writers to a
  token store.
- Use configuration defaults only for non-secret values. Validate public URLs
  as HTTPS and redirect origins as origins, not full paths.

## Documentation conventions

The primary README is English; `README.ru.md` and `docs/DEPLOY.md` are Russian.
Update both READMEs when user-facing setup or supported clients change. Link
deeper process guidance rather than duplicating it. See
[`docs/agents/README.md`](docs/agents/README.md) for task and review templates.
