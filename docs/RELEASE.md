# Release process — schwab-positions-mcp

This is a personal-scale, MIT-licensed MCP. Releases are cut from `main`
after the test suite is green.

## Versioning

[SemVer](https://semver.org/). The token stored on disk
(`~/.config/schwab-positions-mcp/token.json`) is **not** considered
public API; format changes there do not bump major.

- `0.x` — alpha; breaking tool-shape changes allowed in minor bumps.
- `1.0` — first stable release after Phase 4 + a clean external review.

## Release checklist

1. All quality gates green:

   ```bash
   uv run pytest --cov          # ≥ 85% coverage
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy --strict src
   uv run bandit -r src -lll
   uv run pip-audit
   ```

2. Security boundary still in place:

   ```bash
   grep -rE 'place_order|cancel_order|replace_order' src/   # must be 0 hits
   ```

3. `CHANGELOG.md`:
   - Move `[Unreleased]` content under a new
     `## [X.Y.Z] - YYYY-MM-DD` heading.
   - Add a fresh empty `[Unreleased]` skeleton.
4. `pyproject.toml` `version` bump to `X.Y.Z`.
5. `__init__.py` `__version__` bump to `X.Y.Z`.
6. Commit: `chore(release): vX.Y.Z`.
7. Tag + GitHub release:

   ```bash
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin main vX.Y.Z
   gh release create vX.Y.Z --notes-from-tag
   ```

8. Bump `version` in `pyproject.toml` back to a `+dev` suffix on `main`
   for the next iteration.

## Hotfix process

1. Branch off the release tag: `git checkout -b hotfix/X.Y.Z+1 vX.Y.Z`.
2. Fix + tests + CHANGELOG entry.
3. PR into `main`, then cherry-pick onto the release branch and tag.

## Out of scope (deliberate)

- PyPI publishing — this MCP is consumed by `git clone`, not `pip install`.
- Containerised release — token storage relies on POSIX file modes that
  are awkward to thread through container layers.
