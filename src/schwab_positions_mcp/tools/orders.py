"""``get_orders_history`` — return recent orders for one account."""

from __future__ import annotations

from typing import Any

from ..cache import get_cache
from ..models import GetOrdersHistoryInput
from ._common import SchwabApiError, get_client, normalise_response


def get_orders_history_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch orders for ``account_hash`` between ``from_entered_time`` and ``to_entered_time``."""
    args = GetOrdersHistoryInput.model_validate(payload)
    client = get_client()

    kwargs: dict[str, Any] = {
        "from_entered_datetime": args.from_entered_time,
        "to_entered_datetime": args.to_entered_time,
    }
    if args.max_results is not None:
        kwargs["max_results"] = args.max_results
    if args.status is not None:
        # schwab-py accepts strings when ``enforce_enums=False`` (set in
        # ``_build_client``). Pydantic ``Literal[_OrderStatus]`` in
        # ``models.py`` already constrains the value to a known Schwab
        # status, so this kwarg is safe to forward verbatim.
        kwargs["status"] = args.status

    response = client.get_orders_for_account(args.account_hash, **kwargs)
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:error",
        }

    orders = data if isinstance(data, list) else []

    cache_status = "skipped:disabled"
    cache = get_cache()
    if cache is not None:
        try:
            inserted = cache.write_orders_history(args.account_hash, orders)
            cache_status = f"history_written:{inserted}"
        except Exception as exc:  # pragma: no cover
            cache_status = f"skipped:error:{type(exc).__name__}"

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "orders": orders,
        "count": len(orders),
        "from_entered_time": args.from_entered_time.isoformat(),
        "to_entered_time": args.to_entered_time.isoformat(),
        "_cache_status": cache_status,
    }
