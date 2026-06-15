"""Pluggable derived-history cache for schwab-positions-mcp (v0.7 T0).

.. versionchanged:: 0.3.0
    ⚠️ **BREAKING** — the embedded DuckDB cache is removed.  The cache now
    delegates to a pluggable
    :class:`~schwab_positions_mcp.cache_backend.CacheBackend`:

    * **memory** (default) — in-process, zero external dependency,
      concurrency-safe, non-blocking (no global ``RLock``, no file locks).
      The memory backend keeps **no durable history**, so snapshot writes
      report ``0`` rows persisted (graceful degradation) — read-only tools
      are entirely unaffected.
    * **clickhouse** (opt-in) — ``pip install schwab-positions-mcp[clickhouse]``
      + ``SCHWAB_POSITIONS_CLICKHOUSE_URL`` +
      ``SCHWAB_POSITIONS_CACHE_BACKEND=clickhouse`` to durably persist the
      historical position / order / transaction snapshots.

    Selection via ``SCHWAB_POSITIONS_CACHE_BACKEND`` (``memory`` |
    ``clickhouse``, default ``memory``).

This module persists **historical snapshots** of read-only Schwab account
state as append-only derived-analysis time series:

  * ``positions_snapshots``   — one row per (account, symbol, snapshot_at)
  * ``orders_history``        — append-only orders mirror
  * ``transactions_history``  — append-only transactions mirror

Failure mode: **best-effort** — every backend swallows storage errors and the
caller falls through to live API responses.

This module is NOT a security boundary.  Read-only enforcement happens at
:mod:`schwab_positions_mcp.client` (Layer 1).  The cache layer only ever
*writes* derived history; it never serves reads back into a tool, so it
cannot widen the read-only attack surface.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any, Final

from .cache_backend import (
    CacheBackend,
    get_cache_backend,
)

log = logging.getLogger(__name__)

CACHE_DIR_NAME: Final[str] = "schwab-positions-mcp"

ENV_CACHE_ENABLED: Final[str] = "SCHWAB_POSITIONS_CACHE_ENABLED"
ENV_CACHE_BYPASS: Final[str] = "SCHWAB_POSITIONS_CACHE_BYPASS"

_POSITIONS_SERIES: Final[str] = "positions_snapshots"
_ORDERS_SERIES: Final[str] = "orders_history"
_TRANSACTIONS_SERIES: Final[str] = "transactions_history"


def _truthy(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def cache_enabled() -> bool:
    """Whether the derived-history cache is enabled. Default: **False**.

    The cache is **opt-in**. Set ``SCHWAB_POSITIONS_CACHE_ENABLED`` to one of
    ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive) to enable history
    persistence. Any other value — including unset/empty/``0``/``false`` —
    keeps the cache disabled, in which case :func:`get_cache` returns ``None``
    and every tool reports ``_cache_status = "skipped:disabled"``.

    .. versionchanged:: 0.1.4
       Default flipped from enabled to disabled (opt-in).
    """
    return _truthy(os.environ.get(ENV_CACHE_ENABLED), default=False)


def cache_bypass() -> bool:
    return _truthy(os.environ.get(ENV_CACHE_BYPASS), default=False)


# ---------------------------------------------------------------------------
# Cache facade
# ---------------------------------------------------------------------------


class Cache:
    """Backend-agnostic derived-history writer.  One instance per process.

    Delegates all storage to a :class:`CacheBackend` (memory by default,
    ClickHouse when opted in).  The legacy snapshot-write public API is kept
    verbatim so tools require no changes.

    Each ``write_*`` method appends one row per record to the corresponding
    derived-analysis time series and returns the number of rows the backend
    durably persisted (``0`` on the memory backend, which keeps no history).
    """

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self.backend: CacheBackend = backend if backend is not None else get_cache_backend()
        self._lock = threading.Lock()

    def close(self) -> None:
        # Pluggable backends own their own lifecycle; nothing to close for
        # the memory backend, and the ClickHouse client is process-scoped.
        return None

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        self.close()

    # -- internal append helper ------------------------------------------

    def _append_rows(self, series: str, rows: list[dict[str, Any]]) -> int:
        """Append rows to *series*; return the count durably persisted.

        The memory backend persists no history (returns a degradation
        signal) → ``0`` rows.  The ClickHouse backend persists each row →
        full count.  Storage errors are swallowed best-effort → ``0``.
        """
        if not rows:
            return 0
        persisted = 0
        with self._lock:
            for row in rows:
                try:
                    result = self.backend.append_timeseries(series, row)
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("append_timeseries failed for %s: %s", series, exc)
                    break
                if result.get("status") == "ok":
                    persisted += 1
                else:
                    # Memory backend (or error) — no durable history. Stop:
                    # all rows in this batch share the same backend outcome.
                    break
        return persisted

    # -- write helpers (public API — tools depend on these) ---------------

    def write_positions_snapshot(
        self,
        account_hash: str,
        positions: list[dict[str, Any]],
        snapshot_at: datetime | None = None,
    ) -> int:
        """Write a positions snapshot. Returns rows durably persisted."""
        if not positions:
            return 0
        ts = (snapshot_at or datetime.now(UTC)).isoformat()
        rows: list[dict[str, Any]] = []
        for pos in positions:
            instrument = pos.get("instrument") or {}
            rows.append(
                {
                    "snapshot_at": ts,
                    "account_hash": account_hash,
                    "symbol": str(instrument.get("symbol") or ""),
                    "asset_type": str(instrument.get("assetType") or ""),
                    "quantity": _to_float(pos.get("longQuantity") or pos.get("shortQuantity") or pos.get("quantity")),
                    "average_price": _to_float(pos.get("averagePrice")),
                    "market_value": _to_float(pos.get("marketValue")),
                    "unrealized_pl": _to_float(pos.get("currentDayProfitLoss") or pos.get("longOpenProfitLoss")),
                    "currency": str(pos.get("currency") or ""),
                    "raw_json": json.dumps(pos, default=str),
                }
            )
        return self._append_rows(_POSITIONS_SERIES, rows)

    def write_orders_history(self, account_hash: str, orders: list[dict[str, Any]]) -> int:
        if not orders:
            return 0
        ts = datetime.now(UTC).isoformat()
        rows: list[dict[str, Any]] = []
        for order in orders:
            legs = order.get("orderLegCollection") or []
            first_leg = legs[0] if legs else {}
            instr = first_leg.get("instrument") or {}
            rows.append(
                {
                    "observed_at": ts,
                    "account_hash": account_hash,
                    "order_id": str(order.get("orderId") or ""),
                    "status": str(order.get("status") or ""),
                    "entered_time": _dt_str(order.get("enteredTime")),
                    "close_time": _dt_str(order.get("closeTime")),
                    "order_type": str(order.get("orderType") or ""),
                    "symbol": str(instr.get("symbol") or ""),
                    "quantity": _to_float(first_leg.get("quantity")),
                    "filled_quantity": _to_float(order.get("filledQuantity")),
                    "price": _to_float(order.get("price")),
                    "raw_json": json.dumps(order, default=str),
                }
            )
        return self._append_rows(_ORDERS_SERIES, rows)

    def write_transactions_history(self, account_hash: str, transactions: list[dict[str, Any]]) -> int:
        if not transactions:
            return 0
        ts = datetime.now(UTC).isoformat()
        rows: list[dict[str, Any]] = []
        for tx in transactions:
            instr = tx.get("instrument") or {}
            rows.append(
                {
                    "observed_at": ts,
                    "account_hash": account_hash,
                    "transaction_id": str(tx.get("transactionId") or tx.get("activityId") or ""),
                    "transaction_date": _dt_str(tx.get("tradeDate") or tx.get("time")),
                    "type": str(tx.get("type") or ""),
                    "symbol": str(instr.get("symbol") or ""),
                    "amount": _to_float(tx.get("amount")),
                    "net_amount": _to_float(tx.get("netAmount")),
                    "currency": str(tx.get("currency") or ""),
                    "raw_json": json.dumps(tx, default=str),
                }
            )
        return self._append_rows(_TRANSACTIONS_SERIES, rows)

    # -- query (derived-analysis history readback) -----------------------

    def query_history(self, series: str, *, limit: int = 1000) -> dict[str, Any]:
        """Read back a derived-analysis time series.

        Returns the backend payload — ``{"status": "ok", "rows": [...]}`` when
        ClickHouse-backed, or a ``requires_clickhouse_persistence`` signal on
        the memory backend.  Not wired into any read-only tool; provided for
        future analytics that opt into ClickHouse history.
        """
        return self.backend.query_timeseries(series, {"limit": limit})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt_str(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_cache_singleton: Cache | None = None
_cache_singleton_lock = threading.Lock()


def get_cache() -> Cache | None:
    """Return the process-wide :class:`Cache` (lazy init).

    Returns ``None`` when caching is disabled (the default). Caching is
    opt-in: enable it with ``SCHWAB_POSITIONS_CACHE_ENABLED=1`` (or
    ``true`` / ``yes`` / ``on``). See :func:`cache_enabled`.
    """
    global _cache_singleton
    if not cache_enabled():
        return None
    with _cache_singleton_lock:
        if _cache_singleton is None:
            try:
                _cache_singleton = Cache()
            except Exception as exc:  # pragma: no cover - defensive backend init
                log.warning("Cache init failed; running without cache: %s", exc)
                return None
        return _cache_singleton


def reset_cache_singleton() -> None:
    """Test hook: drop the cached singleton."""
    global _cache_singleton
    with _cache_singleton_lock:
        if _cache_singleton is not None:
            _cache_singleton.close()
        _cache_singleton = None


__all__ = [
    "CACHE_DIR_NAME",
    "ENV_CACHE_BYPASS",
    "ENV_CACHE_ENABLED",
    "Cache",
    "cache_bypass",
    "cache_enabled",
    "get_cache",
    "reset_cache_singleton",
]
