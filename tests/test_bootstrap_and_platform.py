"""Tests for ``bootstrap.bootstrap_dotenv`` and ``_platform`` shims."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from schwab_positions_mcp import _platform, bootstrap


class TestBootstrapDotenv:
    def test_returns_bool(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        assert bootstrap.bootstrap_dotenv() in (True, False)

    def test_loads_env_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("SCHWAB_TEST_BOOTSTRAP_VAR=loaded\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("SCHWAB_TEST_BOOTSTRAP_VAR", raising=False)
        bootstrap.bootstrap_dotenv()
        assert os.environ.get("SCHWAB_TEST_BOOTSTRAP_VAR") == "loaded"

    def test_host_env_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        env = tmp_path / ".env"
        env.write_text("SCHWAB_TEST_BOOTSTRAP_VAR=from_file\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("SCHWAB_TEST_BOOTSTRAP_VAR", "from_host")
        bootstrap.bootstrap_dotenv()
        assert os.environ.get("SCHWAB_TEST_BOOTSTRAP_VAR") == "from_host"

    def test_no_env_file_is_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # No .env in tmp_path; must not raise.
        bootstrap.bootstrap_dotenv()


class TestPlatformShims:
    def test_state_root_returns_path(self) -> None:
        assert isinstance(_platform.state_root(), Path)

    def test_state_root_with_xdg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert _platform.state_root() == tmp_path

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only chmod path")
    def test_secure_chmod_posix(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        _platform.secure_chmod(f, 0o600)
        assert _platform.is_secure_perms(f, 0o600)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only fchmod")
    def test_secure_fchmod_posix(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        fd = os.open(str(f), os.O_RDWR)
        try:
            _platform.secure_fchmod(fd, 0o600)
        finally:
            os.close(fd)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only umask")
    def test_restrictive_umask_posix(self) -> None:
        with _platform.restrictive_umask():
            assert os.umask(0o077) == 0o077

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only flock")
    def test_exclusive_file_lock_posix(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        fd = os.open(str(f), os.O_RDWR)
        try:
            with _platform.exclusive_file_lock(fd):
                pass
        finally:
            os.close(fd)

    def test_file_mode_returns_int(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        assert isinstance(_platform.file_mode(f), int)

    def test_is_secure_perms_missing_path(self, tmp_path: Path) -> None:
        assert _platform.is_secure_perms(tmp_path / "no-such-file", 0o600) is False

    def test_notify_desktop_never_raises(self) -> None:
        # No display server / shutil-which path may return None — we just want
        # to confirm the function handles the absence gracefully.
        _platform.notify_desktop("title", "message")
