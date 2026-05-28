# Threat model — schwab-positions-mcp

This document complements [`SECURITY.md`](SECURITY.md) (which covers the
5-layer read-only boundary in depth) with a structured threat enumeration
in the STRIDE style.

## Assets

| Asset                           | Sensitivity | Where it lives                                                |
| ------------------------------- | ----------- | ------------------------------------------------------------- |
| Schwab OAuth refresh token      | High        | `~/.config/schwab-positions-mcp/token.json` (`0o600`)         |
| Schwab access token (in-memory) | High        | RSS of the running MCP process                                |
| Cached account snapshots        | Medium      | `~/.local/state/schwab-positions-mcp/cache.duckdb` (`0o600`)  |
| `SCHWAB_API_KEY` / `_APP_SECRET` | Medium      | `.env` on disk; injected to env at server start              |
| Server logs                     | Low         | `${XDG_STATE_HOME}/schwab-positions-mcp/logs/server.log`      |

## STRIDE table

| Threat                                      | Vector                                                                 | Mitigation                                                                                          |
| ------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **S**poofing — fake MCP server in PATH      | Attacker drops a binary named `schwab-positions-mcp` earlier in PATH   | User installs via `uv` from this repo only; signed git tags for releases.                           |
| **T**ampering — malicious PR adds `place_order` | Maintainer review failure                                          | Layer 4 grep gate (`security-grep.yml`); CodeQL; Layer 1 white-list test (Phase 3 Layer 5).         |
| **T**ampering — supply-chain dependency compromise | Compromised PyPI package                                          | `pip-audit` in CI; dependabot alerts; pinned major versions in `pyproject.toml`.                    |
| **R**epudiation — "I didn't run that tool"  | Missing audit trail                                                    | Schwab side: `Schwab-Client-CorrelId` request id surfaces in tool errors; structured logs to disk.  |
| **I**nformation disclosure — token leak     | `.env` / `token.json` accidentally committed                           | `.gitignore` covers both; pre-commit gitleaks + detect-secrets; CHANGELOG warns; `0o600` on token. |
| **I**nformation disclosure — log scraping   | Bearer / refresh token logged in plain text                            | `tools._common._redact()` redacts request ids; schwab-py loggers forced to WARNING in `server.py`.  |
| **D**enial of service — Schwab rate-limit   | Misconfigured agent loop hammers Schwab                                | Cache layer absorbs repeats; future: per-process rate limiter (Phase 4).                            |
| **E**levation of privilege — LLM places trade | Prompt injection / jailbreak triggers a hidden mutation tool          | **No mutation tool exists** (Layers 1, 3); Layer 4 enforces this in CI; OAuth scope is unavoidably `trade`. |

## Trust boundaries

```text
+--------------------+     stdio (JSON-RPC)     +-------------------------+
|  MCP host (LLM)    | <----------------------> | schwab-positions-mcp    |
|  (Claude, Cursor…) |                          |  process                |
+--------------------+                          +-------------------------+
                                                            |
                                                            | HTTPS (TLS 1.3, schwab-py)
                                                            v
                                                +---------------------------+
                                                | api.schwabapi.com         |
                                                | (Schwab Trader API)       |
                                                +---------------------------+
```

The boundary that matters here is between the LLM host and the
`schwab-positions-mcp` process: anything the LLM can ask for must come
back through one of the 7 registered tools, and none of those tools have
a mutation surface. The TLS hop to Schwab is delegated to schwab-py +
httpx; the certificate pinning question is left to httpx defaults.

## Residual risk

- **Host compromise.** If an attacker has shell as your user, they have
  the token file and can call Schwab directly. This MCP server's
  read-only contract does not help. Mitigation: FDE, dedicated user,
  no `~/.config/schwab-positions-mcp` on cloud-sync drives.
- **Schwab-side compromise.** Out of our control.
- **OAuth scope is `trade`.** Schwab does not offer a narrower scope.
  We cannot fix this in our codebase; the 5-layer boundary is the
  compensating control.

## Detection

- Schwab developer-portal "Recent Activity" panel shows every API call
  timestamped with the IP source. Audit weekly.
- `cache.duckdb`'s `cache_events` table records every write — anomalous
  rows outside your usual usage window are a leakage signal.
- Server logs at WARNING level surface any `NotImplementedError` raised
  by Layer 1 — that means *something* tried to call a mutation method.
  Investigate immediately.
