# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-05-29

### Fixed

- **(B1)** ``_build_client()`` now constructs the schwab-py client with
  ``enforce_enums=False`` (previously ``True``). With ``enforce_enums=True``
  schwab-py rejected every MCP-supplied string literal — ``fields=["positions"]``,
  ``status="FILLED"``, ``types=["TRADE"]`` — with an opaque
  ``expected type "Fields", got type "str"`` ``ValueError``. This made
  ``get_account_positions``, ``get_account_summary``, and
  ``get_accounts(fields=…)`` 100% unusable, and silently broke the
  ``status`` / ``types`` filter parameters of ``get_orders_history`` /
  ``get_transactions``. Pydantic ``Literal[…]`` constraints in
  ``models.py`` already restrict the same vocabulary, so the schwab-py
  layer was duplicating validation. ``auth.py`` (``login_flow`` /
  ``manual_flow``) is updated for consistency.
- **(B3)** Cleaned up the misleading comment in
  ``tools/orders.py`` that claimed ``enforce_enums=False`` applied "at
  higher precedence" — the comment now reflects the real mechanism.

### Added

- **(B2)** New ``get_account_numbers`` MCP tool. Returns the
  ``[{"accountNumber": …, "hashValue": …}]`` mapping required by every
  other tool that takes an ``account_hash`` argument. Without this tool
  the previous v0.1.0 release left users with no in-protocol way to
  translate a plaintext ``accountNumber`` (returned by
  ``get_accounts``) into the encrypted ``hashValue`` that the Schwab
  Trader API uses everywhere else. Read-only, on the existing
  ``_READ_ONLY_METHODS`` whitelist.
- **(B4)** ``get_server_info`` now self-reports the new tool; tool
  count moves from 7 → **8**. Both ``README.md`` and ``README_zh.md``
  updated to advertise the new surface.
- ``tests/test_v0_1_1_patches.py`` — 19 new regression tests covering
  every B1–B4 scenario (build_client kwarg, source-text invariant,
  every previously-broken tool path, get_account_numbers happy path +
  401 / 429 / 5xx / empty / malformed responses, comment hygiene, meta
  / README self-reporting).

### Compatibility

- **No breaking changes.** Pure patch release — the v0.1.0 tool surface
  is preserved as a strict subset of v0.1.1 (every existing tool name,
  argument, and response shape unchanged).
- All 5 read-only boundary layers preserved (whitelist / startup
  warning / read-only tool surface / CI grep gate / mutation-reject
  test); the new ``get_account_numbers`` tool calls the
  already-whitelisted ``schwab.client.Client.get_account_numbers``
  method.
- 178 tests pass (159 from v0.1.0 + 19 v0.1.1 regression tests).
  Coverage 91.98% (vs 91.33% v0.1.0 baseline — does not regress).

### Reported by

- Real-world ``schwab-positions-mcp`` sync-portfolio testing on
  2026-05-29; sibling subagent transcripts ``57240d8f`` (live testing)
  and ``4533edf4`` (engineering report).

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

[Unreleased]: https://github.com/kevinkda/schwab-positions-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.0
