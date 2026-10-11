# Bookstore API
Books, reviews, subjects and similar titles. FastAPI reference service with a live explorer page.

## Stack
Python 3.12 · FastAPI · Pydantic v2 · pytest · ruff. Interactive docs at `/docs`. Hosted on Render.

## Commands
- Dev: `uvicorn app.main:app --reload`
- Test: `pytest` · Lint: `ruff check .`

## Architecture
- `app/main.py` wires routers; `app/models.py` Pydantic schemas; storage sits behind a repository function
  so it can be replaced without touching routes.

## API rules
- Pydantic v2 models validate every request and response. Never return a raw dict.
- One error shape everywhere, including `X-Request-ID` on errors.
- Reject oversized bodies with 413. Writes require the API key; reads stay public.

## Testing rules
- Every endpoint needs tests for the happy path, a validation failure and an auth failure.
- Run `pytest` and `ruff check .` before finishing.

## Security and git
- Never commit secrets, keystores, .env files or email addresses.
- Keep the Content-Security-Policy strict; add a host to connect-src only when a feature needs it.
- `main` is protected: branch, open a PR, merge when CI is green. One issue per PR, under ~300 lines.

## Done when
Lint clean, tests pass, CI green, PR opened with a three line summary.
