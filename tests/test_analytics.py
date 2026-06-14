"""Unit tests for the v0.2.1 read-only derived analytics tools.

Covers ``get_pnl_analysis``, ``get_concentration_analysis``, and
``get_cross_account_summary`` with normal / boundary / error paths, plus
the cost-basis correctness checks and cross-account edge cases
(single / multi / empty account).

These use the same stubbed ``ReadOnlySchwabClient`` harness as
``test_tools_unit.py`` (the underlying schwab-py client is a ``MagicMock``;
white-listed read methods get fake ``httpx.Response``-shaped objects). No
real network and no cache writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp.tools import analytics

VALID_HASH = "ACCT_HASH_AAAAAAAAAAAA"
VALID_HASH_B = "ACCT_HASH_BBBBBBBBBBBB"


def _resp(status: int, payload: Any = None, request_id: str | None = None) -> MagicMock:
    r = MagicMock(name=f"Response[{status}]")
    r.status_code = status
    r.headers = {"Schwab-Client-CorrelId": request_id} if request_id else {}
    r.json.return_value = payload
    r.text = "" if payload is None else str(payload)
    return r


def _positions_payload(positions: list[dict[str, Any]], balances: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "securitiesAccount": {
            "accountNumber": "X",
            "positions": positions,
            "currentBalances": balances or {"cashBalance": 100.0, "liquidationValue": 1000.0, "currency": "USD"},
        }
    }


# AAPL: 10 sh @ 150 cost (1500) → mv 1700 → +200 (+13.33%)
# MSFT: 5 sh @ 300 cost (1500) → mv 1600 → +100 (+6.67%)
_TWO_POSITIONS = [
    {
        "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
        "longQuantity": 10.0,
        "averagePrice": 150.0,
        "marketValue": 1700.0,
    },
    {
        "instrument": {"symbol": "MSFT", "assetType": "EQUITY"},
        "longQuantity": 5.0,
        "averagePrice": 300.0,
        "marketValue": 1600.0,
    },
]


# ---------------------------------------------------------------------------
# get_pnl_analysis
# ---------------------------------------------------------------------------


class TestGetPnlAnalysis:
    def test_computes_average_cost_pnl(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["cost_basis_method"] == "average_cost"
        aapl = next(p for p in out["positions"] if p["symbol"] == "AAPL")
        assert aapl["cost_basis"] == pytest.approx(1500.0)
        assert aapl["unrealized_pl"] == pytest.approx(200.0)
        assert aapl["unrealized_pct"] == pytest.approx(13.3333, abs=0.01)
        port = out["portfolio"]
        assert port["total_cost_basis"] == pytest.approx(3000.0)
        assert port["total_market_value"] == pytest.approx(3300.0)
        assert port["total_unrealized_pl"] == pytest.approx(300.0)
        assert port["total_unrealized_pct"] == pytest.approx(10.0, abs=0.01)

    def test_realized_pl_from_sell_trades(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        # One SELL (positive net cash) + one BUY (negative) → only SELL counts.
        mock_schwab_client.get_transactions.return_value = _resp(
            200,
            [
                {"type": "TRADE", "netAmount": 500.0},
                {"type": "TRADE", "netAmount": -200.0},
            ],
        )
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        port = out["portfolio"]
        assert port["realized_pl"] == pytest.approx(500.0)
        assert port["realized_trade_count"] == 1
        assert port["realized_pl_available"] is True

    def test_realized_pl_unavailable_on_txn_error(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        mock_schwab_client.get_transactions.return_value = _resp(429)
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        # Unrealized block still returns; realized degrades gracefully.
        assert out["ok"] is True
        assert out["portfolio"]["realized_pl"] is None
        assert out["portfolio"]["realized_pl_available"] is False

    def test_lookback_passed_to_transactions(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH, "realized_lookback_days": 30})
        kwargs = mock_schwab_client.get_transactions.call_args.kwargs
        assert kwargs["transaction_types"] == ["TRADE"]

    def test_empty_positions_edge_case(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload([]))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        assert out["portfolio"]["position_count"] == 0
        assert out["portfolio"]["total_cost_basis"] == 0.0
        assert out["portfolio"]["total_unrealized_pct"] is None

    def test_zero_cost_basis_position_pct_none(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        # averagePrice 0 → cost_basis 0 → pct must be None (no div-by-zero).
        positions = [
            {
                "instrument": {"symbol": "FREE", "assetType": "EQUITY"},
                "longQuantity": 3.0,
                "averagePrice": 0.0,
                "marketValue": 30.0,
            }
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        pos = out["positions"][0]
        assert pos["cost_basis"] == 0.0
        assert pos["unrealized_pct"] is None

    def test_short_position_negative_quantity(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        positions = [
            {
                "instrument": {"symbol": "SHRT", "assetType": "EQUITY"},
                "shortQuantity": 4.0,
                "averagePrice": 50.0,
                "marketValue": -180.0,
            }
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        pos = out["positions"][0]
        assert pos["quantity"] == pytest.approx(-4.0)
        # cost basis uses abs(qty): 4 * 50 = 200; mv -180 → unrealized = -180 - 200 = -380.
        assert pos["cost_basis"] == pytest.approx(200.0)
        assert pos["unrealized_pl"] == pytest.approx(-380.0)

    def test_garbage_numeric_field_coerced_to_zero(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        # marketValue is non-numeric → _safe_float junk branch (→ 0.0).
        positions = [
            {
                "instrument": {"symbol": "JUNK", "assetType": "EQUITY"},
                "longQuantity": 2.0,
                "averagePrice": 10.0,
                "marketValue": "not-a-number",
            }
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        pos = out["positions"][0]
        assert pos["market_value"] == 0.0
        assert pos["cost_basis"] == pytest.approx(20.0)
        assert pos["unrealized_pl"] == pytest.approx(-20.0)

    def test_missing_instrument_symbol_defaults_unknown(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        positions = [{"longQuantity": 1.0, "averagePrice": 10.0, "marketValue": 12.0}]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["positions"][0]["symbol"] == "UNKNOWN"
        assert out["positions"][0]["asset_type"] == "UNKNOWN"

    def test_invalid_hash_raises_validation(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            analytics.get_pnl_analysis_impl({"account_hash": "x"})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_lookback_out_of_range_raises_validation(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH, "realized_lookback_days": 999})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(401)
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "refresh_token_expired"

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(429)
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(503)
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_does_not_write_cache(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])
        out = analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        assert out["_cache_status"] == "skipped:not-cached"


# ---------------------------------------------------------------------------
# get_concentration_analysis
# ---------------------------------------------------------------------------


class TestGetConcentrationAnalysis:
    def test_computes_weights_and_hhi(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is True
        conc = out["concentration"]
        assert conc["position_count"] == 2
        assert conc["total_abs_market_value"] == pytest.approx(3300.0)
        # AAPL weight 1700/3300 ≈ 51.515%, MSFT 1600/3300 ≈ 48.485%.
        assert conc["max_position_weight_pct"] == pytest.approx(51.515, abs=0.01)
        # HHI = 0.51515^2 + 0.48485^2 ≈ 0.50046.
        assert conc["hhi"] == pytest.approx(0.50046, abs=0.001)
        assert conc["top_n_weight_pct"] == pytest.approx(100.0, abs=0.01)

    def test_top_n_truncates(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH, "top_n": 1})
        conc = out["concentration"]
        assert len(conc["top_holdings"]) == 1
        # Largest is AAPL (1700).
        assert conc["top_holdings"][0]["symbol"] == "AAPL"
        assert conc["top_n_weight_pct"] == pytest.approx(51.515, abs=0.01)

    def test_asset_type_exposure_and_sector_na(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        positions = [
            {
                "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                "longQuantity": 10.0,
                "marketValue": 1000.0,
            },
            {
                "instrument": {"symbol": "SPY 250620C", "assetType": "OPTION"},
                "longQuantity": 1.0,
                "marketValue": 250.0,
            },
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        conc = out["concentration"]
        assert conc["sector_exposure"] == "N/A"
        assert set(conc["asset_type_exposure"].keys()) == {"EQUITY", "OPTION"}
        assert conc["asset_type_exposure"]["EQUITY"]["weight_pct"] == pytest.approx(80.0, abs=0.01)

    def test_empty_positions_edge_case(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload([]))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        conc = out["concentration"]
        assert conc["position_count"] == 0
        assert conc["total_abs_market_value"] == 0.0
        assert conc["hhi"] == 0.0
        assert conc["max_position_weight_pct"] == 0.0
        assert conc["top_n_weight_pct"] == 0.0
        assert conc["hhi_interpretation"] == "empty"

    def test_single_position_is_highly_concentrated(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        positions = [
            {"instrument": {"symbol": "ONE", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 999.0}
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        conc = out["concentration"]
        assert conc["hhi"] == pytest.approx(1.0)
        assert conc["max_position_weight_pct"] == pytest.approx(100.0)
        assert conc["hhi_interpretation"] == "highly_concentrated"

    def test_moderately_concentrated_band(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        # Weights ~0.4/0.3/0.3 → HHI ≈ 0.34 → highly; tune to land in [0.15,0.25).
        # weights 0.3,0.2,0.2,0.15,0.15 → HHI = 0.215 → moderate.
        positions = [
            {"instrument": {"symbol": "A", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 300.0},
            {"instrument": {"symbol": "B", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 200.0},
            {"instrument": {"symbol": "C", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 200.0},
            {"instrument": {"symbol": "D", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 150.0},
            {"instrument": {"symbol": "E", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 150.0},
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        # HHI = .3^2+.2^2+.2^2+.15^2+.15^2 = .09+.04+.04+.0225+.0225 = 0.215.
        assert out["concentration"]["hhi"] == pytest.approx(0.215, abs=0.001)
        assert out["concentration"]["hhi_interpretation"] == "moderately_concentrated"

    def test_diversified_band(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        # 10 equal positions → HHI = 0.1 → diversified.
        positions = [
            {"instrument": {"symbol": f"S{i}", "assetType": "EQUITY"}, "longQuantity": 1.0, "marketValue": 100.0}
            for i in range(10)
        ]
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(positions))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        assert out["concentration"]["hhi"] == pytest.approx(0.1, abs=0.001)
        assert out["concentration"]["hhi_interpretation"] == "diversified"

    def test_invalid_hash_raises_validation(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            analytics.get_concentration_analysis_impl({"account_hash": "x"})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_top_n_out_of_range_raises_validation(self, installed_client: Any) -> None:
        with pytest.raises(Exception) as excinfo:
            analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH, "top_n": 0})
        assert "validation" in type(excinfo.value).__name__.lower()

    def test_handles_401(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(401)
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False

    def test_handles_429(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(429)
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_handles_5xx(self, installed_client: Any, mock_schwab_client: MagicMock) -> None:
        mock_schwab_client.get_account.return_value = _resp(500)
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_does_not_write_cache(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        out = analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        assert out["_cache_status"] == "skipped:not-cached"


# ---------------------------------------------------------------------------
# get_cross_account_summary
# ---------------------------------------------------------------------------


class TestGetCrossAccountSummary:
    def test_single_account(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(
            200, [{"accountNumber": "A1", "hashValue": VALID_HASH}]
        )
        mock_schwab_client.get_account.return_value = _resp(
            200,
            _positions_payload(
                _TWO_POSITIONS,
                {"cashBalance": 100.0, "liquidationValue": 3400.0, "currency": "USD"},
            ),
        )
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is True
        assert out["account_count"] == 1
        assert out["accounts_aggregated"] == 1
        assert out["totals"]["total_market_value"] == pytest.approx(3300.0)
        assert out["totals"]["unique_symbol_count"] == 2
        assert out["accounts"][0]["liquidation_share_pct"] == pytest.approx(100.0)

    def test_multi_account_dedup_symbols(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(
            200,
            [
                {"accountNumber": "A1", "hashValue": VALID_HASH},
                {"accountNumber": "A2", "hashValue": VALID_HASH_B},
            ],
        )
        acct_a = _positions_payload(
            [{"instrument": {"symbol": "AAPL", "assetType": "EQUITY"}, "longQuantity": 10.0, "marketValue": 1700.0}],
            {"liquidationValue": 2000.0, "cashBalance": 300.0, "currency": "USD"},
        )
        acct_b = _positions_payload(
            [{"instrument": {"symbol": "AAPL", "assetType": "EQUITY"}, "longQuantity": 5.0, "marketValue": 850.0}],
            {"liquidationValue": 1000.0, "cashBalance": 150.0, "currency": "USD"},
        )
        mock_schwab_client.get_account.side_effect = [_resp(200, acct_a), _resp(200, acct_b)]
        out = analytics.get_cross_account_summary_impl()
        assert out["account_count"] == 2
        assert out["accounts_aggregated"] == 2
        # AAPL held in both → deduped to one symbol bucket, qty 15, mv 2550.
        assert out["totals"]["unique_symbol_count"] == 1
        aapl = out["merged_holdings"][0]
        assert aapl["symbol"] == "AAPL"
        assert aapl["total_quantity"] == pytest.approx(15.0)
        assert aapl["total_market_value"] == pytest.approx(2550.0)
        assert aapl["account_count"] == 2
        # Liquidation shares: 2000/3000 ≈ 66.67%, 1000/3000 ≈ 33.33%.
        shares = [a["liquidation_share_pct"] for a in out["accounts"]]
        assert shares[0] == pytest.approx(66.667, abs=0.01)
        assert shares[1] == pytest.approx(33.333, abs=0.01)

    def test_empty_no_accounts(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(200, [])
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is True
        assert out["account_count"] == 0
        assert out["accounts_aggregated"] == 0
        assert out["accounts"] == []
        assert out["merged_holdings"] == []
        assert out["totals"]["total_liquidation_value"] == 0.0

    def test_account_numbers_non_list(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        # Defensive: a non-list payload coerces to zero accounts.
        mock_schwab_client.get_account_numbers.return_value = _resp(200, {"unexpected": "dict"})
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is True
        assert out["account_count"] == 0

    def test_skips_mapping_without_hashvalue(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(
            200,
            [
                {"accountNumber": "A1", "hashValue": VALID_HASH},
                {"accountNumber": "A2"},  # missing hashValue → skipped
                {"accountNumber": "A3", "hashValue": 12345},  # non-str → skipped
            ],
        )
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        out = analytics.get_cross_account_summary_impl()
        assert out["account_count"] == 1

    def test_per_account_error_recorded(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(
            200,
            [
                {"accountNumber": "A1", "hashValue": VALID_HASH},
                {"accountNumber": "A2", "hashValue": VALID_HASH_B},
            ],
        )
        # First account ok, second 401 → recorded, not fatal.
        mock_schwab_client.get_account.side_effect = [
            _resp(200, _positions_payload(_TWO_POSITIONS)),
            _resp(401),
        ]
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is True
        assert out["account_count"] == 2
        assert out["accounts_aggregated"] == 1
        failed = next(a for a in out["accounts"] if not a["ok"])
        assert failed["error"]["status_code"] == 401

    def test_zero_liquidation_total_share_is_zero(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(
            200, [{"accountNumber": "A1", "hashValue": VALID_HASH}]
        )
        mock_schwab_client.get_account.return_value = _resp(
            200,
            _positions_payload([], {"liquidationValue": 0.0, "cashBalance": 0.0, "currency": "USD"}),
        )
        out = analytics.get_cross_account_summary_impl()
        assert out["accounts"][0]["liquidation_share_pct"] == 0.0

    def test_account_numbers_401(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(401, request_id="REQ")
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "refresh_token_expired"

    def test_account_numbers_429(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(429)
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "rate_limited"

    def test_account_numbers_5xx(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(503)
        out = analytics.get_cross_account_summary_impl()
        assert out["ok"] is False
        assert out["error"]["reason"] == "upstream_error"

    def test_payload_argument_is_ignored(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(200, [])
        out = analytics.get_cross_account_summary_impl({"ignored": "value"})
        assert out["ok"] is True

    def test_does_not_write_cache(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        mock_schwab_client.get_account_numbers.return_value = _resp(200, [])
        out = analytics.get_cross_account_summary_impl()
        assert out["_cache_status"] == "skipped:not-cached"


# ---------------------------------------------------------------------------
# Security boundary — analytics tools never touch mutation methods
# ---------------------------------------------------------------------------


class TestAnalyticsReadOnlyBoundary:
    def test_analytics_only_calls_whitelisted_reads(
        self,
        installed_client: Any,
        mock_schwab_client: MagicMock,
    ) -> None:
        """Exercising all 3 tools must only invoke white-listed read methods."""
        mock_schwab_client.get_account_numbers.return_value = _resp(
            200, [{"accountNumber": "A1", "hashValue": VALID_HASH}]
        )
        mock_schwab_client.get_account.return_value = _resp(200, _positions_payload(_TWO_POSITIONS))
        mock_schwab_client.get_transactions.return_value = _resp(200, [])

        analytics.get_pnl_analysis_impl({"account_hash": VALID_HASH})
        analytics.get_concentration_analysis_impl({"account_hash": VALID_HASH})
        analytics.get_cross_account_summary_impl()

        called = {c[0] for c in mock_schwab_client.method_calls}
        assert called <= {"get_account", "get_account_numbers", "get_transactions"}, (
            f"analytics tools called a non-read method: {called}"
        )

    def test_analytics_module_has_no_mutation_keywords(self) -> None:
        src = Path(analytics.__file__).read_text(encoding="utf-8")
        for kw in ("place_" + "order", "cancel_" + "order", "replace_" + "order"):
            assert kw not in src, f"analytics.py contains forbidden mutation keyword {kw!r}"
