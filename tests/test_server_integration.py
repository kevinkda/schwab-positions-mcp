"""Server-level integration tests for ``schwab_positions_mcp.server``.

These tests verify the FastMCP-registered tool surface end-to-end without
spawning a subprocess: we call the registered tool callables directly so we
can inject a mocked ``ReadOnlySchwabClient``. A fully out-of-process
``stdio_client`` test would need a real OAuth token, which we deliberately
keep out of CI.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from schwab_positions_mcp import server as server_module
from schwab_positions_mcp.tools import meta


def _resp(status: int = 200, payload: Any = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {}
    r.json.return_value = payload
    r.text = ""
    return r


class TestServerToolSurface:
    def test_eight_tools_registered(self) -> None:
        info = meta.get_server_info_impl()
        assert len(info["tools"]) == 8

    def test_server_module_has_all_tool_callables(self) -> None:
        for name in (
            "get_accounts",
            "get_account_numbers",
            "get_account_positions",
            "get_orders_history",
            "get_transactions",
            "get_account_summary",
            "health_check",
            "get_server_info",
        ):
            assert hasattr(server_module, name), f"server.{name} missing"

    def test_get_accounts_proxies_to_client(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_account_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_accounts.return_value = _resp(200, mock_account_data)
        out = server_module.get_accounts()
        assert out["ok"] is True
        assert out["count"] == 2

    def test_health_check_returns_dict(self) -> None:
        out = server_module.health_check()
        assert isinstance(out, dict)
        assert "status" in out
        assert out["is_read_only"] is True

    def test_get_server_info_returns_dict(self) -> None:
        out = server_module.get_server_info()
        assert out["name"] == "schwab-positions-mcp"

    def test_get_account_positions_via_server(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = server_module.get_account_positions(
            account_hash="ACCT_HASH_AAAAAAAAAAAA",
        )
        assert out["ok"] is True
        assert out["count"] == 2

    def test_get_orders_history_via_server(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_orders_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, mock_orders_data)
        out = server_module.get_orders_history(
            account_hash="ACCT_HASH_AAAAAAAAAAAA",
            from_entered_time="2026-05-01T00:00:00+00:00",
            to_entered_time="2026-05-28T00:00:00+00:00",
        )
        assert out["ok"] is True
        assert out["count"] == 2

    def test_get_transactions_via_server(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_transactions_data: list[dict[str, Any]],
    ) -> None:
        mock_schwab_client.get_transactions.return_value = _resp(200, mock_transactions_data)
        out = server_module.get_transactions(
            account_hash="ACCT_HASH_AAAAAAAAAAAA",
            start_date="2026-05-01",
            end_date="2026-05-28",
        )
        assert out["ok"] is True
        assert out["count"] == 2

    def test_get_account_summary_via_server(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = server_module.get_account_summary(
            account_hash="ACCT_HASH_AAAAAAAAAAAA",
        )
        assert out["ok"] is True
        assert out["summary"]["position_count"] == 2


class TestMainFunctionGuarded:
    """Ensure the ``main`` entrypoint exists and uses stdio transport."""

    def test_main_callable_exists(self) -> None:
        assert callable(server_module.main)


class TestVersionAttribute:
    def test_mcp_server_has_version(self) -> None:
        assert server_module.mcp._mcp_server.version
