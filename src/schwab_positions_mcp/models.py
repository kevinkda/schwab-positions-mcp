"""Pydantic v2 input schemas for schwab-positions-mcp tools.

Each schema validates MCP tool arguments before they reach the
Schwab API client. Field constraints mirror Schwab Trader API
documentation (where the upstream enforces them) so we fail fast
client-side instead of paying a round-trip on bad inputs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

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
        return value.astimezone(timezone.utc)


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


class GetAccountSummaryInput(_StrictModel):
    """Input for ``get_account_summary`` (positions + balances aggregate)."""

    account_hash: _AccountHashStr
