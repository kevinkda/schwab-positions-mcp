"""``get_accounts`` — list all linked Schwab accounts (read-only)."""

from __future__ import annotations

from typing import Any

from ..models import GetAccountsInput
from ._common import SchwabApiError, get_client, normalise_response


def get_accounts_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Return all linked accounts. Optional ``fields=['positions']`` expansion."""
    args = GetAccountsInput.model_validate(payload)
    client = get_client()

    fields_param: list[str] | None = None
    if args.fields:
        fields_param = list(args.fields)

    response = client.get_accounts(fields=fields_param) if fields_param else client.get_accounts()
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:error",
        }

    accounts = data if isinstance(data, list) else []
    return {
        "ok": True,
        "accounts": accounts,
        "count": len(accounts),
        "_cache_status": "skipped:not-cached",
    }
