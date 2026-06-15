"""Tests for the pluggable derived-history cache in ``schwab_positions_mcp.cache``.

.. versionchanged:: 0.3.0
    DuckDB removed; the cache delegates to a pluggable ``CacheBackend``
    (memory default).  Snapshot writes append to a derived-analysis time
    series — the memory backend keeps no durable history (persists 0 rows),
    a ClickHouse backend persists the full batch.  Read-only tools are
    entirely unaffected (the cache only ever *writes* history).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp.cache import (
    Cache,
    cache_bypass,
    cache_enabled,
    get_cache,
    reset_cache_singleton,
)
from schwab_positions_mcp.cache_backend import ClickHouseBackend, MemoryBackend


def _ch_backend() -> ClickHouseBackend:
    """A ClickHouse backend wired to a mock client that persists every row."""
    client = MagicMock()
    client.command.return_value = None
    client.insert.return_value = None
    result = MagicMock()
    result.result_rows = []
    client.query.return_value = result
    return ClickHouseBackend(url="clickhouse://x", client=client)


@pytest.fixture
def fresh_cache() -> Cache:
    # Default memory backend — no durable history, no files.
    return Cache(backend=MemoryBackend())


# ---------------------------------------------------------------------------
# Snapshot writes — memory backend (no durable history → persists 0)
# ---------------------------------------------------------------------------


class TestWriteMemoryBackend:
    def test_positions_memory_persists_zero(self, fresh_cache: Cache, mock_positions_data: dict[str, Any]) -> None:
        positions = mock_positions_data["securitiesAccount"]["positions"]
        # Memory backend keeps no durable history → 0 rows persisted (graceful).
        assert fresh_cache.write_positions_snapshot("HASH_1", positions) == 0

    def test_orders_memory_persists_zero(self, fresh_cache: Cache, mock_orders_data: list[dict[str, Any]]) -> None:
        assert fresh_cache.write_orders_history("HASH_1", mock_orders_data) == 0

    def test_transactions_memory_persists_zero(
        self, fresh_cache: Cache, mock_transactions_data: list[dict[str, Any]]
    ) -> None:
        assert fresh_cache.write_transactions_history("HASH_1", mock_transactions_data) == 0

    def test_empty_inputs_write_zero(self, fresh_cache: Cache) -> None:
        assert fresh_cache.write_positions_snapshot("HASH_1", []) == 0
        assert fresh_cache.write_orders_history("HASH_1", []) == 0
        assert fresh_cache.write_transactions_history("HASH_1", []) == 0


# ---------------------------------------------------------------------------
# Snapshot writes — ClickHouse backend (durable history → persists N)
# ---------------------------------------------------------------------------


class TestWriteClickHouseBackend:
    def test_positions_persisted(self, mock_positions_data: dict[str, Any]) -> None:
        cache = Cache(backend=_ch_backend())
        positions = mock_positions_data["securitiesAccount"]["positions"]
        n = cache.write_positions_snapshot("HASH_1", positions)
        assert n == 2

    def test_positions_replays_accumulate(self, mock_positions_data: dict[str, Any]) -> None:
        cache = Cache(backend=_ch_backend())
        positions = mock_positions_data["securitiesAccount"]["positions"]
        ts = datetime.now(UTC)
        first = cache.write_positions_snapshot("HASH_1", positions, snapshot_at=ts)
        second = cache.write_positions_snapshot("HASH_1", positions, snapshot_at=ts)
        # Append-only by design — replays each persist the full batch.
        assert first == 2
        assert second == 2

    def test_orders_persisted(self, mock_orders_data: list[dict[str, Any]]) -> None:
        cache = Cache(backend=_ch_backend())
        assert cache.write_orders_history("HASH_1", mock_orders_data) == 2

    def test_transactions_persisted(self, mock_transactions_data: list[dict[str, Any]]) -> None:
        cache = Cache(backend=_ch_backend())
        assert cache.write_transactions_history("HASH_1", mock_transactions_data) == 2

    def test_append_error_stops_and_persists_partial(self, mock_orders_data: list[dict[str, Any]]) -> None:
        backend = _ch_backend()
        # First insert succeeds, second raises → loop stops, count reflects it.
        backend._client.insert.side_effect = [None, RuntimeError("boom")]
        cache = Cache(backend=backend)
        assert cache.write_orders_history("HASH_1", mock_orders_data) == 1

    def test_query_history_roundtrip(self) -> None:
        backend = _ch_backend()
        result = MagicMock()
        result.result_rows = [['{"symbol": "AAPL"}']]
        backend._client.query.return_value = result
        cache = Cache(backend=backend)
        out = cache.query_history("positions_snapshots", limit=10)
        assert out["status"] == "ok"
        assert out["rows"] == [{"symbol": "AAPL"}]


# ---------------------------------------------------------------------------
# Degradation: memory query returns the requires_clickhouse signal
# ---------------------------------------------------------------------------


class TestQueryDegradation:
    def test_memory_query_degrades(self, fresh_cache: Cache) -> None:
        out = fresh_cache.query_history("positions_snapshots")
        assert out["status"] == "requires_clickhouse_persistence"


# ---------------------------------------------------------------------------
# Env flags
# ---------------------------------------------------------------------------


class TestEnvFlags:
    def test_cache_enabled_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_ENABLED", raising=False)
        assert cache_enabled() is False

    def test_cache_enabled_empty_string_is_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "")
        assert cache_enabled() is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON", " on ", "  1  "])
    def test_cache_enabled_truthy_tokens(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", raw)
        assert cache_enabled() is True

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", "OFF", "nope", "2", "enable"])
    def test_cache_enabled_falsy_tokens(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", raw)
        assert cache_enabled() is False

    def test_cache_bypass_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_BYPASS", raising=False)
        assert cache_bypass() is False

    def test_cache_bypass_truthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_BYPASS", "yes")
        assert cache_bypass() is True


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_cache_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_cache_singleton()
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "0")
        assert get_cache() is None

    def test_get_cache_default_unset_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_cache_singleton()
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_ENABLED", raising=False)
        assert get_cache() is None

    def test_get_cache_returns_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reset_cache_singleton()
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "1")
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_BACKEND", raising=False)
        first = get_cache()
        second = get_cache()
        assert first is second
        assert first is not None
        assert first.backend.name == "memory"
        reset_cache_singleton()

    def test_get_cache_init_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import schwab_positions_mcp.cache as cache_mod

        reset_cache_singleton()
        monkeypatch.setattr(cache_mod, "cache_enabled", lambda: True)

        def boom() -> Any:
            raise RuntimeError("backend init failed")

        monkeypatch.setattr(cache_mod, "get_cache_backend", boom)
        assert get_cache() is None
        reset_cache_singleton()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

    def test_dt_str_none(self) -> None:
        from schwab_positions_mcp.cache import _dt_str

        assert _dt_str(None) is None
        assert _dt_str("") is None

    def test_dt_str_iso(self) -> None:
        from schwab_positions_mcp.cache import _dt_str

        out = _dt_str("2026-05-01T13:30:00Z")
        assert out is not None
        assert out.startswith("2026-05-01")

    def test_dt_str_garbage(self) -> None:
        from schwab_positions_mcp.cache import _dt_str

        assert _dt_str("not-a-date") is None

    def test_dt_str_passthrough_datetime(self) -> None:
        from schwab_positions_mcp.cache import _dt_str

        ts = datetime(2026, 5, 1, 13, 30, tzinfo=UTC)
        assert _dt_str(ts) == ts.isoformat()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_close_is_idempotent(self, fresh_cache: Cache) -> None:
        fresh_cache.close()
        fresh_cache.close()

    def test_context_manager(self, mock_orders_data: list[dict[str, Any]]) -> None:
        with Cache(backend=MemoryBackend()) as cache:
            assert cache.write_orders_history("H", mock_orders_data) == 0

    def test_default_backend_is_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_BACKEND", raising=False)
        assert Cache().backend.name == "memory"

    def test_append_rows_empty_returns_zero(self) -> None:
        # Direct guard: the internal helper short-circuits on an empty batch.
        cache = Cache(backend=MemoryBackend())
        assert cache._append_rows("positions_snapshots", []) == 0
