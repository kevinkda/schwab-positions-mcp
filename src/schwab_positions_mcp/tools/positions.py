"""``get_account_positions`` — return one account's positions and write a snapshot.

## Balances field guide (LLM hint)

Schwab returns 3 balance snapshots per account; this tool surfaces 2 of them:

- ``current_balances``: real-time account state (use for "what's my buying
  power right now?"). Fields: cashAvailableForTrading, buyingPower,
  marginBalance, equity, etc.
- ``initial_balances``: snapshot taken at the start of the trading day
  (use for daily P&L baseline). Fields mirror currentBalances.
- (``projectedBalances``: balances after pending settlements; not
  surfaced here — use ``get_account_summary`` if you need them.)

For most LLM agent queries asking about "my balance" or "buying power",
use ``current_balances.buyingPower`` and ``current_balances.equity``.
``initial_balances`` is rarely needed in agent flows.
"""

from __future__ import annotations

from typing import Any

from ..cache import get_cache
from ..models import GetAccountPositionsInput
from ._common import SchwabApiError, get_client, normalise_response


def get_account_positions_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Fetch account + positions, persist snapshot to cache, return both."""
    args = GetAccountPositionsInput.model_validate(payload)
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

    cache_status = "skipped:disabled"
    cache = get_cache()
    if cache is not None:
        try:
            inserted = cache.write_positions_snapshot(args.account_hash, positions)
            cache_status = f"snapshot_written:{inserted}"
        except Exception as exc:  # pragma: no cover - defensive
            cache_status = f"skipped:error:{type(exc).__name__}"

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "positions": positions,
        "count": len(positions),
        "current_balances": securities_account.get("currentBalances"),
        "initial_balances": securities_account.get("initialBalances"),
        "_cache_status": cache_status,
    }
