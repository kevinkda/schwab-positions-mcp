# Security model — schwab-positions-mcp

`schwab-positions-mcp` is a **read-only** MCP server: it surfaces Schwab
account state (positions, orders, transactions, balances) to LLM agents but
intentionally does **not** expose any mutation endpoint. There is no
`place_order`, no `cancel_order`, no `replace_order`, no fund-transfer tool,
no anything that can move money or open / close positions.

This document spells out the 5-layer boundary that enforces that contract
and the threat model behind the design.

## TL;DR

- A compromised LLM (prompt injection, jailbreak, supply-chain compromise)
  cannot place trades through this MCP server. The mutation surface does
  not exist in the codebase, the white-list refuses unknown method calls
  at runtime, the CI grep gate refuses any commit that adds mutation
  keywords, and the OAuth token still grants Schwab `trade` scope only
  because the upstream API requires it.
- Compromise of the host machine — token exfiltration, ptrace, swap
  inspection — is **out of scope**. If the attacker has shell on your box,
  they can replay your token directly against `api.schwabapi.com` and the
  read-only nature of this MCP buys you nothing. Use FDE, run on a
  dedicated user account, do not commit `.env` / `token.json`.

## 5-layer read-only boundary

### Layer 1 — `client.py` white-list (runtime)

`src/schwab_positions_mcp/client.py` defines `ReadOnlySchwabClient`, a thin
wrapper around `schwab.client.Client`. Every attribute access goes through
`__getattr__`, which:

- forwards calls in `_READ_ONLY_METHODS` (a frozen set: `get_accounts`,
  `get_account`, `get_account_orders`, `get_orders_for_account`,
  `get_transactions`, `get_user_preferences`, `get_account_numbers`)
- raises `NotImplementedError` for **everything else** with a message
  pointing back at this document.

`__setattr__` is also overridden so monkey-patching the wrapper itself is
rejected. Tool modules in `src/schwab_positions_mcp/tools/` MUST go through
`tools._common.get_client()`, which only ever returns a `ReadOnlySchwabClient`.

### Layer 2 — startup warning

`server.py` emits `WARNING: schwab-positions-mcp starting in READ-ONLY MODE.
No trade endpoints exposed.` on every boot. Operators who accidentally
configure this MCP where they meant to use a trading MCP see the warning
in their MCP host's logs before the first tool call.

### Layer 3 — tool layer

Only 7 tools are registered in `server.py`: 5 read-only business tools
(`get_accounts`, `get_account_positions`, `get_orders_history`,
`get_transactions`, `get_account_summary`) plus 2 meta tools
(`health_check`, `get_server_info`). Both meta tools include
`is_read_only: true` in their response, so a calling agent (or a paranoid
human reviewer) can sanity-check the contract over JSON-RPC.

### Layer 4 — CI grep gate

`.github/workflows/security-grep.yml` runs on every push and pull request.
It greps `src/` for the literal tokens `place_order`, `cancel_order`,
`replace_order` and fails the build if any are found. It also asserts that
`client.py` still declares `_READ_ONLY_METHODS` and still raises
`NotImplementedError`, so the Layer 1 white-list cannot be silently
disabled by a maintainer (or an automated PR) without tripping the gate.

### Layer 5 — mutation reject test (Phase 3)

A test (`tests/test_client_readonly.py`, scheduled for the Phase 3 sibling)
attempts to call `client.place_order(...)` on a `ReadOnlySchwabClient` and
asserts that it raises `NotImplementedError`. This catches the case where
a maintainer adds `place_order` to `_READ_ONLY_METHODS` by mistake — Layer
4 catches the keyword in the source, but Layer 5 catches the runtime
behaviour change.

## Threat model

### In scope

| Threat                                              | Mitigation                                            |
| --------------------------------------------------- | ----------------------------------------------------- |
| LLM is jailbroken / prompt-injected                 | Layers 1, 3, 4 — no mutation tool exists to call      |
| Malicious dependency added via PR                   | Layer 4 grep gate; CodeQL workflow; dependabot        |
| OAuth token leaks at rest                           | Token file is `0o600`; co-located with cache `0o600`  |
| Maintainer accidentally exposes `place_order`       | Layers 4 + 5 fail CI / tests                          |
| Refresh-token theft (replay)                        | Detect via `Schwab-Client-CorrelId` in audit logs     |

### Out of scope

| Threat                                              | Why not                                                                                  |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Host compromise (RCE, kernel exploit, malware)      | Attacker has direct access to `token.json`; can trade against Schwab API directly        |
| Schwab-side compromise / Schwab insider             | Out of our control; mitigated by Schwab's own controls                                   |
| User explicitly forks the repo and deletes Layer 1  | Documented contract — the MIT licence ends here, fork at your own risk                   |
| Side-channel timing attacks on the token file       | Token is at-rest only; refresh path uses TLS                                             |

### OAuth scope risk

Schwab's OAuth endpoint requires the `trade` scope to access **any** account
data, including the read-only positions / orders / transactions endpoints
this MCP server uses. We cannot opt down to a `read` scope; that scope does
not exist on Schwab's authorization server today. The token therefore has
the *capability* to place trades — Schwab simply has no narrower scope to
issue.

This is exactly why Layers 1–5 exist: the OAuth scope is the most permissive
unit Schwab exposes, and we do not want our codebase to be the place where
the read-only intent breaks down.

### Token leakage response

If you suspect your `~/.config/schwab-positions-mcp/token.json` has leaked:

1. Revoke the OAuth grant in your Schwab developer portal immediately.
2. Delete the local token file: `rm -f ~/.config/schwab-positions-mcp/token.json`.
3. Re-run the OAuth flow:
   `uv run python -m schwab_positions_mcp.auth login_flow`.
4. Audit `cache.duckdb` for unexpected snapshots — any positions / orders
   / transactions row whose `observed_at` falls outside your normal
   activity window may indicate a third party replayed the token.

## Reporting security issues

Open a private security advisory on GitHub:
<https://github.com/kevinkda/schwab-positions-mcp/security/advisories>.
Do **not** open a public issue with the details.
