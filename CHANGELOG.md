# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-05-28

### Added

- Initial public release of `schwab-positions-mcp`, a read-only MCP
  server for Schwab account positions, orders, and transactions.
- 7 tools: `get_accounts`, `get_account_positions`, `get_orders_history`,
  `get_transactions`, `get_account_summary`, `health_check`,
  `get_server_info`.
- DuckDB cache layer (4 tables: `positions_snapshots` /
  `orders_history` / `transactions_history` / `cache_events`).
- 5-layer read-only security boundary:
  1. Layer 1 — `ReadOnlySchwabClient` white-list at `client.py`
  2. Layer 2 — Startup log warning in `server.py`
  3. Layer 3 — Tool layer (7 tools, no mutation surface)
  4. Layer 4 — CI grep gate (`.github/workflows/security-grep.yml`)
  5. Layer 5 — Mutation reject test (`tests/test_client_readonly.py`)
- OAuth scope=trade with token storage at
  `~/.config/schwab-positions-mcp/token.json` (POSIX 0o600, isolated
  from schwab-marketdata-mcp token).
- Cross-platform `_platform.py` shim (POSIX + Windows file locking,
  permissions, notifications).
- Bilingual docs (`README.md` + `README_zh.md`) with READ-ONLY banner.
- 159 unit / integration tests with 91% coverage
  (`ReadOnlySchwabClient` 100%, every business tool ≥ 89%).

### Compatibility

- Python ≥ 3.11
- `schwab-py` 1.5.x
- `mcp` 1.6.x
- `pydantic` 2.x
- `duckdb` 1.x

### Security

- See [`docs/SECURITY.md`](docs/SECURITY.md) for the threat model and
  5-layer boundary rationale.

[Unreleased]: https://github.com/kevinkda/schwab-positions-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.0
