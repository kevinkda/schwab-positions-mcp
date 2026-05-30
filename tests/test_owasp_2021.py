"""OWASP Top 10 — 2021 security test suite for schwab-positions-mcp.

The 2021 edition reorganises the 2017 list and adds A04 Insecure Design and
A10 SSRF. This file maps each applicable 2021 category to a concrete invariant
on the read-only MCP attack surface.

Highlights specific to 2021:
  * A01 Broken Access Control — the full 5-layer read-only boundary.
  * A04 Insecure Design — read-only-by-design, enforced structurally.
  * A08 Software & Data Integrity — response shape validation.
  * A10 SSRF — account_hash / symbol cannot redirect the outbound request.

Every test asserts a concrete invariant — no empty-coverage padding.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp.cache import Cache
from schwab_positions_mcp.client import _READ_ONLY_METHODS, ReadOnlySchwabClient
from schwab_positions_mcp.tools import _common as tools_common

if TYPE_CHECKING:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "schwab_positions_mcp"


# ===========================================================================
# A01:2021 — Broken Access Control  (5-layer read-only boundary, full sweep)
# ===========================================================================


class TestA01BrokenAccessControl:
    _MUTATIONS = ["place_" + "order", "cancel_" + "order", "replace_" + "order"]

    def test_layer1_whitelist_is_read_only(self) -> None:
        """Layer 1: the method white-list contains only read verbs."""
        assert all(m.startswith("get_") for m in _READ_ONLY_METHODS)
        assert "get_account" in _READ_ONLY_METHODS

    def test_layer1_runtime_rejects_mutations(self, readonly_client: ReadOnlySchwabClient) -> None:
        """Layer 1 (runtime): mutations raise NotImplementedError via __getattr__."""
        for m in self._MUTATIONS:
            with pytest.raises(NotImplementedError):
                getattr(readonly_client, m)

    def test_layer2_startup_warning_present(self, caplog: pytest.LogCaptureFixture) -> None:
        """Layer 2: importing the server emits the READ-ONLY startup warning."""
        import importlib

        import schwab_positions_mcp.server as srv

        with caplog.at_level(logging.WARNING, logger=srv.__name__):
            importlib.reload(srv)
        joined = " ".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
        assert "READ-ONLY MODE" in joined

    def test_layer3_tool_surface_has_no_mutation_tool(self) -> None:
        """Layer 3: the registered tool surface contains no mutation tool."""
        from schwab_positions_mcp.tools import meta

        tools = meta.get_server_info_impl()["tools"]
        for t in tools:
            assert "place" not in t and "cancel" not in t and "replace" not in t
            assert "order" not in t or t in {"get_orders_history"}

    def test_layer4_src_has_no_mutation_keywords(self) -> None:
        """Layer 4: no src file references the literal mutation API keywords."""
        import re

        pattern = re.compile(
            r"\b(?:" + "|".join(["place_" + "order", "cancel_" + "order", "replace_" + "order"]) + r")\b"
        )
        offenders = [
            str(py.relative_to(REPO_ROOT)) for py in SRC_ROOT.rglob("*.py") if pattern.search(py.read_text("utf-8"))
        ]
        assert offenders == [], f"Layer 4 regression — mutation keywords in: {offenders}"

    def test_layer5_setattr_blocked(self, readonly_client: ReadOnlySchwabClient) -> None:
        """Layer 5: the wrapper is immutable — monkeypatching the wrapper is blocked."""
        with pytest.raises(AttributeError):
            readonly_client.injected = "x"  # type: ignore[attr-defined]


# ===========================================================================
# A02:2021 — Cryptographic Failures
# ===========================================================================


class TestA02CryptographicFailures:
    def test_token_path_resolves_under_user_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default token path is under the user's private ~/.config tree (not world-readable)."""
        monkeypatch.delenv("SCHWAB_POSITIONS_TOKEN_PATH", raising=False)
        path = tools_common._token_path()
        assert ".config" in str(path)
        assert path.name == "token.json"

    def test_cache_db_not_world_readable_on_posix(self, tmp_path: Path) -> None:
        """Secret-adjacent DuckDB file must be 0o600 (owner-only) on POSIX."""
        import os
        import stat
        import sys

        if sys.platform == "win32":
            pytest.skip("POSIX-only perm semantics")
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            mode = stat.S_IMODE(os.stat(tmp_path / "cache.duckdb").st_mode)
            assert mode == 0o600
            # No group/other read bits.
            assert not (mode & stat.S_IRGRP)
            assert not (mode & stat.S_IROTH)
        finally:
            cache.close()

    def test_secret_never_appears_in_correlation_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """Correlation ids in logs are redacted — no plaintext secret-like material logged."""

        class _R:
            status_code = 401
            headers = {"Schwab-Client-CorrelId": "PLAINTEXT-SECRET-VALUE"}

        with caplog.at_level(logging.WARNING, logger="schwab_positions_mcp.tools._common"):
            with pytest.raises(tools_common.SchwabApiError):
                tools_common.normalise_response(_R())
        assert "PLAINTEXT-SECRET-VALUE" not in " ".join(r.getMessage() for r in caplog.records)


# ===========================================================================
# A03:2021 — Injection
# ===========================================================================


class TestA03Injection:
    def test_parameterised_query_blocks_sql_injection(self, tmp_path: Path) -> None:
        """DuckDB writes use bound parameters — a SQL payload cannot alter the schema."""
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            payload = "x'); DROP TABLE orders_history;--"
            cache.write_orders_history(
                "HASH_ABCDEF",
                [{"orderId": payload, "orderLegCollection": [{"instrument": {"symbol": payload}}]}],
            )
            # Table survived; payload stored as inert data.
            row = cache._conn.execute(  # type: ignore[union-attr]
                "SELECT order_id FROM orders_history WHERE account_hash = ?", ["HASH_ABCDEF"]
            ).fetchone()
            assert row is not None and row[0] == payload
        finally:
            cache.close()

    def test_status_filter_constrained_by_literal_enum(self) -> None:
        """Order status is a Literal enum — arbitrary injected statuses are rejected."""
        from pydantic import ValidationError

        from schwab_positions_mcp.models import GetOrdersHistoryInput

        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate(
                {
                    "account_hash": "VALIDHASH123",
                    "from_entered_time": _recent_iso(),
                    "to_entered_time": _now_iso(),
                    "status": "'; DROP TABLE orders_history;--",
                }
            )

    def test_transaction_type_constrained_by_literal_enum(self) -> None:
        """Transaction types are Literal-constrained — injected types are rejected."""
        from pydantic import ValidationError

        from schwab_positions_mcp.models import GetTransactionsInput

        with pytest.raises(ValidationError):
            GetTransactionsInput.model_validate(
                {
                    "account_hash": "VALIDHASH123",
                    "start_date": _recent_date(),
                    "end_date": _today_date(),
                    "types": ["TRADE", "$(reboot)"],
                }
            )


# ===========================================================================
# A04:2021 — Insecure Design
# ===========================================================================


class TestA04InsecureDesign:
    def test_read_only_by_design_no_write_methods_exposed(self) -> None:
        """By design, the wrapper exposes zero write methods — read-only is structural, not optional."""
        assert all(name.startswith("get_") for name in _READ_ONLY_METHODS)

    def test_wrapper_default_denies_unknown_attributes(self, readonly_client: ReadOnlySchwabClient) -> None:
        """Default-deny: anything not explicitly white-listed is rejected."""
        with pytest.raises(NotImplementedError):
            _ = readonly_client.some_future_unknown_method

    def test_security_doc_documents_5_layer_design(self) -> None:
        """The design is documented — docs/SECURITY.md must describe the layered boundary."""
        body = (REPO_ROOT / "docs" / "SECURITY.md").read_text("utf-8")
        assert "Layer 1" in body or "5-layer" in body or "5 layers" in body


# ===========================================================================
# A05:2021 — Security Misconfiguration
# ===========================================================================


class TestA05SecurityMisconfiguration:
    def test_startup_warning_is_emitted(self, caplog: pytest.LogCaptureFixture) -> None:
        """A misconfigured deploy must still be loudly flagged as read-only on startup."""
        import importlib

        import schwab_positions_mcp.server as srv

        with caplog.at_level(logging.WARNING, logger=srv.__name__):
            importlib.reload(srv)
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "No trade endpoints exposed" in joined

    def test_cache_enabled_default_is_explicit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cache defaults are explicit, not surprising: enabled-by-default, bypass-off-by-default."""
        from schwab_positions_mcp.cache import cache_bypass, cache_enabled

        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_ENABLED", raising=False)
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_BYPASS", raising=False)
        assert cache_enabled() is True
        assert cache_bypass() is False


# ===========================================================================
# A07:2021 — Identification and Authentication Failures
# ===========================================================================


class TestA07AuthenticationFailures:
    def test_health_check_reports_token_absent_without_contacting_schwab(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """health_check reflects auth state from disk only — never silently 'ready' without a token."""
        from schwab_positions_mcp.tools import meta

        monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
        monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "no-token.json"))
        result = meta.health_check_impl()
        assert result["status"] == "needs_env_setup"
        assert result["checks"]["token_present"] is False

    def test_health_check_needs_oauth_when_creds_present_token_absent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from schwab_positions_mcp.tools import meta

        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "no-token.json"))
        result = meta.health_check_impl()
        assert result["status"] == "needs_oauth_login"

    def test_expired_token_401_normalises_to_actionable_reason(self) -> None:
        class _R:
            status_code = 401
            headers: dict[str, str] = {}

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_R())
        assert excinfo.value.reason == "refresh_token_expired"


# ===========================================================================
# A08:2021 — Software and Data Integrity Failures
# ===========================================================================


class TestA08DataIntegrity:
    def test_unexpected_payload_type_does_not_corrupt_output(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """A tampered/mismatched response shape must not flow into the tool result unchecked."""
        from schwab_positions_mcp.tools import transactions

        mock_schwab_client.get_transactions.return_value = make_response(json_payload="not-a-list")
        result = transactions.get_transactions_impl(
            {
                "account_hash": "VALIDHASH123",
                "start_date": _recent_date(),
                "end_date": _today_date(),
            }
        )
        assert result["transactions"] == []
        assert result["count"] == 0

    def test_cache_roundtrip_preserves_values(self, tmp_path: Path, mock_positions_data: dict[str, Any]) -> None:
        """Data written to cache must read back identically (integrity of persisted snapshots)."""
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            positions = mock_positions_data["securitiesAccount"]["positions"]
            cache.write_positions_snapshot("HASH_ABCDEF", positions)
            rows = cache._conn.execute(  # type: ignore[union-attr]
                "SELECT symbol, market_value FROM positions_snapshots ORDER BY symbol"
            ).fetchall()
            symbols = {r[0] for r in rows}
            assert symbols == {"AAPL", "MSFT"}
            mv = {r[0]: r[1] for r in rows}
            assert mv["AAPL"] == pytest.approx(1700.0)
        finally:
            cache.close()


# ===========================================================================
# A09:2021 — Security Logging and Monitoring Failures
# ===========================================================================


class TestA09LoggingMonitoring:
    def test_insert_logged_as_audit_event(self, tmp_path: Path, mock_orders_data: list[dict[str, Any]]) -> None:
        """Successful writes are auditable via the cache_events INSERT log."""
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            cache.write_orders_history("HASH_ABCDEF", mock_orders_data)
            rows = cache._conn.execute(  # type: ignore[union-attr]
                "SELECT kind, row_count FROM cache_events WHERE kind='INSERT'"
            ).fetchall()
            assert rows and rows[0][1] == len(mock_orders_data)
        finally:
            cache.close()

    def test_rate_limit_429_surfaces_for_monitoring(self) -> None:
        class _R:
            status_code = 429
            headers: dict[str, str] = {}

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_R())
        assert excinfo.value.reason == "rate_limited"


# ===========================================================================
# A10:2021 — Server-Side Request Forgery (SSRF)
# ===========================================================================


class TestA10SSRF:
    def test_account_hash_cannot_inject_arbitrary_url(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """A URL-shaped account_hash is rejected by the schema, so it can't redirect the request.

        The account_hash pattern forbids ``:`` ``/`` ``.`` — an attacker cannot
        smuggle ``http://169.254.169.254/latest/meta-data`` into the path.
        """
        from pydantic import ValidationError

        from schwab_positions_mcp.models import GetAccountPositionsInput

        ssrf_payloads = [
            "http://169.254.169.254/latest/meta-data/",
            "https://evil.example/steal",
            "file:///etc/passwd",
            "//evil.example/x",
            "localhost:8080",
            "127.0.0.1",
        ]
        for payload in ssrf_payloads:
            with pytest.raises(ValidationError):
                GetAccountPositionsInput.model_validate({"account_hash": payload})

    def test_valid_hash_only_used_as_opaque_arg_to_schwab_py(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """Our code never builds an outbound URL — it hands the hash to schwab-py verbatim.

        schwab-py owns the base URL (api.schwabapi.com); the hash is only a
        path segment it controls. We assert our layer does not synthesise any
        URL and forwards the hash as the first positional arg.
        """
        from schwab_positions_mcp.tools import positions

        mock_schwab_client.get_account.return_value = make_response(json_payload={"securitiesAccount": {}})
        positions.get_account_positions_impl({"account_hash": "OPAQUEHASH99"})

        passed_hash = mock_schwab_client.get_account.call_args.args[0]
        assert passed_hash == "OPAQUEHASH99"
        # Confirm no URL/scheme ever materialised in the call args/kwargs.
        flat = repr(mock_schwab_client.get_account.call_args)
        assert "http://" not in flat and "https://" not in flat

    def test_symbol_filter_does_not_control_request_host(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """The optional symbol filter is forwarded as a query param to schwab-py, not a host."""
        from schwab_positions_mcp.tools import transactions

        mock_schwab_client.get_transactions.return_value = make_response(json_payload=[])
        transactions.get_transactions_impl(
            {
                "account_hash": "VALIDHASH123",
                "start_date": _recent_date(),
                "end_date": _today_date(),
                "symbol": "AAPL",
            }
        )
        kwargs = mock_schwab_client.get_transactions.call_args.kwargs
        assert kwargs.get("symbol") == "AAPL"
        assert "http" not in repr(kwargs)


# ===========================================================================
# Helpers
# ===========================================================================


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _recent_iso() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


def _today_date() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def _recent_date() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
