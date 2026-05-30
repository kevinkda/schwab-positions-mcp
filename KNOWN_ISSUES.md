# Known Issues

Tracked known issues and limitations for `schwab-positions-mcp`. For
resolved issues see [CHANGELOG.md](./CHANGELOG.md).

## Open

### OAuth `scope=trade` is broader than this MCP's read-only intent

Schwab's OAuth server has **no narrower `read` scope** — `trade` scope is
required to access any account data, including the read-only positions /
orders / transactions endpoints this server uses. The token therefore has
the *capability* to place trades even though this MCP exposes no trade
path. This is exactly why the 5-layer read-only boundary (white-list /
startup warning / read-only tool surface / CI grep gate / mutation-reject
test) exists. See `docs/SECURITY.md`. **Accepted by design**, not a bug.

### 60-day history lookback window (Schwab Trader API limit)

`get_orders_history` / `get_transactions` reject `from_entered_time` /
`start_date` older than 60 days at the Pydantic layer (v0.1.2), matching
Schwab's Trader API lookback window. Requests for older history fail fast
client-side with a self-describing `ValueError` rather than an opaque
server 400. This is an upstream API constraint, not a defect.

## Upstream / Deferred

- **`schwab-py` is bus-factor-1** — tracked in `docs/THREAT_MODEL.md`;
  dependabot ignores `schwab-py` bumps (manual upgrade checklist required).
- **`mcp` 1.x → 2.x major bump deferred** — requires the §6.5
  compatibility checklist; dependabot ignores the major bump.
- **Host compromise is out of scope** — an attacker with shell access can
  replay `~/.config/schwab-positions-mcp/token.json` directly against
  `api.schwabapi.com`; the read-only MCP buys nothing in that case. Use
  FDE + a dedicated user account; never commit `.env` / `token.json`.

## Resolved

All known bugs surfaced during real-world sync testing were fixed in the
v0.1.1 / v0.1.2 releases:

- **B1** — `enforce_enums=True` made every filtered tool 100 % unusable →
  fixed (`enforce_enums=False`) in v0.1.1.
- **B2 / B4** — missing `get_account_numbers` tool (account-hash mapping)
  → exposed as the 8th tool in v0.1.1.
- **B3** — mutation literal keywords in docstrings tripped the Layer 4
  grep gate → rephrased in v0.1.1.
- CI lint/mypy regression after v0.1.2 → fixed in `da433de` (2026-05-30).

See [CHANGELOG.md](./CHANGELOG.md) for the full history.
