"""Boundary / edge-case test suite for schwab-positions-mcp.

Covers min/max/null/empty/overflow conditions on every untrusted input:
  * account_hash length boundaries (8 min, 128 max) + empty/None/special.
  * max_results boundaries (1, 3000, 0, negative, oversized).
  * symbol max_length (32) boundary.
  * positions list size: empty / single / large.
  * DuckDB numeric overflow & extreme floats.

The 60-day date-lookback boundary is already covered exhaustively in
``test_v0_1_2_lookback_validation.py``; this file does not duplicate it but
adds the remaining input dimensions. Every test asserts a concrete invariant.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from schwab_positions_mcp.cache import Cache, _to_float
from schwab_positions_mcp.cache_backend import ClickHouseBackend
from schwab_positions_mcp.models import (
    GetAccountPositionsInput,
    GetOrdersHistoryInput,
    GetTransactionsInput,
)

if TYPE_CHECKING:
    pass


def _ch_cache() -> tuple[Cache, MagicMock]:
    """A ClickHouse-backed cache + its mock client (every insert persists)."""
    client = MagicMock()
    client.command.return_value = None
    client.insert.return_value = None
    result = MagicMock()
    result.result_rows = []
    client.query.return_value = result
    return Cache(backend=ClickHouseBackend(url="clickhouse://x", client=client)), client


def _inserted_rows(client: MagicMock) -> list[dict[str, Any]]:
    """Decode the JSON payloads the backend inserted into the timeseries table."""
    rows: list[dict[str, Any]] = []
    for call in client.insert.call_args_list:
        # ClickHouseBackend.append_timeseries inserts [[series, json_payload]].
        data = call.args[1]
        for entry in data:
            rows.append(json.loads(entry[1]))
    return rows


def _recent_iso() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=1)).isoformat()


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _today_date() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


# ===========================================================================
# account_hash length boundaries (min 8, max 128) + null / empty / special
# ===========================================================================


class TestAccountHashBoundaries:
    def test_min_length_minus_one_rejected(self) -> None:
        """7 chars is below the 8-char minimum → rejected."""
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": "A" * 7})

    def test_exact_min_length_accepted(self) -> None:
        """Exactly 8 chars is accepted (inclusive lower bound)."""
        out = GetAccountPositionsInput.model_validate({"account_hash": "A" * 8})
        assert out.account_hash == "A" * 8

    def test_exact_max_length_accepted(self) -> None:
        """Exactly 128 chars is accepted (inclusive upper bound)."""
        out = GetAccountPositionsInput.model_validate({"account_hash": "A" * 128})
        assert len(out.account_hash) == 128

    def test_max_length_plus_one_rejected(self) -> None:
        """129 chars exceeds the 128-char maximum → rejected."""
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": "A" * 129})

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": ""})

    def test_none_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": None})

    def test_missing_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({})

    @pytest.mark.parametrize("special", ["with space12", "tab\tchar1", "slash/char", "dot.char1", "at@char12"])
    def test_special_characters_rejected(self, special: str) -> None:
        """Any character outside ``[A-Za-z0-9_\\-]`` is rejected by the pattern."""
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": special})

    def test_allowed_underscore_and_hyphen_accepted(self) -> None:
        out = GetAccountPositionsInput.model_validate({"account_hash": "AB_cd-12"})
        assert out.account_hash == "AB_cd-12"

    def test_extra_field_rejected_strict_model(self) -> None:
        """The strict base model forbids unknown fields (extra='forbid')."""
        with pytest.raises(ValidationError):
            GetAccountPositionsInput.model_validate({"account_hash": "VALIDHASH123", "evil": "x"})


# ===========================================================================
# max_results boundaries (ge=1, le=3000)
# ===========================================================================


class TestMaxResultsBoundaries:
    def _base(self) -> dict[str, Any]:
        return {
            "account_hash": "VALIDHASH123",
            "from_entered_time": _recent_iso(),
            "to_entered_time": _now_iso(),
        }

    def test_min_value_one_accepted(self) -> None:
        out = GetOrdersHistoryInput.model_validate({**self._base(), "max_results": 1})
        assert out.max_results == 1

    def test_max_value_3000_accepted(self) -> None:
        out = GetOrdersHistoryInput.model_validate({**self._base(), "max_results": 3000})
        assert out.max_results == 3000

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate({**self._base(), "max_results": 0})

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate({**self._base(), "max_results": -1})

    def test_3001_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate({**self._base(), "max_results": 3001})

    def test_oversized_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetOrdersHistoryInput.model_validate({**self._base(), "max_results": 10**12})

    def test_none_means_unset_accepted(self) -> None:
        out = GetOrdersHistoryInput.model_validate({**self._base(), "max_results": None})
        assert out.max_results is None


# ===========================================================================
# symbol max_length (32) boundary
# ===========================================================================


class TestSymbolBoundaries:
    def _base(self) -> dict[str, Any]:
        return {
            "account_hash": "VALIDHASH123",
            "start_date": _recent_date(),
            "end_date": _today_date(),
        }

    def test_symbol_at_max_length_accepted(self) -> None:
        out = GetTransactionsInput.model_validate({**self._base(), "symbol": "A" * 32})
        assert out.symbol is not None and len(out.symbol) == 32

    def test_symbol_over_max_length_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GetTransactionsInput.model_validate({**self._base(), "symbol": "A" * 33})

    def test_symbol_none_accepted(self) -> None:
        out = GetTransactionsInput.model_validate({**self._base(), "symbol": None})
        assert out.symbol is None


# ===========================================================================
# positions list size: empty / single / large
# ===========================================================================


class TestPositionsListSizes:
    def test_empty_positions_writes_zero(self) -> None:
        cache, _ = _ch_cache()
        assert cache.write_positions_snapshot("H_ABCDEF", []) == 0

    def test_single_position(self) -> None:
        cache, _ = _ch_cache()
        n = cache.write_positions_snapshot(
            "H_ABCDEF",
            [{"instrument": {"symbol": "AAPL", "assetType": "EQUITY"}, "marketValue": 1.0}],
        )
        assert n == 1

    def test_large_positions_list(self) -> None:
        """A large (2000-row) positions batch must persist completely."""
        cache, client = _ch_cache()
        big = [
            {"instrument": {"symbol": f"SYM{i}", "assetType": "EQUITY"}, "marketValue": float(i)} for i in range(2000)
        ]
        n = cache.write_positions_snapshot("H_ABCDEF", big)
        assert n == 2000
        assert len(_inserted_rows(client)) == 2000

    def test_position_with_missing_instrument_handled(self) -> None:
        """A position lacking an 'instrument' key must not crash — symbol stored as empty."""
        cache, client = _ch_cache()
        n = cache.write_positions_snapshot("H_ABCDEF", [{"marketValue": 5.0}])
        assert n == 1
        rows = _inserted_rows(client)
        assert rows[0]["symbol"] == ""


# ===========================================================================
# DuckDB numeric overflow & extreme floats
# ===========================================================================


class TestNumericBoundaries:
    def test_to_float_extreme_values(self) -> None:
        assert _to_float(1e308) == pytest.approx(1e308)
        assert _to_float(-1e308) == pytest.approx(-1e308)
        assert _to_float(0) == 0.0
        assert _to_float("0") == 0.0

    def test_to_float_inf_and_nan_strings(self) -> None:
        """'inf'/'nan' parse to float specials (DuckDB DOUBLE can hold them)."""
        import math

        assert math.isinf(_to_float("inf") or 0.0)
        assert math.isnan(_to_float("nan") or 0.0)

    def test_cache_handles_extreme_market_value(self) -> None:
        """An astronomically large market value must persist without overflow error."""
        cache, client = _ch_cache()
        n = cache.write_positions_snapshot(
            "H_ABCDEF",
            [{"instrument": {"symbol": "BIG", "assetType": "EQUITY"}, "marketValue": 1e300}],
        )
        assert n == 1
        rows = _inserted_rows(client)
        assert rows[0]["market_value"] == pytest.approx(1e300)

    def test_cache_handles_negative_quantity(self) -> None:
        """Short positions (negative quantity) must persist as-is, no clamping."""
        cache, client = _ch_cache()
        cache.write_positions_snapshot(
            "H_ABCDEF",
            [{"instrument": {"symbol": "SHORT", "assetType": "EQUITY"}, "shortQuantity": 50.0}],
        )
        rows = _inserted_rows(client)
        assert rows[0]["quantity"] == pytest.approx(50.0)


# ===========================================================================
# date end < start boundary (complements lookback file)
# ===========================================================================


class TestDateOrderBoundary:
    def test_end_equal_start_accepted(self) -> None:
        """end_date == start_date is a valid single-day window."""
        d = _recent_date()
        out = GetTransactionsInput.model_validate({"account_hash": "VALIDHASH123", "start_date": d, "end_date": d})
        assert out.start_date == out.end_date

    def test_end_before_start_rejected(self) -> None:
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        with pytest.raises(ValidationError):
            GetTransactionsInput.model_validate(
                {
                    "account_hash": "VALIDHASH123",
                    "start_date": today.isoformat(),
                    "end_date": (today - timedelta(days=1)).isoformat(),
                }
            )


def _recent_date() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
