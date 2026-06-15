"""``get_user_preferences`` — return the account user-preference settings.

This tool exposes Schwab's read-only ``GET /userPreference`` endpoint as an
MCP tool. The underlying method (``schwab.client.Client.get_user_preferences``)
was already on the Layer-1 ``_READ_ONLY_METHODS`` allow list in
:mod:`schwab_positions_mcp.client`; v0.4.0 simply surfaces it as a first-class
MCP tool so callers can read account-level preferences (default account, nick
names, streamer routing metadata) without leaving the MCP context.

This tool is **read-only**: it only calls the allow-listed
``get_user_preferences`` method, places no order, and writes no cache.
"""

from __future__ import annotations

from typing import Any

from ._common import SchwabApiError, get_client, normalise_response


def get_user_preferences_impl(
    _payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the account user-preference settings (read-only).

    No arguments. No mutation. No cache (preferences change rarely and are
    cheap to re-fetch on demand).
    """
    client = get_client()
    response = client.get_user_preferences()
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {
                "status_code": exc.status_code,
                "reason": exc.reason,
                "request_id": exc.request_id,
            },
            "_cache_status": "skipped:error",
        }

    return {
        "ok": True,
        "preferences": data,
        "_cache_status": "skipped:not-cached",
    }
