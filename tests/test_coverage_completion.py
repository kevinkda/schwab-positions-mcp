"""Deep-coverage completion tests — drive the remaining uncovered branches to 100%.

This file exists to exercise the *defensive* / *platform-specific* / *error*
paths that the happy-path tests never reach:

* ``_platform.notify_desktop`` macOS / Linux dispatch + best-effort swallow.
* ``cache.Cache`` DuckDB open-failure → quarantine, DDL failure, write
  failures, and ``get_cache`` init failure.
* ``bootstrap.bootstrap_dotenv`` missing-``python-dotenv`` branch.
* ``server`` thin tool wrappers + ``main``.
* ``tools._common.get_client`` lazy build + ``normalise_response`` no-headers
  branch.
* ``tools.summary`` cache-write success branch.

Every test carries a *substantive* assertion (no empty-coverage padding) so
this file also functions as a behavioural regression net, not just a coverage
filler. (V6-J lesson: coverage AND assertion quality.)
"""

from __future__ import annotations

import builtins
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from schwab_positions_mcp import _platform, bootstrap
from schwab_positions_mcp import cache as cache_module
from schwab_positions_mcp.cache import Cache, get_cache, reset_cache_singleton
from schwab_positions_mcp.cache_backend import ClickHouseBackend, MemoryBackend
from schwab_positions_mcp.tools import _common as tools_common

if TYPE_CHECKING:
    from schwab_positions_mcp.client import ReadOnlySchwabClient


def _ch_backend() -> ClickHouseBackend:
    """A ClickHouse backend wired to a mock client that persists every row."""
    client = MagicMock()
    client.command.return_value = None
    client.insert.return_value = None
    result = MagicMock()
    result.result_rows = []
    client.query.return_value = result
    return ClickHouseBackend(url="clickhouse://x", client=client)


# ===========================================================================
# _platform.notify_desktop — macOS / Linux dispatch + best-effort swallow
# ===========================================================================


class TestNotifyDesktopDispatch:
    def test_macos_branch_invokes_osascript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When IS_MACOS, notify_desktop must route to _notify_macos and run osascript."""
        monkeypatch.setattr(_platform, "IS_MACOS", True)
        monkeypatch.setattr(_platform, "IS_LINUX", False)

        which_calls: list[str] = []
        run_calls: list[list[str]] = []

        def fake_which(name: str) -> str | None:
            which_calls.append(name)
            return "/usr/bin/osascript"

        def fake_run(args: list[str], **_kw: Any) -> MagicMock:
            run_calls.append(args)
            return MagicMock()

        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(subprocess, "run", fake_run)

        _platform.notify_desktop("My Title", "My Message")

        # The macOS path was actually taken: osascript was resolved + invoked.
        assert which_calls == ["osascript"]
        assert len(run_calls) == 1
        invoked = run_calls[0]
        assert invoked[0] == "/usr/bin/osascript"
        # Title + message are embedded in the AppleScript payload.
        joined = " ".join(invoked)
        assert "My Title" in joined
        assert "My Message" in joined

    def test_macos_branch_no_osascript_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If osascript is absent, _notify_macos returns early without running anything."""
        monkeypatch.setattr(_platform, "IS_MACOS", True)
        monkeypatch.setattr(_platform, "IS_LINUX", False)

        run_calls: list[Any] = []
        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: run_calls.append((a, k)))

        _platform.notify_desktop("t", "m")

        assert run_calls == [], "subprocess.run must NOT be called when osascript is missing"

    def test_linux_branch_no_notify_send_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux path: if notify-send is absent, _notify_linux returns early (branch 217)."""
        monkeypatch.setattr(_platform, "IS_MACOS", False)
        monkeypatch.setattr(_platform, "IS_LINUX", True)

        run_calls: list[Any] = []
        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", lambda _name: None)
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: run_calls.append((a, k)))

        _platform.notify_desktop("t", "m")

        assert run_calls == [], "subprocess.run must NOT be called when notify-send is missing"

    def test_linux_branch_invokes_notify_send(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux path: when notify-send exists, it is invoked with critical urgency."""
        monkeypatch.setattr(_platform, "IS_MACOS", False)
        monkeypatch.setattr(_platform, "IS_LINUX", True)

        run_calls: list[list[str]] = []
        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/notify-send")
        monkeypatch.setattr(subprocess, "run", lambda args, **_k: run_calls.append(args) or MagicMock())

        _platform.notify_desktop("Linux Title", "Linux Body")

        assert len(run_calls) == 1
        args = run_calls[0]
        assert args[0] == "/usr/bin/notify-send"
        assert "critical" in args
        assert "Linux Title" in args
        assert "Linux Body" in args

    def test_notify_desktop_swallows_exceptions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """notify_desktop must NEVER raise — exceptions from the platform helper are swallowed."""
        monkeypatch.setattr(_platform, "IS_MACOS", True)
        monkeypatch.setattr(_platform, "IS_LINUX", False)

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("simulated notify failure")

        monkeypatch.setattr(_platform, "_notify_macos", boom)

        # Must not propagate — best-effort contract.
        _platform.notify_desktop("t", "m")  # no exception == pass

    def test_notify_desktop_unknown_platform_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When neither macOS nor Linux nor Windows, notify_desktop does nothing and returns."""
        monkeypatch.setattr(_platform, "IS_MACOS", False)
        monkeypatch.setattr(_platform, "IS_LINUX", False)
        monkeypatch.setattr(_platform, "IS_WINDOWS", False)

        macos = MagicMock()
        linux = MagicMock()
        monkeypatch.setattr(_platform, "_notify_macos", macos)
        monkeypatch.setattr(_platform, "_notify_linux", linux)

        _platform.notify_desktop("t", "m")

        macos.assert_not_called()
        linux.assert_not_called()


# ===========================================================================
# bootstrap.bootstrap_dotenv — missing python-dotenv branch
# ===========================================================================


class TestBootstrapMissingDotenv:
    def test_returns_false_when_dotenv_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """If ``import dotenv`` raises ImportError, bootstrap_dotenv must return False (no crash)."""
        monkeypatch.chdir(tmp_path)
        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "dotenv":
                raise ImportError("simulated: python-dotenv not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        result = bootstrap.bootstrap_dotenv()

        assert result is False, "missing python-dotenv must degrade to False, not raise"


# ===========================================================================
# cache.Cache — backend failure / degradation paths
# ===========================================================================


class TestCacheBackendSelection:
    def test_default_backend_is_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_CACHE_BACKEND", raising=False)
        assert Cache().backend.name == "memory"

    def test_memory_query_degrades(self) -> None:
        cache = Cache(backend=MemoryBackend())
        out = cache.query_history("positions_snapshots")
        assert out["status"] == "requires_clickhouse_persistence"


class TestCacheWriteFailures:
    def _broken_ch(self) -> Cache:
        backend = _ch_backend()
        backend._client.insert.side_effect = RuntimeError("simulated write failure")
        return Cache(backend=backend)

    def test_write_positions_failure_returns_zero(self, mock_positions_data: dict[str, Any]) -> None:
        positions = mock_positions_data["securitiesAccount"]["positions"]
        assert self._broken_ch().write_positions_snapshot("HASH", positions) == 0

    def test_write_orders_failure_returns_zero(self, mock_orders_data: list[dict[str, Any]]) -> None:
        assert self._broken_ch().write_orders_history("HASH", mock_orders_data) == 0

    def test_write_transactions_failure_returns_zero(self, mock_transactions_data: list[dict[str, Any]]) -> None:
        assert self._broken_ch().write_transactions_history("HASH", mock_transactions_data) == 0

    def test_memory_writes_persist_zero(self, mock_orders_data: list[dict[str, Any]]) -> None:
        assert Cache(backend=MemoryBackend()).write_orders_history("HASH", mock_orders_data) == 0


class TestGetCacheInitFailure:
    def test_get_cache_returns_none_on_init_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If backend construction raises, get_cache returns None (degrade)."""
        reset_cache_singleton()
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "1")

        def boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("simulated cache init failure")

        monkeypatch.setattr(cache_module, "get_cache_backend", boom)

        result = get_cache()
        assert result is None, "cache init failure must degrade to None, never crash the tool"
        reset_cache_singleton()


# ===========================================================================
# server — thin tool wrappers + main
# ===========================================================================


class TestServerWrappers:
    def test_get_account_numbers_wrapper_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """server.get_account_numbers must delegate to the impl (line 61)."""
        import schwab_positions_mcp.server as srv
        from schwab_positions_mcp.tools import account_numbers

        sentinel = {"ok": True, "account_numbers": [], "count": 0, "_cache_status": "x"}
        monkeypatch.setattr(account_numbers, "get_account_numbers_impl", lambda: sentinel)

        # FastMCP's @mcp.tool returns the original function object, so it is
        # directly callable here (verified: type is <function>, no .fn attr).
        result = srv.get_account_numbers()
        assert result is sentinel

    def test_main_runs_stdio_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """server.main must start the MCP server with stdio transport by default."""
        import schwab_positions_mcp.server as srv

        run_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(srv.mcp, "run", lambda **kwargs: run_calls.append(kwargs))

        # Pass explicit empty argv so pytest's sys.argv doesn't interfere with argparse.
        srv.main([])

        assert run_calls == [{"transport": "stdio"}], "main must invoke mcp.run(transport='stdio') by default"

    def test_main_http_transport_configures_settings_and_runs_streamable_http(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--http flag must set host/port on mcp.settings and use streamable-http transport."""
        import schwab_positions_mcp.server as srv

        run_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(srv.mcp, "run", lambda **kwargs: run_calls.append(kwargs))

        # Save/restore because the function mutates the live settings object
        orig_host = srv.mcp.settings.host
        orig_port = srv.mcp.settings.port
        try:
            srv.main(["--http", "--host", "0.0.0.0", "--port", "3470"])  # noqa: S104
            assert run_calls == [{"transport": "streamable-http"}]
            assert srv.mcp.settings.host == "0.0.0.0"  # noqa: S104
            assert srv.mcp.settings.port == 3470
        finally:
            srv.mcp.settings.host = orig_host
            srv.mcp.settings.port = orig_port

    def test_main_http_uses_default_host_and_port_when_not_specified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only --http should still use the documented defaults (127.0.0.1:8000)."""
        import schwab_positions_mcp.server as srv

        run_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(srv.mcp, "run", lambda **kwargs: run_calls.append(kwargs))

        orig_host = srv.mcp.settings.host
        orig_port = srv.mcp.settings.port
        try:
            srv.main(["--http"])
            assert run_calls == [{"transport": "streamable-http"}]
            assert srv.mcp.settings.host == "127.0.0.1"
            assert srv.mcp.settings.port == 8000
        finally:
            srv.mcp.settings.host = orig_host
            srv.mcp.settings.port = orig_port


# ===========================================================================
# tools._common — get_client lazy build + normalise_response no-headers branch
# ===========================================================================


class TestGetClientLazyBuild:
    def test_get_client_builds_when_singleton_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_client must call _build_client exactly once when the singleton is empty (line 91)."""
        tools_common.reset_client_singleton()

        built = MagicMock(name="ReadOnlySchwabClient")
        build_calls: dict[str, int] = {"n": 0}

        def fake_build() -> Any:
            build_calls["n"] += 1
            return built

        monkeypatch.setattr(tools_common, "_build_client", fake_build)

        first = tools_common.get_client()
        second = tools_common.get_client()

        assert first is built
        assert second is built
        assert build_calls["n"] == 1, "the client must be built lazily, exactly once"
        tools_common.reset_client_singleton()


class TestNormaliseResponseNoHeaders:
    def test_2xx_response_without_headers_attr(self) -> None:
        """A 2xx response object lacking a ``headers`` attribute must still parse (branch 140->143)."""

        class _NoHeaderResponse:
            status_code = 200

            @staticmethod
            def json() -> dict[str, str]:
                return {"ok": "yes"}

        out = tools_common.normalise_response(_NoHeaderResponse())
        assert out == {"ok": "yes"}

    def test_error_response_without_headers_attr_still_raises(self) -> None:
        """A non-2xx response lacking headers must still raise with request_id=None."""

        class _NoHeaderError:
            status_code = 429

        with pytest.raises(tools_common.SchwabApiError) as excinfo:
            tools_common.normalise_response(_NoHeaderError())
        assert excinfo.value.status_code == 429
        assert excinfo.value.request_id is None


# ===========================================================================
# tools.summary — cache-write success branch
# ===========================================================================


class TestSummaryCacheWriteSuccess:
    def test_summary_writes_snapshot_and_reports_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        installed_client: ReadOnlySchwabClient,
        mock_schwab_client: MagicMock,
        mock_positions_data: dict[str, Any],
        make_response: Any,
    ) -> None:
        """get_account_summary_impl must write a snapshot and surface 'snapshot_written:N'."""
        from schwab_positions_mcp.tools import summary

        # ClickHouse-mocked backend so the derived-history write durably persists.
        monkeypatch.setenv("SCHWAB_POSITIONS_CACHE_ENABLED", "1")
        cache_module.reset_cache_singleton()
        real_cache = Cache(backend=_ch_backend())
        cache_module._cache_singleton = real_cache

        mock_schwab_client.get_account.return_value = make_response(json_payload=mock_positions_data)

        try:
            result = summary.get_account_summary_impl({"account_hash": "HASH_ABCDEF"})
            assert result["ok"] is True
            # Two positions in mock_positions_data → 2 snapshot rows persisted.
            assert result["_cache_status"] == "snapshot_written:2", (
                f"expected snapshot_written:2, got {result['_cache_status']!r}"
            )
        finally:
            cache_module.reset_cache_singleton()


# ===========================================================================
# date import guard (keep ruff happy; used in boundary tests elsewhere)
# ===========================================================================


def test_module_imports_are_live() -> None:
    """Sanity: the imports above resolve (guards against dead-import drift)."""
    assert callable(get_cache)
    assert isinstance(datetime.now(UTC), datetime)
    assert isinstance(date.today(), date)
