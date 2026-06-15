"""Shared fixtures for the schwab-positions-mcp test suite.

The fixtures here keep tests hermetic:

* ``mock_schwab_client`` — a ``MagicMock`` standing in for
  ``schwab.client.Client``. Tests wrap it in
  :class:`schwab_positions_mcp.client.ReadOnlySchwabClient` and inject it
  into the tools layer via the ``installed_client`` fixture.
* ``tmp_cache`` — a :class:`schwab_positions_mcp.cache.Cache` rooted at
  ``tmp_path`` so tests never touch ``$XDG_STATE_HOME``.
* ``mock_account_data`` / ``mock_positions_data`` / ``mock_orders_data`` /
  ``mock_transactions_data`` — small but realistic Schwab payload fixtures
  used by the tool-layer tests.

No real network or credential is ever read. The ``_no_real_creds``
autouse fixture scrubs ``SCHWAB_*`` from ``os.environ`` so tests never
accidentally hit Schwab.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp import cache as cache_module
from schwab_positions_mcp.cache import Cache
from schwab_positions_mcp.cache_backend import ClickHouseBackend
from schwab_positions_mcp.client import ReadOnlySchwabClient
from schwab_positions_mcp.tools import _common as tools_common


@pytest.fixture(autouse=True)
def _no_real_creds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip real Schwab creds from the env so tests stay hermetic.

    Also points the token path override at ``tmp_path`` so health checks
    and ``_token_path()`` resolve to a sandbox.
    """
    for var in (
        "SCHWAB_API_KEY",
        "SCHWAB_APP_SECRET",
        "SCHWAB_CALLBACK_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(
        "SCHWAB_POSITIONS_TOKEN_PATH",
        str(tmp_path / "token.json"),
    )
    monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "0")


@pytest.fixture
def mock_schwab_client() -> MagicMock:
    """A bare ``MagicMock`` configured with the read-only schwab-py methods."""
    mock = MagicMock(name="schwab.client.Client")
    return mock


@pytest.fixture
def readonly_client(mock_schwab_client: MagicMock) -> ReadOnlySchwabClient:
    """Wrap the mock schwab-py client in :class:`ReadOnlySchwabClient`."""
    return ReadOnlySchwabClient(mock_schwab_client)


@pytest.fixture
def installed_client(
    readonly_client: ReadOnlySchwabClient,
) -> Iterator[ReadOnlySchwabClient]:
    """Inject ``readonly_client`` as the process-wide client singleton.

    Restores the previous singleton on teardown so tests can still
    monkeypatch it independently if they want.
    """
    tools_common.reset_client_singleton()
    tools_common._CLIENT_SINGLETON = readonly_client
    try:
        yield readonly_client
    finally:
        tools_common.reset_client_singleton()


@pytest.fixture
def tmp_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Cache]:
    """A :class:`Cache` installed as the singleton, backed by a ClickHouse-mocked
    client so derived-history writes durably persist (``*_written:N``).

    Uses a mock client (no live ClickHouse) per the test contract — every
    ``insert`` succeeds, so snapshot/history writes report the full count.
    """
    del tmp_path
    monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "1")
    cache_module.reset_cache_singleton()
    client = MagicMock()
    client.command.return_value = None
    client.insert.return_value = None
    result = MagicMock()
    result.result_rows = []
    client.query.return_value = result
    cache = Cache(backend=ClickHouseBackend(url="clickhouse://x", client=client))
    cache_module._cache_singleton = cache
    try:
        yield cache
    finally:
        cache_module.reset_cache_singleton()


@pytest.fixture
def mock_account_data() -> list[dict[str, Any]]:
    """Two-account ``GET /accounts`` payload."""
    return [
        {
            "securitiesAccount": {
                "accountNumber": "ACCT_HASH_AAA",
                "type": "MARGIN",
                "currentBalances": {
                    "cashBalance": 1000.0,
                    "buyingPower": 5000.0,
                    "liquidationValue": 12000.0,
                    "currency": "USD",
                },
            }
        },
        {
            "securitiesAccount": {
                "accountNumber": "ACCT_HASH_BBB",
                "type": "CASH",
                "currentBalances": {
                    "cashBalance": 250.0,
                    "buyingPower": 250.0,
                    "liquidationValue": 250.0,
                    "currency": "USD",
                },
            }
        },
    ]


@pytest.fixture
def mock_positions_data() -> dict[str, Any]:
    """``GET /accounts/{hash}?fields=positions`` payload."""
    return {
        "securitiesAccount": {
            "accountNumber": "ACCT_HASH_AAA",
            "currentBalances": {
                "cashBalance": 1000.0,
                "buyingPower": 5000.0,
                "liquidationValue": 12000.0,
                "currency": "USD",
            },
            "initialBalances": {
                "cashBalance": 1000.0,
            },
            "positions": [
                {
                    "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                    "longQuantity": 10.0,
                    "averagePrice": 150.0,
                    "marketValue": 1700.0,
                    "longOpenProfitLoss": 200.0,
                    "currency": "USD",
                },
                {
                    "instrument": {"symbol": "MSFT", "assetType": "EQUITY"},
                    "longQuantity": 5.0,
                    "averagePrice": 300.0,
                    "marketValue": 1600.0,
                    "longOpenProfitLoss": 100.0,
                    "currency": "USD",
                },
            ],
        }
    }


@pytest.fixture
def mock_orders_data() -> list[dict[str, Any]]:
    """Sample list of two orders."""
    return [
        {
            "orderId": 11111111,
            "status": "FILLED",
            "enteredTime": "2026-05-01T13:30:00+0000",
            "closeTime": "2026-05-01T13:30:05+0000",
            "orderType": "MARKET",
            "filledQuantity": 10,
            "price": 150.0,
            "orderLegCollection": [
                {
                    "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                    "quantity": 10,
                }
            ],
        },
        {
            "orderId": 22222222,
            "status": "WORKING",
            "enteredTime": "2026-05-15T14:00:00+0000",
            "orderType": "LIMIT",
            "filledQuantity": 0,
            "price": 295.0,
            "orderLegCollection": [
                {
                    "instrument": {"symbol": "MSFT", "assetType": "EQUITY"},
                    "quantity": 5,
                }
            ],
        },
    ]


@pytest.fixture
def mock_transactions_data() -> list[dict[str, Any]]:
    """Sample list of two transactions."""
    return [
        {
            "transactionId": "TX_001",
            "tradeDate": "2026-05-01T13:30:00+0000",
            "type": "TRADE",
            "instrument": {"symbol": "AAPL"},
            "amount": 1500.0,
            "netAmount": -1500.0,
            "currency": "USD",
        },
        {
            "transactionId": "TX_002",
            "tradeDate": "2026-05-04T13:30:00+0000",
            "type": "DIVIDEND_OR_INTEREST",
            "instrument": {"symbol": "AAPL"},
            "amount": 5.50,
            "netAmount": 5.50,
            "currency": "USD",
        },
    ]


def _mk_response(
    *,
    status_code: int = 200,
    json_payload: Any = None,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a MagicMock that quacks like an httpx.Response."""
    response = MagicMock(name=f"httpx.Response[{status_code}]")
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = json_payload
    response.text = "" if json_payload is None else str(json_payload)
    return response


@pytest.fixture
def make_response() -> Any:
    """Factory for fake httpx.Response objects."""
    return _mk_response


@pytest.fixture(autouse=True)
def _suppress_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make sure ``bootstrap_dotenv`` cannot leak the developer's real .env.

    We force the cwd-anchored search to start in ``tmp_path``.
    """
    monkeypatch.chdir(tmp_path)
    # Ensure no leftover env from a sibling test
    os.environ.pop("SCHWAB_MCP_DOTENV_LOADED", None)
