import hashlib
import html
import json
import os
import secrets
import time
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


def normalize_isbn(value: str) -> str:
    isbn = value.replace("-", "").replace(" ", "").upper()
    if len(isbn) == 10 and isbn[:9].isdigit() and (isbn[9].isdigit() or isbn[9] == "X"):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn))
        ok = total % 11 == 0
    elif len(isbn) == 13 and isbn.isdigit():
        total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(isbn))
        ok = total % 10 == 0
    else:
        ok = False
    if not ok:
        raise ValueError("Invalid ISBN: must be a valid ISBN-10 or ISBN-13")
    return isbn


class BookIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0)
    stock: int = Field(ge=0, default=0)
    genre: str | None = Field(default=None, min_length=1, max_length=50)
    isbn: str | None = None

    @field_validator("isbn")
    @classmethod
    def check_isbn(cls, v: str | None) -> str | None:
        return None if v is None else normalize_isbn(v)


class Book(BookIn):
    id: int
    average_rating: float | None = None
    review_count: int = 0


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    text: str = Field(default="", max_length=1000)


class Review(ReviewIn):
    id: int
    book_id: int


def problem(
    status: int, detail: str, headers: dict[str, str] | None = None, **extra: object
) -> JSONResponse:
    """RFC 9457 problem+json response."""
    body = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase if status != 422 else "Unprocessable Content",
        "status": status,
        "detail": detail,
        **extra,
    }
    return JSONResponse(
        jsonable_encoder(body),
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


SORT_FIELDS = ("title", "author", "price")


def create_app(rate_limit: int = 100, rate_window: float = 60.0) -> FastAPI:
    app = FastAPI(title="Bookstore API", version="1.0.0")
    started = time.monotonic()
    hits: dict[str, list[float]] = {}

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        recent = [t for t in hits.get(ip, []) if now - t < rate_window]
        if len(recent) >= rate_limit:
            hits[ip] = recent
            retry = max(1, int(rate_window - (now - recent[0])) + 1)
            return problem(429, "Rate limit exceeded", {"Retry-After": str(retry)})
        recent.append(now)
        hits[ip] = recent
        return await call_next(request)

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        key = os.environ.get("API_KEY")
        if key and request.method in {"POST", "PUT", "DELETE"}:
            given = request.headers.get("X-API-Key", "")
            if not secrets.compare_digest(given.encode(), key.encode()):
                return problem(401, "Invalid or missing API key")
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.method in {"POST", "PUT", "DELETE"}:
            response.headers["Cache-Control"] = "no-store"
        if request.url.path not in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return problem(exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return problem(422, "Request validation failed", errors=exc.errors())

    origins = [
        o.strip()
        for o in os.environ.get("ALLOWED_ORIGINS", "https://sabin78910.github.io").split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        expose_headers=["X-Total-Count", "Link"],
    )

    books: dict[int, Book] = {}
    counter = {"next": 1}
    reviews: dict[int, list[Review]] = {}

    def with_stats(book: Book) -> Book:
        rs = reviews.get(book.id, [])
        avg = round(sum(r.rating for r in rs) / len(rs), 2) if rs else None
        return book.model_copy(update={"average_rating": avg, "review_count": len(rs)})

    def conditional(
        request: Request, payload: object, extra: dict[str, str] | None = None
    ) -> Response:
        body = json.dumps(
            jsonable_encoder(payload), separators=(",", ":"), ensure_ascii=False
        ).encode()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        sent = [
            t.strip().removeprefix("W/")
            for t in request.headers.get("if-none-match", "").split(",")
        ]
        if etag in sent or "*" in sent:
            return Response(status_code=304, headers={**(extra or {}), "ETag": etag})
        return Response(
            body, media_type="application/json", headers={**(extra or {}), "ETag": etag}
        )

    def page_url(request: Request, limit: int, offset: int) -> str:
        return str(request.url.include_query_params(limit=limit, offset=offset))

    def check_isbn_free(isbn: str | None, exclude: int | None = None) -> None:
        if isbn and any(b.isbn == isbn and b.id != exclude for b in books.values()):
            raise HTTPException(409, "A book with this ISBN already exists")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing() -> str:
        rated = sorted(
            (with_stats(b) for b in books.values()),
            key=lambda b: (-(b.average_rating or 0), b.id),
        )
        top = [b for b in rated if b.average_rating is not None][:5]
        items = (
            "".join(
                f"<li>{html.escape(b.title)} <span>by {html.escape(b.author)}"
                f" &middot; {b.average_rating}&#9733;</span></li>"
                for b in top
            )
            or "<li>No rated books yet.</li>"
        )
        cards = "".join(
            f"<div class='card'><b>{m}</b> <code>{p}</code><p>{d}</p></div>"
            for m, p, d in [
                ("GET", "/books", "Browse and search books"),
                ("POST", "/books", "Add a book"),
                ("GET", "/genres", "Genres with counts"),
                ("GET", "/health", "Service status"),
            ]
        )
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bookstore API</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700" rel="stylesheet">
<style>
body{{margin:0;font-family:Inter,system-ui,sans-serif;background:#faf7f2;color:#222}}
.hero{{background:#2b2d42;color:#fff;padding:64px 24px;text-align:center}}
.hero h1{{margin:0 0 8px;font-size:2.5rem}}
.hero a{{color:#ffd166}}
main{{max-width:800px;margin:0 auto;padding:24px}}
.count{{font-size:1.25rem}}
li span{{color:#666}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}}
.card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 4px #0002}}
</style></head><body>
<section class="hero"><h1>Bookstore API</h1>
<p>Books, genres and reviews over REST.</p>
<a href="/docs">Explore the API docs &rarr;</a></section>
<main>
<p class="count"><b>{len(books)}</b> books in the catalog</p>
<h2>Top rated</h2><ul>{items}</ul>
<h2>Endpoints</h2><div class="cards">{cards}</div>
</main></body></html>"""

    @app.get("/health")
    def health() -> dict[str, str | float]:
        return {
            "status": "ok",
            "version": app.version,
            "uptime_seconds": round(time.monotonic() - started, 3),
        }

    @app.get("/books", response_model=list[Book])
    def list_books(
        request: Request,
        author: str | None = Query(default=None),
        genre: str | None = Query(default=None),
        q: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        sort: str | None = Query(default=None, description="title, author or price; '-' = desc"),
    ) -> Response:
        field = (sort or "").removeprefix("-")
        if sort is not None and field not in SORT_FIELDS:
            raise HTTPException(422, f"Invalid sort field; allowed: {', '.join(SORT_FIELDS)}")
        result = list(books.values())
        if author:
            result = [b for b in result if author.lower() in b.author.lower()]
        if genre:
            result = [b for b in result if b.genre and b.genre.lower() == genre.lower()]
        if q:
            result = [b for b in result if q.lower() in b.title.lower()]
        if sort:
            result.sort(
                key=lambda b: v.lower() if isinstance(v := getattr(b, field), str) else v,
                reverse=sort.startswith("-"),
            )
        total = len(result)
        headers = {"X-Total-Count": str(total)}
        links = []
        if offset + limit < total:
            links.append(f'<{page_url(request, limit, offset + limit)}>; rel="next"')
        if offset > 0:
            links.append(f'<{page_url(request, limit, max(0, offset - limit))}>; rel="prev"')
        if links:
            headers["Link"] = ", ".join(links)
        page = [with_stats(b) for b in result[offset : offset + limit]]
        return conditional(request, page, headers)

    @app.get("/genres")
    def list_genres() -> list[dict[str, str | int]]:
        counts: dict[str, int] = {}
        for b in books.values():
            if b.genre:
                key = b.genre.lower()
                counts[key] = counts.get(key, 0) + 1
        return [{"genre": g, "count": c} for g, c in sorted(counts.items())]

    @app.post("/books", response_model=Book, status_code=201)
    def create_book(data: BookIn) -> Book:
        check_isbn_free(data.isbn)
        book = Book(id=counter["next"], **data.model_dump())
        counter["next"] += 1
        books[book.id] = book
        return with_stats(book)

    @app.get("/books/low-stock", response_model=list[Book])
    def low_stock(threshold: int = Query(default=3, ge=0)) -> list[Book]:
        return [with_stats(b) for b in books.values() if b.stock <= threshold]

    @app.get("/books/{book_id}", response_model=Book)
    def get_book(book_id: int, request: Request) -> Response:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        return conditional(request, with_stats(books[book_id]))

    @app.get("/books/{book_id}/similar", response_model=list[Book])
    def similar_books(book_id: int) -> list[Book]:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        src = books[book_id]
        genre = src.genre.lower() if src.genre else None
        found = [
            with_stats(b)
            for b in books.values()
            if b.id != book_id
            and (
                b.author.lower() == src.author.lower()
                or (genre and b.genre and b.genre.lower() == genre)
            )
        ]
        found.sort(key=lambda b: (-(b.average_rating or 0), b.id))
        return found

    @app.put("/books/{book_id}", response_model=Book)
    def update_book(book_id: int, data: BookIn) -> Book:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        check_isbn_free(data.isbn, exclude=book_id)
        books[book_id] = Book(id=book_id, **data.model_dump())
        return with_stats(books[book_id])

    @app.post("/books/{book_id}/sell", response_model=Book)
    def sell(book_id: int, qty: int = Query(default=1, ge=1)) -> Book:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        book = books[book_id]
        if book.stock < qty:
            raise HTTPException(422, "Insufficient stock")
        book.stock -= qty
        return with_stats(book)

    @app.delete("/books/{book_id}", status_code=204)
    def delete_book(book_id: int) -> None:
        if books.pop(book_id, None) is None:
            raise HTTPException(404, "Book not found")
        reviews.pop(book_id, None)

    @app.post("/books/{book_id}/reviews", response_model=Review, status_code=201)
    def add_review(book_id: int, data: ReviewIn) -> Review:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        items = reviews.setdefault(book_id, [])
        review = Review(id=len(items) + 1, book_id=book_id, **data.model_dump())
        items.append(review)
        return review

    @app.get("/books/{book_id}/reviews", response_model=list[Review])
    def list_reviews(book_id: int) -> list[Review]:
        if book_id not in books:
            raise HTTPException(404, "Book not found")
        return reviews.get(book_id, [])

    return app


app = create_app()
