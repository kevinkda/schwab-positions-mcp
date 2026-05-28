"""Tests for the DuckDB cache layer in ``schwab_positions_mcp.cache``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from schwab_positions_mcp.cache import (
    Cache,
    cache_bypass,
    cache_enabled,
    default_db_path,
    get_cache,
    reset_cache_singleton,
)


@pytest.fixture
def fresh_cache(tmp_path: Path) -> Cache:
    db = tmp_path / "cache.duckdb"
    return Cache(db_path=db)


class TestSchema:
    def test_init_creates_4_tables(self, fresh_cache: Cache) -> None:
        rows = fresh_cache._conn.execute(  # type: ignore[union-attr]
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert {
            "positions_snapshots",
            "orders_history",
            "transactions_history",
            "cache_events",
        }.issubset(names)


class TestWritePositions:
    def test_inserts(self, fresh_cache: Cache, mock_positions_data: dict[str, Any]) -> None:
        positions = mock_positions_data["securitiesAccount"]["positions"]
        n = fresh_cache.write_positions_snapshot("HASH_1", positions)
        assert n == 2
        count = fresh_cache._conn.execute(  # type: ignore[union-attr]
            "SELECT COUNT(*) FROM positions_snapshots"
        ).fetchone()
        assert count is not None and count[0] == 2

    def test_idempotent_replays_create_new_snapshots(
        self, fresh_cache: Cache, mock_positions_data: dict[str, Any]
    ) -> None:
        positions = mock_positions_data["securitiesAccount"]["positions"]
        ts = datetime.now(UTC)
        fresh_cache.write_positions_snapshot("HASH_1", positions, snapshot_at=ts)
        fresh_cache.write_positions_snapshot("HASH_1", positions, snapshot_at=ts)
        count = fresh_cache._conn.execute(  # type: ignore[union-attr]
            "SELECT COUNT(*) FROM positions_snapshots"
        ).fetchone()
        # Append-only by design — replays accumulate (auditable history).
        assert count is not None and count[0] == 4

    def test_empty_input_writes_zero(self, fresh_cache: Cache) -> None:
        n = fresh_cache.write_positions_snapshot("HASH_1", [])
        assert n == 0


class TestWriteOrders:
    def test_inserts(self, fresh_cache: Cache, mock_orders_data: list[dict[str, Any]]) -> None:
        n = fresh_cache.write_orders_history("HASH_1", mock_orders_data)
        assert n == 2

    def test_empty_input(self, fresh_cache: Cache) -> None:
        assert fresh_cache.write_orders_history("HASH_1", []) == 0


class TestWriteTransactions:
    def test_inserts(self, fresh_cache: Cache, mock_transactions_data: list[dict[str, Any]]) -> None:
        n = fresh_cache.write_transactions_history("HASH_1", mock_transactions_data)
        assert n == 2

    def test_empty_input(self, fresh_cache: Cache) -> None:
        assert fresh_cache.write_transactions_history("HASH_1", []) == 0


class TestCacheEvents:
    def test_event_logged_on_insert(self, fresh_cache: Cache, mock_orders_data: list[dict[str, Any]]) -> None:
        fresh_cache.write_orders_history("HASH_1", mock_orders_data)
        rows = fresh_cache._conn.execute(  # type: ignore[union-attr]
            "SELECT kind, table_name, row_count FROM cache_events"
        ).fetchall()
        kinds = [r[0] for r in rows]
        assert "INSERT" in kinds


class TestEnvFlags:
    def test_cache_enabled_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_ENABLED", raising=False)
        assert cache_enabled() is True

    def test_cache_enabled_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "0")
        assert cache_enabled() is False

    def test_cache_bypass_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_BYPASS", raising=False)
        assert cache_bypass() is False

    def test_cache_bypass_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_BYPASS", "yes")
        assert cache_bypass() is True


class TestSingleton:
    def test_get_cache_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_cache_singleton()
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "0")
        assert get_cache() is None

    def test_get_cache_returns_singleton(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        reset_cache_singleton()
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "1")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        first = get_cache()
        second = get_cache()
        assert first is second
        reset_cache_singleton()


class TestHelpers:
    def test_to_float_none(self) -> None:
        from schwab_positions_mcp.cache import _to_float

        assert _to_float(None) is None
        assert _to_float("") is None

    def test_to_float_garbage(self) -> None:
        from schwab_positions_mcp.cache import _to_float

        assert _to_float("not-a-number") is None

    def test_to_float_normal(self) -> None:
        from schwab_positions_mcp.cache import _to_float

        assert _to_float("3.14") == pytest.approx(3.14)

    def test_parse_dt_none(self) -> None:
        from schwab_positions_mcp.cache import _parse_dt

        assert _parse_dt(None) is None
        assert _parse_dt("") is None

    def test_parse_dt_iso(self) -> None:
        from schwab_positions_mcp.cache import _parse_dt

        out = _parse_dt("2026-05-01T13:30:00Z")
        assert out is not None
        assert out.year == 2026

    def test_parse_dt_garbage(self) -> None:
        from schwab_positions_mcp.cache import _parse_dt

        assert _parse_dt("not-a-date") is None

    def test_parse_dt_passthrough_datetime(self) -> None:
        from schwab_positions_mcp.cache import _parse_dt

        ts = datetime.now(UTC)
        assert _parse_dt(ts) is ts


class TestDefaultDbPath:
    def test_default_db_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        p = default_db_path()
        assert p.name == "cache.duckdb"
        assert "schwab-positions-mcp" in str(p)


class TestErrorHandling:
    def test_close_is_idempotent(self, fresh_cache: Cache) -> None:
        fresh_cache.close()
        # Second close on the same instance must not raise.
        fresh_cache.close()
        assert fresh_cache._conn is None

    def test_writes_after_close_return_zero(
        self,
        fresh_cache: Cache,
        mock_orders_data: list[dict[str, Any]],
    ) -> None:
        fresh_cache.close()
        assert fresh_cache.write_orders_history("H", mock_orders_data) == 0
        assert fresh_cache.write_transactions_history("H", mock_orders_data) == 0
        assert fresh_cache.write_positions_snapshot("H", mock_orders_data) == 0
