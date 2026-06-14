"""Tests for ``health_check`` and ``get_server_info`` meta tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schwab_positions_mcp import __version__
from schwab_positions_mcp.tools import meta


class TestHealthCheck:
    def test_includes_is_read_only_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
        out = meta.health_check_impl()
        assert out["is_read_only"] is True

    def test_handles_token_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
        monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
        out = meta.health_check_impl()
        assert out["status"] == "needs_env_setup"
        assert out["checks"]["token_present"] is False

    def test_status_needs_oauth_login_when_creds_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "missing.json"))
        out = meta.health_check_impl()
        assert out["status"] == "needs_oauth_login"

    def test_overall_status_ready(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        token = tmp_path / "tok.json"
        token.write_text(json.dumps({"access_token": "x"}))
        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(token))
        out = meta.health_check_impl()
        assert out["status"] == "ready"
        assert out["checks"]["token_present"] is True

    def test_reports_token_age_and_expiry_when_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import os
        from datetime import UTC, datetime, timedelta

        token = tmp_path / "tok.json"
        token.write_text(json.dumps({"access_token": "x"}))
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(token))
        # Backdate mtime by 2 days → age≈2, expires_in≈5.
        old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
        os.utime(token, (old, old))
        out = meta.health_check_impl()
        assert out["checks"]["token_age_days"] == pytest.approx(2.0, abs=0.05)
        assert out["checks"]["token_expires_in_days"] == pytest.approx(5.0, abs=0.05)

    def test_token_age_none_when_absent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "missing.json"))
        out = meta.health_check_impl()
        assert out["checks"]["token_age_days"] is None
        assert out["checks"]["token_expires_in_days"] is None

    def test_token_age_none_on_stat_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        token = tmp_path / "tok.json"
        token.write_text("{}")
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(token))

        # exists() returns True (token_present) but the subsequent stat() in the
        # age block raises — exercises the OSError guard inside the age branch.
        monkeypatch.setattr(Path, "exists", lambda _self: True)

        def _boom_stat(_self: Path, *a: object, **k: object) -> object:
            raise OSError("stat failed")

        monkeypatch.setattr(Path, "stat", _boom_stat)
        out = meta.health_check_impl()
        assert out["checks"]["token_present"] is True
        assert out["checks"]["token_age_days"] is None
        assert out["checks"]["token_expires_in_days"] is None

    def test_checked_at_is_iso_utc(self) -> None:
        out = meta.health_check_impl()
        assert out["checked_at"].endswith("+00:00")


class TestGetServerInfo:
    def test_returns_version(self) -> None:
        out = meta.get_server_info_impl()
        assert out["version"] == __version__

    def test_lists_8_tools(self) -> None:
        out = meta.get_server_info_impl()
        assert len(out["tools"]) == 8
        for name in (
            "get_accounts",
            "get_account_numbers",
            "get_account_positions",
            "get_orders_history",
            "get_transactions",
            "get_account_summary",
            "health_check",
            "get_server_info",
        ):
            assert name in out["tools"]

    def test_declares_read_only(self) -> None:
        out = meta.get_server_info_impl()
        assert out["is_read_only"] is True
        assert out["trade_endpoints_exposed"] is False

    def test_security_doc_referenced(self) -> None:
        out = meta.get_server_info_impl()
        assert out["security_doc"] == "docs/SECURITY.md"

    def test_includes_platform_metadata(self) -> None:
        out = meta.get_server_info_impl()
        assert isinstance(out["platform"], str)
        assert isinstance(out["python_version"], str)
        assert out["name"] == "schwab-positions-mcp"

    def test_payload_argument_is_ignored(self) -> None:
        out = meta.get_server_info_impl({"anything": "is_ignored"})  # type: ignore[arg-type]
        assert out["name"] == "schwab-positions-mcp"

    def test_health_payload_argument_is_ignored(self) -> None:
        out = meta.health_check_impl({"anything": "is_ignored"})
        assert "checked_at" in out


class TestVersionConsistency:
    """The dunder version must match what server_info reports."""

    def test_server_info_version_matches_init(self) -> None:
        from schwab_positions_mcp import __version__ as init_version

        info = meta.get_server_info_impl()
        assert info["version"] == init_version
