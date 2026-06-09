# schwab-positions-mcp

> **🔒 READ-ONLY by design — see [docs/SECURITY.md](docs/SECURITY.md)**
>
> No `place_order`, no `cancel_order`, no `replace_order`. This MCP server
> surfaces Schwab account state to LLM agents and **never** exposes a
> mutation endpoint. Enforced by a 5-layer boundary (runtime white-list +
> startup warning + tool surface audit + CI grep gate + reject test).

[![test](https://github.com/kevinkda/schwab-positions-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/kevinkda/schwab-positions-mcp/actions/workflows/test.yml)
[![CodeQL](https://github.com/kevinkda/schwab-positions-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/kevinkda/schwab-positions-mcp/actions/workflows/codeql.yml)
[![Security Boundary](https://github.com/kevinkda/schwab-positions-mcp/actions/workflows/security-grep.yml/badge.svg)](https://github.com/kevinkda/schwab-positions-mcp/actions/workflows/security-grep.yml)

[简体中文](README_zh.md)

`schwab-positions-mcp` is an MCP (Model Context Protocol) server that
exposes a **read-only** view of your Charles Schwab brokerage account —
positions, orders, transactions, and account balances — to any
MCP-compatible LLM client (Claude Desktop, Cursor, etc.). It is the
companion of [`schwab-marketdata-mcp`](https://github.com/kevinkda/schwab-marketdata-mcp),
which exposes Schwab Market Data Production endpoints. The two repos are
deliberately split so the trading-account credential set lives in its own
process and config directory.

## Tools (8)

### Account / portfolio (6)

| Tool                     | Description                                                                                          |
| ------------------------ | ---------------------------------------------------------------------------------------------------- |
| `get_accounts`           | List all linked Schwab accounts. Optional `fields=["positions"]` expands holdings inline.            |
| `get_account_numbers`    | Map plaintext `accountNumber` to encrypted `account_hash` (required by every other tool below).      |
| `get_account_positions`  | Fetch one account's holdings + balances; persists a positions snapshot to local DuckDB when caching is enabled. |
| `get_orders_history`     | Return orders between two timezone-aware datetimes (Schwab caps lookback at 60 days).                |
| `get_transactions`       | Return transactions between two ISO dates (TRADE / DIVIDEND_OR_INTEREST / etc).                      |
| `get_account_summary`    | Compact aggregate: position count, total market value, total P&L, cash, buying power, balances.     |

### Meta (2)

| Tool              | Description                                                                                  |
| ----------------- | -------------------------------------------------------------------------------------------- |
| `health_check`    | Readiness probe — reports credential / token presence without contacting Schwab.             |
| `get_server_info` | Server metadata — version, platform, **`is_read_only: true`**, tool list.                    |

## Install

```bash
git clone https://github.com/kevinkda/schwab-positions-mcp.git
cd schwab-positions-mcp
uv sync --extra dev
```

Requires Python ≥ 3.11 and an installed Schwab Trader API client app
(see [docs/REGISTER.md](docs/REGISTER.md)).

## Configure

```bash
cp .env.example .env
# edit .env to set SCHWAB_API_KEY, SCHWAB_APP_SECRET, SCHWAB_CALLBACK_URL
```

## OAuth (one-time)

```bash
uv run python -m schwab_positions_mcp.auth login_flow
# or, if browser auto-redirect breaks:
uv run python -m schwab_positions_mcp.auth manual_flow
```

The token persists at `~/.config/schwab-positions-mcp/token.json` (mode
`0o600`, separate directory from `schwab-marketdata-mcp`).

> **OAuth scope.** Schwab requires the `trade` scope on every token,
> including read-only positions / orders / transactions calls. The token
> therefore has the *capability* to trade. **This MCP server blocks every
> mutation call at the code layer** so a compromised LLM cannot use it to
> place trades. See [docs/SECURITY.md](docs/SECURITY.md) for the 5-layer
> contract.

## Run

```bash
uv run schwab-positions-mcp           # MCP stdio transport
# or
uv run python -m schwab_positions_mcp # equivalent
```

Wire it into Claude Desktop / Cursor by pointing at the binary in the
usual MCP `command` + `args` shape.

## Security

- **Read-only contract:** see [docs/SECURITY.md](docs/SECURITY.md).
- **Threat model:** see [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
- **Token storage:** `~/.config/schwab-positions-mcp/token.json`,
  `0o600`, never committed.
- **CI:** `test.yml` (lint / type / unit), `codeql.yml` (CodeQL),
  `security-grep.yml` (no-trade gate).

## Cache

A local DuckDB cache can persist read-only snapshots (positions / orders
/ transactions) at `~/.local/state/schwab-positions-mcp/cache.duckdb` so
your LLM agent can run "what changed since last week" queries without
hammering Schwab.

The cache is **disabled by default (opt-in)** — no DuckDB file is created
and every tool call hits Schwab live. Enable it explicitly with
`SCHWAB_POSITIONS_CACHE_ENABLED=true` (also accepts `1` / `yes` / `on`).
When disabled, tools return `_cache_status: "skipped:disabled"`; the
response shape is otherwise unchanged.

## License

MIT — see [LICENSE](LICENSE).
