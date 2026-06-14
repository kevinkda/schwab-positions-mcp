"""``health_check`` and ``get_server_info`` meta tools."""

from __future__ import annotations

import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__
from ..health import REFRESH_TOKEN_LIFETIME_DAYS


def health_check_impl(_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lightweight readiness check.

    Reports presence of credentials + token without contacting Schwab. When a
    token file exists, also reports its approximate age and days-to-expiry so
    callers (and the user) can see the 7-day refresh-token clock ticking down.
    The age is derived from the token file's mtime — an offline-safe estimate
    that needs no credentials; the dedicated ``schwab_positions_mcp.health``
    CLI probe uses schwab-py's exact ``creation_timestamp`` when creds exist.
    """
    api_key_present = bool(os.environ.get("SCHWAB_API_KEY", "").strip())
    app_secret_present = bool(os.environ.get("SCHWAB_APP_SECRET", "").strip())
    token_path = Path(
        os.environ.get("SCHWAB_POSITIONS_TOKEN_PATH")
        or (Path.home() / ".config" / "schwab-positions-mcp" / "token.json")
    )
    token_present = token_path.exists()

    token_age_days: float | None = None
    token_expires_in_days: float | None = None
    if token_present:
        try:
            mtime = token_path.stat().st_mtime
            age_seconds = datetime.now(UTC).timestamp() - mtime
            token_age_days = round(max(0.0, age_seconds) / 86400, 3)
            token_expires_in_days = round(REFRESH_TOKEN_LIFETIME_DAYS - token_age_days, 3)
        except OSError:
            token_age_days = None
            token_expires_in_days = None

    if api_key_present and app_secret_present and token_present:
        status = "ready"
    elif api_key_present and app_secret_present:
        status = "needs_oauth_login"
    else:
        status = "needs_env_setup"

    return {
        "status": status,
        "is_read_only": True,
        "checks": {
            "api_key_present": api_key_present,
            "app_secret_present": app_secret_present,
            "token_present": token_present,
            "token_path": str(token_path),
            "token_age_days": token_age_days,
            "token_expires_in_days": token_expires_in_days,
        },
        "checked_at": datetime.now(UTC).isoformat(),
    }


def get_server_info_impl(_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Server metadata — version, platform, read-only declaration."""
    return {
        "name": "schwab-positions-mcp",
        "version": __version__,
        "is_read_only": True,
        "trade_endpoints_exposed": False,
        "tools": [
            "get_accounts",
            "get_account_numbers",
            "get_account_positions",
            "get_orders_history",
            "get_transactions",
            "get_account_summary",
            "health_check",
            "get_server_info",
        ],
        "python_version": sys.version,
        "platform": platform.platform(),
        "security_doc": "docs/SECURITY.md",
    }
