# schwab-positions-mcp

> **🔒 READ-ONLY by design — see [docs/SECURITY.md](docs/SECURITY.md)**
>
> No `place_order`, no `cancel_order`, no `replace_order`. This MCP server
> surfaces Schwab account state to LLM agents and **never** exposes a
> mutation endpoint. Enforced by a 5-layer boundary (runtime allow list +
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

## Tools (14)

### Account / portfolio (9)

| Tool                     | Description                                                                                          |
| ------------------------ | ---------------------------------------------------------------------------------------------------- |
| `get_accounts`           | List all linked Schwab accounts. Optional `fields=["positions"]` expands holdings inline.            |
| `get_account_numbers`    | Map plaintext `accountNumber` to encrypted `account_hash` (required by every other tool below).      |
| `get_account_positions`  | Fetch one account's holdings + balances; persists a positions snapshot to the derived-history cache when caching is enabled. |
| `get_orders_history`     | Return orders between two timezone-aware datetimes (Schwab caps lookback at 60 days).                |
| `get_transactions`       | Return transactions between two ISO dates (TRADE / DIVIDEND_OR_INTEREST / etc).                      |
| `get_user_preferences`   | Return the account user-preference settings (default account, nicknames, streamer routing). No args, no cache write. |
| `get_order_detail`       | Read one order's full detail by numeric `order_id` (status / legs / fills). Read-only — never places / cancels / replaces. |
| `get_transaction_detail` | Read one historical transaction's detail by `transaction_id`. Read-only — no money moves.            |
| `get_account_summary`    | Compact aggregate: position count, total market value, total P&L, cash, buying power, balances.     |

### Derived analytics (3, read-only — pure computation, no cache write)

| Tool                          | Description                                                                                          |
| ----------------------------- | ---------------------------------------------------------------------------------------------------- |
| `get_pnl_analysis`            | Per-position cost basis / unrealized P&L / unrealized %, transaction-derived realized P&L, and a portfolio roll-up. **Cost-basis method: AVERAGE COST** (the positions feed exposes only `averagePrice`; no per-lot records for FIFO). |
| `get_concentration_analysis`  | Top-N weights, Herfindahl-Hirschman Index (HHI), max single-position weight, and asset-type exposure. Sector exposure is `N/A` — the Schwab positions feed has no GICS sector field. |
| `get_cross_account_summary`   | Fan out over `get_account_numbers` → `get_account` per account, then merge positions + balances into a combined view with per-account share-of-total and symbol-level de-duplication. |

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

A pluggable cache can persist read-only **derived-history snapshots**
(positions / orders / transactions) so your LLM agent can run "what
changed since last week" queries without hammering Schwab.

The cache is **disabled by default (opt-in)** — every tool call hits
Schwab live. Enable it with `SCHWAB_POSITIONS_CACHE_ENABLED=true` (also
accepts `1` / `yes` / `on`). When disabled, tools return
`_cache_status: "skipped:disabled"`; the response shape is unchanged.

### Cache backend (v0.3.0)

⚠️ **BREAKING (v0.3.0):** the embedded DuckDB cache is removed in favour of
a pluggable backend selected via `SCHWAB_POSITIONS_CACHE_BACKEND`:

| Backend | Default | Dependency | Notes |
| --- | --- | --- | --- |
| `memory` | ✅ | none (stdlib) | In-process, concurrency-safe, non-blocking, no files. Keeps **no durable history** — snapshot writes report `snapshot_written:0` (graceful degradation). |
| `clickhouse` | — | `pip install schwab-positions-mcp[clickhouse]` + `SCHWAB_POSITIONS_CLICKHOUSE_URL` | Durably persists the position/order/transaction history. |

The cache layer only ever **writes** derived history — it never feeds reads
back into a tool, so it cannot widen the 5-layer read-only boundary. Without
ClickHouse, history persistence degrades to a `requires_clickhouse_persistence`
signal and read-only tools are entirely unaffected.

## License

MIT — see [LICENSE](LICENSE).
