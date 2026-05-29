"""``get_account_summary`` — aggregate positions + balances for one account.

## Balances field guide (LLM hint)

Schwab returns 3 balance snapshots per account; choose by use case:

- ``currentBalances``: real-time account state (use for "what's my buying
  power right now?"). Fields: cashAvailableForTrading, buyingPower,
  marginBalance, equity, etc.
- ``initialBalances``: snapshot taken at the start of the trading day
  (use for daily P&L baseline). Fields mirror currentBalances.
- ``projectedBalances``: balances after pending settlements applied
  (use for "what will I have after T+2 settlements?"). Useful when
  recent trades haven't settled.

For most LLM agent queries asking about "my balance", use
``currentBalances.buyingPower`` and ``currentBalances.equity``. The
intermediate ``initialBalances`` and ``projectedBalances`` are surfaced
for completeness but rarely needed in agent flows. This module's
``get_account_summary_impl`` exposes the ``currentBalances`` snapshot
under the ``balances`` key.
"""

from __future__ import annotations

from typing import Any

from ..cache import get_cache
from ..models import GetAccountSummaryInput
from ._common import SchwabApiError, get_client, normalise_response


def get_account_summary_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary: positions count, market value, cash, balances."""
    args = GetAccountSummaryInput.model_validate(payload)
    client = get_client()

    response = client.get_account(args.account_hash, fields=["positions"])
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:error",
        }

    securities_account = (data or {}).get("securitiesAccount") or {}
    positions = securities_account.get("positions") or []
    current = securities_account.get("currentBalances") or {}

    total_market_value = sum(_safe_float(p.get("marketValue")) for p in positions)
    total_pl = sum(_safe_float(p.get("longOpenProfitLoss") or p.get("currentDayProfitLoss")) for p in positions)

    cache_status = "skipped:disabled"
    cache = get_cache()
    if cache is not None:
        try:
            inserted = cache.write_positions_snapshot(args.account_hash, positions)
            cache_status = f"snapshot_written:{inserted}"
        except Exception as exc:  # pragma: no cover
            cache_status = f"skipped:error:{type(exc).__name__}"

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "summary": {
            "position_count": len(positions),
            "total_market_value": total_market_value,
            "total_unrealized_pl": total_pl,
            "cash_balance": current.get("cashBalance"),
            "buying_power": current.get("buyingPower"),
            "liquidation_value": current.get("liquidationValue"),
            "currency": current.get("currency") or "USD",
        },
        "balances": current,
        "_cache_status": cache_status,
    }


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
