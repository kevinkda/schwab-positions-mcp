"""Pydantic v2 input-schema boundary tests for ``schwab_positions_mcp.models``."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from schwab_positions_mcp.models import (
    GetAccountPositionsInput,
    GetAccountsInput,
    GetAccountSummaryInput,
    GetOrdersHistoryInput,
    GetTransactionsInput,
)

VALID_HASH = "ACCT_HASH_AAAAAAAAAAAA"


# ---------------------------------------------------------------------------
# GetAccountsInput
# ---------------------------------------------------------------------------


class TestGetAccountsInput:
    def test_default_no_fields(self) -> None:
        m = GetAccountsInput.model_validate({})
        assert m.fields is None

    def test_explicit_none_fields(self) -> None:
        m = GetAccountsInput.model_validate({"fields": None})
        assert m.fields is None

    def test_positions_field_accepted(self) -> None:
        m = GetAccountsInput.model_validate({"fields": ["positions"]})
        assert m.fields == ["positions"]

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountsInput.model_validate({"fields": ["something_else"]})

    def test_extra_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountsInput.model_validate({"fields": None, "stray": 1})


# ---------------------------------------------------------------------------
# GetAccountPositionsInput
# ---------------------------------------------------------------------------


class TestGetAccountPositionsInput:
    def test_account_hash_required(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({})

    def test_valid_hash(self) -> None:
        m = GetAccountPositionsInput.model_validate({"account_hash": VALID_HASH})
        assert m.account_hash == VALID_HASH

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": ""})

    def test_too_short_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": "abc"})

    def test_invalid_chars_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": "BAD HASH WITH SPACES!!"})

    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": "A" * 200})


# ---------------------------------------------------------------------------
# GetOrdersHistoryInput
# ---------------------------------------------------------------------------


class TestGetOrdersHistoryInput:
    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "account_hash": VALID_HASH,
            "from_entered_time": datetime(2026, 5, 1, tzinfo=UTC),
            "to_entered_time": datetime(2026, 5, 28, tzinfo=UTC),
        }
        base.update(overrides)
        return base

    def test_minimal_valid_payload(self) -> None:
        m = GetOrdersHistoryInput.model_validate(self._payload())
        assert m.account_hash == VALID_HASH
        assert m.status is None
        assert m.max_results is None

    def test_status_literal_accepted(self) -> None:
        m = GetOrdersHistoryInput.model_validate(self._payload(status="FILLED"))
        assert m.status == "FILLED"

    def test_status_literal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate(self._payload(status="NOT_A_REAL_STATUS"))

    def test_max_results_lower_bound(self) -> None:
        m = GetOrdersHistoryInput.model_validate(self._payload(max_results=1))
        assert m.max_results == 1

    def test_max_results_upper_bound(self) -> None:
        m = GetOrdersHistoryInput.model_validate(self._payload(max_results=3000))
        assert m.max_results == 3000

    def test_max_results_below_lower_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate(self._payload(max_results=0))

    def test_max_results_above_upper_bound_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate(self._payload(max_results=3001))

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate(
                self._payload(from_entered_time=datetime(2026, 5, 1)),
            )

    def test_status_optional(self) -> None:
        m = GetOrdersHistoryInput.model_validate(self._payload(status=None))
        assert m.status is None


# ---------------------------------------------------------------------------
# GetTransactionsInput
# ---------------------------------------------------------------------------


class TestGetTransactionsInput:
    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "account_hash": VALID_HASH,
            "start_date": date(2026, 5, 1),
            "end_date": date(2026, 5, 28),
        }
        base.update(overrides)
        return base

    def test_minimal_valid(self) -> None:
        m = GetTransactionsInput.model_validate(self._payload())
        assert m.types is None
        assert m.symbol is None

    def test_type_literal_accepted(self) -> None:
        m = GetTransactionsInput.model_validate(self._payload(types=["TRADE"]))
        assert m.types == ["TRADE"]

    def test_type_literal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetTransactionsInput.model_validate(self._payload(types=["NOT_REAL"]))

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetTransactionsInput.model_validate(
                self._payload(start_date=date(2026, 5, 28), end_date=date(2026, 5, 1)),
            )

    def test_symbol_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetTransactionsInput.model_validate(self._payload(symbol="A" * 33))


# ---------------------------------------------------------------------------
# GetAccountSummaryInput
# ---------------------------------------------------------------------------


class TestGetAccountSummaryInput:
    def test_account_hash_required(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountSummaryInput.model_validate({})

    def test_valid_hash(self) -> None:
        m = GetAccountSummaryInput.model_validate({"account_hash": VALID_HASH})
        assert m.account_hash == VALID_HASH

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountSummaryInput.model_validate({"account_hash": VALID_HASH, "stray": True})
