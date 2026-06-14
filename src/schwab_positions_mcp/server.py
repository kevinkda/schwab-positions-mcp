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

import logging  # noqa: E402
from typing import Any  # noqa: E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from . import __version__ as SERVER_VERSION  # noqa: E402
from .tools import account_numbers, accounts, analytics, meta, orders, positions, summary, transactions  # noqa: E402

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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
