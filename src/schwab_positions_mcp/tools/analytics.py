"""Read-only derived portfolio analytics (v0.2.1).

This module adds three **read-only, derived** tools that compute analytics on
top of the data already returned by the existing read-only Schwab endpoints.
They introduce **no** new mutation paths, **no** new cache writes, and call
**only** the Layer-1 white-listed read methods (``get_account``,
``get_account_numbers``) via :class:`schwab_positions_mcp.client.ReadOnlySchwabClient`:

- :func:`get_pnl_analysis_impl` — per-position cost basis / unrealized P&L /
  unrealized %, plus a transaction-derived realized P&L, and a portfolio roll-up.
  **Cost-basis method: AVERAGE COST.** Schwab's positions feed exposes
  ``averagePrice`` (the blended average cost per share already maintained by
  Schwab); the positions API does **not** expose per-lot acquisition records,
  so a true FIFO lot walk is impossible from this feed. We therefore report
  unrealized P&L on the average-cost basis. Realized P&L is derived from
  ``TRADE`` transactions whose net cash is positive (proceeds from SELLs) minus
  the average-cost outflow Schwab reports in the transaction ``netAmount`` —
  see :func:`_derive_realized_pl` for the exact, conservative formula and its
  documented limitations.
- :func:`get_concentration_analysis_impl` — top-N weights, Herfindahl-Hirschman
  Index (HHI), max single-position weight, and asset-type exposure. There is no
  GICS-sector field in the Schwab positions feed, so genuine sector exposure is
  reported as ``"N/A"`` and we surface ``assetType`` (EQUITY / OPTION / …) as a
  best-effort proxy bucket instead.
- :func:`get_cross_account_summary_impl` — fan out over
  ``get_account_numbers`` → ``get_account`` per account, then aggregate
  positions + balances into a merged view with per-account share-of-total and
  symbol-level de-duplication across accounts.

Every function is a pure transform of read-only inputs: it returns a fresh dict
and never writes to the DuckDB cache (the cache module is owned by a sibling
task; this module deliberately does not import it).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..models import (
    GetConcentrationAnalysisInput,
    GetPnlAnalysisInput,
)
from ._common import SchwabApiError, get_client, normalise_response


def _safe_float(value: Any) -> float:
    """Coerce a Schwab numeric-ish field to float; junk → 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position_symbol(position: dict[str, Any]) -> str:
    """Best-effort symbol extraction from a Schwab position dict."""
    instrument = position.get("instrument") or {}
    symbol = instrument.get("symbol")
    if isinstance(symbol, str) and symbol:
        return symbol
    return "UNKNOWN"


def _position_asset_type(position: dict[str, Any]) -> str:
    """Best-effort assetType extraction (proxy for sector, which is absent)."""
    instrument = position.get("instrument") or {}
    asset_type = instrument.get("assetType")
    if isinstance(asset_type, str) and asset_type:
        return asset_type
    return "UNKNOWN"


def _position_quantity(position: dict[str, Any]) -> float:
    """Net quantity = longQuantity - shortQuantity (short positions go negative)."""
    return _safe_float(position.get("longQuantity")) - _safe_float(position.get("shortQuantity"))


def _fetch_securities_account(account_hash: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch one account with positions; return (securitiesAccount, error_payload).

    On success returns ``(securities_account, None)``. On a normalised Schwab
    error returns ``(None, error_payload)`` where ``error_payload`` is the
    standard ``{"ok": False, "error": {...}}`` shape used across the tools.
    Only the Layer-1 white-listed read method ``get_account`` is used.
    """
    client = get_client()
    response = client.get_account(account_hash, fields=["positions"])
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return None, {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:not-cached",
        }
    securities_account = (data or {}).get("securitiesAccount") or {}
    return securities_account, None


# ---------------------------------------------------------------------------
# get_pnl_analysis
# ---------------------------------------------------------------------------


def get_pnl_analysis_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive per-position + portfolio P&L (cost-basis method: AVERAGE COST).

    Read-only derived computation. No mutation, no cache write. Calls only the
    white-listed read methods ``get_account`` (positions) and ``get_transactions``
    (realized P&L). The cost-basis method is **average cost** because Schwab's
    positions feed only exposes the blended ``averagePrice`` per holding — there
    are no per-lot acquisition records to run a FIFO walk over.
    """
    args = GetPnlAnalysisInput.model_validate(payload)

    securities_account, error = _fetch_securities_account(args.account_hash)
    if error is not None:
        return error
    assert securities_account is not None  # narrowing for mypy

    positions = securities_account.get("positions") or []

    per_position: list[dict[str, Any]] = []
    total_cost_basis = 0.0
    total_market_value = 0.0
    for position in positions:
        symbol = _position_symbol(position)
        quantity = _position_quantity(position)
        average_price = _safe_float(position.get("averagePrice"))
        market_value = _safe_float(position.get("marketValue"))
        cost_basis = abs(quantity) * average_price
        unrealized_pl = market_value - cost_basis
        unrealized_pct = (unrealized_pl / cost_basis * 100.0) if cost_basis else None

        per_position.append(
            {
                "symbol": symbol,
                "asset_type": _position_asset_type(position),
                "quantity": quantity,
                "average_price": average_price,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "unrealized_pl": unrealized_pl,
                "unrealized_pct": unrealized_pct,
            }
        )
        total_cost_basis += cost_basis
        total_market_value += market_value

    total_unrealized_pl = total_market_value - total_cost_basis
    total_unrealized_pct = (total_unrealized_pl / total_cost_basis * 100.0) if total_cost_basis else None

    realized = _derive_realized_pl(args.account_hash, args.realized_lookback_days)

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "cost_basis_method": "average_cost",
        "positions": per_position,
        "portfolio": {
            "position_count": len(per_position),
            "total_cost_basis": total_cost_basis,
            "total_market_value": total_market_value,
            "total_unrealized_pl": total_unrealized_pl,
            "total_unrealized_pct": total_unrealized_pct,
            "realized_pl": realized["realized_pl"],
            "realized_lookback_days": args.realized_lookback_days,
            "realized_trade_count": realized["realized_trade_count"],
            "realized_pl_available": realized["available"],
        },
        "_cache_status": "skipped:not-cached",
    }


def _derive_realized_pl(account_hash: str, lookback_days: int) -> dict[str, Any]:
    """Derive realized P&L from recent ``TRADE`` transactions (best-effort).

    Schwab's transactions feed reports each closing trade's ``netAmount`` (the
    net cash that hit the account). For a SELL, ``netAmount`` is the sale
    proceeds net of fees. Schwab does **not** annotate the matched lot cost on
    the transaction, so we cannot compute exact realized gain per closing trade
    from this feed alone. We therefore report a **conservative proxy**: the sum
    of net cash from closing (SELL) trades in the window, labelled clearly as
    proceeds-based so callers do not mistake it for a fully-matched FIFO result.

    Returns ``available: False`` (and ``realized_pl: None``) when the
    transactions endpoint errors, so the P&L tool still returns the unrealized
    block rather than failing the whole call. Uses only the white-listed
    ``get_transactions`` read method.
    """
    client = get_client()
    today = datetime.now(UTC).date()
    start = today - timedelta(days=lookback_days)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(today, datetime.max.time(), tzinfo=UTC)

    response = client.get_transactions(
        account_hash,
        start_date=start_dt,
        end_date=end_dt,
        transaction_types=["TRADE"],
    )
    try:
        data = normalise_response(response)
    except SchwabApiError:
        return {"realized_pl": None, "realized_trade_count": 0, "available": False}

    transactions = data if isinstance(data, list) else []
    realized_proceeds = 0.0
    trade_count = 0
    for txn in transactions:
        # A closing SELL produces positive net cash; opening BUY is negative.
        net_amount = _safe_float(txn.get("netAmount"))
        if net_amount > 0:
            realized_proceeds += net_amount
            trade_count += 1

    return {
        "realized_pl": realized_proceeds,
        "realized_trade_count": trade_count,
        "available": True,
    }


# ---------------------------------------------------------------------------
# get_concentration_analysis
# ---------------------------------------------------------------------------


def get_concentration_analysis_impl(payload: dict[str, Any]) -> dict[str, Any]:
    """Derive concentration metrics (top-N weights / HHI / max weight / asset mix).

    Read-only derived computation. No mutation, no cache write. Calls only the
    white-listed ``get_account`` read method. Weights are computed on absolute
    market value so short positions still contribute exposure. There is no
    sector field in the Schwab positions feed, so ``sector_exposure`` is
    ``"N/A"`` and ``asset_type_exposure`` is surfaced as a proxy.
    """
    args = GetConcentrationAnalysisInput.model_validate(payload)

    securities_account, error = _fetch_securities_account(args.account_hash)
    if error is not None:
        return error
    assert securities_account is not None  # narrowing for mypy

    positions = securities_account.get("positions") or []

    holdings: list[dict[str, Any]] = []
    total_abs_value = 0.0
    for position in positions:
        abs_value = abs(_safe_float(position.get("marketValue")))
        holdings.append(
            {
                "symbol": _position_symbol(position),
                "asset_type": _position_asset_type(position),
                "abs_market_value": abs_value,
            }
        )
        total_abs_value += abs_value

    # Weights + HHI.
    hhi = 0.0
    max_weight = 0.0
    asset_type_value: dict[str, float] = {}
    for holding in holdings:
        weight = (holding["abs_market_value"] / total_abs_value) if total_abs_value else 0.0
        holding["weight_pct"] = weight * 100.0
        hhi += weight * weight
        max_weight = max(max_weight, weight)
        asset_type_value[holding["asset_type"]] = (
            asset_type_value.get(holding["asset_type"], 0.0) + holding["abs_market_value"]
        )

    top_holdings = sorted(holdings, key=lambda h: h["abs_market_value"], reverse=True)[: args.top_n]
    top_n_weight_pct = sum(h["weight_pct"] for h in top_holdings)

    asset_type_exposure = {
        asset_type: {
            "abs_market_value": value,
            "weight_pct": (value / total_abs_value * 100.0) if total_abs_value else 0.0,
        }
        for asset_type, value in sorted(asset_type_value.items(), key=lambda kv: kv[1], reverse=True)
    }

    return {
        "ok": True,
        "account_hash": args.account_hash,
        "concentration": {
            "position_count": len(holdings),
            "total_abs_market_value": total_abs_value,
            "top_n": args.top_n,
            "top_n_weight_pct": top_n_weight_pct,
            "max_position_weight_pct": max_weight * 100.0,
            "hhi": hhi,
            "hhi_interpretation": _interpret_hhi(hhi, len(holdings)),
            "top_holdings": top_holdings,
            "asset_type_exposure": asset_type_exposure,
            "sector_exposure": "N/A",
        },
        "_cache_status": "skipped:not-cached",
    }


def _interpret_hhi(hhi: float, position_count: int) -> str:
    """Label HHI on the standard 0..1 normalised scale.

    HHI is the sum of squared weights. With ``n`` equal-weighted positions HHI
    is ``1/n`` (fully diversified for the count); a single position gives 1.0
    (maximally concentrated). Thresholds follow the common antitrust-derived
    bands rescaled to 0..1.
    """
    if position_count == 0:
        return "empty"
    if hhi >= 0.25:
        return "highly_concentrated"
    if hhi >= 0.15:
        return "moderately_concentrated"
    return "diversified"


# ---------------------------------------------------------------------------
# get_cross_account_summary
# ---------------------------------------------------------------------------


def get_cross_account_summary_impl(_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate positions + balances across every linked account.

    Read-only derived computation. No mutation, no cache write. Fans out over
    the white-listed ``get_account_numbers`` (to discover account hashes) then
    ``get_account`` per account, and merges the results into a combined view:
    per-account share-of-total liquidation value and a symbol-level holdings
    roll-up de-duplicated across accounts.

    Handles the single-account, multi-account, and zero-account cases. If the
    account-numbers discovery call errors, the standard error payload is
    returned. Individual per-account fetch errors are recorded per account
    without failing the whole aggregation.
    """
    client = get_client()
    response = client.get_account_numbers()
    try:
        data = normalise_response(response)
    except SchwabApiError as exc:
        return {
            "ok": False,
            "error": {"status_code": exc.status_code, "reason": exc.reason, "request_id": exc.request_id},
            "_cache_status": "skipped:not-cached",
        }

    mappings = data if isinstance(data, list) else []
    account_hashes = [m.get("hashValue") for m in mappings if isinstance(m.get("hashValue"), str)]

    per_account: list[dict[str, Any]] = []
    symbol_rollup: dict[str, dict[str, Any]] = {}
    grand_market_value = 0.0
    grand_liquidation_value = 0.0
    grand_cash = 0.0

    for account_hash in account_hashes:
        securities_account, error = _fetch_securities_account(account_hash)
        if error is not None:
            per_account.append(
                {
                    "account_hash": account_hash,
                    "ok": False,
                    "error": error["error"],
                }
            )
            continue
        assert securities_account is not None  # narrowing for mypy

        positions = securities_account.get("positions") or []
        current = securities_account.get("currentBalances") or {}

        account_market_value = 0.0
        for position in positions:
            symbol = _position_symbol(position)
            market_value = _safe_float(position.get("marketValue"))
            quantity = _position_quantity(position)
            account_market_value += market_value

            bucket = symbol_rollup.setdefault(
                symbol,
                {"symbol": symbol, "total_quantity": 0.0, "total_market_value": 0.0, "account_count": 0},
            )
            bucket["total_quantity"] += quantity
            bucket["total_market_value"] += market_value
            bucket["account_count"] += 1

        liquidation_value = _safe_float(current.get("liquidationValue"))
        cash_balance = _safe_float(current.get("cashBalance"))
        grand_market_value += account_market_value
        grand_liquidation_value += liquidation_value
        grand_cash += cash_balance

        per_account.append(
            {
                "account_hash": account_hash,
                "ok": True,
                "position_count": len(positions),
                "market_value": account_market_value,
                "liquidation_value": liquidation_value,
                "cash_balance": cash_balance,
            }
        )

    # Per-account share of total liquidation value (computed after the grand
    # total is known so shares sum to ~100%).
    for entry in per_account:
        if entry.get("ok"):
            entry["liquidation_share_pct"] = (
                entry["liquidation_value"] / grand_liquidation_value * 100.0 if grand_liquidation_value else 0.0
            )

    merged_holdings = sorted(
        symbol_rollup.values(),
        key=lambda h: h["total_market_value"],
        reverse=True,
    )

    ok_accounts = [a for a in per_account if a.get("ok")]
    return {
        "ok": True,
        "account_count": len(account_hashes),
        "accounts_aggregated": len(ok_accounts),
        "accounts": per_account,
        "totals": {
            "total_market_value": grand_market_value,
            "total_liquidation_value": grand_liquidation_value,
            "total_cash_balance": grand_cash,
            "unique_symbol_count": len(merged_holdings),
        },
        "merged_holdings": merged_holdings,
        "_cache_status": "skipped:not-cached",
    }
