"""Unit tests for the 5 business tools.

These tests use a stubbed ``ReadOnlySchwabClient`` (the underlying schwab-py
``Client`` is a ``MagicMock``) and inject a fake ``httpx.Response``-shaped
object on the white-listed read methods. We deliberately avoid real network
calls — ``respx`` would only help if schwab-py actually used ``httpx`` calls
inside our test harness, which it does not when we replace the whole client.

Each tool has at least 5 paths covered:
    * normal payload
    * 401 (refresh-token expired)
    * 429 (rate-limited)
    * 5xx (upstream error)
    * 400-class / boundary edge case (varies per tool)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp.tools import (
    accounts,
    orders,
    positions,
    summary,
    transactions,
)

VALID_HASH = "ACCT_HASH_AAAAAAAAAAAA"


def _resp(status: int, payload: Any = None, request_id: str | None = None) -> MagicMock:
    r = MagicMock(name=f"Response[{status}]")
    r.status_code = status
    r.headers = {"Schwab-Client-CorrelId": request_id} if request_id else {}
    r.json.return_value = payload
    r.text = "" if payload is None else str(payload)
    return r


# ---------------------------------------------------------------------------
# accounts.get_accounts_impl
# ---------------------------------------------------------------------------


class TestGetAccounts:
    def test_returns_list(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_account_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(200, mock_account_data)
        out = accounts.get_accounts_impl({})
        assert out["ok"] is True
        assert out["count"] == 2
        assert out["accounts"][0]["securitiesAccount"]["accountNumber"] == "ACCT_HASH_AAA"

    def test_passes_fields_kwarg(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_account_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(200, mock_account_data)
        accounts.get_accounts_impl({"fields": ["positions"]})
        mock_schwab_client.get_accounts.assert_called_once_with(fields=["positions"])

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(401, request_id="REQ-401")
        out = accounts.get_accounts_impl({})
        assert out["ok"] is False
        assert out["error"]["status_code"] == 401
        assert out["error"]["reason"] == "refresh_token_expired"

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(429)
        out = accounts.get_accounts_impl({})
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(503)
        out = accounts.get_accounts_impl({})
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_empty_list_edge_case(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(200, [])
        out = accounts.get_accounts_impl({})
        assert out["ok"] is True
        assert out["count"] == 0

    def test_handles_403(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(403)
        out = accounts.get_accounts_impl({})
        assert out["ok"] is False
        assert out["error"]["status_code"] == 403


# ---------------------------------------------------------------------------
# positions.get_account_positions_impl
# ---------------------------------------------------------------------------


class TestGetAccountPositions:
    def test_returns_positions(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["count"] == 2
        assert out["positions"][0]["instrument"]["symbol"] == "AAPL"

    def test_writes_cache_when_enabled(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
        tmp_cache: Any,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["_cache_status"].startswith("snapshot_written:")
        assert out["_cache_status"] == "snapshot_written:2"

    def test_skips_cache_when_disabled(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
    ) -> None:
        # Default mode (autouse conftest pins CACHE_ENABLED=0): cache is a
        # no-op and the tool reports the disabled status — response shape
        # otherwise unchanged.
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["count"] == 2
        assert out["_cache_status"] == "skipped:disabled"

    def test_invalid_hash_raises_validation(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            positions.get_account_positions_impl({"account_hash": "x"})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(401)
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(429)
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(500)
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_no_positions_edge_case(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(
            200, {"securitiesAccount": {"accountNumber": "X", "positions": []}}
        )
        out = positions.get_account_positions_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["count"] == 0


# ---------------------------------------------------------------------------
# orders.get_orders_history_impl
# ---------------------------------------------------------------------------


class TestGetOrdersHistory:
    def _payload(self, **kw: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "account_hash": VALID_HASH,
            "from_entered_time": datetime(2026, 5, 1, tzinfo=UTC),
            "to_entered_time": datetime(2026, 5, 28, tzinfo=UTC),
        }
        base.update(kw)
        return base

    def test_returns_orders(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_orders_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, mock_orders_data)
        out = orders.get_orders_history_impl(self._payload())
        assert out["ok"] is True
        assert out["count"] == 2

    def test_filters_by_status(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_orders_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, mock_orders_data)
        orders.get_orders_history_impl(self._payload(status="FILLED"))
        kwargs = mock_schwab_client.get_orders_for_account.call_args.kwargs
        assert kwargs["status"] == "FILLED"

    def test_pagination_max_results(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_orders_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, mock_orders_data)
        orders.get_orders_history_impl(self._payload(max_results=100))
        kwargs = mock_schwab_client.get_orders_for_account.call_args.kwargs
        assert kwargs["max_results"] == 100

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(429)
        out = orders.get_orders_history_impl(self._payload())
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(401)
        out = orders.get_orders_history_impl(self._payload())
        assert out["ok"] is False

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(502)
        out = orders.get_orders_history_impl(self._payload())
        assert out["ok"] is False

    def test_empty_results(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, [])
        out = orders.get_orders_history_impl(self._payload())
        assert out["ok"] is True
        assert out["count"] == 0

    def test_writes_cache(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_orders_data: list[dict[str, Any]],
        tmp_cache: Any,
    ) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, mock_orders_data)
        out = orders.get_orders_history_impl(self._payload())
        assert out["_cache_status"] == "history_written:2"


# ---------------------------------------------------------------------------
# transactions.get_transactions_impl
# ---------------------------------------------------------------------------


class TestGetTransactions:
    def _payload(self, **kw: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "account_hash": VALID_HASH,
            "start_date": date(2026, 5, 1),
            "end_date": date(2026, 5, 28),
        }
        base.update(kw)
        return base

    def test_returns_transactions(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_transactions_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(200, mock_transactions_data)
        out = transactions.get_transactions_impl(self._payload())
        assert out["ok"] is True
        assert out["count"] == 2

    def test_filters_by_type(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_transactions_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(200, mock_transactions_data)
        transactions.get_transactions_impl(self._payload(types=["TRADE"]))
        kwargs = mock_schwab_client.get_transactions.call_args.kwargs
        assert kwargs["transaction_types"] == ["TRADE"]

    def test_filters_by_symbol(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_transactions_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(200, mock_transactions_data)
        transactions.get_transactions_impl(self._payload(symbol="AAPL"))
        kwargs = mock_schwab_client.get_transactions.call_args.kwargs
        assert kwargs["symbol"] == "AAPL"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(500)
        out = transactions.get_transactions_impl(self._payload())
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(401)
        out = transactions.get_transactions_impl(self._payload())
        assert out["ok"] is False

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(429)
        out = transactions.get_transactions_impl(self._payload())
        assert out["ok"] is False

    def test_writes_cache(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_transactions_data: list[dict[str, Any]],
        tmp_cache: Any,
    ) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(200, mock_transactions_data)
        out = transactions.get_transactions_impl(self._payload())
        assert out["_cache_status"] == "history_written:2"


# ---------------------------------------------------------------------------
# summary.get_account_summary_impl
# ---------------------------------------------------------------------------


class TestGetAccountSummary:
    def test_aggregates_correctly(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        s = out["summary"]
        assert s["position_count"] == 2
        assert s["total_market_value"] == pytest.approx(3300.0)
        assert s["total_unrealized_pl"] == pytest.approx(300.0)
        assert s["cash_balance"] == 1000.0
        assert s["currency"] == "USD"

    def test_handles_no_positions(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(
            200,
            {
                "securitiesAccount": {
                    "accountNumber": "X",
                    "positions": [],
                    "currentBalances": {"cashBalance": 0.0, "currency": "USD"},
                }
            },
        )
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["summary"]["position_count"] == 0
        assert out["summary"]["total_market_value"] == 0.0

    def test_handles_401(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(401)
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False

    def test_handles_429(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(429)
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False

    def test_handles_5xx(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(503)
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False

    def test_safe_float_handles_garbage(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(
            200,
            {
                "securitiesAccount": {
                    "accountNumber": "X",
                    "positions": [
                        {
                            "instrument": {"symbol": "X", "assetType": "EQUITY"},
                            "marketValue": "not-a-number",
                            "longOpenProfitLoss": None,
                        }
                    ],
                    "currentBalances": {"currency": "USD"},
                }
            },
        )
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["summary"]["total_market_value"] == 0.0
        assert out["summary"]["total_unrealized_pl"] == 0.0


# ---------------------------------------------------------------------------
# normalise_response — covers the response-handling edge cases
# ---------------------------------------------------------------------------


class TestNormaliseResponseCoverage:
    """Direct tests on _common.normalise_response for the rarer branches."""

    def test_already_parsed_payload_passes_through(self) -> None:
        from schwab_positions_mcp.tools._common import normalise_response

        out = normalise_response({"already": "parsed"})
        assert out == {"already": "parsed"}

    def test_invalid_json_falls_back_to_text(self) -> None:
        from schwab_positions_mcp.tools._common import normalise_response

        r = MagicMock()
        r.status_code = 200
        r.headers = {}
        r.json.side_effect = ValueError("nope")
        r.text = "raw-body"
        assert normalise_response(r) == "raw-body"

    def test_unexpected_status_code(self) -> None:
        from schwab_positions_mcp.tools._common import SchwabApiError, normalise_response

        with pytest.raises(SchwabApiError) as excinfo:
            normalise_response(_resp(418))
        assert excinfo.value.status_code == 418
        assert "unexpected_418" in excinfo.value.reason


# ---------------------------------------------------------------------------
# tools._common helpers
# ---------------------------------------------------------------------------


class TestCommonHelpers:
    def test_redact_short(self) -> None:
        from schwab_positions_mcp.tools._common import _redact

        assert _redact("ab") == "****"
        assert _redact("") == ""

    def test_redact_long(self) -> None:
        from schwab_positions_mcp.tools._common import _redact

        assert _redact("ABCDEFGH").startswith("AB")
        assert _redact("ABCDEFGH").endswith("GH")

    def test_token_path_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from schwab_positions_mcp.tools._common import _token_path

        monkeypatch.delenv("SCHWAB_POSITIONS_TOKEN_PATH", raising=False)
        p = _token_path()
        assert p.name == "token.json"
        assert "schwab-positions-mcp" in str(p)

    def test_build_client_missing_creds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from schwab_positions_mcp.tools._common import (
            SchwabClientUnavailable,
            _build_client,
        )

        monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
        monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
        with pytest.raises(SchwabClientUnavailable):
            _build_client()

    def test_build_client_missing_token(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        from schwab_positions_mcp.tools._common import (
            SchwabClientUnavailable,
            _build_client,
        )

        monkeypatch.setenv("SCHWAB_API_KEY", "fake")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "fake")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "missing.json"))
        with pytest.raises(SchwabClientUnavailable):
            _build_client()

    def test_server_version_returns_dunder(self) -> None:
        from schwab_positions_mcp import __version__
        from schwab_positions_mcp.tools._common import server_version

        assert server_version() == __version__
