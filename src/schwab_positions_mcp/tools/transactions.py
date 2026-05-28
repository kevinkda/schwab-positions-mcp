"""``get_transactions`` — return transaction history for one account."""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

from ..cache import get_cache
from ..models import GetTransactionsInput
from ._common import SchwabApiError, get_client, normalise_response


def get_transactions_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch transactions between ``start_date`` and ``end_date`` (inclusive)."""
    args = GetTransactionsInput.model_validate(payload)
    client = get_client()

    start_dt = datetime.combine(args.start_date, time.min, tzinfo=UTC)
    end_dt = datetime.combine(args.end_date, time.max, tzinfo=UTC)

    kwargs: dict[str, Any] = {
        "start_date": start_dt,
        "end_date": end_dt,
    }
    if args.types is not None:
        kwargs["transaction_types"] = list(args.types)
    if args.symbol is not None:
        kwargs["symbol"] = args.symbol

    response = client.get_transactions(args.account_hash, **kwargs)
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:error",
        }

    transactions = data if isinstance(data, list) else []

    cache_status = "skipped:disabled"
    cache = get_cache()
    if cache is not None:
        try:
            inserted = cache.write_transactions_history(args.account_hash, transactions)
            cache_status = f"history_written:{inserted}"
        except Exception as exc:  # pragma: no cover
            cache_status = f"skipped:error:{type(exc).__name__}"

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "transactions": transactions,
        "count": len(transactions),
        "start_date": args.start_date.isoformat(),
        "end_date": args.end_date.isoformat(),
        "_cache_status": cache_status,
    }
