"""``get_account_numbers`` — return Schwab accountNumber → encrypted hash mapping.

The Schwab Trader API encrypts account numbers in every other endpoint
(``/accounts/{hashValue}``, ``/accounts/{hashValue}/orders``, etc.). The
plaintext ``accountNumber`` returned by ``GET /accounts`` cannot be used as
the path component — callers MUST first hit ``GET /accounts/accountNumbers``
to get the SHA-256-style ``hashValue`` for each account.

Without this tool, an MCP user has no in-protocol way to discover
``account_hash`` and is forced to leave the MCP context (e.g. open a Python
REPL with schwab-py) just to translate ``accountNumber`` → ``hashValue``.

This tool is read-only — it only calls the read-only
``schwab.client.Client.get_account_numbers`` method which is already on the
Layer-1 ``_READ_ONLY_METHODS`` white-list in :mod:`schwab_positions_mcp.client`.
"""

from __future__ import annotations

from typing import Any

from ._common import SchwabApiError, get_client, normalise_response


def get_account_numbers_impl(
    _payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the ``accountNumber`` → ``hashValue`` mapping for every linked account.

    Returns a list of ``{"accountNumber": "...", "hashValue": "..."}``
    dictionaries. The ``hashValue`` is the encrypted ``account_hash``
    required by all other ``schwab-positions-mcp`` tools that take an
    ``account_hash`` argument.

    No mutation. No cache (the mapping rarely changes and is cheap to
    re-fetch on demand).
    """
    client = get_client()
    response = client.get_account_numbers()
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

    mappings: list[dict[str, Any]] = data if isinstance(data, list) else []
    return {
        "ok": True,
        "account_numbers": mappings,
        "count": len(mappings),
        "_cache_status": "skipped:not-cached",
    }
