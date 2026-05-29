"""Regression tests for v0.1.1 patches (sibling-reported bugs).

Each test maps to a bug ID from the sibling-A/B reports:

* **B1** — ``_build_client`` must construct schwab-py client with
  ``enforce_enums=False`` so MCP-supplied string literals
  (``fields=["positions"]``, ``status="FILLED"``, ``types=["TRADE"]``)
  flow through without ``ValueError``. See tools/_common.py.

* **B2** — ``get_account_numbers`` MCP tool must exist, return a
  list of ``{accountNumber, hashValue}`` mappings, and surface 401 / 429 /
  5xx errors. See tools/account_numbers.py.

* **B3** — ``orders.py`` comment must reflect actual behaviour
  (``enforce_enums=False`` is set in ``_build_client``, not at "higher
  precedence"). Source-text check.

* **B4** — ``get_server_info`` must list ``get_account_numbers`` in its
  ``tools`` array (so MCP discovery / introspection surfaces the new
  tool).

Hermetic: every test uses fixtures from ``conftest.py`` (mocked
schwab-py client, no network). The 5-layer read-only boundary is NOT
exercised here — see tests/test_security_boundary.py for that.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from schwab_positions_mcp.tools import (
    account_numbers,
    accounts,
    meta,
    orders,
    summary,
    transactions,
)

VALID_HASH = "ACCT_HASH_AAAAAAAAAAAA"


def _resp(status: int, payload: Any = None) -> MagicMock:
    r = MagicMock(name=f"Response[{status}]")
    r.status_code = status
    r.headers = {}
    r.json.return_value = payload
    r.text = "" if payload is None else str(payload)
    return r


# ---------------------------------------------------------------------------
# B1 — enforce_enums=False
# ---------------------------------------------------------------------------


class TestB1EnforceEnumsDisabled:
    """Constructing the schwab-py client must use ``enforce_enums=False``.

    Otherwise MCP string inputs (``fields=["positions"]``, status="FILLED",
    types=["TRADE"]) are rejected by schwab-py's internal enum enforcement
    with an opaque ValueError.
    """

    def test_b1_build_client_sets_enforce_enums_false(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """``_build_client`` passes ``enforce_enums=False`` to schwab-py."""
        token_file = tmp_path / "token.json"
        token_file.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("SCHWAB_API_KEY", "fake-key")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "fake-secret")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(token_file))

        from schwab_positions_mcp.tools import _common

        with patch("schwab.auth.client_from_token_file") as mock_factory:
            mock_factory.return_value = MagicMock(name="schwab.client.Client")
            _common._build_client()

        assert mock_factory.call_count == 1
        kwargs = mock_factory.call_args.kwargs
        assert kwargs["enforce_enums"] is False, (
            "B1 regression — _build_client MUST pass enforce_enums=False so "
            "MCP string inputs (fields=['positions'], status='FILLED', "
            "types=['TRADE']) are not rejected by schwab-py."
        )

    def test_b1_source_does_not_set_enforce_enums_true(self) -> None:
        """The literal ``enforce_enums=True`` must NOT appear in src/.

        Both the runtime client (``tools/_common.py``) and the OAuth
        bootstrap (``auth.py``) construct schwab-py clients; both must
        use ``enforce_enums=False`` for the v0.1.1 contract to hold.
        Substring matches inside comments / docstrings are allowed
        (we explain in prose why ``enforce_enums=True`` was wrong) but
        not the assignment expression itself.
        """
        repo_root = Path(__file__).resolve().parent.parent
        src_root = repo_root / "src" / "schwab_positions_mcp"
        offenders: list[str] = []
        for py in src_root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                # Skip docstring/triple-quoted prose.
                if '"""' in stripped or "'''" in stripped:
                    continue
                if "enforce_enums=True" in stripped:
                    offenders.append(str(py.relative_to(repo_root)))
                    break
        assert offenders == [], (
            f"B1 regression — 'enforce_enums=True' is back in non-comment src/ code. Offenders: {offenders}"
        )

    def test_b1_get_accounts_with_fields_string_succeeds(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_account_data: list[dict[str, Any]],
    ) -> None:
        """``get_accounts(fields=["positions"])`` must reach schwab-py without raising."""
        mock_schwab_client.get_accounts.return_value = _resp(200, mock_account_data)
        out = accounts.get_accounts_impl({"fields": ["positions"]})
        assert out["ok"] is True
        # And the list[str] is forwarded verbatim, not wrapped in an enum.
        kwargs = mock_schwab_client.get_accounts.call_args.kwargs
        assert kwargs["fields"] == ["positions"]

    def test_b1_orders_with_status_string_forwards_value(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_orders_data: list[dict[str, Any]],
    ) -> None:
        """``status="FILLED"`` must reach ``get_orders_for_account`` as a string."""
        mock_schwab_client.get_orders_for_account.return_value = _resp(200, mock_orders_data)
        out = orders.get_orders_history_impl(
            {
                "account_hash": VALID_HASH,
                "from_entered_time": datetime(2026, 5, 1, tzinfo=UTC),
                "to_entered_time": datetime(2026, 5, 28, tzinfo=UTC),
                "status": "FILLED",
            }
        )
        assert out["ok"] is True
        kwargs = mock_schwab_client.get_orders_for_account.call_args.kwargs
        assert kwargs["status"] == "FILLED"

    def test_b1_transactions_with_types_string_forwards_value(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_transactions_data: list[dict[str, Any]],
    ) -> None:
        """``types=["TRADE"]`` must reach ``get_transactions`` as a list of strings."""
        mock_schwab_client.get_transactions.return_value = _resp(200, mock_transactions_data)
        out = transactions.get_transactions_impl(
            {
                "account_hash": VALID_HASH,
                "start_date": date(2026, 5, 1),
                "end_date": date(2026, 5, 28),
                "types": ["TRADE"],
            }
        )
        assert out["ok"] is True
        kwargs = mock_schwab_client.get_transactions.call_args.kwargs
        assert kwargs["transaction_types"] == ["TRADE"]

    def test_b1_account_summary_with_string_field_succeeds(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
    ) -> None:
        """``get_account_summary`` (which internally passes fields=['positions'])
        must reach schwab-py without raising."""
        mock_schwab_client.get_account.return_value = _resp(200, mock_positions_data)
        out = summary.get_account_summary_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        # Confirm fields=["positions"] (string list) was forwarded.
        kwargs = mock_schwab_client.get_account.call_args.kwargs
        assert kwargs["fields"] == ["positions"]


# ---------------------------------------------------------------------------
# B2 — get_account_numbers MCP tool
# ---------------------------------------------------------------------------


class TestB2GetAccountNumbersTool:
    """``get_account_numbers`` exposes accountNumber → hashValue mapping."""

    def test_b2_returns_mappings(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """200 OK → ``ok=True`` with the parsed list and a ``count``."""
        payload = [
            {"accountNumber": "74775319", "hashValue": "5ABDD309F7B7"},  # pragma: allowlist secret
            {"accountNumber": "12345678", "hashValue": "ABCDEF123456"},  # pragma: allowlist secret
        ]
        mock_schwab_client.get_account_numbers.return_value = _resp(200, payload)
        out = account_numbers.get_account_numbers_impl()
        assert out["ok"] is True
        assert out["count"] == 2
        assert out["account_numbers"][0]["accountNumber"] == "74775319"
        assert out["account_numbers"][0]["hashValue"] == "5ABDD309F7B7"  # pragma: allowlist secret
        assert out["_cache_status"] == "skipped:not-cached"

    def test_b2_handles_401(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """401 → ``ok=False`` with refresh_token_expired surfaced cleanly."""
        mock_schwab_client.get_account_numbers.return_value = _resp(401)
        out = account_numbers.get_account_numbers_impl()
        assert out["ok"] is False
        assert out["error"]["status_code"] == 401
        assert out["error"]["reason"] == "refresh_token_expired"

    def test_b2_handles_429(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """429 → rate_limited."""
        mock_schwab_client.get_account_numbers.return_value = _resp(429)
        out = account_numbers.get_account_numbers_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_b2_handles_5xx(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """5xx → upstream_error."""
        mock_schwab_client.get_account_numbers.return_value = _resp(503)
        out = account_numbers.get_account_numbers_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_b2_handles_empty_list(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """Empty list (no accounts) → ``count=0``, ``ok=True``."""
        mock_schwab_client.get_account_numbers.return_value = _resp(200, [])
        out = account_numbers.get_account_numbers_impl()
        assert out["ok"] is True
        assert out["count"] == 0
        assert out["account_numbers"] == []

    def test_b2_non_list_payload_yields_empty_count(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """Defensive: a malformed (dict) payload should not crash; treat as empty."""
        mock_schwab_client.get_account_numbers.return_value = _resp(200, {"unexpected": "shape"})
        out = account_numbers.get_account_numbers_impl()
        assert out["ok"] is True
        assert out["count"] == 0

    def test_b2_uses_readonly_whitelist_method(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """Tool calls schwab-py's ``get_account_numbers`` (already in white-list)."""
        mock_schwab_client.get_account_numbers.return_value = _resp(200, [])
        account_numbers.get_account_numbers_impl()
        mock_schwab_client.get_account_numbers.assert_called_once_with()


# ---------------------------------------------------------------------------
# B3 — orders.py comment hygiene
# ---------------------------------------------------------------------------


class TestB3OrdersCommentAccurate:
    """The orders.py comment around the ``status`` kwarg must reflect reality."""

    def test_b3_comment_does_not_claim_higher_precedence(self) -> None:
        """The misleading "at higher precedence" phrase must be gone."""
        repo_root = Path(__file__).resolve().parent.parent
        orders_py = (repo_root / "src" / "schwab_positions_mcp" / "tools" / "orders.py").read_text(encoding="utf-8")
        assert "higher precedence" not in orders_py, (
            "B3 regression — orders.py still claims enforce_enums=False is "
            "applied 'at higher precedence', which was never true."
        )

    def test_b3_comment_mentions_enforce_enums_false(self) -> None:
        """The new comment should explain the actual mechanism."""
        repo_root = Path(__file__).resolve().parent.parent
        orders_py = (repo_root / "src" / "schwab_positions_mcp" / "tools" / "orders.py").read_text(encoding="utf-8")
        assert "enforce_enums=False" in orders_py


# ---------------------------------------------------------------------------
# B4 — get_server_info self-reports the new tool
# ---------------------------------------------------------------------------


class TestB4ServerInfoListsAccountNumbers:
    """``get_server_info`` must include ``get_account_numbers`` in its tool list."""

    def test_b4_tool_list_contains_get_account_numbers(self) -> None:
        info = meta.get_server_info_impl()
        assert "get_account_numbers" in info["tools"], (
            "B4 regression — get_server_info must list get_account_numbers now that it is registered as an MCP tool."
        )

    def test_b4_tool_count_is_eight(self) -> None:
        info = meta.get_server_info_impl()
        assert len(info["tools"]) == 8, (
            "B4 regression — server_info should report exactly 8 tools "
            "after v0.1.1 (5 portfolio + 1 account_numbers + 2 meta)."
        )

    def test_b4_readme_advertises_eight_tools(self) -> None:
        """README.md (English) must reflect the 8-tool surface."""
        repo_root = Path(__file__).resolve().parent.parent
        readme = (repo_root / "README.md").read_text(encoding="utf-8")
        assert "Tools (8)" in readme
        assert "get_account_numbers" in readme

    def test_b4_readme_zh_advertises_eight_tools(self) -> None:
        """README_zh.md must reflect the 8-tool surface."""
        repo_root = Path(__file__).resolve().parent.parent
        path = repo_root / "README_zh.md"
        if not path.exists():
            pytest.skip("README_zh.md absent")
        body = path.read_text(encoding="utf-8")
        assert "工具列表（8 个）" in body or "8 个）" in body  # noqa: RUF001 (matching README_zh literal)
        assert "get_account_numbers" in body
