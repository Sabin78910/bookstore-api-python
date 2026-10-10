# Bookstore API

Bookstore REST API built with FastAPI and Pydantic v2. Interactive docs are served at `/docs`.

![CI](https://github.com/Sabin78910/bookstore-api-python/actions/workflows/ci.yml/badge.svg)

**Live:** https://bookstore-api-lhpl.onrender.com · interactive docs: [`/docs`](https://bookstore-api-lhpl.onrender.com/docs). Hosted on Render's free plan: it sleeps when idle, so the first request can take about 30–60 s. Data is in memory and resets on restart.

## Run (Mac Terminal / VS Code)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000/docs
.venv/bin/pytest
```

## API / Output
GET /health · GET /books?author=&sort=-price (sort: title, author, price; responses carry `X-Total-Count` and RFC 8288 `Link` rel=next/prev headers) · POST /books · GET /books/{id} · POST /books/{id}/sell?qty= · DELETE /books/{id}

Idempotency: `POST /books` and `POST /books/{id}/sell` accept an optional `Idempotency-Key` header (1-255 printable ASCII chars, else 422). A repeat with the same key, path and body returns the first response with `Idempotent-Replayed: true` and does not repeat the side effect; reusing a key for a different path/body is 422. In-memory store, capped at 1000 keys (oldest evicted), lost on restart.

Write protection: when the `API_KEY` env var is set, POST/PUT/DELETE require an `X-API-Key` header with that value (else 401); reads stay public. Render generates `API_KEY` via `render.yaml` (see the service's Environment tab). Unset locally = writes open.

Rate limiting: responses that pass the limiter carry `RateLimit-Limit`, `RateLimit-Remaining` (never below 0) and `RateLimit-Reset` (integer seconds until the window frees up); 429s also send `Retry-After`.

Security headers: every response (including errors, 304s and 429s) carries `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`; POST/PUT/DELETE responses add `Cache-Control: no-store`; all but `/docs` and `/redoc` add `Content-Security-Policy: frame-ancestors 'none'`.

## Automation (runs on GitHub, no laptop needed)
| Workflow | Trigger | What it does |
|---|---|---|
| CI | push / PR | ruff lint and format, pytest, pip-audit |
| Docker image | push to main / tag | publishes `ghcr.io/sabin78910/bookstore-api-python` |
| CodeQL | push / PR / weekly | security analysis |
| Dependabot | weekly | dependency update PRs |
