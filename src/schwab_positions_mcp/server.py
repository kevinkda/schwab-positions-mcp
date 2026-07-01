"""schwab-positions-mcp MCP server entrypoint.

READ-ONLY MODE — no trade endpoints exposed.

The first thing this module does at import time is bootstrap ``.env`` so that
``SCHWAB_API_KEY`` / ``SCHWAB_APP_SECRET`` / ``SCHWAB_CALLBACK_URL`` are
present before any tool is called.

We then emit a startup WARNING declaring the read-only contract. Layer 2 of
the 5-layer boundary (see ``docs/SECURITY.md``).
"""

from __future__ import annotations

from . import bootstrap

bootstrap.bootstrap_dotenv()

import argparse  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from typing import Any  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from . import __version__ as SERVER_VERSION  # noqa: E402
from .tools import (  # noqa: E402
    account_numbers,
    accounts,
    analytics,
    meta,
    order_detail,
    orders,
    positions,
    preferences,
    summary,
    transaction_detail,
    transactions,
)

logger = logging.getLogger(__name__)
logger.warning("schwab-positions-mcp starting in READ-ONLY MODE. No trade endpoints exposed. See docs/SECURITY.md.")

mcp: FastMCP = FastMCP("schwab-positions-mcp")
mcp._mcp_server.version = SERVER_VERSION


# ---------------------------------------------------------------------------
# Tool registrations — each delegates to the ``*_impl`` function in
# ``schwab_positions_mcp.tools.<module>``. Keep this file the single source of
# truth for the MCP tool surface so the read-only contract is auditable in
# one place.
# ---------------------------------------------------------------------------


@mcp.tool(
    name="get_accounts",
    description="List all linked Schwab accounts (read-only). Optional fields=['positions'] expansion.",
)
def get_accounts(fields: list[str] | None = None) -> dict[str, Any]:
    return accounts.get_accounts_impl({"fields": fields})


@mcp.tool(
    name="get_account_numbers",
    description=(
        "Return the mapping of plaintext accountNumber to encrypted "
        "account_hash (Schwab hashValue) for every linked account. The "
        "encrypted hash is required by all other tools that take an "
        "account_hash argument (get_account_positions, get_account_summary, "
        "get_orders_history, get_transactions)."
    ),
)
def get_account_numbers() -> dict[str, Any]:
    return account_numbers.get_account_numbers_impl()


@mcp.tool(
    name="get_account_positions",
    description="Return one account's holdings and balances; persists a positions snapshot to local DuckDB.",
)
def get_account_positions(account_hash: str) -> dict[str, Any]:
    return positions.get_account_positions_impl({"account_hash": account_hash})


@mcp.tool(
    name="get_orders_history",
    description=(
        "Return historical orders for an account between two timezone-aware "
        "datetimes (Schwab caps lookback at 60 days)."
    ),
)
def get_orders_history(
    account_hash: str,
    from_entered_time: str,
    to_entered_time: str,
    max_results: int | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    return orders.get_orders_history_impl(
        {
            "account_hash": account_hash,
            "from_entered_time": from_entered_time,
            "to_entered_time": to_entered_time,
            "max_results": max_results,
            "status": status,
        }
    )


@mcp.tool(
    name="get_transactions",
    description="Return transactions for an account between two ISO-format dates.",
)
def get_transactions(
    account_hash: str,
    start_date: str,
    end_date: str,
    types: list[str] | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    return transactions.get_transactions_impl(
        {
            "account_hash": account_hash,
            "start_date": start_date,
            "end_date": end_date,
            "types": types,
            "symbol": symbol,
        }
    )


@mcp.tool(
    name="get_user_preferences",
    description=(
        "Return the account user-preference settings (read-only): default "
        "account, account nicknames, and streamer routing metadata. No "
        "arguments. No mutation, no cache write."
    ),
)
def get_user_preferences() -> dict[str, Any]:
    return preferences.get_user_preferences_impl()


@mcp.tool(
    name="get_order_detail",
    description=(
        "Read a single order's full detail by numeric order_id for one "
        "account (read-only). Retrieves an existing order's status / legs / "
        "fills — it does NOT place, cancel, or replace an order. No cache write."
    ),
)
def get_order_detail(account_hash: str, order_id: int) -> dict[str, Any]:
    return order_detail.get_order_detail_impl({"account_hash": account_hash, "order_id": order_id})


@mcp.tool(
    name="get_transaction_detail",
    description=(
        "Read a single historical transaction's detail by transaction_id for "
        "one account (read-only). Retrieves an existing settled/booked "
        "transaction record — no money moves, no mutation. No cache write."
    ),
)
def get_transaction_detail(account_hash: str, transaction_id: str) -> dict[str, Any]:
    return transaction_detail.get_transaction_detail_impl(
        {"account_hash": account_hash, "transaction_id": transaction_id}
    )


@mcp.tool(
    name="get_account_summary",
    description="Aggregate one account: position count, total market value, P&L, cash, balances.",
)
def get_account_summary(account_hash: str) -> dict[str, Any]:
    return summary.get_account_summary_impl({"account_hash": account_hash})


@mcp.tool(
    name="get_pnl_analysis",
    description=(
        "Read-only derived P&L analytics for one account: per-position cost "
        "basis / unrealized P&L / unrealized % (cost-basis method: AVERAGE "
        "COST, since the positions feed exposes only averagePrice — no per-lot "
        "records for FIFO), a transaction-derived realized P&L over the "
        "lookback window, and a portfolio roll-up. Pure computation; no "
        "mutation, no cache write."
    ),
)
def get_pnl_analysis(account_hash: str, realized_lookback_days: int = 60) -> dict[str, Any]:
    return analytics.get_pnl_analysis_impl(
        {"account_hash": account_hash, "realized_lookback_days": realized_lookback_days}
    )


@mcp.tool(
    name="get_concentration_analysis",
    description=(
        "Read-only derived concentration analytics for one account: top-N "
        "weights, Herfindahl-Hirschman Index (HHI), max single-position "
        "weight, and asset-type exposure (sector exposure is N/A — the Schwab "
        "positions feed has no GICS sector field). Pure computation; no "
        "mutation, no cache write."
    ),
)
def get_concentration_analysis(account_hash: str, top_n: int = 5) -> dict[str, Any]:
    return analytics.get_concentration_analysis_impl({"account_hash": account_hash, "top_n": top_n})


@mcp.tool(
    name="get_cross_account_summary",
    description=(
        "Read-only derived cross-account aggregation: discovers all linked "
        "accounts via get_account_numbers, then merges positions + balances "
        "into a combined view with per-account share-of-total and symbol-level "
        "de-duplication. Pure computation; no mutation, no cache write."
    ),
)
def get_cross_account_summary() -> dict[str, Any]:
    return analytics.get_cross_account_summary_impl()


@mcp.tool(
    name="health_check",
    description="Lightweight readiness check; reports credential / token presence without contacting Schwab.",
)
def health_check() -> dict[str, Any]:
    return meta.health_check_impl()


@mcp.tool(
    name="get_server_info",
    description="Server metadata — version, platform, read-only declaration, tool list.",
)
def get_server_info() -> dict[str, Any]:
    return meta.get_server_info_impl()


def main(argv: list[str] | None = None) -> None:
    """Entry point supporting both stdio (default) and streamable-http for gateway use.

    Examples:
      python -m schwab_positions_mcp                  # stdio (Claude Desktop etc.)
      schwab-positions-mcp --http --port 3470         # HTTP for Grok App / gateway
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="schwab-positions-mcp",
        description="Schwab positions MCP (read-only). stdio by default; use --http for remote/Grok connectors.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use streamable-http transport (binds to --host/--port) instead of stdio.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface to bind when using --http (use 0.0.0.0 for gateway).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port when using --http transport.",
    )

    args = parser.parse_args(argv)

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
