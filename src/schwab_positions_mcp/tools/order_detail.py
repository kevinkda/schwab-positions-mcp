"""``get_order_detail`` — read a single order by id (read-only).

Wraps schwab-py's ``Client.get_order(order_id, account_hash)``, which is a
**read** of one order's full detail. It is NOT a mutation: there is no
placing, cancelling, or replacing of an order here — only retrieval of an
existing order's status / legs / fills by id.

``get_order`` is on the Layer-1 ``_READ_ONLY_METHODS`` allow list in
:mod:`schwab_positions_mcp.client`. No cache write (single-record reads are
cheap and the orders-history tool already persists list snapshots).
"""

from __future__ import annotations

from typing import Any

from ..models import GetOrderDetailInput
from ._common import SchwabApiError, get_client, normalise_response


def get_order_detail_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch one order's detail by ``order_id`` for ``account_hash`` (read-only)."""
    args = GetOrderDetailInput.model_validate(payload)
    client = get_client()

    # schwab-py signature: get_order(order_id, account_hash) — positional order
    # matters; order_id comes first.
    response = client.get_order(args.order_id, args.account_hash)
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
        "order_id": args.order_id,
        "order": data,
        "_cache_status": "skipped:not-cached",
    }
