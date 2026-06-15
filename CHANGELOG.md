# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] - 2026-06-15

### Changed

- ⚠️ **BREAKING: the embedded DuckDB cache is removed and replaced by a
  pluggable cache backend (v0.7 T0).** Storage is selected via
  `SCHWAB_POSITIONS_CACHE_BACKEND`:
  - **memory** (default) — in-process, zero external dependency,
    concurrency-safe, non-blocking. Removes the old single-connection
    DuckDB + global `RLock`, the on-disk `cache.duckdb` file, file locks,
    the corrupt-DB quarantine machinery, and the `cache_events` audit
    table. Keeps **no durable history**, so snapshot writes report
    `snapshot_written:0` (graceful degradation).
  - **clickhouse** (opt-in) — `pip install schwab-positions-mcp[clickhouse]`
    with `SCHWAB_POSITIONS_CLICKHOUSE_URL` and
    `SCHWAB_POSITIONS_CACHE_BACKEND=clickhouse` to durably persist the
    historical position / order / transaction snapshots.
- **Removed the `duckdb` runtime dependency.** ClickHouse is an opt-in
  `[clickhouse]` extra only; the default install ships with **zero new
  dependencies** and works out of the box.
- The snapshot-write public API (`write_positions_snapshot` /
  `write_orders_history` / `write_transactions_history`) is unchanged — all
  tools and the 5-layer read-only boundary are unaffected, and
  `analytics.py` is untouched. The cache layer only ever *writes* derived
  history; it never feeds reads back into a tool, so it cannot widen the
  read-only attack surface.
- 100% line+branch coverage preserved (memory degradation, ClickHouse via a
  mocked client, factory fallback, and backend error paths). Old
  DuckDB-internals tests adapted to the backend model.

## [0.2.1] - 2026-06-15

### Added

- **Three read-only derived analytics tools** (`src/schwab_positions_mcp/tools/analytics.py`),
  growing the tool surface from 8 → 11. All three are pure transforms over the
  existing read-only feeds — **no** new mutation paths, **no** new cache
  writes, and they call only the Layer-1 white-listed read methods
  (`get_account`, `get_account_numbers`, `get_transactions`):
  - **`get_pnl_analysis(account_hash, realized_lookback_days=60)`** — per-position
    cost basis / unrealized P&L / unrealized %, a transaction-derived realized
    P&L over the lookback window, and a portfolio roll-up.
    **Cost-basis method: AVERAGE COST** — Schwab's positions feed exposes only
    the blended `averagePrice` per holding, with no per-lot acquisition records,
    so a true FIFO lot walk is impossible from this feed. Realized P&L is a
    conservative proceeds-based proxy (sum of net cash from closing SELL trades),
    clearly labelled and degrading to `realized_pl_available: false` if the
    transactions endpoint errors so the unrealized block still returns.
  - **`get_concentration_analysis(account_hash, top_n=5)`** — top-N weights,
    Herfindahl-Hirschman Index (HHI) with a normalised interpretation band
    (`diversified` / `moderately_concentrated` / `highly_concentrated`), max
    single-position weight, and asset-type exposure. Sector exposure is `"N/A"`
    (no GICS field in the Schwab positions feed); `assetType` is surfaced as a
    best-effort proxy.
  - **`get_cross_account_summary()`** — discovers every linked account via
    `get_account_numbers`, then fans out `get_account` per account and merges
    positions + balances into a combined view with per-account
    share-of-liquidation-value and symbol-level de-duplication across accounts.
    Handles single / multi / zero-account cases; a per-account fetch error is
    recorded on that account without failing the whole aggregation.
- `tests/test_analytics.py` — 48 new tests covering normal / boundary / error
  paths for all three tools, cost-basis correctness, HHI band edges, the
  single / multi / empty cross-account matrix, and a read-only boundary test
  asserting the analytics layer only ever calls white-listed read methods and
  carries no mutation keywords.

### Changed

- `get_server_info` now reports 11 tools; README / README_zh tool tables and the
  v0.1.1 regression tests updated to the 11-tool surface.

## [0.2.0] - 2026-06-15

### Added

- **Token-health probe CLI** `python -m schwab_positions_mcp.health`
  (`src/schwab_positions_mcp/health.py`). Schwab refresh tokens expire after
  a hard, non-extendable **7 days** (confirmed against schwab-py docs + the
  Schwab Developer Portal: using the refresh token does **not** reset the
  7-day clock, so there is no fully-automatic keep-alive). The probe makes
  that expiry *predictable*: scheduled via cron / launchd / Task Scheduler it
  reads the local token file, computes days-to-expiry (schwab-py
  `token_age()` when creds are present, else file mtime — **offline-safe**,
  never opens a browser or calls Schwab), and on any non-zero exit fires a
  best-effort desktop notification (macOS `osascript` / Linux `notify-send` /
  Windows toast) **and** writes `~/Desktop/SCHWAB_POSITIONS_REAUTH_NEEDED.md`
  with the exact one-command re-auth instructions. Exit codes: `0` healthy /
  `1` <24h / `2` <12h-or-expired / `3` missing / `4` malformed / `5` insecure
  perms. This wires up the previously-dormant `_platform.notify_desktop`
  helper.
- `health_check` MCP tool now also reports `token_age_days` and
  `token_expires_in_days` under `checks` (mtime-based, offline-safe estimate),
  so an agent can surface the 7-day countdown in-protocol. Existing fields are
  unchanged.
- `docs/cron.example` — ready-to-paste launchd / crontab / Task Scheduler
  snippets for the probe (Sunday 20:00 + Wednesday 21:00 + a 4-hour
  laptop-wake fallback).
- `docs/REGISTER.md §4` rewritten to explain the 7-day hard limit, why there
  is no auto-renew, the new health probe, and the one-command re-auth flow.
- `tests/test_health.py` — 38 new tests covering the full exit-code matrix,
  both token-age probe paths (schwab-py primary mocked + mtime fallback),
  redaction, marker-file write, and CLI entry. Plus 4 new `test_meta.py` cases
  for the `token_age_days` / `token_expires_in_days` fields. Coverage held at
  **100.00%** line + branch (`health.py` 171 stmts / 48 branches, 0 miss).

### Security

- The probe never logs tokens: `~/Desktop/SCHWAB_POSITIONS_REAUTH_NEEDED.md`
  redacts any `Bearer` / `access_token` / `refresh_token` material before
  writing. Token file permissions (0o600) are still asserted (exit code 5 on
  drift). No new dependency added; the probe is read-only and offline-safe.

### Compatibility

- **No breaking changes.** Additive only: a new CLI module, two new
  `health_check` `checks` fields, and docs. The 8-tool MCP surface, the
  5-layer read-only boundary, and every response shape are unchanged.

## [0.1.4] - 2026-06-09

### Changed

- ⚠️ **BREAKING (default behavior change): DuckDB cache is now opt-in,
  disabled by default.** `cache_enabled()` previously defaulted to `True`
  (cache on unless `SCHWAB_POSITIONS_CACHE_ENABLED=0`); it now defaults to
  `False`. With no env var set, no DuckDB file is created, no snapshots are
  written, and every tool reports `_cache_status: "skipped:disabled"`.
  **To restore the previous default-on caching, set
  `SCHWAB_POSITIONS_CACHE_ENABLED=true`** (also accepts `1` / `yes` / `on`,
  case-insensitive). Rationale: least-surprise for fresh installs, zero
  on-disk footprint until explicitly requested, and deterministic
  live-API behavior for agents/CI. The tool-return shape is otherwise
  unchanged — only the `_cache_status` default value changes. First repo
  in a 5-MCP cross-repo rollout (template for schwab-marketdata-mcp /
  sec-edgar-mcp / polygon-news-mcp / yfinance-mcp). See
  `stock-personal/docs/sprints/cache-toggle-design.md`.

### Added

- Cache-toggle test coverage: every truthy token (`1/true/yes/on` +
  case + whitespace), falsy/unset/empty paths, `get_cache()` returns
  `None` when disabled or unset, and tool `_cache_status` is
  `skipped:disabled` in default mode vs `*_written:N` when enabled.
  Coverage held at 100% (line + branch).

### Migration

- Operators relying on automatic snapshotting (e.g. "what changed since
  last week" agent queries, or playbooks asserting a cache hit-rate gate)
  must add `SCHWAB_POSITIONS_CACHE_ENABLED=true` to their environment /
  `.env` / host `mcp.json` `envFile`.

## [0.1.3] - 2026-05-31

### Added

- **100% test coverage** (line + branch) enforced via
  ``[tool.coverage.report] fail_under = 100``. Closed the remaining gaps
  in ``_platform.py`` (macOS/Linux desktop-notification dispatch + best-effort
  swallow), ``cache.py`` (DuckDB open-failure → quarantine/retry, DDL-failure
  logging, write-failure error events, ``get_cache`` init-failure degrade),
  ``bootstrap.py`` (missing ``python-dotenv`` branch), ``server.py`` (tool
  wrapper plus ``main`` stdio transport), ``tools/_common.py`` (lazy client
  build and headerless-response normalisation), and ``tools/summary.py``
  (cache-write success path) with ``tests/test_coverage_completion.py``.
- **OWASP Top 10 security suites — three editions:**
  - ``tests/test_owasp_2017.py`` — A1 Injection (SQL/command/path payloads
    rejected by the ``account_hash`` pattern + parameterised DuckDB writes),
    A2 Broken Authentication, A3 Sensitive Data Exposure (``redact()``),
    A5 Broken Access Control (5-layer read-only boundary), A6 Misconfiguration
    (0o600/0o700 perms), A8 Insecure Deserialization, A9 Vulnerable Components
    (bounded deps; Dependabot/pip-audit note), A10 Logging; A4 XXE and A7 XSS
    documented as N/A with source-level drift guards.
  - ``tests/test_owasp_2021.py`` — A01–A10 incl. A04 Insecure Design and
    A10 SSRF (URL/metadata-endpoint payloads cannot be smuggled through
    ``account_hash``; our layer synthesises no outbound URL).
  - ``tests/test_owasp_2025.py`` — Zero-Trust access control, crypto hygiene
    - token rotation signal, **prompt-injection** (tool args/descriptions
    cannot coax the LLM into a write action; injected text is inert data),
    insecure-design, safe env defaults, token lifecycle, data integrity,
    structured logging, and cloud-native SSRF.
- **Penetration-test suite** ``tests/test_pentest.py`` — authentication-bypass,
  privilege-escalation (read-only → write), data-exfiltration, and
  ``ReadOnlySchwabClient.__getattr__`` bypass attempts (dunder / reflection /
  indirect ``getattr`` / ``__dict__`` / ``dir()`` enumeration all denied).
- **Exception-path suite** ``tests/test_exception.py`` — every HTTP-error and
  ``except duckdb.Error`` branch triggered; exception messages verified to
  never leak token / API-key / account material; post-exception cache
  consistency; nested/chained-exception recovery.
- **Boundary suite** ``tests/test_boundary.py`` — ``account_hash`` length
  (8/128 min-max), ``max_results`` (1/3000/0/negative/oversized), ``symbol``
  max-length (32), positions-list size (empty/single/2000), DuckDB numeric
  extremes (1e300 / inf / nan / negative quantity), and date-order edges.

### Changed

- CI coverage gate raised from **85% → 100%** in ``pyproject.toml`` (the
  reusable ``mcp-ci-templates`` workflow enforces each repo's own
  ``fail_under`` via ``uv run pytest --cov``). schwab-positions-mcp is the
  first repo in the ecosystem to enforce a 100% gate.

### Compatibility

- **No breaking changes.** Test/hardening-only release — zero source-behaviour
  changes to the tool surface, white-list, or response shapes.
- All 5 read-only boundary layers preserved and **strengthened** with new
  runtime assertions (no weakening).
- 368 tests pass (192 from v0.1.2 + 176 new) plus 4 live-smoke tests skipped
  by default. Coverage **100.00%** line + branch (vs 92.22% v0.1.2 baseline).

## [0.1.2] - 2026-05-29

### Added

- **(P1)** ``GetOrdersHistoryInput.from_entered_time`` and
  ``GetTransactionsInput.start_date`` now reject inputs older than the
  Schwab Trader API's 60-day lookback window at the Pydantic layer, so
  out-of-window requests fail fast client-side with a self-describing
  ``ValueError`` (cutoff + offending value surfaced verbatim) instead of
  consuming an HTTP round-trip just to receive an opaque server-side
  400. Two new module-level constants ``_ORDERS_LOOKBACK_DAYS`` and
  ``_TRANSACTIONS_LOOKBACK_DAYS`` make the policy easy to tune. The
  validators run after ``_require_tzaware`` (orders side) so the value
  is already tz-aware UTC before lookback comparison.
- **(P2)** ``tools/summary.py`` and ``tools/positions.py`` module
  docstrings now include a "Balances field guide (LLM hint)" section
  documenting Schwab's three balance snapshots (``currentBalances`` /
  ``initialBalances`` / ``projectedBalances``) and steering agents to
  the right field for "what's my buying power" vs "daily P&L baseline"
  vs "post-settlement" questions.
- **(P3)** New ``tests/test_live_smoke.py`` — 4 live-token e2e smoke
  tests (``health_check``, ``get_account_numbers``,
  ``get_account_positions``, lookback boundary). **Skipped by default**;
  enable with ``SCHWAB_POSITIONS_LIVE_E2E=1 uv run pytest
  tests/test_live_smoke.py -v``. Read-only by design — they consume
  rate-limit budget but never mutate state.
- ``tests/test_v0_1_2_lookback_validation.py`` — 14 new regression
  tests covering 30 / 59 / 60 / 61 / 90 / 120 / 180-day boundaries,
  error-message ergonomics (cutoff ISO + offending value present),
  validator ordering invariant (``_require_tzaware`` runs before
  ``_within_orders_lookback``), and double-bad-input behaviour
  (``end < start`` AND ``start`` out-of-window for transactions).

### Compatibility

- **No breaking changes.** Pure patch release — the v0.1.1 tool surface
  is preserved as a strict subset of v0.1.2 (every existing tool name,
  argument, and response shape unchanged). Inputs that were valid under
  v0.1.1 ``from_entered_time`` / ``start_date`` constraints (i.e. within
  60 days) continue to pass; only previously-doomed out-of-window
  requests now fail one round-trip earlier.
- All 5 read-only boundary layers preserved (whitelist / startup
  warning / read-only tool surface / CI grep gate / mutation-reject
  test). No new tool added; no whitelist change; no new dependency.
- 192 tests pass (178 from v0.1.1 + 14 v0.1.2 regression tests) plus
  4 live-smoke tests skipped by default. Coverage **92.22%** (vs
  91.98% v0.1.1 baseline — does not regress).

### Reported by

- Real-world ``schwab-positions-mcp`` sync-portfolio testing on
  2026-05-29 surfaced (a) opaque server-side 400s on out-of-window
  history queries that wasted a round-trip and (b) LLM-agent confusion
  on which balance snapshot to use. P3 live-smoke harness added so
  future sibling sessions can verify auth + 3 read paths against a
  real token without waiting for the next end-to-end portfolio sync.

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

[Unreleased]: https://github.com/kevinkda/schwab-positions-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.2.0
[0.1.4]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.4
[0.1.3]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.3
[0.1.2]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.0
