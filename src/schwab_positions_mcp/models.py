"""Pydantic v2 input schemas for schwab-positions-mcp tools.

Each schema validates MCP tool arguments before they reach the
Schwab API client. Field constraints mirror Schwab Trader API
documentation (where the upstream enforces them) so we fail fast
client-side instead of paying a round-trip on bad inputs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Schwab Trader API hard limit on order history lookback window.
_ORDERS_LOOKBACK_DAYS = 60
# Schwab Trader API hard limit on transactions history lookback window.
# (Some Schwab docs mention 90 days; we stay conservative at 60 to match the
# documented orders behavior — bump only after verifying upstream.)
_TRANSACTIONS_LOOKBACK_DAYS = 60


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

# Schwab "encrypted" account hash — alphanumeric. Length floats around 32 in
# the Trader API; we keep the lower bound generous to avoid breaking on a
# Schwab-side change while still rejecting obvious garbage.
_AccountHashStr = Annotated[
    str,
    Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_\-]+$",
        description="Encrypted account hash from /accounts/accountNumbers.",
    ),
]


_AccountFields = Literal["positions"]

_OrderStatus = Literal[
    "AWAITING_PARENT_ORDER",
    "AWAITING_CONDITION",
    "AWAITING_STOP_CONDITION",
    "AWAITING_MANUAL_REVIEW",
    "ACCEPTED",
    "AWAITING_UR_OUT",
    "PENDING_ACTIVATION",
    "QUEUED",
    "WORKING",
    "REJECTED",
    "PENDING_CANCEL",
    "CANCELED",
    "PENDING_REPLACE",
    "REPLACED",
    "FILLED",
    "EXPIRED",
    "NEW",
    "AWAITING_RELEASE_TIME",
    "PENDING_ACKNOWLEDGEMENT",
    "PENDING_RECALL",
    "UNKNOWN",
]

_TransactionType = Literal[
    "TRADE",
    "RECEIVE_AND_DELIVER",
    "DIVIDEND_OR_INTEREST",
    "ACH_RECEIPT",
    "ACH_DISBURSEMENT",
    "CASH_RECEIPT",
    "CASH_DISBURSEMENT",
    "ELECTRONIC_FUND",
    "WIRE_OUT",
    "WIRE_IN",
    "JOURNAL",
    "MEMORANDUM",
    "MARGIN_CALL",
    "MONEY_MARKET",
    "SMA_ADJUSTMENT",
]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Strict base — reject unknown fields and freeze on construction."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------------
# Tool input schemas
# ---------------------------------------------------------------------------


class GetAccountsInput(_StrictModel):
    """Input for ``get_accounts``.

    fields=["positions"] is the only supported expansion the Schwab API
    documents for this endpoint.
    """

    fields: list[_AccountFields] | None = Field(
        default=None,
        description="Optional list of expansion fields, e.g. ['positions'].",
    )


class GetAccountPositionsInput(_StrictModel):
    """Input for ``get_account_positions``."""

    account_hash: _AccountHashStr


class GetOrdersHistoryInput(_StrictModel):
    """Input for ``get_orders_history``.

    Schwab Trader API caps ``from_entered_time`` lookback at 60 days.
    """

    account_hash: _AccountHashStr
    from_entered_time: datetime = Field(
        description="Lower bound (inclusive) on enteredTime. Must be timezone-aware.",
    )
    to_entered_time: datetime = Field(
        description="Upper bound (inclusive) on enteredTime. Must be timezone-aware.",
    )
    max_results: int | None = Field(
        default=None,
        ge=1,
        le=3000,
        description="Maximum number of orders to return (Schwab caps at 3000).",
    )
    status: _OrderStatus | None = Field(
        default=None,
        description="Optional Schwab order status filter.",
    )

    @field_validator("from_entered_time", "to_entered_time")
    @classmethod
    def _require_tzaware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (use UTC).")
        return value.astimezone(UTC)

    @field_validator("from_entered_time")
    @classmethod
    def _within_orders_lookback(cls, value: datetime) -> datetime:
        """Schwab Trader API caps from_entered_time at 60 days lookback.

        Note on boundary: strict ``<`` comparison — a value exactly
        ``now - 60 days`` is accepted by Pydantic. If Schwab itself
        enforces ``≤ 60 days``, the API may still 400; bump to ``<=``
        if real-world testing surfaces that case.

        Validator order matters: ``_require_tzaware`` runs first
        (declaration order under Pydantic v2), so ``value`` here is
        guaranteed tz-aware UTC by the time we compare it.
        """
        cutoff = datetime.now(UTC) - timedelta(days=_ORDERS_LOOKBACK_DAYS)
        if value < cutoff:
            raise ValueError(
                f"from_entered_time must be within last {_ORDERS_LOOKBACK_DAYS} days "
                f"(>= {cutoff.isoformat()}). Got {value.isoformat()}. "
                f"Schwab Trader API caps order history lookback at "
                f"{_ORDERS_LOOKBACK_DAYS} days."
            )
        return value


class GetTransactionsInput(_StrictModel):
    """Input for ``get_transactions``."""

    account_hash: _AccountHashStr
    start_date: date = Field(description="Lower bound (inclusive) on transaction date.")
    end_date: date = Field(description="Upper bound (inclusive) on transaction date.")
    types: list[_TransactionType] | None = Field(
        default=None,
        description="Optional Schwab transaction-type filter.",
    )
    symbol: str | None = Field(
        default=None,
        max_length=32,
        description="Optional symbol filter.",
    )

    @field_validator("end_date")
    @classmethod
    def _end_after_start(cls, value: date, info: ValidationInfo) -> date:
        start = info.data.get("start_date") if info.data else None
        if start and value < start:
            raise ValueError("end_date must be >= start_date.")
        return value

    @field_validator("start_date")
    @classmethod
    def _within_transactions_lookback(cls, value: date) -> date:
        """Schwab Trader API caps start_date at 60 days lookback.

        Mirrors GetOrdersHistoryInput._within_orders_lookback. Operates
        on ``date`` (not ``datetime``); we compute the cutoff from
        ``datetime.now(UTC).date()`` for parity with order-side logic.
        """
        cutoff = (datetime.now(UTC) - timedelta(days=_TRANSACTIONS_LOOKBACK_DAYS)).date()
        if value < cutoff:
            raise ValueError(
                f"start_date must be within last {_TRANSACTIONS_LOOKBACK_DAYS} days "
                f"(>= {cutoff.isoformat()}). Got {value.isoformat()}. "
                f"Schwab Trader API caps transaction history lookback at "
                f"{_TRANSACTIONS_LOOKBACK_DAYS} days."
            )
        return value


class GetAccountSummaryInput(_StrictModel):
    """Input for ``get_account_summary`` (positions + balances aggregate)."""

    account_hash: _AccountHashStr


class GetPnlAnalysisInput(_StrictModel):
    """Input for ``get_pnl_analysis`` (read-only derived P&L analytics).

    Pure derived computation over the read-only positions + transactions
    feeds — no mutation, no cache write. ``account_hash`` is the only
    argument; ``realized_lookback_days`` bounds the transaction window used
    to derive realized P&L (Schwab caps history at 60 days; we clamp here
    so the derived call stays inside the same boundary as get_transactions).
    """

    account_hash: _AccountHashStr
    realized_lookback_days: int = Field(
        default=_TRANSACTIONS_LOOKBACK_DAYS,
        ge=1,
        le=_TRANSACTIONS_LOOKBACK_DAYS,
        description=(
            "Window (days back from today) over which SELL trades are scanned "
            f"to derive realized P&L. 1..{_TRANSACTIONS_LOOKBACK_DAYS} "
            "(Schwab history cap)."
        ),
    )


class GetConcentrationAnalysisInput(_StrictModel):
    """Input for ``get_concentration_analysis`` (read-only derived analytics).

    Pure derived computation over the read-only positions feed — no mutation,
    no cache write. ``top_n`` controls how many of the largest holdings are
    surfaced in the ``top_holdings`` breakdown.
    """

    account_hash: _AccountHashStr
    top_n: int = Field(
        default=5,
        ge=1,
        le=50,
        description="How many largest holdings to surface in top_holdings (1..50).",
    )
