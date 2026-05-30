"""Exception-path test suite for schwab-positions-mcp.

Goals:
  * Trigger every ``except`` branch in the data path with a realistic cause.
  * Confirm exception messages NEVER carry secrets (token / API key / account).
  * Confirm post-exception state consistency (cache stays usable, no corruption).
  * Exercise nested / chained exceptions and the best-effort swallow contract.

Every test asserts a concrete invariant — no empty-coverage padding.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import duckdb
import pytest

from schwab_positions_mcp import _platform
from schwab_positions_mcp.cache import Cache, _parse_dt, _to_float
from schwab_positions_mcp.tools import _common as tools_common

if TYPE_CHECKING:
    from pathlib import Path

    from schwab_positions_mcp.client import ReadOnlySchwabClient


# ===========================================================================
# normalise_response — every HTTP error branch
# ===========================================================================


class TestNormaliseResponseExceptionBranches:
    @pytest.mark.parametrize(
        ("status", "expected_reason"),
        [
            (401, "refresh_token_expired"),
            (403, "forbidden"),
            (429, "rate_limited"),
            (500, "upstream_error"),
            (502, "upstream_error"),
            (503, "upstream_error"),
            (599, "upstream_error"),
        ],
    )
    def test_each_error_status_raises_expected_reason(self, status: int, expected_reason: str) -> None:
        class _R:
            status_code = status
            headers: dict[str, str] = {}

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_R())
        assert excinfo.value.reason == expected_reason
        assert excinfo.value.status_code == status

    @pytest.mark.parametrize("status", [400, 404, 418, 451])
    def test_unexpected_status_surfaced_verbatim(self, status: int) -> None:
        class _R:
            status_code = status
            headers: dict[str, str] = {}

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_R())
        assert excinfo.value.reason == f"unexpected_{status}"

    def test_json_value_error_falls_back_to_text(self) -> None:
        class _R:
            status_code = 200
            headers: dict[str, str] = {}
            text = "fallback-text"

            @staticmethod
            def json() -> Any:
                raise ValueError("bad json")

        assert tools_common.normalise_response(_R()) == "fallback-text"

    def test_json_attribute_error_falls_back_to_text(self) -> None:
        class _R:
            status_code = 200
            headers: dict[str, str] = {}
            text = "attr-fallback"

            @staticmethod
            def json() -> Any:
                raise AttributeError("no json")

        assert tools_common.normalise_response(_R()) == "attr-fallback"

    def test_already_parsed_payload_passthrough(self) -> None:
        """A dict with no status_code is treated as an already-parsed payload."""
        payload = {"already": "parsed"}
        assert tools_common.normalise_response(payload) is payload


# ===========================================================================
# cache write failures — every except duckdb.Error branch
# ===========================================================================


class TestCacheWriteExceptionBranches:
    def _broken_cache(self, tmp_path: Path) -> Cache:
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        real_conn = cache._conn
        broken = MagicMock(wraps=real_conn)
        broken.executemany.side_effect = duckdb.Error("simulated write failure")
        broken.execute.side_effect = real_conn.execute  # type: ignore[union-attr]
        cache._conn = broken
        return cache

    def test_positions_write_exception_returns_zero(self, tmp_path: Path, mock_positions_data: dict[str, Any]) -> None:
        cache = self._broken_cache(tmp_path)
        try:
            positions = mock_positions_data["securitiesAccount"]["positions"]
            assert cache.write_positions_snapshot("H_ABCDEF", positions) == 0
        finally:
            cache.close()

    def test_orders_write_exception_returns_zero(self, tmp_path: Path, mock_orders_data: list[dict[str, Any]]) -> None:
        cache = self._broken_cache(tmp_path)
        try:
            assert cache.write_orders_history("H_ABCDEF", mock_orders_data) == 0
        finally:
            cache.close()

    def test_transactions_write_exception_returns_zero(
        self, tmp_path: Path, mock_transactions_data: list[dict[str, Any]]
    ) -> None:
        cache = self._broken_cache(tmp_path)
        try:
            assert cache.write_transactions_history("H_ABCDEF", mock_transactions_data) == 0
        finally:
            cache.close()

    def test_ddl_exception_logged_not_raised(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            broken = MagicMock()
            broken.execute.side_effect = duckdb.Error("DDL boom")
            cache._conn = broken
            with caplog.at_level(logging.WARNING):
                cache._init_schema()
            assert any("DDL failed" in r.getMessage() for r in caplog.records)
        finally:
            cache._conn = None

    def test_close_exception_suppressed(self, tmp_path: Path) -> None:
        """A duckdb.Error during close() is suppressed (contextlib.suppress)."""
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        broken = MagicMock()
        broken.close.side_effect = duckdb.Error("close boom")
        cache._conn = broken
        # Must not raise.
        cache.close()
        assert cache._conn is None


# ===========================================================================
# helpers — exception swallow contracts
# ===========================================================================


class TestHelperExceptionSwallow:
    def test_to_float_swallows_type_and_value_errors(self) -> None:
        assert _to_float("not-a-number") is None
        assert _to_float(object()) is None  # TypeError path
        assert _to_float(None) is None

    def test_parse_dt_swallows_bad_input(self) -> None:
        assert _parse_dt("garbage-date") is None
        assert _parse_dt(12345) is None  # not str/datetime → str() then fail → None

    def test_notify_desktop_never_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_platform, "IS_MACOS", True)
        monkeypatch.setattr(_platform, "IS_LINUX", False)
        monkeypatch.setattr(_platform, "_notify_macos", lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")))
        _platform.notify_desktop("t", "m")  # no raise == pass


# ===========================================================================
# Exception messages must NOT leak secrets
# ===========================================================================


class TestExceptionMessagesDoNotLeakSecrets:
    def test_unavailable_message_excludes_secret_values(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """SchwabClientUnavailable text must not echo the actual secret values."""
        monkeypatch.setenv("SCHWAB_API_KEY", "REAL-KEY-DEADBEEF")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "REAL-SECRET-CAFEBABE")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "no-token.json"))
        tools_common.reset_client_singleton()
        with pytest.raises(tools_common.SchwabClientUnavailable) as excinfo:
            tools_common._build_client()
        msg = str(excinfo.value)
        assert "REAL-KEY-DEADBEEF" not in msg
        assert "REAL-SECRET-CAFEBABE" not in msg
        tools_common.reset_client_singleton()

    def test_api_error_str_carries_only_status_and_reason(self) -> None:
        """SchwabApiError's str form is status + reason — no request_id/secret embedded by default."""
        err = tools_common.SchwabApiError(429, "rate_limited", request_id="SECRET-CORREL")
        s = str(err)
        assert "429" in s
        assert "rate_limited" in s
        # The correlation id is an attribute, not embedded in the human string.
        assert "SECRET-CORREL" not in s
        assert err.request_id == "SECRET-CORREL"


# ===========================================================================
# Post-exception state consistency
# ===========================================================================


class TestPostExceptionConsistency:
    def test_cache_recovers_after_write_failure(self, tmp_path: Path, mock_orders_data: list[dict[str, Any]]) -> None:
        """A transient write failure must not corrupt the cache — a later write succeeds."""
        cache = Cache(db_path=tmp_path / "cache.duckdb")
        try:
            real_conn = cache._conn
            broken = MagicMock(wraps=real_conn)
            broken.executemany.side_effect = duckdb.Error("transient")
            broken.execute.side_effect = real_conn.execute  # type: ignore[union-attr]
            cache._conn = broken
            assert cache.write_orders_history("H_ABCDEF", mock_orders_data) == 0
            # Restore connection — DB file is intact, schema present, writes work.
            cache._conn = real_conn
            assert cache.write_orders_history("H_ABCDEF", mock_orders_data) == len(mock_orders_data)
            count = real_conn.execute(  # type: ignore[union-attr]
                "SELECT COUNT(*) FROM orders_history"
            ).fetchone()
            assert count is not None and count[0] == len(mock_orders_data)
        finally:
            cache.close()

    def test_tool_returns_structured_error_not_exception(
        self,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        make_response: Any,
    ) -> None:
        """An upstream error inside a tool is caught and returned as a structured dict, not raised."""
        from schwab_positions_mcp.tools import accounts

        mock_schwab_client.get_accounts.return_value = make_response(status_code=503)
        result = accounts.get_accounts_impl({})
        assert result["ok"] is False
        assert result["error"]["status_code"] == 503
        assert result["error"]["reason"] == "upstream_error"


# ===========================================================================
# Nested / chained exceptions
# ===========================================================================


class TestNestedExceptions:
    def test_quarantine_chain_recovers(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Open fails (1st exc) → quarantine → retry connect succeeds (recovery from chained failure)."""
        db = tmp_path / "cache.duckdb"
        db.write_text("corrupt")
        real_connect = duckdb.connect
        calls = {"n": 0}

        def flaky(path: str, *a: Any, **k: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise duckdb.IOException("corrupt on first open")
            return real_connect(path, *a, **k)

        monkeypatch.setattr(duckdb, "connect", flaky)
        cache = Cache(db_path=db)
        try:
            assert cache._conn is not None
            assert calls["n"] == 2
            # The original corrupt file was quarantined.
            assert list(tmp_path.glob("cache.duckdb.corrupt-*"))
        finally:
            cache.close()

    def test_get_cache_init_failure_degrades_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If Cache() construction raises, get_cache returns None (graceful nested-failure handling)."""
        import schwab_positions_mcp.cache as cache_module

        cache_module.reset_cache_singleton()
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "1")
        monkeypatch.setattr(cache_module, "Cache", lambda *_a, **_k: (_ for _ in ()).throw(duckdb.Error("init boom")))
        assert cache_module.get_cache() is None
        cache_module.reset_cache_singleton()
