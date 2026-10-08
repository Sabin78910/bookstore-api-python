# Bookstore API
Purpose: Bookstore REST API (Python, FastAPI, Pydantic v2).

## Commands
- Test: `.venv/bin/pytest -q`
- Lint: `.venv/bin/ruff check . && .venv/bin/ruff format --check .`
- Build: `docker build .`

## Architecture
app/main.py — create_app() factory; tests in tests/ use TestClient

## Rules
- Read only the files you need; do not scan the whole repo.
- Every behavior change needs a test. Run tests and lint before finishing.
- No new dependencies, permissions, or signing/secrets changes without asking.
- Never commit secrets, keystores, .env files.
- Keep PRs under ~300 changed lines; one issue per PR.
- Be concise: diffs plus a 3-line summary.
- If tests still fail after 3 attempts, stop and report the blocker.

## Definition of done
Lint clean, tests pass, CI green, short summary, PR opened as draft.
