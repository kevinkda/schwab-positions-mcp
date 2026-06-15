"""Shared helpers for ``schwab_positions_mcp.tools`` modules.

Every tool entrypoint follows the same shape:
  1. Validate input with a Pydantic schema (caller does this).
  2. Acquire a :class:`ReadOnlySchwabClient` (Layer 1 allow list).
  3. Call the wrapped schwab-py method.
  4. Normalise errors from the HTTP response (401 / 429 / 5xx).
  5. Return a dict with a ``_cache_status`` field describing cache outcome.

This module deliberately stays tiny so the security boundary is easy to audit.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from .. import __version__
from ..client import ReadOnlySchwabClient

log = logging.getLogger(__name__)

_CLIENT_LOCK = threading.Lock()
_CLIENT_SINGLETON: ReadOnlySchwabClient | None = None


def _redact(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return value[:2] + "…" + value[-2:]


def _token_path() -> Path:
    override = os.environ.get("SCHWAB_POSITIONS_TOKEN_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".config" / "schwab-positions-mcp" / "token.json"


def _build_client() -> ReadOnlySchwabClient:
    """Construct a ReadOnlySchwabClient from environment + token on disk.

    Raises :class:`SchwabClientUnavailable` if credentials or token are missing.
    """
    api_key = os.environ.get("SCHWAB_API_KEY", "").strip()
    app_secret = os.environ.get("SCHWAB_APP_SECRET", "").strip()
    token_path = _token_path()
    if not api_key or not app_secret:
        raise SchwabClientUnavailable("Missing SCHWAB_API_KEY / SCHWAB_APP_SECRET; populate .env.")
    if not token_path.exists():
        raise SchwabClientUnavailable(
            f"OAuth token not found at {token_path}. Run `uv run python -m schwab_positions_mcp.auth login_flow` first."
        )

    # Local import — schwab-py is heavy and has its own logging side-effects.
    from schwab.auth import client_from_token_file

    # We disable schwab-py's runtime enum enforcement on purpose:
    #   1. Pydantic ``Literal[...]`` constraints in ``models.py`` already
    #      restrict every enum-valued input (``fields`` / ``status`` /
    #      transaction ``types``) to a known Schwab vocabulary, so the
    #      schwab-py layer would only re-do that check.
    #   2. MCP tool inputs arrive as JSON strings. Wrapping them in
    #      ``schwab.client.base.*`` enums at this layer would couple us to
    #      schwab-py private paths (the public ``schwab.client`` re-export
    #      does not expose ``Account.Fields`` etc. for direct lookup).
    #   3. With ``enforce_enums=True`` the same string the user already
    #      validated through Pydantic was rejected at the schwab-py boundary
    #      with an opaque ``expected type "Fields", got type "str"`` error
    #      — see B1 in CHANGELOG v0.1.1.
    raw_client = client_from_token_file(
        api_key=api_key,
        app_secret=app_secret,
        token_path=str(token_path),
        asyncio=False,
        enforce_enums=False,
    )
    return ReadOnlySchwabClient(raw_client)


def get_client() -> ReadOnlySchwabClient:
    """Return the process-wide :class:`ReadOnlySchwabClient` (lazy)."""
    global _CLIENT_SINGLETON
    with _CLIENT_LOCK:
        if _CLIENT_SINGLETON is None:
            _CLIENT_SINGLETON = _build_client()
        return _CLIENT_SINGLETON


def reset_client_singleton() -> None:
    """Test hook: drop cached client (does not invalidate token on disk)."""
    global _CLIENT_SINGLETON
    with _CLIENT_LOCK:
        _CLIENT_SINGLETON = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SchwabClientUnavailable(RuntimeError):
    """No usable Schwab client (missing creds / token)."""


class SchwabApiError(RuntimeError):
    """Normalised Schwab HTTP error.

    Attributes:
      status_code: HTTP status (401, 403, 429, 5xx, …).
      reason: short, human-readable reason that callers can surface.
      request_id: Schwab ``Schwab-Client-CorrelId`` header if present.
    """

    def __init__(self, status_code: int, reason: str, request_id: str | None = None) -> None:
        super().__init__(f"[{status_code}] {reason}")
        self.status_code = status_code
        self.reason = reason
        self.request_id = request_id


def normalise_response(response: Any) -> Any:
    """Convert an httpx ``Response`` into either parsed JSON or raise ``SchwabApiError``.

    schwab-py returns ``httpx.Response`` from its sync client. We treat 2xx as
    success, 401 as ``refresh_token_expired`` (caller should surface "re-run
    login_flow"), 429 as ``rate_limited``, 5xx as ``upstream_error``.
    Other non-2xx codes are surfaced verbatim.
    """
    status = getattr(response, "status_code", None)
    if status is None:
        return response  # already-parsed payload

    request_id = None
    if hasattr(response, "headers"):
        request_id = response.headers.get("Schwab-Client-CorrelId")

    if 200 <= status < 300:
        try:
            return response.json()
        except (ValueError, AttributeError):
            return getattr(response, "text", "")

    if status == 401:
        log.warning("Schwab 401 (req_id=%s) — refresh token likely expired", _redact(request_id or ""))
        raise SchwabApiError(401, "refresh_token_expired", request_id)
    if status == 403:
        raise SchwabApiError(403, "forbidden", request_id)
    if status == 429:
        raise SchwabApiError(429, "rate_limited", request_id)
    if 500 <= status < 600:
        raise SchwabApiError(status, "upstream_error", request_id)
    raise SchwabApiError(status, f"unexpected_{status}", request_id)


def server_version() -> str:
    return __version__
