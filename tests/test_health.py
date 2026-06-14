"""``health.py`` unit tests — exit-code matrix, probes, and side-channels.

All tests are hermetic: the conftest ``_no_real_creds`` fixture strips real
Schwab credentials and points ``SCHWAB_POSITIONS_TOKEN_PATH`` at a tmp dir, so
the schwab-py primary probe always falls back to mtime unless a test
explicitly monkeypatches ``schwab.auth.easy_client``. No real OAuth, no
network.
"""

from __future__ import annotations

import json
import os
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from schwab_positions_mcp import health

# ---------------------------------------------------------------------------
# redact_secrets
# ---------------------------------------------------------------------------


class TestRedact:
    def test_redacts_bearer(self) -> None:
        out = health.redact_secrets("Authorization: Bearer abc.def-123")
        assert "abc.def-123" not in out
        assert "***REDACTED***" in out

    def test_redacts_access_and_refresh_token(self) -> None:
        out = health.redact_secrets('{"access_token": "SEKRET1", "refresh_token": "SEKRET2"}')
        assert "SEKRET1" not in out
        assert "SEKRET2" not in out

    def test_passthrough_when_no_secret(self) -> None:
        assert health.redact_secrets("nothing to see") == "nothing to see"


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


class TestPureFunctions:
    def test_classify_table(self) -> None:
        assert health.classify(timedelta(0)) == health.HealthExit.EXPIRED_OR_12H
        assert health.classify(timedelta(hours=11)) == health.HealthExit.EXPIRED_OR_12H
        assert health.classify(timedelta(hours=23)) == health.HealthExit.EXPIRES_24H
        assert health.classify(timedelta(hours=25)) == health.HealthExit.HEALTHY
        assert health.classify(timedelta(seconds=-1)) == health.HealthExit.EXPIRED_OR_12H

    def test_compute_expires_in_zero_age(self) -> None:
        now = datetime.now(tz=UTC).timestamp()
        assert health.compute_expires_in(now, now_ts=now) == timedelta(days=7)

    def test_compute_expires_in_old_token(self) -> None:
        now = datetime.now(tz=UTC).timestamp()
        assert health.compute_expires_in(now - 8 * 86400, now_ts=now) <= timedelta(0)

    def test_compute_expires_in_default_now(self) -> None:
        # now_ts omitted → uses wall clock; a token created "now" ≈ 7 days left.
        out = health.compute_expires_in(datetime.now(tz=UTC).timestamp())
        assert timedelta(days=6, hours=23) < out <= timedelta(days=7)

    def test_human_summary_all_codes(self) -> None:
        cases = [
            (health.HealthExit.HEALTHY, timedelta(hours=72)),
            (health.HealthExit.EXPIRES_24H, timedelta(hours=20)),
            (health.HealthExit.EXPIRED_OR_12H, None),
            (health.HealthExit.MISSING, None),
            (health.HealthExit.MALFORMED, None),
            (health.HealthExit.INSECURE_PERMS, None),
            (99, None),  # unknown
        ]
        for code, exp in cases:
            out = health.human_summary(code, exp)
            assert isinstance(out, str)
            assert "Schwab Positions MCP" in out


# ---------------------------------------------------------------------------
# resolve_token_path
# ---------------------------------------------------------------------------


class TestResolveTokenPath:
    def test_explicit_arg_wins(self, tmp_path: Path) -> None:
        p = tmp_path / "explicit.json"
        assert health.resolve_token_path(str(p)) == p

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        target = tmp_path / "envtok.json"
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(target))
        assert health.resolve_token_path(None) == target

    def test_default_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_TOKEN_PATH", raising=False)
        out = health.resolve_token_path(None)
        assert out == health._DEFAULT_CONFIG_DIR / health._TOKEN_FILE_NAME


# ---------------------------------------------------------------------------
# check_token_file_state
# ---------------------------------------------------------------------------


class TestCheckTokenFileState:
    def test_missing(self, tmp_path: Path) -> None:
        assert health.check_token_file_state(tmp_path / "nope.json") is health.TokenState.MISSING

    @pytest.mark.posix_only
    def test_insecure_perms(self, tmp_path: Path) -> None:
        f = tmp_path / "token.json"
        f.write_text("{}")
        os.chmod(f, 0o644)
        assert health.check_token_file_state(f) is health.TokenState.INSECURE_PERMS

    def test_malformed(self, tmp_path: Path) -> None:
        f = tmp_path / "token.json"
        f.write_text("not-json")
        os.chmod(f, 0o600)
        assert health.check_token_file_state(f) is health.TokenState.MALFORMED

    def test_valid(self, tmp_path: Path) -> None:
        f = tmp_path / "token.json"
        f.write_text(json.dumps({"creation_timestamp": 1700000000}))
        os.chmod(f, 0o600)
        assert health.check_token_file_state(f) is health.TokenState.VALID

    def test_malformed_on_read_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "token.json"
        f.write_text("{}")
        os.chmod(f, 0o600)

        def _boom(*_a: object, **_k: object) -> str:
            raise OSError("unreadable")

        monkeypatch.setattr(Path, "read_text", _boom)
        assert health.check_token_file_state(f) is health.TokenState.MALFORMED


# ---------------------------------------------------------------------------
# Side channels
# ---------------------------------------------------------------------------


class TestSideChannels:
    def test_write_desktop_marker_redacts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "Desktop").mkdir()
        out = health._write_desktop_marker("hello", hint="Authorization: Bearer leaky.payload")
        assert out is not None
        body = out.read_text()
        assert "hello" in body
        assert "leaky.payload" not in body
        assert "***REDACTED***" in body
        assert "schwab_positions_mcp.auth login_flow" in body
        assert out.name == "SCHWAB_POSITIONS_REAUTH_NEEDED.md"

    def test_write_desktop_marker_no_desktop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        assert health._write_desktop_marker("nothing") is None

    def test_write_desktop_marker_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "Desktop").mkdir()

        def _boom(*_a: object, **_k: object) -> int:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", _boom)
        assert health._write_desktop_marker("hello") is None

    def test_notify_silent_on_unknown_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "fictional-os")
        health._notify("doesn't matter")  # must not raise

    def test_emit_stderr_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        health._emit_stderr({"event": "test", "x": 1})
        err = capsys.readouterr().err
        assert json.loads(err.strip()) == {"event": "test", "x": 1}


# ---------------------------------------------------------------------------
# Token-age probes
# ---------------------------------------------------------------------------


def _install_fake_easy_client(monkeypatch: pytest.MonkeyPatch, age: object) -> None:
    """Inject a fake ``schwab.auth`` module whose client returns *age*."""

    class _FakeClient:
        def token_age(self) -> object:
            return age

    def _easy_client(**_kwargs: object) -> _FakeClient:
        return _FakeClient()

    fake_auth = types.ModuleType("schwab.auth")
    fake_auth.easy_client = _easy_client  # type: ignore[attr-defined]
    fake_schwab = types.ModuleType("schwab")
    monkeypatch.setitem(sys.modules, "schwab", fake_schwab)
    monkeypatch.setitem(sys.modules, "schwab.auth", fake_auth)


class TestProbes:
    def test_schwab_py_no_creds_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_API_KEY", raising=False)
        monkeypatch.delenv("SCHWAB_APP_SECRET", raising=False)
        assert health._probe_token_age_via_schwab_py(tmp_path / "token.json") is None

    def test_schwab_py_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")
        _install_fake_easy_client(monkeypatch, timedelta(days=2))
        out = health._probe_token_age_via_schwab_py(tmp_path / "token.json")
        assert out == timedelta(days=5)  # 7 - 2

    def test_schwab_py_non_timedelta_age(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")
        _install_fake_easy_client(monkeypatch, "not-a-timedelta")
        assert health._probe_token_age_via_schwab_py(tmp_path / "token.json") is None

    def test_schwab_py_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")

        def _easy_client(**_kwargs: object) -> object:
            raise RuntimeError("boom")

        fake_auth = types.ModuleType("schwab.auth")
        fake_auth.easy_client = _easy_client  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "schwab", types.ModuleType("schwab"))
        monkeypatch.setitem(sys.modules, "schwab.auth", fake_auth)
        assert health._probe_token_age_via_schwab_py(tmp_path / "token.json") is None

    def test_mtime_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "token.json"
        f.write_text("{}")
        fresh = datetime.now(tz=UTC).timestamp()
        os.utime(f, (fresh, fresh))
        out = health._probe_token_age_via_mtime(f)
        assert out is not None
        assert timedelta(days=6, hours=23) < out <= timedelta(days=7)

    def test_mtime_missing_returns_none(self, tmp_path: Path) -> None:
        assert health._probe_token_age_via_mtime(tmp_path / "nope.json") is None


# ---------------------------------------------------------------------------
# run() — full state matrix
# ---------------------------------------------------------------------------


def _setup_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, content: str = "{}") -> Path:
    f = tmp_path / "token.json"
    f.write_text(content)
    os.chmod(f, 0o600)
    monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(f))
    return f


class TestRun:
    def test_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "absent.json"))
        assert health.run(None) == health.HealthExit.MISSING

    @pytest.mark.posix_only
    def test_insecure_perms(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = _setup_token(tmp_path, monkeypatch)
        os.chmod(f, 0o644)
        assert health.run(None) == health.HealthExit.INSECURE_PERMS

    def test_malformed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup_token(tmp_path, monkeypatch, content="not-json")
        assert health.run(None) == health.HealthExit.MALFORMED

    def test_valid_healthy_via_mtime(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = _setup_token(tmp_path, monkeypatch)
        fresh = datetime.now(tz=UTC).timestamp()
        os.utime(f, (fresh, fresh))
        assert health.run(None) == health.HealthExit.HEALTHY

    def test_valid_healthy_via_schwab_py(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup_token(tmp_path, monkeypatch)
        monkeypatch.setenv("SCHWAB_API_KEY", "k")
        monkeypatch.setenv("SCHWAB_APP_SECRET", "s")
        _install_fake_easy_client(monkeypatch, timedelta(days=3))
        assert health.run(None) == health.HealthExit.HEALTHY

    def test_warn_24h(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = _setup_token(tmp_path, monkeypatch)
        old = datetime.now(tz=UTC).timestamp() - (7 * 86400 - 18 * 3600)  # 18h left
        os.utime(f, (old, old))
        assert health.run(None) == health.HealthExit.EXPIRES_24H

    def test_critical_12h(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = _setup_token(tmp_path, monkeypatch)
        old = datetime.now(tz=UTC).timestamp() - 6.6 * 86400  # ~9.6h left
        os.utime(f, (old, old))
        assert health.run(None) in (
            health.HealthExit.EXPIRED_OR_12H,
            health.HealthExit.EXPIRES_24H,
        )

    def test_both_probes_fail(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _setup_token(tmp_path, monkeypatch)
        monkeypatch.setattr(health, "_probe_token_age_via_schwab_py", lambda _p: None)
        monkeypatch.setattr(health, "_probe_token_age_via_mtime", lambda _p: None)
        assert health.run(None) == health.HealthExit.EXPIRED_OR_12H


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_no_args_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHWAB_POSITIONS_TOKEN_PATH", str(tmp_path / "absent.json"))
        assert health.cli_main([]) == health.HealthExit.MISSING

    def test_with_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SCHWAB_POSITIONS_TOKEN_PATH", raising=False)
        # config-dir has no token.json → MISSING
        assert health.cli_main(["--config-dir", str(tmp_path)]) == health.HealthExit.MISSING
