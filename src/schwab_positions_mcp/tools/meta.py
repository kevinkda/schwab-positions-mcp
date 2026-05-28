"""``health_check`` and ``get_server_info`` meta tools."""

from __future__ import annotations

import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import __version__


def health_check_impl(_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lightweight readiness check.

    Reports presence of credentials + token without contacting Schwab.
    """
    api_key_present = bool(os.environ.get("SCHWAB_API_KEY", "").strip())
    app_secret_present = bool(os.environ.get("SCHWAB_APP_SECRET", "").strip())
    token_path = Path(
        os.environ.get("SCHWAB_POSITIONS_TOKEN_PATH")
        or (Path.home() / ".config" / "schwab-positions-mcp" / "token.json")
    )
    token_present = token_path.exists()

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
