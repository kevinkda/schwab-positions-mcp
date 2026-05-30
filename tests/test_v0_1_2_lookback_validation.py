"""Tests for v0.1.2 60-day lookback validation.

Covers:
- ``GetOrdersHistoryInput.from_entered_time`` 60-day lookback
- ``GetTransactionsInput.start_date`` 60-day lookback
- Boundary case (exactly 60 days back)
- Validator-ordering invariant (``_require_tzaware`` runs before
  ``_within_orders_lookback``).
- Error-message ergonomics for LLM agents (cutoff + input value
  surfaced verbatim).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from schwab_positions_mcp.models import (
    GetOrdersHistoryInput,
    GetTransactionsInput,
)

ACCOUNT_HASH = "x" * 32


# ---------------------------------------------------------------------------
# GetOrdersHistoryInput — from_entered_time 60-day lookback
# ---------------------------------------------------------------------------


class TestOrdersLookback:
    def test_30_days_back_passes(self) -> None:
        from_time = datetime.now(UTC) - timedelta(days=30)
        to_time = datetime.now(UTC)
        result = GetOrdersHistoryInput(
            account_hash=ACCOUNT_HASH,
            from_entered_time=from_time,
            to_entered_time=to_time,
        )
        # Validator may astimezone-normalise to UTC but the moment is preserved.
        assert result.from_entered_time == from_time

    def test_59_days_back_passes(self) -> None:
        from_time = datetime.now(UTC) - timedelta(days=59)
        to_time = datetime.now(UTC)
        result = GetOrdersHistoryInput(
            account_hash=ACCOUNT_HASH,
            from_entered_time=from_time,
            to_entered_time=to_time,
        )
        assert result.from_entered_time == from_time

    def test_60_days_back_passes_at_boundary(self) -> None:
        """Strict ``<`` comparison: exactly 60 days is accepted.

        We add 1-second buffer to defeat the in-flight clock advance
        between constructing ``from_time`` and the validator running.
        """
        from_time = datetime.now(UTC) - timedelta(days=60) + timedelta(seconds=1)
        to_time = datetime.now(UTC)
        result = GetOrdersHistoryInput(
            account_hash=ACCOUNT_HASH,
            from_entered_time=from_time,
            to_entered_time=to_time,
        )
        assert result.from_entered_time == from_time

    def test_61_days_back_rejected(self) -> None:
        from_time = datetime.now(UTC) - timedelta(days=61)
        to_time = datetime.now(UTC)
        with pytest.raises(ValueError, match="60 days"):
            GetOrdersHistoryInput(
                account_hash=ACCOUNT_HASH,
                from_entered_time=from_time,
                to_entered_time=to_time,
            )

    def test_90_days_back_rejected(self) -> None:
        from_time = datetime.now(UTC) - timedelta(days=90)
        to_time = datetime.now(UTC)
        with pytest.raises(ValueError, match="60 days"):
            GetOrdersHistoryInput(
                account_hash=ACCOUNT_HASH,
                from_entered_time=from_time,
                to_entered_time=to_time,
            )

    def test_error_message_includes_cutoff_iso(self) -> None:
        from_time = datetime.now(UTC) - timedelta(days=120)
        to_time = datetime.now(UTC)
        with pytest.raises(ValueError) as excinfo:
            GetOrdersHistoryInput(
                account_hash=ACCOUNT_HASH,
                from_entered_time=from_time,
                to_entered_time=to_time,
            )
        msg = str(excinfo.value)
        assert "must be within last 60 days" in msg
        # Cutoff carries an explicit ISO marker (>= ...).
        assert ">=" in msg

    def test_error_message_includes_input_value(self) -> None:
        from_time = datetime(2024, 1, 1, tzinfo=UTC)
        to_time = datetime.now(UTC)
        with pytest.raises(ValueError) as excinfo:
            GetOrdersHistoryInput(
                account_hash=ACCOUNT_HASH,
                from_entered_time=from_time,
                to_entered_time=to_time,
            )
        msg = str(excinfo.value)
        assert "Got " in msg
        assert "2024-01-01" in msg

    def test_naive_datetime_still_rejected_first(self) -> None:
        """``_require_tzaware`` precedes ``_within_orders_lookback``.

        A naive datetime that is also out-of-window must surface the
        tz-awareness error (declaration order under Pydantic v2), not
        the lookback error — otherwise we'd be comparing naive to
        aware datetimes and crashing with a TypeError instead of a
        clean ValueError.
        """
        naive_old = datetime(2024, 1, 1)  # intentional naive datetime (DTZ not enabled)
        to_time = datetime.now(UTC)
        with pytest.raises(ValueError, match="timezone-aware"):
            GetOrdersHistoryInput(
                account_hash=ACCOUNT_HASH,
                from_entered_time=naive_old,
                to_entered_time=to_time,
            )


# ---------------------------------------------------------------------------
# GetTransactionsInput — start_date 60-day lookback
# ---------------------------------------------------------------------------


class TestTransactionsLookback:
    def test_30_days_back_passes(self) -> None:
        today = datetime.now(UTC).date()
        result = GetTransactionsInput(
            account_hash=ACCOUNT_HASH,
            start_date=today - timedelta(days=30),
            end_date=today,
        )
        assert result.start_date == today - timedelta(days=30)

    def test_60_days_back_at_boundary(self) -> None:
        """Strict ``<`` comparison, exactly 60 days back is accepted."""
        today = datetime.now(UTC).date()
        result = GetTransactionsInput(
            account_hash=ACCOUNT_HASH,
            start_date=today - timedelta(days=60),
            end_date=today,
        )
        assert result.start_date == today - timedelta(days=60)

    def test_61_days_back_rejected(self) -> None:
        today = datetime.now(UTC).date()
        with pytest.raises(ValueError, match="60 days"):
            GetTransactionsInput(
                account_hash=ACCOUNT_HASH,
                start_date=today - timedelta(days=61),
                end_date=today,
            )

    def test_90_days_back_rejected(self) -> None:
        today = datetime.now(UTC).date()
        with pytest.raises(ValueError, match="60 days"):
            GetTransactionsInput(
                account_hash=ACCOUNT_HASH,
                start_date=today - timedelta(days=90),
                end_date=today,
            )

    def test_error_includes_cutoff(self) -> None:
        today = datetime.now(UTC).date()
        with pytest.raises(ValueError) as excinfo:
            GetTransactionsInput(
                account_hash=ACCOUNT_HASH,
                start_date=today - timedelta(days=180),
                end_date=today,
            )
        msg = str(excinfo.value)
        assert "must be within last 60 days" in msg
        assert ">=" in msg
        assert "Got " in msg

    def test_end_before_start_still_caught_after_lookback(self) -> None:
        """``_end_after_start`` and ``_within_transactions_lookback`` cohabit.

        The end-before-start validator targets ``end_date``, the lookback
        validator targets ``start_date``. A doubly-bad input (end<start
        AND start out-of-window) must still raise — exactly which one
        wins is a Pydantic implementation detail; we just assert that
        ValidationError is raised, not which of the two messages is
        surfaced.
        """
        today = datetime.now(UTC).date()
        with pytest.raises(ValueError):
            GetTransactionsInput(
                account_hash=ACCOUNT_HASH,
                start_date=today - timedelta(days=120),
                end_date=today - timedelta(days=121),
            )
