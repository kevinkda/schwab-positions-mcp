"""OAuth CLI for schwab-positions-mcp.

# pragma: no cover  (entire module — see ``[tool.coverage.run].omit``)

Two sub-commands:
    * ``login_flow``  — :func:`schwab.auth.client_from_login_flow` (browser).
    * ``manual_flow`` — :func:`schwab.auth.client_from_manual_flow` (paste).

Token is persisted to ``~/.config/schwab-positions-mcp/token.json`` (separate
directory from ``schwab-marketdata-mcp`` to keep the read-only credential set
isolated from the market-data credential set).

OAuth scope: ``trade`` is required by Schwab Trader API even for read-only
positions / orders / transactions endpoints. The mutation surface is blocked
at the code layer (see :mod:`schwab_positions_mcp.client`) and at the CI grep
gate (``.github/workflows/security-grep.yml``). See ``docs/SECURITY.md`` for
the full 5-layer boundary.

DO NOT register this module as an MCP server: the OAuth flows write to stdout
to talk to the user's browser and would corrupt JSON-RPC.
"""

# pragma: no cover

from __future__ import annotations

import argparse
import logging
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bootstrap import bootstrap_dotenv

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "schwab-positions-mcp"
_TOKEN_FILE_NAME = "token.json"


class SchwabAuthError(RuntimeError):
    """Raised when OAuth pre-flight or token I/O fails."""


@dataclass(frozen=True)
class AuthConfig:
    api_key: str
    app_secret: str
    callback_url: str
    token_path: Path


def _resolve_config_dir(override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_CONFIG_DIR


def build_auth_config(config_dir: str | None = None) -> AuthConfig:
    api_key = os.environ.get("SCHWAB_API_KEY", "").strip()
    app_secret = os.environ.get("SCHWAB_APP_SECRET", "").strip()
    callback_url = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182").strip()
    if not api_key or not app_secret:
        raise SchwabAuthError("Missing SCHWAB_API_KEY / SCHWAB_APP_SECRET. Populate .env from .env.example.")
    cfg_dir = _resolve_config_dir(config_dir)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cfg_dir, 0o700)
    except OSError:
        pass
    token_path = cfg_dir / _TOKEN_FILE_NAME
    return AuthConfig(
        api_key=api_key,
        app_secret=app_secret,
        callback_url=callback_url,
        token_path=token_path,
    )


def preflight_summary(cfg: AuthConfig) -> str:
    return (
        "schwab-positions-mcp OAuth pre-flight\n"
        f"  api_key      = {cfg.api_key[:4]}…\n"
        f"  callback_url = {cfg.callback_url}\n"
        f"  token_path   = {cfg.token_path}\n"
        "  scope        = trade (required by Schwab; mutation blocked at code layer)\n"
    )


def make_token_write_func(token_path: Path) -> Any:
    """Return a callback that schwab-py calls with each refreshed token.

    Persists to disk with mode 0o600 so the file is unreadable by other users.
    """

    def _write(token: dict[str, Any]) -> None:
        import json

        token_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(token), encoding="utf-8")
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp, token_path)

    return _write


def _emit_scope_warning() -> None:
    logger.warning(
        "OAuth scope=trade required for read-only positions API; "
        "mutation calls are blocked at code level. See docs/SECURITY.md."
    )


def _run_login_flow(args: argparse.Namespace) -> int:
    cfg = build_auth_config(config_dir=args.config_dir)
    print(preflight_summary(cfg), file=sys.stderr)
    _emit_scope_warning()
    if getattr(args, "dry_run", False):
        print("dry-run: pre-flight passed; skipping browser flow.", file=sys.stderr)
        return 0

    from schwab.auth import client_from_login_flow

    client_from_login_flow(
        api_key=cfg.api_key,
        app_secret=cfg.app_secret,
        callback_url=cfg.callback_url,
        token_path=str(cfg.token_path),
        asyncio=False,
        enforce_enums=False,
        token_write_func=make_token_write_func(cfg.token_path),
    )
    print(f"OK — token persisted at {cfg.token_path}", file=sys.stderr)
    return 0


def _run_manual_flow(args: argparse.Namespace) -> int:
    cfg = build_auth_config(config_dir=args.config_dir)
    print(preflight_summary(cfg), file=sys.stderr)
    _emit_scope_warning()
    if getattr(args, "dry_run", False):
        print("dry-run: pre-flight passed; skipping interactive paste.", file=sys.stderr)
        return 0

    from schwab.auth import client_from_manual_flow

    client_from_manual_flow(
        api_key=cfg.api_key,
        app_secret=cfg.app_secret,
        callback_url=cfg.callback_url,
        token_path=str(cfg.token_path),
        asyncio=False,
        token_write_func=make_token_write_func(cfg.token_path),
        enforce_enums=False,
    )
    print(f"OK — token persisted at {cfg.token_path}", file=sys.stderr)
    return 0


def cli_main(argv: list[str] | None = None) -> int:
    bootstrap_dotenv()
    parser = argparse.ArgumentParser(
        prog="schwab_positions_mcp.auth",
        description="OAuth credential capture for schwab-positions-mcp.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, runner in (("login_flow", _run_login_flow), ("manual_flow", _run_manual_flow)):
        sp = sub.add_parser(name, help=f"Run the {name} variant.")
        sp.add_argument(
            "--config-dir",
            type=str,
            default=None,
            help="Override the token directory.",
        )
        sp.add_argument(
            "--dry-run",
            dest="dry_run",
            action="store_true",
            help="Run pre-flight checks and exit without contacting Schwab.",
        )
        sp.set_defaults(func=runner)

    args = parser.parse_args(argv)
    fn: Any = args.func
    try:
        return int(fn(args))
    except SchwabAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_main())
