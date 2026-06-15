"""``get_transaction_detail`` — read a single transaction by id (read-only).

Wraps schwab-py's ``Client.get_transaction(account_hash, transaction_id)``,
which **reads** one historical transaction record's detail. It is NOT a
mutation — no money moves, no order is placed; it only retrieves an existing
settled/booked transaction by id.

``get_transaction`` is on the Layer-1 ``_READ_ONLY_METHODS`` allow list in
:mod:`schwab_positions_mcp.client`. No cache write (single-record reads are
cheap; the transactions-history tool already persists list snapshots).
"""

from __future__ import annotations

from typing import Any

from ..models import GetTransactionDetailInput
from ._common import SchwabApiError, get_client, normalise_response


def get_transaction_detail_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch one transaction's detail by ``transaction_id`` for ``account_hash``."""
    args = GetTransactionDetailInput.model_validate(payload)
    client = get_client()

    # schwab-py signature: get_transaction(account_hash, transaction_id) —
    # positional order matters; account_hash comes first.
    response = client.get_transaction(args.account_hash, args.transaction_id)
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:error",
        }

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "transaction_id": args.transaction_id,
        "transaction": data,
        "_cache_status": "skipped:not-cached",
    }
