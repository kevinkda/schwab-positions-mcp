"""Token-health probe CLI — invoked from cron / launchd / Task Scheduler.

Run with ``python -m schwab_positions_mcp.health``.

Schwab's **refresh token has a hard 7-day lifetime that cannot be extended
or rotated** — confirmed by both the schwab-py docs ("There is currently no
way to make a refresh token last longer than seven days") and the Schwab
developer portal. Using the refresh token to mint a new access token does
**not** reset the 7-day clock; the window is measured from the token's
original ``creation_timestamp``. There is therefore no fully-automatic
keep-alive: every 7 days the user must re-run the browser OAuth flow.

This probe makes that re-authorization *predictable* instead of surprising:
a cron / launchd job runs it periodically, and when the refresh token is
within ~24 h of expiry (or already expired / missing / corrupt) it fires a
best-effort desktop notification and writes a Markdown marker on the user's
Desktop with the exact re-auth command.

Exit codes:

    0 — healthy
    1 — expires_in < 24 h (warn)
    2 — expires_in < 12 h **or** already expired (error; cron MAILTO triggers)
    3 — token file missing
    4 — token file malformed (not JSON)
    5 — token file has insecure permissions (POSIX only)

Side-effects (all best-effort, never raise):
    * Writes/overwrites ``~/Desktop/SCHWAB_POSITIONS_REAUTH_NEEDED.md`` for any
      non-zero exit code.
    * Fires a desktop notification via ``_platform.notify_desktop`` —
      ``osascript`` (macOS) / ``notify-send`` (Linux) / plyer + PowerShell
      (Windows).
    * Emits one line of structured JSON to stderr.

This module is **offline-safe**: the primary token-age probe asks schwab-py
for ``client.token_age()`` (which reads the token's ``creation_timestamp``),
but if no real credentials are present it silently falls back to the token
file's mtime — it never opens a browser and never mutates Schwab state.

DO NOT register this module as an MCP server: like ``auth.py`` it writes to
stdout/stderr and would corrupt JSON-RPC.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Final

from . import _platform
from .auth import _DEFAULT_CONFIG_DIR, _TOKEN_FILE_NAME

# Schwab refresh-token lifetime (days). Hard limit, not extendable.
REFRESH_TOKEN_LIFETIME_DAYS: Final[int] = 7
WARN_THRESHOLD: Final[timedelta] = timedelta(hours=24)
CRITICAL_THRESHOLD: Final[timedelta] = timedelta(hours=12)
DESKTOP_REAUTH_FILE: Final[str] = "SCHWAB_POSITIONS_REAUTH_NEEDED.md"

#: Required POSIX permission bits for the token file (owner read/write only).
_TOKEN_FILE_MODE: Final[int] = stat.S_IRUSR | stat.S_IWUSR  # 0o600

#: Patterns redacted before any secret-bearing string reaches the marker file.
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(access_token\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]+"),
    re.compile(r"(?i)(refresh_token\"?\s*[:=]\s*\"?)[A-Za-z0-9._\-]+"),
)


def redact_secrets(text: str) -> str:
    """Best-effort redaction of token-like material from human-facing text."""
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub(r"\1***REDACTED***", out)
    return out


class TokenState(Enum):
    """Outcome of inspecting the token file on disk."""

    MISSING = "missing"
    INSECURE_PERMS = "insecure_perms"
    MALFORMED = "malformed"
    VALID = "valid"


class HealthExit:
    """Process exit codes (stable contract for cron / launchd)."""

    HEALTHY = 0
    EXPIRES_24H = 1
    EXPIRED_OR_12H = 2
    MISSING = 3
    MALFORMED = 4
    INSECURE_PERMS = 5


# ---------------------------------------------------------------------------
# Token-path resolution
# ---------------------------------------------------------------------------


def resolve_token_path(token_path_arg: str | None) -> Path:
    """Resolve the token-file path.

    Precedence (highest first):
        1. Explicit ``token_path_arg`` (from ``--config-dir``).
        2. ``$SCHWAB_POSITIONS_TOKEN_PATH`` (matches the MCP ``health_check``
           tool and the test harness).
        3. Default ``~/.config/schwab-positions-mcp/token.json``.
    """
    if token_path_arg:
        return Path(token_path_arg).expanduser()
    env_override = os.environ.get("SCHWAB_POSITIONS_TOKEN_PATH")
    if env_override:
        return Path(env_override).expanduser()
    return _DEFAULT_CONFIG_DIR / _TOKEN_FILE_NAME


def check_token_file_state(path: Path) -> TokenState:
    """Classify the token file: missing → insecure-perms → malformed → valid.

    The order matters: we refuse to parse a file we cannot trust the perms
    of (defense in depth), and we treat unreadable/parse failures as
    ``MALFORMED`` rather than crashing the probe.
    """
    if not path.exists():
        return TokenState.MISSING
    if not _platform.is_secure_perms(path, _TOKEN_FILE_MODE):
        return TokenState.INSECURE_PERMS
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TokenState.MALFORMED
    return TokenState.VALID


# ---------------------------------------------------------------------------
# Pure helpers (kept thin & testable)
# ---------------------------------------------------------------------------


def compute_expires_in(creation_ts: float, *, now_ts: float | None = None) -> timedelta:
    """Time-to-expiry from a token's creation timestamp (seconds since epoch)."""
    now = now_ts if now_ts is not None else datetime.now(tz=UTC).timestamp()
    age = timedelta(seconds=max(0.0, now - creation_ts))
    lifetime = timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)
    return lifetime - age


def classify(expires_in: timedelta) -> int:
    """Map a time-to-expiry to one of the :class:`HealthExit` codes."""
    if expires_in < CRITICAL_THRESHOLD:
        return HealthExit.EXPIRED_OR_12H
    if expires_in < WARN_THRESHOLD:
        return HealthExit.EXPIRES_24H
    return HealthExit.HEALTHY


def human_summary(exit_code: int, expires_in: timedelta | None) -> str:
    """One-line summary suitable for desktop notification + log."""
    if exit_code == HealthExit.HEALTHY:
        assert expires_in is not None
        hours = int(expires_in.total_seconds() // 3600)
        return f"Schwab Positions MCP: token healthy, expires in ~{hours}h"
    if exit_code == HealthExit.EXPIRES_24H:
        assert expires_in is not None
        hours = int(expires_in.total_seconds() // 3600)
        return f"Schwab Positions MCP: token expires in {hours}h — please reauthorize soon"
    if exit_code == HealthExit.EXPIRED_OR_12H:
        return "Schwab Positions MCP: token expired or <12h left — run auth login_flow"
    if exit_code == HealthExit.MISSING:
        return "Schwab Positions MCP: token not initialized — run auth login_flow"
    if exit_code == HealthExit.MALFORMED:
        return "Schwab Positions MCP: token file corrupted — back it up and re-run auth"
    if exit_code == HealthExit.INSECURE_PERMS:
        return "Schwab Positions MCP: token file has insecure permissions — chmod 600"
    return f"Schwab Positions MCP: unknown state ({exit_code})"


# ---------------------------------------------------------------------------
# Notification side channels (best-effort)
# ---------------------------------------------------------------------------


def _write_desktop_marker(message: str, *, hint: str = "") -> Path | None:
    """Write ``~/Desktop/SCHWAB_POSITIONS_REAUTH_NEEDED.md``.

    Returns the path on success, ``None`` if there is no Desktop dir or the
    write fails. Any secret-bearing ``hint`` is redacted first.
    """
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        return None
    target = desktop / DESKTOP_REAUTH_FILE
    body = (
        "# Schwab Positions MCP — Reauthorize required\n\n"
        f"_Generated by `schwab_positions_mcp.health` at "
        f"{datetime.now(tz=UTC).isoformat(timespec='seconds')}_\n\n"
        f"## Status\n\n{message}\n\n"
    )
    if hint:
        body += f"## Hint\n\n```\n{redact_secrets(hint)}\n```\n\n"
    body += (
        "## What to do next\n\n"
        "Schwab refresh tokens expire after 7 days and cannot be auto-renewed.\n"
        "Re-mint one with a single command:\n\n"
        "1. Open a terminal in your `schwab-positions-mcp` checkout.\n"
        "2. Run:\n\n"
        "   ```bash\n"
        "   uv run python -m schwab_positions_mcp.auth login_flow\n"
        "   ```\n\n"
        "   (Your App Key / Secret are already saved in `.env` — you only\n"
        "   re-do the browser login, not the registration.)\n\n"
        "3. After the browser flow completes, this file is regenerated on the\n"
        "   next health probe with a healthy status.\n"
    )
    try:
        target.write_text(body, encoding="utf-8")
    except OSError:
        return None
    return target


def _notify(message: str) -> None:
    """Fire a best-effort desktop notification — never raises."""
    _platform.notify_desktop("Schwab Positions MCP", message)


def _emit_stderr(record: dict[str, object]) -> None:
    sys.stderr.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Token-age probe with primary + fall-back paths
# ---------------------------------------------------------------------------


def _probe_token_age_via_schwab_py(token_path: Path) -> timedelta | None:
    """Primary path — ``schwab.client.Client.token_age()``.

    ``token_age()`` is computed from the token's ``creation_timestamp``, which
    is the *correct* basis for the 7-day window (mtime only reflects the last
    refresh). Returns ``None`` — so the caller falls back to mtime — if creds
    are absent or anything goes wrong. ``interactive=False`` guarantees we
    never open a browser.
    """
    api_key = os.environ.get("SCHWAB_API_KEY")
    app_secret = os.environ.get("SCHWAB_APP_SECRET")
    callback = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")
    if not api_key or not app_secret:
        return None
    try:
        from schwab.auth import easy_client

        client = easy_client(
            api_key=api_key,
            app_secret=app_secret,
            callback_url=callback,
            token_path=str(token_path),
            asyncio=False,
            enforce_enums=False,
            interactive=False,
        )
        age = client.token_age()
        if isinstance(age, timedelta):
            return timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS) - age
    except Exception:
        return None
    return None


def _probe_token_age_via_mtime(token_path: Path) -> timedelta | None:
    """Fall-back — read the file mtime (degraded mode, no creds)."""
    try:
        mtime = token_path.stat().st_mtime
    except OSError:
        return None
    age = datetime.now(tz=UTC).timestamp() - mtime
    return timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS) - timedelta(seconds=max(0.0, age))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(token_path_arg: str | None = None) -> int:
    """Run the health probe and return the exit code."""
    token_path = resolve_token_path(token_path_arg)
    state = check_token_file_state(token_path)

    if state is TokenState.MISSING:
        msg = human_summary(HealthExit.MISSING, None)
        _emit_stderr({"event": "health", "state": "missing", "path": str(token_path)})
        _write_desktop_marker(msg)
        _notify(msg)
        return HealthExit.MISSING

    if state is TokenState.INSECURE_PERMS:
        actual = _platform.file_mode(token_path)
        hint = f"token file mode is {oct(actual)}; expected 0o600. Run: chmod 600 {token_path}"
        msg = human_summary(HealthExit.INSECURE_PERMS, None)
        _emit_stderr({"event": "health", "state": "insecure_perms", "actual_mode": oct(actual)})
        _write_desktop_marker(msg, hint=hint)
        _notify(msg)
        return HealthExit.INSECURE_PERMS

    if state is TokenState.MALFORMED:
        msg = human_summary(HealthExit.MALFORMED, None)
        _emit_stderr({"event": "health", "state": "malformed", "path": str(token_path)})
        _write_desktop_marker(msg)
        _notify(msg)
        return HealthExit.MALFORMED

    # state is VALID — probe age: primary (schwab-py) → fall-back (mtime).
    expires_in = _probe_token_age_via_schwab_py(token_path)
    probe_path = "schwab_py"
    if expires_in is None:
        expires_in = _probe_token_age_via_mtime(token_path)
        probe_path = "mtime_fallback"
    if expires_in is None:
        # Both paths failed — treat as worst-case (critical).
        _emit_stderr({"event": "health", "state": "probe_failed"})
        _write_desktop_marker(human_summary(HealthExit.EXPIRED_OR_12H, None))
        _notify(human_summary(HealthExit.EXPIRED_OR_12H, None))
        return HealthExit.EXPIRED_OR_12H

    code = classify(expires_in)
    _emit_stderr(
        {
            "event": "health",
            "state": "probed",
            "probe_path": probe_path,
            "expires_in_seconds": int(expires_in.total_seconds()),
            "exit_code": code,
        }
    )

    msg = human_summary(code, expires_in)
    if code != HealthExit.HEALTHY:
        _write_desktop_marker(msg)
        _notify(msg)
    return code


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schwab_positions_mcp.health")
    parser.add_argument(
        "--config-dir",
        type=str,
        default=None,
        help="Override the token directory (token.json is read from inside it).",
    )
    args = parser.parse_args(argv)
    token_arg: str | None = None
    if args.config_dir:
        token_arg = str(Path(args.config_dir) / _TOKEN_FILE_NAME)
    return run(token_arg)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_main())
