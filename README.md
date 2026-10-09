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
GET /health · GET /books?author= · POST /books · GET /books/{id} · POST /books/{id}/sell?qty= · DELETE /books/{id}

## Automation (runs on GitHub, no laptop needed)
| Workflow | Trigger | What it does |
|---|---|---|
| CI | push / PR | ruff lint and format, pytest, pip-audit |
| Docker image | push to main / tag | publishes `ghcr.io/sabin78910/bookstore-api-python` |
| CodeQL | push / PR / weekly | security analysis |
| Dependabot | weekly | dependency update PRs |
