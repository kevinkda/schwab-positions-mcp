"""Live-token e2e smoke tests.

Runs against the real Schwab Trader API with the user's live token.
SKIPPED by default. Enable with::

    SCHWAB_POSITIONS_LIVE_E2E=1 uv run pytest tests/test_live_smoke.py -v

Prerequisites
-------------
- ``~/.config/schwab-positions-mcp/token.json`` must exist
  (run ``uv run python -m schwab_positions_mcp.auth login_flow`` first).
- ``SCHWAB_API_KEY`` / ``SCHWAB_APP_SECRET`` populated in environment.
- The Schwab account must have at least 1 active linked account
  (otherwise some tests skip).

These tests do **not** mutate state (read-only by design) but they DO
consume Schwab API rate-limit budget (2 req/s soft cap). Run sparingly.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

LIVE = os.environ.get("SCHWAB_POSITIONS_LIVE_E2E") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason="Set SCHWAB_POSITIONS_LIVE_E2E=1 to run live e2e tests",
)


@pytest.fixture(scope="module")
def live_token_path() -> Path:
    """Verify the live token is present, else skip the whole module."""
    token_path = Path.home() / ".config" / "schwab-positions-mcp" / "token.json"
    if not token_path.exists():
        pytest.skip(f"No live token at {token_path}; run login_flow first")
    return token_path


@pytest.fixture(scope="module", autouse=True)
def _reset_live_singleton() -> None:
    """Drop the cached singleton so live tests build a fresh client."""
    from schwab_positions_mcp.tools._common import reset_client_singleton

    reset_client_singleton()
    yield
    reset_client_singleton()


class TestHealthCheck:
    """Smoke test: health_check returns is_read_only=true with live env."""

    def test_health_check_live(self, live_token_path: Path) -> None:
        from schwab_positions_mcp.tools.meta import health_check_impl

        result = health_check_impl()
        assert result["is_read_only"] is True
        assert result["status"] in {"ready", "ok"}, (
            f"expected ready, got {result['status']}; checks={result.get('checks')}"
        )
        assert result["checks"]["token_present"] is True


class TestAccountNumbers:
    """Smoke test: get_account_numbers returns non-empty mapping."""

    def test_get_account_numbers_live(self, live_token_path: Path) -> None:
        from schwab_positions_mcp.tools.account_numbers import (
            get_account_numbers_impl,
        )

        result = get_account_numbers_impl()
        assert isinstance(result, dict)
        assert result.get("ok") is True, f"unexpected error: {result}"
        accounts = result.get("account_numbers") or []
        assert isinstance(accounts, list)
        assert len(accounts) >= 1, "expected at least 1 account on test user"
        for acc in accounts:
            assert "accountNumber" in acc
            assert "hashValue" in acc


class TestAccountPositions:
    """Smoke test: get_account_positions returns parseable structure."""

    def test_get_account_positions_live(self, live_token_path: Path) -> None:
        from schwab_positions_mcp.tools.account_numbers import (
            get_account_numbers_impl,
        )
        from schwab_positions_mcp.tools.positions import (
            get_account_positions_impl,
        )

        listing = get_account_numbers_impl()
        accounts = (listing or {}).get("account_numbers") or []
        if not accounts:
            pytest.skip("No accounts on test user")

        first_hash = accounts[0]["hashValue"]
        result = get_account_positions_impl({"account_hash": first_hash})
        assert result.get("ok") is True, f"unexpected error: {result}"
        assert "positions" in result
        assert isinstance(result["positions"], list)


class TestOrdersHistoryLookbackBoundary:
    """Smoke test: 60-day lookback validation works against live setup."""

    def test_within_60_days_passes_pydantic(self, live_token_path: Path) -> None:
        """30 days back should pass Pydantic validation."""
        from schwab_positions_mcp.models import GetOrdersHistoryInput
        from schwab_positions_mcp.tools.account_numbers import (
            get_account_numbers_impl,
        )

        listing = get_account_numbers_impl()
        accounts = (listing or {}).get("account_numbers") or []
        if not accounts:
            pytest.skip("No accounts on test user")

        from_time = datetime.now(UTC) - timedelta(days=30)
        to_time = datetime.now(UTC)

        validated = GetOrdersHistoryInput(
            account_hash=accounts[0]["hashValue"],
            from_entered_time=from_time,
            to_entered_time=to_time,
        )
        assert validated.from_entered_time is not None
        assert validated.from_entered_time.tzinfo is not None
