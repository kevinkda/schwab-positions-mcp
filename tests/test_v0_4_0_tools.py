"""Unit tests for the v0.4.0 read-only detail tools.

Covers the three pure read-only tools added in v0.4.0:

* ``get_user_preferences`` — account preferences (no args)
* ``get_order_detail`` — single order by id
* ``get_transaction_detail`` — single transaction by id

Each tool is exercised across normal / boundary / error paths using the same
stubbed ``ReadOnlySchwabClient`` harness as the rest of the suite (the
underlying schwab-py client is a ``MagicMock``; allow-listed read methods get
fake ``httpx.Response``-shaped objects). No real network, no cache writes.

A dedicated boundary block asserts the three tools are **zero-mutation**: they
call only allow-listed read methods, and the underlying mutation methods are
never invoked / never reachable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp.client import _READ_ONLY_METHODS, ReadOnlySchwabClient
from schwab_positions_mcp.tools import order_detail, preferences, transaction_detail

VALID_HASH = "ACCT_HASH_AAAAAAAAAAAA"
VALID_ORDER_ID = 1234567890
VALID_TX_ID = "TX_0001"


def _resp(status: int, payload: Any = None, request_id: str | None = None) -> MagicMock:
    r = MagicMock(name=f"Response[{status}]")
    r.status_code = status
    r.headers = {"Schwab-Client-CorrelId": request_id} if request_id else {}
    r.json.return_value = payload
    r.text = "" if payload is None else str(payload)
    return r


# ---------------------------------------------------------------------------
# preferences.get_user_preferences_impl
# ---------------------------------------------------------------------------


class TestGetUserPreferences:
    def test_returns_preferences(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        payload = {"accounts": [{"accountNumber": "A1", "primaryAccount": True}], "streamerInfo": []}
        mock_schwab_client.get_user_preferences.return_value = _resp(200, payload)
        out = preferences.get_user_preferences_impl()
        assert out["ok"] is True
        assert out["preferences"] == payload
        assert out["_cache_status"] == "skipped:not-cached"

    def test_ignores_payload_argument(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(200, {"accounts": []})
        out = preferences.get_user_preferences_impl({"anything": "ignored"})
        assert out["ok"] is True
        mock_schwab_client.get_user_preferences.assert_called_once_with()

    def test_empty_preferences_edge_case(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(200, {})
        out = preferences.get_user_preferences_impl()
        assert out["ok"] is True
        assert out["preferences"] == {}

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(401, request_id="REQ-401")
        out = preferences.get_user_preferences_impl()
        assert out["ok"] is False
        assert out["error"]["status_code"] == 401
        assert out["error"]["reason"] == "refresh_token_expired"
        assert out["_cache_status"] == "skipped:error"

    def test_handles_403(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(403)
        out = preferences.get_user_preferences_impl()
        assert out["ok"] is False
        assert out["error"]["status_code"] == 403

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(429)
        out = preferences.get_user_preferences_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(503)
        out = preferences.get_user_preferences_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"


# ---------------------------------------------------------------------------
# order_detail.get_order_detail_impl
# ---------------------------------------------------------------------------


class TestGetOrderDetail:
    def test_returns_order(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        order = {"orderId": VALID_ORDER_ID, "status": "FILLED", "orderType": "MARKET"}
        mock_schwab_client.get_order.return_value = _resp(200, order)
        out = order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": VALID_ORDER_ID})
        assert out["ok"] is True
        assert out["order"] == order
        assert out["order_id"] == VALID_ORDER_ID
        assert out["account_hash"] == VALID_HASH
        assert out["_cache_status"] == "skipped:not-cached"

    def test_forwards_positional_args_order_id_first(
        self, installed_client: Any, mock_schwab_client: MagicMock
    ) -> None:
        # schwab-py: get_order(order_id, account_hash) — order_id MUST be first.
        mock_schwab_client.get_order.return_value = _resp(200, {"orderId": VALID_ORDER_ID})
        order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": VALID_ORDER_ID})
        mock_schwab_client.get_order.assert_called_once_with(VALID_ORDER_ID, VALID_HASH)

    def test_min_order_id_boundary(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_order.return_value = _resp(200, {"orderId": 1})
        out = order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": 1})
        assert out["ok"] is True
        assert out["order_id"] == 1

    def test_zero_order_id_rejected(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": 0})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_negative_order_id_rejected(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": -5})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_invalid_hash_rejected(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            order_detail.get_order_detail_impl({"account_hash": "x", "order_id": VALID_ORDER_ID})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_order.return_value = _resp(401)
        out = order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": VALID_ORDER_ID})
        assert out["ok"] is False
        assert out["error"]["reason"] == "refresh_token_expired"
        assert out["_cache_status"] == "skipped:error"

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_order.return_value = _resp(429)
        out = order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": VALID_ORDER_ID})
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_order.return_value = _resp(500)
        out = order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": VALID_ORDER_ID})
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"


# ---------------------------------------------------------------------------
# transaction_detail.get_transaction_detail_impl
# ---------------------------------------------------------------------------


class TestGetTransactionDetail:
    def test_returns_transaction(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        tx = {"transactionId": VALID_TX_ID, "type": "TRADE", "netAmount": -1500.0}
        mock_schwab_client.get_transaction.return_value = _resp(200, tx)
        out = transaction_detail.get_transaction_detail_impl(
            {"account_hash": VALID_HASH, "transaction_id": VALID_TX_ID}
        )
        assert out["ok"] is True
        assert out["transaction"] == tx
        assert out["transaction_id"] == VALID_TX_ID
        assert out["account_hash"] == VALID_HASH
        assert out["_cache_status"] == "skipped:not-cached"

    def test_forwards_positional_args_account_hash_first(
        self, installed_client: Any, mock_schwab_client: MagicMock
    ) -> None:
        # schwab-py: get_transaction(account_hash, transaction_id) — hash MUST be first.
        mock_schwab_client.get_transaction.return_value = _resp(200, {"transactionId": VALID_TX_ID})
        transaction_detail.get_transaction_detail_impl({"account_hash": VALID_HASH, "transaction_id": VALID_TX_ID})
        mock_schwab_client.get_transaction.assert_called_once_with(VALID_HASH, VALID_TX_ID)

    def test_numeric_string_id_boundary(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transaction.return_value = _resp(200, {"transactionId": "123456"})
        out = transaction_detail.get_transaction_detail_impl({"account_hash": VALID_HASH, "transaction_id": "123456"})
        assert out["ok"] is True
        assert out["transaction_id"] == "123456"

    def test_empty_transaction_id_rejected(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            transaction_detail.get_transaction_detail_impl({"account_hash": VALID_HASH, "transaction_id": ""})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_illegal_chars_transaction_id_rejected(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            transaction_detail.get_transaction_detail_impl({"account_hash": VALID_HASH, "transaction_id": "TX/../etc"})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_invalid_hash_rejected(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            transaction_detail.get_transaction_detail_impl({"account_hash": "x", "transaction_id": VALID_TX_ID})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transaction.return_value = _resp(401)
        out = transaction_detail.get_transaction_detail_impl(
            {"account_hash": VALID_HASH, "transaction_id": VALID_TX_ID}
        )
        assert out["ok"] is False
        assert out["error"]["reason"] == "refresh_token_expired"
        assert out["_cache_status"] == "skipped:error"

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transaction.return_value = _resp(429)
        out = transaction_detail.get_transaction_detail_impl(
            {"account_hash": VALID_HASH, "transaction_id": VALID_TX_ID}
        )
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_transaction.return_value = _resp(502)
        out = transaction_detail.get_transaction_detail_impl(
            {"account_hash": VALID_HASH, "transaction_id": VALID_TX_ID}
        )
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"


# ---------------------------------------------------------------------------
# Zero-mutation verification — the three new tools never touch a write path
# ---------------------------------------------------------------------------


class TestV040ToolsAreZeroMutation:
    """The 3 new tools are pure reads: allow-listed methods only, no mutation."""

    _NEW_READ_METHODS = ("get_user_preferences", "get_order", "get_transaction")
    _MUTATIONS = ("place_" + "order", "cancel_" + "order", "replace_" + "order")

    def test_new_methods_are_on_the_allow_list(self) -> None:
        for name in self._NEW_READ_METHODS:
            assert name in _READ_ONLY_METHODS, f"{name!r} must be on the read-only allow list"

    def test_new_methods_are_read_verbs(self) -> None:
        for name in self._NEW_READ_METHODS:
            assert name.startswith("get_"), f"{name!r} is not a read verb"

    def test_tools_only_call_allow_listed_reads(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_user_preferences.return_value = _resp(200, {})
        mock_schwab_client.get_order.return_value = _resp(200, {"orderId": VALID_ORDER_ID})
        mock_schwab_client.get_transaction.return_value = _resp(200, {"transactionId": VALID_TX_ID})

        preferences.get_user_preferences_impl()
        order_detail.get_order_detail_impl({"account_hash": VALID_HASH, "order_id": VALID_ORDER_ID})
        transaction_detail.get_transaction_detail_impl({"account_hash": VALID_HASH, "transaction_id": VALID_TX_ID})

        called = {c[0] for c in mock_schwab_client.method_calls}
        assert called <= set(_READ_ONLY_METHODS), f"a new tool called a non-allow-listed method: {called}"

    def test_new_tool_modules_have_no_mutation_keywords(self) -> None:
        for mod in (preferences, order_detail, transaction_detail):
            src = Path(mod.__file__).read_text(encoding="utf-8")  # type: ignore[arg-type]
            for kw in self._MUTATIONS:
                assert kw not in src, f"{mod.__name__} contains forbidden mutation keyword {kw!r}"

    def test_mutation_still_rejected_after_allow_list_growth(self, readonly_client: ReadOnlySchwabClient) -> None:
        """Growing the allow list with 3 reads must NOT make any mutation reachable."""
        for kw in self._MUTATIONS:
            with pytest.raises(NotImplementedError):
                getattr(readonly_client, kw)
