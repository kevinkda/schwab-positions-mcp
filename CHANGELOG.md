# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Phase 1 — repo scaffolding.** Initial repository with quality
  infrastructure ported from `schwab-marketdata-mcp`: `.gitignore`,
  `.pre-commit-config.yaml` (ruff / mypy / detect-secrets / markdownlint
  / gitleaks-manual), `pyproject.toml` (Python ≥ 3.11, schwab-py 1.5.x,
  mcp 1.6.x, pydantic 2.x, duckdb 1.x), GitHub Actions
  (`test.yml`, `codeql.yml`, `security-grep.yml`),
  issue / PR templates, dependabot.
- **Phase 2 — core implementation.**
  - `__init__.py` exposing `__version__ = "0.1.0"`.
  - `auth.py` — OAuth `login_flow` / `manual_flow` CLI persisting token
    to `~/.config/schwab-positions-mcp/token.json` (separate directory
    from `schwab-marketdata-mcp`); emits `OAuth scope=trade required ...`
    warning.
  - `client.py` — `ReadOnlySchwabClient` enforcing the
    `_READ_ONLY_METHODS` white-list (Layer 1 of the 5-layer read-only
    boundary). Non-white-listed attribute access raises
    `NotImplementedError`. `__setattr__` is also blocked.
  - `cache.py` — DuckDB schema for `positions_snapshots`,
    `orders_history`, `transactions_history`, `cache_events`, plus
    write helpers, singleton accessor, and quarantine-on-corruption
    handling.
  - `models.py` — Pydantic v2 input schemas (`GetAccountsInput`,
    `GetAccountPositionsInput`, `GetOrdersHistoryInput`,
    `GetTransactionsInput`, `GetAccountSummaryInput`) with strict
    field validation (`extra="forbid"`, frozen, tz-aware datetimes,
    Schwab enum literals).
  - `tools/` — 5 business tools (`get_accounts`,
    `get_account_positions`, `get_orders_history`, `get_transactions`,
    `get_account_summary`) + 2 meta tools (`health_check`,
    `get_server_info`). Each returns a dict including `_cache_status`.
    Meta tools return `is_read_only: true`.
  - `server.py` — FastMCP entrypoint registering all 7 tools, emitting
    the startup `READ-ONLY MODE` warning (Layer 2).
- **Security boundary documentation.** `docs/SECURITY.md` describes the
  5-layer read-only contract. `docs/THREAT_MODEL.md` enumerates threats
  in / out of scope.
- **CI grep gate.** `.github/workflows/security-grep.yml` fails the
  build if any of `place_order` / `cancel_order` / `replace_order`
  appears in `src/`, and asserts that `client.py` still declares
  `_READ_ONLY_METHODS` and raises `NotImplementedError` (Layer 4).
- **Documentation.** `README.md` / `README_zh.md` (with READ-ONLY
  banner), `CONTRIBUTING.md`, `docs/REGISTER.md`, `docs/RELEASE.md`,
  `docs/THREAT_MODEL.md`, `.env.example`.

### Pending (Phase 3 + 4 — separate sibling)

- Test suite (`tests/test_client_readonly.py` Layer 5 mutation reject
  test, models / cache / tool unit tests, OWASP coverage).
- `pytest --cov` ≥ 85% gate.
- Version bump from `0.0.0+dev` → `0.1.0`, CHANGELOG freeze, `gh release
  create v0.1.0`.
- Symlink registration into the local agent skills index.
