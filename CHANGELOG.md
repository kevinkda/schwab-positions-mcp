# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/kevinkda/schwab-positions-mcp/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.3
[0.1.2]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.1
[0.1.0]: https://github.com/kevinkda/schwab-positions-mcp/releases/tag/v0.1.0
