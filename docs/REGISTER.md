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
- **Refresh token** expires every **7 days** — a hard, non-extendable limit
  enforced by Schwab's servers. Using the refresh token to mint an access
  token does **not** reset the 7-day clock (the window runs from the token's
  original creation, not its last use), so there is **no fully-automatic
  keep-alive**. When it expires, every API call returns 401 with
  `refresh_token_expired`; re-run `login_flow` (or `manual_flow`) to mint a
  fresh refresh token.

> **Why no auto-renew?** Confirmed against the
> [schwab-py docs](https://schwab-py.readthedocs.io/en/latest/auth.html)
> ("There is currently no way to make a refresh token last longer than seven
> days") and the Schwab Developer Portal. This is a deliberate Schwab security
> measure, identical across every Schwab API client library.

### 4.1 Make the 7-day expiry predictable (recommended)

Because expiry can't be avoided, schedule the bundled health probe so you get
warned *before* the token dies instead of discovering it mid-session:

```bash
# Run it now to see the current state (exit code 0 healthy … 5 insecure perms).
uv run python -m schwab_positions_mcp.health
echo "exit: $?"
```

The probe is **offline-safe** (no browser, no Schwab call): it reads the local
token file, computes days-to-expiry (via schwab-py's `token_age()` when creds
are present, else the file mtime), and on any non-zero exit it:

- fires a best-effort desktop notification (macOS `osascript` / Linux
  `notify-send` / Windows toast), and
- writes `~/Desktop/SCHWAB_POSITIONS_REAUTH_NEEDED.md` with the exact one-line
  re-auth command.

Schedule it with cron / launchd / Task Scheduler — see
[`docs/cron.example`](cron.example) for ready-to-paste snippets (Sunday 20:00
+ Wednesday 21:00 + a 4-hour fallback).

The MCP `health_check` tool also now reports `token_age_days` and
`token_expires_in_days` under `checks`, so an agent can surface the countdown
in-protocol.

### 4.2 Re-authorizing when it does expire

One command — your App Key / Secret are already saved in `.env`, so you only
redo the browser login, not the registration:

```bash
uv run python -m schwab_positions_mcp.auth login_flow
# or, if the browser flow is flaky:
uv run python -m schwab_positions_mcp.auth manual_flow
```


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
