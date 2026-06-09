"""DuckDB-backed local cache for schwab-positions-mcp.

Stores **historical snapshots** of read-only Schwab account state:
  * ``positions_snapshots``  — one row per (account, symbol, snapshot_at)
  * ``orders_history``       — append-only orders mirror
  * ``transactions_history`` — append-only transactions mirror
  * ``cache_events``         — diagnostic event log (INSERT / SKIP / ERROR)

Storage
-------
Single-file DuckDB database under
``${XDG_STATE_HOME}/schwab-positions-mcp/cache.duckdb`` (or
``%LOCALAPPDATA%\\schwab-positions-mcp`` on Windows). Co-located with
``token.json`` so the threat model can reason about all secret-adjacent state
as one boundary. The DB file is chmod'd to ``0o600`` on POSIX.

Concurrency
-----------
DuckDB owns its own intra-process file lock; the :class:`Cache` opens one
connection per process and serialises writes via DuckDB's own transaction
guarantees plus a thread-local lock for safety.

Failure mode
------------
**Cache is best-effort.** Any DuckDB / IO error is caught, logged at WARNING,
and the caller falls through to live API responses. A corrupt DB is renamed
aside (``cache.duckdb.corrupt-<ts>``) and a fresh one created.

This module is NOT a security boundary. Read-only enforcement happens at
:mod:`schwab_positions_mcp.client` (Layer 1). The cache layer just persists.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import duckdb

from . import _platform

log = logging.getLogger(__name__)

CACHE_DB_FILENAME: Final[str] = "cache.duckdb"
CACHE_DIR_NAME: Final[str] = "schwab-positions-mcp"

ENV_CACHE_ENABLED: Final[str] = "SCHWAB_POSITIONS_CACHE_ENABLED"
ENV_CACHE_BYPASS: Final[str] = "SCHWAB_POSITIONS_CACHE_BYPASS"


def _truthy(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def cache_enabled() -> bool:
    """Whether the DuckDB cache is enabled. Default: **False** (disabled).

    The cache is **opt-in**. Set ``SCHWAB_POSITIONS_CACHE_ENABLED`` to one of
    ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive) to enable response
    caching. Any other value — including unset/empty/``0``/``false`` — keeps
    the cache disabled, in which case :func:`get_cache` returns ``None`` and
    every tool reports ``_cache_status = "skipped:disabled"``.

    .. versionchanged:: 0.1.4
       Default flipped from enabled to disabled (opt-in). BREAKING for any
       consumer that relied on default-on caching — set the env var to
       restore the previous behavior.
    """
    return _truthy(os.environ.get(ENV_CACHE_ENABLED), default=False)


def cache_bypass() -> bool:
    return _truthy(os.environ.get(ENV_CACHE_BYPASS), default=False)


def default_db_path() -> Path:
    return _platform.state_root() / CACHE_DIR_NAME / CACHE_DB_FILENAME


# ---------------------------------------------------------------------------
# Schema (DDL) — keep idempotent; CREATE TABLE IF NOT EXISTS.
# ---------------------------------------------------------------------------

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS positions_snapshots (
        snapshot_at      TIMESTAMP NOT NULL,
        account_hash     VARCHAR   NOT NULL,
        symbol           VARCHAR   NOT NULL,
        asset_type       VARCHAR,
        quantity         DOUBLE,
        average_price    DOUBLE,
        market_value     DOUBLE,
        unrealized_pl    DOUBLE,
        currency         VARCHAR,
        raw_json         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders_history (
        observed_at      TIMESTAMP NOT NULL,
        account_hash     VARCHAR   NOT NULL,
        order_id         VARCHAR   NOT NULL,
        status           VARCHAR,
        entered_time     TIMESTAMP,
        close_time       TIMESTAMP,
        order_type       VARCHAR,
        symbol           VARCHAR,
        quantity         DOUBLE,
        filled_quantity  DOUBLE,
        price            DOUBLE,
        raw_json         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transactions_history (
        observed_at      TIMESTAMP NOT NULL,
        account_hash     VARCHAR   NOT NULL,
        transaction_id   VARCHAR   NOT NULL,
        transaction_date TIMESTAMP,
        type             VARCHAR,
        symbol           VARCHAR,
        amount           DOUBLE,
        net_amount       DOUBLE,
        currency         VARCHAR,
        raw_json         VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cache_events (
        ts          TIMESTAMP NOT NULL,
        kind        VARCHAR   NOT NULL,  -- INSERT / SKIP / ERROR
        table_name  VARCHAR,
        row_count   BIGINT,
        detail      VARCHAR
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_positions_account_symbol ON positions_snapshots(account_hash, symbol)",
    "CREATE INDEX IF NOT EXISTS idx_positions_snapshot_at ON positions_snapshots(snapshot_at)",
    "CREATE INDEX IF NOT EXISTS idx_orders_account_orderid ON orders_history(account_hash, order_id)",
    "CREATE INDEX IF NOT EXISTS idx_transactions_account_txid ON transactions_history(account_hash, transaction_id)",
)


# ---------------------------------------------------------------------------
# Cache class
# ---------------------------------------------------------------------------


class Cache:
    """Thread-safe DuckDB cache.

    Open ONE instance per process and reuse it. The class owns:
      * the DuckDB connection
      * a re-entrant lock that serialises writes from concurrent tool calls
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: Path = db_path or default_db_path()
        self._lock = threading.RLock()
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._open()

    # -- lifecycle --------------------------------------------------------

    def _open(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self._db_path.parent, 0o700)
        try:
            self._conn = duckdb.connect(str(self._db_path))
        except duckdb.Error as exc:
            log.warning("DuckDB open failed at %s: %s; quarantining and retrying", self._db_path, exc)
            self._quarantine_and_retry()
        self._init_schema()
        with contextlib.suppress(OSError):
            _platform.secure_chmod(self._db_path, 0o600)

    def _quarantine_and_retry(self) -> None:
        ts = int(time.time())
        bad = self._db_path.with_name(f"{self._db_path.name}.corrupt-{ts}")
        with contextlib.suppress(OSError):
            self._db_path.rename(bad)
            log.warning("Quarantined corrupt cache to %s", bad)
        self._conn = duckdb.connect(str(self._db_path))

    def _init_schema(self) -> None:
        assert self._conn is not None
        for stmt in _DDL:
            try:
                self._conn.execute(stmt)
            except duckdb.Error as exc:
                log.warning("DuckDB DDL failed: %s; stmt=%s", exc, stmt[:60])

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                with contextlib.suppress(duckdb.Error):
                    self._conn.close()
                self._conn = None

    # -- write helpers ----------------------------------------------------

    def _log_event(self, kind: str, table: str, count: int, detail: str = "") -> None:
        if self._conn is None:
            return
        with contextlib.suppress(duckdb.Error):
            self._conn.execute(
                "INSERT INTO cache_events (ts, kind, table_name, row_count, detail) VALUES (?, ?, ?, ?, ?)",
                [datetime.now(UTC), kind, table, count, detail[:500]],
            )

    def write_positions_snapshot(
        self,
        account_hash: str,
        positions: list[dict[str, Any]],
        snapshot_at: datetime | None = None,
    ) -> int:
        """Write a positions snapshot. Returns inserted row count."""
        if self._conn is None or not positions:
            return 0
        ts = snapshot_at or datetime.now(UTC)
        rows: list[list[Any]] = []
        for pos in positions:
            instrument = pos.get("instrument") or {}
            rows.append(
                [
                    ts,
                    account_hash,
                    str(instrument.get("symbol") or ""),
                    str(instrument.get("assetType") or ""),
                    _to_float(pos.get("longQuantity") or pos.get("shortQuantity") or pos.get("quantity")),
                    _to_float(pos.get("averagePrice")),
                    _to_float(pos.get("marketValue")),
                    _to_float(pos.get("currentDayProfitLoss") or pos.get("longOpenProfitLoss")),
                    str(pos.get("currency") or ""),
                    json.dumps(pos, default=str),
                ]
            )
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO positions_snapshots (snapshot_at, account_hash, symbol, asset_type, "
                    "quantity, average_price, market_value, unrealized_pl, currency, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._log_event("INSERT", "positions_snapshots", len(rows))
            except duckdb.Error as exc:
                self._log_event("ERROR", "positions_snapshots", 0, str(exc))
                log.warning("write_positions_snapshot failed: %s", exc)
                return 0
        return len(rows)

    def write_orders_history(self, account_hash: str, orders: list[dict[str, Any]]) -> int:
        if self._conn is None or not orders:
            return 0
        ts = datetime.now(UTC)
        rows: list[list[Any]] = []
        for order in orders:
            legs = order.get("orderLegCollection") or []
            first_leg = legs[0] if legs else {}
            instr = first_leg.get("instrument") or {}
            rows.append(
                [
                    ts,
                    account_hash,
                    str(order.get("orderId") or ""),
                    str(order.get("status") or ""),
                    _parse_dt(order.get("enteredTime")),
                    _parse_dt(order.get("closeTime")),
                    str(order.get("orderType") or ""),
                    str(instr.get("symbol") or ""),
                    _to_float(first_leg.get("quantity")),
                    _to_float(order.get("filledQuantity")),
                    _to_float(order.get("price")),
                    json.dumps(order, default=str),
                ]
            )
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO orders_history (observed_at, account_hash, order_id, status, "
                    "entered_time, close_time, order_type, symbol, quantity, filled_quantity, "
                    "price, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._log_event("INSERT", "orders_history", len(rows))
            except duckdb.Error as exc:
                self._log_event("ERROR", "orders_history", 0, str(exc))
                log.warning("write_orders_history failed: %s", exc)
                return 0
        return len(rows)

    def write_transactions_history(self, account_hash: str, transactions: list[dict[str, Any]]) -> int:
        if self._conn is None or not transactions:
            return 0
        ts = datetime.now(UTC)
        rows: list[list[Any]] = []
        for tx in transactions:
            instr = tx.get("instrument") or {}
            rows.append(
                [
                    ts,
                    account_hash,
                    str(tx.get("transactionId") or tx.get("activityId") or ""),
                    _parse_dt(tx.get("tradeDate") or tx.get("time")),
                    str(tx.get("type") or ""),
                    str(instr.get("symbol") or ""),
                    _to_float(tx.get("amount")),
                    _to_float(tx.get("netAmount")),
                    str(tx.get("currency") or ""),
                    json.dumps(tx, default=str),
                ]
            )
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO transactions_history (observed_at, account_hash, transaction_id, "
                    "transaction_date, type, symbol, amount, net_amount, currency, raw_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._log_event("INSERT", "transactions_history", len(rows))
            except duckdb.Error as exc:
                self._log_event("ERROR", "transactions_history", 0, str(exc))
                log.warning("write_transactions_history failed: %s", exc)
                return 0
        return len(rows)


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


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
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
            except duckdb.Error as exc:
                log.warning("Cache init failed; running without cache: %s", exc)
                return None
        return _cache_singleton


def reset_cache_singleton() -> None:
    """Test hook: drop the cached singleton (does NOT delete the DB file)."""
    global _cache_singleton
    with _cache_singleton_lock:
        if _cache_singleton is not None:
            _cache_singleton.close()
        _cache_singleton = None
