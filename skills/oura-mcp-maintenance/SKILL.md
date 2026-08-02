---
name: oura-mcp-maintenance
description: Safely change, test, or document the my-oura-mcp repository. Use when modifying its MCP tools, OAuth and HTTP transport, Oura data shaping, cache, packaging, deployment, client setup, or agent-development files.
---

# Oura MCP maintenance

Make a focused change while preserving privacy, stable MCP contracts, and
compatibility with Claude Code, Claude.ai, Codex, and ChatGPT.

## Orient

1. Read the root `AGENTS.md`, the relevant source module, and its tests.
2. Inspect `git status --short`; preserve unrelated work.
3. For a multi-file task, make the short brief from
   `docs/agents/task-template.md` and state file ownership before editing.

## Change safely

- Keep public tool names, arguments, and response shapes backward compatible.
- Treat `.env`, `.oura/`, bearer tokens, OAuth codes, and token stores as
  secrets. Never expose them in a patch, fixture, log, or documentation sample.
- Treat `OURA_OAUTH_ALLOWED_REDIRECT_ORIGINS` as an allowlist. Accept exact,
  HTTPS origins only; do not add a client origin without an explicit need.
- Preserve local timezone grouping, offline tests, and the single-writer rule
  for Oura refresh tokens.
- Keep health wording descriptive. Do not add diagnostic, treatment, or
  training-prescription language.

## Select the verification

| Change | Minimum evidence |
| --- | --- |
| Tool, shaping, dates, cache | Focused regression test through `mcp.call_tool()` where applicable |
| OAuth, public URL, HTTP auth | Parser test and `server.build()` wiring test |
| Client setup | Verify both README files and the `install` output |
| Package, image, manifest | Version-consistency tests; build a wheel if packaging changes |
| Agent instructions or skills | Check links, frontmatter, and client matrix |

Always finish with:

```bash
uv lock --check
uv run pytest -q
git diff --check
```

Report the changed files, test output, and any limitation. Do not stage,
commit, publish, or deploy unless the user explicitly asks.
