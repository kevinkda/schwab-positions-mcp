"""Schwab API client wrapper with read-only white-list enforcement.

This is **Layer 1** of the 5-layer read-only security boundary documented in
``docs/SECURITY.md``. Every call into the underlying ``schwab.client.Client``
must go through :class:`ReadOnlySchwabClient`, which exposes ONLY the methods
listed in :data:`_READ_ONLY_METHODS`. Any other attribute access — including
all of schwab-py's mutation methods for placing, cancelling, or replacing
orders — raises :class:`NotImplementedError` at runtime.

This is enforced via ``__getattr__``, so even reflection / monkeypatching
attempts that bypass the import-time interface still fail.

The complementary CI grep gate (``.github/workflows/security-grep.yml``,
Layer 4) blocks any source file from referring to those mutation method names
at all, so this white-list is defence-in-depth, not the only barrier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import schwab


# ---------------------------------------------------------------------------
# Read-only method white-list — keep alphabetised; every entry must be a
# read-only schwab-py Client method that this MCP server intends to expose.
# ---------------------------------------------------------------------------
_READ_ONLY_METHODS: frozenset[str] = frozenset(
    {
        "get_account",
        "get_account_numbers",
        "get_accounts",
        "get_account_orders",
        "get_orders_for_account",
        "get_transactions",
        "get_user_preferences",
    }
)


class ReadOnlySchwabClient:
    """Thin wrapper enforcing a read-only method white-list.

    Calls to any non-white-listed attribute raise :class:`NotImplementedError`
    with a message pointing at ``docs/SECURITY.md``.
    """

    __slots__ = ("_client",)

    def __init__(self, schwab_client: schwab.client.Client) -> None:
        object.__setattr__(self, "_client", schwab_client)

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails; that is fine
        # here because nothing else lives on the instance.
        if name in _READ_ONLY_METHODS:
            return getattr(self._client, name)
        raise NotImplementedError(
            f"Method {name!r} is not available in schwab-positions-mcp. "
            "This MCP server is read-only by design and does NOT expose "
            "trade / order-mutation endpoints. See docs/SECURITY.md."
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"ReadOnlySchwabClient is immutable; cannot set attribute {name!r}. See docs/SECURITY.md.")

    def __repr__(self) -> str:
        return "ReadOnlySchwabClient(<schwab.client.Client>)"
