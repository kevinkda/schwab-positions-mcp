# Schwab Developer Portal — registering the Trader API client app

`schwab-positions-mcp` consumes Schwab's **Trader API** (the same OAuth
authorization server as `schwab-marketdata-mcp`, but the resource
endpoints we hit are the read-only account ones — `/accounts`,
`/accounts/{accountHash}/orders`, `/accounts/{accountHash}/transactions`).

You need a registered client app on
<https://developer.schwab.com/dashboard/apps> before the OAuth flow will
work.

## 1. Create the app

1. Sign in to the Schwab Developer Portal with your existing Schwab
   brokerage credentials.
2. Click **Add a new app**.
3. **App Type:** *Individual Developer Application*.
4. **API Product:** select **Accounts and Trading Production**.
5. **Order Limit:** 120 / minute is fine for read-only usage.
6. **Callback URL:** `https://127.0.0.1:8182` (must match
   `SCHWAB_CALLBACK_URL` byte-for-byte; trailing slash matters).
7. Submit and wait for Schwab to approve (typically 1–3 business days).

## 2. Capture the credentials

Once Schwab approves, the app dashboard exposes:

- **App Key** → set as `SCHWAB_API_KEY` in `.env`
- **Secret** → set as `SCHWAB_APP_SECRET` in `.env`
- **Callback URL** → confirm matches `SCHWAB_CALLBACK_URL`

```bash
cp .env.example .env
$EDITOR .env
```

> Never commit `.env`. The `.gitignore` blocks it; pre-commit
> (gitleaks + detect-secrets) will block any leak that slips through.

## 3. OAuth bootstrap

```bash
uv run python -m schwab_positions_mcp.auth login_flow --dry-run
# verify pre-flight: api_key=…, callback_url=…, token_path=…

uv run python -m schwab_positions_mcp.auth login_flow
# opens a browser to https://api.schwabapi.com/v1/oauth/authorize
# log in, click Allow; the local callback server captures the code and
# exchanges it for an access + refresh token.
```

If the browser flow fails (stale tab, HSTS rewrite, browser extension
interfering), use the manual flow:

```bash
uv run python -m schwab_positions_mcp.auth manual_flow
# 1. CLI prints an authorize URL.
# 2. Paste it into your browser, log in, click Allow.
# 3. Browser lands on https://127.0.0.1:8182/?code=...&state=... (page
#    will fail to load — that's expected).
# 4. Copy the FULL URL from the address bar.
# 5. Paste back into the prompt and press Enter.
```

The token persists at `~/.config/schwab-positions-mcp/token.json`
(`0o600`).

## 4. Token lifetime

- **Access token** expires every ~30 minutes; schwab-py auto-refreshes.
- **Refresh token** expires every **7 days**. When it does, every API
  call returns 401 with `refresh_token_expired`. Re-run `login_flow`
  (or `manual_flow`) to mint a fresh refresh token.

## 5. Troubleshooting

### `MismatchingStateException` (CSRF Warning)

Almost always one of:

- Stale Schwab tab in the browser from a previous run → quit the browser
  entirely, retry.
- `SCHWAB_CALLBACK_URL` does not byte-for-byte match the registered
  Callback URL → fix `.env`.
- Browser extension / corporate HSTS stripping the redirect → switch to
  `manual_flow`.

### `403 Forbidden` on `/accounts`

The app exists but isn't approved for production yet. Wait for Schwab.

### `401` after a successful `login_flow`

Usually the refresh token expired (7-day rolling window) — re-run
`login_flow`.

## 6. Why two repos?

`schwab-marketdata-mcp` and `schwab-positions-mcp` are split intentionally:

- Different MCP processes → different OS user-process trust boundaries.
- Different config dirs → token leak in one does not implicate the other.
- Different read / write contracts: `marketdata` is reference-data
  read-only; `positions` is account read-only with a write-capable token.
- Different CI gates: `security-grep.yml` only exists on the positions
  repo because that's where the trading-scope token lives.

You can run a single Schwab Developer Portal app for both repos, OR
register two apps (one per repo) — the latter is recommended if you
want the option to revoke one without touching the other.
