"""Layer 5 — runtime mutation-reject test for :class:`ReadOnlySchwabClient`.

This complements:

* Layer 1 — :data:`schwab_positions_mcp.client._READ_ONLY_METHODS` whitelist
* Layer 4 — CI grep gate that refuses mutation keywords in ``src/``

Layer 5 is the *runtime* assertion: even if a future maintainer accidentally
added a mutation method name to the whitelist (Layer 1 regression) or routed
a tool through a raw ``schwab.client.Client`` (Layer 3 regression), this test
would catch the behavioural change.

The test deliberately *constructs* the forbidden method names from string
fragments. The repo's grep gate only scans ``src/`` (see
``.github/workflows/security-grep.yml``), so literal keywords would still be
allowed here, but the indirection makes the intent obvious to a human reader.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schwab_positions_mcp.client import _READ_ONLY_METHODS, ReadOnlySchwabClient

if TYPE_CHECKING:
    from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# 5 read-only methods proxy through correctly
# ---------------------------------------------------------------------------


def test_white_list_methods_proxy_to_underlying(
    readonly_client: ReadOnlySchwabClient,
    mock_schwab_client: "MagicMock",
) -> None:
    """Every method in the white-list must forward verbatim to the wrapped client."""
    for method_name in _READ_ONLY_METHODS:
        bound = getattr(readonly_client, method_name)
        bound("any-arg", kw="any-kwarg")
        getattr(mock_schwab_client, method_name).assert_called_with("any-arg", kw="any-kwarg")


def test_white_list_contains_expected_read_methods() -> None:
    """Sanity: the whitelist matches what docs/SECURITY.md advertises."""
    assert "get_account" in _READ_ONLY_METHODS
    assert "get_accounts" in _READ_ONLY_METHODS
    assert "get_account_numbers" in _READ_ONLY_METHODS
    assert "get_orders_for_account" in _READ_ONLY_METHODS
    assert "get_account_orders" in _READ_ONLY_METHODS
    assert "get_transactions" in _READ_ONLY_METHODS
    assert "get_user_preferences" in _READ_ONLY_METHODS


# ---------------------------------------------------------------------------
# Mutation rejection — the core Layer 5 contract
# ---------------------------------------------------------------------------

# We assemble the forbidden method names from fragments so a casual ``rg``
# does not flag this test file as a "mutation API reference"; the Layer 4
# grep gate only scans ``src/`` but readability still matters.
_PLACE = "place_" + "order"
_CANCEL = "cancel_" + "order"
_REPLACE = "replace_" + "order"


def test_place_method_raises_not_implemented(readonly_client: ReadOnlySchwabClient) -> None:
    """The 'submit a new trade' method must be rejected at runtime."""
    with pytest.raises(NotImplementedError):
        getattr(readonly_client, _PLACE)


def test_cancel_method_raises_not_implemented(readonly_client: ReadOnlySchwabClient) -> None:
    """The 'cancel a working trade' method must be rejected at runtime."""
    with pytest.raises(NotImplementedError):
        getattr(readonly_client, _CANCEL)


def test_replace_method_raises_not_implemented(readonly_client: ReadOnlySchwabClient) -> None:
    """The 'replace a working trade' method must be rejected at runtime."""
    with pytest.raises(NotImplementedError):
        getattr(readonly_client, _REPLACE)


@pytest.mark.parametrize(
    "method_name",
    [
        "wire_funds",
        "transfer_assets",
        "delete_account",
        "update_preferences",
        "totally_made_up_method",
        "_internal_hack",
        "submit_trade_request",
    ],
)
def test_arbitrary_mutation_method_raises_not_implemented(
    readonly_client: ReadOnlySchwabClient,
    method_name: str,
) -> None:
    """Any non-white-listed attribute access MUST raise ``NotImplementedError``."""
    with pytest.raises(NotImplementedError):
        getattr(readonly_client, method_name)


def test_error_message_mentions_security_md(readonly_client: ReadOnlySchwabClient) -> None:
    """The error message must point users at the canonical security doc."""
    with pytest.raises(NotImplementedError) as excinfo:
        getattr(readonly_client, _PLACE)
    assert "SECURITY.md" in str(excinfo.value)


def test_error_message_mentions_read_only_design(readonly_client: ReadOnlySchwabClient) -> None:
    """The error message must announce the read-only contract clearly."""
    with pytest.raises(NotImplementedError) as excinfo:
        getattr(readonly_client, _CANCEL)
    assert "read-only by design" in str(excinfo.value)


def test_error_message_includes_method_name(readonly_client: ReadOnlySchwabClient) -> None:
    """Operators need to know which call was rejected."""
    with pytest.raises(NotImplementedError) as excinfo:
        getattr(readonly_client, _REPLACE)
    assert _REPLACE in str(excinfo.value)


# ---------------------------------------------------------------------------
# Setattr is also blocked — protects against monkeypatching the wrapper
# ---------------------------------------------------------------------------


def test_setattr_is_rejected(readonly_client: ReadOnlySchwabClient) -> None:
    """Monkeypatching the wrapper itself must be impossible."""
    with pytest.raises(AttributeError):
        readonly_client.something = "x"  # type: ignore[misc]


def test_repr_does_not_leak_underlying(readonly_client: ReadOnlySchwabClient) -> None:
    """``repr`` should be informative but reveal no token / secret state."""
    r = repr(readonly_client)
    assert "ReadOnlySchwabClient" in r
    # The underlying MagicMock's repr leaks `id(...)`; we wrap in a stable label.
    assert "schwab.client.Client" in r
