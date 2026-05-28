# Contributing to schwab-positions-mcp

Thanks for considering a contribution! This is a **read-only** MCP server
for Charles Schwab account state. The read-only contract is **non-negotiable**
— see [`docs/SECURITY.md`](docs/SECURITY.md) for the 5-layer boundary.

## Before you start

1. Read [`docs/REGISTER.md`](docs/REGISTER.md) for the OAuth + Schwab
   Developer Portal setup.
2. Read [`docs/SECURITY.md`](docs/SECURITY.md) for the read-only contract.
3. Read [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) for the threat surface.
4. Check open issues / discussions to avoid duplicate work.

## Development setup

```bash
git clone https://github.com/kevinkda/schwab-positions-mcp
cd schwab-positions-mcp
uv sync --extra dev
uv run pre-commit install
```

## Quality gates (must pass before PR)

- `uv run pytest --cov` — all tests pass.
- `uv run ruff check src tests` — 0 warnings.
- `uv run ruff format --check src tests` — must be formatted.
- `uv run mypy --strict src` — 0 errors.
- `uv run bandit -r src -lll` — 0 high.
- `uv run pip-audit` — 0 known vulnerabilities.
- `pre-commit run --all-files` — all hooks pass.

## What contributions are welcome

- Bug fixes, documentation improvements, additional tests, OWASP coverage
  expansion.
- Cross-platform support improvements (Windows Tier B, etc.).
- New **read-only** Schwab Trader API endpoints (e.g. `get_user_preferences`).
- Mutation / trading endpoints (`place_order`, `cancel_order`,
  `replace_order`, fund transfers) — **explicitly out of scope** per
  [`docs/SECURITY.md`](docs/SECURITY.md). Such PRs will be rejected at the
  CI grep gate (`security-grep.yml`) before review.

## Commit message style

Follow [Conventional Commits](https://www.conventionalcommits.org/). Examples:

- `feat(client): expand white-list to include get_user_preferences`
- `fix(cache): handle DuckDB lock contention on parallel snapshots`
- `docs(security): clarify OAuth scope rationale`

Subject ≤ 72 chars. Use English. Body explains *why*, not *what*.

## Branching

- `main` is the integration branch. PRs target `main`.
- **Never force-push `main`.**

## Inclusive language

This project follows
[Amazon's inclusive language guidelines](https://aws.amazon.com/blogs/aws/blogpost-inclusive-language/).
Replace `master` / `blacklist` / `whitelist` with `main` / `deny list` /
`allow list`. Self-audit before submitting.

## Security disclosures

Do **not** open a public issue for vulnerabilities. Use the GitHub
private security advisory flow:
<https://github.com/kevinkda/schwab-positions-mcp/security/advisories>.

## License

By submitting a PR, you agree your contribution will be licensed under
MIT (see [LICENSE](LICENSE)).
